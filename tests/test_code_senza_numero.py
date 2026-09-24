"""La coda che apre una facciata non ha numero, ma si sa di chi e'."""

import sqlite3

import pytest

from history_maker import dataset, ricuci


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    return c


def _atto(conn, pagina, numero, tipo="matrimonio", registro="r", anno=1814):
    cur = conn.execute(
        "INSERT INTO atti (registro, immagine, numero_atto, tipo, anno) "
        "VALUES (?,?,?,?,?)",
        (registro, f"{registro}/{pagina:04d}-pag-{pagina}.jpg", numero, tipo, anno))
    return cur.lastrowid


def _persona(conn, atto, ruolo, nome):
    conn.execute(
        "INSERT INTO persone (atto, ruolo, nome, cognome) VALUES (?,?,?,'X')",
        (atto, ruolo, nome))


def test_la_coda_prende_il_numero_dell_ultimo_atto_di_prima(conn):
    _atto(conn, 18, "Cinque")
    coda = _atto(conn, 19, None)
    _atto(conn, 19, "Sei")
    assert ricuci._code_senza_numero(conn) == {coda: (1814, 5)}


def test_un_frammento_che_non_apre_la_scansione_non_e_una_coda(conn):
    _atto(conn, 18, "Cinque")
    _atto(conn, 19, "Sei")
    dentro = _atto(conn, 19, None)      # viene dopo: non e' una coda
    assert ricuci._code_senza_numero(conn) == {}


def test_una_coda_non_prende_il_numero_di_un_altro_tipo(conn):
    _atto(conn, 18, "Cinque", tipo="nascita")
    _atto(conn, 19, None, tipo="matrimonio")
    assert ricuci._code_senza_numero(conn) == {}


def test_una_coda_lontana_non_si_attacca(conn):
    _atto(conn, 2, "Cinque")
    _atto(conn, 40, None)
    assert ricuci._code_senza_numero(conn) == {}


def test_la_coda_viene_cucita_nell_atto_giusto(conn):
    """Il caso vero: i testimoni dell'atto n. 5 sulla facciata del n. 6."""
    cinque = _atto(conn, 18, "Cinque")
    _persona(conn, cinque, "sposo", "Nicola")
    coda = _atto(conn, 19, None)
    _persona(conn, coda, "testimone", "Gesualdo")
    sei = _atto(conn, 19, "Sei")
    _persona(conn, sei, "sposo", "Giuseppe")
    conn.commit()

    cuciti, tolti = ricuci.ricuci_atti(conn)
    assert (cuciti, tolti) == (1, 1)
    rimasti = {r["id"] for r in conn.execute("SELECT id FROM atti")}
    assert rimasti == {cinque, sei}
    dentro = {r["nome"] for r in conn.execute(
        "SELECT nome FROM persone WHERE atto=?", (cinque,))}
    assert dentro == {"Nicola", "Gesualdo"}
    assert conn.execute("SELECT COUNT(*) FROM persone WHERE atto=?",
                        (sei,)).fetchone()[0] == 1


def test_la_coda_prende_anche_l_anno_dell_atto_che_finiva(conn):
    """Una facciata di coda non porta date: eredita l'anno del registro.

    Il registro '1813-matrimoni' contiene anche i matrimoni del 1814, e
    cucire sul solo numero lasciava dieci code perfette appese al nulla.
    """
    _atto(conn, 18, "Cinque", anno=1814)
    coda = _atto(conn, 19, None, anno=1813)     # l'anno del registro
    _atto(conn, 19, "Sei", anno=1814)
    assert ricuci._code_senza_numero(conn) == {coda: (1814, 5)}


def test_la_coda_con_l_anno_del_registro_viene_cucita_lo_stesso(conn):
    cinque = _atto(conn, 18, "Cinque", anno=1814)
    _persona(conn, cinque, "sposo", "Nicola")
    coda = _atto(conn, 19, None, anno=1813)
    _persona(conn, coda, "testimone", "Gesualdo")
    conn.commit()
    assert ricuci.ricuci_atti(conn) == (1, 1)
    assert {r["nome"] for r in conn.execute(
        "SELECT nome FROM persone WHERE atto=?", (cinque,))} == {"Nicola", "Gesualdo"}


# --- il guardiano sui protagonisti: severo, ma non cieco ---------------

MATRIMONIO = {"sposo": 1, "sposa": 1}
MORTE = {"defunto": 1}


def test_lo_sposo_concorde_salva_la_sposa_letta_in_due_modi():
    """Il n. 5 del 1815: «Angela Maria Torzi» in testa, «Poyo» in coda."""
    assert ricuci._e_lo_stesso_atto_riletto(
        {"sposo": {("Domen", "Lella")},
         "sposa": {("Angel", "Torzi"), ("Angel", "Poyo")}}, MATRIMONIO)


def test_due_morti_con_lo_stesso_numero_restano_divise():
    """Un solo protagonista: non c'e' nessuno con cui concordare."""
    assert not ricuci._e_lo_stesso_atto_riletto(
        {"defunto": {("Luigi", "Desid"), ("Maria", "Cicch")}}, MORTE)


def test_tre_spose_non_sono_una_rilettura():
    assert not ricuci._e_lo_stesso_atto_riletto(
        {"sposo": {("Domen", "Montt")},
         "sposa": {("Celut", "Monno"), ("Emanu", "Bosci"), ("Perna", "Cicch")}},
        MATRIMONIO)


def test_se_discordano_tutti_e_due_non_si_cuce():
    assert not ricuci._e_lo_stesso_atto_riletto(
        {"sposo": {("Anni", "Salva"), ("Ermi", "Salva")},
         "sposa": {("Maria", "Cicch"), ("Rosa", "Pelli")}}, MATRIMONIO)


def test_la_coda_col_numero_sbagliato_si_cuce_lo_stesso(conn):
    """Il matrimonio n. 4 del 1822: la coda porta scritto «5», e non e' suo.

    La trascrizione mette un numero in testa a ogni facciata, e dove la
    carta non ce l'ha lo indovina. La facciata che continua il n. 4 si
    ritrova cosi' il numero del n. 5: cucendo sul numero non la si
    raggiunge, e la sposa dell'atto - letta una seconda volta sulla
    facciata voltata - resta una seconda donna, moglie dello stesso uomo.
    Si riconosce da come comincia: non «L'anno mille...» ma in mezzo a
    una frase. Il contrappeso: un atto che apre davvero, anche se gli
    manca uno sposo, non e' una coda.
    """
    quattro = _atto(conn, 13, "Quattro")
    _persona(conn, quattro, "sposo", "Crescenzo")
    _persona(conn, quattro, "sposa", "Clementina")
    coda = _atto(conn, 14, "5")
    conn.execute("UPDATE atti SET testo_integrale = ? WHERE id = ?",
                 ("E Maria Maddalena Clementina Jorio d'anni venti", coda))
    _persona(conn, coda, "sposa", "Maria Maddalena Clementina")
    _persona(conn, coda, "testimone", "Romualdo")
    intero = _atto(conn, 16, "4")
    conn.execute("UPDATE atti SET testo_integrale = ? WHERE id = ?",
                 ("L'anno mille ottocento ventidue il di' ventisette", intero))
    _persona(conn, intero, "sposo", "Emanuele")

    ricuci.ricuci_atti(conn)

    assert conn.execute("SELECT COUNT(*) FROM atti WHERE id = ?", (coda,)).fetchone()[0] == 0
    assert [r["nome"] for r in conn.execute(
        "SELECT nome FROM persone WHERE atto = ? AND ruolo = 'sposa' ORDER BY id", (quattro,))] == [
        "Clementina", "Maria Maddalena Clementina"]
    # L'atto intero del n. 4 resta dov'e': apre la sua facciata.
    assert conn.execute("SELECT COUNT(*) FROM atti WHERE id = ?", (intero,)).fetchone()[0] == 1
