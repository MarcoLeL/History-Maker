"""Scarica dal portale la singola pagina che serve, quando non c'e' su disco.

Le immagini dei registri sono cinque giga: stanno fuori da git, e una
sessione che parte da un clone non le ha. Non servono tutte, pero': un giro
di coda ne guarda otto o dieci, e ognuna ha un indirizzo stabile.

Il manifest IIIF di ogni registro — quarantatre righe di JSON per pagina,
otto mega e mezzo per tutti e trecentotrentotto — porta l'URL di ogni
immagine. Qui dentro c'e' la mappa; il resto e' una GET.

    python strumenti/coda/scarica_pagina.py 992          # l'atto 992
    python strumenti/coda/scarica_pagina.py 1821-morti-17810350/0012-pag-12.jpg

Rende il percorso locale del file, scaricandolo se manca. Se c'e' gia', non
tocca niente e non chiede niente al portale.
"""
import json
import sqlite3
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RADICE / "src"))
from history_maker import download, http, iiif  # noqa: E402

IMMAGINI = RADICE / "data" / "immagini"
MANIFEST = Path(__file__).resolve().parent / "manifest"
# Il reverse proxy del portale risponde 403 a chi non somiglia a un
# browser: la sessione del progetto porta gia' gli header giusti e i
# ritentativi. Rifarli qui vorrebbe dire sbagliarli.
ATTESA_S = 120


def manifest_del_registro(registro: str) -> dict:
    """Il manifest di un registro: quello scaricato, o la copia nel repository."""
    for percorso in (IMMAGINI / registro / "manifest.json", MANIFEST / f"{registro}.json"):
        if percorso.exists():
            return json.loads(percorso.read_text(encoding="utf-8"))
    raise SystemExit(f"manifest mancante per {registro}: ne' in data/immagini ne' in {MANIFEST}")


def pagina_dell_atto(atto: int) -> str:
    """Il percorso 'registro/file.jpg' che il database attribuisce a un atto."""
    conn = sqlite3.connect(f"file:{RADICE / 'data/dataset/torrebruna.sqlite'}?mode=ro", uri=True)
    riga = conn.execute("SELECT immagine FROM atti WHERE id = ?", (atto,)).fetchone()
    if riga is None or not riga[0]:
        raise SystemExit(f"l'atto {atto} non ha un'immagine nel database")
    return riga[0]


def scarica(relativo: str, lato_max: int = 0) -> Path:
    """Porta su disco la pagina 'registro/file.jpg', se non c'e' gia'."""
    registro, _, nome = relativo.replace("\\", "/").partition("/")
    destinazione = IMMAGINI / registro / nome
    if destinazione.exists():
        return destinazione

    canvases = iiif.canvases(manifest_del_registro(registro))
    voluto = Path(nome).stem
    for indice, canvas in enumerate(canvases, start=1):
        if download.nome_pagina(canvas, indice) != voluto:
            continue
        url = iiif.con_dimensione(iiif.url_immagine(canvas), lato_max)
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        risposta = http.get(http.crea_sessione(), url, timeout=ATTESA_S)
        dati = risposta.content
        # Scrittura in due tempi: un'interruzione a meta' lascerebbe un file
        # buono a meta', e il giro dopo lo troverebbe e non lo riscaricherebbe.
        parziale = destinazione.with_suffix(destinazione.suffix + ".parziale")
        parziale.write_bytes(dati)
        parziale.replace(destinazione)
        return destinazione
    raise SystemExit(f"nel manifest di {registro} non c'e' una pagina {voluto}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    argomento = sys.argv[1]
    relativo = pagina_dell_atto(int(argomento)) if argomento.isdigit() else argomento
    print(scarica(relativo))


if __name__ == "__main__":
    main()
