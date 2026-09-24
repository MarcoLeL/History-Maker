"""Figlio e padre con cognomi diversi: che cosa dice l'atto che li lega.

Se nell'atto i due hanno lo stesso cognome, l'errore e' nella scheda (una
fusione sbagliata, o il cognome scelto per la scheda); se l'atto scrive due
cognomi diversi, e' la trascrizione (o un nome nella casella del cognome).
"""
import collections, json, sqlite3, sys
sys.path.insert(0, "src")
from history_maker import paleografia
from history_maker.ricostruzione import lettura

S = str(__import__("pathlib").Path(__file__).resolve().parent) + "/"
c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
c.row_factory = sqlite3.Row
norma = lambda t: paleografia.normalizza(t or "")
nomi_propri = collections.Counter()
for (n,) in c.execute("SELECT nome FROM persone WHERE nome IS NOT NULL"):
    for pezzo in n.split():
        nomi_propri[norma(pezzo)] += 1
corpus = lettura.carica(c)
casi = json.load(open(S + "analisi_coniugi_cognomi.json", encoding="utf-8"))["cognomi"]
classi, esempi, risultato = collections.Counter(), collections.defaultdict(list), []
for x in casi:
    if x["classe"] == "cognome mancante":
        continue
    l = c.execute("SELECT atto FROM legami WHERE figlio=? AND genitore=? AND tipo='padre'", (x["figlio"], x["padre"])).fetchone()
    # Le righe come le vede l'ALBERO, non come le ha scritte la
    # trascrizione: fra le due ci sono le regole di menzioni.py e le
    # correzioni lette sulla pagina, e leggere 'persone' faceva contare
    # per errori cose gia' aggiustate (il «padre» che e' il nonno, il
    # nome corretto sull'immagine, il secondo nome tolto dal cognome).
    righe = {}
    for r in c.execute("SELECT p.id, m.individuo FROM persone p "
                       "JOIN menzioni m ON m.persona = p.id WHERE p.atto = ?", (l["atto"],)):
        menzione = corpus.per_id.get(r["id"])
        if menzione is not None:
            righe[r["individuo"]] = menzione
    f, p = righe.get(x["figlio"]), righe.get(x["padre"])
    ind_f = c.execute("SELECT cognome FROM individui WHERE id=?", (x["figlio"],)).fetchone()[0]
    ind_p = c.execute("SELECT cognome FROM individui WHERE id=?", (x["padre"],)).fetchone()[0]
    if f is None or p is None:
        k = "una delle due righe non e' nell'atto del legame"
    elif not f.cognome:
        k = "figlio senza cognome nell'atto"
    elif norma(f.cognome) == norma(p.cognome):
        if norma(f.cognome) == norma(ind_f):
            k = "uguali nell'atto; la scheda del PADRE porta un altro cognome"
        elif norma(p.cognome) == norma(ind_p):
            k = "uguali nell'atto; la scheda del FIGLIO porta un altro cognome (fusione)"
        else:
            k = "uguali nell'atto; tutte e due le schede portano altri cognomi"
    elif nomi_propri[norma(f.cognome)] >= 20 and nomi_propri[norma(f.cognome)] > 5 * max(1, c.execute(
            "SELECT COUNT(*) FROM persone WHERE cognome=?", (f.cognome,)).fetchone()[0]):
        k = "nel figlio un nome di battesimo nella casella del cognome"
    elif paleografia.somiglianza(norma(f.cognome), norma(p.cognome)) >= 0.8:
        k = "diversi nell'atto, ma varianti (>=0.80)"
    else:
        k = "diversi nell'atto"
    classi[k] += 1
    caso = dict(x, atto=l["atto"], figlio_atto=f and f"{f.nome} {f.cognome} ({f.ruolo})",
                padre_atto=p and f"{p.nome} {p.cognome}", scheda_figlio=ind_f, scheda_padre=ind_p, motivo=k)
    esempi[k].append(caso)
    risultato.append(caso)
json.dump(risultato, open(S + "analisi_cognomi_atto.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k, n in classi.most_common():
    print(f"{n:>4}  {k}")
for k in classi:
    print(f"\n== {k}")
    for x in esempi[k][:7]:
        print(f"   atto {x['atto']}: figlio «{x['figlio_atto']}» -> scheda {x['scheda_figlio']} | padre «{x['padre_atto']}» -> scheda {x['scheda_padre']}")
