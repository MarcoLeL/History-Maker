"""La casella del genitore che l'atto lascia in bianco non e' una persona.

Gli atti lo dicono: «e da padre incerto» (1813 nati n. 32), «figlia delli
quondam» e poi piu' niente (1812 morti n. 26). Contare quelle righe apriva
una scheda senza nome per ognuna — duecentosedici righe, duecentottantasei
schede vuote nell'albero.
"""

from history_maker import menzioni as mod
from history_maker.ricostruzione import lettura


def _menzione(ruolo, nome=None, cognome=None):
    return mod.Menzione(
        id=1, atto=1, tipo_atto="nascita", anno=1813, data=None, ruolo=ruolo,
        nome=nome, cognome=cognome, nome_letto=nome, cognome_letto=cognome,
        cognome_origine=None, incerto=False, eta_letta=None, professione=None,
        residenza=None, via=None, stato_vitale=None, note=None, immagine=None, eta=None)


def test_il_padre_lasciato_in_bianco_esce_dalla_famiglia():
    m = _menzione("padre")
    lettura.Interprete()(m)
    assert m.ruolo == "altro"


def test_la_madre_lasciata_in_bianco_esce_dalla_famiglia():
    m = _menzione("madre")
    lettura.Interprete()(m)
    assert m.ruolo == "altro"


def test_al_genitore_basta_il_cognome_per_restare():
    """«e di ... Lella» nella morte del 1841 n. 14: il casato c'e', la persona pure."""
    m = _menzione("madre", cognome="Lella")
    lettura.Interprete()(m)
    assert m.ruolo == "madre"


def test_il_neonato_senza_nome_resta_al_suo_posto():
    """Il nato morto non ha nome, ma e' il soggetto dell'atto: toglierlo perde la famiglia."""
    m = _menzione("neonato")
    lettura.Interprete()(m)
    assert m.ruolo == "neonato"


def test_la_madre_dichiarata_ignota_resta_al_suo_posto():
    """«Dalla sua unione con donna non maritata» e' un dato, non un vuoto.

    Nella nascita del 1891 n. 19 Enrico Pelliccia dichiara un figlio
    naturale: togliendo quella riga si perdeva il segno del figlio
    naturale, e il bambino restava senza il padre che lo riconosce.
    """
    m = _menzione("madre")
    m.ignota = True
    m.note = "dalla sua unione con donna non maritata"
    lettura.Interprete()(m)
    assert m.ruolo == "madre"
