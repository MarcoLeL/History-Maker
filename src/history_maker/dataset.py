"""Fase 4: dalle trascrizioni a un archivio consultabile.

Produce tre cose dalla stessa fonte:

* ``torrebruna.sqlite``  tre tabelle (registri, atti, persone) piu' un
  indice full-text su cui si possono fare domande come "tutti i Di Nardo
  fra il 1840 e il 1860".
* ``atti.csv`` / ``persone.csv``  per chi preferisce un foglio di calcolo.
* ``sintesi.md``  un quadro d'insieme: quanti atti per anno, i cognomi
  piu' frequenti, i mestieri, le lacune della serie.

Non interpreta i dati: li mette in forma. L'interpretazione storica resta
un lavoro da fare sulle fonti cosi' ordinate.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from history_maker import glossario, normalizza, paleografia, ricuci
from history_maker.config import Config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS registri (
    slug        TEXT PRIMARY KEY,
    anno        INTEGER,
    tipologia   TEXT,
    contesto    TEXT,
    ark_url     TEXT
);

CREATE TABLE IF NOT EXISTS atti (
    id              INTEGER PRIMARY KEY,
    registro        TEXT REFERENCES registri(slug),
    immagine        TEXT,
    numero_atto     TEXT,
    tipo            TEXT,
    anno            INTEGER,
    data_atto       TEXT,
    data_evento     TEXT,
    ora_evento      TEXT,
    luogo           TEXT,
    luogo_letto     TEXT,
    luogo_incerto   INTEGER NOT NULL DEFAULT 0,
    -- La contrada, separata dal comune. 'luogo' porta la formula intera
    -- dell'atto ("Torrebruna, strada della Trascinella"); qui resta la
    -- sola parte che dice DOVE dentro il paese, che e' quella su cui si
    -- raggruppano le famiglie per vicinato.
    via             TEXT,
    testo_integrale TEXT,
    affidabilita    TEXT,
    incertezze      TEXT
);

-- 'nome' e 'cognome' sono le forme su cui si conta; 'nome_letto' e
-- 'cognome_letto' conservano cio' che c'era scritto sulla pagina, con il
-- marcatore di dubbio compreso. Tenere le due cose separate e' quello che
-- permette di normalizzare senza distruggere: la lettura originale resta
-- sempre ispezionabile, e ogni normalizzazione e' reversibile.
CREATE TABLE IF NOT EXISTS persone (
    id            INTEGER PRIMARY KEY,
    atto          INTEGER REFERENCES atti(id),
    ruolo         TEXT,
    nome          TEXT,
    nome_letto    TEXT,
    cognome       TEXT,
    cognome_letto TEXT,
    -- Da dove viene il cognome: 'atto' se e' scritto sulla pagina,
    -- 'padre' o 'madre' se e' stato ricavato. Sui neonati il cognome
    -- quasi non compare mai — 39 su 45 nel solo 1809 — perche' il
    -- formulario lo da' per implicito nel nome del padre. Ricavarlo
    -- serve a rendere il bambino trovabile, ma un'inferenza non va
    -- confusa con una lettura: chi studia le nascite illegittime deve
    -- poter distinguere le due cose con una clausola WHERE.
    cognome_origine TEXT,
    incerto       INTEGER NOT NULL DEFAULT 0,
    eta           TEXT,
    professione   TEXT,
    residenza     TEXT,
    residenza_letta TEXT,
    -- La contrada ricavata dalla residenza della persona, quando l'atto
    -- la nomina per lei invece che per l'evento.
    via           TEXT,
    -- L'incertezza sul luogo si segna a parte da quella sul nome: sono
    -- due letture diverse, e una pagina puo' avere il cognome limpido e
    -- la contrada illeggibile. Serve a scegliere cosa vale la pena
    -- rileggere.
    residenza_incerta INTEGER NOT NULL DEFAULT 0,
    stato_vitale  TEXT,
    note          TEXT
);

-- Le voci di indice sono una seconda lettura degli stessi cognomi degli
-- atti: stanno in una tabella a parte perche' la fase di revisione le
-- confronta con quelle lette negli atti dello stesso registro.
CREATE TABLE IF NOT EXISTS voci_indice (
    id          INTEGER PRIMARY KEY,
    registro    TEXT REFERENCES registri(slug),
    immagine    TEXT,
    numero_atto TEXT,
    nome        TEXT,
    cognome     TEXT,
    cognome_letto TEXT
);

CREATE INDEX IF NOT EXISTS idx_indice_registro ON voci_indice(registro);
CREATE INDEX IF NOT EXISTS idx_atti_anno    ON atti(anno);
CREATE INDEX IF NOT EXISTS idx_atti_tipo    ON atti(tipo);
CREATE INDEX IF NOT EXISTS idx_pers_cognome ON persone(cognome);
CREATE INDEX IF NOT EXISTS idx_pers_atto    ON persone(atto);

CREATE VIRTUAL TABLE IF NOT EXISTS atti_fts USING fts5(
    testo_integrale, numero_atto, luogo, content='atti', content_rowid='id'
);
"""


def leggi_trascrizioni(
    cartella: Path, ammessi: set[str] | None = None
) -> Iterator[dict[str, Any]]:
    """Scorre i JSON prodotti dalla fase di trascrizione.

    ``ammessi`` sono gli slug dei registri che la configurazione tiene.
    Serve perche' **le esclusioni non si applicano da sole a cio' che e'
    gia' su disco**: escludere le pubblicazioni dal file di configurazione
    ferma la fase 3, ma le pagine trascritte prima restano nella cartella,
    e senza questo filtro rientrerebbero nel database dalla porta di
    servizio — con gli stessi sposi e gli stessi genitori dell'atto di
    matrimonio vero, contati due volte.

    Un registro che il catalogo non conosce affatto viene tenuto: e' una
    trascrizione che qualcuno ha messo li' a mano, e buttarla via in
    silenzio sarebbe peggio che includerla.
    """
    saltati = 0
    for percorso in sorted(cartella.rglob("*.json")):
        if percorso.name.startswith("_"):
            continue
        try:
            pagina = json.loads(percorso.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("%s: JSON illeggibile (%s)", percorso, exc)
            continue
        registro = (pagina.get("_origine") or {}).get("registro")
        if ammessi is not None and registro is not None and registro not in ammessi:
            saltati += 1
            continue
        yield pagina
    if saltati:
        logger.info(
            "%d pagine ignorate: appartengono a registri esclusi dalla configurazione", saltati
        )


def _ammessi_dalle_trascrizioni(config: Config) -> set[str] | None:
    """I registri da tenere quando il catalogo non c'e', ricavati dalle pagine.

    Il catalogo sta in ``data/``, fuori dal repository; le trascrizioni no.
    Un clone rifaceva il database prendendo TUTTO: anche le pubblicazioni
    del 1810, che la configurazione esclude perche' duplicano gli atti di
    matrimonio. Sono 131 righe in testa al secolo, e spostavano di 131 gli
    id di tutte quelle che seguono — gli id a cui puntano le decisioni prese
    sull'immagine. Nel clone ogni correzione finiva su un'altra persona.

    Ogni pagina porta in ``_origine`` l'anno, la tipologia e il contesto del
    suo registro: e' quanto serve a :func:`catalogo.selezione`, che cosi'
    decide come avrebbe deciso col catalogo, recuperi compresi (le
    pubblicazioni del 1870-1888, uniche tracce dei matrimoni di quegli anni).
    Un registro senza quei dati - una trascrizione messa li' a mano - si
    tiene, come prima.
    """
    from history_maker.catalogo import Catalogo, Registro, selezione

    registri: dict[str, Registro] = {}
    senza_dati: set[str] = set()
    for percorso in sorted(config.trascrizioni.rglob("*.json")):
        if percorso.name.startswith("_") or percorso.parent.name in registri:
            continue
        try:
            origine = json.loads(percorso.read_text(encoding="utf-8")).get("_origine") or {}
        except json.JSONDecodeError:
            continue
        slug = origine.get("registro")
        if not slug or slug in registri:
            continue
        if not (origine.get("contesto") and origine.get("tipologia") and origine.get("anno")):
            senza_dati.add(slug)
            continue
        registri[slug] = Registro(
            ark_url=origine.get("ark_url") or slug,
            contesto=origine["contesto"],
            titolo=str(origine["anno"]),
            tipologia=origine["tipologia"],
            anno=int(origine["anno"]),
            cartella=slug,
        )
    if not registri:
        return None
    tenuti = selezione(Catalogo(comune=config.comune, registri=list(registri.values())), config)
    return {r.cartella for r in tenuti} | senza_dati


def _percorso_pulito(percorso: str | None) -> str | None:
    r"""Percorso con separatori '/', qualunque sistema l'abbia scritto.

    La fase 3 salva il percorso relativo dell'immagine col separatore del
    sistema su cui gira: su Windows finisce nel database come
    'registro\0049-pag-49.jpg', dove il backslash e' insieme illeggibile,
    non portabile e — davanti a una cifra — perfino ambiguo, perche' in
    parecchi linguaggi '\0' e' una sequenza di escape.
    """
    return percorso.replace("\\", "/") if percorso else percorso


def _anno_da_data(data: str | None, ripiego: int | None) -> int | None:
    if data:
        match = re.search(r"\b(1[78]\d{2}|19\d{2})\b", data)
        if match:
            return int(match.group(1))
    return ripiego


# I ruoli il cui cognome il formulario da' per implicito. Un atto di
# nascita nomina "Maria, figlia di Giuseppe Colella": il cognome della
# bambina non e' scritto da nessuna parte, ed e' corretto che la
# trascrizione lo lasci vuoto — sulla pagina non c'e'.
RUOLI_DA_DERIVARE = ("neonato",)

# In quale ordine cercare il cognome da cui derivare.
GENITORI = ("padre", "madre")


def _deriva_cognome(persone: list[dict]) -> None:
    """Da' un cognome a chi sulla pagina non ce l'ha, prendendolo dai genitori.

    Nel solo 1809 sono 39 neonati su 45: senza questo il bambino resta
    introvabile per cognome, cioe' proprio nella ricerca per cui il
    database esiste.

    L'inferenza viene **segnata** in ``cognome_origine``, mai confusa con
    una lettura. Non e' pignoleria: i figli naturali e gli esposti sono
    una categoria storicamente importante, e sono esattamente i casi in
    cui il cognome del padre non c'e' o non si applica. Riempire in
    silenzio cancellerebbe il dato piu' interessante che quelle nascite
    portano con se'.

    Modifica la lista sul posto.
    """
    genitori = {
        p["ruolo"]: p["cognome"]
        for p in persone
        if p.get("ruolo") in GENITORI and p.get("cognome")
    }
    if not genitori:
        return
    for persona in persone:
        if persona.get("ruolo") not in RUOLI_DA_DERIVARE or persona.get("cognome"):
            continue
        for ruolo in GENITORI:
            if genitori.get(ruolo):
                persona["cognome"] = genitori[ruolo]
                persona["cognome_origine"] = ruolo
                break


def _applica_alternative(conn: sqlite3.Connection) -> dict[str, str]:
    """Sfrutta le seconde letture che il modello ha annotato nelle note.

    Gira **prima** del raggruppamento per somiglianza, perche' arriva
    dove quello non puo': 'Tommolilli' dista 0,67 da 'Femminilli', sotto
    qualunque soglia sensata, ma l'alternativa che il modello stesso
    aveva annotato — 'Iemminilli' — dista 0,90. Correggere prima significa
    anche che le frequenze su cui il raggruppamento decide sono gia'
    quelle giuste.
    """
    attestate = Counter(
        {
            cognome: quanti
            for cognome, quanti in conn.execute(
                "SELECT cognome, COUNT(*) FROM persone WHERE incerto = 0 "
                "AND cognome_origine = 'atto' AND cognome IS NOT NULL "
                "GROUP BY cognome"
            )
        }
    )
    incerte = conn.execute(
        "SELECT cognome, note FROM persone WHERE incerto = 1 "
        "AND cognome_origine = 'atto' AND cognome IS NOT NULL"
    ).fetchall()

    corrette = normalizza.correzioni_dalle_alternative(list(incerte), attestate)
    for variante, forma in corrette.items():
        conn.execute("UPDATE persone SET cognome = ? WHERE cognome = ?", (forma, variante))
        conn.execute(
            "UPDATE voci_indice SET cognome = ? WHERE cognome = ?", (forma, variante)
        )
    if corrette:
        logger.info(
            "Corretti dalle letture alternative annotate: %s",
            ", ".join(f"{a} -> {b}" for a, b in sorted(corrette.items())),
        )
    return corrette


def _proposte_toponimi(conn: sqlite3.Connection) -> list[normalizza.Proposta]:
    """Le contrade lette in piu' modi, da sottoporre a chi conosce il paese.

    A differenza dei cognomi qui **non si corregge niente in automatico**,
    e non per prudenza: la correzione dei toponimi avviene per
    sostituzione dentro una frase intera ("in questa Comune di
    Torrebruna, strada a piedi la <toponimo>"), e quel lavoro lo fa gia'
    il glossario. Quello che manca e' sapere quale delle grafie sia
    quella buona — e lo sa solo chi in quelle strade ci e' passato.

    Il caso che ha portato qui: la sola 'Rua di Nuorro' compare letta
    come 'Lama di Nuorro', 'Via di Nuovo', 'Bua di Nuovo' e 'Rua di
    Nuovo'. Nessuna statistica sceglie fra queste; un abitante sì.
    """
    valori = [
        riga[0]
        for riga in conn.execute("SELECT residenza_letta FROM persone WHERE residenza_letta IS NOT NULL")
    ] + [
        riga[0] for riga in conn.execute("SELECT luogo_letto FROM atti WHERE luogo_letto IS NOT NULL")
    ]

    # Ogni nucleo si porta dietro la forma leggibile piu' ricorrente, che
    # e' quella che poi finira' nel glossario.
    frequenze: Counter[str] = Counter()
    leggibili: dict[str, Counter[str]] = {}
    for valore in valori:
        nucleo = normalizza.nucleo_toponimo(valore)
        if not nucleo:
            continue
        riscontro = normalizza._TOPONIMO.search(valore)
        forma = normalizza.separa_incertezza(riscontro.group(0))[0] if riscontro else None
        if not forma:
            continue
        frequenze[nucleo] += 1
        leggibili.setdefault(nucleo, Counter())[forma.strip()] += 1

    def leggibile(nucleo: str) -> str:
        return leggibili[nucleo].most_common(1)[0][0]

    # I gruppi si presentano **piatti**: tutte le grafie di una contrada
    # sotto una sola voce. Le coppie a catena — 'Fiascinella' che rimanda
    # a 'Fraccinella' che rimanda a 'Fraginella' — sono corrette ma
    # illeggibili per chi deve solo dire quale sia la forma buona, e qui
    # il destinatario e' una persona, non il codice.
    proposte: list[normalizza.Proposta] = []
    for gruppo in normalizza._raggruppa(list(frequenze), normalizza.SOGLIA_TOPONIMI):
        if len(gruppo) < 2:
            continue
        capofila = max(sorted(gruppo), key=lambda n: frequenze[n])
        for nucleo in sorted(gruppo, key=lambda n: -frequenze[n]):
            if nucleo == capofila or leggibile(nucleo) == leggibile(capofila):
                continue
            proposte.append(
                normalizza.Proposta(
                    letto=leggibile(nucleo),
                    proposto=leggibile(capofila),
                    occorrenze_lette=frequenze[nucleo],
                    occorrenze_proposte=frequenze[capofila],
                    somiglianza=paleografia.somiglianza(nucleo, capofila),
                )
            )
    return proposte


def _unifica_varianti(conn: sqlite3.Connection) -> list[normalizza.Proposta]:
    """Riconduce le grafie della stessa famiglia a una forma sola.

    Gira **a caricamento finito**, e non potrebbe essere altrimenti: una
    variante si giudica per quanto ricorre nell'intero corpus, non dentro
    la pagina o l'anno in cui capita. Una forma vista due volte in una
    pagina e quindici altrove e' un'altra cosa da una vista due volte e
    basta.

    Tocca solo la colonna ``cognome``: ``cognome_letto`` conserva sempre
    cio' che c'era scritto sulla carta, quindi ogni unificazione resta
    ispezionabile e reversibile. Le varianti su cui il calcolo non se la
    sente non vengono toccate: tornano indietro come proposte per il
    glossario, dove decide chi conosce il paese.
    """
    # Solo i cognomi **letti** fanno testo. Un cognome derivato dal padre
    # e' una copia della stessa lettura, non una seconda testimonianza:
    # contarlo gonfierebbe la dominanza di quella forma con l'eco di se
    # stessa, e la dominanza e' proprio cio' che decide se una variante si
    # corregge da sola o va sottoposta a una persona.
    frequenze = Counter(
        {
            cognome: quanti
            for cognome, quanti in conn.execute(
                "SELECT cognome, COUNT(*) FROM persone "
                "WHERE cognome IS NOT NULL AND cognome <> '' "
                "AND cognome_origine = 'atto' GROUP BY cognome"
            )
        }
    )
    correzioni, proposte = normalizza.raggruppa_varianti(frequenze)

    for variante, canonica in correzioni.items():
        conn.execute(
            "UPDATE persone SET cognome = ? WHERE cognome = ?", (canonica, variante)
        )
        # Le voci d'indice vanno unificate con la stessa mappa: sono la
        # stessa famiglia letta da un'altra pagina, e normalizzarne una
        # sola farebbe divergere proprio il confronto che la fase 5 usa
        # come segnale piu' affidabile.
        conn.execute(
            "UPDATE voci_indice SET cognome = ? WHERE cognome = ?", (canonica, variante)
        )

    if correzioni:
        logger.info("Varianti unificate da sole: %d", len(correzioni))
    if proposte:
        logger.info("Varianti da decidere nel glossario: %d", len(proposte))
    return proposte


# «di mesi otto», «di giorni tre»: l'unita' che distingue un lattante da
# un ragazzo. Il modulo a stampa lo dice in calce — «Si scrivera' anni,
# mesi, giorni o ore a seconda della eta' del defunto» — ma nel campo
# arriva spesso il solo numero, e allora un bambino morto a otto mesi
# entra nell'albero come un ragazzo di otto anni, con la nascita
# spostata di sette. Su Torrebruna sono settantaquattro defunti.
_ETA_CON_UNITA = re.compile(
    r"\bdi\s+(mesi|mese|giorni|giorno|ore|ora)\s+([a-zA-Zà-ùÀ-Ù]+)", re.I
)
_UNITA_NELL_ETA = re.compile(r"\b(mesi|mese|giorni|giorno|ore|ora)\b", re.I)


def _eta_con_unita(testo: str | None, eta: str | None) -> str | None:
    """Rimette l'unita' a un'eta' che l'ha persa per strada.

    Solo se il numero coincide: l'unita' non si attacca a indovinare. Se
    il campo dice 'otto' e l'atto dice «di mesi otto», allora sono la
    stessa cosa e l'unita' torna al suo posto; se dicono numeri diversi,
    non e' la stessa eta' e non si tocca niente.

    La formula della sepoltura — «dargli sepoltura dopo lo spazio di ore
    ventiquattro» — e' in ogni atto di morte e non e' l'eta' di nessuno:
    la si riconosce dalla parola 'spazio' che la precede.
    """
    ripulita = (eta or "").strip()
    if not ripulita or not testo or _UNITA_NELL_ETA.search(ripulita):
        return eta
    for trovato in _ETA_CON_UNITA.finditer(testo):
        if "spazio" in testo[max(0, trovato.start() - 40):trovato.start()].lower():
            continue
        unita, numero = trovato.group(1).lower(), trovato.group(2)
        if numero.casefold() == ripulita.casefold():
            return f"{unita} {numero}"
    return eta


# La tavola alfabetica non e' una pagina di atti: e' l'elenco che il
# comune compilava a fine anno, una riga per matrimonio, con il numero
# d'ordine e i nomi degli sposi. Ha la forma di un atto abbastanza da
# ingannare — c'e' un "Num. d'ordine", ci sono due nomi — e lo schema
# prevede apposta "tipo_pagina": "indice" e "voci_indice", ma il modello
# la classifica lo stesso fra gli atti. Undici pagine cosi' avevano
# generato diciassette atti e sessantotto persone che non esistono, e
# ognuna di quelle persone entra nell'albero come un parente in piu'.
_UNA_TAVOLA = re.compile(
    r"tavola\s+(?:annuale\s+)?alfabetica|indice\s+annuale|"
    r"tavola\s+de'?\s*(?:matrimoni|nati|morti|atti)",
    re.I,
)


def _e_una_tavola(atto: dict) -> bool:
    """L'«atto» e' in realta' una riga dell'indice di fine anno."""
    return bool(_UNA_TAVOLA.search(atto.get("testo_integrale") or ""))


def costruisci(config: Config) -> Path:
    """Costruisce il database SQLite e i CSV; restituisce il percorso del database."""
    config.dataset.mkdir(parents=True, exist_ok=True)
    percorso_db = config.dataset / "torrebruna.sqlite"
    # Le tabelle che discendono dai JSON si rifanno; quelle che NON ci
    # discendono — il registro delle decisioni e le riletture pagate con
    # la quota — si mettono da parte prima e si rimettono dopo, con le
    # menzioni ritrovate per chiave stabile invece che per rowid.
    conservato = conserva(percorso_db)
    # Si sposta, non si cancella. Fra il 'conserva' e il 'ripristina' c'e'
    # una ricostruzione intera, e per tutta la sua durata il registro —
    # trentasettemila decisioni, le riletture pagate a quota, i giudizi
    # dati a mano — esisterebbe solo nella memoria di questo processo.
    # Un Ctrl+C, un disco pieno, o il file tenuto aperto dal server
    # dell'albero, e sarebbe perduto per sempre: e' l'unica cosa del
    # progetto che non si puo' rifare partendo dai JSON.
    precedente = percorso_db.with_suffix(".sqlite.precedente")
    if percorso_db.exists():
        precedente.unlink(missing_ok=True)
        percorso_db.rename(precedente)

    conn = sqlite3.connect(percorso_db)
    conn.executescript(SCHEMA_SQL)

    # Le forme attestate del paese entrano qui: sono l'unica cosa che
    # raddrizza le letture sbagliate in modo concorde, che nessuna
    # statistica puo' scoprire perche' non c'e' nessun disaccordo da
    # rilevare.
    glossario_locale = glossario.Glossario.carica(config.glossario)
    corrette: Counter[str] = Counter()

    def _con_glossario(valore: str | None, campo: str) -> str | None:
        return _con_glossario_e_dubbio(valore, campo)[0]

    def _con_glossario_e_dubbio(valore: str | None, campo: str) -> tuple[str | None, bool]:
        pulito, incerto = normalizza.separa_incertezza(valore)
        corretto, applicate = glossario_locale.correggi_campo(pulito, campo)
        for c in applicate:
            corrette[f"{c.letto} -> {c.corretto}"] += 1
        return corretto, incerto

    n_atti = n_persone = n_pagine = n_voci = n_tavole = 0
    # Quante pagine ci sono in tutto: serve a dare una percentuale invece
    # di un silenzio. Contarle costa un attraversamento di directory, e
    # vale ogni millisecondo — tre volte oggi una fase muta e' sembrata
    # bloccata mentre lavorava.
    da_leggere = sum(1 for p in config.trascrizioni.rglob("*.json") if not p.name.startswith("_"))
    logger.info("Fase 1/5: leggo %d pagine di trascrizione", da_leggere)
    passo = max(1, da_leggere // 20)
    # Gli slug che la configurazione tiene. Senza catalogo il catalogo si
    # rifa' dalle trascrizioni stesse (vedi _catalogo_dalle_trascrizioni).
    from history_maker.catalogo import Catalogo, selezione

    if config.catalogo.exists():
        ammessi = {r.slug for r in selezione(Catalogo.carica(config.catalogo), config)}
    else:
        ammessi = _ammessi_dalle_trascrizioni(config)

    for lette, pagina in enumerate(leggi_trascrizioni(config.trascrizioni, ammessi), 1):
        if lette % passo == 0 or lette == da_leggere:
            logger.info(
                "  ...%d/%d pagine (%d%%) — %d atti, %d persone",
                lette, da_leggere, 100 * lette // max(1, da_leggere), n_atti, n_persone,
            )
        origine = pagina.get("_origine", {})
        n_pagine += 1
        conn.execute(
            "INSERT OR IGNORE INTO registri VALUES (?,?,?,?,?)",
            (
                origine.get("registro"),
                origine.get("anno"),
                origine.get("tipologia"),
                origine.get("contesto"),
                origine.get("ark_url"),
            ),
        )
        for voce in pagina.get("voci_indice") or []:
            conn.execute(
                "INSERT INTO voci_indice (registro, immagine, numero_atto, nome, "
                "cognome, cognome_letto) VALUES (?,?,?,?,?,?)",
                (
                    origine.get("registro"),
                    _percorso_pulito(origine.get("immagine")),
                    voce.get("numero_atto"),
                    # Le voci d'indice servono a un solo scopo: essere
                    # confrontate con i cognomi letti negli atti. Un
                    # marcatore di dubbio attaccato al valore farebbe
                    # fallire il confronto proprio dove funziona meglio.
                    normalizza.separa_incertezza(voce.get("nome"))[0],
                    _con_glossario(voce.get("cognome"), "cognome"),
                    voce.get("cognome"),
                ),
            )
            n_voci += 1

        for atto in pagina.get("atti") or []:
            if _e_una_tavola(atto):
                n_tavole += 1
                continue
            luogo_corretto, luogo_incerto = _con_glossario_e_dubbio(atto.get("luogo"), "luogo")
            cursore = conn.execute(
                """INSERT INTO atti (registro, immagine, numero_atto, tipo, anno,
                                     data_atto, data_evento, ora_evento, luogo,
                                     luogo_letto, luogo_incerto, via, testo_integrale,
                                     affidabilita, incertezze)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    origine.get("registro"),
                    _percorso_pulito(origine.get("immagine")),
                    atto.get("numero_atto"),
                    atto.get("tipo"),
                    _anno_da_data(atto.get("data_atto"), origine.get("anno")),
                    atto.get("data_atto"),
                    atto.get("data_evento"),
                    atto.get("ora_evento"),
                    # Valori nominati invece che spacchettati con '*'.
                    # Lo spacchettamento faceva finire il flag 'incerto'
                    # nella colonna 'luogo_letto' e il testo del luogo in
                    # 'luogo_incerto': uno scambio invisibile finche' un
                    # atto senza luogo non ha fatto scattare il vincolo
                    # NOT NULL. Con tre colonne consecutive che parlano
                    # tutte del luogo, l'ordine posizionale e' una trappola.
                    luogo_corretto,
                    atto.get("luogo"),
                    int(luogo_incerto),
                    normalizza.via_da(luogo_corretto, config.comune),
                    atto.get("testo_integrale"),
                    atto.get("affidabilita"),
                    "; ".join(atto.get("parti_illeggibili") or []) or None,
                ),
            )
            id_atto = cursore.lastrowid
            n_atti += 1
            # Le persone si preparano tutte prima di scriverle: il cognome
            # di un neonato si ricava da quello del padre, che sta in
            # un'altra riga dello stesso atto.
            preparate = []
            for persona in atto.get("persone") or []:
                nome, nome_incerto = normalizza.separa_incertezza(persona.get("nome"))
                _, cognome_incerto = normalizza.separa_incertezza(persona.get("cognome"))
                cognome = _con_glossario(persona.get("cognome"), "cognome")
                residenza, residenza_incerta = _con_glossario_e_dubbio(
                    persona.get("residenza"), "luogo"
                )
                preparate.append(
                    {
                        "ruolo": persona.get("ruolo"),
                        "nome": nome,
                        "nome_letto": persona.get("nome"),
                        "cognome": cognome,
                        "cognome_letto": persona.get("cognome"),
                        "cognome_origine": "atto" if cognome else None,
                        "incerto": int(nome_incerto or cognome_incerto),
                        "eta": (
                            _eta_con_unita(atto.get("testo_integrale"),
                                           persona.get("eta"))
                            if persona.get("ruolo") == "defunto"
                            else persona.get("eta")
                        ),
                        "professione": persona.get("professione"),
                        "residenza": residenza,
                        "residenza_letta": persona.get("residenza"),
                        "residenza_incerta": int(residenza_incerta),
                        "via": normalizza.via_da(residenza, config.comune),
                        "stato_vitale": persona.get("stato_vitale"),
                        "note": persona.get("note"),
                    }
                )
            _deriva_cognome(preparate)

            for persona in preparate:
                conn.execute(
                    """INSERT INTO persone (atto, ruolo, nome, nome_letto,
                                            cognome, cognome_letto, cognome_origine,
                                            incerto, eta, professione, residenza,
                                            residenza_letta, residenza_incerta, via,
                                            stato_vitale, note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (id_atto, *(persona[c] for c in (
                        "ruolo", "nome", "nome_letto", "cognome", "cognome_letto",
                        "cognome_origine", "incerto", "eta", "professione",
                        "residenza", "residenza_letta", "residenza_incerta", "via",
                        "stato_vitale", "note",
                    ))),
                )
                n_persone += 1

    # Prima di qualunque altra cosa si rimettono insieme gli atti che la
    # paginazione ha spezzato. Va fatto qui e non dopo: tutto quello che
    # segue — le varianti, il riconoscimento, le parentele — legge
    # 'dentro un atto', e finche' un matrimonio sta su tre atti diversi
    # non c'e' nessun atto che contenga una coppia.
    cuciti, tolti = ricuci.ricuci_atti(conn)
    if cuciti:
        logger.info(
            "Fase 1b/5: %d atti spezzati su piu' pagine rimessi insieme "
            "(%d frammenti riassorbiti)", cuciti, tolti,
        )
        n_atti -= tolti
    if n_tavole:
        logger.info(
            "%d righe di tavola alfabetica scartate: sono indici, non atti",
            n_tavole,
        )

    logger.info("Fase 2/5: applico le alternative lette nelle note")
    _applica_alternative(conn)

    # E' la fase lenta, e ora si sa perche': confronta ogni cognome con
    # ogni altro con una distanza pesata sulle confusioni paleografiche.
    # Cresce col QUADRATO delle forme distinte — su 86 pagine erano ~200
    # forme, su 5.000 sono 3.301 — quindi e' quella che va detta a voce.
    distinti = conn.execute(
        "SELECT COUNT(DISTINCT cognome) FROM persone WHERE cognome IS NOT NULL"
    ).fetchone()[0]
    logger.info(
        "Fase 3/5: raggruppo le varianti di %d forme di cognome "
        "(confronto a coppie: e' la fase lunga)", distinti,
    )
    proposte = _unifica_varianti(conn)

    logger.info("Fase 4/5: raggruppo le varianti dei toponimi")
    toponimi = _proposte_toponimi(conn)

    logger.info("Fase 5/5: indice full-text, CSV e sintesi")

    conn.execute(
        "INSERT INTO atti_fts(rowid, testo_integrale, numero_atto, luogo) "
        "SELECT id, testo_integrale, numero_atto, luogo FROM atti"
    )
    conn.commit()
    logger.info(
        "Database: %d pagine, %d atti, %d persone, %d voci di indice",
        n_pagine, n_atti, n_persone, n_voci,
    )

    if corrette:
        logger.info(
            "Glossario applicato: %s",
            ", ".join(f"{k} ({n})" for k, n in sorted(corrette.items())),
        )

    _esporta_csv(conn, config.dataset, config.csv_separatore)
    (config.dataset / "sintesi.md").write_text(sintesi(conn, config), encoding="utf-8")
    _scrivi_proposte(config, proposte, toponimi)
    conn.close()

    if conservato:
        conti = ripristina(percorso_db, conservato)
        logger.info(
            "Registro rimesso: %d decisioni, %d riletture%s%s",
            conti["decisioni"], conti["riletture"],
            f", {conti['riprese']} orfane tornate a casa" if conti["riprese"] else "",
            f", {conti['orfane']} messe da parte in decisioni_orfane"
            if conti["orfane"] else "",
        )
    # Solo adesso il database di prima e' davvero di troppo.
    precedente.unlink(missing_ok=True)
    return percorso_db


def _scrivi_proposte(
    config: Config,
    proposte: list[normalizza.Proposta],
    toponimi: list[normalizza.Proposta] | None = None,
) -> Path:
    """Scrive le varianti da decidere gia' in forma di glossario.

    Il file e' pronto da incollare in ``glossario-*.yaml``: chi decide non
    deve ricopiare niente, solo cancellare le righe che non gli tornano.
    Le forme sono ordinate per quanto ricorrono, perche' una vista sei
    volte merita attenzione e una vista una volta sola quasi mai.
    """
    percorso = config.dataset / "glossario-proposto.yaml"
    if not proposte:
        percorso.write_text(
            "# Nessuna variante da decidere: il calcolo se l'e' cavata da solo.\n",
            encoding="utf-8",
        )
        return percorso

    righe = [
        "# Varianti su cui il calcolo non se la sente di decidere.",
        "#",
        "# Sono forme troppo simili per essere famiglie estranee e troppo alla",
        "# pari per stabilire quale sia la lettura buona. Qui serve chi conosce",
        "# il paese: un cognome raro puo' essere una lettura sbagliata, ma anche",
        "# un forestiero vero — una sposa di un comune vicino, un soldato, un",
        "# prete — e quello e' un dato storico, non rumore.",
        "#",
        "# Per accettare una proposta, sposta la riga sotto 'cognomi:' nel",
        "# glossario. Per rifiutarla, cancellala: se la forma e' giusta cosi',",
        "# elencala sotto 'confermati:' e non verra' piu' riproposta.",
        "",
        "cognomi:",
    ]
    # Le varianti vanno raccolte sotto la forma proposta: in YAML una
    # chiave ripetuta sovrascrive in silenzio la precedente, quindi un
    # elenco con 'Lella:' scritto tre volte perderebbe due proposte su
    # tre senza dirlo a nessuno.
    per_forma: dict[str, list[normalizza.Proposta]] = {}
    for p in proposte:
        per_forma.setdefault(p.proposto, []).append(p)

    for forma, gruppo in per_forma.items():
        righe.append(f"  {forma}:")
        for p in gruppo:
            righe.append(f"    # {p.motivo}")
            righe.append(f"    - {p.letto}")
    righe.append("")

    if toponimi:
        righe += [
            "# Contrade lette in piu' modi. Qui NON si corregge niente in",
            "# automatico: la sola 'Rua di Nuorro' compare come 'Lama di Nuorro',",
            "# 'Via di Nuovo' e 'Bua di Nuovo', e fra queste non sceglie una",
            "# statistica — sceglie chi in quelle strade ci e' passato.",
            "#",
            "# Le strade di un paese sono pero' un insieme CHIUSO: non esiste la",
            "# 'via forestiera vera' che rende rischioso unificare i cognomi.",
            "# Qui si puo' essere decisi.",
            "",
            "toponimi:",
        ]
        per_luogo: dict[str, list[normalizza.Proposta]] = {}
        for t in toponimi:
            per_luogo.setdefault(t.proposto, []).append(t)
        for forma, gruppo in per_luogo.items():
            righe.append(f"  {forma}:")
            for t in gruppo:
                righe.append(
                    f"    # letto {t.occorrenze_lette} volte, "
                    f"'{t.proposto}' {t.occorrenze_proposte}"
                )
                righe.append(f"    - {t.letto}")
        righe.append("")

    percorso.write_text("\n".join(righe), encoding="utf-8")
    logger.info("Proposte per il glossario: %s", percorso)
    return percorso


# I CSV servono a chi apre un foglio di calcolo, non a chi scrive SQL: la
# tabella 'persone' da sola contiene un 'atto' che e' un numero e basta,
# quindi per rispondere a "chi sono i morti del 1809" bisognerebbe
# incrociarla a mano con 'atti'. Portarsi dietro anno e tipo dell'atto
# rende il file utilizzabile da solo.
# I ruoli che nell'atto hanno dei genitori nominati. Un testimone o un
# ufficiale compaiono nello stesso atto ma i genitori che vi figurano non
# sono i suoi.
_RUOLI_CON_GENITORI = "('neonato', 'sposo', 'sposa', 'defunto')"


def _genitore(ruolo: str) -> str:
    """Sotto-query che rende il genitore, **solo** se e' inequivocabile.

    Un atto di nascita nomina un padre solo, e attribuirlo al neonato non
    e' una scelta. Un matrimonio ne nomina due — quello dello sposo e
    quello della sposa — e il ruolo non dice quale sia quale: li' il campo
    resta vuoto. Meglio un buco dichiarato che una parentela inventata,
    tanto piu' in un lavoro che deve ricostruire alberi genealogici.
    """
    return f"""
        CASE WHEN p.ruolo IN {_RUOLI_CON_GENITORI}
              AND (SELECT COUNT(*) FROM persone g
                   WHERE g.atto = p.atto AND g.ruolo = '{ruolo}') = 1
        THEN (SELECT TRIM(COALESCE(g.nome, '') || ' ' || COALESCE(g.cognome, ''))
              FROM persone g WHERE g.atto = p.atto AND g.ruolo = '{ruolo}')
        END
    """


QUERY_CSV = {
    "atti": "SELECT * FROM atti",
    "persone": f"""
        SELECT p.id, p.atto, a.anno, a.tipo AS tipo_atto, a.numero_atto,
               a.data_atto, a.registro, a.immagine,
               p.ruolo, p.nome, p.nome_letto, p.cognome, p.cognome_letto,
               p.cognome_origine, p.incerto, p.eta, p.professione,
               {_genitore('padre')} AS padre,
               {_genitore('madre')} AS madre,
               p.residenza, p.residenza_letta,
               -- La contrada: quella scritta per la persona se l'atto la
               -- nomina, altrimenti quella dell'evento. I due modelli la
               -- mettono in posti diversi e questa COALESCE e' cio' che
               -- rende la colonna indipendente da chi ha letto.
               COALESCE(p.via, a.via) AS via,
               p.stato_vitale, p.note
        FROM persone p LEFT JOIN atti a ON a.id = p.atto
        ORDER BY a.anno, a.numero_atto, p.id
    """,
    "voci_indice": "SELECT * FROM voci_indice",
}


def _esporta_csv(conn: sqlite3.Connection, cartella: Path, separatore: str = ";") -> None:
    for tabella, query in QUERY_CSV.items():
        cursore = conn.execute(query)
        intestazioni = [d[0] for d in cursore.description]
        with (cartella / f"{tabella}.csv").open(
            "w",
            newline="",
            # 'utf-8-sig' scrive il BOM. Senza, Excel apre il file con la
            # codepage di sistema e ogni accento diventa mojibake: 'eta''
            # si legge 'etÃ '. Il BOM e' l'unico modo di dirgli che il file
            # e' UTF-8, e gli altri strumenti (pandas, R, LibreOffice) lo
            # riconoscono e lo scartano da soli.
            encoding="utf-8-sig",
        ) as handle:
            scrittore = csv.writer(handle, delimiter=separatore)
            scrittore.writerow(intestazioni)
            scrittore.writerows(cursore)


def sintesi(conn: sqlite3.Connection, config: Config) -> str:
    """Quadro d'insieme in Markdown: il primo sguardo sui dati raccolti."""

    def query(sql: str, *parametri) -> list[tuple]:
        return conn.execute(sql, parametri).fetchall()

    (totale_atti,) = query("SELECT COUNT(*) FROM atti")[0]
    (totale_persone,) = query("SELECT COUNT(*) FROM persone")[0]

    righe = [
        f"# {config.comune}, {config.anno_min}-{config.anno_max}",
        "",
        "Sintesi generata automaticamente dalle trascrizioni degli atti di",
        "stato civile conservati nell'Archivio di Stato di Chieti e pubblicati",
        "sul Portale Antenati.",
        "",
        f"- Atti trascritti: **{totale_atti}**",
        f"- Persone nominate: **{totale_persone}**",
        "",
        "## Atti per tipo",
        "",
        "| tipo | atti |",
        "|---|---|",
    ]
    for tipo, quanti in query(
        "SELECT COALESCE(tipo,'?'), COUNT(*) FROM atti GROUP BY 1 ORDER BY 2 DESC"
    ):
        righe.append(f"| {tipo} | {quanti} |")

    righe += ["", "## Copertura per decennio", "", "| decennio | atti |", "|---|---|"]
    for decennio, quanti in query(
        "SELECT (anno/10)*10, COUNT(*) FROM atti WHERE anno IS NOT NULL "
        "GROUP BY 1 ORDER BY 1"
    ):
        righe.append(f"| {decennio}-{decennio + 9} | {quanti} |")

    anni_presenti = {a for (a,) in query("SELECT DISTINCT anno FROM atti WHERE anno IS NOT NULL")}
    lacune = [a for a in range(config.anno_min, config.anno_max + 1) if a not in anni_presenti]
    if lacune:
        righe += [
            "",
            "## Anni senza atti trascritti",
            "",
            "Sono le lacune della serie: registri mai versati all'archivio,",
            "perduti, non ancora digitalizzati, oppure semplicemente non",
            "compresi in questa raccolta.",
            "",
            _compatta_intervalli(lacune),
        ]

    righe += ["", "## Cognomi piu' frequenti", "", "| cognome | ricorrenze |", "|---|---|"]
    for cognome, quanti in query(
        "SELECT cognome, COUNT(*) FROM persone WHERE cognome IS NOT NULL AND cognome <> '' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
    ):
        righe.append(f"| {cognome} | {quanti} |")

    righe += ["", "## Mestieri dichiarati", "", "| professione | ricorrenze |", "|---|---|"]
    for professione, quanti in query(
        "SELECT LOWER(professione), COUNT(*) FROM persone "
        "WHERE professione IS NOT NULL AND professione <> '' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
    ):
        righe.append(f"| {professione} | {quanti} |")

    affidabilita = Counter(
        dict(query("SELECT COALESCE(affidabilita,'?'), COUNT(*) FROM atti GROUP BY 1"))
    )
    righe += [
        "",
        "## Qualita' della trascrizione",
        "",
        "Dichiarata dal modello atto per atto. Gli atti a affidabilita' bassa",
        "vanno riletti sull'immagine originale prima di usarli.",
        "",
        "| affidabilita | atti |",
        "|---|---|",
    ]
    for livello, quanti in affidabilita.most_common():
        righe.append(f"| {livello} | {quanti} |")

    return "\n".join(righe) + "\n"


def _compatta_intervalli(anni: list[int]) -> str:
    """1810, 1811, 1812, 1820 -> '1810-1812, 1820'."""
    if not anni:
        return "nessuno"
    gruppi: list[str] = []
    inizio = precedente = anni[0]
    for anno in anni[1:]:
        if anno == precedente + 1:
            precedente = anno
            continue
        gruppi.append(str(inizio) if inizio == precedente else f"{inizio}-{precedente}")
        inizio = precedente = anno
    gruppi.append(str(inizio) if inizio == precedente else f"{inizio}-{precedente}")
    return ", ".join(gruppi)


# ---------------------------------------------------------------------------
# Ricostruire senza perdere le prove
# ---------------------------------------------------------------------------
#
# 'costruisci' cancella il file e lo rifa' dai JSON. E' giusto per le
# tabelle che dai JSON discendono — atti, persone, pagine, voci — ed e'
# rovinoso per le due che non ci discendono:
#
#   decisioni   il registro di cio' che e' stato deciso e perche', comprese
#               le decisioni prese da una PERSONA. Il suo modulo dice che
#               "non si azzera"; questo file la azzerava.
#   riletture   le risposte gia' pagate con la quota giornaliera.
#
# C'e' un secondo strato, meno visibile e piu' pericoloso. Le decisioni
# nominano le menzioni per 'persone.id', che e' un rowid assegnato
# nell'ordine di inserimento: bastano duecento atti recuperati in mezzo
# all'archivio perche' ogni id successivo scorra, e ogni correzione
# finisca addosso a un'altra persona. Peggio: l'identita' di una SCHEDA
# e' "P<prima menzione>", quindi scorrerebbe anche l'albero.
#
# La cura e' non fidarsi del rowid ma di una chiave che dipende dal
# contenuto: quale registro, quale numero d'atto, che ruolo, come si
# chiamava. Prima di cancellare si prende quella; dopo si ritrova l'id
# nuovo. Cio' che non si ritrova non si indovina: resta da parte, in
# 'decisioni_orfane', dove si puo' leggere e riconciliare a mano.

# 'id_originale' e 'chiavi' non sono contabilita': sono cio' che rende
# l'orfanezza reversibile. Con la chiave stabile al posto dell'id — che
# dopo una ricostruzione non vuol dire piu' niente — la decisione si puo'
# ritentare al giro dopo, e una menzione ricomparsa (una trascrizione
# aggiunta, un cognome che il glossario ha smesso di travisare) se la
# riprende. Senza, ogni ricostruzione cancellava la tabella che esiste
# apposta per non perdere niente.
SCHEMA_ORFANE = """
CREATE TABLE IF NOT EXISTS decisioni_orfane (
    quando TEXT, azione TEXT, entita TEXT, motivo TEXT, confidenza REAL,
    evidenze TEXT, contraddizioni TEXT, atti TEXT, decisore TEXT,
    modello TEXT, versione_prompt TEXT, versione_algoritmo TEXT, disfa INTEGER,
    perche TEXT, id_originale INTEGER, chiavi TEXT
);
"""


def _chiavi_delle_menzioni(conn: sqlite3.Connection) -> dict:
    """Le menzioni per chiave stabile, e la mappa al contrario.

    La chiave e' quel che identifica una menzione **senza** dipendere
    dall'ordine di inserimento: l'atto (registro e numero, non
    l'immagine, che puo' cambiare), il ruolo, e le due letture grezze.
    L'ordinale scioglie i pochi casi in cui due menzioni dello stesso
    atto sono identiche in tutto — due testimoni omonimi.
    """
    from collections import Counter

    visti: Counter = Counter()
    avanti, indietro = {}, {}
    for riga in conn.execute(
        "SELECT p.id, a.registro, a.numero_atto, a.tipo, p.ruolo, "
        "p.nome_letto, p.cognome_letto FROM persone p "
        "JOIN atti a ON a.id = p.atto ORDER BY p.id"
    ):
        base = tuple("" if x is None else str(x) for x in tuple(riga)[1:])
        visti[base] += 1
        chiave = base + (visti[base],)
        avanti[riga[0]] = chiave
        indietro[chiave] = riga[0]
    return {"per_id": avanti, "per_chiave": indietro}


def _chiavi_degli_atti(conn: sqlite3.Connection) -> dict:
    avanti, indietro = {}, {}
    for riga in conn.execute("SELECT id, registro, numero_atto, tipo FROM atti"):
        chiave = tuple("" if x is None else str(x) for x in tuple(riga)[1:])
        avanti[riga[0]] = chiave
        indietro[chiave] = riga[0]
    return {"per_id": avanti, "per_chiave": indietro}


def conserva(percorso: Path) -> dict:
    """Prende dal database cio' che i JSON non sanno rifare.

    Rende una struttura che :func:`ripristina` sa rimettere dentro, con
    le menzioni gia' tradotte in chiavi stabili.
    """
    if not percorso.exists():
        return {}
    conn = sqlite3.connect(percorso)
    conn.row_factory = sqlite3.Row
    try:
        menzioni = _chiavi_delle_menzioni(conn)["per_id"]
        atti = _chiavi_degli_atti(conn)["per_id"]
        fuori: dict = {"decisioni": [], "riletture": [], "orfane": []}
        try:
            for riga in conn.execute("SELECT * FROM decisioni ORDER BY id"):
                d = dict(riga)
                try:
                    entita = json.loads(d["entita"] or "[]")
                except (TypeError, ValueError):
                    entita = []
                # Le decisioni sull'identita' nominano schede, non menzioni;
                # ma una scheda e' "P<prima menzione>", quindi la chiave e'
                # quella della menzione anche li'.
                d["_entita_chiavi"] = [menzioni.get(x) for x in entita]
                fuori["decisioni"].append(d)
        except sqlite3.OperationalError:
            pass
        try:
            for riga in conn.execute("SELECT * FROM decisioni_orfane"):
                d = dict(riga)
                try:
                    chiavi = json.loads(d.get("chiavi") or "[]")
                except (TypeError, ValueError):
                    chiavi = []
                d["id"] = d.get("id_originale")
                # Le chiavi tornano dal JSON come liste: il confronto le
                # vuole tuple, o nessuna corrispondenza andrebbe a segno.
                d["_entita_chiavi"] = [tuple(k) if k else None for k in chiavi]
                if not chiavi:
                    # Un'orfana senza chiave non si ritenta MAI. Le orfane
                    # scritte prima che questa colonna esistesse portano un
                    # 'entita' fatto di identificatori di un database
                    # cancellato: numeri che oggi indicano qualcun altro.
                    # Riattaccarle con quelli ha messo la correzione di una
                    # bambina di sedici mesi addosso al Sindaco. Il numero
                    # non e' un indizio debole, e' un falso amico.
                    d["_senza_chiave"] = True
                fuori["orfane"].append(d)
        except sqlite3.OperationalError:
            pass
        try:
            for riga in conn.execute("SELECT * FROM riletture"):
                d = dict(riga)
                d["_atto_chiave"] = atti.get(d.get("atto"))
                fuori["riletture"].append(d)
        except sqlite3.OperationalError:
            pass
        return fuori
    finally:
        conn.close()


COLONNE_DECISIONE = (
    "quando", "azione", "entita", "motivo", "confidenza", "evidenze",
    "contraddizioni", "atti", "decisore", "modello", "versione_prompt",
    "versione_algoritmo", "disfa",
)


def ripristina(percorso: Path, conservato: dict) -> dict:
    """Rimette le decisioni e le riletture nel database appena rifatto.

    Cio' che non si riattacca finisce in ``decisioni_orfane`` con il
    motivo scritto: un archivio che perde una decisione in silenzio e'
    peggio di uno che la mette da parte e lo dice.

    E l'orfanezza non e' definitiva. Le orfane dei giri precedenti
    rientrano in questa stessa fila e si ritentano: una menzione puo'
    benissimo ricomparire — una trascrizione aggiunta, un cognome che il
    glossario ha smesso di travisare — e allora la decisione torna a
    valere. ``riprese`` dice quante ne sono tornate.
    """
    if not conservato:
        return {"decisioni": 0, "riletture": 0, "orfane": 0, "riprese": 0}
    from history_maker.ricostruzione import modello as _modello
    from history_maker.ricostruzione import rilettura as _rilettura

    conn = sqlite3.connect(percorso)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_modello.SCHEMA_SQL)
        conn.executescript(_rilettura.SCHEMA_TABELLA)
        conn.executescript(SCHEMA_ORFANE)
        menzioni = _chiavi_delle_menzioni(conn)["per_chiave"]
        atti = _chiavi_degli_atti(conn)["per_chiave"]
        vecchio_a_nuovo: dict = {}
        conti = {"decisioni": 0, "riletture": 0, "orfane": 0,
                 "riprese": 0}
        segnaposto = ",".join("?" * len(COLONNE_DECISIONE))

        # Le orfane dei giri precedenti rientrano nella stessa fila delle
        # decisioni, in ordine di identificatore originale: e' l'unico
        # modo perche' un 'disfa' trovi il suo bersaglio anche quando il
        # bersaglio era orfano e la decisione che lo supera no.
        tutte = sorted(
            list(conservato.get("decisioni", [])) + list(conservato.get("orfane", [])),
            key=lambda d: (d.get("id") is None, d.get("id") or 0),
        )
        for d in tutte:
            chiavi = d.get("_entita_chiavi") or []
            nuove = [menzioni.get(k) if k else None for k in chiavi]
            if d.get("_senza_chiave") or (chiavi and any(x is None for x in nuove)):
                conn.execute(
                    "INSERT INTO decisioni_orfane ("
                    + ",".join(COLONNE_DECISIONE)
                    + ",perche,id_originale,chiavi) VALUES ("
                    + segnaposto + ",?,?,?)",
                    tuple(d.get(c) for c in COLONNE_DECISIONE)
                    + ("nessuna chiave stabile: il bersaglio non e' ricostruibile"
                       if d.get("_senza_chiave") else
                       "una o piu' menzioni non esistono piu' dopo la ricostruzione",
                       d.get("id"), json.dumps(chiavi)),
                )
                conti["orfane"] += 1
                continue
            valori = [d.get(c) for c in COLONNE_DECISIONE]
            valori[COLONNE_DECISIONE.index("entita")] = json.dumps(nuove)
            # 'disfa' nomina un'altra decisione: si traduce con la mappa
            # degli identificatori gia' riscritti.
            if d.get("disfa") is not None:
                valori[COLONNE_DECISIONE.index("disfa")] = vecchio_a_nuovo.get(d["disfa"])
            cursore = conn.execute(
                "INSERT INTO decisioni (" + ",".join(COLONNE_DECISIONE)
                + ") VALUES (" + segnaposto + ")", tuple(valori))
            if d["id"] is not None:
                vecchio_a_nuovo[d["id"]] = cursore.lastrowid
            conti["decisioni"] += 1
            if d.get("perche"):
                conti["riprese"] += 1        # era orfana, ha ritrovato la sua menzione

        for r in conservato.get("riletture", []):
            nuovo = atti.get(r.get("_atto_chiave")) if r.get("_atto_chiave") else None
            if nuovo is None:
                continue        # l'atto non c'e' piu': la risposta non ha bersaglio
            conn.execute(
                "INSERT OR REPLACE INTO riletture (chiave, atto, immagine, esito, "
                "campi, quante_correzioni, modello, versione_prompt, "
                "versione_contesto, quando, stato) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["chiave"], nuovo, r["immagine"], r["esito"], r["campi"],
                 r["quante_correzioni"], r["modello"], r["versione_prompt"],
                 r["versione_contesto"], r["quando"], r["stato"]),
            )
            conti["riletture"] += 1
        conn.commit()
        return conti
    finally:
        conn.close()
