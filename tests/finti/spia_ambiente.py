"""Finto ``claude`` che annota se vede una chiave API nel proprio ambiente.

Scrive in ``FINTO_SPIA`` il valore di ``ANTHROPIC_API_KEY``, oppure
``ASSENTE``. E' la verifica end-to-end che la pipeline non possa finire
fatturata a consumo: non basta che il codice tolga la variabile, deve
mancare davvero al processo figlio.
"""

import os
from pathlib import Path

Path(os.environ["FINTO_SPIA"]).write_text(
    os.environ.get("ANTHROPIC_API_KEY", "ASSENTE"), encoding="utf-8"
)

print('{"is_error":false,"subtype":"success","result":"[]","usage":{}}')
