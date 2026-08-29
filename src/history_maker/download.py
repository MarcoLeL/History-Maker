"""Fase 2: scaricamento delle immagini dagli endpoint IIIF.

Qui il browser non serve: i manifest e le immagini stanno su domini DAM /
IIIF che il WAF non protegge. Il download e' parallelo ma volutamente
poco aggressivo (default: 3 connessioni) — dall'altra parte c'e' un
servizio pubblico gratuito.

Il download e' ripartibile: un'immagine gia' presente e non vuota viene
saltata, quindi rilanciare il comando dopo un'interruzione riprende da
dove si era fermato.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slugify import slugify

from history_maker import http, iiif
from history_maker.catalogo import Catalogo, Registro, pertinente
from history_maker.config import Config

logger = logging.getLogger(__name__)

ESTENSIONI = {"image/jpeg": ".jpg", "image/jp2": ".jp2", "image/png": ".png", "image/tiff": ".tif"}


@dataclass
class Esito:
    scaricate: int = 0
    saltate: int = 0
    fallite: int = 0
    byte: int = 0

    def __add__(self, altro: "Esito") -> "Esito":
        return Esito(
            self.scaricate + altro.scaricate,
            self.saltate + altro.saltate,
            self.fallite + altro.fallite,
            self.byte + altro.byte,
        )


def nome_pagina(canvas: dict[str, Any], indice: int) -> str:
    """Nome file di una pagina, ordinabile alfabeticamente."""
    etichetta = str(canvas.get("label") or "").strip()
    if etichetta.isdigit():
        return f"{int(etichetta):04d}"
    if etichetta:
        return f"{indice:04d}-{slugify(etichetta)}"
    return f"{indice:04d}"


def scarica_registro(
    registro: Registro,
    config: Config,
    radice: Path,
    lato_max: int = 0,
    progresso: Callable[[], None] | None = None,
) -> Esito:
    """Scarica tutte le pagine di un registro nella sua cartella."""
    sessione = http.crea_sessione(tentativi=config.rete.tentativi)
    manifest = json.loads(
        http.get(sessione, registro.manifest_url, timeout=config.rete.timeout_s).text
    )
    pagine = iiif.canvases(manifest)
    registro.n_immagini = len(pagine)

    cartella = radice / registro.slug
    cartella.mkdir(parents=True, exist_ok=True)
    registro.cartella = str(cartella.relative_to(radice))
    (cartella / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    esito = Esito()

    def una_pagina(coppia: tuple[int, dict[str, Any]]) -> Esito:
        indice, canvas = coppia
        stem = nome_pagina(canvas, indice)
        esistenti = list(cartella.glob(f"{stem}.*"))
        if any(p.stat().st_size > 0 for p in esistenti if p.suffix != ".json"):
            if progresso:
                progresso()
            return Esito(saltate=1)
        url = iiif.con_dimensione(iiif.url_immagine(canvas), lato_max)
        try:
            risposta = http.get(sessione, url, timeout=config.rete.timeout_s, stream=True)
            estensione = ESTENSIONI.get(http.tipo_contenuto(risposta), ".jpg")
            destinazione = cartella / f"{stem}{estensione}"
            parziale = destinazione.with_suffix(destinazione.suffix + ".part")
            byte = 0
            with parziale.open("wb") as handle:
                for blocco in risposta.iter_content(chunk_size=64 * 1024):
                    handle.write(blocco)
                    byte += len(blocco)
            # Rinominare solo a scaricamento finito evita che un file
            # troncato da Ctrl-C venga scambiato per completo al rilancio.
            parziale.rename(destinazione)
            time.sleep(config.rete.pausa_tra_richieste_s)
            if progresso:
                progresso()
            return Esito(scaricate=1, byte=byte)
        except Exception as exc:  # noqa: BLE001 - una pagina persa non ferma il registro
            logger.warning("%s pagina %s: %s", registro.slug, stem, exc)
            if progresso:
                progresso()
            return Esito(fallite=1)

    with ThreadPoolExecutor(max_workers=config.rete.download_paralleli) as pool:
        for futuro in as_completed(pool.submit(una_pagina, c) for c in enumerate(pagine, 1)):
            esito = esito + futuro.result()
    return esito


def esegui(
    config: Config,
    limite_registri: int | None = None,
    lato_max: int = 0,
    solo_stima: bool = False,
    dal: int | None = None,
    al: int | None = None,
) -> Esito:
    """Scarica i registri pertinenti del catalogo, eventualmente di soli alcuni anni."""
    catalogo = Catalogo.carica(config.catalogo)
    selezionati = [r for r in catalogo.registri if pertinente(r, config)[0]]
    if dal is not None:
        selezionati = [r for r in selezionati if (r.anno or 0) >= dal]
    if al is not None:
        selezionati = [r for r in selezionati if (r.anno or 0) <= al]
    selezionati.sort(key=lambda r: (r.anno or 0, r.tipologia or ""))
    if limite_registri:
        selezionati = selezionati[:limite_registri]

    attese = sum(r.n_immagini or 0 for r in selezionati)
    logger.info("%d registri selezionati, ~%d immagini", len(selezionati), attese)
    if solo_stima:
        for registro in selezionati:
            tipologia = registro.tipologia or "?"
            print(f"  {registro.anno}  {tipologia:<16} {registro.n_immagini or '?':>5} img  {registro.slug}")
        return Esito()

    totale = Esito()
    for indice, registro in enumerate(selezionati, 1):
        logger.info(
            "[%d/%d] %s %s (%s immagini)",
            indice, len(selezionati), registro.anno, registro.tipologia, registro.n_immagini or "?",
        )
        totale = totale + scarica_registro(registro, config, config.immagini, lato_max)
        catalogo.salva(config.catalogo)
    return totale
