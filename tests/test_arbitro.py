"""Che l'arbitro avanzi nella coda, invece di restare in testa.

Il difetto, trovato eseguendo davvero un ciclo di piu' esecuzioni: senza
un modo di ricordare cosa e' gia' stato sottoposto, ogni esecuzione
richiede di nuovo gli stessi casi in cima alla coda — una 'conferma' che
non cambia la scheda non la toglie dalla priorita' — e non avanza mai
verso quelli sotto. E' lo stesso difetto, nello stesso punto, gia' trovato
in ``rilettura.casi``.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from history_maker.ricostruzione import arbitro, modello


def anomalia(individui, tipo="DUPLICATE_PERSON", priorita=1.0):
    return modello.Anomalia(
        tipo=tipo, individui=tuple(individui), descrizione="",
        confidenza=0.5, impatto=1,
    )


class Esito:
    """Un finto esito: 'anomalie.coda' guarda solo 'esito.anomalie'."""

    def __init__(self, anomalie):
        self.anomalie = anomalie


def test_senza_esclusi_prende_semplicemente_la_testa_della_coda():
    esito = Esito([anomalia([1, 2]), anomalia([3, 4])])
    trovati = arbitro.casi(esito, 10)
    assert len(trovati) == 2


def test_un_caso_escluso_non_si_riseleziona():
    esito = Esito([anomalia([1, 2]), anomalia([3, 4])])
    esclusi = {frozenset([1, 2])}
    trovati = arbitro.casi(esito, 10, esclusi=esclusi)
    assert [a.individui for a in trovati] == [(3, 4)]


def test_senza_lo_scarto_lo_stesso_caso_resta_in_cima_per_sempre():
    """Il bug in una riga: senza 'esclusi' il giro successivo richiede la
    stessa testa della coda, e i casi sotto non si vedono mai."""
    esito = Esito([anomalia([1, 2], priorita=99), anomalia([3, 4], priorita=1)])
    primo_giro = arbitro.casi(esito, 1)
    assert [a.individui for a in primo_giro] == [(1, 2)]
    secondo_giro_senza_scarto = arbitro.casi(esito, 1)
    assert secondo_giro_senza_scarto == primo_giro   # bloccato in testa

    secondo_giro_con_scarto = arbitro.casi(
        esito, 1, esclusi={frozenset(a.individui) for a in primo_giro}
    )
    assert [a.individui for a in secondo_giro_con_scarto] == [(3, 4)]  # avanza


# --- gia_arbitrati -----------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(modello.SCHEMA_SQL)
    return c


def _decisione(conn, entita, decisore="claude"):
    conn.execute(
        "INSERT INTO decisioni (quando, azione, entita, decisore) "
        "VALUES ('2026-01-01T00:00:00Z', 'conferma', ?, ?)",
        (json.dumps(list(entita)), decisore),
    )


def test_gia_arbitrati_legge_le_decisioni_non_algoritmiche(conn):
    _decisione(conn, [5, 9])
    _decisione(conn, [1, 2, 3])
    esclusi = arbitro.gia_arbitrati(conn)
    assert frozenset([5, 9]) in esclusi
    assert frozenset([1, 2, 3]) in esclusi


def test_gia_arbitrati_ignora_le_decisioni_dell_algoritmo(conn):
    """Quelle si rifanno da sole a ogni esecuzione: escluderle vorrebbe
    dire non richiedere mai piu' un caso che l'algoritmo, non un
    ragionamento, aveva deciso — e i due non sono la stessa cosa."""
    _decisione(conn, [5, 9], decisore="algoritmo")
    assert arbitro.gia_arbitrati(conn) == set()


def test_gia_arbitrati_regge_senza_la_tabella_decisioni():
    c = sqlite3.connect(":memory:")   # database vuoto, prima di ogni schema
    assert arbitro.gia_arbitrati(c) == set()


def test_gia_arbitrati_dentro_casi_fa_avanzare_il_ciclo(conn):
    """Il caso d'uso vero: la pipeline chiede a gia_arbitrati cosa
    escludere, senza dover sapere niente di come sono fatte le
    decisioni."""
    _decisione(conn, [1, 2])
    esito = Esito([anomalia([1, 2], priorita=99), anomalia([3, 4], priorita=1)])
    trovati = arbitro.casi(esito, 10, esclusi=arbitro.gia_arbitrati(conn))
    assert [a.individui for a in trovati] == [(3, 4)]
