"""Due ricostruzioni nello stesso archivio, senza che si distruggano.

Il problema, prima di questo modulo. La fase 6 vecchia (``genealogia``) e
la nuova (``ricostruisci``) scrivono nelle **stesse tabelle dello stesso
database**, e ciascuna comincia buttando quelle dell'altra::

    genealogia.py            ricostruzione/modello.py
    DROP TABLE individui;    DROP TABLE individui;
    DROP TABLE menzioni;     DROP TABLE menzioni;
    ...                      DROP TABLE fatti;
                             DROP TABLE anomalie;

Due conseguenze, e la seconda e' peggiore della prima.

**Non si possono eseguire tutt'e due.** Confrontare il vecchio motore
col nuovo — che e' la sola prova che il nuovo sia meglio — richiede di
rieseguire tutto due volte e ricopiare i numeri a mano.

**La V1 dopo la V2 corrompe l'archivio in silenzio.** La V1 non conosce
``fatti`` e ``anomalie``, quindi non le butta: trecentomila fatti
restano a puntare a ``individui.id`` che nel frattempo sono stati
rinumerati da zero. Nessun errore, nessun avviso, e ogni interrogazione
che unisce le due parti da' risposte sbagliate.

Come si risolve, e perche' cosi'
--------------------------------

La strada ovvia — prefissare le tabelle della V1 con ``v1_`` — vuol dire
riscrivere 229 frammenti di SQL nella V1 e 108 nei lettori. Su un
sistema che funziona e' il modo piu' probabile di romperlo.

Qui invece la separazione e' **a livello di file**: ogni motore ha il suo
database, e la fase 4 — che nessuno dei due deve toccare — sta nel
database principale e viene **attaccata** in lettura. Le sue tabelle
ricompaiono nel motore come viste temporanee con lo stesso nome, quindi
tutto l'SQL esistente continua a funzionare parola per parola::

    torrebruna.sqlite          torrebruna-v1.sqlite
    ├─ registri  ┐             ├─ (temp view) registri  ┐
    ├─ atti      │ fase 4      ├─ (temp view) atti      │ le stesse righe,
    ├─ persone   │ (la fonte)  ├─ (temp view) persone   │ in sola lettura
    ├─ voci_indice ┘           ├─ (temp view) voci_indice ┘
    │                          │
    ├─ individui ┐             ├─ individui ┐
    ├─ menzioni  │ fase 6b     ├─ menzioni  │ fase 6
    ├─ legami    │ (la V2)     ├─ legami    │ (la V1)
    ├─ unioni    │             ├─ unioni    │
    ├─ fatti     │             └─ scartati  ┘
    ├─ anomalie  │
    ├─ decisioni │
    └─ verifiche ┘

Le viste sono **temporanee** di proposito: si rifanno a ogni
connessione, quindi non c'e' modo che restino appese a un database che
non c'e' piu'. E il file della V1 pesa qualche megabyte invece di
centotrenta, perche' della fase 4 non copia niente.

Il database principale resta quello che l'applicazione apre e che
``ricostruisci`` scrive: chi non ha mai eseguito la V1 non si accorge
che questo modulo esiste.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from history_maker.config import Config

logger = logging.getLogger(__name__)


# Le tabelle della fase 4: la fonte di tutto, e l'unica cosa che i due
# motori condividono. Nessuno dei due le scrive.
TABELLE_FASE_4 = ("registri", "atti", "persone", "voci_indice")

# Il nome con cui il database principale compare dentro quello di un
# motore. Serve solo a chi scrive SQL qualificato: le viste rendono
# inutile qualificare.
FASE_4 = "fase4"

# I motori, e il file su cui ciascuno lavora. La V2 sta nel database
# principale perche' e' quella che l'applicazione mostra e che il resto
# della pipeline si aspetta di trovare.
MOTORI = {
    "v1": "torrebruna-v1.sqlite",
    "v2": None,                     # None = il database principale
}


def percorso_principale(config: Config) -> Path:
    return config.dataset / "torrebruna.sqlite"


def percorso(config: Config, motore: str = "v2") -> Path:
    """Il file su cui lavora un motore."""
    if motore not in MOTORI:
        raise ValueError(
            f"motore sconosciuto: {motore!r} (sono {', '.join(sorted(MOTORI))})"
        )
    nome = MOTORI[motore]
    return percorso_principale(config) if nome is None else config.dataset / nome


def apri(
    config: Config, motore: str = "v2", sola_lettura: bool = False
) -> sqlite3.Connection:
    """Apre il database di un motore, con la fase 4 gia' a portata di SQL.

    Per la V2 e' semplicemente il database principale: la fase 4 e le sue
    tabelle stanno gia' insieme, e non c'e' niente da attaccare.

    Per la V1 e' il suo file, con il principale attaccato e le tabelle
    della fase 4 ricreate come viste. Da fuori le due connessioni si
    usano allo stesso modo, ed e' il punto: ``qualita.analizza(conn)``
    non deve sapere quale delle due sta misurando.

    ``sola_lettura`` e' per chi misura invece di costruire. Vale anche per
    il database attaccato: una misura non deve poter scrivere da nessuna
    parte, nemmeno per sbaglio.
    """
    principale = percorso_principale(config)
    if not principale.exists():
        raise FileNotFoundError(
            f"manca {principale}: prima va costruito il dataset "
            f"(python -m history_maker dataset)"
        )

    if MOTORI[motore] is None:
        conn = _connetti(principale, sola_lettura)
        conn.row_factory = sqlite3.Row
        return conn

    suo = percorso(config, motore)
    if sola_lettura and not suo.exists():
        raise FileNotFoundError(
            f"manca {suo}: la ricostruzione {motore} non e' mai stata eseguita "
            f"(python -m history_maker genealogia)"
        )
    conn = _connetti(suo, sola_lettura)
    conn.row_factory = sqlite3.Row
    collega_fase_4(conn, principale)
    return conn


def _connetti(percorso_file: Path, sola_lettura: bool) -> sqlite3.Connection:
    """Una connessione, aperta in lettura o in scrittura.

    ``uri=True`` sempre, anche in scrittura: e' cio' che permette poi a
    ``ATTACH`` di accettare a sua volta un URI, e quindi di attaccare la
    fase 4 in sola lettura.
    """
    if sola_lettura:
        return sqlite3.connect(f"file:{percorso_file.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(f"file:{percorso_file.as_posix()}", uri=True)


def collega_fase_4(conn: sqlite3.Connection, principale: Path) -> None:
    """Attacca il database principale e ne rende visibili le tabelle.

    Le viste hanno lo **stesso nome** delle tabelle originali, ed e' cio'
    che permette a duemila righe di SQL scritte prima di questo modulo di
    continuare a funzionare senza toccarne una. Sono **temporanee**: si
    rifanno a ogni connessione, quindi non possono restare appese a un
    database che non c'e' piu'.

    Vengono create solo per le tabelle che ci sono davvero: un archivio a
    cui manca ``voci_indice`` — perche' nessun registro aveva un indice —
    non deve fallire qui.

    **Sempre in sola lettura**, anche quando il motore sta scrivendo, e
    non e' prudenza generica: e' la difesa contro un modo preciso di
    perdere l'archivio. Quando un nome di tabella non e' qualificato,
    SQLite lo cerca prima in ``main`` e poi nei database attaccati —
    quindi il ``DROP TABLE IF EXISTS individui`` con cui un motore
    comincia a scrivere, eseguito su un database dove ``individui`` non
    c'e' ancora, **scende nel principale e butta la ricostruzione
    dell'altro motore**. Provato: la tabella spariva senza un errore.

    Con l'attacco in sola lettura quel comando fallisce a voce alta
    invece di distruggere. Perche' non fallisca affatto, i ``DROP`` dei
    motori sono qualificati con ``main.`` (vedi ``SCHEMA_SQL`` in
    :mod:`history_maker.genealogia`).
    """
    conn.execute(
        f"ATTACH DATABASE ? AS {FASE_4}", (f"file:{principale.as_posix()}?mode=ro",)
    )
    presenti = {
        riga[0] for riga in conn.execute(
            f"SELECT name FROM {FASE_4}.sqlite_master WHERE type = 'table'"
        )
    }
    for tabella in TABELLE_FASE_4:
        if tabella in presenti:
            conn.execute(
                f"CREATE TEMP VIEW IF NOT EXISTS {tabella} "
                f"AS SELECT * FROM {FASE_4}.{tabella}"
            )


def ricostruita(conn: sqlite3.Connection) -> bool:
    """Se questo database contiene una ricostruzione, o solo la fase 4."""
    riga = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'individui'"
    ).fetchone()
    if riga is None:
        return False
    return conn.execute("SELECT count(*) FROM individui").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Il confronto
# ---------------------------------------------------------------------------
#
# Perche' e' un comando e non un foglio di calcolo. La tabella che
# confronta i due motori esisteva — sta in docs/ricostruzione.md — ma era
# stata compilata a mano, eseguendo due volte e ricopiando i numeri. Un
# confronto che costa mezz'ora di lavoro manuale si fa una volta sola,
# cioe' quando si vuole dimostrare che il nuovo motore e' meglio, e non si
# rifa' piu' — che e' esattamente quando servirebbe.

# I due assi, e vanno letti insieme. Prendere solo il primo premia chi
# fonde tutto con tutto; prendere solo il secondo premia chi non fonde
# niente. Il numero che conta e' la somma.
ASSI = ("impossibile", "frammentazione", "accorpamento", "sospetto")


def confronta(config: Config, esempi: int = 0) -> str:
    """Mette i due motori fianco a fianco sugli stessi dati.

    Rende il rapporto in Markdown. Non dichiara un vincitore: dice di
    quanto i numeri si spostano e in che direzione, perche' la lettura
    dipende da cosa si sta cercando di migliorare — e perche' un albero
    piu' piccolo ma corretto vale piu' di uno enorme pieno di parentele
    inventate.
    """
    from history_maker import qualita

    misure: dict[str, dict] = {}
    for motore in ("v1", "v2"):
        conn = apri(config, motore, sola_lettura=True)
        if not ricostruita(conn):
            conn.close()
            raise ValueError(
                f"la ricostruzione '{motore}' non e' stata eseguita: "
                f"manca {percorso(config, motore).name}"
            )
        esiti = qualita.analizza(conn)
        misure[motore] = {
            "collegamento": qualita.collegamento(conn),
            "conteggi": qualita.conteggi(esiti),
            "controlli": {e.controllo.nome: e.quanti for e in esiti},
            "categoria": {e.controllo.nome: e.controllo.categoria for e in esiti},
            "cancellate": _cancellate(conn),
        }
        conn.close()

    return _rapporto(misure, esempi)


def _cancellate(conn: sqlite3.Connection) -> int:
    """Quante conclusioni il motore ha **tolto** perche' impossibili.

    Serve a leggere lo zero della V1 per quello che e'. Quella fase
    finisce con una passata di correzione che cancella le conclusioni
    assurde invece di segnalarle, quindi il suo 'zero impossibili' non e'
    un albero senza contraddizioni: e' un albero a cui le contraddizioni
    sono state tolte. Le due colonne non misurano la stessa cosa, e senza
    questo numero il confronto direbbe una bugia.
    """
    try:
        return conn.execute("SELECT COUNT(*) FROM scartati").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _scarto(prima: int, dopo: int) -> str:
    """La differenza, con il segno, o un trattino se non si muove."""
    if prima == dopo:
        return "—"
    return f"{dopo - prima:+d}"


def _rapporto(misure: dict[str, dict], esempi: int) -> str:
    v1, v2 = misure["v1"], misure["v2"]
    righe = [
        "# Le due ricostruzioni, sugli stessi dati",
        "",
        "`genealogia` (V1, pesi fissi e veti) contro `ricostruisci` (V2,",
        "log-verosimiglianza misurata sul corpus). Stessa fase 4, stesso",
        "glossario, stesse menzioni.",
        "",
        "## Quanto l'albero tiene",
        "",
        "Queste righe contano **cio' che regge**, non cio' che e' rotto. Una",
        "persona spezzata in due schede non viola niente e non compare in",
        "nessun controllo: si vede solo qui, come un collegamento che manca.",
        "",
        "| | V1 | V2 | |",
        "|---|---:|---:|---:|",
    ]
    for voce in v1["collegamento"]:
        uno, due = v1["collegamento"][voce], v2["collegamento"][voce]
        righe.append(f"| {voce} | {uno:,} | {due:,} | {_scarto(uno, due)} |"
                     .replace(",", "."))

    righe += [
        "",
        "## Cosa non puo' essere vero",
        "",
        "Da leggere **insieme** alla tabella sopra, mai da sola: stringere i",
        "criteri fa scendere gli impossibili e salire le frammentazioni, e",
        "allargarli fa il contrario. Il numero che conta e' la somma.",
        "",
        "| categoria | V1 | V2 | |",
        "|---|---:|---:|---:|",
    ]
    for asse in ASSI:
        uno = v1["conteggi"].get(asse, 0)
        due = v2["conteggi"].get(asse, 0)
        righe.append(f"| {asse} | {uno} | {due} | {_scarto(uno, due)} |")
    somma_v1 = sum(v1["conteggi"].get(a, 0) for a in ASSI)
    somma_v2 = sum(v2["conteggi"].get(a, 0) for a in ASSI)
    righe.append(
        f"| **totale** | **{somma_v1}** | **{somma_v2}** | "
        f"**{_scarto(somma_v1, somma_v2)}** |"
    )

    # Senza questa nota la riga 'impossibile' e' una bugia per omissione.
    if v1["cancellate"] or v2["cancellate"]:
        righe += ["", "Da sapere per leggere la riga **impossibile**:", ""]
        for motore, misura in (("V1", v1), ("V2", v2)):
            if misura["cancellate"]:
                righe.append(
                    f"* la {motore} **cancella** le conclusioni che rendono "
                    f"l'albero impossibile invece di segnalarle: "
                    f"{misura['cancellate']} righe tolte, in tabella `scartati`. "
                    f"Il suo conteggio e' quello che resta **dopo** quella "
                    f"passata, non quello che la ricostruzione ha prodotto."
                )
            else:
                righe.append(
                    f"* la {motore} non cancella niente: i suoi "
                    f"{misura['conteggi'].get('impossibile', 0)} casi sono in "
                    f"coda con la domanda gia' pronta per l'immagine."
                )
        righe.append(
            ""
        )
        righe.append(
            "Le due colonne quindi **non misurano la stessa cosa**, e la "
            "somma va letta sapendolo."
        )

    righe += [
        "",
        "## Controllo per controllo",
        "",
        "| controllo | categoria | V1 | V2 | |",
        "|---|---|---:|---:|---:|",
    ]
    nomi_controlli = sorted(
        set(v1["controlli"]) | set(v2["controlli"]),
        key=lambda n: -(v1["controlli"].get(n, 0) + v2["controlli"].get(n, 0)),
    )
    for nome in nomi_controlli:
        uno = v1["controlli"].get(nome, 0)
        due = v2["controlli"].get(nome, 0)
        if not uno and not due:
            continue
        categoria = v1["categoria"].get(nome) or v2["categoria"].get(nome, "")
        righe.append(f"| {nome} | {categoria} | {uno} | {due} | {_scarto(uno, due)} |")

    righe += [
        "",
        "---",
        "",
        "I due database stanno in `torrebruna-v1.sqlite` e",
        "`torrebruna.sqlite`, e nessuno dei due tocca l'altro: si possono",
        "rieseguire in qualunque ordine.",
        "",
    ]
    return "\n".join(righe)
