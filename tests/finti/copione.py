"""Finto ``claude`` che risponde a copione e annota ogni invocazione.

Legge da ``FINTO_COPIONE`` la lista delle risposte, una per chiamata, e
scrive una riga in ``FINTO_CHIAMATE`` a ogni invocazione: e' cosi' che i
test contano le chiamate senza doverle dedurre dal prompt, che contiene
ritorni a capo.

Esaurito il copione ripete l'ultima risposta, cosi' un test che programma
una sola risposta non deve prevedere quante volte verra' chiamata.
"""

import json
import os
from pathlib import Path

chiamate = Path(os.environ["FINTO_CHIAMATE"])
with chiamate.open("a", encoding="utf-8") as registro:
    registro.write("chiamata\n")

fatte = len([r for r in chiamate.read_text(encoding="utf-8").splitlines() if r.strip()])
copione = json.loads(Path(os.environ["FINTO_COPIONE"]).read_text(encoding="utf-8"))

print(json.dumps(copione[min(fatte - 1, len(copione) - 1)]))
