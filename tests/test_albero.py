"""Chi entra nell'albero e chi resta nell'archivio.

L'applicazione mostra due cose diverse con lo stesso nome: le persone
dell'**albero**, che hanno una parentela dichiarata da un atto, e tutte
le altre — testimoni, dichiaranti, ufficiali di stato civile — che i
registri nominano e basta. Sono un quarto dell'archivio e portano gli
stessi nomi: se entrano nella ricerca, coprono proprio la persona che si
sta cercando.

Qui si verifica che il confine tenga da tutte e due le parti: che chi ha
un legame non venga mai escluso, e che chi non ne ha non sia ne'
cercabile ne' cliccabile.
"""

from __future__ import annotations

import sqlite3

import pytest

from history_maker import albero, dataset, genealogia


@pytest.fixture
def archivio() -> sqlite3.Connection:
    """Un atto di nascita con dentro tutti e tre i casi.

    Nicola Pelliccia e' padre, Maria Pelliccia e' sua figlia, Antonio
    Pelliccia e' il testimone e non e' parente di nessuno. Il cognome e'
    lo stesso apposta: e' la situazione che rende la ricerca inutile se
    non si distingue.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dataset.SCHEMA_SQL)
    conn.executescript(genealogia.SCHEMA_SQL)

    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno) "
        "VALUES (1, '1809-nati', '0001.jpg', '3', 'nascita', 1809)"
    )
    for identificatore, nome, ruolo in (
        (1, "Nicola", "padre"),
        (2, "Maria", "neonato"),
        (3, "Antonio", "testimone"),
        (4, "Rosa", "madre"),
    ):
        conn.execute(
            "INSERT INTO persone (id, atto, ruolo, nome, cognome, nome_letto, "
            "cognome_letto, cognome_origine) VALUES (?, 1, ?, ?, 'Pelliccia', ?, "
            "'Pelliccia', 'atto')",
            (identificatore, ruolo, nome, nome),
        )
        conn.execute(
            "INSERT INTO individui (id, nome, cognome, menzioni, fondata_su) "
            "VALUES (?, ?, 'Pelliccia', 1, 'una sola menzione')",
            (identificatore, nome),
        )
        conn.execute(
            "INSERT INTO menzioni (persona, individuo) VALUES (?, ?)",
            (identificatore, identificatore),
        )

    conn.execute(
        "INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (2, 1, 'padre', 1)"
    )
    # Rosa non ha figli riconosciuti: sta nell'albero solo perche' e'
    # moglie di qualcuno. E' il secondo dei due modi di entrarci, e va
    # verificato a parte.
    conn.execute(
        "INSERT INTO unioni (marito, moglie, anno, origine) "
        "VALUES (1, 4, 1808, 'matrimonio')"
    )
    conn.execute(
        "INSERT INTO individui_fts(rowid, nome, cognome, varianti_nome, varianti_cognome) "
        "SELECT id, nome, cognome, varianti_nome, varianti_cognome FROM individui"
    )
    return conn


def test_la_ricerca_da_solo_chi_ha_un_posto_nell_albero(archivio):
    trovati = albero.cerca(archivio, "Pelliccia")
    assert {r["nome"] for r in trovati["risultati"]} == {"Nicola", "Maria", "Rosa"}


def test_la_ricerca_dice_quanti_ne_ha_lasciati_fuori(archivio):
    """Il testimone non si mostra, ma non si nasconde nemmeno.

    Chi cerca un nome che l'archivio conosce solo come testimone deve
    capire che il nome c'e': altrimenti crede che manchi la pagina.
    """
    assert albero.cerca(archivio, "Pelliccia")["fuori"] == 1
    assert albero.cerca(archivio, "Antonio")["risultati"] == []
    assert albero.cerca(archivio, "Antonio")["fuori"] == 1


def test_la_ricerca_vuota_non_interroga_l_indice(archivio):
    assert albero.cerca(archivio, " ") == {"risultati": [], "fuori": 0}


def test_la_scheda_sa_se_e_nell_albero(archivio):
    assert albero.persona(archivio, 1)["nell_albero"]
    assert albero.persona(archivio, 4)["nell_albero"], "il coniuge ci sta"
    assert not albero.persona(archivio, 3)["nell_albero"]


def test_chi_altro_c_era_distingue_chi_si_puo_aprire(archivio):
    """Nell'atto restano tutti, ma solo alcuni portano da qualche parte."""
    compagni = albero.compagni_di_atto(archivio, atto=1, escluso=2)
    apribili = {c["nome"]: bool(c["nell_albero"]) for c in compagni}
    assert apribili == {"Nicola": True, "Rosa": True, "Antonio": False}


def test_le_statistiche_contano_le_due_cose(archivio):
    numeri = albero.statistiche(archivio)
    assert numeri["individui"] == 4
    assert numeri["individui_albero"] == 3


# --- l'albero disegnato -----------------------------------------------------

def albero_di_prova() -> sqlite3.Connection:
    """Filippo, i suoi due figli, e i nipoti per parte di figlia.

    E' la forma che ha fatto emergere due difetti veri: il nipote appeso
    alla sola madre — che nel disegno sembrava capitato li' per caso — e
    il genero messo su una generazione diversa da quella della moglie.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dataset.SCHEMA_SQL)
    conn.executescript(genealogia.SCHEMA_SQL)
    for identificatore, nome, cognome in (
        (1, "Filippo", "Lella"), (2, "Marianna", "Lella"),
        (3, "Egidio", "Pacilli"), (4, "Pietro", "Pacilli"),
    ):
        conn.execute(
            "INSERT INTO individui (id, nome, cognome, menzioni, fondata_su) "
            "VALUES (?,?,?,1,'prova')", (identificatore, nome, cognome)
        )
    conn.executemany(
        "INSERT INTO legami (figlio, genitore, tipo) VALUES (?,?,?)",
        [(2, 1, "padre"), (4, 2, "madre"), (4, 3, "padre")],
    )
    conn.execute(
        "INSERT INTO unioni (marito, moglie, anno, origine) "
        "VALUES (3, 2, 1810, 'matrimonio')"
    )
    return conn


def test_il_nipote_si_porta_dietro_tutti_e_due_i_genitori():
    """Senza il padre accanto, il nipote sembra arrivato dal nulla."""
    grafo = albero.albero(albero_di_prova(), 1, su=0, giu=3)
    dentro = {n["id"] for n in grafo["nodi"]}
    assert 3 in dentro, "il padre del nipote deve entrare nell'albero"
    genitori_di_pietro = {
        a["da"] for a in grafo["archi"]
        if a["tipo"] == "filiazione" and a["a"] == 4
    }
    assert genitori_di_pietro == {2, 3}


def test_il_coniuge_sta_sulla_generazione_di_chi_ha_sposato():
    grafo = albero.albero(albero_di_prova(), 1, su=0, giu=3)
    livelli = {n["id"]: n["generazione"] for n in grafo["nodi"]}
    assert livelli[3] == livelli[2], "marito e moglie sulla stessa riga"


def test_la_ricerca_trova_i_nomi_composti_attaccati():
    """'Domenico Antonio' e 'Domenicantonio' sono la stessa persona.

    Per l'indice sono parole diverse, e nemmeno il prefisso le fa
    incontrare: 'domenicantonio' non comincia per 'domenico'.
    """
    query = albero._query_fts(["domenico", "antonio", "lella"])
    assert '"domenicantonio"*' in query
    assert '"domenicoantonio"*' in query
    # La forma normale resta la prima: e' quella che deve vincere quando
    # trova qualcosa, e le altre sono un ripiego.
    assert query.startswith('("domenico"* AND "antonio"* AND "lella"*)')
