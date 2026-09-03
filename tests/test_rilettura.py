"""La rilettura della pagina intera, con il contesto accanto.

E' il passaggio che mancava fra la trascrizione cieca e la domanda chiusa
su una parola, ed e' anche il piu' facile da rovinare: basta presentare
il contesto come un dato di fatto e il modello rilegge finche' non torna,
producendo conferme che non valgono niente.

Questi test difendono le regole che lo rendono una prova invece che una
conferma.
"""

import json
import sqlite3

import pytest

from history_maker import dataset
from history_maker.ricostruzione import contesto, modello, rilettura


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(modello.SCHEMA_SQL)
    c.executescript(rilettura.SCHEMA_TABELLA)
    return c


def atto(conn, id, anno=1831, immagine=None):
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, "
        "testo_integrale) VALUES (?,?,?,?,?,?,?)",
        (id, "r", immagine or f"{id:04d}.jpg", str(id), "morte", anno, "testo"),
    )


def anomalia(conn, tipo, individui, atti, priorita=1.0, confidenza=0.6):
    conn.execute(
        "INSERT INTO anomalie (tipo, individui, atti, campo, descrizione, "
        "spiegazioni, confidenza, impatto, gravita, priorita, stato) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'aperta')",
        (tipo, json.dumps(list(individui)), json.dumps(list(atti)), "cognome",
         "un dubbio", "", confidenza, 1, "media", priorita),
    )


# --- la scelta delle pagine ------------------------------------------------

def test_una_persona_sola_non_si_prende_la_coda(conn):
    """Il difetto che si vede solo eseguendo.

    Una donna con quattro parti troppo ravvicinati ha otto atti in cui il
    dubbio compare, e senza tetto se ne prendeva sei su otto: una
    giornata di quota su una vita sola non e' un campione.
    """
    for numero in range(1, 9):
        atto(conn, numero)
        anomalia(conn, "DATE_ANOMALY", [77], [numero], priorita=9.0 - numero * 0.1)
    for numero in range(9, 13):
        atto(conn, numero)
        anomalia(conn, "SURNAME_ANOMALY", [numero], [numero], priorita=1.0)

    scelti = rilettura.casi(conn, 6)
    assert len(scelti) == 6
    # Al massimo due pagine per la donna dei parti ravvicinati.
    assert len([a for a in scelti if a <= 8]) <= rilettura.PAGINE_PER_PERSONA


def test_una_pagina_con_tre_dubbi_si_paga_una_volta(conn):
    atto(conn, 1)
    for _ in range(3):
        anomalia(conn, "SURNAME_ANOMALY", [10], [1])
    assert rilettura.casi(conn, 10) == [1]


def test_i_dubbi_di_identita_non_mandano_nessuno_all_immagine(conn):
    """La pagina dice cosa c'e' scritto, non chi era.

    Spendere quota per chiedere a un'immagine se due schede sono la
    stessa persona e' chiedere di indovinare invece che di leggere.
    """
    atto(conn, 1)
    anomalia(conn, "DUPLICATE_PERSON", [10, 11], [1], priorita=99.0)
    assert rilettura.casi(conn, 10) == []


# --- il prompt -------------------------------------------------------------

@pytest.fixture
def dato(conn):
    atto(conn, 1)
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
        "VALUES (10, 'P10', 'Marzio', 'Lella', 1)"
    )
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
        "VALUES (100, 1, 'defunto', 'Marzio', ?, 'atto')", ("d'Andria Motta",)
    )
    conn.execute("INSERT INTO menzioni (persona, individuo, certa) VALUES (100, 10, 1)")
    return contesto.per_documento(conn, 1)


def test_il_contesto_e_presentato_come_ipotesi(dato):
    """Senza questa parola tutto il resto e' controproducente."""
    testo = rilettura.istruzione(dato)
    assert "IPOTESI" in testo
    assert "CONFLITTO_CON_IL_CONTESTO" in testo


def test_la_pagina_viene_prima_del_contesto(dato):
    """L'ordine non e' estetica.

    Mettere il contesto per primo lo trasformerebbe nella domanda invece
    che nel termine di paragone.
    """
    testo = rilettura.istruzione(dato)
    assert testo.index("TRASCRIZIONE PRECEDENTE") < testo.index("CONTESTO GENEALOGICO")


def test_il_sistema_vieta_di_correggere_perche_torni(dato):
    assert "NON e' correggere la trascrizione perche' torni" in rilettura.SISTEMA
    assert "ha ragione l'immagine" in rilettura.SISTEMA


def test_non_decidere_e_una_risposta_prevista():
    assert "PROVE_INSUFFICIENTI" in rilettura.VERDETTI
    assert "NON_LEGGIBILE" in rilettura.VERDETTI
    assert "PROVE_INSUFFICIENTI" in rilettura.SISTEMA


# --- la cache --------------------------------------------------------------

def test_la_chiave_cambia_se_cambia_l_immagine(dato):
    """L'impronta e' del contenuto, non del nome del file.

    Indicizzare sul percorso vuol dire che riscaricare le pagine a piena
    risoluzione non invalida le risposte date su quelle ridotte — e
    quelle sono proprio le risposte che avrebbe senso rifare.
    """
    dato["atto"]["impronta_immagine"] = "aaa"
    prima = rilettura.chiave(dato, "gemini-3.6-flash")
    dato["atto"]["impronta_immagine"] = "bbb"
    assert rilettura.chiave(dato, "gemini-3.6-flash") != prima


def test_la_chiave_cambia_se_cambia_il_contesto(dato):
    prima = rilettura.chiave(dato, "gemini-3.6-flash")
    dato["contesto_genealogico"]["persone"][0]["cognome"] = "Lelli"
    assert rilettura.chiave(dato, "gemini-3.6-flash") != prima


def test_la_chiave_cambia_se_cambia_il_modello(dato):
    assert (rilettura.chiave(dato, "gemini-3.6-flash")
            != rilettura.chiave(dato, "gemini-3.5-flash"))


# --- le correzioni ---------------------------------------------------------

def _campo(menzione, campo, lettura, verdetto="CONFLITTO_CON_LA_TRASCRIZIONE",
           confidenza=0.95):
    return {"menzione": menzione, "campo": campo, "lettura_dall_immagine": lettura,
            "verdetto": verdetto, "confidenza": confidenza, "spiegazione": "si legge"}


def test_una_lettura_diversa_e_sicura_diventa_una_correzione(conn, dato):
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "cognome", "Lella")]}, "gemini-3.6-flash"
    )
    assert quante == 1
    riga = conn.execute(
        "SELECT azione, decisore, evidenze FROM decisioni"
    ).fetchone()
    assert riga["azione"] == "correzione"
    assert riga["decisore"] == "gemini"
    assert riga["evidenze"] == "cognome=Lella"


def test_una_conferma_non_e_una_correzione(conn, dato):
    """Confermare e' un risultato quanto correggere.

    Significa che li' a non tornare e' l'identita', non la lettura — e
    registrarla come correzione la farebbe sparire.
    """
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "cognome", "d'Andria Motta", "CONFERMATO")]},
        "gemini-3.6-flash",
    )
    assert quante == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 0


def test_una_lettura_incerta_non_corregge(conn, dato):
    """Una correzione sbagliata si propaga ai parenti, una mancata no."""
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "cognome", "Lella", confidenza=0.5)]},
        "gemini-3.6-flash",
    )
    assert quante == 0


def test_la_stessa_correzione_non_si_registra_due_volte(conn, dato):
    risposta = {"campi": [_campo(100, "cognome", "Lella")]}
    rilettura._correggi(conn, dato, risposta, "gemini-3.6-flash")
    assert rilettura._correggi(conn, dato, risposta, "gemini-3.6-flash") == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 1


def test_la_trascrizione_originale_non_viene_toccata(conn, dato):
    """La correzione entra come decisione, non come UPDATE.

    E' l'invariante del progetto: la fase 4 e' l'esito della lettura
    delle pagine, e riscriverla vorrebbe dire perdere la possibilita' di
    accorgersi che la correzione era sbagliata.
    """
    rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "cognome", "Lella")]}, "gemini-3.6-flash"
    )
    assert conn.execute(
        "SELECT cognome FROM persone WHERE id = 100"
    ).fetchone()[0] == "d'Andria Motta"


def test_una_risposta_illeggibile_non_fa_cadere_tutto():
    """Il ramo che era gia' costato una quota intera.

    'estrai_json' rende gia' la struttura: ripassarla a json.loads
    solleva un TypeError, e il ripiego trasformava ogni risposta buona in
    una vuota — senza lasciare traccia, perche' una risposta vuota e'
    esattamente cio' che si vede quando il modello non legge.
    """
    assert rilettura._interpreta("non e' json") == {}
    assert rilettura._interpreta("") == {}
    assert rilettura._interpreta('{"esito": "CONFERMATO"}') == {"esito": "CONFERMATO"}
    assert rilettura._interpreta('[{"esito": "CONFERMATO"}]') == {"esito": "CONFERMATO"}
