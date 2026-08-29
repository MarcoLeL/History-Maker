"""Finto ``claude`` che restituisce il prompt esattamente come l'ha ricevuto.

Serve a verificare il *trasporto* del prompt, non la sua composizione: il
bug che ha originato questo finto stava fra i due: il prompt era corretto
e arrivava mutilato, perche' su Windows passava da ``cmd.exe``, che si
ferma al primo ritorno a capo e tratta ``>`` come una redirezione.

Rende anche il prompt di sistema, che arriva da ``--system-prompt-file``.
"""

import json
import sys
from pathlib import Path

prompt = sys.stdin.read()

argomenti = sys.argv[1:]
sistema = ""
if "--system-prompt-file" in argomenti:
    percorso = argomenti[argomenti.index("--system-prompt-file") + 1]
    sistema = Path(percorso).read_text(encoding="utf-8")

sys.stdout.buffer.write(
    json.dumps(
        {
            "is_error": False,
            "subtype": "success",
            "result": json.dumps({"prompt": prompt, "sistema": sistema}, ensure_ascii=False),
            "usage": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
)
