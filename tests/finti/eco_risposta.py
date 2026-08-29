"""Finto ``claude`` che stampa sempre la stessa risposta.

Il file da stampare arriva nella variabile d'ambiente ``FINTO_RISPOSTA``.
Serve ai test che guardano come viene letta una singola risposta, non
come vengono concatenate piu' invocazioni.
"""

import os
import sys
from pathlib import Path

sys.stdout.write(Path(os.environ["FINTO_RISPOSTA"]).read_text(encoding="utf-8"))
