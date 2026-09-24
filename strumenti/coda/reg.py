"""Registra le letture fatte a occhio e segna le pagine come guardate.

Legge da stdin righe cosi':
    P 216                                   pagina guardata (n di rari.json)
    C 5595 nome Tomaso | motivo             correzione (campo: nome/cognome/ruolo/eta)
    U 111 222 | motivo                      unione
    S 111 222 333 | motivo                  separazione (la prima resta)
"""
import json, sys
from pathlib import Path

S = Path(__file__).resolve().parent
v = json.load(open(S / "verdetti_casi.json", encoding="utf-8"))
voci = {x["n"]: x["immagine"] for x in json.load(open(S / "rari.json", encoding="utf-8"))}
ff = S / "rari_fatti.json"
fatti = set(json.load(open(ff, encoding="utf-8"))) if ff.exists() else set()
n0 = len(v)
for riga in sys.stdin.read().splitlines():
    riga = riga.strip()
    if not riga:
        continue
    testa, _, motivo = riga.partition("|")
    motivo = motivo.strip() or "Letto sulla pagina."
    parti = testa.split()
    if parti[0] == "P":
        for n in parti[1:]:
            fatti.add(voci[int(n)])
    elif parti[0] == "C":
        # Il genitore gia' morto («figlia delli furono ...») resta genitore: il
        # «fu» e' lo stato civile. Scritto nel ruolo, nella morte e nel
        # matrimonio faceva della madre la figlia di suo marito (giro R4).
        if parti[2] == "ruolo" and parti[3] in ("defunto", "defunta"):
            sys.exit(f"riga {parti[1]}: il «fu» di un genitore va in 'stato_vitale {parti[3]}', "
                     "non nel ruolo")
        v.append({"tipo": "correzione", "menzione": int(parti[1]), "campo": parti[2],
                  "valore": " ".join(parti[3:]), "motivo": motivo})
    elif parti[0] in ("U", "S"):
        v.append({"tipo": "unione" if parti[0] == "U" else "separazione",
                  "menzioni": [int(x) for x in parti[1:]], "motivo": motivo})
json.dump(v, open(S / "verdetti_casi.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(sorted(fatti), open(ff, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(f"decisioni {n0} -> {len(v)}; pagine guardate {len(fatti)}/{len(voci)}")
