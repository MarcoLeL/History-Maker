"""L'orchestrazione: dal database della fase 4 all'albero e ai suoi dubbi.

Questa e' la fase 6 nuova. Legge le stesse tabelle di quella vecchia,
scrive le stesse quattro piu' cinque, e in mezzo fa un lavoro diverso.

Le tabelle in uscita
--------------------

``individui``, ``menzioni``, ``legami``, ``unioni`` hanno i nomi e le
colonne di prima, piu' ``confidenza``, ``stato`` e ``prove``. E' voluto:
l'applicazione dell'albero e il controllo di qualita' leggono quelle, e un
impianto nuovo che obblighi a riscrivere tutto cio' che gli sta attorno
non si puo' confrontare con quello che sostituisce. **Il confronto fra i
due e' l'unica prova che il nuovo sia meglio**, e va reso facile.

``fatti`` e' la novita' che cambia il modello: ogni affermazione
documentaria con la sua fonte, il suo anno e la sua confidenza. E' cio'
che permette a una persona di essere bovaro nel 1850 e contadino nel 1875
senza che il secondo cancelli il primo.

``anomalie`` e' l'elenco di cio' che non torna, con priorita' e impatto.
``decisioni`` e' l'audit trail, e **non si cancella** a ogni esecuzione.
``verifiche`` e' la cache delle domande all'immagine.

Il conto che deve tornare
-------------------------

Nessuna menzione va persa e nessuna finisce in due schede. E' l'unica
garanzia che questo archivio possa davvero dare, ed e' un errore e non un
avvertimento: un albero genealogico a cui manca qualcuno non si vede che
e' incompleto — sta li' con l'aria di essere tutto.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path

from history_maker import paleografia
from history_maker.config import Config
from history_maker.ricostruzione import (
    anomalie as mod_anomalie, attributi, cache, deduzione, lettura,
    modello as mod, registro, risoluzione,
)
from history_maker.ricostruzione.scheda import Scheda

logger = logging.getLogger(__name__)

VERSIONE_ALGORITMO = "1.0.0"

# Fin dove puo' arrivare la cronologia di una filiazione. Sono gli stessi
# limiti della fase 6 vecchia, e restano perche' non vengono da una
# statistica ma dall'anagrafe: sotto i tredici anni non si e' genitori.
ETA_MINIMA_GENITORE = 13
ETA_MASSIMA = {"padre": 75, "madre": 55}
POSTUMO = {"padre": 1, "madre": 0}
SCARTO_STIMA = 5


def costruisci(
    config: Config, giri: int = risoluzione.GIRI_MASSIMI, senza_cache: bool = False
) -> Path:
    """Ricostruisce l'albero e lo scrive nel database della fase 4."""
    percorso = config.dataset / "torrebruna.sqlite"
    if not percorso.exists():
        raise FileNotFoundError(
            f"manca {percorso}: prima va costruito il dataset "
            f"(python -m history_maker dataset)"
        )

    conn = sqlite3.connect(percorso)
    conn.row_factory = sqlite3.Row

    deposito = None if senza_cache else cache.Deposito(config.dataset)

    logger.info("lettura e interpretazione delle menzioni...")
    corpus = lettura.carica(conn, deposito)
    for voce, quante in sorted(corpus.interpretazioni.items()):
        logger.info("  %s: %d", voce, quante)

    logger.info("vocabolario dei mestieri e delle contrade...")
    vocabolari = _vocabolari(config, corpus, deposito)
    for nome, vocabolario in sorted(vocabolari.items()):
        ricondotte = sum(
            1 for forma, testa in vocabolario.capofila.items() if forma != testa
        )
        logger.info("  %s: %d forme ricondotte a %d",
                    nome, ricondotte, len(vocabolario.famiglie))
    # Prima della risoluzione, non dopo: e' il confronto fra due schede
    # che deve sapere che 'Agrimenfore' e 'Agrimensore' sono lo stesso
    # mestiere. Costruirli dopo li rendeva utili solo alla scrittura.
    corpus.vocabolari = vocabolari

    imposte = registro.imposizioni(conn)
    if imposte["unire"] or imposte["separare"]:
        logger.info("decisioni gia' prese da riapplicare: %d unioni, %d separazioni",
                    len(imposte["unire"]), len(imposte["separare"]))
    esito = risoluzione.ricostruisci(
        corpus, giri=giri, deposito=deposito, imposizioni=imposte
    )
    _verifica(corpus, esito)

    logger.info("anomalie e coda dei dubbi...")
    esito.anomalie.extend(mod_anomalie.tutte(esito))
    esito.anomalie.extend(attributi.anomalie(esito, vocabolari))
    logger.info("  %d anomalie", len(esito.anomalie))

    logger.info("scrittura delle tabelle...")
    conn.executescript(mod.SCHEMA_SQL)
    numeri = _scrivi_individui(conn, esito, vocabolari)
    fatti = _scrivi_fatti(conn, esito, numeri, vocabolari)
    legami, scartati = _scrivi_legami(conn, esito, numeri)
    unioni = _scrivi_unioni(conn, esito, numeri)
    _scrivi_anomalie(conn, esito, numeri)
    # Dopo le anomalie, non prima: la correzione **chiude** la
    # segnalazione che l'ha motivata, e non puo' chiudere una riga che
    # non e' ancora stata scritta.
    ricondotti = cognomi_dal_padre(conn, esito, numeri)
    if ricondotti:
        logger.info("  %d cognomi ricondotti a quello del padre", ricondotti)

    # Per ultimo, quando l'albero c'e' gia': i genitori che nessun atto
    # scrive, dedotti dal nome dei nipoti. Va dopo tutto il resto perche'
    # legge l'albero finito — chi sono i figli di chi, e in che ordine
    # sono nati — e perche' cio' che aggiunge non deve poter influenzare
    # nessuna delle decisioni prese sopra.
    proposte = deduzione.proposte(conn)
    dedotti = deduzione.applica(conn, proposte)
    if proposte:
        logger.info("  %d genitori dedotti dal nome dei nipoti, %d ambigui",
                    dedotti, sum(1 for p in proposte if not p.sicura))
        (config.dataset / "deduzioni.md").write_text(
            deduzione.rapporto(conn, proposte), encoding="utf-8"
        )
    registro.salva(conn, esito.decisioni)
    _indicizza(conn)
    conn.commit()

    logger.info("  %d individui, %d legami, %d unioni, %d fatti",
                len(esito.schede), legami, unioni, fatti)
    if scartati:
        logger.info("  %d legami scartati perche' cronologicamente impossibili",
                    scartati)

    relazione = sintesi(conn, corpus, esito)
    (config.dataset / "ricostruzione.md").write_text(relazione, encoding="utf-8")
    (config.dataset / "anomalie.md").write_text(
        mod_anomalie.rapporto(esito, numeri), encoding="utf-8"
    )
    conn.close()
    return percorso


def _vocabolari(config: Config, corpus, deposito) -> dict:
    """I vocabolari del paese, dalla cache quando si puo'.

    La chiave comprende il glossario: quelle equivalenze le dichiara una
    persona, e cambiarle deve rifare il vocabolario anche se le menzioni
    sono le stesse.
    """
    from history_maker.glossario import Glossario

    glossario = Glossario.carica(config.glossario)
    if deposito is None:
        return attributi.vocabolari(corpus, glossario)
    chiave = cache.impronta(
        len(corpus.menzioni),
        sorted(corpus.frequenze_professione.items()),
        sorted(corpus.frequenze_contrada.items()),
        sorted((glossario.mestieri or {}).items()),
        attributi.SOGLIA_VOCABOLARIO, attributi.DOMINANZA,
    )
    return deposito.ottieni(
        "vocabolari", chiave, lambda: attributi.vocabolari(corpus, glossario)
    )


def _verifica(corpus, esito: risoluzione.Esito) -> None:
    """Il conto delle menzioni deve tornare, o non si scrive niente."""
    raccolte = sum(scheda.quante for scheda in esito.schede.values())
    if raccolte != corpus.totale:
        raise ValueError(
            f"persa qualche menzione nella ricostruzione: {raccolte} raccolte "
            f"contro {corpus.totale} lette"
        )
    identificatori = {
        menzione.id for scheda in esito.schede.values() for menzione in scheda.menzioni
    }
    if len(identificatori) != corpus.totale:
        raise ValueError(
            f"qualche menzione e' finita in due schede: {len(identificatori)} "
            f"distinte su {corpus.totale}"
        )


# ---------------------------------------------------------------------------
# Le tabelle
# ---------------------------------------------------------------------------

def _scrivi_individui(
    conn: sqlite3.Connection, esito: risoluzione.Esito, vocabolari: dict | None = None
) -> dict:
    """Scrive gli individui e rende la mappa chiave stabile -> numero di riga.

    Il numero di riga serve alle chiavi esterne e cambia a ogni
    ricostruzione; la **chiave** no, ed e' quella che va usata per
    annotare, discutere e ritrovare una persona domani.
    """
    attestazione_cognomi = esito.corpus.frequenze_cognome
    attestazione_nomi = esito.corpus.frequenze_nome
    numeri = {chiave: numero for numero, chiave in enumerate(sorted(esito.schede), 1)}

    righe, ponte = [], []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        confidenza = _confidenza(scheda)
        righe.append((
            numeri[chiave],
            f"P{chiave}",
            _forma(scheda.nomi, attestazione_nomi),
            cognome_di_famiglia(esito, scheda, attestazione_cognomi),
            scheda.sesso,
            scheda.anno_nascita,
            "certa" if scheda.nascite_certe else ("stimata" if scheda.nascite_stimate else None),
            scheda.morte,
            scheda.morta_entro,
            scheda.anno_primo,
            scheda.anno_ultimo,
            _serie(scheda, "professione", vocabolari) or mod.elenco(scheda.professioni),
            mod.elenco(scheda.residenze),
            _serie(scheda, "contrada", vocabolari) or mod.elenco(scheda.contrade),
            mod.elenco(Counter(
                m.nome_letto for m in scheda.menzioni if m.nome_letto
            )),
            mod.elenco(Counter(
                m.cognome_letto for m in scheda.menzioni if m.cognome_letto
            )),
            scheda.quante,
            len(scheda.incerte),
            scheda.fondamento(),
            confidenza,
            mod.stato_da_confidenza(confidenza),
            " | ".join(scheda.prove[-4:]) or None,
        ))
        for menzione in scheda.menzioni:
            certa = menzione.id not in scheda.incerte
            ponte.append((
                menzione.id, numeri[chiave], 1 if certa else 0,
                1.0 if certa else 0.6,
                mod.CONFERMATO if certa else mod.POSSIBILE,
                scheda.prove[-1] if scheda.prove else None,
            ))

    conn.executemany(
        "INSERT INTO individui (id, chiave, nome, cognome, sesso, anno_nascita, "
        "nascita_origine, anno_morte, morta_entro, anno_primo, anno_ultimo, "
        "professioni, residenze, contrade, varianti_nome, varianti_cognome, "
        "menzioni, menzioni_incerte, fondata_su, confidenza, stato, prove) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        righe,
    )
    conn.executemany(
        "INSERT INTO menzioni (persona, individuo, certa, confidenza, stato, prove) "
        "VALUES (?,?,?,?,?,?)",
        ponte,
    )
    return numeri


def _confidenza(scheda: Scheda) -> float:
    """Quanto il sistema crede a questa scheda nel suo insieme.

    Una scheda di una menzione sola e' certa per definizione — non c'e'
    stata nessuna decisione da sbagliare. Le altre valgono quanto la meno
    sicura delle decisioni che le hanno messe insieme.
    """
    if scheda.quante == 1:
        return 1.0
    if not scheda.incerte:
        return 0.95
    return max(0.55, 0.95 - 0.1 * len(scheda.incerte) / scheda.quante * 4)


def _forma(valori: Counter, attestazione: Counter) -> str | None:
    from history_maker.ricostruzione.scheda import _piu_attestata

    return _piu_attestata(valori, attestazione)


def cognome_di_famiglia(
    esito: risoluzione.Esito, scheda: Scheda, attestazione: Counter
) -> str | None:
    """Fra le letture del cognome, quella che i figli confermano.

    ``_piu_attestata`` sceglie la forma guardando **solo** la scheda e la
    frequenza nell'archivio, e ogni tanto sbaglia il verso: Luigi De
    Lucia e' letto 'di Suio' una volta, 'di Fazio' due e 'De Lucia' due,
    e vince 'di Fazio' perche' nel secolo e' un po' piu' frequente. Nel
    grafo pero' c'e' una prova che la frequenza non ha: i suoi due
    figli, Mercedes e Fileno, si chiamano tutti e due **De Lucia**.

    Un figlio legittimo porta il cognome del padre. Quando fra le letture
    del genitore ce n'e' una che i figli portano, e' quella la buona, e
    non c'e' bisogno di nessuna soglia per dirlo: e' la stessa parola
    letta due volte, una volta male e una volta bene, e i figli fanno da
    seconda lettura.

    Dei parenti si guarda il cognome **scelto**, non tutte le loro
    letture: Mercedes De Lucia ha 'di Fazio' fra le sue, ereditata dalla
    stessa pagina che ha confuso il padre, e bastava quella a confermare
    la scelta sbagliata. Il cognome scelto e' invece cio' che ognuno dei
    parenti, guardato per conto suo, e' risultato chiamarsi.

    Vale nei due versi, perche' la regola e' una sola — padre e figli
    portano lo stesso casato — e non dice da che parte stia la lettura
    buona. Verso il basso: Luigi, letto 'di Fazio', ha due figli De
    Lucia. Verso l'alto: **Chiara**, letta 'Lalli' e 'Lella' e
    'Colella', e' figlia di Giuseppe **Lella** — e 'Lalli' vinceva solo
    perche' nel secolo e' un po' piu' frequente.

    Non cambia nessun legame e non unisce niente: cambia **come si
    chiama** la scheda, che e' cio' che si vede sull'albero. E non
    inventa: se nessuna delle letture della scheda torna nei parenti,
    lascia la scelta com'era e ci pensa
    ``anomalie.padre_di_un_altro_casato``.
    """
    scelta = _forma(scheda.cognomi, attestazione)
    if scelta is None:
        return scelta
    # I figli valgono piu' dei padri: sono di piu', e un padre puo'
    # essere il patrigno o il padre naturale mentre un figlio legittimo
    # il casato ce l'ha per legge.
    dai_parenti: Counter = Counter()
    #
    # I figli contano solo per un uomo: quelli di una donna portano il
    # casato del marito. Chiara Lella, moglie di Antonio Marianacci, ha
    # una figlia letta «Leonice Lalli» — il cognome della madre copiato
    # sulla figlia — e bastava quella a confermare 'Lalli' contro il
    # 'Lella' del padre e dell'atto di nascita.
    figli = scheda.figli if scheda.sesso == "M" else set()
    for parenti, peso in ((figli, 2), (scheda.padri, 1)):
        for chiave in parenti:
            parente = esito.schede.get(chiave)
            if parente is None or not parente.cognomi:
                continue
            suo = _forma(parente.cognomi, attestazione)
            if suo:
                dai_parenti[paleografia.forma_canonica(suo)] += peso
    if not dai_parenti or paleografia.forma_canonica(scelta) in dai_parenti:
        return scelta
    candidate = [
        forma for forma in scheda.cognomi
        if paleografia.forma_canonica(forma) in dai_parenti
    ]
    if not candidate:
        return scelta
    return max(sorted(candidate), key=lambda f: (
        dai_parenti[paleografia.forma_canonica(f)], scheda.cognomi[f], f
    ))


def senza_nome(scheda: Scheda) -> bool:
    """La scheda non ha ne' nome ne' cognome: non e' una persona.

    Nei registri capita spesso, ed e' informazione vera: «figlio di
    genitori ignoti», «da donna che non consente di essere nominata», il
    padre lasciato in bianco perche' nessuno lo sapeva. L'estrazione ne
    fa comunque una riga, e da li' l'archivio ne faceva una **persona**:
    duecentotrentadue schede senza nome, centoottantuno delle quali
    finivano nell'albero — e siccome si somigliavano tutte (nessun nome
    da confrontare), si univano fra loro e sposavano qualcuno.

    Nell'albero di Filippo Lella se ne vedeva una: un nodo «senza nome»
    del 1895 accanto a Teresa Desiderio, che veniva dal padre ignoto di
    una Claudina Desiderio a cui i Lella non c'entrano niente.

    La riga resta dov'e' — l'atto dice che quel padre non si sa, e
    saperlo vale — ma non diventa un genitore ne' un coniuge.
    """
    return not scheda.nomi and not scheda.cognomi


def _serie(scheda: Scheda, tipo: str, vocabolari: dict | None) -> str | None:
    """Le forme di un attributo, ricondotte e in ordine di tempo.

    L'ordine e' **dalla piu' recente alla piu' antica**, e non e' una
    preferenza estetica: l'applicazione dell'albero mostra la prima come
    etichetta della persona, e un uomo che e' stato bovaro da giovane e
    bracciante per trent'anni va scritto bracciante. La serie datata per
    intero sta nei fatti, dove ogni valore ha il suo anno e il suo atto.
    """
    if not vocabolari or tipo not in vocabolari:
        return None
    voci = attributi.serie(scheda, tipo, vocabolari[tipo]).valori
    return " | ".join(reversed(voci)) or None


def _scrivi_fatti(
    conn: sqlite3.Connection, esito: risoluzione.Esito, numeri: dict,
    vocabolari: dict | None = None,
) -> int:
    """Ogni affermazione documentaria, con la sua fonte e il suo anno."""
    righe = []
    for chiave in sorted(esito.schede):
        individuo = numeri[chiave]
        for menzione in esito.schede[chiave].menzioni:
            for fatto in lettura.fatti_dalla_menzione(menzione, individuo, vocabolari):
                righe.append((
                    fatto.individuo, fatto.tipo, fatto.valore.grezzo,
                    fatto.valore.normalizzato, fatto.valore.interpretato,
                    fatto.anno, fatto.atto, fatto.menzione,
                    fatto.confidenza, fatto.valore.stato,
                ))
    conn.executemany(
        "INSERT INTO fatti (individuo, tipo, grezzo, normalizzato, interpretato, "
        "anno, atto, menzione, confidenza, stato) VALUES (?,?,?,?,?,?,?,?,?,?)",
        righe,
    )
    return len(righe)


def _scrivi_legami(
    conn: sqlite3.Connection, esito: risoluzione.Esito, numeri: dict
) -> tuple[int, int]:
    """La filiazione, presa da dentro gli atti e tradotta in individui.

    Qui succede l'ultima cosa che l'archivio non puo' evitare di fare:
    **un figlio ha un padre solo e una madre sola**. Quando gli atti ne
    dichiarano due, uno dei due e' sbagliato — o perche' una lettura e'
    rovinata, o perche' le due schede del genitore andavano unite, o
    perche' quella riga non e' di questo figlio. Scrivere tutti e due
    sarebbe scrivere una cosa che non puo' essere vera.

    Il criterio non e' "nel dubbio togli", e' **tieni la prova migliore**:
    l'atto di nascita di quel bambino, scritto il giorno del parto dal
    padre che va a dichiararlo, batte l'atto di matrimonio o di morte che
    nomina i genitori a decenni di distanza e a memoria di terzi.

    Niente sparisce in silenzio: il legame che perde diventa
    un'anomalia, con l'atto che lo affermava. Si toglie la
    **conclusione**, non la fonte.
    """
    dichiarazioni: dict[tuple[int, int, str], list[tuple[int, int]]] = {}
    for menzione in esito.corpus.menzioni:
        figlio = esito.di_menzione.get(menzione.id)
        if figlio is None:
            continue
        for riferimento, tipo in ((menzione.padre, "padre"), (menzione.madre, "madre")):
            if riferimento is None:
                continue
            genitore = esito.di_menzione.get(riferimento)
            if genitore is None or genitore == figlio:
                continue
            # Un genitore che l'atto non nomina non e' un genitore: vedi
            # 'senza_nome'. Il legame non si scrive, la menzione resta.
            if senza_nome(esito.schede[genitore]):
                continue
            chiave = (numeri[figlio], numeri[genitore], tipo)
            dichiarazioni.setdefault(chiave, []).append(
                (_qualita_della_fonte(menzione), menzione.atto)
            )

    anni = {
        numeri[chiave]: {
            "anno_nascita": scheda.anno_nascita,
            "nascita_origine": "certa" if scheda.nascite_certe else "stimata",
            "anno_morte": scheda.morte,
        }
        for chiave, scheda in esito.schede.items()
    }
    per_chiave = {n: c for c, n in numeri.items()}

    # Prima si scartano i legami cronologicamente impossibili, poi si
    # sceglie fra quelli che restano: un padre morto vent'anni prima non
    # deve poter vincere solo perche' e' dichiarato da un atto migliore.
    candidati_per_figlio: dict[tuple[int, str], list] = {}
    scartati = 0
    for (figlio, genitore, tipo), fonti in sorted(dichiarazioni.items()):
        if not _cronologia_possibile(anni.get(figlio, {}), anni.get(genitore, {}), tipo):
            scartati += 1
            continue
        migliore = max(fonti)
        candidati_per_figlio.setdefault((figlio, tipo), []).append(
            (migliore[0], len(fonti), genitore, migliore[1])
        )

    buoni = []
    for (figlio, tipo), possibili in sorted(candidati_per_figlio.items()):
        possibili.sort(key=lambda riga: (-riga[0], -riga[1], riga[2]))
        qualita, quante, genitore, atto = possibili[0]
        buoni.append((figlio, genitore, tipo, atto, 1.0, mod.CONFERMATO))
        for _, _, perdente, atto_perdente in possibili[1:]:
            esito.anomalie.append(mod.Anomalia(
                tipo="RELATIONSHIP_ANOMALY",
                individui=tuple(
                    per_chiave.get(n, n) for n in (figlio, genitore, perdente)
                ),
                campo=tipo,
                descrizione=(
                    f"gli atti danno a {_etichetta(esito, per_chiave, figlio)} due "
                    # 'padre' e 'madre' fanno 'padri' e 'madri', non
                    # 'padrei': la 'e' finale se ne va.
                    f"{tipo[:-1]}i: {_etichetta(esito, per_chiave, genitore)} (atto "
                    f"{atto}) e {_etichetta(esito, per_chiave, perdente)} (atto "
                    f"{atto_perdente}); l'albero tiene il primo"
                ),
                atti=(atto, atto_perdente),
                spiegazioni=(
                    "le due schede del genitore sono la stessa persona da unire",
                    "una delle due letture del nome e' rovinata",
                    f"la riga dell'atto {atto_perdente} non e' di questo figlio",
                ),
                confidenza=0.7,
                impatto=2,
                gravita="alta",
            ))

    buoni, troppo_vicini = _parti_possibili(buoni, esito, per_chiave, anni)
    conn.executemany(
        "INSERT OR IGNORE INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
        "VALUES (?,?,?,?,?,?)",
        buoni,
    )
    return len(buoni), scartati + troppo_vicini


# Fra due parti passano nove mesi. E' l'ultima cosa impossibile che
# l'archivio puo' ancora scrivere dopo aver scelto un genitore per figlio,
# e va tolta per la stessa ragione delle altre: non e' un dubbio, e'
# un'affermazione falsa.
GIORNI_FRA_DUE_PARTI = 250


def _parti_possibili(legami: list, esito, per_chiave: dict, anni: dict):
    """Toglie le maternita' che cadono troppo vicine a un'altra.

    Il rimedio non e' spezzare la madre — provato, e raddoppia le
    frammentazioni perche' il marito si ritrova due mogli — ma togliere
    **il legame piu' debole**: uno dei due atti attribuisce il bambino
    alla donna sbagliata, e il colpevole probabile e' quello che non e'
    l'atto di nascita del bambino stesso.
    """
    per_madre: dict[int, list] = {}
    for riga in legami:
        figlio, genitore, tipo, atto, _, _ = riga
        if tipo == "madre":
            per_madre.setdefault(genitore, []).append(riga)

    da_togliere = set()
    for madre, righe in sorted(per_madre.items()):
        if len(righe) < 2:
            continue
        datate = sorted(
            (esito.corpus.atti.get(riga[3], {}).get("data") or "", riga)
            for riga in righe
        )
        datate = [(data, riga) for data, riga in datate if len(data) >= 7]
        for (una, prima), (altra, seconda) in zip(datate, datate[1:]):
            giorni = _giorni_fra(una, altra)
            if giorni is None or not 0 < giorni < GIORNI_FRA_DUE_PARTI:
                continue
            da_togliere.add(seconda)
            esito.anomalie.append(mod.Anomalia(
                tipo="RELATIONSHIP_ANOMALY",
                individui=(per_chiave.get(madre, madre),
                           per_chiave.get(seconda[0], seconda[0])),
                campo="madre",
                descrizione=(
                    f"{_etichetta(esito, per_chiave, madre)} avrebbe partorito il "
                    f"{una} e il {altra}, a {giorni} giorni: la seconda "
                    f"maternita' non e' stata scritta"
                ),
                atti=(prima[3], seconda[3]),
                spiegazioni=(
                    "uno dei due atti attribuisce il bambino a un'altra madre",
                    "una delle due date e' letta male",
                    "le due madri sono due donne diverse da separare",
                ),
                confidenza=0.8, impatto=2, gravita="alta",
            ))
    return [riga for riga in legami if riga not in da_togliere], len(da_togliere)


def _giorni_fra(una: str, altra: str) -> int | None:
    from datetime import date

    try:
        prima = date(int(una[:4]), int(una[5:7]), int(una[8:10] or 1))
        seconda = date(int(altra[:4]), int(altra[5:7]), int(altra[8:10] or 1))
    except (ValueError, IndexError):
        return None
    return abs((seconda - prima).days)


# Quanto vale, come fonte su una paternita', l'atto in cui la si legge.
# La scala e' quella che i registri stessi suggeriscono: l'atto di
# nascita di quel bambino e' la prova migliore che esista sui suoi
# genitori, perche' e' scritto il giorno del parto; l'atto di matrimonio
# o di morte **di quella stessa persona** viene dopo, perche' nomina i
# genitori a distanza di anni e spesso a memoria di terzi; tutto il resto
# viene per ultimo.
def _qualita_della_fonte(menzione) -> int:
    if menzione.tipo_atto == "nascita" and menzione.ruolo in ("neonato", "neonata"):
        return 3
    if menzione.ruolo in ("sposo", "sposa", "defunto", "defunta"):
        return 2
    return 1


def _etichetta(esito, per_chiave: dict, numero: int) -> str:
    scheda = esito.schede.get(per_chiave.get(numero))
    return scheda.etichetta() if scheda is not None else f"#{numero}"


def _cronologia_possibile(figlio: dict, genitore: dict, tipo: str) -> bool:
    """Se un genitore poteva davvero avere quel figlio in quell'anno."""
    anno_figlio = figlio.get("anno_nascita")
    if anno_figlio is None:
        return True
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


def _scrivi_unioni(
    conn: sqlite3.Connection, esito: risoluzione.Esito, numeri: dict
) -> int:
    """Le coppie, dagli atti di matrimonio e dai figli avuti insieme.

    Gli atti di matrimonio superstiti sono meno delle coppie che si
    ricavano dagli atti di **nascita** — ogni volta che un atto nomina un
    padre e una madre insieme dichiara che sono marito e moglie. Senza
    quelle, tutte le famiglie sposate prima del 1809 o negli anni i cui
    registri sono perduti resterebbero invisibili, e con loro i figli. Le
    due provenienze restano distinte in ``origine``.
    """
    per_id = esito.corpus.per_id
    unioni: dict[tuple[int, int], dict] = {}

    for menzione in esito.corpus.menzioni:
        if menzione.coniuge is None:
            continue
        altra = per_id.get(menzione.coniuge)
        if altra is None:
            continue
        uno = esito.di_menzione.get(menzione.id)
        due = esito.di_menzione.get(altra.id)
        if uno is None or due is None or uno == due:
            continue
        # Due persone dello stesso sesso non sono una coppia dei registri
        # dell'Ottocento: e' il segno che una delle due schede ha
        # raccolto la riga sbagliata, e scrivere l'unione nasconderebbe
        # l'errore invece di mostrarlo.
        prima, seconda = esito.schede[uno], esito.schede[due]
        # Chi l'atto non nomina non sposa nessuno: vedi 'senza_nome'.
        if senza_nome(prima) or senza_nome(seconda):
            continue
        if prima.sesso and prima.sesso == seconda.sesso:
            esito.anomalie.append(mod.Anomalia(
                tipo="MARITAL_ANOMALY",
                individui=(uno, due),
                descrizione=(
                    f"{prima.etichetta()} e {seconda.etichetta()} risultano "
                    f"coniugi e hanno lo stesso sesso: l'unione non e' stata scritta"
                ),
                atti=(menzione.atto,),
                spiegazioni=(
                    "una delle due schede ha raccolto la riga di un'altra persona",
                    "il ruolo e' letto male sull'atto",
                ),
                confidenza=0.8, impatto=prima.quante + seconda.quante,
                gravita="alta",
            ))
            continue
        marito, moglie = _ordina_coppia(menzione, altra, numeri[uno], numeri[due])
        # La stessa coppia non va scritta due volte, una per verso: e'
        # l'ordine a doversi decidere, non la coppia.
        if (moglie, marito) in unioni:
            marito, moglie = moglie, marito
        chiave = (marito, moglie)
        # Il matrimonio e' documentato solo dalle righe degli sposi. I
        # genitori dello sposo, nello stesso atto, sono una coppia anche
        # loro, ma sposata trent'anni prima: prendere l'anno di quest'atto
        # faceva sposare Nicola Moretta, nato nel 1755, nel 1832 a
        # settantasette anni. La loro unione la dice il figlio che si sposa.
        documentata = (
            menzione.tipo_atto in ("matrimonio", "pubblicazione")
            and menzione.ruolo in RUOLI_DEGLI_SPOSI
            and altra.ruolo in RUOLI_DEGLI_SPOSI
        )
        precedente = unioni.get(chiave)
        if precedente is None:
            unioni[chiave] = {
                "anno": menzione.anno,
                "atto": menzione.atto,
                "origine": "matrimonio" if documentata else "figli",
            }
            continue
        if documentata and precedente["origine"] != "matrimonio":
            precedente.update(anno=menzione.anno, atto=menzione.atto, origine="matrimonio")
        elif documentata == (precedente["origine"] == "matrimonio"):
            if menzione.anno and menzione.anno < (precedente["anno"] or 9999):
                precedente.update(anno=menzione.anno, atto=menzione.atto)

    conn.executemany(
        "INSERT INTO unioni (marito, moglie, anno, atto, origine, confidenza, stato) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (marito, moglie, dati["anno"], dati["atto"], dati["origine"],
             1.0 if dati["origine"] == "matrimonio" else 0.9,
             mod.CONFERMATO if dati["origine"] == "matrimonio" else mod.PROBABILE)
            for (marito, moglie), dati in sorted(unioni.items())
        ],
    )
    return len(unioni)


def _ordina_coppia(una, altra, uno: int, due: int) -> tuple[int, int]:
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


# Quando il cognome di un figlio si riconduce a quello del padre. Sono
# gli stessi numeri con cui 'anomalie.cognomi_sospetti' segnala il caso,
# e devono restare uguali: la correzione e' la segnalazione portata fino
# in fondo, non una regola diversa che decide per conto suo.
COGNOME_RARO = 3
DOMINANZA_PATERNA = 8
# Quanto devono somigliarsi il cognome del figlio e quello del padre perche'
# siano la stessa parola letta in due modi: Zorzi e Torzi, Bosia e Boscia,
# Sepe e Pepe. Allora la frequenza non conta: conta chi ha piu' righe.
SOMIGLIANZA_DI_UNA_VARIANTE = 0.80

# La lettura rara che padre e figlio condividono nello stesso atto: «Modesto
# Sepe» e la figlia «Sepe», e il padre e' Modesto Pepe in trentotto righe.
# Rara: meno righe di quante ne ha una famiglia vera del paese in un secolo
# (vedi 'evidenza.RIGHE_DI_UNA_FAMIGLIA'). Vicina: piu' della meta' delle
# lettere, perche' «Femminilli» e «Ferrara» sono due famiglie, non una
# parola letta in due modi.
RIGHE_DI_UNA_LETTURA = 30
SOMIGLIANZA_DI_UNA_LETTURA = 0.50

# Quanti fratelli, figli della stessa coppia, devono portare il cognome
# della scheda del padre perche' quello del figlio diverso sia una lettura
# sbagliata. Uno solo puo' essere lui stesso letto male; due sono la
# famiglia.
FRATELLI_CHE_DICONO_IL_CASATO = 2

# Le righe che fanno di un atto di matrimonio il matrimonio di una coppia:
# gli sposi, non i loro genitori (vedi '_scrivi_unioni').
RUOLI_DEGLI_SPOSI = frozenset({"sposo", "sposa"})


def cognomi_dal_padre(
    conn: sqlite3.Connection, esito: risoluzione.Esito, numeri: dict
) -> int:
    """Da' al figlio il cognome del padre, quando la sua lettura non esiste.

    Un figlio legittimo porta il cognome del padre: non e' una
    probabilita', e' come funziona l'atto di stato civile. Quando le due
    letture divergono, quindi, **una delle due e' sbagliata** — e la
    sbagliata e' quella che nel resto del secolo non compare mai.

    Il caso che l'ha voluta e' l'atto di morte n. 18 del 1831. Il defunto
    e' trascritto ``Marzio d'Andria Motta``, figlio del fu Filippo Lella e
    di Margherita Rossi; a dichiararlo e' Nicolangelo Lella, che e' suo
    fratello. Sulla carta c'e' scritto «marito d'Andria Motta figlio del
    fu Filippo Lella»: 'Andria Motta' e' il nome della **moglie**, e chi
    ha trascritto l'ha attaccato a lui come cognome. Marzio e' un Lella, e
    lo dicono nove fratelli e un padre.

    Correggere qui non e' cancellare: la lettura resta in
    ``varianti_cognome`` e nel campo ``grezzo`` dei fatti, la sostituzione
    entra come **interpretazione**, e nel registro resta una decisione con
    il suo motivo. Ed e' volutamente prudente: un cognome raro puo' essere
    un figlio naturale o un forestiero, e per questo si tocca solo quando
    la forma letta non esiste (tre volte o meno in un secolo) e quella del
    padre e' otto volte piu' attestata.

    Oppure quando le due forme sono **la stessa parola** letta in due modi
    (:data:`SOMIGLIANZA_DI_UNA_VARIANTE`) e il padre ha almeno tante righe
    quante il figlio. Il caso: la morte n. 22 del 1813, «Domenica Zorzi,
    figlia di Francesco Zorzi». Il padre e' Francesco Torzi, e cosi' lo
    scrivono le sue altre righe; la figlia, con una riga sola, restava
    Zorzi. Nell'albero padre e figlia di due casati: centoquattro figli
    cosi', con lo stesso cognome del padre nell'atto che li lega.
    """
    frequenze = esito.corpus.frequenze_cognome
    per_chiave = {n: c for c, n in numeri.items()}
    corretti = []

    # Provato e scartato: aggiungere un quarto caso, il 'casato
    # confermato' — l'atto che li lega scrive la stessa parola per il
    # figlio e per suo padre, due letture indipendenti nella stessa
    # pagina — e ricondurre il figlio al casato del padre ogni volta che
    # le due schede ne mostrano due diversi. Nasceva da trentuno legami
    # veri: Monaco e Manes, Lorri e Lozzi, Leandro e Landea, Torzi e
    # Corzi.
    #
    # Misurato: i figli segnalati sono saliti da sessantadue a
    # ottantatre, e la classe che si voleva svuotare — «la scheda del
    # padre porta un altro cognome» — da tredici a quaranta. Rinominare
    # un figlio col casato del padre rompe l'accordo che quel figlio ha
    # coi **suoi** figli, e il guaio si propaga di generazione in
    # generazione. Il casato confermato e' frequentissimo (dodicimila
    # righe su cinquantaduemila): usato come permesso, scavalca tutte le
    # prudenze che questa funzione ha misurato una per una.

    # I fratelli: i figli della stessa coppia, padre e madre. Quando la
    # coppia ha altri figli che portano tutti il cognome della scheda del
    # padre, il figlio che ne porta un altro e' letto male — anche se il
    # cognome letto e' quello di una famiglia vera. «Carmela Femminilli,
    # figlia di Nicolangelo Femminilli e di Custoda Pizzi»: Custoda Pizzi e'
    # la moglie di Nicolangelo Ferrara, e i loro figli sono Ferrara.
    figli_della_coppia: dict = {}
    madre_di: dict = {}
    for figlio, padre, madre in conn.execute(
        "SELECT a.figlio, a.genitore, b.genitore FROM legami a "
        "  JOIN legami b ON b.figlio = a.figlio "
        " WHERE a.tipo = 'padre' AND b.tipo = 'madre'"
    ):
        figli_della_coppia.setdefault((padre, madre), set()).add(figlio)
        madre_di[figlio] = madre
    cognome_di = {
        numero: paleografia.forma_canonica(cognome or "")
        for numero, cognome in conn.execute("SELECT id, cognome FROM individui")
    }

    for riga in conn.execute(
        "SELECT l.figlio, l.genitore AS padre, f.cognome AS cognome_figlio, "
        "       p.cognome AS cognome_padre, "
        "       l.atto, f.menzioni AS menzioni_figlio, p.menzioni AS menzioni_padre "
        "  FROM legami l JOIN individui f ON f.id = l.figlio "
        "                JOIN individui p ON p.id = l.genitore "
        " WHERE l.tipo = 'padre' AND f.cognome IS NOT NULL AND p.cognome IS NOT NULL"
    ).fetchall():
        del_figlio = paleografia.forma_canonica(riga["cognome_figlio"])
        del_padre = paleografia.forma_canonica(riga["cognome_padre"])
        if del_figlio == del_padre:
            continue
        variante = (
            paleografia.somiglianza(del_figlio, del_padre) >= SOMIGLIANZA_DI_UNA_VARIANTE
            and (riga["menzioni_padre"] or 0) >= (riga["menzioni_figlio"] or 0)
        )
        # La lettura rara e vicina: «Sepe» per Pepe, «Lorri» per Lozzi,
        # «Colle» per Colella. Il padre deve avere piu' righe del figlio e
        # un cognome piu' attestato: la parola giusta e' la sua.
        lettura = (
            frequenze.get(del_figlio, 0) < RIGHE_DI_UNA_LETTURA
            and frequenze.get(del_padre, 0) > frequenze.get(del_figlio, 0)
            and paleografia.somiglianza(del_figlio, del_padre) > SOMIGLIANZA_DI_UNA_LETTURA
            and (riga["menzioni_padre"] or 0) > (riga["menzioni_figlio"] or 0)
        )
        fratelli = figli_della_coppia.get(
            (riga["padre"], madre_di.get(riga["figlio"])), set()
        ) - {riga["figlio"]}
        famiglia = (
            (riga["menzioni_figlio"] or 0) <= risoluzione.MENZIONI_DI_UN_FRAMMENTO
            and sum(1 for f in fratelli if cognome_di.get(f) == del_padre)
            >= FRATELLI_CHE_DICONO_IL_CASATO
        )
        if not (variante or lettura or famiglia):
            quante_figlio = frequenze.get(del_figlio, 0)
            quante_padre = frequenze.get(del_padre, 0)
            if quante_figlio > COGNOME_RARO:
                continue
            if quante_padre < max(1, quante_figlio) * DOMINANZA_PATERNA:
                continue
        corretti.append((riga["figlio"], riga["cognome_figlio"],
                         riga["cognome_padre"], riga["atto"]))

    for figlio, letto, giusto, atto in corretti:
        conn.execute(
            "UPDATE individui SET cognome = ? WHERE id = ?", (giusto, figlio)
        )
        # Il fatto conserva il grezzo e prende l'interpretazione: e' il
        # posto dove la correzione resta reversibile.
        conn.execute(
            "UPDATE fatti SET interpretato = ?, stato = ? "
            " WHERE individuo = ? AND tipo = 'cognome'",
            (giusto, mod.PROBABILE, figlio),
        )
        conn.execute(
            "UPDATE anomalie SET stato = 'corretta' "
            " WHERE tipo = 'SURNAME_ANOMALY' AND individui LIKE ?",
            (f"[{figlio},%",),
        )
        registro.annota(
            conn, "correzione", (per_chiave.get(figlio, figlio),),
            f"cognome: letto «{letto}», ricondotto a «{giusto}» come suo padre",
            confidenza=0.85, decisore="algoritmo",
            evidenze=(f"cognome={giusto}", "il padre lo porta e la forma letta non esiste"),
            contraddizioni=(f"la trascrizione diceva {letto}",),
            atti=(atto,) if atto else (),
        )
    return len(corretti)


def _scrivi_anomalie(
    conn: sqlite3.Connection, esito: risoluzione.Esito, numeri: dict
) -> int:
    righe = []
    for anomalia in sorted(esito.anomalie, key=lambda a: -a.priorita):
        righe.append((
            anomalia.tipo,
            json.dumps([numeri.get(i, i) for i in anomalia.individui]),
            json.dumps(list(anomalia.atti)),
            anomalia.campo,
            anomalia.descrizione,
            " | ".join(anomalia.spiegazioni),
            anomalia.evidenza.racconta() if anomalia.evidenza else None,
            anomalia.confidenza,
            anomalia.impatto,
            anomalia.gravita,
            anomalia.priorita,
            "aperta",
        ))
    conn.executemany(
        "INSERT INTO anomalie (tipo, individui, atti, campo, descrizione, "
        "spiegazioni, prove, confidenza, impatto, gravita, priorita, stato) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        righe,
    )
    return len(righe)


def _indicizza(conn: sqlite3.Connection) -> None:
    """Riempie l'indice di ricerca. Va fatto per ultimo."""
    conn.execute("INSERT INTO individui_fts(individui_fts) VALUES('delete-all')")
    conn.execute(
        "INSERT INTO individui_fts(rowid, nome, cognome, varianti_nome, varianti_cognome) "
        "SELECT id, nome, cognome, varianti_nome, varianti_cognome FROM individui"
    )


# ---------------------------------------------------------------------------
# Il rapporto
# ---------------------------------------------------------------------------

def sintesi(conn: sqlite3.Connection, corpus, esito: risoluzione.Esito) -> str:
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
    sole = uno("SELECT COUNT(*) FROM individui WHERE menzioni = 1")

    stati = {
        riga["stato"]: riga["quante"]
        for riga in conn.execute(
            "SELECT stato, COUNT(*) AS quante FROM individui GROUP BY stato"
        )
    }
    per_tipo = Counter(anomalia.tipo for anomalia in esito.anomalie)

    righe = [
        "# La ricostruzione genealogica di Torrebruna",
        "",
        "Non e' un elenco di schede: e' il risultato di un ragionamento che",
        "resta ispezionabile. Ogni scheda dice su quali prove si regge, ogni",
        "legame l'atto che lo dichiara, ogni decisione perche' e' stata presa.",
        "",
        "## Cosa contiene",
        "",
        f"- Persone ricostruite: **{individui}**",
        f"- Menzioni riordinate: **{menzioni}** (nessuna esclusa)",
        f"- Persone di cui si conoscono i genitori: **{con_genitori}**",
        f"- Persone di cui si conoscono i figli: **{con_figli}**",
        f"- Coppie: **{unioni}**, di cui con atto di matrimonio: **{documentate}**",
        f"- Persone che si reggono su una menzione sola: **{sole}**",
        "",
        "## Quanto e' sicura ogni scheda",
        "",
        "| stato | persone |",
        "|---|---:|",
    ]
    for stato in (mod.CONFERMATO, mod.PROBABILE, mod.POSSIBILE, mod.IRRISOLTO):
        if stati.get(stato):
            righe.append(f"| {stato} | {stati[stato]} |")

    righe += [
        "",
        "## Come si e' arrivati qui",
        "",
        f"- Schede dopo il primo giro: **{esito.statistiche.get('schede dopo il primo giro', 0)}**",
        f"- Unioni fatte nella riconciliazione: **{esito.statistiche.get('unioni', 0)}**",
        f"- Separazioni: **{esito.statistiche.get('separazioni', 0)}**",
        f"- Fusioni sospese perche' ambigue: "
        f"**{esito.statistiche.get('fusioni sospese per omonimia', 0)}**",
        "",
        "Le fusioni sospese non sono un fallimento: sono i casi in cui due",
        "candidati incompatibili erano ugualmente buoni, e scegliere a caso",
        "avrebbe prodotto una persona che non e' mai esistita. Stanno nella",
        "coda delle anomalie, che e' il posto da cui si riparte.",
        "",
        "## Lo strato di interpretazione",
        "",
    ]
    for voce, quante in sorted(corpus.interpretazioni.items()):
        righe.append(f"- {voce}: **{quante}**")
    righe += [
        "",
        "Sono correzioni di **lettura del formulario**, non di identita': il",
        "'fu Michele' attaccato a un cognome non e' parte del cognome di",
        "nessuno. La lettura originale resta in ``varianti_cognome`` e nei",
        "fatti, dove il campo ``grezzo`` non viene mai toccato.",
        "",
        "## I dubbi",
        "",
        f"- Anomalie aperte: **{len(esito.anomalie)}**",
        "",
        "| tipo | quante |",
        "|---|---:|",
    ]
    for tipo, quante in per_tipo.most_common():
        righe.append(f"| {tipo} | {quante} |")
    righe += [
        "",
        "L'elenco per esteso, in ordine di priorita', sta in `anomalie.md`.",
        "",
    ]
    return "\n".join(righe)
