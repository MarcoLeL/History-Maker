"""Il raggruppamento delle varianti sull'intero corpus.

Tutti i casi vengono dalle pagine vere del 1809 di Torrebruna. Il criterio
non e' la fascia di frequenza ma la somiglianza reciproca, e la ragione e'
nei dati: le quattro forme di 'Cicchillitto' contano 6, 5, 4 e 3
occorrenze, quindi nessuna e' "rara" (<=2) ne' "frequente" (>=10). La
regola raro-contro-frequente, sul gruppo piu' numeroso del corpus, non
scatterebbe mai.
"""

from collections import Counter

import pytest

from history_maker.normalizza import raggruppa_varianti


def correzioni(frequenze: dict[str, int]) -> dict[str, str]:
    return raggruppa_varianti(Counter(frequenze))[0]


def proposte(frequenze: dict[str, int]) -> dict[str, str]:
    return {p.letto: p.proposto for p in raggruppa_varianti(Counter(frequenze))[1]}


# --- famiglie diverse che devono restare diverse ---------------------------

def test_lella_e_colella_restano_separati():
    """Confermato da chi conosce il paese: sono due famiglie diverse.

    Stanno a somiglianza 0,71, sotto la soglia di gruppo. E' il caso che
    fissa il limite inferiore: abbassare la soglia le unirebbe.
    """
    frequenze = {"Lella": 8, "Colella": 6}
    assert correzioni(frequenze) == {}
    assert proposte(frequenze) == {}


def test_cognomi_estranei_non_si_toccano():
    frequenze = {"Nicodemo": 14, "Desiderio": 10, "Lattanzio": 9}
    assert correzioni(frequenze) == {}
    assert proposte(frequenze) == {}


# --- quello che si applica da solo -----------------------------------------

def test_lettura_sbagliata_accanto_a_forma_dominante():
    """'Tr' letto per 'Fr' e' confusione di mano, e 15 contro 2 decide."""
    assert correzioni({"Franchella": 15, "Trinchella": 2}) == {"Trinchella": "Franchella"}


def test_sola_punteggiatura_non_e_una_decisione():
    """'Dettorre' e \"d'Ettorre\" sono la stessa stringa senza apostrofo.

    Qui la dominanza non deve entrarci: non c'e' nessuna lettura da
    scegliere, solo una grafia da uniformare.
    """
    assert correzioni({"d'Ettorre": 5, "Dettorre": 2}) == {"Dettorre": "d'Ettorre"}


def test_doppie_instabili_si_uniformano():
    assert correzioni({"Pelliccia": 22, "Pellicia": 2}) == {"Pellicia": "Pelliccia"}


# --- quello che va sottoposto invece che deciso ----------------------------

def test_due_forme_alla_pari_non_si_correggono_a_vicenda():
    """Nessuna evidenza sulla direzione: e' una proposta, non una
    correzione.

    Senza questa guardia 'Pinti' (1) e 'Pinnti' (1) venivano unite
    d'ufficio, e a scegliere la forma buona era l'ordine alfabetico.
    """
    frequenze = {"Pinti": 1, "Pinnti": 1}
    assert correzioni(frequenze) == {}
    assert "Pinti" in proposte(frequenze) or "Pinnti" in proposte(frequenze)


def test_alternanza_o_i_resta_una_decisione_umana():
    """L'alternanza -o/-i dei cognomi meridionali puo' essere una lettura
    sbagliata o due rami della stessa famiglia: non la decide una
    statistica."""
    frequenze = {"Cicchilitti": 9, "Cicchillitto": 6, "Cicchilitto": 4}
    assert correzioni(frequenze) == {}
    assert proposte(frequenze) == {
        "Cicchillitto": "Cicchilitti",
        "Cicchilitto": "Cicchilitti",
    }


def test_le_proposte_sono_ordinate_per_ricorrenza():
    """Una forma vista una volta sola non merita il tempo di nessuno;
    una vista sei volte si'."""
    _, prop = raggruppa_varianti(
        Counter({"Pizzi": 11, "Pozzi": 1, "Cicchilitti": 9, "Cicchillitto": 6})
    )
    assert [p.letto for p in prop] == ["Cicchillitto", "Pozzi"]


def test_la_proposta_spiega_perche(capsys):
    _, prop = raggruppa_varianti(Counter({"Mancini": 2, "Mancino": 2}))
    assert len(prop) == 1
    motivo = prop[0].motivo
    assert "2 volte" in motivo and "somiglianza" in motivo


# --- forma del risultato ---------------------------------------------------

@pytest.mark.parametrize("frequenze", [{}, {"Solo": 3}])
def test_niente_da_raggruppare(frequenze):
    assert raggruppa_varianti(Counter(frequenze)) == ({}, [])


def test_una_variante_non_e_mai_anche_una_proposta():
    """Le due liste sono disgiunte: un caso o si decide o si sottopone."""
    frequenze = {
        "Pelliccia": 22, "Pellicia": 2, "Cicchilitti": 9, "Cicchillitto": 6,
        "Lella": 8, "Lelia": 2,
    }
    corr, prop = raggruppa_varianti(Counter(frequenze))
    assert not (set(corr) & {p.letto for p in prop})
