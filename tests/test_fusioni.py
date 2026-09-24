"""Il controllo sul glossario: correggere una grafia, non fondere due famiglie."""

from history_maker import glossario


def test_una_variante_rara_e_una_lettura_errata():
    """Il caso normale: 'Trojlo' per 'Troilo'. Nessun sospetto."""
    testi = ["Giuseppe Troilo"] * 20 + ["Nicola Trojlo"]
    assert glossario.fusioni_sospette(testi, {"Troilo": ["Trojlo"]}) == []


def test_una_variante_piu_frequente_della_canonica_e_sospetta():
    testi = ["Nicola Lozzi"] * 12 + ["Crescenzo Torzi"] * 4
    (v,) = glossario.fusioni_sospette(testi, {"Torzi": ["Lozzi"]})
    assert v["variante"] == "Lozzi"
    assert v["perche"] == "la variante e' piu' frequente della forma canonica"


def test_due_forme_nello_stesso_atto_sono_due_famiglie():
    """Anche se la variante e' rara: chi sta nella stessa riga e' un altro."""
    insieme = "Crescenzo Torzi dichiarante Ferdinando Lozzi Testimone"
    testi = ["Angela Torzi"] * 30 + [insieme] * 3
    (v,) = glossario.fusioni_sospette(testi, {"Torzi": ["Lozzi"]})
    assert v["atti_insieme"] == 3
    assert v["perche"] == "compaiono insieme nello stesso atto"


def test_una_coesistenza_sola_non_basta():
    testi = ["Angela Torzi"] * 30 + ["Torzi e Lozzi"]
    assert glossario.fusioni_sospette(testi, {"Torzi": ["Lozzi"]}) == []


def test_una_variante_mai_letta_non_fonde_niente():
    assert glossario.fusioni_sospette(["Angela Torzi"], {"Torzi": ["Jorzo"]}) == []


def test_il_confine_di_parola_non_prende_i_pezzi_di_altre_parole():
    """'Toro' non deve trovarsi dentro 'Torosanto'."""
    testi = ["Michele Torosanto"] * 40 + ["Angela Torzi"]
    assert glossario.fusioni_sospette(testi, {"Torzi": ["Toro"]}) == []
