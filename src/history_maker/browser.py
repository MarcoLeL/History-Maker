"""Avvio e governo del browser Selenium.

Il browser serve per una ragione precisa: le pagine del portale sono
protette da un WAF di AWS che risponde alle richieste automatiche con una
challenge JavaScript. Un browser vero la risolve da solo; ``requests`` no.
Gli endpoint IIIF, che non sono protetti, restano su HTTP semplice.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from history_maker.http import USER_AGENT

logger = logging.getLogger(__name__)

BASE = "https://antenati.cultura.gov.it"

# Testi dei pulsanti di consenso ai cookie osservati sui siti del
# Ministero della cultura. Sono tentativi, non requisiti: se nessuno
# corrisponde si prosegue comunque.
TESTI_CONSENSO = ("Accetta", "Accetto", "Accetta tutti", "Ho capito", "Chiudi", "OK")


@contextmanager
def apri_browser(headless: bool = False, timeout: int = 45) -> Iterator[webdriver.Chrome]:
    """Apre Chrome/Chromium e lo chiude in ogni caso all'uscita.

    Il default e' *con* finestra: la challenge del WAF viene superata piu'
    facilmente da un browser che si comporta come quello di una persona, e
    se il portale chiede un CAPTCHA lo si puo' risolvere a mano una volta
    sola invece di veder fallire tutta la raccolta.
    """
    opzioni = Options()
    if headless:
        opzioni.add_argument("--headless=new")
    opzioni.add_argument("--window-size=1400,1000")
    opzioni.add_argument("--disable-gpu")
    opzioni.add_argument("--no-sandbox")
    opzioni.add_argument("--disable-dev-shm-usage")
    opzioni.add_argument("--lang=it-IT")
    opzioni.add_argument(f"--user-agent={USER_AGENT}")
    # Nasconde il flag che segnala l'automazione, che alcuni WAF leggono.
    opzioni.add_argument("--disable-blink-features=AutomationControlled")
    opzioni.add_experimental_option("excludeSwitches", ["enable-automation"])
    opzioni.add_experimental_option("useAutomationExtension", False)

    binario = os.environ.get("CHROME_BINARY")
    if binario:
        opzioni.binary_location = binario

    # Senza CHROMEDRIVER, Selenium Manager scarica da solo il driver
    # giusto: comodo, ma richiede internet e fallisce dietro i proxy
    # aziendali, e non aiuta quando il browser installato e' piu' vecchio
    # del driver gia' presente nel PATH.
    driver_path = os.environ.get("CHROMEDRIVER")
    servizio = Service(executable_path=driver_path) if driver_path else None

    try:
        driver = webdriver.Chrome(options=opzioni, service=servizio)
    except WebDriverException as exc:  # pragma: no cover - dipende dall'ambiente
        raise RuntimeError(
            "Impossibile avviare Chrome.\n"
            f"  {getattr(exc, 'msg', None) or exc}\n"
            "Servono Google Chrome o Chromium e un chromedriver della STESSA "
            "versione maggiore. Se le versioni non coincidono, o la macchina "
            "non raggiunge internet, scarica il driver da "
            "https://googlechromelabs.github.io/chrome-for-testing/ e imposta "
            "CHROMEDRIVER sul suo percorso (e CHROME_BINARY su quello del "
            "browser, se non e' nel PATH)."
        ) from exc

    driver.set_page_load_timeout(timeout)
    try:
        yield driver
    finally:
        driver.quit()


# Oltre questa dimensione complessiva dei cookie, il portale risponde
# "Bad Request — Size of a request header field exceeds server limit" e
# non si riprende piu' da solo. Il limite dei server e' tipicamente 8 KB
# per intestazione: si interviene prima, con margine.
COOKIE_MAX_BYTE = 6_000

# Come si presenta il guasto quando ci si arriva lo stesso: non e' un
# errore di Selenium, e' una pagina servita col testo dentro, quindi
# l'unico modo di accorgersene e' leggerla.
SEGNALI_HEADER_TROPPO_GRANDE = (
    "request header field exceeds",
    "size of a request header",
    "400 bad request",
)


def _peso_cookie(driver: webdriver.Chrome) -> int:
    """Quanti byte occuperebbero i cookie nell'intestazione della richiesta."""
    try:
        return sum(
            len(c.get("name", "")) + len(c.get("value", "")) + 3
            for c in driver.get_cookies()
        )
    except WebDriverException:
        return 0


def sfoltisci_cookie(driver: webdriver.Chrome, soglia: int = COOKIE_MAX_BYTE) -> bool:
    """Butta i cookie quando diventano troppi. Dice se l'ha fatto.

    Il portale sta dietro un WAF di AWS che rilascia un token a ogni
    passaggio, e in una scoperta di novant'anni sono novanta ricerche piu'
    qualche centinaio di gallerie: i cookie si accumulano finche'
    l'intestazione supera il limite del server, e da quel momento **ogni**
    pagina risponde "Bad Request". Il guasto e' insidioso perche' non
    assomiglia a un problema di cookie: sembra che il portale sia caduto.

    Buttarli tutti costa una challenge del WAF in piu' — che la funzione
    di attesa gestisce gia' — e vale molto meno di una scoperta da
    ricominciare.
    """
    if _peso_cookie(driver) <= soglia:
        return False
    logger.info("Cookie troppo voluminosi: li azzero per non farmi rifiutare dal portale.")
    try:
        driver.delete_all_cookies()
    except WebDriverException:
        return False
    return True


def _header_troppo_grande(driver: webdriver.Chrome) -> bool:
    try:
        testo = driver.find_element(By.TAG_NAME, "body").text.lower()
    except WebDriverException:
        return False
    return any(s in testo for s in SEGNALI_HEADER_TROPPO_GRANDE)


def vai(driver: webdriver.Chrome, url: str, attesa: int = 30) -> None:
    """Apre l'URL, accetta i cookie e aspetta che il WAF lasci passare.

    Sfoltisce i cookie prima di partire, e se il portale rifiuta comunque
    la richiesta per intestazione troppo grande, li azzera e riprova una
    volta: senza, da quel punto in poi fallirebbe tutto il resto della
    scoperta senza che nulla lo dica.
    """
    logger.info("Apro %s", url)
    sfoltisci_cookie(driver)
    driver.get(url)
    if _header_troppo_grande(driver):
        logger.warning("Il portale ha rifiutato la richiesta per cookie troppo grandi: riprovo.")
        driver.delete_all_cookies()
        driver.get(url)
    accetta_cookie(driver)
    attendi_superamento_waf(driver, attesa)


def accetta_cookie(driver: webdriver.Chrome) -> None:
    """Chiude il banner dei cookie, se ce n'e' uno riconoscibile."""
    for testo in TESTI_CONSENSO:
        xpath = (
            f"//button[contains(normalize-space(.), '{testo}')] | "
            f"//a[contains(normalize-space(.), '{testo}')]"
        )
        for elemento in driver.find_elements(By.XPATH, xpath):
            try:
                if elemento.is_displayed():
                    elemento.click()
                    logger.debug("Banner cookie chiuso con '%s'", testo)
                    time.sleep(0.5)
                    return
            except WebDriverException:
                continue


def attendi_superamento_waf(driver: webdriver.Chrome, attesa: int = 30) -> None:
    """Aspetta che la pagina interstiziale del WAF lasci il posto al sito.

    La challenge di AWS si presenta come una pagina quasi vuota che si
    ricarica da sola. Aspettiamo che compaia un ``<main>`` o comunque un
    corpo con contenuto reale.
    """
    try:
        WebDriverWait(driver, attesa).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "main, #main, .main-content")) > 0
            or len(d.find_element(By.TAG_NAME, "body").text.strip()) > 200
        )
    except TimeoutException:
        logger.warning(
            "La pagina %s non si e' aperta entro %ss: potrebbe esserci una "
            "challenge del WAF da risolvere a mano nella finestra del browser.",
            driver.current_url,
            attesa,
        )
