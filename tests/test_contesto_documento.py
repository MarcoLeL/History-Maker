"""Il contesto costruito attorno a un atto, invece che attorno a un dubbio.

Il fascicolo dell'arbitro ha per unita' il **caso da decidere**. Per
tornare all'immagine con qualcosa in mano l'unita' e' un'altra —
**l'atto** — e la differenza non e' di forma: cambia cosa ci finisce
dentro e in che ordine.

Questi test difendono le due scelte da cui dipende se il contesto serve
o confonde: che sia dichiarato **ipotesi**, e che in cima ci vada quello
che la pagina puo' davvero sciogliere.
"""

import json
import sqlite3

import pytest

from history_maker import dataset
from history_maker.ricostruzione import contesto, modello


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(modello.SCHEMA_SQL)
    return c


def atto(conn, id, tipo="morte", anno=1831, numero="18", testo=None):
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, "
        "testo_integrale, affidabilita) VALUES (?,?,?,?,?,?,?,?)",
        (id, "r", f"{id:04d}.jpg", numero, tipo, anno, testo, "alta"),
    )


def menzione(conn, id, atto_id, ruolo, nome, cognome, individuo,
             eta=None, cognome_letto=None, cognome_origine="atto"):
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome, cognome_letto, "
        "cognome_origine, eta) VALUES (?,?,?,?,?,?,?,?)",
        (id, atto_id, ruolo, nome, cognome, cognome_letto, cognome_origine, eta),
    )
    conn.execute(
        "INSERT INTO menzioni (persona, individuo, certa) VALUES (?,?,1)",
        (id, individuo),
    )


def individuo(conn, id, nome, cognome, anno_nascita=None, menzioni=1,
              varianti_cognome=None, stato="confermato", confidenza=1.0):
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, anno_nascita, "
        "menzioni, varianti_cognome, stato, confidenza, fondata_su) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (id, f"P{id}", nome, cognome, anno_nascita, menzioni, varianti_cognome,
         stato, confidenza, "una sola menzione"),
    )


def anomalia(conn, tipo, individui, descrizione, confidenza=0.5, atti=(),
             campo="", impatto=1, stato="aperta", gravita="media"):
    conn.execute(
        "INSERT INTO anomalie (tipo, individui, atti, campo, descrizione, "
        "spiegazioni, confidenza, impatto, gravita, priorita, stato) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tipo, json.dumps(list(individui)), json.dumps(list(atti)), campo,
         descrizione, "", confidenza, impatto, gravita, impatto * 1.0, stato),
    )


@pytest.fixture
def marzio(conn):
    """Il caso vero: un cognome che e' il nome della moglie.

    L'atto di morte n. 18 del 1831 dice 'marito d'Andria Motta', e chi ha
    trascritto l'ha attaccato a lui come cognome. Marzio e' un Lella, e
    compare una volta sola in tutto il secolo.
    """
    atto(conn, 1, testo="e' morto Marzio d'Andria Motta d'anni trentasei")
    individuo(conn, 10, "Marzio", "Lella", 1795, varianti_cognome="d'Andria Motta")
    individuo(conn, 20, "Filippo", "Lella", 1758, menzioni=31)
    individuo(conn, 30, "Margherita", "Rossi", 1761, menzioni=22)
    individuo(conn, 40, "Egidio", "Pelliccia", menzioni=193)
    menzione(conn, 100, 1, "defunto", "Marzio", "d'Andria Motta", 10, eta="trentasei")
    menzione(conn, 101, 1, "padre", "Filippo", "Lella", 20)
    menzione(conn, 102, 1, "madre", "Margherita", "Rossi", 30)
    menzione(conn, 103, 1, "ufficiale", "Egidio", "Pelliccia", 40)
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (10,20,'padre',1)")
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (10,30,'madre',1)")
    return conn


def test_il_contesto_dice_di_essere_un_ipotesi(marzio):
    """La riga senza cui tutto il resto e' controproducente.

    Un modello a cui si da' il grafo come dato di fatto rilegge
    l'immagine finche' non torna, e produce una conferma che non vale
    niente.
    """
    dato = contesto.per_documento(marzio, 1)
    nota = dato["contesto_genealogico"]["_nota"]
    assert "IPOTESI" in nota
    assert "ha ragione l'immagine" in nota


def test_la_lettura_originale_resta_accanto_a_quella_corretta(marzio):
    """Il grezzo non si perde: e' l'invariante di tutto il progetto."""
    dato = contesto.per_documento(marzio, 1)
    riga = [m for m in dato["trascrizione_precedente"]["menzioni"]
            if m["ruolo"] == "defunto"][0]
    assert riga["cognome"] == "d'Andria Motta"

    scheda = [p for p in dato["contesto_genealogico"]["persone"]
              if p["individuo"] == 10][0]
    assert scheda["cognome"] == "Lella"
    assert "d'Andria Motta" in scheda["letture_del_cognome"]


def test_i_fratelli_entrano_nel_contesto(marzio):
    """Sono la prova migliore che il contesto possa portare."""
    individuo(marzio, 50, "Rebecca", "Lella", 1794)
    marzio.execute(
        "INSERT INTO legami (figlio, genitore, tipo, atto) VALUES (50,20,'padre',1)"
    )
    dato = contesto.per_documento(marzio, 1)
    scheda = [p for p in dato["contesto_genealogico"]["persone"]
              if p["individuo"] == 10][0]
    assert [f["individuo"] for f in scheda["fratelli"]] == [50]


def test_una_persona_nominata_due_volte_resta_una(conn):
    """Il padre che e' anche il dichiarante non e' due persone.

    Presentarlo due volte suggerirebbe da solo la risposta sbagliata a
    chiunque legga il contesto.
    """
    atto(conn, 1, tipo="nascita")
    individuo(conn, 10, "Giuseppe", "Lella")
    menzione(conn, 100, 1, "padre", "Giuseppe", "Lella", 10)
    menzione(conn, 101, 1, "dichiarante", "Giuseppe", "Lella", 10)

    persone = contesto.per_documento(conn, 1)["contesto_genealogico"]["persone"]
    assert len(persone) == 1
    assert "padre" in persone[0]["ruolo_nell_atto"]
    assert "dichiarante" in persone[0]["ruolo_nell_atto"]


def test_vengono_prima_i_dubbi_che_la_pagina_puo_sciogliere(marzio):
    """L'ordinamento che decide se la sezione serve.

    Un dubbio sull'identita' non si risolve guardando l'immagine, per
    quanto il calcolo ci creda; un dubbio sulla lettura si', e proprio
    quando il calcolo ci crede poco.
    """
    anomalia(marzio, "SURNAME_ANOMALY", [10],
             "porta 'dandriamota' (1 volta) ma suo padre porta 'lela' (1657)",
             confidenza=0.65, atti=[1], campo="cognome", stato="corretta")
    # Il sindaco firma milleduecento atti: ogni dubbio sulla sua identita'
    # sposta duecento persone, e con l'ordinamento per priorita' vinceva.
    anomalia(marzio, "DUPLICATE_PERSON", [40, 999],
             "Egidio Pelliccia e Egidio Pelliccia potrebbero essere la stessa (3%)",
             confidenza=0.03, impatto=194, gravita="alta")

    dato = contesto.per_documento(marzio, 1)
    assert dato["possible_errors"][0]["codice"] == "surname_variant"
    assert dato["possible_errors"][0]["la_pagina_puo_rispondere"] is True
    # Il duplicato al 3% non e' un sospetto: e' il calcolo che dice di no.
    assert all(e["codice"] != "duplicate_person" for e in dato["possible_errors"])


def test_una_correzione_gia_applicata_resta_una_domanda(marzio):
    """Lo stato 'corretta' non chiude la questione per l'immagine.

    Il cognome raddrizzato sul padre e' un'inferenza dell'algoritmo, ed e'
    esattamente cio' che vale la pena mettere davanti all'originale.
    Escluderlo perche' lo stato non e' 'aperta' toglieva dal contesto il
    caso migliore che ci fosse.
    """
    anomalia(marzio, "SURNAME_ANOMALY", [10], "il cognome viene dal padre",
             confidenza=0.65, atti=[1], campo="cognome", stato="corretta")
    dato = contesto.per_documento(marzio, 1)
    errore = dato["possible_errors"][0]
    assert "gia_corretto_dall_algoritmo" in errore
    assert dato["domande_aperte"]
    assert "il cognome" in dato["domande_aperte"][0]


def test_un_dubbio_di_identita_non_diventa_una_domanda_all_immagine(marzio):
    """La pagina dice cosa c'e' scritto, non chi era.

    Chiedere all'immagine se due schede sono la stessa persona vuol dire
    chiedere al modello di indovinare invece che di leggere.
    """
    anomalia(marzio, "DUPLICATE_PERSON", [10, 999],
             "potrebbero essere la stessa persona", confidenza=0.9, atti=[1])
    dato = contesto.per_documento(marzio, 1)
    assert any(e["codice"] == "duplicate_person" for e in dato["possible_errors"])
    assert dato["domande_aperte"] == []


def test_gli_attributi_sono_una_serie_non_un_campo(conn):
    """'bovaro nel 1850, contadino nel 1875' e' una biografia.

    'bovaro | contadino' e' una contraddizione apparente, ed e' la
    differenza fra far ragionare un modello e confonderlo.
    """
    atto(conn, 1)
    individuo(conn, 10, "Domenico", "Lella", 1820)
    menzione(conn, 100, 1, "defunto", "Domenico", "Lella", 10)
    for anno, mestiere in ((1850, "bovaro"), (1861, "contadino"), (1875, "bracciante")):
        conn.execute(
            "INSERT INTO fatti (individuo, tipo, grezzo, anno) VALUES (?,?,?,?)",
            (10, "professione", mestiere, anno),
        )
    scheda = contesto.per_documento(conn, 1)["contesto_genealogico"]["persone"][0]
    assert scheda["mestieri"] == ["1850: bovaro", "1861: contadino", "1875: bracciante"]


def test_tutti_i_coniugi_non_il_primo(conn):
    """Sei coniugi sono un dato, non una lista da troncare.

    Se la scheda ne ha inghiottiti sei, quello e' proprio cio' che si
    vuole vedere: tagliare a uno nasconderebbe il fatto su cui si sta
    chiedendo di ragionare.
    """
    atto(conn, 1)
    individuo(conn, 10, "Angela", "Lella", 1800)
    menzione(conn, 100, 1, "defunto", "Angela", "Lella", 10)
    for numero in range(2, 8):
        individuo(conn, numero, f"Marito{numero}", "Rossi")
        conn.execute(
            "INSERT INTO unioni (marito, moglie, anno, origine) VALUES (?,?,?,?)",
            (numero, 10, 1820 + numero, "matrimonio"),
        )
    scheda = contesto.per_documento(conn, 1)["contesto_genealogico"]["persone"][0]
    assert len(scheda["coniugi"]) == 6


def test_un_atto_che_non_esiste_lo_dice(conn):
    with pytest.raises(ValueError, match="non esiste"):
        contesto.per_documento(conn, 999)
