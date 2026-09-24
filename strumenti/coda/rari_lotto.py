"""Il prossimo lotto di pagine da rileggere a occhio, in ordine di priorita'.

Stampa, per ciascuna pagina, le righe sospette (id, ruolo, atto, trascrizione,
segni) e salva l'immagine ridotta in lotto/<n>.jpg. Le pagine gia' guardate
stanno in rari_fatti.json (lista di immagini).

    python rari_lotto.py [quante] [--salta-cognomi]
"""
import json, sqlite3, sys
from pathlib import Path
from PIL import Image

S = Path(__file__).resolve().parent
PRIORITA = ("nome uguale al cognome", "nome mai visto", "senza cognome", "senza nome", "cognome mai visto")


REALI = set(open(Path(__file__).resolve().parent / "nomi_reali.txt", encoding="utf-8").read().split())     if (Path(__file__).resolve().parent / "nomi_reali.txt").exists() else set()


def grave(r):
    """Una riga e' grave se il nome e' uguale al cognome, manca, o e' un nome mai visto non reale."""
    for s in r["segni"]:
        if s.startswith(("nome uguale", "senza nome")):
            return True
        if s.startswith("nome mai visto:") and any(t not in REALI for t in s.split(":")[1].split()):
            return True
    return False


def ordine(voce):
    if any(grave(r) for r in voce["righe"]):
        return (-1, voce["immagine"])
    migliore = len(PRIORITA)
    for r in voce["righe"]:
        for s in r["segni"]:
            for i, p in enumerate(PRIORITA):
                if s.startswith(p):
                    migliore = min(migliore, i)
    return (migliore, voce["immagine"])


def main():
    quante = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    voci = json.load(open(S / "rari.json", encoding="utf-8"))
    fatti_file = S / "rari_fatti.json"
    fatti = set(json.load(open(fatti_file, encoding="utf-8"))) if fatti_file.exists() else set()
    resto = sorted((v for v in voci if v["immagine"] not in fatti), key=ordine)
    print(f"restano {len(resto)} pagine su {len(voci)}")
    (S / "lotto").mkdir(exist_ok=True)
    c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
    for v in resto[:quante]:
        im = Image.open(Path("data/immagini") / v["immagine"]).convert("L")
        w, h = im.size
        im.resize((1600, int(1600 * h / w))).save(S / "lotto" / f"{v['n']}.jpg")
        print(f"\n== pagina {v['n']}  {v['immagine']}")
        sospette = {r["persona"]: r for r in v["righe"]}
        for atto in sorted({r["atto"] for r in v["righe"]}):
            r0 = next(r for r in v["righe"] if r["atto"] == atto)
            print(f"   atto {atto} {r0['tipo']} {r0['anno']} n.{r0['numero']}")
            for pid, ruolo, nome, cognome, eta in c.execute(
                    "SELECT id, ruolo, nome, cognome, eta FROM persone WHERE atto=? AND ruolo NOT IN "
                    "('testimone','ufficiale') ORDER BY id", (atto,)):
                segno = ("  <-- " + "; ".join(sospette[pid]["segni"])) if pid in sospette else ""
                print(f"      {pid:6} {ruolo:11} {nome or ''} | {cognome or ''} | {eta or ''}{segno}")


if __name__ == "__main__":
    main()
