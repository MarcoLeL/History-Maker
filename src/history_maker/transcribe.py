"""Fase 3: trascrizione delle immagini con Claude.

Due percorsi, stessa richiesta:

* ``sincrono``  una chiamata per pagina, risultati subito. Comodo per
  provare il prompt su poche pagine.
* ``batch``     la Batch API, che costa la meta' e accetta fino a 100.000
  richieste per lotto. E' il percorso giusto per un secolo di registri.

In entrambi i casi la risposta e' vincolata allo schema di
``history_maker.schema``, quindi arriva gia' come JSON valido, e il prompt
di sistema viaggia con ``cache_control`` perche' e' identico per tutte le
pagine.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from PIL import Image

from history_maker.catalogo import Catalogo, Registro, pertinente
from history_maker.config import Config
from history_maker.errors import TranscriptionError
from history_maker.prompt import ISTRUZIONE_UTENTE, SISTEMA
from history_maker.schema import FORMATO_RISPOSTA

logger = logging.getLogger(__name__)

ESTENSIONI_IMMAGINE = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Prezzo per milione di token di Claude Opus 5, usato solo per la stima
# mostrata prima di spendere. Il lotto asincrono costa la meta'.
PREZZO_INPUT = 5.00
PREZZO_OUTPUT = 25.00


@dataclass
class Pagina:
    """Una pagina da trascrivere, con il contesto del registro che la contiene."""

    percorso: Path
    registro: Registro

    @property
    def id_richiesta(self) -> str:
        """Identificativo stabile, usato come ``custom_id`` nei lotti."""
        return f"{self.registro.slug}--{self.percorso.stem}"

    @property
    def destinazione_relativa(self) -> Path:
        return Path(self.registro.slug) / f"{self.percorso.stem}.json"


def pagine_da_trascrivere(config: Config, solo_mancanti: bool = True) -> list[Pagina]:
    """Elenca le pagine scaricate che rientrano nella raccolta."""
    catalogo = Catalogo.carica(config.catalogo)
    pagine: list[Pagina] = []
    for registro in catalogo.registri:
        if not pertinente(registro, config)[0]:
            continue
        cartella = config.immagini / registro.slug
        if not cartella.is_dir():
            continue
        for immagine in sorted(cartella.iterdir()):
            if immagine.suffix.lower() not in ESTENSIONI_IMMAGINE:
                continue
            pagina = Pagina(percorso=immagine, registro=registro)
            if solo_mancanti and (config.trascrizioni / pagina.destinazione_relativa).exists():
                continue
            pagine.append(pagina)
    return pagine


def prepara_immagine(percorso: Path, lato_lungo: int) -> tuple[str, str]:
    """Ridimensiona e codifica l'immagine per l'API.

    Oltre i ~1568 px di lato lungo l'API ridimensiona comunque, quindi
    spedire l'originale da 5000 px consuma banda senza aggiungere
    dettaglio che il modello possa vedere. Restituisce (media_type, base64).
    """
    with Image.open(percorso) as immagine:
        immagine = immagine.convert("RGB")
        if max(immagine.size) > lato_lungo:
            fattore = lato_lungo / max(immagine.size)
            nuova = (round(immagine.width * fattore), round(immagine.height * fattore))
            immagine = immagine.resize(nuova, Image.LANCZOS)
        buffer = io.BytesIO()
        immagine.save(buffer, format="JPEG", quality=90, optimize=True)
    return "image/jpeg", base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def costruisci_richiesta(pagina: Pagina, config: Config) -> dict[str, Any]:
    """Parametri della richiesta, identici fra percorso sincrono e lotto."""
    media_type, dati = prepara_immagine(pagina.percorso, config.trascrizione.lato_lungo_px)
    istruzione = ISTRUZIONE_UTENTE.format(
        contesto=pagina.registro.contesto or "n.d.",
        anno=pagina.registro.anno or "n.d.",
        tipologia=pagina.registro.tipologia or "n.d.",
        pagina=pagina.percorso.stem,
    )
    return {
        "model": config.trascrizione.modello,
        "max_tokens": config.trascrizione.max_token_risposta,
        # Il prompt di sistema e' identico per ogni pagina: metterlo in
        # cache lo fa pagare per intero una volta sola.
        "system": [{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": dati}},
                    {"type": "text", "text": istruzione},
                ],
            }
        ],
        "output_config": {"format": FORMATO_RISPOSTA},
    }


def _client():
    import anthropic

    return anthropic.Anthropic()


def _salva(config: Config, pagina: Pagina, contenuto: dict[str, Any]) -> Path:
    destinazione = config.trascrizioni / pagina.destinazione_relativa
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    contenuto = dict(contenuto)
    contenuto["_origine"] = {
        "immagine": str(pagina.percorso.relative_to(config.immagini)),
        "registro": pagina.registro.slug,
        "ark_url": pagina.registro.ark_url,
        "anno": pagina.registro.anno,
        "tipologia": pagina.registro.tipologia,
        "contesto": pagina.registro.contesto,
    }
    destinazione.write_text(
        json.dumps(contenuto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destinazione


def _testo_risposta(messaggio) -> str:
    for blocco in messaggio.content:
        if blocco.type == "text":
            return blocco.text
    raise TranscriptionError("La risposta non contiene blocchi di testo")


def trascrivi_sincrono(config: Config, pagine: list[Pagina]) -> int:
    """Trascrive le pagine una per una, in parallelo su piu' thread."""
    import anthropic

    client = _client()

    def una(pagina: Pagina) -> bool:
        try:
            messaggio = client.messages.create(**costruisci_richiesta(pagina, config))
            if messaggio.stop_reason == "refusal":
                logger.warning("%s: richiesta declinata dal modello", pagina.id_richiesta)
                return False
            _salva(config, pagina, json.loads(_testo_risposta(messaggio)))
            return True
        except anthropic.RateLimitError as exc:
            attesa = int(exc.response.headers.get("retry-after", "30"))
            logger.warning("Limite di frequenza raggiunto, attendo %ss", attesa)
            time.sleep(attesa)
            return False
        except anthropic.APIStatusError as exc:
            logger.error("%s: errore API %s", pagina.id_richiesta, exc.status_code)
            return False
        except anthropic.APIConnectionError as exc:
            logger.error("%s: errore di rete (%s)", pagina.id_richiesta, exc)
            return False
        except (json.JSONDecodeError, TranscriptionError) as exc:
            logger.error("%s: risposta non utilizzabile (%s)", pagina.id_richiesta, exc)
            return False

    fatte = 0
    with ThreadPoolExecutor(max_workers=config.trascrizione.richieste_parallele) as pool:
        futuri = {pool.submit(una, p): p for p in pagine}
        for indice, futuro in enumerate(as_completed(futuri), 1):
            if futuro.result():
                fatte += 1
            if indice % 25 == 0:
                logger.info("  ...%d/%d pagine", indice, len(pagine))
    return fatte


def invia_lotti(config: Config, pagine: list[Pagina], dimensione: int = 500) -> list[str]:
    """Invia le pagine alla Batch API e restituisce gli ID dei lotti.

    I lotti sono tenuti a 500 pagine per stare comodamente sotto il
    limite di 256 MB per richiesta: un'immagine da 1568 px in base64
    pesa circa 300-500 KB.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = _client()
    identificativi: list[str] = []
    for inizio in range(0, len(pagine), dimensione):
        fetta = pagine[inizio : inizio + dimensione]
        richieste = [
            Request(
                custom_id=p.id_richiesta,
                params=MessageCreateParamsNonStreaming(**costruisci_richiesta(p, config)),
            )
            for p in fetta
        ]
        lotto = client.messages.batches.create(requests=richieste)
        identificativi.append(lotto.id)
        logger.info("Lotto %s inviato (%d pagine)", lotto.id, len(fetta))
    _registro_lotti(config).write_text(
        json.dumps({"lotti": identificativi}, indent=2), encoding="utf-8"
    )
    return identificativi


def _registro_lotti(config: Config) -> Path:
    config.trascrizioni.mkdir(parents=True, exist_ok=True)
    return config.trascrizioni / "_lotti.json"


def raccogli_lotti(config: Config, identificativi: list[str] | None = None) -> int:
    """Scarica i risultati dei lotti conclusi e li salva su disco."""
    client = _client()
    if identificativi is None:
        percorso = _registro_lotti(config)
        if not percorso.exists():
            raise TranscriptionError(
                "Nessun lotto registrato: lancia prima 'transcribe --batch'."
            )
        identificativi = json.loads(percorso.read_text(encoding="utf-8"))["lotti"]

    indice = {p.id_richiesta: p for p in pagine_da_trascrivere(config, solo_mancanti=False)}
    salvate = 0
    for identificativo in identificativi:
        lotto = client.messages.batches.retrieve(identificativo)
        if lotto.processing_status != "ended":
            logger.info("Lotto %s ancora in corso (%s)", identificativo, lotto.processing_status)
            continue
        for risultato in client.messages.batches.results(identificativo):
            pagina = indice.get(risultato.custom_id)
            if pagina is None:
                logger.warning("Risultato senza pagina corrispondente: %s", risultato.custom_id)
                continue
            if risultato.result.type != "succeeded":
                logger.warning("%s: esito %s", risultato.custom_id, risultato.result.type)
                continue
            try:
                _salva(config, pagina, json.loads(_testo_risposta(risultato.result.message)))
                salvate += 1
            except (json.JSONDecodeError, TranscriptionError) as exc:
                logger.error("%s: risposta non utilizzabile (%s)", risultato.custom_id, exc)
    return salvate


def stima_costo(config: Config, pagine: list[Pagina], lotto: bool = False) -> str:
    """Stima in euro/dollari quanto costerebbe trascrivere queste pagine.

    Il conto dei token di un'immagine e' circa larghezza*altezza/750; per
    una pagina ridotta a 1568 px di lato lungo siamo intorno ai 2.500
    token. L'output di un atto trascritto integralmente sta di norma fra
    800 e 1.500 token.
    """
    if not pagine:
        return "Nessuna pagina da trascrivere."
    token_immagine = (config.trascrizione.lato_lungo_px * config.trascrizione.lato_lungo_px * 0.75) / 750
    token_sistema = len(SISTEMA) / 3.5
    input_totale = len(pagine) * (token_immagine + token_sistema * 0.1)  # 0.1: quasi tutto in cache
    output_totale = len(pagine) * 1200
    sconto = 0.5 if lotto else 1.0
    costo = sconto * (input_totale / 1e6 * PREZZO_INPUT + output_totale / 1e6 * PREZZO_OUTPUT)
    return (
        f"{len(pagine)} pagine da trascrivere con {config.trascrizione.modello}\n"
        f"  ~{input_totale/1e6:.2f}M token in ingresso, ~{output_totale/1e6:.2f}M in uscita\n"
        f"  costo stimato: ${costo:,.2f} ({'lotto asincrono' if lotto else 'chiamate sincrone'})\n"
        f"  la stima e' indicativa: dipende da quanti atti ci sono per pagina."
    )
