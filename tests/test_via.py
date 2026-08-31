"""La contrada, separata dal comune.

La formula degli atti nomina i due nello stesso respiro — "in Torrebruna,
strada della Trascinella" — e i motori li distribuiscono diversamente:
Gemini mette il comune in ``persona.residenza`` e la contrada in
``atto.luogo``, Claude infilava entrambi nella residenza. Estrarre la
contrada qui, invece di chiederla separata nel prompt, e' cio' che rende
la colonna indipendente da chi ha letto.

Il modo di sbagliare che questi test difendono e' uno solo: mettere in
quella colonna qualcosa che una contrada non e'. Serve a raggruppare le
famiglie per vicinato, e un comune forestiero o una formula di
cancelleria la renderebbero inservibile proprio per quello.
"""

import pytest

from history_maker.normalizza import separa_comune, via_da

COMUNE = "Torrebruna"


@pytest.mark.parametrize(
    "luogo, atteso",
    [
        # Il caso piu' comune di tutti: comune, virgola, contrada.
        ("Torrebruna, strada della Trascinella", "strada della Trascinella"),
        ("Torrebruna, a Capo la Rua di Nuorro", "a Capo la Rua di Nuorro"),
        ("Torrebruna, Coste delle Pecchie", "Coste delle Pecchie"),
        # Senza virgola, che nei registri capita altrettanto spesso.
        ("Torrebruna sopra la Torre", "sopra la Torre"),
        # La sola contrada, senza premettere il comune.
        ("strada di Portamurella", "strada di Portamurella"),
        # 'rua' annuncia il toponimo quanto 'strada': la contrada si
        # riconosce anche quando l'atto non premette ne' comune ne' via.
        ("Rua di Nuorro", "Rua di Nuorro"),
        ("contrada delle Coste", "contrada delle Coste"),
        # Maiuscole e accenti non devono contare.
        ("torrebruna , Rua di Nuorro", "Rua di Nuorro"),
    ],
)
def test_la_contrada_si_estrae(luogo, atteso):
    assert via_da(luogo, COMUNE) == atteso


@pytest.mark.parametrize(
    "luogo",
    [
        "Torrebruna",           # il solo comune non e' una contrada
        None,
        "",
        "   ",
    ],
)
def test_senza_contrada_il_campo_resta_vuoto(luogo):
    """Un campo vuoto dichiarato, non una stringa vuota che finge d'essere un dato."""
    assert via_da(luogo, COMUNE) is None


@pytest.mark.parametrize(
    "luogo",
    ["Castiglione Messer Marino", "Napoli", "Castelguidone", "Chieti", "Fraine"],
)
def test_il_comune_di_un_forestiero_non_e_una_via(luogo):
    """Una sposa di Castiglione ha una residenza, non una contrada di Torrebruna.

    E' il caso che rovinerebbe la colonna: mescolare comuni e contrade
    renderebbe impossibile la domanda per cui la colonna esiste.
    """
    assert via_da(luogo, COMUNE) is None


@pytest.mark.parametrize(
    "luogo",
    [
        "Torrebruna, in casa di sua abitazione",
        "Torrebruna, in sua abitazione",
        "Torrebruna, in casa di propria abitazione",
    ],
)
def test_la_formula_della_casa_non_e_una_contrada(luogo):
    """«in casa di sua abitazione» dice che l'evento e' avvenuto in casa.

    E' un'informazione vera, ma non un luogo del paese: in una colonna
    che serve al vicinato sarebbe rumore.
    """
    assert via_da(luogo, COMUNE) is None


def test_il_comune_si_riconosce_solo_in_testa():
    """Un 'Torrebruna' in coda fa parte del toponimo, e toglierlo lo storpia."""
    assert separa_comune("strada che porta a Torrebruna", COMUNE) == (
        None,
        "strada che porta a Torrebruna",
    )


def test_separa_comune_restituisce_entrambe_le_parti():
    assert separa_comune("Torrebruna, strada della Chiesa", COMUNE) == (
        "Torrebruna",
        "strada della Chiesa",
    )
    assert separa_comune("Torrebruna", COMUNE) == ("Torrebruna", None)


def test_un_comune_di_piu_parole():
    """Il confronto conta le parole del comune, non si ferma alla prima."""
    assert via_da("Castiglione Messer Marino, strada del Colle", "Castiglione Messer Marino") == (
        "strada del Colle"
    )
