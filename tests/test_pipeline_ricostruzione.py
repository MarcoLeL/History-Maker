"""Quando la pipeline si ferma, e perche'.

La decisione se continuare non chiede mai la rete: e' una funzione pura su
una lista di numeri, e questi test la esercitano senza eseguire nessuna
delle quattro fasi vere. E' cio' che la rende verificabile in un decimo
di secondo invece che in ore.
"""

from __future__ import annotations

from history_maker.ricostruzione.pipeline import Giro, convergenza


def giro(
    numero=1, correzioni=0, decisioni=0, pagine_esaminate=None,
    pagine_fallite=0, casi_sottoposti=None, quota_lettura=False,
    quota_arbitro=False,
):
    # Nel codice vero 'pagine_esaminate' comprende sempre le corrette, e
    # 'casi_sottoposti' e' sempre almeno quante le decisioni applicate:
    # qui, se non specificati, si deducono dagli altri due — cosi' i test
    # che parlano solo di 'correzioni'/'decisioni' restano leggibili senza
    # dover ripetere ogni volta il conteggio dell'attivita' sottostante.
    if pagine_esaminate is None:
        pagine_esaminate = correzioni
    if casi_sottoposti is None:
        casi_sottoposti = decisioni
    return Giro(
        numero=numero, schede_prima=100, schede_dopo=100,
        correzioni_lettura=correzioni, pagine_esaminate=pagine_esaminate,
        pagine_fallite=pagine_fallite,
        decisioni_arbitro=decisioni, casi_sottoposti=casi_sottoposti,
        quota_lettura_esaurita=quota_lettura, quota_arbitro_esaurita=quota_arbitro,
    )


def test_una_storia_vuota_non_e_convergenza():
    """Il primo giro deve poter partire."""
    assert convergenza([]) is None


def test_un_giro_produttivo_continua():
    assert convergenza([giro(correzioni=3)]) is None
    assert convergenza([giro(decisioni=1)]) is None
    assert convergenza([giro(correzioni=1, decisioni=1)]) is None


def test_un_giro_senza_niente_e_convergenza():
    """Nessuna pagina esaminata, nessun caso sottoposto: non c'e' piu'
    niente da fare."""
    motivo = convergenza([giro()])
    assert motivo is not None
    assert "convergenza" in motivo


def test_esaminare_senza_applicare_e_comunque_avanzare():
    """Il caso vero che ha reso necessaria questa distinzione.

    Un giro che rilegge venti pagine tutte CONFERMATO, o che sottopone
    venti casi all'arbitro e li vede tornare come 'conferma' — un
    ragionamento scritto, nessuna fusione applicata — non e'
    convergenza: quelle pagine e quei casi non verranno richiesti una
    seconda volta, quindi la coda si e' comunque ristretta. Contare solo
    le correzioni/decisioni **applicate** fermerebbe il ciclo mentre sta
    ancora scoprendo la coda — ed e' esattamente quello che e' successo
    eseguendo la pipeline sul serio: la scheda piu' grossa dell'archivio
    stava alla posizione venticinque, non alla prima, e un criterio che
    guardava solo le decisioni applicate non l'ha mai raggiunta.
    """
    g = giro(correzioni=0, decisioni=0, pagine_esaminate=20, casi_sottoposti=20)
    assert g.produttivo
    assert convergenza([g]) is None


def test_pagine_fallite_contano_come_attivita():
    """Un fallimento non e' 'niente da fare': e' 'ho provato'.

    Senza, un tratto di pagine con l'immagine mancante fermerebbe il
    ciclo per convergenza invece che segnalare il problema vero.
    """
    g = giro(pagine_fallite=5)
    assert g.produttivo


def test_conta_solo_l_ultimo_giro():
    """Un giro produttivo seguito da uno vuoto e' convergenza.

    La storia dei giri precedenti non deve far continuare un ciclo che
    ormai non produce piu' niente: la domanda e' 'cosa ha fatto
    l'ultimo giro', non 'ha mai fatto qualcosa'.
    """
    storia = [giro(numero=1, correzioni=5), giro(numero=2)]
    assert convergenza(storia) is not None

    storia = [giro(numero=1), giro(numero=2, correzioni=2)]
    assert convergenza(storia) is None


def test_il_tetto_dei_giri_ferma_anche_un_ciclo_produttivo():
    """Il tetto e' una difesa, non un giudizio sul fatto che sia finito."""
    from history_maker.ricostruzione import pipeline

    storia = [
        giro(numero=n, correzioni=1) for n in range(1, pipeline.GIRI_MASSIMI + 1)
    ]
    motivo = convergenza(storia)
    assert motivo is not None
    assert "tetto" in motivo


def test_sotto_il_tetto_un_ciclo_produttivo_non_si_ferma():
    from history_maker.ricostruzione import pipeline

    storia = [
        giro(numero=n, correzioni=1) for n in range(1, pipeline.GIRI_MASSIMI)
    ]
    assert convergenza(storia) is None


# --- le proprieta' di Giro --------------------------------------------------

def test_produttivo_conta_le_correzioni_o_le_decisioni():
    assert giro(correzioni=1).produttivo
    assert giro(decisioni=1).produttivo
    assert not giro().produttivo


def test_fermo_per_quota_distingue_i_due_motivi_di_arresto():
    """Fermarsi perche' la quota e' finita non e' la stessa cosa che
    fermarsi perche' non c'e' piu' niente da fare: solo il primo vale la
    pena riprovare domani senza cambiare niente."""
    assert giro(quota_lettura=True).fermo_per_quota
    assert giro(quota_arbitro=True).fermo_per_quota
    assert not giro().fermo_per_quota


def test_un_giro_fermo_per_quota_ma_produttivo_non_e_convergenza():
    """La quota che finisce a meta' giro non vuol dire che sia finito il
    lavoro: 'esegui' si ferma comunque, ma per un motivo diverso da
    'convergenza', e il chiamante deve poterli distinguere."""
    g = giro(correzioni=3, quota_lettura=True)
    assert g.produttivo
    assert g.fermo_per_quota
    assert convergenza([g]) is None    # non e' convergenza: e' quota


# --- il rapporto -------------------------------------------------------

def test_il_rapporto_elenca_ogni_giro():
    from history_maker.ricostruzione.pipeline import rapporto

    storia = [giro(numero=1, correzioni=3), giro(numero=2)]
    testo = rapporto(storia)
    assert "| 1 |" in testo
    assert "| 2 |" in testo
    assert "convergenza" in testo


def test_il_rapporto_su_una_storia_vuota_non_fallisce():
    from history_maker.ricostruzione.pipeline import rapporto

    assert rapporto([])
