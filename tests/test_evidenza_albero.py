"""Cio' che l'applicazione mostra e' una vista, non il database.

Fino a qui ``albero.py`` serviva diciotto colonne di ``individui`` e non
toccava mai ``fatti``, ``anomalie`` o ``decisioni``, ne' le colonne di
confidenza che la fase 6b ha aggiunto proprio a ``individui``. Chi
consultava l'albero vedeva un nome e non aveva modo di sapere se era
confermato o soltanto possibile, su cosa si reggeva, o che su quella
persona pendeva un dubbio in coda.

Questi test verificano che la scheda porti ora quelle evidenze — e che
lo faccia senza sommergere chi guarda: un tetto su quante anomalie e
decisioni mostrare, perche' il sindaco ne ha duecento che lo riguardano
e una scheda che le elenca tutte smette di essere leggibile.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from history_maker import albero, dataset
from history_maker.ricostruzione import modello


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dataset.SCHEMA_SQL)
    c.executescript(modello.SCHEMA_SQL)
    return c


def individuo(conn, id, nome="Marzio", cognome="Lella", confidenza=1.0,
              stato="confermato", fondata_su="una sola menzione",
              varianti_nome=None, varianti_cognome=None, prove=None):
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, cognome, menzioni, "
        "confidenza, stato, fondata_su, varianti_nome, varianti_cognome, prove) "
        "VALUES (?,?,?,?,1,?,?,?,?,?,?)",
        (id, f"P{id}", nome, cognome, confidenza, stato, fondata_su,
         varianti_nome, varianti_cognome, prove),
    )


def anomalia(conn, tipo, individui, descrizione, confidenza=0.6, stato="aperta",
             priorita=1.0):
    conn.execute(
        "INSERT INTO anomalie (tipo, individui, campo, descrizione, spiegazioni, "
        "confidenza, impatto, gravita, priorita, stato) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tipo, json.dumps(list(individui)), "cognome", descrizione, "",
         confidenza, 1, "media", priorita, stato),
    )


def decisione(conn, entita, azione="unione", motivo="stessi genitori",
              decisore="algoritmo", confidenza=0.9):
    conn.execute(
        "INSERT INTO decisioni (quando, azione, entita, motivo, confidenza, "
        "decisore) VALUES ('2026-01-01T00:00:00Z', ?, ?, ?, ?, ?)",
        (azione, json.dumps(list(entita)), motivo, confidenza, decisore),
    )


def test_la_scheda_porta_confidenza_e_stato(conn):
    individuo(conn, 1, confidenza=0.62, stato="possibile")
    dato = albero.scheda(conn, 1)
    assert dato["evidenza"]["confidenza"] == 0.62
    assert dato["evidenza"]["stato"] == "possibile"


def test_le_letture_scartate_si_vedono_accanto_a_quella_scelta(conn):
    """Il grezzo non si perde: e' l'invariante di tutto il progetto.

    Un valore che scompare quando l'interpretazione lo sostituisce non
    e' una correzione: e' una perdita.
    """
    individuo(conn, 1, cognome="Lella", varianti_cognome="d'Andria Motta | Lelli")
    dato = albero.scheda(conn, 1)
    assert dato["evidenza"]["letture_scartate_del_cognome"] == [
        "d'Andria Motta", "Lelli"
    ]


def test_nessuna_variante_e_una_lista_vuota_non_un_errore(conn):
    individuo(conn, 1)
    dato = albero.scheda(conn, 1)
    assert dato["evidenza"]["letture_scartate_del_nome"] == []


def test_le_anomalie_aperte_su_questa_persona_si_vedono(conn):
    individuo(conn, 1)
    individuo(conn, 2)
    anomalia(conn, "DUPLICATE_PERSON", [1, 2], "potrebbero essere la stessa")
    dato = albero.scheda(conn, 1)
    assert len(dato["evidenza"]["anomalie_aperte"]) == 1
    assert dato["evidenza"]["anomalie_aperte"][0]["tipo"] == "DUPLICATE_PERSON"


def test_le_anomalie_chiuse_non_compaiono(conn):
    """Una correzione gia' applicata non e' piu' un dubbio aperto."""
    individuo(conn, 1)
    anomalia(conn, "SURNAME_ANOMALY", [1], "corretta dal padre", stato="corretta")
    dato = albero.scheda(conn, 1)
    assert dato["evidenza"]["anomalie_aperte"] == []


def test_il_filtro_sul_json_non_prende_falsi_positivi(conn):
    """L'individuo 5 non deve comparire per un'anomalia su [15, 25].

    E' la trappola di un filtro SQL fatto con LIKE su una colonna JSON:
    la stringa '5' e' contenuta in '[15, 25]' anche se l'intero 5 non
    c'entra niente.
    """
    individuo(conn, 5)
    individuo(conn, 15)
    individuo(conn, 25)
    anomalia(conn, "MARITAL_ANOMALY", [15, 25], "due coniugi negli stessi anni")
    dato = albero.scheda(conn, 5)
    assert dato["evidenza"]["anomalie_aperte"] == []


def test_le_decisioni_su_questa_persona_si_vedono(conn):
    individuo(conn, 1)
    individuo(conn, 2)
    decisione(conn, [1, 2], motivo="stessa moglie, stessi figli", decisore="claude")
    dato = albero.scheda(conn, 1)
    assert len(dato["evidenza"]["decisioni"]) == 1
    assert dato["evidenza"]["decisioni"][0]["decisore"] == "claude"
    assert dato["evidenza"]["decisioni"][0]["motivo"] == "stessa moglie, stessi figli"


def test_il_filtro_sulle_decisioni_non_prende_falsi_positivi(conn):
    individuo(conn, 5)
    individuo(conn, 15)
    decisione(conn, [15], motivo="non c'entra")
    dato = albero.scheda(conn, 5)
    assert dato["evidenza"]["decisioni"] == []


def test_un_tetto_limita_quante_evidenze_mostrare(conn):
    """Il sindaco ha duecento decisioni che lo riguardano.

    Una scheda che le elenca tutte smette di essere leggibile: il tetto
    non e' un limite sui dati, che restano interrogabili per intero, e'
    un limite su cosa e' utile mostrare in una scheda.
    """
    individuo(conn, 1)
    for numero in range(30):
        individuo(conn, 100 + numero)
        anomalia(conn, "DUPLICATE_PERSON", [1, 100 + numero], f"caso {numero}",
                  priorita=float(numero))
    dato = albero.scheda(conn, 1)
    assert len(dato["evidenza"]["anomalie_aperte"]) == albero.EVIDENZE_MASSIME


def test_una_persona_senza_scheda_non_ha_evidenza(conn):
    assert albero.scheda(conn, 999) is None


def test_su_cosa_si_regge_viene_da_fondata_su(conn):
    individuo(conn, 1, fondata_su="genitori dello stesso figlio; coniuge riconosciuto")
    dato = albero.scheda(conn, 1)
    assert "genitori" in dato["evidenza"]["su_cosa_si_regge"]
