"""Il parto che l'atto documenta senza essere un atto di nascita."""

from history_maker import menzioni as mod
from history_maker.ricostruzione import evidenza
from history_maker.ricostruzione.scheda import Scheda


def _menzione(id, tipo_atto, ruolo, anno, eta=None, **extra):
    return mod.Menzione(
        id=id, atto=id, tipo_atto=tipo_atto, anno=anno, data=None, ruolo=ruolo,
        nome="Maria Domenica", cognome="Raffa", nome_letto=None, cognome_letto=None,
        cognome_origine=None, incerto=False, eta_letta=None, professione=None,
        residenza=None, via=None, stato_vitale=None, note=None, immagine=None,
        eta=eta, **extra)


def test_la_madre_di_un_defunto_ha_partorito_l_anno_in_cui_e_nato():
    """Il caso vero: Marta Raffa, madre di un uomo morto a 58 anni nel 1886."""
    figlio = _menzione(1, "morte", "defunto", 1886, eta=mod.nomi.Eta(58, False, "cinquantotto"))
    figlio.nome, figlio.cognome = "Feliciantonio", "Lella"
    madre = _menzione(2, "morte", "madre", 1886)
    figlio.madre = madre.id
    mod.finestre_dai_figli([figlio, madre])
    assert madre.parto_implicito == 1828


def test_su_un_atto_di_nascita_non_serve_il_parto_implicito():
    """Li' il parto si sa gia' dal tipo d'atto, e contarlo due volte confonde."""
    figlio = _menzione(1, "nascita", "neonato", 1873)
    figlio.nascita_certa_forzata = None
    madre = _menzione(2, "nascita", "madre", 1873)
    figlio.madre = madre.id
    figlio.eta = mod.nomi.Eta(0, False, "zero")
    mod.finestre_dai_figli([figlio, madre])
    assert madre.parto_implicito is None


def test_una_finestra_rovesciata_e_una_contraddizione():
    """Due menzioni che si contraddicono lasciano un intervallo impossibile."""
    scheda = Scheda(chiave="k")
    scheda.finestra = (1840, 1815)
    guai = evidenza.incoerenze(scheda)
    assert any("non si toccano" in g for g in guai)


def test_una_finestra_normale_non_e_una_contraddizione():
    scheda = Scheda(chiave="k")
    scheda.finestra = (1815, 1840)
    assert evidenza.incoerenze(scheda) == []


def test_l_eta_dichiarata_fuori_dalla_propria_finestra_e_una_contraddizione():
    """Il caso Raffa: 'ventidue anni nel 1873' contro 'madre di uno del 1828'."""
    scheda = Scheda(chiave="k")
    scheda.finestra = (1818, 1820)
    scheda.nascite_stimate = [(1851, False)]
    guai = evidenza.incoerenze(scheda)
    assert any("1851" in g and "1818" in g for g in guai)


def test_un_eta_dentro_la_finestra_non_e_una_contraddizione():
    scheda = Scheda(chiave="k")
    scheda.finestra = (1818, 1860)
    scheda.nascite_stimate = [(1851, False)]
    assert evidenza.incoerenze(scheda) == []


def test_gli_arrotondamenti_non_diventano_contraddizioni():
    """I registri arrotondano: qui si cercano gli assurdi, non le sviste."""
    scheda = Scheda(chiave="k")
    scheda.finestra = (1820, 1830)
    scheda.nascite_stimate = [(1838, True)]      # otto anni fuori
    assert evidenza.incoerenze(scheda) == []


def test_un_bambino_di_pochi_mesi_e_nato_l_anno_prima():
    """Nicola Troilo, morto di cinque mesi il 23 marzo 1854, e' nato nel 1853.

    Contarlo nel 1854 faceva partorire sua madre Antonia Ferrara dopo la
    propria morte (21 novembre 1853), e il veto teneva divisa in due la
    famiglia di Felice Americo Troilo.
    """
    figlio = _menzione(1, "morte", "defunto", 1854, eta=mod.nomi.Eta(5 / 12, False, "mesi cinque"))
    figlio.data = "1854-03-23"
    assert figlio.anno_nascita == 1853
    madre = _menzione(2, "morte", "madre", 1854)
    madre.data = "1854-03-23"
    figlio.madre = madre.id
    mod.finestre_dai_figli([figlio, madre])
    assert madre.parto_implicito == 1853


def test_i_mesi_che_entrano_nell_anno_non_lo_arretrano():
    """Cinque mesi a dicembre restano nello stesso anno."""
    figlio = _menzione(3, "morte", "defunto", 1854, eta=mod.nomi.Eta(5 / 12, False, "mesi cinque"))
    figlio.data = "1854-12-02"
    assert figlio.anno_nascita == 1854


def test_una_data_illeggibile_non_ferma_la_ricostruzione():
    """Le date dell'archivio non sono tutte ISO: 'i ' non e' un mese.

    Leggerne i caratteri come un numero fermava l'intero giro con un
    ValueError, e nessuna correzione entrava piu' nell'albero.
    """
    for data in ("i ", "", "senza data", "1854", None):
        figlio = _menzione(4, "morte", "defunto", 1854,
                           eta=mod.nomi.Eta(5 / 12, False, "mesi cinque"))
        figlio.data = data
        assert figlio.anno_nascita == 1854
