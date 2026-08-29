"""Finto ``claude`` che risponde in UTF-8 con caratteri accentati.

Gli atti di stato civile italiani sono pieni di accenti: se l'output del
processo figlio viene decodificato con la codepage del sistema invece che
in UTF-8, la trascrizione arriva storpiata senza che nulla segnali un
errore. Questo finto serve a rendere visibile quella corruzione.
"""

import json
import sys

TESTO = "L'anno milleottocentonove, addì ventisette, città di Torrebruna, età trentadué"

sys.stdout.buffer.write(
    json.dumps(
        {
            "is_error": False,
            "subtype": "success",
            "result": TESTO,
            "usage": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
)
