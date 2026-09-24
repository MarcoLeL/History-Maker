"""L'unita' dell'eta': quella che distingue un lattante da un ragazzo."""

from history_maker.dataset import _eta_con_unita

ATTO = ("i quali han dichiarato che e' morta nella sua casa Maria Ottaviano "
        "di mesi otto, nata in Torrebruna, figlia di Nicola. Per esecuzione "
        "della legge abbiamo autorizzato il Parroco a dargli sepoltura dopo "
        "lo spazio di ore ventiquattro.")


def test_l_unita_torna_al_suo_posto():
    assert _eta_con_unita(ATTO, "otto") == "mesi otto"


def test_la_formula_della_sepoltura_non_e_l_eta_di_nessuno():
    """«dopo lo spazio di ore ventiquattro» sta in ogni atto di morte."""
    assert _eta_con_unita(ATTO, "ventiquattro") == "ventiquattro"


def test_un_numero_diverso_non_si_tocca():
    """Se l'atto dice otto mesi e il campo dice dieci, non sono la stessa eta'."""
    assert _eta_con_unita(ATTO, "dieci") == "dieci"


def test_un_eta_che_ha_gia_l_unita_resta_com_e():
    assert _eta_con_unita(ATTO, "mesi otto") == "mesi otto"
    assert _eta_con_unita(ATTO, "di mesi otto") == "di mesi otto"


def test_senza_testo_o_senza_eta_non_succede_niente():
    assert _eta_con_unita(None, "otto") == "otto"
    assert _eta_con_unita(ATTO, None) is None
    assert _eta_con_unita(ATTO, "") == ""


def test_i_giorni_valgono_quanto_i_mesi():
    testo = "e' morta Carmela Troilo di giorni tre, nata in Torrebruna"
    assert _eta_con_unita(testo, "tre") == "giorni tre"
