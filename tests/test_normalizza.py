"""La separazione fra una lettura e il dubbio che la accompagna.

I casi vengono dalle prime 28 pagine trascritte davvero del 1809: e' li'
che si e' visto che 'Bracchi [?]' e 'Bracchi' finivano contati come due
cognomi diversi.
"""

import pytest

from history_maker.normalizza import separa_incertezza


@pytest.mark.parametrize(
    "letto, atteso",
    [
        # Le forme viste sulle pagine vere del 1809.
        ("Bracchi [?]", "Bracchi"),
        ("Trinchella[?]", "Trinchella"),
        ("Muselle[?]", "Muselle"),
        ("strada Trafficinella [?]", "strada Trafficinella"),
        ("Lama di Nuorro[?]", "Lama di Nuorro"),
        # Altre forme plausibili dello stesso marcatore.
        ("Petrosa (?)", "Petrosa"),
        ("Colangelo?", "Colangelo"),
        ("Muselle [?]?", "Muselle"),
    ],
)
def test_il_marcatore_di_dubbio_esce_dal_valore(letto, atteso):
    valore, incerto = separa_incertezza(letto)
    assert valore == atteso
    assert incerto


@pytest.mark.parametrize("letto", ["Franchella", "Di Nardo", "Cicchillitto"])
def test_una_lettura_sicura_resta_intatta(letto):
    assert separa_incertezza(letto) == (letto, False)


def test_stessa_famiglia_stessa_chiave():
    """Il punto di tutto il modulo: i conteggi devono ricongiungersi."""
    assert separa_incertezza("Bracchi [?]")[0] == separa_incertezza("Bracchi")[0]


def test_solo_dubbio_non_e_un_nome():
    """Un '[?]' da solo e' un dato non letto, non un cognome."""
    assert separa_incertezza("[?]") == (None, True)


def test_valore_assente_resta_assente():
    assert separa_incertezza(None) == (None, False)
    assert separa_incertezza("   ") == (None, False)


def test_il_marcatore_interno_a_un_nome_composto_esce():
    """Caso vero del 1809: il modello marca ogni elemento dubbio.

    Ripulito solo in coda restava 'Petriccio[?] Flajo', cioe' un
    marcatore in mezzo a un cognome — che nei conteggi vale come una
    famiglia a se'.
    """
    assert separa_incertezza("Petriccio[?] Flajo[?]") == ("Petriccio Flajo", True)
    assert separa_incertezza("Sabba [?] Pelliccia") == ("Sabba Pelliccia", True)


def test_la_lacuna_interna_resta_ma_segna_il_dubbio():
    """'B[...]' dice dove sta il buco: si tiene, ma la lettura e' incerta."""
    assert separa_incertezza("B[...]chi") == ("B[...]chi", True)


def test_gli_spazi_attorno_non_contano():
    assert separa_incertezza("  Pelliccia  ") == ("Pelliccia", False)
