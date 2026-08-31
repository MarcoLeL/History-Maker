"""Il cognome dei neonati, che il formulario da' per implicito.

Un atto di nascita nomina "Maria, figlia di Giuseppe Colella": il cognome
della bambina non e' scritto da nessuna parte. Nel solo 1809 di
Torrebruna sono 39 neonati su 45, quindi lasciarlo vuoto rende il bambino
introvabile proprio nella ricerca per cui il database esiste.
"""

from history_maker.dataset import _deriva_cognome


def persona(ruolo, nome, cognome=None):
    return {
        "ruolo": ruolo,
        "nome": nome,
        "cognome": cognome,
        "cognome_origine": "atto" if cognome else None,
    }


def test_il_neonato_prende_il_cognome_del_padre():
    gente = [
        persona("neonato", "Maria"),
        persona("padre", "Giuseppe", "Colella"),
        persona("madre", "Fortunata", "Moretta"),
    ]
    _deriva_cognome(gente)
    assert gente[0]["cognome"] == "Colella"
    assert gente[0]["cognome_origine"] == "padre"


def test_l_inferenza_non_si_confonde_con_una_lettura():
    """E' la garanzia su cui poggia tutto: chi studia le nascite
    illegittime deve poter separare le due cose con un WHERE."""
    gente = [persona("neonato", "Maria"), persona("padre", "Giuseppe", "Colella")]
    _deriva_cognome(gente)
    assert gente[0]["cognome_origine"] == "padre"
    assert gente[1]["cognome_origine"] == "atto"
    # La lettura resta vuota: sulla pagina quel cognome non c'era.
    assert gente[0].get("cognome_letto") is None


def test_senza_padre_si_ripiega_sulla_madre():
    """Caso vero del 1809: 'Anna Giuseppa', padre senza cognome, madre
    'Rosa Pelliccia'. Sono con ogni probabilita' figli naturali."""
    gente = [
        persona("neonato", "Anna Giuseppa"),
        persona("padre", None),
        persona("madre", "Rosa", "Pelliccia"),
    ]
    _deriva_cognome(gente)
    assert gente[0]["cognome"] == "Pelliccia"
    assert gente[0]["cognome_origine"] == "madre"


def test_senza_genitori_il_cognome_resta_vuoto():
    """Un esposto non ha un cognome da cui derivare: inventarlo sarebbe
    il contrario di quello che serve."""
    gente = [persona("neonato", "Fioravante")]
    _deriva_cognome(gente)
    assert gente[0]["cognome"] is None
    assert gente[0]["cognome_origine"] is None


def test_un_cognome_gia_letto_non_si_sovrascrive():
    gente = [
        persona("neonato", "Maria", "Esposito"),
        persona("padre", "Giuseppe", "Colella"),
    ]
    _deriva_cognome(gente)
    assert gente[0]["cognome"] == "Esposito"
    assert gente[0]["cognome_origine"] == "atto"


def test_solo_i_neonati_ereditano():
    """Un testimone senza cognome non e' figlio di nessuno dei presenti."""
    gente = [
        persona("testimone", "Nicola"),
        persona("padre", "Giuseppe", "Colella"),
    ]
    _deriva_cognome(gente)
    assert gente[0]["cognome"] is None
