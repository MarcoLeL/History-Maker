"""Le due impossibilita' dell'Ottocento, misurate sull'albero scritto.

1. Chi ha piu' di un coniuge: quanti, e perche' (coniuge spezzato in due,
   due matrimoni negli stessi anni, primo coniuge ancora vivo, vedovanza).
2. I figli con un cognome diverso da quello del padre: variante di lettura,
   lettura lontana, o un altro casato (padre sbagliato).
"""
import collections, json, sqlite3, sys
sys.path.insert(0, "src")
from history_maker import nomi, paleografia

S = str(__import__("pathlib").Path(__file__).resolve().parent) + "/"
c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
c.row_factory = sqlite3.Row
ind = {r["id"]: dict(r) for r in c.execute(
    "SELECT id, nome, cognome, sesso, anno_nascita, nascita_origine, anno_morte, anno_primo, anno_ultimo, "
    "menzioni, varianti_cognome FROM individui")}
unioni = [dict(r) for r in c.execute("SELECT * FROM unioni")]
legami = [dict(r) for r in c.execute("SELECT * FROM legami")]
norma = lambda t: paleografia.normalizza(t or "")

genitori = collections.defaultdict(dict)
figli_di = collections.defaultdict(set)
for l in legami:
    genitori[l["figlio"]][l["tipo"]] = l["genitore"]
    figli_di[l["genitore"]].add(l["figlio"])
coniugi = collections.defaultdict(dict)
for u in unioni:
    coniugi[u["marito"]].setdefault(u["moglie"], []).append((u["anno"], u["origine"]))
    coniugi[u["moglie"]].setdefault(u["marito"], []).append((u["anno"], u["origine"]))


def etichetta(x):
    i = ind[x]
    vita = f"n.{i['anno_nascita'] or '?'}" + (f" m.{i['anno_morte']}" if i["anno_morte"] else "")
    return f"[{x}] {i['nome']} {i['cognome']} {vita} ({i['menzioni']} menz.)"


def periodo(p, s):
    """Gli anni in cui la coppia e' documentata **viva e insieme**.

    Non gli anni degli atti che la nominano: un'unione dedotta dai figli
    porta l'anno dell'atto che li lega, e l'atto puo' essere il
    matrimonio di un figlio cinquant'anni dopo, o la sua morte. Cosi'
    Angela Pizzi, morta nel 1790 e nominata nel matrimonio del 1820 di
    suo figlio, risultava moglie di Vincenzo Pelliccia fino al 1820, e si
    accavallava con la seconda moglie. Contano il matrimonio, che e'
    l'anno vero, e la nascita dei figli.
    """
    anni = [a for a, o in coniugi[p][s] if a and o == "matrimonio"]
    for f in figli_di[p] & figli_di[s]:
        if ind[f]["anno_nascita"]:
            anni.append(ind[f]["anno_nascita"])
    return (min(anni), max(anni)) if anni else None


def stesso_nome(a, b):
    na, nb = norma(ind[a]["nome"]), norma(ind[b]["nome"])
    return bool(na and nb) and nomi.somiglianza_nome(na, nb) >= 0.8


GRAVITA = ["coniuge doppio (stesso nome)", "matrimoni negli stessi anni",
           "primo coniuge morto dopo il secondo matrimonio",
           "successivi, morte del primo non documentata", "vedovanza documentata", "senza date"]
classi, esempi = collections.Counter(), collections.defaultdict(list)
per_numero = collections.Counter()
casi_coniugi = []
for p, cs in coniugi.items():
    per_numero[len(cs)] += 1
    if len(cs) < 2:
        continue
    lista = sorted(cs, key=lambda s: (periodo(p, s) or (9999, 9999))[0])
    peggiore, dettaglio = len(GRAVITA) - 1, ""
    for i, a in enumerate(lista):
        for b in lista[i + 1:]:
            pa, pb = periodo(p, a), periodo(p, b)
            # La vedovanza documentata viene prima del nome: nel paese ci
            # si risposava anche con un'omonima della prima moglie - spesso
            # la cognata - e l'archivio lo dimostra (Nicola Chielli, vedovo
            # di Maria Domenica Desiderio nel 1875, sposa Maria Domenica
            # Mastrovincenzo nel 1876). Chiamarli «coniuge doppio» metteva
            # fra le persone da ricomporre due matrimoni veri.
            if pa and pb and ind[a]["anno_morte"] and ind[a]["anno_morte"] <= pb[0]:
                g = 4
            elif stesso_nome(a, b):
                g = 0
            elif pa and pb and pa[1] >= pb[0] and pb[1] >= pa[0]:
                g = 1
            elif pa and pb and ind[a]["anno_morte"] and ind[a]["anno_morte"] > pb[0]:
                g = 2
            elif pa and pb:
                g = 3
            else:
                g = 5
            if g < peggiore or not dettaglio:
                peggiore = min(peggiore, g)
                dettaglio = f"{etichetta(a)} {pa} / {etichetta(b)} {pb}"
    classe = GRAVITA[peggiore]
    classi[classe] += 1
    caso = dict(persona=p, chi=etichetta(p), classe=classe, coniugi=[etichetta(s) for s in lista],
                periodi=[periodo(p, s) for s in lista], dettaglio=dettaglio)
    casi_coniugi.append(caso)
    esempi[classe].append(caso)

print("persone per numero di coniugi:", dict(sorted(per_numero.items())))
print("con piu' di un coniuge:", sum(classi.values()), "(ogni coppia conta da tutti e due i lati)")
for k in GRAVITA:
    print(f"  {classi[k]:>4}  {k}")

# --- 2. figli con un cognome diverso dal padre --------------------------------
cl2, es2, casi_cognomi = collections.Counter(), collections.defaultdict(list), []
for f, g in genitori.items():
    padre = g.get("padre")
    if not padre:
        continue
    cf, cp = norma(ind[f]["cognome"]), norma(ind[padre]["cognome"])
    if not cf or not cp:
        cl2["cognome mancante"] += 1
        continue
    if cf == cp:
        continue
    sim = paleografia.somiglianza(cf, cp)
    madre = g.get("madre")
    della_madre = madre and norma(ind[madre]["cognome"]) == cf
    if della_madre:
        k = "cognome della madre"
    elif sim >= 0.8:
        k = "variante di lettura (>=0.80)"
    elif sim >= 0.5:
        k = "lettura lontana (0.50-0.80)"
    else:
        k = "altro casato (<0.50)"
    cl2[k] += 1
    caso = dict(figlio=f, padre=padre, madre=madre, classe=k, sim=round(sim, 2),
                chi=etichetta(f), del_padre=etichetta(padre), della_madre=etichetta(madre) if madre else "")
    casi_cognomi.append(caso)
    es2[k].append(caso)
print("\nfigli con padre:", sum(1 for g in genitori.values() if g.get("padre")))
for k, n in cl2.most_common():
    print(f"  {n:>4}  {k}")

json.dump(dict(coniugi=casi_coniugi, cognomi=casi_cognomi), open(S + "analisi_coniugi_cognomi.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
for k in GRAVITA[:4]:
    print(f"\n== {k}")
    for x in esempi[k][:8]:
        print("  ", x["chi"], "|", x["dettaglio"])
for k in ("altro casato (<0.50)", "lettura lontana (0.50-0.80)", "variante di lettura (>=0.80)", "cognome della madre"):
    print(f"\n== {k}")
    for x in es2[k][:10]:
        print("  ", x["chi"], "<- padre", x["del_padre"], "| madre", x["della_madre"])
