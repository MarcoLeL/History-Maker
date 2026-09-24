"""Le schede appese a un filo solo."""

import sqlite3

import pytest

from history_maker import dataset, isolati
from history_maker.ricostruzione import modello


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(modello.SCHEMA_SQL)
    return c


def _individuo(conn, id, nome, cognome, menzioni=1):
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni) VALUES (?,?,?,?,?)",
        (id, f"k{id}", nome, cognome, menzioni))


def test_chi_ha_un_legame_solo_finisce_nell_elenco(conn):
    _individuo(conn, 1, "Concetta", "Pepe")
    _individuo(conn, 2, "Giuseppe", "Pepe", menzioni=9)
    _individuo(conn, 3, "Andrea", "Pepe", menzioni=4)
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                 "VALUES (2, 1, 'madre', 1, 1.0, 'confermato')")
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                 "VALUES (2, 3, 'padre', 1, 1.0, 'confermato')")
    conn.commit()

    fuori = isolati.elenco(conn)
    # Giuseppe ha due legami e resta fuori. Senza omonimi l'ordine e'
    # per menzioni: prima Andrea, che l'archivio conosce di piu'.
    assert [x["id"] for x in fuori] == [3, 1]
    assert fuori[1]["attacchi"][0]["verso"] == "genitore di"
    assert fuori[1]["attacchi"][0]["chi"] == "Giuseppe Pepe"


def test_un_omonimo_grosso_manda_la_scheda_in_cima(conn):
    """E' il caso che conta: non una persona sola, un pezzo staccato."""
    _individuo(conn, 1, "Casimiro", "Chielli", menzioni=1)
    _individuo(conn, 2, "Casimiro", "Chielli", menzioni=34)
    _individuo(conn, 3, "Rosa", "Lella", menzioni=1)
    _individuo(conn, 9, "Figlio", "Qualunque", menzioni=5)
    for genitore in (1, 3):
        conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, "
                     "stato) VALUES (9, ?, 'padre', 1, 1.0, 'confermato')", (genitore,))
    conn.commit()

    fuori = isolati.elenco(conn)
    assert fuori[0]["id"] == 1 and fuori[0]["gemello_massimo"] == 34
    assert fuori[1]["id"] == 3 and fuori[1]["gemello_massimo"] == 0


def test_un_unione_vale_come_un_legame(conn):
    """Sullo schermo la riga si vede lo stesso, anche se e' 'probabile'."""
    _individuo(conn, 1, "Vito", "Lella")
    _individuo(conn, 2, "Anna", "Pepe")
    conn.execute("INSERT INTO unioni (marito, moglie, anno, atto, origine, "
                 "confidenza, stato) VALUES (1, 2, 1830, 1, 'atto', 0.6, 'probabile')")
    conn.commit()
    assert {s["id"] for s in isolati.elenco(conn)} == {1, 2}


def test_le_schede_staccate_del_tutto_si_chiedono_a_parte(conn):
    _individuo(conn, 1, "Nessuno", "Solo")
    conn.commit()
    assert isolati.elenco(conn, quanti_legami=1) == []
    (sola,) = isolati.elenco(conn, quanti_legami=0)
    assert sola["id"] == 1 and sola["attacchi"] == []


def test_il_riassunto_conta_tutte_le_specie(conn):
    _individuo(conn, 1, "A", "A"); _individuo(conn, 2, "B", "B")
    _individuo(conn, 3, "C", "C"); _individuo(conn, 4, "D", "D")
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                 "VALUES (1, 2, 'padre', 1, 1.0, 'confermato')")
    conn.commit()
    r = isolati.riassunto(conn)
    assert r["individui"] == 4 and r["staccate"] == 2 and r["un_filo"] == 2
