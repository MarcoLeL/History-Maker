"""L'audit trail: come si legge, e come non si perde peso.

Due proprieta' contano piu' delle altre: che la storia di una persona si
trovi senza scansionare tutto l'archivio, e che archiviare le decisioni
vecchie dell'algoritmo non tocchi mai quelle prese da chi ha guardato la
carta.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from history_maker.ricostruzione import modello, registro


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(modello.SCHEMA_SQL)
    return c


def _quando(base: datetime, secondi: int) -> str:
    return (base + timedelta(seconds=secondi)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inserisci(conn, quando, azione, entita, decisore="algoritmo", disfa=None):
    conn.execute(
        "INSERT INTO decisioni (quando, azione, entita, motivo, confidenza, "
        "decisore, disfa) VALUES (?,?,?,?,?,?,?)",
        (quando, azione, json.dumps(list(entita)), "prova", 0.9, decisore, disfa),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# --- storia() ----------------------------------------------------------

def test_la_storia_trova_le_decisioni_di_una_persona(conn):
    base = datetime(2026, 1, 1)
    _inserisci(conn, _quando(base, 0), "unione", [1, 2])
    _inserisci(conn, _quando(base, 1), "unione", [3, 4])
    storia = registro.storia(conn, 1)
    assert len(storia) == 1
    assert json.loads(storia[0]["entita"]) == [1, 2]


def test_il_filtro_non_prende_falsi_positivi(conn):
    """L'individuo 5 non deve comparire per una decisione su [15, 25].

    E' la trappola di un filtro fatto solo con LIKE: la stringa '5' e'
    contenuta anche dentro '[15, 25]'.
    """
    base = datetime(2026, 1, 1)
    _inserisci(conn, _quando(base, 0), "unione", [15, 25])
    assert registro.storia(conn, 5) == []


def test_la_storia_e_in_ordine_di_tempo(conn):
    """L'ordine e' quello per identificatore — la stessa convenzione di
    ``correzioni()`` ('l'ultima decisione vince: e' l'ordine in cui sono
    state prese') — perche' gli id crescono nell'ordine in cui le
    decisioni sono state salvate.
    """
    base = datetime(2026, 1, 1)
    attesi = []
    for secondi in (10, 20, 30):
        attesi.append(_inserisci(conn, _quando(base, secondi), "unione", [1, 2]))
    storia = registro.storia(conn, 1)
    assert [r["id"] for r in storia] == attesi


def test_la_storia_guarda_anche_l_archivio(conn):
    """Spostare una decisione non e' cancellarla: 'perche'?' deve reggere."""
    conn.execute(registro.SCHEMA_ARCHIVIO)
    conn.execute(
        "INSERT INTO decisioni_archivio (id, quando, azione, entita, decisore) "
        "VALUES (99, '2020-01-01T00:00:00Z', 'unione', '[1, 2]', 'algoritmo')"
    )
    storia = registro.storia(conn, 1)
    assert len(storia) == 1
    assert storia[0]["id"] == 99


def test_la_storia_regge_senza_la_tabella_archivio(conn):
    """Un archivio mai creato non deve far fallire la ricerca."""
    assert registro.storia(conn, 1) == []


# --- archivia() ----------------------------------------------------------

def _due_esecuzioni_vecchie_una_recente(conn):
    """Tre gruppi di decisioni algoritmiche, separati da pause lunghe."""
    base = datetime(2026, 1, 1, 0, 0, 0)
    ids = {"prima": [], "seconda": [], "terza": []}
    for gruppo, offset in (("prima", 0), ("seconda", 3600), ("terza", 7200)):
        for secondi in (0, 1, 2):
            i = _inserisci(
                conn, _quando(base, offset + secondi), "unione", [secondi, secondi + 1]
            )
            ids[gruppo].append(i)
    return ids


def test_riconosce_i_confini_fra_esecuzioni_dal_silenzio(conn):
    """Non c'e' un identificatore di esecuzione: si rileva dal salto di tempo."""
    _due_esecuzioni_vecchie_una_recente(conn)
    esiti = registro.archivia(conn, tieni_ultime=1, a_secco=True)
    assert esiti["esecuzioni_trovate"] == 3


def test_tiene_le_ultime_n_esecuzioni_nella_tabella_calda(conn):
    ids = _due_esecuzioni_vecchie_una_recente(conn)
    registro.archivia(conn, tieni_ultime=1)

    rimaste = {r["id"] for r in conn.execute("SELECT id FROM decisioni")}
    assert rimaste == set(ids["terza"])

    archiviate = {r["id"] for r in conn.execute("SELECT id FROM decisioni_archivio")}
    assert archiviate == set(ids["prima"]) | set(ids["seconda"])


def test_meno_esecuzioni_del_tetto_non_archivia_niente(conn):
    _due_esecuzioni_vecchie_una_recente(conn)
    esiti = registro.archivia(conn, tieni_ultime=5)
    assert esiti["archiviate"] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 9


def test_le_decisioni_non_algoritmiche_non_si_archiviano_mai(conn):
    """La parte piu' preziosa del registro non si tocca, quale che sia la sua eta'."""
    base = datetime(2020, 1, 1)
    umana = _inserisci(conn, _quando(base, 0), "unione", [1, 2], decisore="persona")
    _due_esecuzioni_vecchie_una_recente(conn)

    registro.archivia(conn, tieni_ultime=1)

    ancora_qui = conn.execute(
        "SELECT decisore FROM decisioni WHERE id = ?", (umana,)
    ).fetchone()
    assert ancora_qui is not None
    assert ancora_qui["decisore"] == "persona"
    assert conn.execute(
        "SELECT COUNT(*) FROM decisioni_archivio WHERE id = ?", (umana,)
    ).fetchone()[0] == 0


def test_a_secco_non_sposta_niente(conn):
    prima = conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0]
    _due_esecuzioni_vecchie_una_recente(conn)
    totale = conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0]

    registro.archivia(conn, tieni_ultime=1, a_secco=True)

    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == totale
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM decisioni_archivio"
        ).fetchone()[0] == 0
    except sqlite3.OperationalError:
        pass  # la tabella puo' non esistere ancora: e' altrettanto corretto


def test_una_decisione_referenziata_da_disfa_non_si_archivia(conn):
    """Chi la punta deve poterla ancora trovare senza incrociare due tabelle."""
    base = datetime(2026, 1, 1)
    vecchia = _inserisci(conn, _quando(base, 0), "unione", [1, 2])
    for secondi in range(1, 4):
        _inserisci(conn, _quando(base, secondi + 3600), "unione", [secondi, secondi + 1])
    # Un'altra esecuzione, cosi' la prima non e' fra le ultime da tenere.
    for secondi in range(4):
        _inserisci(conn, _quando(base, secondi + 7200), "unione", [secondi, secondi + 1])
    # Qualcuno disfa la primissima decisione.
    _inserisci(conn, _quando(base, 10000), "separazione", [1, 2], decisore="persona",
               disfa=vecchia)

    registro.archivia(conn, tieni_ultime=1)

    assert conn.execute(
        "SELECT COUNT(*) FROM decisioni WHERE id = ?", (vecchia,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM decisioni_archivio WHERE id = ?", (vecchia,)
    ).fetchone()[0] == 0


def test_archiviare_due_volte_non_duplica_niente(conn):
    _due_esecuzioni_vecchie_una_recente(conn)
    registro.archivia(conn, tieni_ultime=1)
    prima = conn.execute("SELECT COUNT(*) FROM decisioni_archivio").fetchone()[0]
    registro.archivia(conn, tieni_ultime=1)
    dopo = conn.execute("SELECT COUNT(*) FROM decisioni_archivio").fetchone()[0]
    assert dopo == prima


# --- imposizioni() -----------------------------------------------------

def test_una_separazione_resta_un_gruppo_solo(conn):
    """Tre menzioni staccate dalla stessa decisione fanno un pezzo solo.

    E' cio' che distingue «questi tre vanno via insieme» da «questi tre
    vanno via ciascuno per conto suo»: la prima e' la decisione che
    qualcuno ha preso davvero, e appiattirla in coppie la perde.
    """
    base = datetime(2026, 1, 1)
    _inserisci(conn, _quando(base, 0), "separazione", [10, 11, 12, 13],
               decisore="persona")
    assert registro.imposizioni(conn)["separare"] == [(10, (11, 12, 13))]


def test_due_separazioni_sulla_stessa_scheda_restano_due(conn):
    """Due decisioni diverse sono due tagli, anche con lo stesso ancoraggio.

    Il caso vero: dalla scheda di Rebecca Lella andava staccato l'atto di
    morte del 1857 — che e' di sua sorella Maria — e, con un'altra
    decisione, una menzione incerta che e' di Angela Maria Lella. Messe
    nello stesso pezzo, la prima unione se le portava via tutt'e due, e
    Angela Maria finiva addosso a Maria.
    """
    base = datetime(2026, 1, 1)
    _inserisci(conn, _quando(base, 0), "separazione", [10, 11], decisore="persona")
    _inserisci(conn, _quando(base, 1), "separazione", [10, 12], decisore="claude")
    assert registro.imposizioni(conn)["separare"] == [(10, (11,)), (10, (12,))]


def test_le_separazioni_superate_non_si_riapplicano(conn):
    base = datetime(2026, 1, 1)
    vecchia = _inserisci(conn, _quando(base, 0), "separazione", [10, 11],
                         decisore="persona")
    _inserisci(conn, _quando(base, 1), "separazione", [10], decisore="claude",
               disfa=vecchia)
    assert registro.imposizioni(conn)["separare"] == []


# ---------------------------------------------------------------------------
# Il registro non si riempie di copie
# ---------------------------------------------------------------------------

def _decisione(motivo="le due schede si somigliano", decisore="algoritmo", entita=(7, 9)):
    return modello.Decisione(
        azione="separazione", entita=tuple(entita), motivo=motivo,
        confidenza=0.9, decisore=decisore, quando="2026-01-01T00:00:00Z",
    )


def test_la_stessa_decisione_dell_algoritmo_non_si_riscrive(conn):
    """Il giro dopo rifa' lo stesso ragionamento: non e' una decisione nuova.

    La tabella delle decisioni non si azzera, mentre tutte le altre si
    rifanno: cosi' ogni giro riscriveva le stesse separazioni. Una sola
    ci era finita dentro duecento volte, e su quattrocentotrentasettemila
    righe quattrocentoventicinquemila erano ripetizioni.
    """
    for _ in range(3):
        registro.salva(conn, [_decisione()])
    assert conn.execute("SELECT count(*) FROM decisioni").fetchone()[0] == 1


def test_un_motivo_diverso_e_una_decisione_diversa(conn):
    """Se l'algoritmo ci arriva per un'altra strada, il registro lo dice."""
    registro.salva(conn, [_decisione(motivo="le due schede si somigliano")])
    registro.salva(conn, [_decisione(motivo="il coniuge e' un altro")])
    assert conn.execute("SELECT count(*) FROM decisioni").fetchone()[0] == 2


def test_altre_entita_sono_una_decisione_diversa(conn):
    registro.salva(conn, [_decisione(entita=(7, 9))])
    registro.salva(conn, [_decisione(entita=(7, 11))])
    assert conn.execute("SELECT count(*) FROM decisioni").fetchone()[0] == 2


@pytest.mark.parametrize("decisore", ["persona", "claude-immagine", "claude"])
def test_chi_ha_guardato_la_pagina_puo_ripetersi(conn, decisore):
    """Il contrappeso: le decisioni di una persona non si perdono mai.

    Sono poche e sono la cosa piu' preziosa dell'archivio; se qualcuno
    decide due volte la stessa cosa, il registro deve dirlo.
    """
    for _ in range(2):
        registro.salva(conn, [_decisione(decisore=decisore)])
    assert conn.execute("SELECT count(*) FROM decisioni").fetchone()[0] == 2
