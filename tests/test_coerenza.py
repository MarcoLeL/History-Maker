"""Che dall'albero non esca mai una cosa impossibile.

I controlli della fase 7 dicono cosa il mondo non consente; questa fase
lo fa rispettare. I due moduli devono restare d'accordo: se qui passasse
qualcosa che li' viene contato, l'archivio fabbricherebbe di sua mano
proprio cio' che poi segnala.
"""

import sqlite3

import pytest

from history_maker import coerenza, dataset, genealogia, qualita, ricuci


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(genealogia.SCHEMA_SQL)
    return c


def atto(conn, id, tipo="nascita", anno=1840, numero="1", registro="r",
         immagine=None, data=None):
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, data_evento) "
        "VALUES (?,?,?,?,?,?,?)",
        (id, registro, immagine or f"{registro}/{id:04d}-pag-{id}.jpg",
         numero, tipo, anno, data),
    )


def persona(conn, id, atto_id, ruolo, nome, cognome=None):
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome) VALUES (?,?,?,?,?)",
        (id, atto_id, ruolo, nome, cognome),
    )


def individuo(conn, id, nome, cognome=None, **campi):
    colonne = ["id", "nome", "cognome"] + list(campi)
    valori = [id, nome, cognome] + list(campi.values())
    conn.execute(
        f"INSERT INTO individui ({','.join(colonne)}) "
        f"VALUES ({','.join('?' * len(colonne))})",
        valori,
    )


# --- un figlio ha un padre solo --------------------------------------------

def test_fra_due_padri_vince_quello_dell_atto_di_nascita():
    """La prova migliore sui genitori e' l'atto di nascita del figlio.

    Lo scrive il padre stesso, il giorno del parto. Un atto di matrimonio
    di trent'anni dopo nomina gli stessi genitori a memoria di terzi, ed
    e' da li' che arrivano quasi tutte le letture rovinate.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(genealogia.SCHEMA_SQL)

    atto(c, 1, "nascita", 1840)
    atto(c, 2, "matrimonio", 1870)
    persona(c, 10, 1, "neonato", "Maria", "Lella")
    persona(c, 11, 2, "sposa", "Maria", "Lella")
    individuo(c, 1, "Maria", "Lella", anno_nascita=1840, nascita_origine="certa", menzioni=2)
    individuo(c, 2, "Domenico", "Lella", sesso="M", anno_nascita=1810, menzioni=9)
    individuo(c, 3, "Domenno", "Della", sesso="M", anno_nascita=1810, menzioni=1)
    c.executemany("INSERT INTO menzioni (persona, individuo) VALUES (?,?)",
                  [(10, 1), (11, 1)])
    c.executemany("INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (?,?,?,?)",
                  [(1, 2, "padre", 1), (1, 3, "padre", 2)])

    coerenza.rendi_coerente(c)
    rimasti = [r["genitore"] for r in c.execute(
        "SELECT genitore FROM legami WHERE figlio = 1 AND tipo = 'padre'")]
    assert rimasti == [2], "vince il padre dell'atto di nascita"
    assert c.execute("SELECT COUNT(*) FROM scartati").fetchone()[0] == 1


def test_niente_sparisce_senza_lasciare_traccia(conn):
    """Il legame tolto resta in 'scartati' con il motivo.

    E' la differenza fra correggere un archivio e censurarlo: la menzione
    non si tocca, e chi consulta continua a vedere la pagina che diceva
    quella cosa.
    """
    atto(conn, 1, "nascita", 1840)
    persona(conn, 10, 1, "neonato", "Maria", "Lella")
    individuo(conn, 1, "Maria", "Lella", anno_nascita=1840,
              nascita_origine="certa", menzioni=1)
    individuo(conn, 2, "Domenico", "Lella", sesso="M", anno_nascita=1835, menzioni=4)
    conn.execute("INSERT INTO menzioni (persona, individuo) VALUES (10, 1)")
    conn.execute(
        "INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (1, 2, 'padre', 1)")

    coerenza.rendi_coerente(conn)
    assert conn.execute("SELECT COUNT(*) FROM legami").fetchone()[0] == 0, \
        "un padre di cinque anni non e' un padre"
    riga = conn.execute("SELECT * FROM scartati").fetchone()
    assert riga["genere"] == "legame" and "5 anni" in riga["motivo"]
    assert conn.execute("SELECT COUNT(*) FROM menzioni").fetchone()[0] == 1


def test_i_parti_a_meno_di_nove_mesi_non_possono_restare_tutti_e_due(conn):
    for numero, (id_atto, anno, data) in enumerate(
        ((1, 1840, "1840-01-10"), (2, 1840, "1840-05-02")), start=1
    ):
        atto(conn, id_atto, "nascita", anno, data=data)
        persona(conn, 10 + id_atto, id_atto, "neonato", f"Figlio{numero}", "Lella")
        individuo(conn, id_atto, f"Figlio{numero}", "Lella",
                  anno_nascita=anno, nascita_origine="certa", menzioni=1)
        conn.execute("INSERT INTO menzioni (persona, individuo) VALUES (?,?)",
                     (10 + id_atto, id_atto))
    individuo(conn, 9, "Anna", "Lella", sesso="F", anno_nascita=1815, menzioni=6)
    conn.executemany(
        "INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (?,?,'madre',?)",
        [(1, 9, 1), (2, 9, 2)],
    )
    coerenza.rendi_coerente(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM legami WHERE genitore = 9").fetchone()[0] == 1


def test_i_gemelli_restano(conn):
    """Nati lo stesso giorno: quelli sono gemelli, e i gemelli esistono."""
    for numero, id_atto in enumerate((1, 2), start=1):
        atto(conn, id_atto, "nascita", 1840, data="1840-03-04")
        persona(conn, 10 + id_atto, id_atto, "neonato", f"Figlio{numero}", "Lella")
        individuo(conn, id_atto, f"Figlio{numero}", "Lella",
                  anno_nascita=1840, nascita_origine="certa", menzioni=1)
        conn.execute("INSERT INTO menzioni (persona, individuo) VALUES (?,?)",
                     (10 + id_atto, id_atto))
    individuo(conn, 9, "Anna", "Lella", sesso="F", anno_nascita=1815, menzioni=6)
    conn.executemany(
        "INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (?,?,'madre',?)",
        [(1, 9, 1), (2, 9, 2)],
    )
    coerenza.rendi_coerente(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM legami WHERE genitore = 9").fetchone()[0] == 2


def test_chi_ha_un_atto_di_morte_non_testimonia_dopo(conn):
    """Le menzioni successive alla morte sono di un omonimo: si divide."""
    atto(conn, 1, "morte", 1809)
    atto(conn, 2, "matrimonio", 1840)
    persona(conn, 10, 1, "defunto", "Domenico", "Pepe")
    persona(conn, 11, 2, "sposo", "Domenico", "Pepe")
    individuo(conn, 1, "Domenico", "Pepe", sesso="M", anno_morte=1809, menzioni=2)
    conn.executemany("INSERT INTO menzioni (persona, individuo) VALUES (?,?)",
                     [(10, 1), (11, 1)])

    coerenza.rendi_coerente(conn)
    assert conn.execute("SELECT COUNT(*) FROM individui").fetchone()[0] == 2
    nuovo = conn.execute(
        "SELECT individuo FROM menzioni WHERE persona = 11").fetchone()[0]
    assert nuovo != 1
    assert conn.execute("SELECT COUNT(*) FROM scartati WHERE genere='identita'"
                        ).fetchone()[0] == 1


# --- gli atti spezzati fra piu' pagine --------------------------------------

def test_un_matrimonio_su_tre_pagine_torna_un_atto_solo(conn):
    """Il caso vero del 1849: sposo, sposa e testimoni su tre facciate."""
    for id_atto, numero_pagina in ((1, 7), (2, 8), (3, 9)):
        atto(conn, id_atto, "matrimonio", 1849, numero="2",
             immagine=f"1849-matrimoni/{numero_pagina:04d}-pag-{numero_pagina}.jpg")
    persona(conn, 10, 1, "sposo", "Stanislao", "Ottaviano")
    persona(conn, 11, 1, "padre", "Giuseppe", "Ottaviano")
    persona(conn, 12, 2, "sposa", "Maria", "Cicchillitti")
    persona(conn, 13, 2, "padre", "Luigi", "Cicchillitti")
    persona(conn, 14, 3, "testimone", "Francesco", "Bracchi")

    cuciti, tolti = ricuci.ricuci_atti(conn)
    assert cuciti == 1 and tolti == 2
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM persone WHERE atto = 1").fetchone()[0] == 5


def test_lo_stesso_numero_a_venti_pagine_di_distanza_resta_due_atti(conn):
    """Un registro puo' contenere due anni, e allora il 'n. Uno' c'e' due volte."""
    atto(conn, 1, "matrimonio", 1813, numero="Uno",
         immagine="1813-matrimoni/0005-pag-5.jpg")
    atto(conn, 2, "matrimonio", 1813, numero="Uno",
         immagine="1813-matrimoni/0031-pag-31.jpg")
    persona(conn, 10, 1, "sposo", "Domenico", "Lella")
    persona(conn, 11, 2, "sposo", "Gesualdo", "Colaneri")

    cuciti, _ = ricuci.ricuci_atti(conn)
    assert cuciti == 0
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 2


def test_non_si_cuce_se_verrebbero_fuori_due_sposi_diversi(conn):
    """Due sposi diversi su pagine vicine sono due matrimoni, non uno lungo."""
    atto(conn, 1, "matrimonio", 1849, numero="2",
         immagine="1849-matrimoni/0007-pag-7.jpg")
    atto(conn, 2, "matrimonio", 1849, numero="2",
         immagine="1849-matrimoni/0008-pag-8.jpg")
    persona(conn, 10, 1, "sposo", "Stanislao", "Ottaviano")
    persona(conn, 11, 2, "sposo", "Domenico", "Cicchillitti")

    cuciti, _ = ricuci.ricuci_atti(conn)
    assert cuciti == 0


def test_la_stessa_facciata_letta_due_volte_si_butta(conn):
    atto(conn, 1, "nascita", 1840, numero="3",
         immagine="1840-nati/0007-pag-7.jpg")
    atto(conn, 2, "nascita", 1840, numero="3",
         immagine="1840-nati/0008-pag-8.jpg")
    persona(conn, 10, 1, "neonato", "Maria", "Lella")
    persona(conn, 11, 2, "neonato", "Maria", "Lella")

    cuciti, tolti = ricuci.ricuci_atti(conn)
    assert cuciti == 1 and tolti == 1
    assert conn.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 1


def test_il_numero_d_atto_si_legge_anche_in_lettere():
    assert ricuci.numero("2") == 2
    assert ricuci.numero("due") == 2
    assert ricuci.numero("Ventidue") == 22
    assert ricuci.numero("") is None
    assert ricuci.numero("terzultimo") is None


# --- le due fasi devono restare d'accordo -----------------------------------

def test_le_soglie_sono_le_stesse_di_quelle_che_la_fase_7_conta():
    """Se divergono, la fase 6 produce cio' che la fase 7 segnala.

    E' gia' successo: togliendo il peso dell'eta' senza allineare le
    soglie sono comparse 113 segnalazioni nuove in un colpo.
    """
    from history_maker import famiglie

    assert coerenza.qualita is qualita
    assert famiglie.qualita is qualita
