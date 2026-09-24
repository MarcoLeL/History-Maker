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


# --- la facciata: piu' atti, una chiamata sola ----------------------------

def test_gli_atti_della_stessa_facciata_stanno_in_una_chiamata(conn):
    """Su una facciata di registro stanno due, tre, fino a nove atti.

    Chiederli uno per volta vuol dire caricare la stessa immagine altre
    tante volte, e l'immagine e' quasi tutto il costo della chiamata.
    """
    for numero in (1, 2, 3):
        atto(conn, numero, immagine="pag-4.jpg")
        persona(conn, 100 + numero, numero)
    atto(conn, 4, immagine="pag-5.jpg")
    persona(conn, 104, 4)
    assert rilettura.facciate(conn, [1, 2, 3, 4]) == [[1, 2, 3], [4]]


def test_la_facciata_tiene_l_ordine_della_coda(conn):
    """La prima facciata resta quella dell'atto piu' urgente."""
    for numero, pagina in ((1, "a.jpg"), (2, "b.jpg"), (3, "a.jpg")):
        atto(conn, numero, immagine=pagina)
        persona(conn, 100 + numero, numero)
    assert rilettura.facciate(conn, [2, 3]) == [[2], [1, 3]]


def test_la_facciata_si_porta_dietro_gli_atti_non_ancora_letti(conn):
    """L'atto accanto e' gratis: l'immagine parte comunque.

    E' anche il modo di non lasciare indietro l'atto n. 4 solo perche'
    l'anomalia stava sul n. 3.
    """
    for numero in (1, 2, 3):
        atto(conn, numero, immagine="pag-4.jpg")
        persona(conn, 100 + numero, numero)
    assert rilettura.facciate(conn, [2]) == [[1, 2, 3]]


def test_la_facciata_non_ripesca_gli_atti_gia_risposti(conn):
    for numero in (1, 2, 3):
        atto(conn, numero, immagine="pag-4.jpg")
        persona(conn, 100 + numero, numero)
    conn.executescript(rilettura.SCHEMA_TABELLA)
    conn.execute("INSERT INTO riletture (chiave, atto, stato) "
                 "VALUES ('x', 1, 'risposta')")
    assert rilettura.facciate(conn, [2]) == [[2, 3]]


def test_un_atto_chiesto_da_solo_resta_da_solo(conn):
    """'--atto 30' chiede quell'atto, non la sua facciata."""
    for numero in (1, 2):
        atto(conn, numero, immagine="pag-4.jpg")
        persona(conn, 100 + numero, numero)
    assert rilettura.facciate(conn, [2], completa=False) == [[2]]


def test_da_rileggere_conta_facciate_non_atti(conn):
    """'--quante' e' il numero di chiamate, che e' cio' che la quota conta."""
    for numero in range(1, 7):
        atto(conn, numero, immagine=f"pag-{(numero - 1) // 2}.jpg")
        persona(conn, 100 + numero, numero)
    gruppi = rilettura.da_rileggere(conn, 2, tutte=True)
    assert gruppi == [[1, 2], [3, 4]]
    assert sum(len(g) for g in gruppi) == 4     # due chiamate, quattro atti


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


# --- la facciata: il prompt, la cache, lo smistamento ----------------------

@pytest.fixture
def facciata_doppia(conn):
    """Due atti sulla stessa immagine, ciascuno con la sua menzione."""
    for numero, menzione, individuo in ((1, 100, 10), (2, 200, 20)):
        atto(conn, numero, immagine="pag-4.jpg")
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Nome','Cognome',1)", (individuo, f"P{individuo}")
        )
        conn.execute(
            "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
            "VALUES (?,?,'defunto','Nome','Cognome','atto')", (menzione, numero)
        )
        conn.execute("INSERT INTO menzioni (persona, individuo, certa) "
                     "VALUES (?,?,1)", (menzione, individuo))
    return {
        "immagine": "pag-4.jpg", "impronta_immagine": "abc",
        "_percorso_immagine": "pag-4.jpg",
        "atti": [contesto.per_documento(conn, 1), contesto.per_documento(conn, 2)],
    }


def test_una_facciata_da_un_atto_solo_non_invalida_la_cache(dato):
    """Quattrocentoquarantanove risposte sono gia' state pagate.

    Se l'accorpamento cambiasse anche di una virgola il testo o la chiave
    di una facciata da un atto solo, si butterebbero tutte.
    """
    facciata = {"immagine": dato["atto"]["immagine"], "impronta_immagine": None,
                "_percorso_immagine": "x", "atti": [dato]}
    voc = {"nomi": ["Marzio"]}
    assert rilettura.istruzione_facciata(facciata, voc) == rilettura.istruzione(dato, voc)
    assert (rilettura.chiave_facciata(facciata, "m", voc)
            == rilettura.chiave(dato, "m", voc))
    assert rilettura._chiave_riga("impronta", dato, 1) == "impronta"


def test_gli_atti_di_una_facciata_hanno_chiavi_diverse(facciata_doppia):
    """'riletture.chiave' e' una chiave primaria.

    Con la stessa chiave l'ultimo atto scritto cancellerebbe i primi, e
    la facciata risulterebbe letta per un atto solo.
    """
    dati = facciata_doppia["atti"]
    assert len({rilettura._chiave_riga("impronta", d, len(dati)) for d in dati}) == 2


def test_il_contorno_del_prompt_si_manda_una_volta_sola(facciata_doppia):
    """E' l'unico sperpero che l'accorpamento non toglie da se'."""
    testo = rilettura.istruzione_facciata(
        facciata_doppia, {"nomi": ["Marzio"], "cognomi": ["Lella"]})
    assert testo.count("IL VOCABOLARIO DEL PAESE") == 1
    assert testo.count("LA TRASCRIZIONE PRECEDENTE") == 2


def test_la_risposta_di_una_facciata_si_smista_sugli_atti(facciata_doppia):
    fuori = rilettura._per_atto(
        {"atti": [
            {"atto": 1, "esito": "CONFERMATO", "campi": [_campo(100, "nome", "X")]},
            {"atto": 2, "esito": "AMBIGUO", "campi": [_campo(200, "nome", "Y")]},
        ]},
        facciata_doppia["atti"],
    )
    assert fuori[1]["esito"] == "CONFERMATO"
    assert fuori[2]["esito"] == "AMBIGUO"
    assert fuori[1]["campi"][0]["menzione"] == 100
    assert fuori[2]["campi"][0]["menzione"] == 200


def test_la_menzione_batte_l_identificativo_dell_atto(facciata_doppia):
    """L'errore da temere: attribuire a un atto cio' che si legge nell'altro.

    La menzione e' l'ancora solida — il modello ce l'ha davanti scritta,
    non la inventa — mentre l'identificativo dell'atto glielo si chiede
    di riecheggiare, ed e' riecheggiare che sbaglia.
    """
    fuori = rilettura._per_atto(
        {"atti": [{"atto": 1, "esito": "CONFERMATO",
                   "campi": [_campo(200, "nome", "Y")]}]},
        facciata_doppia["atti"],
    )
    assert fuori[2]["campi"][0]["menzione"] == 200
    assert fuori[1]["campi"] == []


def test_una_risposta_piatta_si_smista_lo_stesso(facciata_doppia):
    """Un modello a cui si chiede una forma non sempre la da', e una
    risposta buona nella forma sbagliata non va buttata."""
    fuori = rilettura._per_atto(
        {"esito": "CONFERMATO",
         "campi": [_campo(100, "nome", "X"), _campo(200, "nome", "Y")]},
        facciata_doppia["atti"],
    )
    assert fuori[1]["campi"][0]["menzione"] == 100
    assert fuori[2]["campi"][0]["menzione"] == 200
    assert fuori[1]["esito"] == fuori[2]["esito"] == "CONFERMATO"


def test_una_menzione_di_nessun_atto_non_si_perde(facciata_doppia):
    """Resta nel registro, dove '_correggi' la scartera' lasciandone traccia."""
    fuori = rilettura._per_atto(
        {"esito": "CONFERMATO", "campi": [_campo(999, "nome", "X")]},
        facciata_doppia["atti"],
    )
    assert sum(len(v["campi"]) for v in fuori.values()) == 1


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


def test_attestazioni_vede_anche_le_letture_scartate(conn):
    """La forma canonica non e' tutto quello che l'archivio ha letto.

    Il consolidamento riconduce le grafie rare alla forma dominante, e
    da fuori sembrano non essere mai esistite: sono proprio quelle su
    cui il veto viene interrogato.
    """
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, varianti_nome, menzioni) "
        "VALUES (920, 'P920', 'Mitrodoro', 'Marianacci', ?, 12)",
        ("Metrodoro | Metodoro | Metro | Mitrodoro",),
    )
    assert rilettura._attestazioni(conn, "nome", "Metro") == 1
    assert rilettura._attestazioni(conn, "nome", "Mitrodoro") == 1
    assert rilettura._attestazioni(conn, "nome", "Cynodoro") == 0


def test_una_forma_consolidata_sotto_un_altra_non_e_mai_vista(conn, dato):
    """Il rovescio esatto di 'Cynodoro', ed e' costato una scheda doppia.

    'Metro Marianacci' e' scritto cosi' due volte nell'atto del 1809 e
    letto cosi' in altri due atti, ma sta in archivio sotto
    'Mitrodoro'. Contando la sola forma canonica il veto lo trattava
    come una parola mai esistita e buttava la lettura giusta: il
    calzolaio del 1809 restava una scheda a se', separata dalla
    propria — stessa moglie, stesso anno di nascita, stesso mestiere.
    """
    for numero in range(5):        # 'Marzio', la forma vecchia, e' ben attestata
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,?,?,1)", (930 + numero, f"P{930+numero}", "Marzio", "Vario"),
        )
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, varianti_nome, menzioni) "
        "VALUES (940, 'P940', 'Mitrodoro', 'Vario', ?, 12)", ("Mitrodoro | Metro",),
    )
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "nome", "Metro")]}, "gemini-3.6-flash"
    )
    assert quante == 1


def test_il_cognome_incollato_nel_nome_viene_tolto(conn, dato):
    """Il caso della firma: «Giuseppe Pizzi Sindaco».

    Il modello legge la riga intera e la mette nel campo che gli e'
    stato chiesto. La lettura e' giusta, il dato no: 'nome' = «Giuseppe
    Pizzi» accanto a 'cognome' = «Pizzi» fa «Giuseppe Pizzi Pizzi», e
    la forma canonica mai vista trasforma il nome piu' comune del paese
    in un indizio raro.
    """
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "nome", "Giuseppe d'Andria Motta")]},
        "gemini-3.6-flash",
    )
    assert quante == 1
    riga = conn.execute("SELECT evidenze FROM decisioni").fetchone()
    assert riga["evidenze"] == "nome=Giuseppe"


def test_un_nome_tutto_cognome_non_diventa_una_correzione(conn, dato):
    """Sbucciato non resta niente: non c'e' nessun nome da registrare."""
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "nome", "d'Andria Motta")]},
        "gemini-3.6-flash",
    )
    assert quante == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 0


def test_il_cognome_non_si_sbuccia_mai(conn, dato):
    """'Di Nardo' e' un cognome intero, non un nome piu' un cognome.

    Gli atti scrivono «nome cognome» e mai il contrario: togliere un
    pezzo dal cognome rovinerebbe le forme composte, che in questo
    paese sono la meta' dei casati.
    """
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
        "VALUES (950, 'P950', 'Nardo', 'Rossi', 1)"
    )
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
        "VALUES (101, 1, 'testimone', 'Nardo', 'Rossi', 'atto')"
    )
    conn.execute("INSERT INTO menzioni (persona, individuo, certa) VALUES (101, 950, 1)")
    quante = rilettura._correggi(
        conn, contesto.per_documento(conn, 1),
        {"campi": [_campo(101, "cognome", "Di Nardo")]}, "gemini-3.6-flash",
    )
    assert quante == 1
    riga = conn.execute("SELECT evidenze FROM decisioni").fetchone()
    assert riga["evidenze"] == "cognome=Di Nardo"


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


# --- tornare su cio' che si e' deciso prima di sapere ----------------------

def test_una_frase_non_e_un_nome():
    assert rilettura._forma_da_nome("Giuseppe")
    assert rilettura._forma_da_nome("Maria Nicola")
    assert rilettura._forma_da_nome("Di Nardo")
    assert not rilettura._forma_da_nome("Dantpilo Finan' di Desio Viene")
    assert not rilettura._forma_da_nome("Siaino oppure Sianis (nel testo si legge)")
    assert not rilettura._forma_da_nome("ignoto")
    assert not rilettura._forma_da_nome("")


def _correzione(conn, menzione, campo, valore, versione="1.0.0"):
    from history_maker.ricostruzione import registro
    return registro.annota(
        conn, "correzione", (menzione,), "presa allora", confidenza=0.95,
        decisore="gemini", modello="vecchio", versione_prompt=versione,
        evidenze=(f"{campo}={valore}",),
    )


def test_il_veto_si_applica_anche_a_ritroso(conn, dato):
    """Le prime 437 pagine sono state lette prima che il veto esistesse.

    Ripassarle alla regola di oggi e' l'unico modo di non avere due
    epoche con due metri diversi.
    """
    for numero in range(5):        # 'Marzio' e' ben attestato
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Marzio','Vario',1)", (960 + numero, f"P{960+numero}")
        )
    _correzione(conn, 100, "nome", "Cynodoro")
    verdetti = rilettura.ripassa_al_veto(conn, "1.0.0")
    assert [v["motivo"] for v in verdetti] == ["non plausibile"]
    assert verdetti[0]["sostituto"] is None


def test_a_ritroso_il_cognome_nel_nome_si_sbuccia_invece_di_annullarsi(conn, dato):
    """La lettura era giusta, solo impacchettata male: buttarla e' peggio."""
    _correzione(conn, 100, "nome", "Giuseppe d'Andria Motta")
    verdetti = rilettura.ripassa_al_veto(conn, "1.0.0")
    assert verdetti[0]["motivo"] == "cognome nel nome"
    assert verdetti[0]["sostituto"] == "Giuseppe"


def test_a_ritroso_non_si_cancella_niente(conn, dato):
    """La riga vecchia resta, marcata da quella che la disfa."""
    from history_maker.ricostruzione import registro
    vecchia = _correzione(conn, 100, "cognome", "ignoto")
    assert rilettura.ripassa_al_veto(conn, "1.0.0", applica=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM decisioni WHERE id = ?", (vecchia,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM decisioni WHERE disfa = ?", (vecchia,)
    ).fetchone()[0] == 1
    # e non vale piu': la trascrizione torna a dire la sua
    assert (100, "cognome") not in registro.correzioni(conn)


def test_a_ritroso_una_correzione_che_regge_non_si_tocca(conn, dato):
    _correzione(conn, 100, "cognome", "Bellucci")
    assert rilettura.ripassa_al_veto(conn, "1.0.0", applica=True) == []


def test_a_ritroso_l_eta_non_si_giudica(conn, dato):
    """Un'eta' non ha un vocabolario da rispettare: non c'e' regola con
    cui ripassarla, e inventarne una adesso sarebbe peggio."""
    _correzione(conn, 100, "eta", "quaranta")
    assert rilettura.ripassa_al_veto(conn, "1.0.0") == []


def test_le_pagine_di_un_vecchio_prompt_tornano_in_coda(conn):
    atto(conn, 1)
    persona(conn, 100, 1)
    conn.executescript(rilettura.SCHEMA_TABELLA)
    conn.execute("INSERT INTO riletture (chiave, atto, versione_prompt, stato) "
                 "VALUES ('x', 1, '1.0.0', 'risposta')")
    assert rilettura.tutti_gli_atti(conn, 10) == []      # oggi e' fuori coda

    assert rilettura.da_rifare(conn, "1.0.0", applica=True) == [1]
    assert rilettura.tutti_gli_atti(conn, 10) == [1]     # ci torna
    # ma la risposta vecchia resta leggibile: e' l'unica prova di come
    # rispondeva quel prompt
    riga = conn.execute("SELECT stato FROM riletture WHERE chiave = 'x'").fetchone()
    assert riga["stato"] == "superata"


# --- l'atto che sborda sulla scansione dopo -------------------------------

def test_un_atto_solo_sulla_pagina_si_porta_la_scansione_dopo(conn, tmp_path):
    """Il difetto trovato eseguendo: 14 atti su facciate da uno, 14
    correzioni; 44 atti su facciate intere, zero.

    In 139 registri l'atto comincia sulla pagina destra e finisce in
    cima alla scansione dopo. Senza quella, il modello ha sotto gli
    occhi la coda dell'atto PRECEDENTE e non la fine del proprio — e
    battezza il neonato col nome del bambino di un altro.
    """
    for numero, pagina in ((1, "reg/0040.jpg"), (2, "reg/0041.jpg")):
        atto(conn, numero, immagine=pagina)
        persona(conn, 100 + numero, numero)
    for pagina in ("0040.jpg", "0041.jpg"):
        (tmp_path / "reg").mkdir(exist_ok=True)
        (tmp_path / "reg" / pagina).write_bytes(b"finta immagine " + pagina.encode())

    facciata = rilettura.prepara(conn, [[1]], tmp_path)[0]
    assert facciata["seguito"] == "reg/0041.jpg"
    assert facciata["impronta_seguito"]
    # e il prompt deve dire cos'e', o si rifa' lo stesso errore al contrario
    testo = rilettura.istruzione_facciata(facciata)
    assert "IMMAGINE 2" in testo and "atto SEGUENTE" in testo


def test_una_facciata_con_piu_atti_non_si_porta_niente(conn, tmp_path):
    """Se su una pagina ci stanno due atti sono corti e finiscono dove
    cominciano: la seconda immagine sarebbe un costo senza ragione."""
    for numero in (1, 2):
        atto(conn, numero, immagine="reg/0040.jpg")
        persona(conn, 100 + numero, numero)
    atto(conn, 3, immagine="reg/0041.jpg")
    persona(conn, 103, 3)
    (tmp_path / "reg").mkdir()
    for pagina in ("0040.jpg", "0041.jpg"):
        (tmp_path / "reg" / pagina).write_bytes(b"x")
    facciata = rilettura.prepara(conn, [[1, 2]], tmp_path)[0]
    assert "seguito" not in facciata


def test_il_seguito_non_esce_dal_registro(conn, tmp_path):
    """L'ultimo atto di un registro non prende la prima pagina del
    registro dopo: sarebbe un altro anno e un'altra mano."""
    atto(conn, 1, immagine="reg-a/0040.jpg")
    persona(conn, 101, 1)
    atto(conn, 2, immagine="reg-b/0001.jpg")
    persona(conn, 102, 2)
    for cartella, pagina in (("reg-a", "0040.jpg"), ("reg-b", "0001.jpg")):
        (tmp_path / cartella).mkdir()
        (tmp_path / cartella / pagina).write_bytes(b"x")
    facciata = rilettura.prepara(conn, [[1]], tmp_path)[0]
    assert "seguito" not in facciata


def test_il_seguito_cambia_la_chiave_ma_solo_a_chi_ce_l_ha(conn, tmp_path, dato):
    """Le facciate intere tengono la chiave di prima — le loro risposte
    valgono ancora. Quelle spezzate la cambiano, ed e' voluto: erano
    proprio le risposte da rifare."""
    intera = {"immagine": dato["atto"]["immagine"], "impronta_immagine": None,
              "_percorso_immagine": "x", "atti": [dato]}
    assert (rilettura.chiave_facciata(intera, "m")
            == rilettura.chiave(dato, "m"))
    spezzata = dict(intera, seguito="reg/0041.jpg", impronta_seguito="abc")
    assert rilettura.chiave_facciata(spezzata, "m") != rilettura.chiave(dato, "m")


def test_si_toglie_dal_nome_anche_un_cognome_che_non_era_il_suo(conn, dato):
    """«Giovanni Maria Nanni» riletto «Giovanni Marianacci».

    La guardia che confronta col cognome trascritto non scatta — nessuna
    delle due parole e' 'Nanni' — perche' il modello ha corretto insieme
    nome e cognome. La domanda giusta la fa all'archivio: in questo
    paese, quest'ultima parola e' un cognome o un nome?
    """
    for numero in range(12):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Vario','Marianacci',1)", (970 + numero, f"P{970+numero}")
        )
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "nome", "Giovanni Marianacci")]},
        "gemini-3.6-flash",
    )
    assert quante == 1
    assert conn.execute("SELECT evidenze FROM decisioni").fetchone()[0] == "nome=Giovanni"


def test_un_nome_che_e_anche_cognome_non_si_tocca(conn, dato):
    """'Salvatore' e' un cognome del paese ed e' anche un nome vero:
    245 contro 92. Toglierlo da 'Carmine Salvatore' sarebbe un danno."""
    for numero in range(10):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Vario','Salvatore',1)", (980 + numero, f"P{980+numero}")
        )
    for numero in range(6):        # attestato anche come NOME
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Salvatore','Altro',1)", (995 + numero, f"P{995+numero}")
        )
    assert rilettura._senza_il_cognome(
        {"cognome": "Vario"}, "nome", "Carmine Salvatore", conn) == "Carmine Salvatore"


def test_sbucciare_non_lascia_una_particella(conn):
    """'Di Laudo' non deve diventare 'Di'."""
    for numero in range(12):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Vario','Laudo',1)", (940 + numero, f"P{940+numero}")
        )
    assert rilettura._senza_il_cognome(
        {"cognome": "X"}, "nome", "Di Laudo", conn) == "Di Laudo"


# --- il muro del giorno e l'intoppo di trenta secondi ----------------------

class _MotoreCapriccioso:
    """Dice di no qualche volta, poi risponde."""

    def __init__(self, rifiuti, giornaliera=False, attesa=1.0):
        self.rifiuti, self.giornaliera, self.attesa = rifiuti, giornaliera, attesa
        self.chiamate = 0

    def esegui(self, richiesta):
        from history_maker.backend import LimiteUsoRaggiunto, Risposta
        self.chiamate += 1
        if self.chiamate <= self.rifiuti:
            raise LimiteUsoRaggiunto(
                "servizio occupato", attesa_s=self.attesa,
                giornaliera=self.giornaliera,
            )
        return Risposta(ok=True, testo='{"esito": "CONFERMATO", "campi": []}')


def test_una_congestione_non_e_la_fine_della_giornata(monkeypatch):
    """Il difetto visto eseguendo: 29 facciate su 280, e il comando
    annuncia la quota finita con 93 richieste su 500.

    Il 503 'high demand' passa da solo in mezzo minuto. Fermare per
    quello un lavoro di quattrocento chiamate e' buttare la giornata.
    """
    monkeypatch.setattr(rilettura.time, "sleep", lambda _: None)
    esiti = {"quota_esaurita": False, "attese": 0}
    motore = _MotoreCapriccioso(rifiuti=2)
    risposta = rilettura._chiedi(motore, object(), esiti)
    assert risposta is not None and risposta.ok
    assert esiti["attese"] == 2 and not esiti["quota_esaurita"]
    assert motore.chiamate == 3          # ha ritentato la stessa facciata


def test_il_muro_del_giorno_invece_ferma(monkeypatch):
    monkeypatch.setattr(rilettura.time, "sleep", lambda _: None)
    esiti = {"quota_esaurita": False, "attese": 0}
    motore = _MotoreCapriccioso(rifiuti=1, giornaliera=True)
    assert rilettura._chiedi(motore, object(), esiti) is None
    assert esiti["quota_esaurita"]
    assert motore.chiamate == 1          # non insiste contro un muro


def test_un_servizio_che_non_torna_su_non_si_ritenta_all_infinito(monkeypatch):
    monkeypatch.setattr(rilettura.time, "sleep", lambda _: None)
    esiti = {"quota_esaurita": False, "attese": 0}
    motore = _MotoreCapriccioso(rifiuti=99)
    assert rilettura._chiedi(motore, object(), esiti) is None
    assert esiti["quota_esaurita"]
    assert motore.chiamate <= 6          # il tetto ai ritentativi tiene


# --- i verdetti che il modello si inventa ---------------------------------

def test_i_sinonimi_del_verdetto_si_riconducono(conn, dato):
    """Trentasei letture buttate perche' il confronto era per stringa esatta.

    Il modello dice ERRORE_NELLA_TRASCRIZIONE o CORRETTO_DA_IMMAGINE
    invece di CONFLITTO_CON_LA_TRASCRIZIONE: per un lettore e' lo stesso
    verdetto, per un '!=' e' un'altra cosa, e la correzione spariva.
    """
    for grezzo in ("ERRORE_NELLA_TRASCRIZIONE", "CORRETTO_DA_IMMAGINE",
                   "errore di trascrizione", "CORRETTO"):
        assert rilettura._verdetto_canonico(grezzo) == "CONFLITTO_CON_LA_TRASCRIZIONE"
    for grezzo in ("CONFORME", "CONFERMATO_DALL_IMMAGINE", "CONFERMATO"):
        assert rilettura._verdetto_canonico(grezzo) == "CONFERMATO"


def test_un_sinonimo_diventa_una_correzione(conn, dato):
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "cognome", "Lella", "ERRORE_NELLA_TRASCRIZIONE")]},
        "gemini-3.6-flash",
    )
    assert quante == 1


def test_corretto_dal_contesto_resta_inerte(conn, dato):
    """Dice che il modello ha cambiato la lettura per farla tornare col
    grafo: e' cio' che il modulo vieta al primo paragrafo, e non deve
    diventare una correzione per la porta di servizio dei sinonimi."""
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(100, "cognome", "Lella", "CORRETTO_DAL_CONTESTO")]},
        "gemini-3.6-flash",
    )
    assert quante == 0


def test_un_verdetto_sconosciuto_non_diventa_un_conflitto(conn, dato):
    """Allargare il confronto a 'qualunque cosa somigli' sarebbe il modo
    di trasformare ogni bizzarria in una correzione."""
    assert rilettura._verdetto_canonico("BOH_VEDIAMO") == "BOH_VEDIAMO"
    assert rilettura._correggi(
        conn, dato, {"campi": [_campo(100, "cognome", "Lella", "BOH_VEDIAMO")]},
        "gemini-3.6-flash") == 0


def test_un_refuso_nel_nome_del_verdetto_non_perde_la_correzione(conn, dato):
    """'CONFLITTO_CON_LA_TRASCRITTORE' e' costato «bavaro» -> «bovaro»
    a confidenza 0,95."""
    assert (rilettura._verdetto_canonico("CONFLITTO_CON_LA_TRASCRITTORE")
            == "CONFLITTO_CON_LA_TRASCRIZIONE")
    assert (rilettura._verdetto_canonico("CONFLITTO_CON_IL_CONTESTO_FAMILIARE")
            == "CONFLITTO_CON_IL_CONTESTO")
    assert rilettura._verdetto_canonico("CONFLITTO_ALTRO") == "CONFLITTO_ALTRO"


def test_un_onorifico_non_e_un_cognome():
    """«Donna Federica Zara» stava in un campo COGNOME: tre parole, dentro
    il tetto, ma nessun casato comincia per 'Donna'."""
    assert not rilettura._forma_da_nome("Donna Federica Zara")
    assert not rilettura._forma_da_nome("Don Mitodoro")
    assert rilettura._forma_da_nome("Di Nardo")
    assert rilettura._forma_da_nome("Maria Nicola")


def test_una_annotazione_non_e_una_professione(conn, dato):
    """'contadina' -> «coi [coabitante]»: la parentesi quadra e' il
    modello che ragiona, non la pagina che parla."""
    _correzione(conn, 100, "professione", "coi [coabitante]")
    verdetti = rilettura.ripassa_al_veto(conn, "1.0.0")
    assert [v["motivo"] for v in verdetti] == ["non e' una lettura, e' un'annotazione"]


def test_sbucciare_non_lascia_un_troncone(conn):
    """«Giuseppe di Tommaso» non deve diventare «Giuseppe di».

    Visto in una passata vera: la guardia toglieva l'ultimo pezzo quando
    era un cognome del paese, e lasciava appesa la preposizione. Non
    basta che il resto non SIA una particella: non deve finirci.
    """
    for numero in range(12):
        conn.execute(
            "INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
            "VALUES (?,?,'Vario','Tommaso',1)", (920 + numero, f"Q{920+numero}")
        )
    assert rilettura._senza_il_cognome(
        {"cognome": "X"}, "nome", "Giuseppe di Tommaso", conn) == "Giuseppe di Tommaso"
    # ma un composto normale si sbuccia ancora
    assert rilettura._senza_il_cognome(
        {"cognome": "X"}, "nome", "Giuseppe Tommaso", conn) == "Giuseppe"


# --- la meta' di pagina sbagliata ------------------------------------------

def test_due_letture_prese_dall_atto_accanto_si_scartano(conn):
    """Il caso vero: il matrimonio n. 8 del 1813.

    La rilettura proponeva di rinominare tutti e quattro i testimoni, e i
    quattro nomi nuovi erano — nell'ordine — i testimoni dell'atto n. 7,
    stampati sulla meta' sinistra della stessa scansione. Un atto non
    occupa una scansione intera: il modello ha sempre sotto gli occhi
    pezzi di due atti che non sono il suo.
    """
    atto(conn, 7, immagine="pag.jpg")
    atto(conn, 8, immagine="pag.jpg")
    for numero, (nome, cognome) in enumerate(
            (("Egidio", "Colaneri"), ("Manasse", "Franchella")), start=1):
        conn.execute(
            "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
            "VALUES (?,7,'testimone',?,?,'atto')", (700 + numero, nome, cognome))
    for numero, (nome, cognome) in enumerate(
            (("Vincenzo", "Marianacci"), ("Felice", "Pelliccia")), start=1):
        conn.execute(
            "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
            "VALUES (?,8,'testimone',?,?,'atto')", (800 + numero, nome, cognome))
    for individuo, persona in ((70, 701), (71, 702), (80, 801), (81, 802)):
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
                     "VALUES (?,?,'x','y',1)", (individuo, f"P{persona}"))
        conn.execute("INSERT INTO menzioni (persona, individuo, certa) "
                     "VALUES (?,?,1)", (persona, individuo))
    dato = contesto.per_documento(conn, 8)

    # due letture che vengono tutt'e due dall'atto 7: si buttano insieme
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(801, "nome", "Egidio"), _campo(802, "nome", "Manasse")]},
        "gemini-3.6-flash",
    )
    assert quante == 0
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 0


def test_una_sola_coincidenza_non_basta_a_bocciare(conn):
    """In un paese di poche migliaia di anime gli stessi nomi tornano di
    continuo: bocciare su una somiglianza sola perderebbe letture buone."""
    atto(conn, 7, immagine="pag.jpg")
    atto(conn, 8, immagine="pag.jpg")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
                 "VALUES (701,7,'testimone','Egidio','Colaneri','atto')")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
                 "VALUES (801,8,'testimone','Vincenzo','Marianacci','atto')")
    for individuo, persona in ((70, 701), (80, 801)):
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
                     "VALUES (?,?,'x','y',1)", (individuo, f"P{persona}"))
        conn.execute("INSERT INTO menzioni (persona, individuo, certa) "
                     "VALUES (?,?,1)", (persona, individuo))
    dato = contesto.per_documento(conn, 8)
    quante = rilettura._correggi(
        conn, dato, {"campi": [_campo(801, "nome", "Egidio")]}, "gemini-3.6-flash")
    assert quante == 1


def test_un_valore_gia_presente_nell_atto_non_e_copiato(conn):
    """Se la parola c'e' gia' anche in questo atto, non viene da fuori:
    due fratelli omonimi o un padre e un figlio si chiamano davvero uguale."""
    atto(conn, 7, immagine="pag.jpg")
    atto(conn, 8, immagine="pag.jpg")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
                 "VALUES (701,7,'testimone','Egidio','Colaneri','atto')")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
                 "VALUES (801,8,'padre','Egidio','Marianacci','atto')")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_origine) "
                 "VALUES (802,8,'testimone','Vincenzo','Pelliccia','atto')")
    for individuo, persona in ((70, 701), (80, 801), (81, 802)):
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni) "
                     "VALUES (?,?,'x','y',1)", (individuo, f"P{persona}"))
        conn.execute("INSERT INTO menzioni (persona, individuo, certa) "
                     "VALUES (?,?,1)", (persona, individuo))
    dato = contesto.per_documento(conn, 8)
    quante = rilettura._correggi(
        conn, dato,
        {"campi": [_campo(802, "nome", "Egidio"), _campo(802, "cognome", "Colaneri")]},
        "gemini-3.6-flash",
    )
    # 'Egidio' e' gia' nell'atto 8: non conta come copiato, e resta solo
    # 'Colaneri' come sospetta — una sola, quindi non basta a bocciare
    assert quante >= 1


# --- il veto sulla spiegazione che guarda fuori dall'atto -------------

def test_la_spiegazione_che_cita_l_atto_precedente_e_vetata():
    """Il caso vero: l'atto 12 del 1843, corretto con l'atto 11."""
    assert rilettura._spiegazione_guarda_altrove(
        "L'immagine 1 (in cima a sinistra, fine dell'atto precedente) "
        "mostra chiaramente il nome 'Nicolangela'", "12")


def test_la_spiegazione_che_cita_un_altro_numero_d_atto_e_vetata():
    assert rilettura._spiegazione_guarda_altrove(
        "visibile chiaramente nell'atto n. 2, che inizia in cima alla "
        "seconda immagine", "Uno")


def test_la_coda_del_proprio_atto_sulla_scansione_dopo_non_e_vetata():
    """Un atto non finisce con la scansione: leggerlo fino in fondo e' sano."""
    assert not rilettura._spiegazione_guarda_altrove(
        "nell'immagine 2 (che contiene la parte finale dell'atto n. 1) "
        "e' chiaramente scritto 'uno'", "Uno")


def test_senza_numero_d_atto_il_veto_sui_numeri_non_scatta():
    assert not rilettura._spiegazione_guarda_altrove(
        "l'atto n. 3 mostra un'altra grafia", None)
    # ma le parole restano: non servono numeri per dire 'altrove'
    assert rilettura._spiegazione_guarda_altrove(
        "il nome viene dalla pagina precedente", None)


def test_la_spiegazione_normale_non_e_vetata():
    assert not rilettura._spiegazione_guarda_altrove(
        "La trascrizione riporta 'Cinguanta' con la 'u' invece della 'n', "
        "mentre l'immagine mostra chiaramente 'Cinquanta'", "1")
    assert not rilettura._spiegazione_guarda_altrove("", "1")


def test_ripassa_al_veto_disfa_le_correzioni_lette_fuori_dall_atto(conn):
    """Il veto vale anche all'indietro, su cio' che e' gia' in vigore."""
    from history_maker.ricostruzione import registro

    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno) "
        "VALUES (9, 'r', 'p.jpg', '12', 'nascita', 1843)")
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome) "
        "VALUES (91, 9, 'neonato', 'Maria Celeste', 'Torzi')")
    registro.annota(
        conn, "correzione", (91,),
        "nome: letto «Maria Celeste», sull'immagine «Nicolangela»; L'immagine 1 "
        "(in cima a sinistra, fine dell'atto precedente) mostra 'Nicolangela'.",
        confidenza=0.95, decisore="gemini", modello="m",
        versione_prompt="1.3.0", evidenze=("nome=Nicolangela",))
    conn.commit()

    verdetti = rilettura.ripassa_al_veto(conn, "1.3.0", applica=True)
    assert [v["motivo"] for v in verdetti] == ["letta fuori da questo atto"]
    disfatte = conn.execute(
        "SELECT COUNT(*) FROM decisioni WHERE disfa IS NOT NULL").fetchone()[0]
    assert disfatte == 1


# --- un'assenza non e' una lettura -----------------------------------

def test_un_assenza_non_e_una_lettura():
    for parola in ("nulla", "niente", "nessuno", "illeggibile", "in bianco",
                   "  ", "---", "?", "non presente (e' il neonato)",
                   "non indicata"):
        assert not rilettura._e_una_lettura(parola), parola


def test_una_parola_vera_e_una_lettura():
    for parola in ("Nullo", "Nicolangela", "campagnuola", "trentatre",
                   "Nessi", "Niente Pizzi"):
        assert rilettura._e_una_lettura(parola), parola


def test_ripassa_al_veto_disfa_l_eta_che_dice_di_non_esserci(conn, dato):
    """L'eta' non ha vocabolario: prima nessun controllo la guardava."""
    _correzione(conn, 100, "eta", "non presente (e' il neonato)")
    verdetti = rilettura.ripassa_al_veto(conn, "1.0.0")
    assert [v["motivo"] for v in verdetti] == ["non e' una lettura, e' un'assenza"]


def test_il_ripasso_non_tocca_le_decisioni_di_una_persona(conn, dato):
    """Una statistica non ribalta chi ha aperto la pagina e guardato."""
    from history_maker.ricostruzione import registro
    registro.annota(
        conn, "correzione", (100,),
        "professione: la pagina dice «barilaio», letto a piena risoluzione.",
        confidenza=1.0, decisore="persona", modello="", versione_prompt="",
        evidenze=("professione=barilaio",))
    conn.commit()
    assert rilettura.ripassa_al_veto(conn, "") == []


# --- il cognome incollato nel nome, davanti e dietro -------------------

def test_una_particella_attaccata_e_riconosciuta():
    """Nei registri la particella non e' staccata: «d'Armi» e' un token."""
    for parola in ("di", "De", "della", "d'Armi", "D'Ettorre", "l'Aquila"):
        assert rilettura._e_una_particella(parola), parola
    for parola in ("Lella", "Maria", "Diodato", ""):
        assert not rilettura._e_una_particella(parola), parola


def test_il_cognome_davanti_al_nome_si_sbuccia(conn):
    """Dal 1866 i registri scrivono prima il casato: «Lella Filippo»."""
    for i in range(30):
        conn.execute("INSERT INTO individui (chiave, cognome) VALUES (?,'Lella')",
                     (f"k{i}",))
    conn.execute("INSERT INTO individui (chiave, nome) VALUES ('n','Filippo')")
    conn.commit()
    assert rilettura._senza_un_cognome_del_paese(conn, "Lella Filippo") == "Filippo"


def test_non_si_sbuccia_lasciando_solo_un_casato(conn):
    """«Franchetta d'Armi» sbucciato davanti lasciava «d'Armi»."""
    for i in range(30):
        conn.execute("INSERT INTO individui (chiave, cognome) VALUES (?,'Franchetta')",
                     (f"k{i}",))
    conn.commit()
    assert (rilettura._senza_un_cognome_del_paese(conn, "Franchetta d'Armi")
            == "Franchetta d'Armi")


def test_un_nome_che_e_anche_cognome_non_si_sbuccia(conn):
    """'Salvatore' a Torrebruna e' tutt'e due: 261 casati, 112 battesimi.

    Il limite onesto della regola: dove l'archivio non e' schiacciante
    non decide, e la correzione va guardata da una persona.
    """
    for i in range(20):
        conn.execute("INSERT INTO individui (chiave, cognome) VALUES (?,'Salvatore')",
                     (f"k{i}",))
    for i in range(10):
        conn.execute("INSERT INTO individui (chiave, nome) VALUES (?,'Salvatore')",
                     (f"n{i}",))
    conn.commit()
    assert (rilettura._senza_un_cognome_del_paese(conn, "Salvatore Maria")
            == "Salvatore Maria")
