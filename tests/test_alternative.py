"""Le seconde letture che il modello annota e che noi buttavamo via.

Il caso che ha originato il modulo: 'Tommolilli' e' 'Femminilli' mal
letto, ma dista 0,67 — sotto qualunque soglia sensata, e abbassarla
unirebbe meta' paese. La nota del modello pero' diceva
'(Tommolilli / Iemminilli)', e 'Iemminilli' dista 0,90 da 'Femminilli':
la catena arriva a destinazione passando per l'alternativa.
"""

from collections import Counter

import pytest

from history_maker.normalizza import alternative_dalle_note, correzioni_dalle_alternative


@pytest.mark.parametrize(
    "nota, attese",
    [
        ("cognome di lettura incerta (Tommolilli / Iemminilli)", ["Tommolilli", "Iemminilli"]),
        ("cognome di lettura incerta (Iorzo[?] / Iorio)", ["Iorzo", "Iorio"]),
        ("Cognome molto incerto: 'Pope', 'Popa' o simile", ["Pope", "Popa"]),
        ("grafia del cognome incerta (Pellicci / Pelliccia)", ["Pellicci", "Pelliccia"]),
    ],
)
def test_le_alternative_si_estraggono_dalla_nota(nota, attese):
    assert alternative_dalle_note(nota) == attese


@pytest.mark.parametrize("nota", [None, "", "padre dello sposo", "indicato come 'quondam'"])
def test_una_nota_senza_alternative_non_ne_inventa(nota):
    assert alternative_dalle_note(nota) == []


def test_il_caso_tommolilli():
    """La prova del nove: una correzione irraggiungibile per somiglianza."""
    attestate = Counter({"Femminilli": 7, "Pelliccia": 55})
    corrette = correzioni_dalle_alternative(
        [("Tommolilli", "cognome di lettura incerta (Tommolilli / Iemminilli)")],
        attestate,
    )
    assert corrette == {"Tommolilli": "Femminilli"}


def test_una_forma_gia_attestata_non_si_tocca():
    """Se il cognome letto e' gia' fra le forme sicure, non c'e' dubbio
    da sciogliere."""
    attestate = Counter({"Pelliccia": 55})
    corrette = correzioni_dalle_alternative(
        [("Pelliccia", "grafia incerta (Pelliccia / Pellicci)")], attestate
    )
    assert corrette == {}


def test_un_alternativa_che_non_esiste_nel_corpus_non_corregge():
    """'Pope' / 'Popa': nessuna delle due e' la forma giusta, e il corpus
    non puo' saperlo. Restera' alla rilettura dell'immagine."""
    attestate = Counter({"Pepe": 6})
    corrette = correzioni_dalle_alternative(
        [("Pope", "Cognome molto incerto: 'Pope', 'Popa' o simile")], attestate
    )
    assert corrette == {}


def test_serve_una_forma_davvero_attestata():
    """Un'alternativa che combacia con una forma vista una volta sola non
    e' evidenza: due rarita' non si correggono a vicenda."""
    attestate = Counter({"Femminilli": 1})
    corrette = correzioni_dalle_alternative(
        [("Tommolilli", "incerta (Tommolilli / Iemminilli)")], attestate
    )
    assert corrette == {}
