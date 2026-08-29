"""La raccolta dei link di galleria dalla pagina dei risultati.

Il caso che ha originato questi test: la ricerca restituiva otto
gallerie del 1809 e il catalogo ne raccoglieva zero. Il filtro pretendeva
un segmento finale (``/ark:/12657/an_ua19944535/w9DWR8x``) che il portale
non emette piu' nella pagina dei risultati.
"""

from history_maker.discover import _link_ark


class FintoElemento:
    def __init__(self, href: str) -> None:
        self._href = href

    def get_attribute(self, nome: str) -> str:
        return self._href if nome == "href" else ""


class FintoDriver:
    """Il minimo di webdriver che ``_link_ark`` usa davvero."""

    def __init__(self, href: list[str], sorgente: str = "") -> None:
        self._href = href
        self.page_source = sorgente

    def find_elements(self, _by, _selettore):
        return [FintoElemento(h) for h in self._href]


PORTALE = "https://antenati.cultura.gov.it"


def test_ark_senza_segmento_finale_e_una_galleria():
    """La forma emessa oggi dal portale."""
    driver = FintoDriver([f"{PORTALE}/ark:/12657/an_ua18284973?lang=it"])
    assert _link_ark(driver) == {f"{PORTALE}/ark:/12657/an_ua18284973"}


def test_ark_con_segmento_finale_resta_una_galleria():
    """La forma precedente non deve smettere di funzionare."""
    driver = FintoDriver([f"{PORTALE}/ark:/12657/an_ua19944535/w9DWR8x"])
    assert _link_ark(driver) == {f"{PORTALE}/ark:/12657/an_ua19944535/w9DWR8x"}


def test_gli_ark_non_di_unita_archivistica_si_scartano():
    """Fondi e complessi archivistici non sono gallerie di immagini."""
    driver = FintoDriver(
        [
            f"{PORTALE}/ark:/12657/an_cs00000123",
            f"{PORTALE}/ark:/12657/an_fo456",
            f"{PORTALE}/il-portale/faq/?lang=it",
        ]
    )
    assert _link_ark(driver) == set()


def test_ark_presente_solo_nel_markup():
    """Alcune schede costruiscono il link via JavaScript."""
    driver = FintoDriver(
        [],
        sorgente=f'<div data-url="{PORTALE}/ark:/12657/an_ua18286792"></div>',
    )
    assert _link_ark(driver) == {f"{PORTALE}/ark:/12657/an_ua18286792"}


def test_lo_stesso_ark_nell_href_e_nel_markup_conta_una_volta():
    url = f"{PORTALE}/ark:/12657/an_ua18284981"
    driver = FintoDriver([f"{url}?lang=it"], sorgente=f'<a href="{url}">')
    assert _link_ark(driver) == {url}
