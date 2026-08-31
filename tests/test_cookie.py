"""La saturazione dei cookie, che ferma una scoperta a meta'.

Il portale sta dietro un WAF di AWS che rilascia un token a ogni
passaggio. In una scoperta di novant'anni — novanta ricerche piu'
qualche centinaio di gallerie — i cookie si accumulano finche'
l'intestazione supera il limite del server, e da quel momento OGNI
pagina risponde "Bad Request: Size of a request header field exceeds
server limit".

E' successo davvero, e il modo in cui e' successo e' la ragione di
questi test: la scoperta non si e' fermata. Ha continuato ad aprire
pagine, a non trovare nessun manifest, e a scrivere un catalogo di
registri vuoti — un guasto che assomiglia a un portale caduto.
"""

import pytest

from history_maker import browser


class FintoDriver:
    """Il minimo per esercitare la logica dei cookie."""

    def __init__(self, cookie=None, testo=""):
        self._cookie = list(cookie or [])
        self.testo = testo
        self.azzerati = 0
        self.aperture = []

    def get_cookies(self):
        return list(self._cookie)

    def delete_all_cookies(self):
        self._cookie = []
        self.azzerati += 1

    def get(self, url):
        self.aperture.append(url)

    def find_element(self, by, valore):
        class Corpo:
            text = self.testo

        return Corpo()

    def find_elements(self, by, valore):
        return []

    def execute_script(self, *a, **k):
        return 0


def _cookie(quanti, dimensione=100):
    return [{"name": f"c{i}", "value": "x" * dimensione} for i in range(quanti)]


def test_i_cookie_leggeri_non_si_toccano():
    """Azzerarli senza motivo costa una challenge del WAF a ogni pagina."""
    driver = FintoDriver(_cookie(3))
    assert browser.sfoltisci_cookie(driver) is False
    assert driver.azzerati == 0


def test_i_cookie_troppo_voluminosi_si_azzerano():
    driver = FintoDriver(_cookie(100))  # ~10 KB, oltre il limite dei server
    assert browser.sfoltisci_cookie(driver) is True
    assert driver.get_cookies() == []


def test_la_soglia_sta_sotto_il_limite_dei_server():
    """Il limite tipico e' 8 KB: si interviene prima, con margine."""
    assert browser.COOKIE_MAX_BYTE < 8192


def test_il_peso_conta_nomi_valori_e_separatori():
    driver = FintoDriver([{"name": "ab", "value": "cde"}])
    assert browser._peso_cookie(driver) == 2 + 3 + 3


def test_un_driver_che_non_risponde_non_fa_esplodere_nulla():
    class Rotto(FintoDriver):
        def get_cookies(self):
            from selenium.common.exceptions import WebDriverException

            raise WebDriverException("sessione persa")

    assert browser._peso_cookie(Rotto()) == 0
    assert browser.sfoltisci_cookie(Rotto()) is False


@pytest.mark.parametrize(
    "testo",
    [
        "Bad Request\nYour browser sent a request that this server could not understand.\n"
        "Size of a request header field exceeds server limit.",
        "400 Bad Request",
        "SIZE OF A REQUEST HEADER FIELD EXCEEDS SERVER LIMIT",
    ],
)
def test_il_rifiuto_del_portale_si_riconosce_dal_testo(testo):
    """Non e' un errore di Selenium: e' una pagina servita, col testo dentro."""
    assert browser._header_troppo_grande(FintoDriver(testo=testo))


def test_una_pagina_normale_non_e_un_rifiuto():
    assert not browser._header_troppo_grande(
        FintoDriver(testo="Archivio di Stato di Chieti — Stato civile italiano")
    )


def test_dopo_un_rifiuto_si_azzera_e_si_riprova(monkeypatch):
    """Senza il secondo tentativo, tutto il resto della scoperta fallirebbe."""
    driver = FintoDriver(_cookie(100), testo="Bad Request: size of a request header field exceeds")
    monkeypatch.setattr(browser, "accetta_cookie", lambda d: None)
    monkeypatch.setattr(browser, "attendi_superamento_waf", lambda d, a=30: None)

    browser.vai(driver, "https://antenati.cultura.gov.it/ark:/12657/an_ua1")

    assert driver.aperture == [
        "https://antenati.cultura.gov.it/ark:/12657/an_ua1",
        "https://antenati.cultura.gov.it/ark:/12657/an_ua1",
    ], "il secondo tentativo non e' partito"
    assert driver.azzerati >= 1
