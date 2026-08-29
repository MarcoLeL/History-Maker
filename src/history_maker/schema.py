"""Schema JSON degli atti estratti da una pagina di registro.

Serve due volte: come ``output_config.format`` nella richiesta a Claude,
cosi' che la risposta sia JSON valido e conforme senza doverla riparare,
e come contratto per la fase di aggregazione.

Ogni campo ammette ``null``: un atto ottocentesco puo' benissimo non
dichiarare la professione della madre o l'eta' di un testimone, e
inventare un valore per riempire una casella sarebbe il peggior esito
possibile per una ricerca storica.
"""

from __future__ import annotations

from typing import Any


def _testo() -> dict[str, Any]:
    return {"type": ["string", "null"]}


PERSONA = {
    "type": "object",
    "properties": {
        "ruolo": _testo(),
        "nome": _testo(),
        "cognome": _testo(),
        "eta": _testo(),
        "professione": _testo(),
        "residenza": _testo(),
        "stato_vitale": {
            "type": ["string", "null"],
            "description": "vivente / defunto, quando l'atto lo precisa (es. 'fu Giuseppe')",
        },
        "note": _testo(),
    },
    "required": [
        "ruolo", "nome", "cognome", "eta", "professione",
        "residenza", "stato_vitale", "note",
    ],
    "additionalProperties": False,
}

ATTO = {
    "type": "object",
    "properties": {
        "numero_atto": _testo(),
        "tipo": {
            "type": ["string", "null"],
            "description": "nascita, morte, matrimonio, pubblicazione, cittadinanza, altro",
        },
        "data_atto": {
            "type": ["string", "null"],
            "description": "data di registrazione, in formato AAAA-MM-GG quando ricavabile",
        },
        "data_evento": {
            "type": ["string", "null"],
            "description": "data della nascita/morte/matrimonio, se diversa da quella dell'atto",
        },
        "ora_evento": _testo(),
        "luogo": {
            "type": ["string", "null"],
            "description": "comune o localita' nominata nell'atto (casa, contrada, chiesa)",
        },
        "persone": {"type": "array", "items": PERSONA},
        "testo_integrale": {
            "type": ["string", "null"],
            "description": "trascrizione diplomatica dell'atto, che conserva grafia e formule originali",
        },
        "parti_illeggibili": {
            "type": "array",
            "items": {"type": "string"},
            "description": "punti in cui la lettura non e' sicura, descritti brevemente",
        },
        "affidabilita": {
            "type": ["string", "null"],
            "description": "alta, media o bassa, secondo quanto la scrittura e' leggibile",
        },
    },
    "required": [
        "numero_atto", "tipo", "data_atto", "data_evento", "ora_evento",
        "luogo", "persone", "testo_integrale", "parti_illeggibili", "affidabilita",
    ],
    "additionalProperties": False,
}

# Le pagine di indice elencano i cognomi degli atti di quell'anno: sono
# una SECONDA LETTURA indipendente degli stessi nomi, ed e' su questa che
# si regge la verifica incrociata della fase di revisione. Trascriverle
# come dati, e non come semplice annotazione, non costa nulla in piu' e
# raddoppia le occasioni di accorgersi di un errore.
VOCE_INDICE = {
    "type": "object",
    "properties": {
        "cognome": _testo(),
        "nome": _testo(),
        "numero_atto": _testo(),
    },
    "required": ["cognome", "nome", "numero_atto"],
    "additionalProperties": False,
}

PAGINA = {
    "type": "object",
    "properties": {
        "tipo_pagina": {
            "type": ["string", "null"],
            "description": "atti, copertina, indice, frontespizio, bianca, allegato, altro",
        },
        "anno_indicato": _testo(),
        "comune_indicato": _testo(),
        "atti": {"type": "array", "items": ATTO},
        "voci_indice": {
            "type": "array",
            "items": VOCE_INDICE,
            "description": "solo per le pagine di indice: le voci elencate",
        },
        "osservazioni": {
            "type": ["string", "null"],
            "description": "note del trascrittore: stato di conservazione, annotazioni a margine, timbri",
        },
    },
    "required": [
        "tipo_pagina", "anno_indicato", "comune_indicato",
        "atti", "voci_indice", "osservazioni",
    ],
    "additionalProperties": False,
}

FORMATO_RISPOSTA = {"type": "json_schema", "schema": PAGINA}


# --- validazione -----------------------------------------------------------
#
# Con l'API lo schema viaggia in ``output_config.format`` e la risposta e'
# conforme per costruzione. Claude Code non offre quel vincolo, quindi la
# conformita' va verificata qui: le chiavi mancanti vengono aggiunte a
# null, quelle inattese scartate, e i tipi sbagliati normalizzati.

_CAMPI_PERSONA = tuple(PERSONA["properties"])
_CAMPI_ATTO = tuple(ATTO["properties"])
_CAMPI_PAGINA = tuple(PAGINA["properties"])


class PaginaNonValida(ValueError):
    """La risposta non e' interpretabile come una pagina di registro."""


def _stringa(valore: Any) -> str | None:
    if valore is None:
        return None
    if isinstance(valore, str):
        return valore.strip() or None
    return str(valore)


def _lista_stringhe(valore: Any) -> list[str]:
    if valore is None:
        return []
    if isinstance(valore, str):
        return [valore] if valore.strip() else []
    if isinstance(valore, list):
        return [str(v).strip() for v in valore if str(v).strip()]
    return []


def valida_persona(dati: Any) -> dict[str, Any]:
    grezzo = dati if isinstance(dati, dict) else {}
    return {campo: _stringa(grezzo.get(campo)) for campo in _CAMPI_PERSONA}


def valida_atto(dati: Any) -> dict[str, Any]:
    grezzo = dati if isinstance(dati, dict) else {}
    atto = {campo: _stringa(grezzo.get(campo)) for campo in _CAMPI_ATTO}
    persone = grezzo.get("persone")
    atto["persone"] = [valida_persona(p) for p in persone] if isinstance(persone, list) else []
    atto["parti_illeggibili"] = _lista_stringhe(grezzo.get("parti_illeggibili"))
    return atto


def valida_voce_indice(dati: Any) -> dict[str, Any]:
    grezzo = dati if isinstance(dati, dict) else {}
    return {campo: _stringa(grezzo.get(campo)) for campo in VOCE_INDICE["properties"]}


def valida_pagina(dati: Any) -> dict[str, Any]:
    """Normalizza la risposta del modello nella forma attesa dallo schema.

    Solleva :class:`PaginaNonValida` solo quando non c'e' proprio nulla da
    normalizzare: una pagina senza atti e' un risultato legittimo (una
    copertina, un indice, una pagina bianca), non un errore.
    """
    if not isinstance(dati, dict):
        raise PaginaNonValida(f"attesa una struttura, ricevuto {type(dati).__name__}")

    pagina = {campo: _stringa(dati.get(campo)) for campo in _CAMPI_PAGINA}
    atti = dati.get("atti")
    pagina["atti"] = [valida_atto(a) for a in atti] if isinstance(atti, list) else []
    voci = dati.get("voci_indice")
    pagina["voci_indice"] = (
        [valida_voce_indice(v) for v in voci] if isinstance(voci, list) else []
    )
    return pagina
