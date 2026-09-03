"""Fase 6: dalle persone riconosciute all'albero genealogico del paese.

Prende cio' che :mod:`identita` ha riconosciuto e lo scrive in tabelle
che si possano interrogare: **chi e' figlio di chi**, **chi ha sposato
chi**, e per ogni persona tutto quello che i registri dicono di lei.

Le tabelle nuove stanno nello stesso database delle altre e si buttano e
si rifanno a ogni esecuzione. E' voluto: la fase 4 puo' crescere — domani
arrivano gli anni che mancano — e questa fase deve poter ripartire da
capo sui dati nuovi senza portarsi dietro niente di vecchio. Nessuna
tabella della fase 4 viene toccata: se qualcosa qui e' sbagliato, si
cancella e si rifa'.

Sulle unioni vale la pena dire una cosa che non e' ovvia. Gli atti di
matrimonio superstiti sono 678; le coppie che si ricavano dagli atti di
**nascita** — ogni volta che un atto nomina un padre e una madre insieme,
cioe' dichiara che sono marito e moglie — sono molte di piu'. Senza
quelle, tutte le famiglie sposate prima del 1809 o negli anni i cui
registri sono perduti resterebbero invisibili, e con loro i loro figli.
Le due provenienze restano distinte in ``origine``, cosi' chi guarda sa
sempre se sta vedendo un matrimonio documentato o una coppia dedotta dal
fatto che hanno avuto figli insieme.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from history_maker import identita, nomi, normalizza, paleografia
from history_maker.config import Config

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
DROP TABLE IF EXISTS individui;
DROP TABLE IF EXISTS menzioni;
DROP TABLE IF EXISTS legami;
DROP TABLE IF EXISTS unioni;
DROP TABLE IF EXISTS individui_fts;

-- Una persona vera, cioe' un gruppo di menzioni riconosciute come la
-- stessa. 'menzioni' dice su quante righe si regge: una persona da
-- quindici menzioni e' documentata, una da una sola e' un nome visto una
-- volta, e chi consulta deve poter distinguere i due casi a colpo
-- d'occhio.
CREATE TABLE individui (
    id              INTEGER PRIMARY KEY,
    nome            TEXT,
    cognome         TEXT,
    sesso           TEXT,
    -- L'anno di nascita e se viene dall'atto di nascita ('certa') o
    -- ricavato dalle eta' dichiarate negli atti ('stimata'). Non e' una
    -- sfumatura: sulla prima si puo' costruire, sulla seconda no.
    anno_nascita    INTEGER,
    nascita_origine TEXT,
    anno_morte      INTEGER,
    -- L'anno entro cui la persona risulta gia' morta anche senza atto,
    -- perche' un registro la nomina come 'fu'. E' il modo di sapere
    -- qualcosa di chi e' morto negli anni che mancano.
    morta_entro     INTEGER,
    anno_primo      INTEGER,
    anno_ultimo     INTEGER,
    professioni     TEXT,
    residenze       TEXT,
    contrade        TEXT,
    varianti_nome   TEXT,
    varianti_cognome TEXT,
    menzioni        INTEGER NOT NULL DEFAULT 0,
    menzioni_incerte INTEGER NOT NULL DEFAULT 0,
    fondata_su      TEXT
);

-- Il ponte fra le righe lette sulle pagine e le persone riconosciute.
-- Ogni riga di 'persone' compare qui una e una sola volta: e' il
-- controllo che nessuna menzione si perda per strada.
CREATE TABLE menzioni (
    persona     INTEGER PRIMARY KEY REFERENCES persone(id),
    individuo   INTEGER NOT NULL REFERENCES individui(id),
    certa       INTEGER NOT NULL DEFAULT 1
);

-- La filiazione. 'atto' e' l'atto che la dichiara: ogni legame di questo
-- archivio si puo' risalire fino alla pagina che lo afferma, e la scheda
-- della persona ne mostra l'immagine.
CREATE TABLE legami (
    figlio      INTEGER NOT NULL REFERENCES individui(id),
    genitore    INTEGER NOT NULL REFERENCES individui(id),
    tipo        TEXT NOT NULL,
    atto        INTEGER REFERENCES atti(id),
    PRIMARY KEY (figlio, genitore, tipo)
);

CREATE TABLE unioni (
    id          INTEGER PRIMARY KEY,
    marito      INTEGER REFERENCES individui(id),
    moglie      INTEGER REFERENCES individui(id),
    anno        INTEGER,
    atto        INTEGER REFERENCES atti(id),
    -- 'matrimonio' se c'e' l'atto, 'figli' se la coppia si ricava dal
    -- fatto che i due compaiono insieme come genitori.
    origine     TEXT NOT NULL
);

CREATE INDEX idx_menzioni_individuo ON menzioni(individuo);
CREATE INDEX idx_legami_genitore ON legami(genitore);
CREATE INDEX idx_legami_figlio   ON legami(figlio);
CREATE INDEX idx_unioni_marito   ON unioni(marito);
CREATE INDEX idx_unioni_moglie   ON unioni(moglie);
CREATE INDEX idx_ind_cognome     ON individui(cognome);
CREATE INDEX idx_ind_nome        ON individui(nome);

-- La ricerca per nome dell'applicazione. Comprende le varianti lette,
-- cosi' che cercare la grafia sbagliata trovi comunque la persona.
CREATE VIRTUAL TABLE individui_fts USING fts5(
    nome, cognome, varianti_nome, varianti_cognome,
    content='individui', content_rowid='id'
);
"""


def costruisci(config: Config) -> Path:
    """Ricostruisce da zero l'albero genealogico dal dataset della fase 4."""
    percorso = config.dataset / "torrebruna.sqlite"
    if not percorso.exists():
        raise FileNotFoundError(
            f"manca {percorso}: prima va costruito il dataset "
            f"(python -m history_maker dataset)"
        )

    conn = sqlite3.connect(percorso)
    conn.row_factory = sqlite3.Row

    logger.info("igiene dei nomi...")
    righe = list(conn.execute("SELECT id, atto, ruolo, nome, cognome FROM persone"))
    if not righe:
        raise ValueError("la tabella 'persone' e' vuota: non c'e' niente da ricostruire")

    genere = nomi.Genere.dal_corpus([(r["ruolo"], r["nome"]) for r in righe])
    bilancia = nomi.Bilancia.dal_corpus([(r["nome"], r["cognome"]) for r in righe])
    scambi = nomi.inversioni_per_atto(
        {r["id"]: r["atto"] for r in righe},
        {r["id"]: (r["nome"], r["cognome"]) for r in righe},
        bilancia,
    )
    logger.info("  nomi e cognomi invertiti: %d", len(scambi))

    correzioni, proposte = nomi.correzioni_dei_nomi([r["nome"] for r in righe], genere)
    logger.info("  varianti del nome corrette: %d (%d da guardare a mano)",
                len(correzioni), len(proposte))

    logger.info("lettura delle famiglie dentro gli atti...")
    menzioni = identita.carica_menzioni(conn, correzioni, genere, scambi)

    logger.info("riconoscimento delle persone su %d menzioni...", len(menzioni))
    persone = identita.riconosci(menzioni)
    logger.info("  %d persone", len(persone))

    _verifica(menzioni, persone)

    contrade = _contrade_unificate(menzioni)
    logger.info("  contrade ricondotte a %d nomi",
                len(set(contrade.values())) or len({m.via for m in menzioni if m.via}))

    logger.info("scrittura delle tabelle...")
    conn.executescript(SCHEMA_SQL)
    attestazione = Counter(m.cognome for m in menzioni if m.cognome)
    # I nomi si pesano come i cognomi: fra 'Paola', 'Piacoma' e 'Giacoma'
    # — tre letture della stessa donna — deve vincere quella che il resto
    # del secolo attesta, non quella che capita per prima nella sua scheda.
    attestazione_nomi = Counter(m.nome for m in menzioni if m.nome)
    di_menzione = _scrivi_individui(
        conn, persone, attestazione, contrade, attestazione_nomi
    )
    legami, impossibili = _scrivi_legami(conn, menzioni, di_menzione)
    unioni = _scrivi_unioni(conn, menzioni, di_menzione)

    # L'ultima parola sulla coerenza. Va qui, dopo che tutto e' scritto,
    # perche' molte impossibilita' si vedono solo a struttura finita: un
    # figlio con due madri lo sai quando hai finito di scrivere le madri,
    # non mentre le scrivi.
    from history_maker import coerenza

    corretti = coerenza.rendi_coerente(conn)

    # Dopo i legami, perche' e' dai legami che si sa chi sia il padre;
    # prima dell'indice, perche' l'indice deve trovare le forme corrette.
    ricuciti = cognomi_dal_padre(conn, attestazione)
    spostati = secondo_nome_finito_nel_cognome(conn)
    inesistenti = cognomi_che_non_esistono(conn, attestazione)
    unificate = varianti_dalla_filiazione(conn, attestazione)
    if inesistenti:
        logger.info("  %d cognomi che nel secolo non esistono, presi dal padre",
                    inesistenti)
    if spostati:
        logger.info("  %d secondi nomi tolti dal campo del cognome", spostati)
    if unificate:
        logger.info("  %d grafie di cognome unificate dalla filiazione", unificate)
    _indicizza(conn)
    conn.commit()

    legami -= sum(
        quanti for regola, quanti in corretti.items() if regola != "date impossibili"
    )
    logger.info("  %d legami di filiazione, %d unioni", legami, unioni)
    if corretti:
        logger.info("  %d conclusioni tolte perche' impossibili (tabella 'scartati')",
                    sum(corretti.values()))
    if impossibili:
        logger.info("  %d legami scartati perche' cronologicamente impossibili",
                    impossibili)
    if ricuciti:
        logger.info("  %d cognomi ricondotti a quello del padre", ricuciti)

    cartella = config.dataset
    _scrivi_proposte(cartella / "glossario-nomi-proposto.yaml", proposte, correzioni)
    relazione = sintesi(conn, correzioni, scambi)
    (cartella / "genealogia.md").write_text(relazione, encoding="utf-8")
    conn.close()
    return percorso


def _verifica(menzioni: list[identita.Menzione], persone: list[identita.Persona]) -> None:
    """Il conto delle menzioni deve tornare, o non si scrive niente.

    E' l'unica garanzia che questo archivio possa davvero dare: che
    nessuna riga letta sulle pagine sia sparita nel riordino. Un albero
    genealogico a cui manca qualcuno non si vede che e' incompleto — sta
    li' con l'aria di essere tutto — e per questo il controllo e' un
    errore e non un avvertimento.
    """
    raccolte = sum(len(p.menzioni) for p in persone)
    if raccolte != len(menzioni):
        raise ValueError(
            f"persa qualche menzione nel riconoscimento: {raccolte} "
            f"raccolte contro {len(menzioni)} lette"
        )
    identificatori = {m.id for p in persone for m in p.menzioni}
    if len(identificatori) != len(menzioni):
        raise ValueError(
            f"qualche menzione e' finita in due persone: {len(identificatori)} "
            f"distinte su {len(menzioni)}"
        )


def _contrade_unificate(menzioni: list[identita.Menzione]) -> dict[str, str]:
    """Riconduce le grafie di una contrada a una sola forma.

    Le contrade di un paese sono un **insieme chiuso**, e questa e' la
    differenza che permette di essere piu' generosi che sui cognomi: non
    esiste la contrada forestiera che un giorno potrebbe comparire, come
    esiste invece la sposa venuta da fuori con un cognome mai visto. Se
    due grafie si somigliano, sono lo stesso posto.

    La fase 4 su questo si limita a **proporre**, e per il suo scopo fa
    bene: quello e' l'archivio delle letture, e li' si conserva cio' che
    c'e' scritto. Qui invece si costruisce la scheda di una persona, dove
    'strada Trascinella' e 'strada Trafficinella' devono comparire come
    un indirizzo solo, altrimenti la stessa famiglia sembra aver traslocato.

    La forma che vince e' la piu' attestata del gruppo, e le altre
    restano comunque leggibili sull'atto, che non viene mai toccato.
    """
    frequenze: Counter[str] = Counter(m.via for m in menzioni if m.via)
    unificate: dict[str, str] = {}
    for gruppo in normalizza._raggruppa(list(frequenze), normalizza.SOGLIA_TOPONIMI):
        if len(gruppo) < 2:
            continue
        capofila = max(gruppo, key=lambda forma: (frequenze[forma], forma))
        for forma in gruppo:
            if forma != capofila:
                unificate[forma] = capofila
    return unificate


def _piu_comune(valori: Counter[str], attestazione: Counter[str] | None = None) -> str | None:
    """La forma da mostrare, fra quelle con cui la persona e' stata letta.

    Contare solo le occorrenze **sue** non basta, e il caso lo dimostra:
    la moglie di Domenico Lella e' letta 'Di Nardo' una volta e 'Doro'
    due. A maggioranza vincerebbe 'Doro' — che nell'intero secolo di
    registri compare una manciata di volte, mentre 'Di Nardo' e' una
    delle famiglie del paese. La forma giusta e' quella minoritaria nella
    sua scheda e schiacciante nell'archivio.

    Quindi ogni forma vale per quante volte questa persona la porta,
    moltiplicato per quanto quella forma e' attestata in tutto il corpus.
    Il logaritmo tiene la seconda parte al suo posto: deve rompere i
    pareggi e ribaltare i quasi-pareggi, non cancellare una lettura
    ripetuta cinque volte a favore di un cognome piu' comune visto una.
    """
    if not valori:
        return None
    if attestazione is None:
        return valori.most_common(1)[0][0]

    def peso(forma: str) -> tuple[float, int, str]:
        return (
            valori[forma] * (1.0 + math.log10(attestazione.get(forma, 0) + 1)),
            valori[forma],
            forma,
        )

    return max(valori, key=peso)


def _elenco(valori) -> str | None:
    """Le forme viste, in ordine di frequenza, separate da barre verticali."""
    if not valori:
        return None
    if isinstance(valori, Counter):
        return " | ".join(f for f, _ in valori.most_common())
    return " | ".join(sorted(v for v in valori if v))


def _scrivi_individui(
    conn: sqlite3.Connection,
    persone: list[identita.Persona],
    attestazione: Counter[str] | None = None,
    contrade: dict[str, str] | None = None,
    attestazione_nomi: Counter[str] | None = None,
) -> dict[int, int]:
    """Scrive gli individui e rende la mappa menzione -> individuo."""
    contrade = contrade or {}
    di_menzione: dict[int, int] = {}
    righe = []
    ponte = []

    for persona in persone:
        anni = [m.anno for m in persona.menzioni if m.anno]
        professioni = Counter(
            m.professione for m in persona.menzioni if m.professione
        )
        residenze = Counter(m.residenza for m in persona.menzioni if m.residenza)
        contrade_sue = Counter(
            contrade.get(m.via, m.via) for m in persona.menzioni if m.via
        )
        letti_nome = Counter(
            m.nome_letto for m in persona.menzioni if m.nome_letto
        )
        letti_cognome = Counter(
            m.cognome_letto for m in persona.menzioni if m.cognome_letto
        )

        righe.append((
            persona.id,
            _piu_comune(persona.nomi, attestazione_nomi),
            _piu_comune(persona.cognomi, attestazione),
            persona.sesso,
            persona.anno_nascita,
            "certa" if persona.nascita_certa is not None
            else ("stimata" if persona.nascite_stimate else None),
            persona.morte,
            persona.morta_entro,
            min(anni) if anni else None,
            max(anni) if anni else None,
            _elenco(professioni),
            _elenco(residenze),
            _elenco(contrade_sue),
            _elenco(letti_nome),
            _elenco(letti_cognome),
            len(persona.menzioni),
            len(persona.incerte),
            _fondamento(persona),
        ))
        for menzione in persona.menzioni:
            di_menzione[menzione.id] = persona.id
            ponte.append((menzione.id, persona.id, 0 if menzione.id in persona.incerte else 1))

    conn.executemany(
        "INSERT INTO individui (id, nome, cognome, sesso, anno_nascita, "
        "nascita_origine, anno_morte, morta_entro, anno_primo, anno_ultimo, "
        "professioni, residenze, contrade, varianti_nome, varianti_cognome, "
        "menzioni, menzioni_incerte, fondata_su) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        righe,
    )
    conn.executemany(
        "INSERT INTO menzioni (persona, individuo, certa) VALUES (?,?,?)", ponte
    )
    return di_menzione


def _indicizza(conn: sqlite3.Connection) -> None:
    """Riempie l'indice di ricerca. Va fatto per ultimo.

    E' una tabella a contenuto esterno: rispecchia 'individui' al momento
    in cui la si riempie, e non si aggiorna da sola. Riempirla prima
    della propagazione dei cognomi lascerebbe nell'indice le forme
    vecchie, e cercare 'Lella' non troverebbe chi e' appena diventato un
    Lella.
    """
    # Su una tabella a contenuto esterno il DELETE normale rilegge
    # 'individui' per togliere i termini riga per riga, e su un indice
    # appena creato quei termini non ci sono: sqlite lo chiama
    # 'database disk image is malformed'. Il comando 'delete-all'
    # svuota l'indice senza guardare il contenuto.
    conn.execute("INSERT INTO individui_fts(individui_fts) VALUES('delete-all')")
    conn.execute(
        "INSERT INTO individui_fts(rowid, nome, cognome, varianti_nome, varianti_cognome) "
        "SELECT id, nome, cognome, varianti_nome, varianti_cognome FROM individui"
    )


# Quanto il cognome del padre deve essere piu' attestato di quello letto
# sul figlio perche' valga la pena sostituirlo.
DOMINANZA_PATERNA = 4


def cognomi_dal_padre(conn: sqlite3.Connection, attestazione: Counter[str]) -> int:
    """Da' al figlio il cognome del padre, quando la sua lettura e' peggiore.

    Un figlio legittimo porta il cognome del padre: non e' una
    probabilita', e' come funziona l'atto di stato civile. Quando le due
    letture divergono, quindi, una delle due e' sbagliata — e la
    sbagliata e' quasi sempre quella meno attestata nel resto del secolo.

    Il caso vero: 'Felle' compare 23 volte in tutto l'archivio, e tutte
    e 23 nei registri dal 1852 al 1859 — e' una mano che legge cosi'
    'Lella', che di volte ne ha 1.375. Marianna, figlia di Filippo,
    risultava 'Marianna Felle' mentre suo padre, la cui scheda si era
    ricomposta sulle altre menzioni, risultava gia' 'Filippo Lella'.

    Si tocca solo cio' che si somiglia e che il padre attesta molto
    meglio: un figlio con un cognome del tutto diverso da quello del
    padre e' un dato storico — un figlio naturale riconosciuto, un
    esposto — e va lasciato stare.
    """
    from history_maker import identita

    cambi: list[tuple[str, int]] = []
    for riga in conn.execute(
        """
        SELECT f.id, f.cognome AS suo, p.cognome AS del_padre
          FROM legami l
          JOIN individui f ON f.id = l.figlio
          JOIN individui p ON p.id = l.genitore
         WHERE l.tipo = 'padre' AND f.cognome IS NOT NULL
           AND p.cognome IS NOT NULL AND f.cognome <> p.cognome
        """
    ):
        suo, del_padre = riga["suo"], riga["del_padre"]
        quante_sue = attestazione.get(suo, 0)
        quante_sue = quante_sue if quante_sue else 1
        if attestazione.get(del_padre, 0) < quante_sue * DOMINANZA_PATERNA:
            continue
        if paleografia.somiglianza(suo, del_padre) < identita.SOGLIA_GRAFIA:
            continue
        cambi.append((del_padre, riga["id"]))

    conn.executemany("UPDATE individui SET cognome = ? WHERE id = ?", cambi)
    return len(cambi)


# Quante volte una coppia (cognome del figlio, cognome del padre) deve
# ricorrere perche' valga come prova che sono lo stesso cognome.
#
# Due, non tre: il lavoro pesante lo fa la soglia di somiglianza, che
# pretende gia' 0,80 fra le due forme. Misurato, le varianti vere che
# restavano fuori con tre — 'Femminilla' per 'Femminilli', 'Iannicelli'
# per 'Jannicelli', 'De Plato' per 'De Plata' — ricorrono esattamente due
# volte; e cio' che ricorre spesso ma non si somiglia — 'Minchilli'
# figlio di 'Cicchillitti', sei volte — lo ferma la somiglianza, non il
# conteggio.
RICORRENZE_PER_VARIANTE = 2


def varianti_dalla_filiazione(
    conn: sqlite3.Connection, attestazione: Counter[str]
) -> int:
    """Due cognomi legati da una filiazione sono lo stesso cognome.

    E' la prova migliore che l'archivio possa dare su una variante di
    grafia, e non costa niente perche' e' gia' dentro i dati. Un figlio
    porta il cognome del padre: non e' una probabilita', e' come funziona
    l'atto di stato civile. Quindi ogni volta che un 'Cicchillitti'
    risulta figlio di un 'Cicchillitto', quelle due forme sono la stessa
    — e se succede cento volte non e' piu' nemmeno discutibile.

    E' il caso che :func:`cognomi_dal_padre` non sa toccare, e per una
    ragione giusta: quella funzione corregge il figlio quando la sua
    lettura e' **peggiore** di quella del padre, e si ferma quando tutte
    e due le forme sono ben attestate. 'Cicchillitti' ricorre 985 volte,
    'Cicchillitto' 360: nessuna delle due e' un errore di lettura, sono
    due grafie di una famiglia sola, e vanno unificate invece che
    corrette. La forma che sopravvive e' la piu' attestata nel secolo.

    Non si tocca cio' che non si somiglia: un figlio con un cognome del
    tutto diverso da quello del padre e' un dato storico — un figlio
    naturale, un esposto — e :mod:`qualita` lo segnala a parte.
    """
    coppie: Counter[tuple[str, str]] = Counter()
    for riga in conn.execute(
        """
        SELECT f.cognome AS suo, p.cognome AS del_padre
          FROM legami l
          JOIN individui f ON f.id = l.figlio
          JOIN individui p ON p.id = l.genitore
         WHERE l.tipo = 'padre' AND f.cognome IS NOT NULL
           AND p.cognome IS NOT NULL AND f.cognome <> p.cognome
        """
    ):
        coppie[(riga["suo"], riga["del_padre"])] += 1

    unificate: dict[str, str] = {}
    for (suo, del_padre), quante in coppie.items():
        if quante < RICORRENZE_PER_VARIANTE:
            continue
        if paleografia.somiglianza(suo, del_padre) < identita.SOGLIA_COGNOME:
            continue
        vince, perde = (
            (del_padre, suo)
            if attestazione.get(del_padre, 0) >= attestazione.get(suo, 0)
            else (suo, del_padre)
        )
        unificate.setdefault(perde, vince)

    # Nessuna catena: se A finisce in B e B in C, tutti finiscono in C.
    for perde in list(unificate):
        visto = {perde}
        vince = unificate[perde]
        while vince in unificate and vince not in visto:
            visto.add(vince)
            vince = unificate[vince]
        unificate[perde] = vince

    for perde, vince in unificate.items():
        conn.execute(
            "UPDATE individui SET cognome = ? WHERE cognome = ?", (vince, perde)
        )
        logger.debug("variante dalla filiazione: %s -> %s", perde, vince)

    return len(unificate) + _allinea_coppia_al_padre(conn, attestazione)


# Quante volte un cognome deve comparire in tutto il secolo per essere
# una famiglia invece che una lettura sbagliata. Due: un cognome vero,
# in un paese, lo portano un padre e un figlio come minimo.
COGNOME_INESISTENTE = 2


def cognomi_che_non_esistono(
    conn: sqlite3.Connection, attestazione: Counter[str]
) -> int:
    """Il figlio con un cognome che nel secolo non esiste prende quello del padre.

    Marzio, figlio di Filippo Lella, risultava 'Marzio d'Andria Motta' —
    e "d'Andria Motta" compare **una volta sola** in settemila atti. Un
    cognome vero, in un paese di duemila anime, lo portano come minimo un
    padre e un figlio: uno che appare una volta e mai piu' non e' una
    famiglia, e' una riga letta male.

    :func:`cognomi_dal_padre` non lo tocca perche' pretende che le due
    forme si somiglino, e quella guardia serve — un figlio con un cognome
    del tutto diverso da quello del padre puo' essere un figlio naturale
    riconosciuto o un esposto, che e' un dato storico e non un errore. Ma
    l'esposto un padre dichiarato non ce l'ha, e il figlio naturale porta
    un cognome che nell'archivio ricorre. La rarita' assoluta e' quello
    che distingue i due casi, e qui si guarda quella invece della grafia.
    """
    corretti = 0
    for riga in conn.execute(
        """
        SELECT l.figlio, f.cognome AS suo, p.cognome AS del_padre
          FROM legami l
          JOIN individui f ON f.id = l.figlio
          JOIN individui p ON p.id = l.genitore
         WHERE l.tipo = 'padre' AND f.cognome IS NOT NULL
           AND p.cognome IS NOT NULL AND f.cognome <> p.cognome
        """
    ).fetchall():
        if attestazione.get(riga["suo"], 0) > COGNOME_INESISTENTE:
            continue
        if attestazione.get(riga["del_padre"], 0) <= COGNOME_INESISTENTE:
            continue
        conn.execute(
            "UPDATE individui SET cognome = ? WHERE id = ?",
            (riga["del_padre"], riga["figlio"]),
        )
        logger.debug("cognome inesistente: %s -> %s per #%d",
                     riga["suo"], riga["del_padre"], riga["figlio"])
        corretti += 1
    return corretti


def _allinea_coppia_al_padre(
    conn: sqlite3.Connection, attestazione: Counter[str]
) -> int:
    """Le varianti che ricorrono una volta sola, allineate sul posto.

    Sopra si cambia una grafia in **tutto** l'archivio, e per farlo si
    pretende che ricorra: rinominare ottocento persone sulla fede di un
    solo atto sarebbe sproporzionato. Ma la coppia padre-figlio che si e'
    vista una volta sola resta comunque una prova su **quelle due
    persone**, e li' la correzione si puo' fare senza toccare nessun
    altro: 'Del Porto' figlio di 'del Torto' e' una famiglia sola, che il
    caso si ripeta altrove o no.

    Fra le due forme vince la piu' attestata nel secolo, e si allineano
    tutti e due — il figlio e il padre — perche' il disaccordo e' fra due
    letture di uno stesso cognome, e non si sa a priori quale delle due
    pagine abbia sbagliato.
    """
    corretti = 0
    for riga in conn.execute(
        """
        SELECT l.figlio, l.genitore, f.cognome AS suo, p.cognome AS del_padre
          FROM legami l
          JOIN individui f ON f.id = l.figlio
          JOIN individui p ON p.id = l.genitore
         WHERE l.tipo = 'padre' AND f.cognome IS NOT NULL
           AND p.cognome IS NOT NULL AND f.cognome <> p.cognome
        """
    ).fetchall():
        suo, del_padre = riga["suo"], riga["del_padre"]
        if paleografia.somiglianza(suo, del_padre) < identita.SOGLIA_COGNOME:
            continue
        vince = (
            del_padre
            if attestazione.get(del_padre, 0) >= attestazione.get(suo, 0)
            else suo
        )
        conn.execute(
            "UPDATE individui SET cognome = ? WHERE id IN (?, ?) AND cognome <> ?",
            (vince, riga["figlio"], riga["genitore"], vince),
        )
        corretti += 1
    return corretti


# Quante volte piu' spesso una forma deve comparire come nome di
# battesimo che come cognome perche' si possa dire che e' un nome finito
# nel posto sbagliato. Cinque e' largo: 'Angela' e' un nome e basta,
# 'Marianacci' e' un cognome e basta, e le forme che fanno davvero le due
# cose — 'Salvatore', 'Domenico' — restano fuori e non si toccano.
DOMINANZA_DEL_NOME = 5


def secondo_nome_finito_nel_cognome(conn: sqlite3.Connection) -> int:
    """Il secondo nome del neonato letto come se fosse il suo cognome.

    Il formulario dell'atto di nascita **non scrive il cognome del
    bambino**: lo da' per implicito nel nome del padre. Quando il nome e'
    doppio — 'Francesco Antonio', 'Maria Giustina' — il modello che legge
    la pagina trova due parole dove ne aspetta due, e mette la seconda
    nel campo del cognome. Il risultato e' 'Francesco Antonio figlio di
    Domenico Lella': un bambino che nel proprio atto di nascita porta un
    cognome che non e' quello di suo padre.

    Non e' un errore di lettura — la grafia e' stata decifrata bene, e
    rileggere la pagina non ha nessun motivo di andare meglio — e' un
    errore di **campo**. Si corregge con una regola, e la regola ha una
    prova: quella parola, in tutto il resto dell'archivio, e' un nome di
    battesimo e non un cognome.

    Il cognome vero diventa quello del padre, e ``cognome_origine``
    diventa 'padre': chi consulta continua a vedere che li' la pagina
    non lo scriveva.
    """
    nomi_visti: Counter[str] = Counter()
    cognomi_visti: Counter[str] = Counter()
    for riga in conn.execute("SELECT nome, cognome FROM persone"):
        for parte in (riga["nome"] or "").split():
            nomi_visti[parte.lower()] += 1
        if riga["cognome"]:
            cognomi_visti[riga["cognome"].lower()] += 1

    corretti = 0
    for riga in conn.execute(
        """
        SELECT p.id, m.individuo, p.nome, p.cognome, g.cognome AS del_padre
          FROM legami l
          JOIN menzioni m ON m.individuo = l.figlio
          JOIN persone p ON p.id = m.persona
          JOIN individui g ON g.id = l.genitore
         WHERE l.tipo = 'padre' AND p.ruolo IN ('neonato', 'neonata',
                                                'defunto', 'defunta')
           AND p.cognome_origine = 'atto' AND p.cognome IS NOT NULL
           AND g.cognome IS NOT NULL AND p.cognome <> g.cognome
        """
    ).fetchall():
        chiave = riga["cognome"].lower()
        if " " in riga["cognome"]:
            continue
        if nomi_visti[chiave] < DOMINANZA_DEL_NOME:
            continue
        if nomi_visti[chiave] < cognomi_visti.get(chiave, 0) * DOMINANZA_DEL_NOME:
            continue
        # Si corregge la **scheda**, non la riga letta sulla pagina.
        # 'persone' e' della fase 4 e conserva cio' che c'era scritto:
        # toccarla renderebbe la fase 6 non ripetibile — la seconda
        # esecuzione non troverebbe piu' niente da correggere e darebbe
        # un archivio diverso dalla prima. La riga resta com'e', e nella
        # scheda si vede fra le grafie lette.
        conn.execute(
            "UPDATE individui SET nome = ?, cognome = ? WHERE id = ?",
            (
                f"{riga['nome']} {riga['cognome']}".strip(),
                riga["del_padre"],
                riga["individuo"],
            ),
        )
        corretti += 1
    return corretti


def _fondamento(persona: identita.Persona) -> str:
    """Su che prova si regge questa persona, detto in una parola.

    Chi consulta un archivio ricostruito deve poter sapere, senza aprire
    niente, quanto quella scheda sia solida.
    """
    if persona.nascita_certa is not None:
        return "atto di nascita"
    if persona.padri and persona.madri:
        return "genitori dichiarati"
    if persona.padri or persona.madri:
        return "un genitore dichiarato"
    if persona.coniugi:
        return "coniuge dichiarato"
    if len(persona.menzioni) > 1:
        return "menzioni concordi"
    return "una sola menzione"


# Quanti anni ci vogliono, come minimo, fra un genitore e un figlio.
# Non e' l'eta' tipica: e' il limite oltre il quale il legame e'
# **impossibile**, e serve solo a buttare via cio' che non puo' essere.
ETA_MINIMA_GENITORE = 13

# E quanti al massimo. Diverso per padre e madre, perche' la vita fertile
# lo e'.
ETA_MASSIMA = {"padre": 75, "madre": 55}

# Quanto un figlio puo' nascere DOPO la morte del genitore. Per il padre
# e' una gravidanza — succede, e l'atto lo scrive 'figlio postumo'; per
# la madre non e' niente.
POSTUMO = {"padre": 1, "madre": 0}

# Il gioco da lasciare quando l'anno di nascita del figlio non viene
# dall'atto di nascita ma da un'eta' dichiarata: i registri le
# arrotondano, ed e' lo stesso scarto che il riconoscimento gia' tollera.
SCARTO_STIMA = 3


def _cronologia_possibile(
    figlio: dict, genitore: dict, tipo: str
) -> bool:
    """Se un genitore puo' davvero aver generato quel figlio.

    Manca questo controllo e l'albero produce mostri visibili a occhio:
    Filippo Lella, nato verso il 1750, figlio di due persone nate dopo di
    lui. Nasce dal riconoscimento — due omonimi finiti nella stessa
    scheda — ma il posto giusto per fermarlo e' qui, sul legame, perche'
    e' qui che l'assurdo diventa visibile.

    Si scarta solo cio' che e' impossibile, non cio' che e' improbabile:
    gli anni di nascita sono per lo piu' **stimati** dalle eta'
    dichiarate, che i registri arrotondano di parecchio, e stringere
    troppo butterebbe via parentele buone insieme a quelle sbagliate.
    """
    anno_figlio = figlio.get("anno_nascita")
    if anno_figlio is None:
        return True

    # Nessuna donna partorisce dopo essere morta, e nessun uomo genera
    # oltre i nove mesi dalla propria. E' il piu' netto dei controlli, e
    # a differenza dell'eta' non ha bisogno di margini di dottrina:
    # l'anno di morte viene dall'atto di morte, che e' una data scritta.
    # Il margine che c'e' non e' sul fatto, e' sulla **stima**: quando
    # l'anno di nascita del figlio e' ricavato da un'eta' dichiarata, e
    # non da un atto di nascita, e' arrotondato come tutte le eta'.
    morte = genitore.get("anno_morte")
    if morte is not None:
        margine = 0 if figlio.get("nascita_origine") == "certa" else SCARTO_STIMA
        if anno_figlio > morte + POSTUMO.get(tipo, 0) + margine:
            return False

    anno_genitore = genitore.get("anno_nascita")
    if anno_genitore is None:
        return True
    divario = anno_figlio - anno_genitore
    return ETA_MINIMA_GENITORE <= divario <= ETA_MASSIMA.get(tipo, 75)


def _scrivi_legami(
    conn: sqlite3.Connection,
    menzioni: list[identita.Menzione],
    di_menzione: dict[int, int],
) -> tuple[int, int]:
    """La filiazione, presa da dentro gli atti e tradotta in individui."""
    legami: dict[tuple[int, int, str], int] = {}
    for menzione in menzioni:
        figlio = di_menzione.get(menzione.id)
        if figlio is None:
            continue
        for riferimento, tipo in ((menzione.padre, "padre"), (menzione.madre, "madre")):
            if riferimento is None:
                continue
            genitore = di_menzione.get(riferimento)
            # Una persona non puo' essere genitore di se stessa: capita
            # quando due menzioni dello stesso atto finiscono nella stessa
            # scheda, ed e' un segnale che li' il riconoscimento ha unito
            # troppo. Meglio saltare il legame che scrivere un ciclo.
            if genitore is None or genitore == figlio:
                continue
            legami.setdefault((figlio, genitore, tipo), menzione.atto)

    anni = {
        riga["id"]: dict(riga)
        for riga in conn.execute(
            "SELECT id, anno_nascita, nascita_origine, anno_morte FROM individui"
        )
    }
    buoni, impossibili = [], 0
    for (figlio, genitore, tipo), atto in legami.items():
        if _cronologia_possibile(anni.get(figlio, {}), anni.get(genitore, {}), tipo):
            buoni.append((figlio, genitore, tipo, atto))
        else:
            impossibili += 1

    conn.executemany(
        "INSERT OR IGNORE INTO legami (figlio, genitore, tipo, atto) VALUES (?,?,?,?)",
        buoni,
    )
    return len(buoni), impossibili


def _scrivi_unioni(
    conn: sqlite3.Connection,
    menzioni: list[identita.Menzione],
    di_menzione: dict[int, int],
) -> int:
    """Le coppie, dagli atti di matrimonio e dai figli avuti insieme."""
    per_id = {m.id: m for m in menzioni}
    unioni: dict[tuple[int, int], dict] = {}

    for menzione in menzioni:
        if menzione.coniuge is None:
            continue
        altra = per_id.get(menzione.coniuge)
        if altra is None:
            continue
        uno, due = di_menzione.get(menzione.id), di_menzione.get(altra.id)
        if uno is None or due is None or uno == due:
            continue

        marito, moglie = _ordina_coppia(menzione, altra, uno, due)
        chiave = (marito, moglie)
        documentata = menzione.tipo_atto in ("matrimonio", "pubblicazione")
        precedente = unioni.get(chiave)
        if precedente is None:
            unioni[chiave] = {
                "anno": menzione.anno,
                "atto": menzione.atto,
                "origine": "matrimonio" if documentata else "figli",
            }
            continue
        # L'atto di matrimonio, se c'e', ha sempre la precedenza su una
        # coppia dedotta: e' la fonte che dice la data vera.
        if documentata and precedente["origine"] != "matrimonio":
            precedente.update(anno=menzione.anno, atto=menzione.atto, origine="matrimonio")
        elif documentata == (precedente["origine"] == "matrimonio"):
            if menzione.anno and menzione.anno < (precedente["anno"] or 9999):
                precedente.update(anno=menzione.anno, atto=menzione.atto)

    conn.executemany(
        "INSERT INTO unioni (marito, moglie, anno, atto, origine) VALUES (?,?,?,?,?)",
        [
            (marito, moglie, dati["anno"], dati["atto"], dati["origine"])
            for (marito, moglie), dati in unioni.items()
        ],
    )
    return len(unioni)


def _ordina_coppia(
    una: identita.Menzione, altra: identita.Menzione, uno: int, due: int
) -> tuple[int, int]:
    """Marito e moglie nell'ordine, dal ruolo o dal sesso.

    L'ordine non e' una preferenza: la coppia e' la chiave con cui si
    riconoscono le unioni ripetute, e senza un ordine stabile la stessa
    coppia finirebbe due volte, una per verso.
    """
    if una.ruolo in ("padre", "sposo", "marito") or altra.ruolo in ("madre", "sposa", "moglie"):
        return uno, due
    if altra.ruolo in ("padre", "sposo", "marito") or una.ruolo in ("madre", "sposa", "moglie"):
        return due, uno
    if una.sesso == "M" or altra.sesso == "F":
        return uno, due
    if altra.sesso == "M" or una.sesso == "F":
        return due, uno
    return (uno, due) if uno < due else (due, uno)


def _scrivi_proposte(
    percorso: Path, proposte: list[normalizza.Proposta], correzioni: dict[str, str]
) -> None:
    """Le varianti dei nomi su cui il calcolo non se la sente di decidere.

    Vanno sotto gli occhi di chi conosce il paese, che e' l'unico a poter
    dire se 'Amabile' e 'Amabilia' siano la stessa persona. Il formato e'
    quello del glossario dei cognomi gia' in uso.
    """
    righe = [
        "# Varianti dei NOMI DI BATTESIMO su cui decidere a mano.",
        "#",
        "# Le correzioni gia' applicate da sole stanno in fondo, per",
        "# controllo. Quelle qui sopra no: sono coppie di forme troppo",
        "# simili per essere estranee e troppo alla pari perche' la",
        "# frequenza dica quale sia quella buona.",
        "#",
        "# Il raggruppamento non unisce mai due nomi di sesso diverso:",
        "# 'Domenica' non puo' diventare 'Domenico'.",
        "",
        "proposte:",
    ]
    for proposta in proposte:
        righe.append(f"  # {proposta.motivo}")
        righe.append(f"  {proposta.letto}: {proposta.proposto}")
    righe.extend(["", "# --- applicate da sole ---", "applicate:"])
    for letto, forma in sorted(correzioni.items()):
        righe.append(f"  {letto}: {forma}")
    percorso.write_text("\n".join(righe) + "\n", encoding="utf-8")


def sintesi(
    conn: sqlite3.Connection, correzioni: dict[str, str], scambi: set[int]
) -> str:
    """Un quadro di cosa l'albero contiene e di quanto si regge."""
    def uno(sql: str, *parametri):
        riga = conn.execute(sql, parametri).fetchone()
        return riga[0] if riga else 0

    individui = uno("SELECT COUNT(*) FROM individui")
    menzioni = uno("SELECT COUNT(*) FROM menzioni")
    con_genitori = uno("SELECT COUNT(DISTINCT figlio) FROM legami")
    con_figli = uno("SELECT COUNT(DISTINCT genitore) FROM legami")
    unioni = uno("SELECT COUNT(*) FROM unioni")
    documentate = uno("SELECT COUNT(*) FROM unioni WHERE origine = 'matrimonio'")
    nascita_certa = uno("SELECT COUNT(*) FROM individui WHERE nascita_origine = 'certa'")
    nascita_stimata = uno("SELECT COUNT(*) FROM individui WHERE nascita_origine = 'stimata'")
    con_morte = uno("SELECT COUNT(*) FROM individui WHERE anno_morte IS NOT NULL")
    morte_dedotta = uno(
        "SELECT COUNT(*) FROM individui WHERE anno_morte IS NULL AND morta_entro IS NOT NULL"
    )
    sole = uno("SELECT COUNT(*) FROM individui WHERE menzioni = 1")

    righe = [
        "# L'albero genealogico di Torrebruna",
        "",
        "Ricostruito dalle trascrizioni degli atti di stato civile. Ogni",
        "legame di questo archivio si puo' risalire fino all'atto che lo",
        "dichiara, e da li' fino all'immagine della pagina.",
        "",
        "## Cosa contiene",
        "",
        f"- Persone riconosciute: **{individui}**",
        f"- Menzioni riordinate: **{menzioni}** (nessuna esclusa)",
        f"- Persone di cui si conoscono i genitori: **{con_genitori}**",
        f"- Persone di cui si conoscono i figli: **{con_figli}**",
        f"- Coppie: **{unioni}**, di cui con atto di matrimonio: **{documentate}**",
        "",
        "## Quanto si sa delle date",
        "",
        f"- Anno di nascita dall'atto di nascita: **{nascita_certa}**",
        f"- Anno di nascita ricavato dalle eta' dichiarate: **{nascita_stimata}**",
        f"- Anno di morte dall'atto di morte: **{con_morte}**",
        f"- Gia' morte a una certa data, senza atto: **{morte_dedotta}**",
        "",
        "Le persone gia' morte senza atto sono il guadagno degli anni che",
        "mancano: un registro che nomina 'il fu Domenico Pelliccia' nel",
        "1863 dice che Domenico e' morto prima del 1863, e lo dice anche",
        "se il registro dei morti di quell'anno e' perduto.",
        "",
        "## L'igiene dei nomi",
        "",
        f"- Nomi e cognomi rimessi al loro posto: **{len(scambi)}** menzioni",
        f"- Varianti del nome di battesimo unificate: **{len(correzioni)}** forme",
        "",
        "I registri dal 1881 scrivono 'Cognome Nome' invece di 'Nome",
        "Cognome'. Le menzioni rimesse a posto sono quelle in cui la",
        "statistica dell'intero corpus dice che le due colonne erano",
        "scambiate. La lettura originale resta in ``varianti_nome`` e",
        "``varianti_cognome``.",
        "",
        "## Quanto e' solida ogni scheda",
        "",
        "| fondamento | persone |",
        "|---|---|",
    ]
    for fondamento, quante in conn.execute(
        "SELECT fondata_su, COUNT(*) FROM individui GROUP BY 1 ORDER BY 2 DESC"
    ):
        righe.append(f"| {fondamento} | {quante} |")

    righe += [
        "",
        f"Le persone che si reggono su una menzione sola sono **{sole}**.",
        "Non sono un difetto della ricostruzione: sono per lo piu' bambini",
        "morti in fasce, madri che compaiono in un atto solo, e testimoni",
        "di passaggio. Restano nell'archivio come tutti gli altri.",
        "",
        "## Le famiglie piu' numerose",
        "",
        "| genitori | figli |",
        "|---|---|",
    ]
    for padre, madre, figli in conn.execute(
        """
        SELECT p.nome || ' ' || COALESCE(p.cognome, ''),
               m.nome || ' ' || COALESCE(m.cognome, ''),
               COUNT(*) AS quanti
          FROM unioni u
          JOIN individui p ON p.id = u.marito
          JOIN individui m ON m.id = u.moglie
          JOIN legami lp ON lp.genitore = u.marito AND lp.tipo = 'padre'
          JOIN legami lm ON lm.genitore = u.moglie AND lm.tipo = 'madre'
                        AND lm.figlio = lp.figlio
         GROUP BY u.id ORDER BY quanti DESC LIMIT 15
        """
    ):
        righe.append(f"| {padre} e {madre} | {figli} |")

    return "\n".join(righe) + "\n"
