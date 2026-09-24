"""L'immagine di un atto, intera o ritagliata, pronta da guardare.

    python strumenti/coda/pagina.py 992                  # la pagina intera
    python strumenti/coda/pagina.py 992 820 280 1600 800 # un ritaglio, in scala 1600

Le coordinate sono nella scala 1600 (la pagina intera viene sempre ridotta a
1600 pixel di larghezza): quelle lette su una resa precedente valgono anche
per il ritaglio. Se l'immagine non e' su disco la scarica dal portale.
"""
import sqlite3
import sys
from pathlib import Path

from PIL import Image, ImageOps

import scarica_pagina

S = str(Path(__file__).resolve().parent) + "/"
c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
a = int(sys.argv[1])
p = c.execute("select immagine from atti where id=?", (a,)).fetchone()[0]
# In un clone senza data/immagini la pagina arriva adesso, una sola.
percorso = scarica_pagina.scarica(p)
im = Image.open(percorso).convert("L")
w, h = im.size
f = w / 1600
if len(sys.argv) > 2:
    x0, y0, x1, y1 = [int(v) for v in sys.argv[2:6]]
    im = ImageOps.autocontrast(im.crop((int(x0 * f), int(y0 * f), int(x1 * f), int(y1 * f))), cutoff=1)
    k = min(2.0, 1600 / im.width)
    im = im.resize((int(im.width * k), int(im.height * k)))
    out = S + f"p_{a}_z.jpg"
else:
    im = im.resize((1600, int(1600 * h / w)))
    out = S + f"p_{a}.jpg"
im.save(out)
print(p, out)
