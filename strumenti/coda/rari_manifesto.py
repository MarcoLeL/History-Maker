"""Le persone dai nomi che nessun altro porta: l'elenco da rileggere.

Il fattore comune dei casi segnalati (Colella Colella, Todasmi, Beppa, Ruyi,
Trofio, Patsio, Di Zio, Carmela senza cognome, Nicola padre di Filippo) e'
una riga di famiglia il cui nome o cognome, cosi' come e' letto, non si
ritrova altrove nell'archivio - o manca, o ripete il cognome nel nome. E' la
firma della lettura sbagliata: una parola vera torna, un errore di lettura no.

Si lavora sul corpus gia' corretto (le correzioni lette sulla pagina valgono),
e si scrive rari.json: una voce per pagina, con le righe da rileggere.

    python rari_manifesto.py
"""
import collections, json, re, sqlite3, sys, unicodedata
from pathlib import Path

sys.path.insert(0, "src")
from history_maker.ricostruzione import lettura  # noqa: E402

S = Path(__file__).resolve().parent
FAM = {"neonato", "neonata", "defunto", "defunta", "sposo", "sposa", "padre", "madre",
       "dichiarante", "coniuge"}
PARTICELLE = {"di", "de", "del", "della", "d", "da", "e", "fu", "la", "lo", "detto", "detta"}
RARO = 2
# Una lettura sbagliata ma COSTANTE non e' mai "mai vista": il casato Scica,
# letto Sica in tutte e ventisette le sue righe, passava la rete. Pero' resta
# un casato di poche righe e di poche schede - una famiglia sola - mentre i
# casati veri del paese (Pelliccia, Marianacci, Colella) ne hanno centinaia.
POCO_RIGHE = 30
POCO_SCHEDE = 10
# Un nome vero e un casato vero possono fare una coppia falsa: "Bonaventura
# Marianacci", padre di Vincenzo Marianacci nel 1874, e' Anacleto Marianacci
# letto male. Ne' il nome ne' il casato sono rari, e la rete non lo prendeva.
# Il segno e' un altro: in un casato grande, un battesimo che in quella
# famiglia non torna mai, portato da una scheda che vive in una riga sola.
CASATO_GRANDE = 60
PARENTELA = {"padre", "madre", "defunto", "defunta", "sposo", "sposa", "neonato", "neonata"}


def norma(s):
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s).strip()


def main():
    c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
    c.row_factory = sqlite3.Row
    corpus = lettura.carica(c)
    menzioni = list(corpus.per_id.values())
    nomi, cognomi = collections.Counter(), collections.Counter()
    for m in menzioni:
        for t in norma(m.nome).split():
            nomi[t] += 1
        if m.cognome:
            cognomi[norma(m.cognome)] += 1
    primi = collections.Counter()
    coppie = collections.Counter()
    for m in menzioni:
        parti = norma(m.nome).split()
        if parti:
            primi[parti[0]] += 1
        cognome = norma(m.cognome)
        for t in parti:
            if cognome and t not in PARTICELLE:
                coppie[(cognome, t)] += 1
    individuo = dict(c.execute("SELECT persona, individuo FROM menzioni").fetchall())
    schede_cognome = collections.defaultdict(set)
    for m in menzioni:
        if m.cognome:
            schede_cognome[norma(m.cognome)].add(individuo.get(m.id))
    quante = dict(c.execute("SELECT id, menzioni FROM individui").fetchall())
    atti = {r["id"]: r for r in c.execute("SELECT id, anno, tipo, numero_atto, immagine FROM atti")}

    per_pagina = collections.defaultdict(list)
    conta = collections.Counter()
    for m in menzioni:
        if m.ruolo not in FAM:
            continue
        nome = [t for t in norma(m.nome).split() if t not in PARTICELLE]
        cognome = norma(m.cognome)
        segni = []
        if not cognome:
            segni.append("senza cognome")
        if not nome:
            segni.append("senza nome")
        # Salvatore Salvatore esiste: e' un nome di battesimo e un casato.
        # Colella Colella no: il cognome non si da' come nome.
        if nome and cognome and cognome in nome and primi.get(cognome, 0) < 20:
            segni.append("nome uguale al cognome")
        rari = [t for t in nome if nomi[t] <= RARO]
        if rari:
            segni.append("nome mai visto: " + " ".join(rari))
        if cognome and cognomi[cognome] <= RARO:
            segni.append("cognome mai visto")
        elif (cognome and cognomi[cognome] <= POCO_RIGHE
              and len(schede_cognome[cognome]) <= POCO_SCHEDE):
            segni.append(f"casato di poche schede: {cognomi[cognome]} righe, "
                         f"{len(schede_cognome[cognome])} schede")
        # Solo le righe che portano parentela: un dichiarante isolato dal
        # nome insolito non sposta l'albero, un padre si'.
        if (cognome and nome and m.ruolo in PARENTELA
                and cognomi[cognome] >= CASATO_GRANDE
                and quante.get(individuo.get(m.id), 0) <= 1
                and all(coppie[(cognome, t)] <= 1 for t in nome)):
            segni.append("nome mai visto in questo casato")
        if not segni:
            continue
        for s in segni:
            conta[s.split(":")[0]] += 1
        a = atti.get(m.atto)
        if a is None or not a["immagine"]:
            continue
        ind = individuo.get(m.id)
        per_pagina[a["immagine"]].append(dict(
            persona=m.id, atto=m.atto, anno=a["anno"], tipo=a["tipo"], numero=a["numero_atto"],
            ruolo=m.ruolo, nome=m.nome or "", cognome=m.cognome or "", segni=segni,
            individuo=ind, menzioni_scheda=quante.get(ind)))

    voci = [dict(n=i, immagine=img, righe=righe)
            for i, (img, righe) in enumerate(sorted(per_pagina.items()), 1)]
    json.dump(voci, open(S / "rari.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    righe = [r for v in voci for r in v["righe"]]
    print("righe da rileggere:", len(righe))
    print("  di schede con una sola menzione:", sum(1 for r in righe if r["menzioni_scheda"] == 1))
    print("pagine:", len(voci))
    for s, n in conta.most_common():
        print(f"  {s:25} {n}")


if __name__ == "__main__":
    main()
