"""Sessione HTTP per il Portale Antenati.

Il portale sta dietro un WAF di AWS che risponde alle richieste
automatiche con un HTTP 202 e l'header ``x-amzn-waf-action: challenge``.
Gli endpoint IIIF/DAM non sono protetti: e' su quelli che gira il
download, mentre la navigazione passa da Selenium.
"""

from __future__ import annotations

import logging
from email.message import Message

from requests import Response, Session
from requests.adapters import HTTPAdapter
from requests.utils import default_headers
from urllib3.util.retry import Retry

from history_maker.errors import WafChallengeError

logger = logging.getLogger(__name__)

WAF_STATUS = 202
WAF_HEADER = "x-amzn-waf-action"
WAF_VALORE = "challenge"

# Il reverse proxy del portale rifiuta con 403 le richieste che non
# somigliano a un browser: questi due header fanno parte del contratto.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
)
REFERER = "https://antenati.cultura.gov.it/"

STATI_RIPROVABILI = (429, 500, 502, 503, 504)


def crea_sessione(tentativi: int = 5, backoff: float = 0.5) -> Session:
    """Sessione con header da browser e ritentativi a backoff crescente."""
    sessione = Session()
    headers = default_headers()
    headers["User-Agent"] = USER_AGENT
    headers["Referer"] = REFERER
    sessione.headers = headers
    politica = Retry(
        total=tentativi,
        backoff_factor=backoff,
        status_forcelist=list(STATI_RIPROVABILI),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adattatore = HTTPAdapter(max_retries=politica)
    sessione.mount("https://", adattatore)
    sessione.mount("http://", adattatore)
    return sessione


def get(sessione: Session, url: str, timeout: int = 120, stream: bool = False) -> Response:
    """GET che trasforma la challenge del WAF in un errore tipizzato."""
    logger.debug("GET %s", url)
    risposta = sessione.get(url, timeout=timeout, stream=stream)
    if risposta.status_code == WAF_STATUS and risposta.headers.get(WAF_HEADER) == WAF_VALORE:
        raise WafChallengeError(
            f"{url}: il WAF ha richiesto una challenge. Rilancia la fase "
            "'discover', che usa un browser vero e sa superarla."
        )
    risposta.raise_for_status()
    return risposta


def tipo_contenuto(risposta: Response) -> str:
    """Content-Type senza parametri (``image/jpeg``, non ``image/jpeg; ...``)."""
    messaggio = Message()
    messaggio["Content-Type"] = risposta.headers.get("Content-Type", "")
    return messaggio.get_content_type()
