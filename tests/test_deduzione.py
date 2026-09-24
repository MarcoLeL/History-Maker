"""I genitori dedotti dal nome dei nipoti.

Il modulo lavora sulle tabelle gia' scritte — individui, legami, unioni,
menzioni — quindi i casi si montano direttamente li'. E' anche il modo in
cui va letto: la deduzione non e' parte del calcolo dell'identita', e'
un passo che guarda l'albero finito e prova a riattaccarne i tronconi.
"""

from __future__ import annotations

import sqlite3

import pytest

from history_maker.ricostruzione import deduzione, modello


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(modello.SCHEMA_SQL)
    c.executescript(
        "CREATE TABLE IF NOT EXISTS persone (id INTEGER PRIMARY KEY, atto INTEGER);"
        "CREATE TABLE IF NOT EXISTS atti (id INTEGER PRIMARY KEY);"
    )
    return c


def persona(conn, identificatore, nome, cognome, sesso, nascita, morte=None):
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, sesso, anno_nascita, "
        "anno_morte, menzioni) VALUES (?,?,?,?,?,?,?,1)",
        (identificatore, f"P{identificatore}", nome, cognome, sesso, nascita, morte),
    )


def figlio(conn, chi, di, tipo="padre"):
    conn.execute(
        "INSERT INTO legami (figlio, genitore, tipo, confidenza, stato) "
        "VALUES (?,?,?,1.0,'confermato')", (chi, di, tipo),
    )


def coppia(conn, marito, moglie):
    conn.execute(
        "INSERT INTO unioni (marito, moglie, origine, confidenza, stato) "
        "VALUES (?,?,'matrimonio',1.0,'confermato')", (marito, moglie),
    )


def _famiglia_di_torrebruna(conn):
    """Il caso vero, ridotto all'osso.

    Nicola Lella (1821) sposa Teresa Desiderio. Domenicantonio Lella
    (1862) non ha genitori scritti da nessun atto — i registri dei nati
    di quegli anni non ci sono — ma chiama il primo maschio Nicola Maria
    e la prima femmina Teresa.
    """
    persona(conn, 1, "Nicola", "Lella", "M", 1821, 1900)
    persona(conn, 2, "Teresa", "Desiderio", "F", 1824)
    coppia(conn, 1, 2)
    persona(conn, 10, "Domenicantonio", "Lella", "M", 1862)
    persona(conn, 11, "Clementina", "Petta", "F", 1864)
    coppia(conn, 10, 11)
    persona(conn, 20, "Nicola Maria", "Lella", "M", 1885)
    persona(conn, 21, "Teresa", "Lella", "F", 1887)
    for chi in (20, 21):
        figlio(conn, chi, 10, "padre")
        figlio(conn, chi, 11, "madre")


def test_deduce_il_padre_dal_nome_del_primogenito(conn):
    _famiglia_di_torrebruna(conn)
    trovate = deduzione.proposte(conn)
    assert len(trovate) == 1
    proposta = trovate[0]
    assert (proposta.figlio, proposta.padre, proposta.madre) == (10, 1, 2)
    assert proposta.sicura
    assert proposta.confidenza >= 0.75


def test_il_solo_nome_del_nonno_non_basta(conn):
    """Senza la nonna e senza la presenza negli atti, il caso resta aperto."""
    persona(conn, 1, "Nicola", "Lella", "M", 1821)
    persona(conn, 10, "Domenicantonio", "Lella", "M", 1862)
    persona(conn, 20, "Nicola Maria", "Lella", "M", 1885)
    figlio(conn, 20, 10, "padre")
    assert deduzione.proposte(conn) == []


def test_la_nonna_deve_avere_l_eta_per_esserlo(conn):
    """Maria Felicia di Rado, nata nel 1807, «madre» di un Giuseppe nato nel 1807.

    La moglie sposata tardi dal nonno porta il nome della nipote, ma non
    puo' averne partorito il padre: non e' una prova, e senza di lei il
    solo nome del nonno non basta.
    """
    _famiglia_di_torrebruna(conn)
    conn.execute("UPDATE individui SET anno_nascita = 1862 WHERE id = 2")
    assert deduzione.proposte(conn) == []


def test_il_padre_dedotto_non_e_il_suocero(conn):
    """Giuseppe Ottaviano riceveva per padre il padre scritto di sua moglie."""
    _famiglia_di_torrebruna(conn)
    figlio(conn, 11, 1, "padre")
    assert deduzione.proposte(conn) == []


def test_due_candidati_non_fanno_una_deduzione(conn):
    """Il dubbio dichiarato vale piu' di una risposta a caso."""
    _famiglia_di_torrebruna(conn)
    persona(conn, 3, "Nicola", "Lella", "M", 1823)
    persona(conn, 4, "Teresa", "Marianacci", "F", 1826)
    coppia(conn, 3, 4)
    trovate = deduzione.proposte(conn)
    assert len(trovate) == 1
    assert not trovate[0].sicura
    assert trovate[0].alternative == (1, 3)
    assert deduzione.applica(conn, trovate) == 0


def test_chi_ha_gia_un_padre_scritto_non_si_deduce(conn):
    _famiglia_di_torrebruna(conn)
    persona(conn, 5, "Vincenzo", "Lella", "M", 1800)
    figlio(conn, 10, 5, "padre")
    assert deduzione.proposte(conn) == []


def test_la_cronologia_impossibile_esclude_il_candidato(conn):
    """Un padre morto vent'anni prima non e' un padre."""
    _famiglia_di_torrebruna(conn)
    conn.execute("UPDATE individui SET anno_morte = 1840 WHERE id = 1")
    assert deduzione.proposte(conn) == []


def test_un_padre_troppo_giovane_non_e_un_padre(conn):
    _famiglia_di_torrebruna(conn)
    conn.execute("UPDATE individui SET anno_nascita = 1855 WHERE id = 1")
    assert deduzione.proposte(conn) == []


def test_il_legame_dedotto_si_riconosce(conn):
    """Non si confonde con un legame che un atto dichiara."""
    _famiglia_di_torrebruna(conn)
    deduzione.applica(conn, deduzione.proposte(conn))
    righe = list(conn.execute(
        "SELECT genitore, tipo, atto, confidenza, stato FROM legami "
        " WHERE figlio = 10 ORDER BY tipo"
    ))
    assert [r["tipo"] for r in righe] == ["madre", "padre"]
    assert all(r["stato"] == "dedotto" and r["atto"] is None for r in righe)
    assert all(r["confidenza"] < 1.0 for r in righe)
    decisioni = list(conn.execute(
        "SELECT azione, evidenze FROM decisioni WHERE azione = 'deduzione'"
    ))
    assert len(decisioni) == 1
    assert "primogenito" in decisioni[0]["evidenze"]


def test_il_nome_composto_porta_lo_stesso_quello_del_nonno(conn):
    """«Nicola Maria» e' il nome di «Nicola» con un secondo nome sopra."""
    assert deduzione._stesso_nome("Nicola Maria", "Nicola")
    assert deduzione._stesso_nome("Domenico Antonio", "Domenico")
    assert not deduzione._stesso_nome("Giuseppe", "Nicola")
