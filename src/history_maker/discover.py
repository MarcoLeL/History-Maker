"""Fase 1: scoperta dei registri di Torrebruna sul portale.

Strategia. La ricerca del portale accetta due parametri in query string,
``localita`` e ``anno``. Interrogarla un anno alla volta
(``?localita=Torrebruna&anno=1866``) restituisce insiemi piccoli e aggira
del tutto la paginazione, che e' la parte piu' fragile da automatizzare.
Una passata finale senza ``anno`` raccoglie i registri il cui anno non e'
indicizzato come ci si aspetta.

Dall'HTML dei risultati estraiamo soltanto i link ``ark:``, cioe' la cosa
piu' stabile che la pagina contenga. Titolo, tipologia, anno e comune non
vengono letti dall'HTML ma dal manifest IIIF di ciascun registro: e' una
fonte strutturata e documentata, e quindi il filtro Torrebruna /
Guardiabruna non dipende dalla grafica del portale.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from selenium import webdriver
from selenium.webdriver.common.by import By

from history_maker import iiif
from history_maker.browser import BASE, apri_browser, vai
from history_maker.catalogo import Catalogo, Registro, pertinente
from history_maker.config import Config
from history_maker.errors import ManifestError

logger = logging.getLogger(__name__)

URL_RICERCA = f"{BASE}/search-registry/"
ARK_PATTERN = re.compile(r"https?://[^\s\"']*?/ark:/12657/[A-Za-z0-9_.:/-]+")


def url_ricerca(termine: str, anno: int | None = None) -> str:
    parametri = {"localita": termine}
    if anno is not None:
        parametri["anno"] = str(anno)
    return f"{URL_RICERCA}?{urlencode(parametri)}"


def _link_ark(driver: webdriver.Chrome) -> set[str]:
    """Tutti gli URL di galleria presenti nella pagina corrente.

    Legge sia gli ``href`` degli ancoraggi sia il sorgente grezzo: alcune
    schede dei risultati costruiscono il collegamento via JavaScript e
    l'ark compare solo nel markup.
    """
    trovati: set[str] = set()
    for elemento in driver.find_elements(By.CSS_SELECTOR, "a[href*='/ark:/12657/']"):
        href = elemento.get_attribute("href")
        if href:
            trovati.add(href.split("?")[0].rstrip("/"))
    for match in ARK_PATTERN.finditer(driver.page_source):
        trovati.add(match.group(0).split("?")[0].rstrip("/"))
    # Gli ark che puntano al fondo o all'archivio, non a una galleria, non
    # hanno il segmento finale con il codice dell'unita': si scartano.
    return {u for u in trovati if iiif.estrai_ark_id(u) and u.count("/") >= 6}


def _scorri_fino_in_fondo(driver: webdriver.Chrome, pause: float, max_giri: int = 40) -> None:
    """Scorre la pagina finche' smette di crescere.

    Copre sia lo scorrimento infinito sia i pulsanti "carica altri", che
    vengono cliccati se presenti.
    """
    altezza = 0
    for _ in range(max_giri):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        for testo in ("Carica altri", "Mostra altri", "Carica altro", "Successiva", "Avanti"):
            for bottone in driver.find_elements(
                By.XPATH, f"//button[contains(normalize-space(.), '{testo}')]"
            ):
                try:
                    if bottone.is_displayed() and bottone.is_enabled():
                        driver.execute_script("arguments[0].click();", bottone)
                        time.sleep(pause)
                except Exception:  # noqa: BLE001 - il pulsante puo' sparire fra il find e il click
                    continue
        nuova = driver.execute_script("return document.body.scrollHeight")
        if nuova == altezza:
            return
        altezza = nuova


def raccogli_ark(
    driver: webdriver.Chrome,
    config: Config,
    anni: list[int] | None,
    debug_dir: Path | None = None,
) -> set[str]:
    """Interroga la ricerca e restituisce gli URL di galleria trovati."""
    pausa = config.rete.pausa_tra_richieste_s
    interrogazioni: list[int | None] = list(anni or [])
    interrogazioni.append(None)  # passata finale senza filtro sull'anno

    tutti: set[str] = set()
    for anno in interrogazioni:
        url = url_ricerca(config.termine_ricerca, anno)
        vai(driver, url)
        _scorri_fino_in_fondo(driver, pausa)
        trovati = _link_ark(driver)
        nuovi = trovati - tutti
        tutti |= trovati
        logger.info(
            "%s -> %d galleria/e (%d nuove)",
            f"anno {anno}" if anno else "tutti gli anni",
            len(trovati),
            len(nuovi),
        )
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            nome = f"ricerca-{anno or 'tutti'}.html"
            (debug_dir / nome).write_text(driver.page_source, encoding="utf-8")
        time.sleep(pausa)
    return tutti


def risolvi_manifest(driver: webdriver.Chrome, registro: Registro, pausa: float) -> None:
    """Apre la galleria e ne ricava l'URL del manifest IIIF.

    Deve passare dal browser perche' la pagina di galleria e', come tutto
    il portale, dietro il WAF. Il manifest che ne esce invece si scarica
    con una GET normale.
    """
    vai(driver, registro.ark_url)
    time.sleep(pausa)
    try:
        registro.manifest_url = iiif.estrai_manifest_url(driver.page_source, registro.ark_url)
    except ManifestError as exc:
        registro.note.append(str(exc))
        logger.warning("%s: %s", registro.ark_url, exc)


def esegui(
    config: Config,
    headless: bool = False,
    solo_anni: bool = True,
    debug_dir: Path | None = None,
    dal: int | None = None,
    al: int | None = None,
) -> Catalogo:
    """Costruisce (o aggiorna) il catalogo dei registri del comune.

    ``dal`` e ``al`` restringono gli anni interrogati senza toccare il
    file di configurazione: servono per una prova su un anno solo prima
    di lanciare la spazzata di tutto il secolo, che sono 92 ricerche.
    """
    percorso = config.catalogo
    catalogo = (
        Catalogo.carica(percorso) if percorso.exists() else Catalogo(comune=config.comune)
    )

    primo = dal if dal is not None else config.anno_min
    ultimo = al if al is not None else config.anno_max
    anni = list(range(primo, ultimo + 1)) if solo_anni else None
    if anni:
        logger.info("Interrogo il portale per gli anni %d-%d", primo, ultimo)

    with apri_browser(headless=headless) as driver:
        ark_urls = raccogli_ark(driver, config, anni, debug_dir)
        nuovi = [
            Registro(
                ark_url=url,
                ark_id=iiif.estrai_ark_id(url),
                archive_id=_archive_id_sicuro(url),
            )
            for url in sorted(ark_urls)
        ]
        aggiunti = catalogo.unisci(nuovi)
        logger.info("Catalogo: %d registri totali (%d nuovi)", len(catalogo.registri), aggiunti)
        catalogo.salva(percorso)

        da_risolvere = [r for r in catalogo.registri if not r.manifest_url]
        logger.info("Risolvo il manifest di %d registri", len(da_risolvere))
        for indice, registro in enumerate(da_risolvere, 1):
            risolvi_manifest(driver, registro, config.rete.pausa_tra_richieste_s)
            if indice % 10 == 0:
                catalogo.salva(percorso)
                logger.info("  ...%d/%d", indice, len(da_risolvere))
        catalogo.salva(percorso)

    arricchisci_da_manifest(catalogo, config)
    catalogo.salva(percorso)
    return catalogo


def _archive_id_sicuro(url: str) -> str | None:
    try:
        return iiif.estrai_archive_id(url)
    except ManifestError:
        return None


def arricchisci_da_manifest(catalogo: Catalogo, config: Config) -> None:
    """Scarica i manifest e ne copia i metadati nel catalogo.

    Passa da ``requests``: l'endpoint DAM che serve i manifest non e'
    protetto dal WAF, quindi qui il browser non serve piu'.
    """
    from history_maker import http

    sessione = http.crea_sessione(tentativi=config.rete.tentativi)
    da_fare = [r for r in catalogo.registri if r.manifest_url and r.contesto is None]
    logger.info("Leggo i metadati di %d manifest", len(da_fare))
    for registro in da_fare:
        try:
            risposta = http.get(sessione, registro.manifest_url, timeout=config.rete.timeout_s)
            registro.applica_manifest(json.loads(risposta.text))
        except Exception as exc:  # noqa: BLE001 - un manifest rotto non deve fermare la raccolta
            registro.note.append(f"manifest non leggibile: {exc}")
            logger.warning("%s: %s", registro.manifest_url, exc)
        time.sleep(config.rete.pausa_tra_richieste_s)


def riepilogo(catalogo: Catalogo, config: Config) -> str:
    """Testo che riassume cosa il catalogo contiene e cosa verra' scaricato."""
    dentro, fuori = [], []
    for registro in catalogo.registri:
        ok, motivo = pertinente(registro, config)
        (dentro if ok else fuori).append((registro, motivo))

    righe = [
        f"Catalogo di {catalogo.comune}: {len(catalogo.registri)} registri trovati",
        f"  selezionati ({config.anno_min}-{config.anno_max}): {len(dentro)}",
        f"  scartati:                     {len(fuori)}",
    ]

    per_tipologia: dict[str, int] = {}
    immagini = 0
    for registro, _ in dentro:
        per_tipologia[registro.tipologia or "?"] = per_tipologia.get(registro.tipologia or "?", 0) + 1
        immagini += registro.n_immagini or 0
    if per_tipologia:
        righe.append("  per tipologia: " + ", ".join(f"{k}={v}" for k, v in sorted(per_tipologia.items())))
    righe.append(f"  immagini da scaricare: {immagini}")

    motivi: dict[str, int] = {}
    for _, motivo in fuori:
        chiave = motivo.split(":")[0]
        motivi[chiave] = motivi.get(chiave, 0) + 1
    if motivi:
        righe.append("  motivi di scarto: " + ", ".join(f"{k} ({v})" for k, v in sorted(motivi.items())))
    return "\n".join(righe)
