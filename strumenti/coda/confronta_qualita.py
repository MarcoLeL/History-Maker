"""Mette a confronto due rapporti di qualita', controllo per controllo.

    python confronta_qualita.py A.md B.md
"""
import re
import sys


def leggi(percorso):
    righe = {}
    for riga in open(percorso, encoding="utf-8"):
        m = re.match(r"\| ([^|]+) \| (\w+) \| (\d+) \| (\d+) \|", riga)
        if m:
            righe[m.group(1).strip()] = (m.group(2), int(m.group(3)))
    return righe


a, b = leggi(sys.argv[1]), leggi(sys.argv[2])
totali = {}
print(f"{'controllo':<42} {'categoria':<15} {'A':>5} {'B':>5} {'B-A':>5}")
for nome in sorted(set(a) | set(b), key=lambda n: -(b.get(n, ("", 0))[1])):
    cat = (b.get(nome) or a.get(nome))[0]
    va, vb = a.get(nome, ("", 0))[1], b.get(nome, ("", 0))[1]
    totali.setdefault(cat, [0, 0])
    totali[cat][0] += va
    totali[cat][1] += vb
    if va or vb:
        print(f"{nome:<42} {cat:<15} {va:>5} {vb:>5} {vb - va:>+5}")
print()
for cat, (va, vb) in sorted(totali.items()):
    print(f"{cat:<15} A={va:>4}  B={vb:>4}  ({vb - va:+d})")
