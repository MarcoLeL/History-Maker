"""La tavola alfabetica non e' una pagina di atti."""

from history_maker import dataset


def test_una_riga_di_tavola_non_e_un_atto():
    """Ha un numero d'ordine e due nomi: somiglia a un atto, non lo e'."""
    assert dataset._e_una_tavola({"testo_integrale":
        "TAVOLA annuale alfabetica de' matrimoni celebrati in questo Comune "
        "nell'anno 1813.\nNum. d'ordine: 3.\nNomi e Cognomi degli sposi: "
        "Rosario Antonio Conti, e Rosa Pelliccia"})
    assert dataset._e_una_tavola({"testo_integrale": "Indice annuale dei morti"})
    assert dataset._e_una_tavola({"testo_integrale": "Tavola de' nati"})


def test_un_atto_vero_non_e_una_tavola():
    assert not dataset._e_una_tavola({"testo_integrale":
        "L'anno mille ottocento tredici, avanti di noi Domenicantonio Pizzi "
        "Sindaco ed uffiziale dello stato civile del Comune di Torrebruna"})
    assert not dataset._e_una_tavola({"testo_integrale": None})
    assert not dataset._e_una_tavola({})


def test_una_tavola_nominata_di_sfuggita_non_scarta_l_atto():
    """La parola da sola non basta: serve la formula dell'indice."""
    assert not dataset._e_una_tavola({"testo_integrale":
        "posero il documento sulla tavola della casa comunale"})
