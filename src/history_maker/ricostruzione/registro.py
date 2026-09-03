"""L'audit trail: cosa e' stato deciso, da chi, e come si disfa.

Un archivio ricostruito da una macchina ha un difetto che un archivio
costruito a mano non ha: **sembra sempre finito**. Ogni scheda ha un nome,
delle date, dei genitori, e niente, guardandola, dice che quella
paternita' e' stata dedotta con il 60% di confidenza da un cognome letto
male.

Questo modulo e' il rimedio. Ogni fusione, ogni separazione, ogni
correzione lascia una riga con le prove a favore, le contraddizioni, la
confidenza, e la versione dell'algoritmo o del modello che l'ha presa. Non
serve solo a chi consulta: serve a chi sviluppa, perche' quando fra un
mese una soglia cambiera' si potra' vedere **quali** decisioni cambiano.

Due scelte che vale la pena spiegare.

**La tabella non si azzera.** Tutte le altre della fase si buttano e si
rifanno; questa si accumula, perche' contiene anche le decisioni prese da
una persona, e quelle sono la cosa piu' preziosa dell'archivio. Le righe
di una ricostruzione precedente restano, marcate con la loro versione: e'
cosi' che si vede cosa e' cambiato fra due esecuzioni.

**Non si cancella, si supera.** Tornare indietro su una decisione e' una
decisione nuova che nomina la vecchia nel campo ``disfa``. La storia resta
leggibile per intero, e una scheda che e' stata unita, separata e riunita
lo racconta.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from history_maker.ricostruzione.modello import Decisione

logger = logging.getLogger(__name__)

VERSIONE_ALGORITMO = "1.0.0"


def salva(
    conn: sqlite3.Connection, decisioni: list[Decisione],
    versione: str = VERSIONE_ALGORITMO,
) -> int:
    """Aggiunge le decisioni al registro. Non tocca quelle gia' scritte."""
    righe = [
        (
            decisione.quando or _adesso(),
            decisione.azione,
            json.dumps(list(decisione.entita)),
            decisione.motivo,
            decisione.confidenza,
            " | ".join(decisione.evidenze),
            " | ".join(decisione.contraddizioni),
            json.dumps(list(decisione.atti)),
            decisione.decisore,
            decisione.modello,
            decisione.versione_prompt,
            decisione.versione_algoritmo or versione,
            decisione.disfa,
        )
        for decisione in decisioni
    ]
    conn.executemany(
        "INSERT INTO decisioni (quando, azione, entita, motivo, confidenza, "
        "evidenze, contraddizioni, atti, decisore, modello, versione_prompt, "
        "versione_algoritmo, disfa) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        righe,
    )
    return len(righe)


def annota(
    conn: sqlite3.Connection, azione: str, entita, motivo: str,
    confidenza: float = 1.0, decisore: str = "persona", **extra,
) -> int:
    """Registra una decisione singola e rende il suo identificatore.

    E' la porta da cui entrano le decisioni prese fuori dall'algoritmo:
    quelle di Claude, quelle di Gemini sull'immagine, e soprattutto
    quelle di una persona che ha guardato la carta.
    """
    decisione = Decisione(
        azione=azione, entita=tuple(entita), motivo=motivo,
        confidenza=confidenza, decisore=decisore, quando=_adesso(),
        versione_algoritmo=VERSIONE_ALGORITMO,
        evidenze=tuple(extra.get("evidenze", ())),
        contraddizioni=tuple(extra.get("contraddizioni", ())),
        atti=tuple(extra.get("atti", ())),
        modello=extra.get("modello", ""),
        versione_prompt=extra.get("versione_prompt", ""),
        disfa=extra.get("disfa"),
    )
    salva(conn, [decisione])
    riga = conn.execute("SELECT last_insert_rowid()").fetchone()
    return riga[0] if riga else 0


def disfa(
    conn: sqlite3.Connection, decisione: int, motivo: str,
    decisore: str = "persona",
) -> int:
    """Supera una decisione presa prima, senza cancellarla.

    Rende l'identificatore della decisione nuova. Quella vecchia resta
    dov'e': un archivio in cui si puo' riscrivere il passato non e' un
    archivio.
    """
    vecchia = conn.execute(
        "SELECT azione, entita FROM decisioni WHERE id = ?", (decisione,)
    ).fetchone()
    if vecchia is None:
        raise ValueError(f"la decisione {decisione} non esiste")
    contraria = {"unione": "separazione", "separazione": "unione"}.get(
        vecchia[0], "correzione"
    )
    return annota(
        conn, contraria, json.loads(vecchia[1]), motivo,
        confidenza=1.0, decisore=decisore, disfa=decisione,
    )


def imposizioni(conn: sqlite3.Connection) -> dict:
    """Le decisioni ancora in vigore, pronte da riapplicare.

    Sono quelle prese **fuori dall'algoritmo** — da Claude, da Gemini
    sull'immagine, o da una persona — e non superate da una decisione
    successiva. Quelle dell'algoritmo non si riapplicano: le rifara' da
    solo, e riapplicarle vorrebbe dire congelare per sempre una
    conclusione che i dati nuovi potrebbero smentire.

    E' cio' che chiude il cerchio: senza, il giudizio di chi ha guardato
    la carta vale una volta sola, e la ricostruzione successiva torna alla
    stessa conclusione di prima.
    """
    conn.row_factory = sqlite3.Row
    try:
        righe = list(conn.execute(
            "SELECT id, azione, entita FROM decisioni "
            "WHERE decisore <> 'algoritmo' AND azione IN ('unione', 'separazione') "
            "ORDER BY id"
        ))
        superate = {
            riga[0] for riga in conn.execute(
                "SELECT disfa FROM decisioni WHERE disfa IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        return {"unire": [], "separare": []}

    imposte: dict[str, list] = {"unire": [], "separare": []}
    for riga in righe:
        if riga["id"] in superate:
            continue
        entita = json.loads(riga["entita"])
        if len(entita) < 2:
            continue
        if riga["azione"] == "unione":
            imposte["unire"].append((entita[0], entita[1]))
            continue
        # Una separazione puo' staccare **molte** menzioni dalla stessa
        # scheda: la prima entita' e' quella che resta, tutte le altre se
        # ne vanno. Leggerne solo due lascerebbe settanta righe dalla
        # parte sbagliata e la scheda lunga una vita e mezza.
        for staccata in entita[1:]:
            imposte["separare"].append((entita[0], staccata))
    return imposte


def correzioni(conn: sqlite3.Connection) -> dict:
    """Le letture corrette da chi ha guardato l'immagine, o da una persona.

    Rende ``{(menzione, campo): valore}``. E' l'altra meta' di
    :func:`imposizioni`: quella riporta nell'albero le decisioni
    sull'**identita'**, questa quelle sulla **lettura**. Senza, una
    verifica sull'immagine costa quota e non cambia niente — la
    ricostruzione successiva rileggerebbe la stessa eta' sbagliata.

    La trascrizione non viene toccata: il valore corretto entra come
    interpretazione, e il grezzo resta nei fatti.
    """
    conn.row_factory = sqlite3.Row
    try:
        righe = list(conn.execute(
            "SELECT id, entita, evidenze FROM decisioni "
            "WHERE azione = 'correzione' AND decisore <> 'algoritmo' ORDER BY id"
        ))
        superate = {
            riga[0] for riga in conn.execute(
                "SELECT disfa FROM decisioni WHERE disfa IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        return {}

    corrette: dict = {}
    for riga in righe:
        if riga["id"] in superate:
            continue
        entita = json.loads(riga["entita"])
        for prova in (riga["evidenze"] or "").split(" | "):
            campo, _, valore = prova.partition("=")
            if entita and campo and valore:
                # L'ultima decisione vince: e' l'ordine per identificatore,
                # cioe' l'ordine in cui sono state prese.
                corrette[(entita[0], campo.strip())] = valore.strip()
    return corrette


def storia(conn: sqlite3.Connection, individuo: int) -> list[sqlite3.Row]:
    """Tutte le decisioni che hanno toccato una persona, in ordine di tempo.

    Comprende anche quelle **archiviate** (vedi :func:`archivia`): spostare
    una decisione fuori dalla tabella calda non e' cancellarla, e chi
    chiede 'perche' l'archivio crede questo?' deve continuare a ricevere
    la risposta intera.

    Il filtro e' in due passi: prima un ``LIKE`` che usa l'indice e scarta
    la stragrande maggioranza delle righe, poi ``json.loads`` sulle
    sopravvissute per decidere davvero. Filtrare **solo** con ``LIKE``
    sarebbe sbagliato — la stringa ``'5'`` compare anche dentro
    ``'[15, 25]'`` — e filtrare **solo** con ``json.loads`` vuol dire
    deserializzare tutte le righe della tabella per rispondere su una
    persona sola, che su 134.000 righe e' il costo che questa funzione
    pagava a ogni chiamata.
    """
    conn.row_factory = sqlite3.Row
    fuori = []
    for tabella in ("decisioni", "decisioni_archivio"):
        try:
            righe = conn.execute(
                f"SELECT * FROM {tabella} WHERE entita LIKE ? ORDER BY id",
                (f"%{individuo}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            continue        # l'archivio puo' non esistere ancora
        for riga in righe:
            try:
                if individuo in json.loads(riga["entita"]):
                    fuori.append(riga)
            except (TypeError, ValueError):
                continue
    fuori.sort(key=lambda r: r["id"])
    return fuori


# ---------------------------------------------------------------------------
# L'archiviazione
# ---------------------------------------------------------------------------
#
# 134.100 decisioni dopo due giorni di prove, 131.645 delle quali sono
# fusioni dell'**algoritmo**. Non sono rumore — sono l'unico modo di
# vedere cosa e' cambiato fra due esecuzioni — ma non si riapplicano mai
# (``imposizioni`` e ``correzioni`` leggono solo ``decisore <> 'algoritmo'``,
# e per una buona ragione: l'algoritmo rifa' da solo i suoi conti, e
# ricongelarli vorrebbe dire impedire ai dati nuovi di smentirli). Tenerle
# tutte nella tabella calda costa alla sola funzione che le legge davvero
# — :func:`storia` — una deserializzazione di ogni riga per rispondere su
# una persona sola.
#
# Il rimedio non e' cancellarle: e' spostarle. Le decisioni **non
# algoritmiche** — quelle di una persona, di Claude, di Gemini
# sull'immagine — non si archiviano mai: sono la parte piu' preziosa del
# registro, e restano nella tabella calda per sempre.

SCHEMA_ARCHIVIO = """
CREATE TABLE IF NOT EXISTS decisioni_archivio (
    id          INTEGER PRIMARY KEY,
    quando      TEXT NOT NULL,
    azione      TEXT NOT NULL,
    entita      TEXT NOT NULL,
    motivo      TEXT,
    confidenza  REAL,
    evidenze    TEXT,
    contraddizioni TEXT,
    atti        TEXT,
    decisore    TEXT NOT NULL DEFAULT 'algoritmo',
    modello     TEXT,
    versione_prompt TEXT,
    versione_algoritmo TEXT,
    disfa       INTEGER
);
"""

# Quanto silenzio fra due decisioni algoritmiche segna il confine fra due
# esecuzioni di 'ricostruisci'. Misurato sull'archivio vero: le pause
# dentro una stessa esecuzione (fra un giro di riconciliazione e il
# successivo) restano sotto il minuto; fra un'esecuzione e la successiva
# passano minuti o ore. Cinque minuti lascia un margine largo senza
# rischiare di tagliare in due un'esecuzione lenta.
SCARTO_ESECUZIONE_MINUTI = 5

# Quante esecuzioni tenere per intero nella tabella calda. Due bastano a
# rispondere alla domanda che conta di solito — 'cosa e' cambiato
# rispetto all'ultima volta?' — senza portarsi dietro tutte le prove
# scartate durante lo sviluppo.
ESECUZIONI_DA_TENERE = 2


def _confini_esecuzioni(righe: list, scarto_minuti: int) -> list[int]:
    """Gli identificatori che aprono una nuova esecuzione, in ordine.

    Non c'e' nella tabella un identificatore di esecuzione — aggiungerlo
    vorrebbe dire una migrazione di schema su centotrentamila righe gia'
    scritte — quindi il confine si rileva dal silenzio: un salto di
    ``scarto_minuti`` fra due decisioni algoritmiche consecutive e'
    quasi certamente il tempo fra un lancio del comando e il successivo,
    non una pausa dentro lo stesso lancio.
    """
    confini = [righe[0]["id"]] if righe else []
    soglia = scarto_minuti * 60
    precedente = None
    for riga in righe:
        adesso = datetime.strptime(riga["quando"], "%Y-%m-%dT%H:%M:%SZ")
        if precedente is not None and (adesso - precedente).total_seconds() > soglia:
            confini.append(riga["id"])
        precedente = adesso
    return confini


def archivia(
    conn: sqlite3.Connection, tieni_ultime: int = ESECUZIONI_DA_TENERE,
    scarto_minuti: int = SCARTO_ESECUZIONE_MINUTI, a_secco: bool = False,
) -> dict:
    """Sposta le decisioni algoritmiche delle esecuzioni piu' vecchie.

    Non cancella niente: le righe vanno in ``decisioni_archivio``, con lo
    stesso schema, e :func:`storia` continua a trovarle. Le decisioni non
    algoritmiche non si toccano mai, quale che sia la loro eta'.

    Una decisione referenziata da ``disfa`` — cioe' che qualcun altro ha
    esplicitamente superato — non si archivia: e' un caso che oggi non
    esiste sulle decisioni dell'algoritmo (nessuna le disfa mai), ma la
    guardia costa poco e vale la pena tenerla.

    ``a_secco`` conta senza spostare, per sapere prima quanto costerebbe.
    """
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA_ARCHIVIO)
    righe = conn.execute(
        "SELECT id, quando FROM decisioni WHERE decisore = 'algoritmo' ORDER BY id"
    ).fetchall()
    confini = _confini_esecuzioni(righe, scarto_minuti)
    if len(confini) <= tieni_ultime:
        return {"esecuzioni_trovate": len(confini), "archiviate": 0, "a_secco": a_secco}

    # Il confine dell'esecuzione da cui in poi si tiene tutto: le ultime
    # 'tieni_ultime' partono da qui.
    taglio = confini[-tieni_ultime]
    referenziate = {
        riga[0] for riga in conn.execute(
            "SELECT disfa FROM decisioni WHERE disfa IS NOT NULL"
        )
    }
    da_archiviare = [
        riga["id"] for riga in righe if riga["id"] < taglio
        and riga["id"] not in referenziate
    ]
    if not da_archiviare or a_secco:
        return {
            "esecuzioni_trovate": len(confini),
            "archiviabili": len(da_archiviare),
            "archiviate": 0,
            "a_secco": a_secco,
        }

    segnaposto = ",".join("?" * len(da_archiviare))
    conn.execute(
        f"INSERT INTO decisioni_archivio SELECT * FROM decisioni "
        f"WHERE id IN ({segnaposto})", da_archiviare,
    )
    conn.execute(f"DELETE FROM decisioni WHERE id IN ({segnaposto})", da_archiviare)
    return {
        "esecuzioni_trovate": len(confini),
        "archiviate": len(da_archiviare),
        "a_secco": a_secco,
    }


def _adesso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
