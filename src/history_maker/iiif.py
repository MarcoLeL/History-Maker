"""Lettura dei manifest IIIF del Portale Antenati.

Tutte le funzioni qui dentro lavorano su dati Python semplici e non fanno
I/O: sono la parte della pipeline che si puo' collaudare offline, e sono
anche l'unica descrizione formale che abbiamo del formato del portale.

Formato osservato (IIIF Presentation 2.1)::

    {
      "metadata": [
        {"label": "Contesto archivistico",
         "value": "Stato civile italiano/Chieti/Torrebruna"},
        {"label": "Titolo",    "value": "1866"},
        {"label": "Tipologia", "value": "Nati"}
      ],
      "sequences": [{"canvases": [
        {"@id": ".../an_ua19944535/canvas/p1",
         "label": "0001",
         "images": [{"resource": {
             "@id": "https://iiif-antenati.cultura.gov.it/iiif/2/AbCdEfG/full/full/0/default.jpg",
             "format": "image/jpeg"}}]}
      ]}]
    }
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from history_maker.errors import ManifestError

# La pagina della galleria assegna l'URL del manifest a una variabile
# JavaScript ``manifestId``. Il pattern e' volutamente permissivo sul
# contenuto dell'URL per sopravvivere ai cambi di dominio del portale
# (san.beniculturali.it -> cultura.gov.it e' gia' successo una volta).
_MANIFEST_KEYWORD = "manifestId"
_MANIFEST_URL_PATTERN = r"""['"](https?://[^'"]+)['"]"""

# Ripiego: alcune pagine espongono il manifest come link visibile
# ("IIIF manifest", in fondo al pannello di sinistra) invece che come
# variabile JS.
_MANIFEST_HREF_PATTERN = r"""['"](https?://[^'"\s]+/manifest)['"]"""

# Nel manifest le immagini sono dichiarate con la sintassi legacy
# ``/full/full/0/``, che il server oggi rifiuta con 403 (come pure
# ``/full/max/0/``). ``pct:100`` e' la variante ancora servita a piena
# risoluzione.
_FULL_SIZE_TEMPLATE = "/full/full/0/"

META_CONTESTO = "Contesto archivistico"
META_TITOLO = "Titolo"
META_TIPOLOGIA = "Tipologia"


def normalizza(testo: str) -> str:
    """Minuscolo, senza accenti e senza spazi ai bordi.

    Serve a confrontare "Torrebruna" con "TORREBRUNA" e "Sant'Angelo" con
    "Sant`Angelo" senza dipendere da come il portale scrive i toponimi.
    """
    scomposto = unicodedata.normalize("NFKD", testo)
    senza_accenti = "".join(c for c in scomposto if not unicodedata.combining(c))
    return senza_accenti.casefold().strip()


def is_manifest_url(url: str) -> bool:
    """Vero se l'URL punta direttamente a un manifest IIIF."""
    return urlsplit(url).path.rstrip("/").endswith("/manifest")


def estrai_ark_id(url: str) -> str | None:
    """Restituisce il token ``an_ua...`` contenuto nell'URL, se c'e'.

    ``https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x``
    produce ``an_ua19944535``.
    """
    match = re.search(r"an_\w+", url)
    return match.group(0) if match else None


def estrai_archive_id(url: str) -> str:
    """Restituisce l'identificativo numerico dell'unita' archivistica.

    Gli URL delle gallerie contengono almeno due numeri: ``12657`` (il
    NAAN dell'ark, uguale per tutto il portale) e l'ID dell'unita'.
    """
    numeri = re.findall(r"(\d+)", url)
    if len(numeri) < 2:
        raise ManifestError(f"Impossibile ricavare l'archive ID da {url}")
    return numeri[1]


def estrai_manifest_url(html: str, url_sorgente: str) -> str:
    """Ricava l'URL del manifest dall'HTML della pagina di galleria."""
    riga = next((r for r in html.splitlines() if _MANIFEST_KEYWORD in r), None)
    if riga:
        match = re.search(_MANIFEST_URL_PATTERN, riga)
        if match:
            return match.group(1)
    # Ripiego sul link visibile prima di dichiarare la pagina illeggibile.
    match = re.search(_MANIFEST_HREF_PATTERN, html)
    if match:
        return match.group(1)
    raise ManifestError(f"Nessun manifest IIIF trovato in {url_sorgente}")


def metadato(manifest: dict[str, Any], etichetta: str, default: str | None = None) -> str:
    """Valore della voce ``etichetta`` nei metadati del manifest.

    I metadati IIIF sono una lista di dizionari ``{label, value}``; il
    valore puo' essere una stringa oppure, nei manifest multilingua, una
    lista di dizionari ``{@language, @value}``.
    """
    voci = manifest.get("metadata")
    if not isinstance(voci, list):
        if default is not None:
            return default
        raise ManifestError("Il manifest non ha il campo 'metadata'")
    for voce in voci:
        if isinstance(voce, dict) and voce.get("label") == etichetta:
            return _valore_testuale(voce.get("value"))
    if default is not None:
        return default
    raise ManifestError(f"Metadato '{etichetta}' assente dal manifest")


def _valore_testuale(valore: Any) -> str:
    if isinstance(valore, str):
        return valore
    if isinstance(valore, list) and valore:
        return _valore_testuale(valore[0])
    if isinstance(valore, dict):
        return str(valore.get("@value", ""))
    return "" if valore is None else str(valore)


def canvases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Elenco dei canvas (una pagina digitalizzata ciascuno)."""
    try:
        elenco = manifest["sequences"][0]["canvases"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ManifestError("Il manifest non ha 'sequences[0].canvases'") from exc
    if not elenco:
        raise ManifestError("Il manifest non contiene canvas")
    return elenco


def url_immagine(canvas: dict[str, Any]) -> str:
    """URL dell'immagine dichiarato da un canvas."""
    try:
        return canvas["images"][0]["resource"]["@id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ManifestError("Il canvas non ha 'images[0].resource.@id'") from exc


def con_dimensione(url: str, lato_max: int = 0) -> str:
    """Riscrive l'URL IIIF per chiedere una dimensione servibile.

    ``lato_max == 0`` chiede la piena risoluzione (``pct:100``), qualsiasi
    altro valore vincola l'immagine dentro un riquadro di quel lato.
    """
    dimensione = f"/full/!{lato_max},{lato_max}/0/" if lato_max > 0 else "/full/pct:100/0/"
    return url.replace(_FULL_SIZE_TEMPLATE, dimensione)


def _anni_nel_titolo(titolo: str) -> list[int]:
    return [int(m.group(1)) for m in re.finditer(r"\b(1[7-9]\d{2}|20\d{2})\b", titolo)]


def estrai_anno(titolo: str) -> int | None:
    """Anno di apertura del registro, letto dal titolo.

    Il campo "Titolo" e' di solito il solo anno ("1866") ma capita di
    trovare intervalli ("1866-1870") o diciture piu' lunghe; in quei casi
    fa fede il primo anno, che e' quello di apertura del registro.
    """
    anni = _anni_nel_titolo(titolo)
    return anni[0] if anni else None


def estrai_anno_fine(titolo: str) -> int | None:
    """Anno di chiusura, quando il titolo e' un intervallo.

    **Un registro puo' coprire piu' di un anno, e ignorarlo inventa lacune
    che non esistono.** A Torrebruna dieci registri portano il titolo
    ``1813-1814``: assegnando loro il solo 1813, il 1814 risulta un anno
    senza atti — mentre i suoi atti stanno dentro quei registri, e il
    conteggio delle pagine lo conferma (43 pagine di nati contro una
    mediana di 28 l'anno, 32 di morti contro 17).

    Restituisce ``None`` per i titoli a un anno solo: quello non e' un
    intervallo e non va trattato come tale.
    """
    anni = _anni_nel_titolo(titolo)
    if len(anni) < 2:
        return None
    fine = max(anni)
    return fine if fine > anni[0] else None
