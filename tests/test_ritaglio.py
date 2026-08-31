"""La localizzazione di una parola dubbia sulla pagina.

Il modulo esiste per un numero: le copie che il modello legge sono al 40%
della risoluzione lineare degli originali, e quel 60% perduto e' la causa
prima delle letture sbagliate. Un ritaglio preso dall'originale mostra la
stessa parola con due volte e mezzo il dettaglio.
"""

from pathlib import Path

import pytest
from PIL import Image

from history_maker import ritaglio


@pytest.fixture
def scansione(tmp_path) -> Path:
    """Una finta doppia pagina: bordi neri, carta chiara, testo a destra."""
    immagine = Image.new("L", (800, 500), 20)  # bordo nero
    for x in range(60, 740):
        for y in range(40, 460):
            immagine.putpixel((x, y), 235)  # carta
    # righe di "testo" solo sulla meta' destra
    for riga in range(10):
        y = 90 + riga * 30
        for x in range(430, 700):
            for dy in range(6):
                immagine.putpixel((x, y + dy), 30)
    # PNG e non JPEG: gli aloni di compressione attorno ai bordi neri
    # sporcherebbero il profilo di una finzione fatta di tinte piatte.
    percorso = tmp_path / "pagina.png"
    immagine.save(percorso)
    return percorso


def test_lo_specchio_di_scrittura_esclude_i_bordi(scansione):
    inizio, fine = ritaglio.specchio_di_scrittura(scansione)
    assert 0.10 < inizio < 0.25
    assert 0.60 < fine < 0.85


def test_una_meta_vuota_viene_scartata(scansione):
    """Il verso bianco non va ritagliato: raddoppierebbe i token per
    mostrare carta."""
    sinistra, destra = ritaglio.colonna_di_scrittura(scansione)
    assert sinistra > 0.40 and destra > 0.90


@pytest.mark.parametrize(
    "testo, bersaglio, atteso",
    [
        ("Alfa Beta Gamma", "Alfa", 0.0),
        ("Alfa Beta Gamma", "gamma", pytest.approx(0.667, abs=0.02)),
        ("Alfa Beta Gamma", "Delta", None),
        (None, "Alfa", None),
    ],
)
def test_posizione_nel_testo(testo, bersaglio, atteso):
    assert ritaglio.posizione_nel_testo(testo, bersaglio) == atteso


def test_il_lato_dichiarato_evita_di_indovinare(scansione):
    """Quando la trascrizione dice da che parte sta il testo, il ritaglio
    non dipende piu' da un'euristica sull'inchiostro.

    Qui la meta' sinistra e' bianca, quindi anche la stima automatica ci
    arriva; il valore del dato dichiarato si vede sulle pagine dove
    entrambe le meta' portano segni — un atto scritto a mano da un lato e
    il modulo prestampato dall'altro.
    """
    with Image.open(scansione) as im:
        larghezza = im.width
    meta = ritaglio.banda_per(scansione, "Alfa Beta", "Beta", lato="destra")
    assert meta.sinistra > larghezza * 0.4
    assert meta.destra > larghezza * 0.9


def test_un_bersaglio_introvabile_da_tutto_lo_specchio(scansione):
    """Meglio l'intero specchio che una banda a caso: e' comunque a piena
    risoluzione, quindi piu' nitido della pagina ridotta."""
    banda = ritaglio.banda_per(scansione, "Alfa Beta", "Delta")
    inizio, fine = ritaglio.specchio_di_scrittura(scansione)
    with Image.open(scansione) as im:
        altezza = im.height
    assert banda.alto == round(altezza * inizio)
    assert banda.basso == round(altezza * fine)


def test_il_ritaglio_non_ingrandisce(scansione, tmp_path):
    """Ingrandire non aggiunge informazione ma moltiplica i token."""
    banda = ritaglio.banda_per(scansione, "Alfa Beta", "Beta")
    fuori = ritaglio.ritaglia(scansione, banda, tmp_path / "banda.jpg")
    with Image.open(fuori) as im:
        assert im.width == banda.destra - banda.sinistra
        assert im.height == banda.basso - banda.alto


def test_una_meta_scritta_non_viene_mai_scartata(tmp_path):
    """Il difetto che questa regola esiste per impedire.

    Su questi registri una doppia pagina porta spesso un atto PER LATO,
    non un atto e un modulo vuoto. La prima versione si accontentava di un
    solo indizio e dimezzava 86 pagine su 105: ritrascrivendo, 46 cognomi
    sparivano insieme agli atti che stavano sulla meta' buttata via.

    I due errori non costano uguale. Non ritagliare costa qualche token;
    ritagliare di traverso cancella un atto senza che nessuno se ne
    accorga, perche' la trascrizione che ne esce e' plausibile.
    """
    immagine = Image.new("L", (800, 500), 20)
    for x in range(60, 740):
        for y in range(40, 460):
            immagine.putpixel((x, y), 235)
    # scrittura su ENTRAMBE le meta'
    for riga in range(10):
        y = 90 + riga * 30
        for x in list(range(100, 360)) + list(range(430, 700)):
            for dy in range(6):
                immagine.putpixel((x, y + dy), 30)
    percorso = tmp_path / "due-atti.png"
    immagine.save(percorso)

    assert ritaglio.lato_scritto(percorso) is None
    x0, _, x1, _ = ritaglio.area_da_leggere(percorso)
    assert x0 < 200 and x1 > 600  # nessuna meta' e' stata tolta
