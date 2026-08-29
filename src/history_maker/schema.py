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
        "osservazioni": {
            "type": ["string", "null"],
            "description": "note del trascrittore: stato di conservazione, annotazioni a margine, timbri",
        },
    },
    "required": ["tipo_pagina", "anno_indicato", "comune_indicato", "atti", "osservazioni"],
    "additionalProperties": False,
}

FORMATO_RISPOSTA = {"type": "json_schema", "schema": PAGINA}
