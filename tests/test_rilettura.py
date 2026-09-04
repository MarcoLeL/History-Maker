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


def persona(conn, id, atto_id, ruolo="defunto"):
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome) VALUES (?,?,?,?,?)",
        (id, atto_id, ruolo, "Nome", "Cognome"),
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


def test_una_pagina_gia_risposta_non_si_riseleziona(conn):
    """Il bug trovato eseguendo davvero il comando su un lotto grande.

    Senza questa esclusione, rilanciare 'rileggi' su un archivio con piu'
    pagine leggibili di quante 'esegui' ne processi in un colpo restava
    bloccato per sempre sulle stesse prime pagine — ormai tutte in
    cache — invece di avanzare verso le successive.
    """
    for numero in range(1, 4):
        atto(conn, numero)
        anomalia(conn, "SURNAME_ANOMALY", [numero], [numero], priorita=float(4 - numero))
    conn.executescript(rilettura.SCHEMA_TABELLA)
    conn.execute(
        "INSERT INTO riletture (chiave, atto, stato) VALUES ('x', 1, 'risposta')"
    )
    assert rilettura.casi(conn, 10) == [2, 3]


def test_una_pagina_fallita_puo_essere_ritentata(conn):
    """Solo le risposte vere tolgono una pagina dalla coda.

    Un fallimento — un'immagine mancante, un ritaglio andato male — non
    deve bloccare per sempre l'unica occasione di leggere quella pagina.
    """
    atto(conn, 1)
    anomalia(conn, "SURNAME_ANOMALY", [1], [1])
    conn.executescript(rilettura.SCHEMA_TABELLA)
    conn.execute(
        "INSERT INTO riletture (chiave, atto, stato) VALUES ('x', 1, 'fallita')"
    )
    assert rilettura.casi(conn, 10) == [1]


def test_casi_regge_senza_la_tabella_riletture(conn):
    """Il primissimo lancio, prima che la tabella esista."""
    atto(conn, 1)
    anomalia(conn, "SURNAME_ANOMALY", [1], [1])
    assert rilettura.casi(conn, 10) == [1]


# --- la rilettura ampia: ogni pagina, non solo quelle segnalate -----------

def test_tutti_gli_atti_prende_ogni_pagina_non_ancora_fatta(conn):
    """Senza nessuna anomalia: e' proprio il punto.

    Il caso che l'ha resa necessaria — Pietrangelo Morella, quasi
    certamente un Moretta come tutta la sua famiglia — non aveva mai
    generato un'anomalia, perche' l'atto e il padre dichiarato erano
    d'accordo sulla stessa grafia. 'casi()' non l'avrebbe mai trovato;
    questa si'.
    """
    for numero, anno in ((1, 1810), (2, 1820), (3, 1815)):
        atto(conn, numero, anno=anno)
        persona(conn, numero, numero)
    assert rilettura.tutti_gli_atti(conn, 10) == [1, 3, 2]   # per anno


def test_tutti_gli_atti_esclude_le_pagine_gia_risposte(conn):
    """Stessa esclusione di 'casi()': altrimenti la coda non avanza."""
    atto(conn, 1, anno=1810)
    persona(conn, 1, 1)
    atto(conn, 2, anno=1811)
    persona(conn, 2, 2)
    conn.execute(
        "INSERT INTO riletture (chiave, atto, stato) VALUES ('x', 1, 'risposta')"
    )
    assert rilettura.tutti_gli_atti(conn, 10) == [2]


def test_tutti_gli_atti_salta_le_pagine_senza_nessuno(conn):
    """Una copertina o un indice senza persone non e' un atto da rileggere:
    non c'e' niente da confrontare con un contesto genealogico."""
    atto(conn, 1, anno=1810)
    persona(conn, 1, 1)
    atto(conn, 2, anno=1811)  # nessuna persona: copertina o pagina bianca
    assert rilettura.tutti_gli_atti(conn, 10) == [1]


def test_tutti_gli_atti_rispetta_da_anno(conn):
    for numero, anno in ((1, 1810), (2, 1850), (3, 1890)):
        atto(conn, numero, anno=anno)
        persona(conn, numero, numero)
    assert rilettura.tutti_gli_atti(conn, 10, da_anno=1850) == [2, 3]


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


def test_il_sistema_chiede_plausibilita_del_nome(dato):
    """Il caso vero che l'ha resa necessaria: 'Teodoro', scritto due
    volte nello stesso atto e attestato 31 volte, letto 'Cynodoro' (zero
    attestazioni) a confidenza 0,95 — il contesto c'era, e non e'
    bastato."""
    assert "VOCABOLARIO DEL PAESE" in rilettura.SISTEMA
    assert "genealogista che conosce il paese" in rilettura.SISTEMA


def test_il_vocabolario_entra_nell_istruzione_solo_se_dato(dato):
    testo_senza = rilettura.istruzione(dato)
    assert "VOCABOLARIO DEL PAESE" not in testo_senza

    testo_con = rilettura.istruzione(dato, {"nomi": ["Marzio"], "cognomi": ["Lella"]})
    assert "VOCABOLARIO DEL PAESE" in testo_con
    assert "Marzio" in testo_con


def test_la_chiave_cambia_se_cambia_il_vocabolario(dato):
    prima = rilettura.chiave(dato, "gemini-3.6-flash", {"nomi": ["Marzio"]})
    dopo = rilettura.chiave(dato, "gemini-3.6-flash", {"nomi": ["Marzio", "Lella"]})
    assert prima != dopo


# --- il vocabolario del paese -----------------------------------------

def _individuo(conn, id, nome, cognome):
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
        "VALUES (?,?,?,?,1)", (id, f"P{id}", nome, cognome),
    )


def _fatto(conn, individuo, tipo, valore, interpretato=None):
    conn.execute(
        "INSERT INTO fatti (individuo, tipo, grezzo, interpretato) "
        "VALUES (?,?,?,?)", (individuo, tipo, valore, interpretato),
    )


def test_il_vocabolario_conta_le_persone_non_le_menzioni(conn):
    """Il sindaco che firma mille atti non deve pesare come mille persone
    di nome Egidio: la domanda e' quante persone diverse portano quel
    nome, non quante volte compare la sua firma."""
    for numero in range(5):
        _individuo(conn, numero, "Marzio", "Lella")
    _individuo(conn, 100, "Egidio", "Pelliccia")

    v = rilettura.vocabolario_del_paese(conn)
    assert v["nomi"][0] == "Marzio"


def test_il_vocabolario_delle_contrade_e_dei_mestieri_usa_l_interpretato(conn):
    """La grafia storpiata non deve far testo da sola: conta la forma che
    il vocabolario del paese ha gia' ricondotto."""
    for _ in range(5):
        _fatto(conn, 1, "professione", "Agrimenfore", interpretato="Agrimensore")
    _fatto(conn, 2, "professione", "Contadino")   # senza interpretato: resta il grezzo

    v = rilettura.vocabolario_del_paese(conn)
    assert "Agrimensore" in v["mestieri"]
    assert "Agrimenfore" not in v["mestieri"]
    assert "Contadino" in v["mestieri"]


def test_il_vocabolario_rispetta_i_tetti(conn):
    for numero in range(rilettura.VOCABOLARIO_TOP_NOMI + 10):
        _individuo(conn, numero, f"Nome{numero}", "Cognome")
    v = rilettura.vocabolario_del_paese(conn)
    assert len(v["nomi"]) == rilettura.VOCABOLARIO_TOP_NOMI


def test_il_vocabolario_su_un_database_vuoto_non_fallisce(conn):
    v = rilettura.vocabolario_del_paese(conn)
    assert v == {"nomi": [], "cognomi": [], "contrade": [], "mestieri": []}


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


def test_una_forma_mai_vista_non_scavalca_una_forma_nota(conn, dato):
    """Il caso vero: 'Teodoro', attestato, letto 'Cynodoro', mai visto.

    Scritto due volte identico nello stesso atto, attestato 31 volte
    nell'archivio, e il modello ha detto 'Cynodoro' lo stesso — due
    volte, anche dopo aver aggiunto la regola sulla plausibilita' al
    prompt. Il testo non basta: serve un controllo che non dipenda
    dalla buona volonta' del modello.

    La fixture 'dato' legge 'Marzio' come nome della menzione 100 (e'
    quella lettura che una correzione a 'Cynodoro' sostituirebbe): perche'
    il caso sia lo stesso di quello vero, e' 'Marzio' — la forma
    **vecchia** — che deve essere gia' ben attestata nel paese.
    """
    for numero in range(5):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,?,?,1)", (900 + numero, f"P{900+numero}", "Marzio", "Vario"),
        )
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "nome", "Cynodoro", confidenza=0.95)]},
        "gemini-3.6-flash",
    )
    assert quante == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 0


def test_una_forma_mai_vista_passa_se_la_vecchia_non_e_attestata(conn, dato):
    """Il veto non e' assoluto: se anche la vecchia lettura era rara,
    non c'e' niente da difendere, e la nuova lettura passa come sempre."""
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "cognome", "Bellucci")]}, "gemini-3.6-flash"
    )
    assert quante == 1        # "d'Andria Motta" (fixture) non e' attestato altrove


def test_una_forma_mai_vista_passa_se_anche_lei_e_attestata(conn, dato):
    """Se la nuova lettura esiste gia' nel paese, non e' 'mai vista':
    passa come qualunque altra correzione."""
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
        "VALUES (901, 'P901', 'Altro', 'Bellucci', 1)"
    )
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "cognome", "Bellucci")]}, "gemini-3.6-flash"
    )
    assert quante == 1


def test_il_veto_di_plausibilita_vale_solo_per_nome_cognome_professione(conn, dato):
    """Una data o un'eta' non hanno un vocabolario da rispettare."""
    for numero in range(5):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,?,?,1)", (910 + numero, f"P{910+numero}", "Nome", "Attestato"),
        )
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "eta", "quaranta")]}, "gemini-3.6-flash"
    )
    assert quante == 1


def test_attestazioni_conta_le_persone_non_le_menzioni(conn):
    for numero in range(3):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,?,?,50)", (numero, f"P{numero}", "Marzio", "Lella"),
        )
    assert rilettura._attestazioni(conn, "cognome", "Lella") == 3


def test_attestazioni_su_professione_guarda_i_fatti(conn):
    conn.execute(
        "INSERT INTO fatti (individuo, tipo, grezzo, interpretato) "
        "VALUES (1, 'professione', 'Agrimenfore', 'Agrimensore')"
    )
    assert rilettura._attestazioni(conn, "professione", "Agrimensore") == 1
    assert rilettura._attestazioni(conn, "professione", "Agrimenfore") == 0  # e' il grezzo, non l'interpretato


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
