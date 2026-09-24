"""Le letture sulla pagina dei casi di coniugi e cognomi, messe nel registro.

Legge verdetti_casi.json, una voce per caso letto:

  {"tipo": "unione", "menzioni": [idA, idB], "motivo": "..."}
      le due righe sono la stessa persona (il coniuge letto male);
  {"tipo": "separazione", "menzioni": [resta, stacca...], "motivo": "..."}
      le righe staccate non sono di quella persona;
  {"tipo": "correzione", "menzione": id, "campo": "cognome", "valore": "...", "motivo": "..."}
      la riga dice un'altra cosa sulla pagina.

Le decisioni vanno nel registro come prese guardando l'immagine: la
ricostruzione le riapplica a ogni giro (``registro.imposizioni`` e
``registro.correzioni``). Una voce gia' registrata non si ripete.

    python applica_casi.py [--prova]
"""
import json, sqlite3, sys

sys.path.insert(0, "src")
from history_maker.ricostruzione import registro  # noqa: E402

S = str(__import__("pathlib").Path(__file__).resolve().parent) + "/"
prova = "--prova" in sys.argv
voci = json.load(open(S + "verdetti_casi.json", encoding="utf-8"))
conn = sqlite3.connect("data/dataset/torrebruna.sqlite", timeout=120)
conn.row_factory = sqlite3.Row
# Un database appena rifatto dalle trascrizioni, in un clone nuovo, non ha
# ancora il registro: la fase 'dataset' lo crea solo quando ne ha uno vecchio
# da rimettere. Lo schema qui sotto azzera anche le tabelle ricostruite, quindi
# si lancia solo quando il registro manca, cioe' quando non c'e' niente da perdere.
if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='decisioni'").fetchone():
    from history_maker.ricostruzione import modello  # noqa: E402
    conn.executescript(modello.SCHEMA_SQL)
# Le decisioni prese dalle date portano 'claude': senza anche quelle qui,
# ogni giro le registrava un'altra volta.
# Una correzione e' la stessa solo se tocca lo stesso campo con lo stesso
# valore: la stessa riga puo' averne due (nome e cognome).
def registrate(solo_attive: bool) -> set:
    sql = ("SELECT azione, entita, evidenze FROM decisioni "
           "WHERE decisore IN ('claude-immagine', 'claude', 'gemini-rilettura')")
    if solo_attive:
        sql += " AND id NOT IN (SELECT disfa FROM decisioni WHERE disfa IS NOT NULL)"
    return {(r["azione"], r["entita"], r["evidenze"] if r["azione"] == "correzione" else "")
            for r in conn.execute(sql)}


# Una decisione superata resta superata: la voce del file che l'aveva
# prodotta non la rimette in piedi al giro dopo. Succedeva: la separazione
# delle tre Maria Mastrovincenzo, superata a mano, e' tornata nel registro
# al giro seguente. Per rimettere davvero una decisione superata la voce
# porta "ripristina": true.
tutte, attive = registrate(False), registrate(True)
# Le decisioni gia' superate: una voce che porta "disfa" non va riscritta
# a ogni giro, o il registro si riempie di revoche identiche.
gia_disfatte = {r[0] for r in conn.execute(
    "SELECT disfa FROM decisioni WHERE disfa IS NOT NULL")}
fatte = {"unione": 0, "separazione": 0, "correzione": 0, "gia'": 0}
# Per le correzioni vince l'ultima (registro.correzioni): la riga 830 letta
# Gelsomino, poi Pelsamino, poi di nuovo Gelsomino. L'ultima voce del file su
# una (riga, campo) si confronta col valore IN VIGORE, non con la storia: la
# lettura «Gelsomino» c'era gia', e il controllo dei doppioni la saltava,
# lasciando in vigore Pelsamino. Le voci di mezzo restano storia, e contano
# come fatte se c'e' la stessa correzione identica (non un pezzo di un'altra:
# «nome=Maria» non e' «nome=Maria Vincenza»).
in_vigore = registro.correzioni(conn)
conn.row_factory = sqlite3.Row
ultima_voce = {(x["menzione"], x["campo"]): i for i, x in enumerate(voci) if x["tipo"] == "correzione"}
# Una voce gia' scritta si riconosce da cio' che dice - azione, righe e
# motivo - prima di ogni altro controllo: senza, al secondo passaggio una voce
# che porta "disfa" andava a cercare la decisione da superare e trovava quella
# scritta da una voce PIU' AVANTI nel file.
scritte = {(r["azione"], r["entita"], r["motivo"]) for r in conn.execute(
    "SELECT azione, entita, motivo FROM decisioni "
    "WHERE decisore IN ('claude-immagine', 'claude', 'gemini-rilettura')")}
posizione = {}
for i, x in enumerate(voci):
    ent = json.dumps(x["menzioni"] if x["tipo"] != "correzione" else [x["menzione"]])
    posizione.setdefault((x["tipo"], ent, x["motivo"]), i)
for i_voce, voce in enumerate(voci):
    if voce["tipo"] in ("unione", "separazione"):
        entita = tuple(voce["menzioni"])
    else:
        entita = (voce["menzione"],)
    azione = voce["tipo"]
    if (azione, json.dumps(list(entita)), voce["motivo"]) in scritte:
        fatte["gia'"] += 1
        continue
    evidenze = (f"{voce['campo']}={voce['valore']}",) if azione == "correzione" else ()
    chiave_evidenze = evidenze[0] if evidenze else ""
    # Senza "ripristina" contano anche le decisioni superate: una
    # decisione disfatta a mano non torna da sola al giro dopo.
    # Una voce che porta "disfa" supera una decisione vecchia: non e' un
    # doppione di se stessa, e va scritta anche se una decisione uguale
    # esiste gia' (quella che si sta superando ne e' spesso l'opposto).
    # 'disfa' porta l'id della decisione nel database in cui la voce e' nata.
    # In un database rifatto dalle trascrizioni gli id sono altri: la
    # decisione da superare si ritrova dal contenuto (l'azione contraria sulle
    # stesse righe, scritta poco prima da una voce di questo file). Se non c'e'
    # affatto - era dell'algoritmo, o di un registro che qui non esiste - non
    # c'e' niente da superare, e la voce vale come decisione semplice.
    # Un id che esiste non basta: nel database nuovo lo stesso numero puo'
    # essere di un'altra decisione (la 2418 era un'unione, qui e' una correzione).
    contraria = {"unione": "separazione", "separazione": "unione"}.get(azione, "correzione")
    bersaglio = conn.execute("SELECT azione, entita FROM decisioni WHERE id = ?",
                             (voce["disfa"],)).fetchone() if voce.get("disfa") else None
    if voce.get("disfa") and (bersaglio is None or bersaglio["azione"] != contraria
                              or sorted(json.loads(bersaglio["entita"])) != sorted(entita)):
        # Solo decisioni nate PRIMA di questa voce: una voce piu' avanti nel
        # file puo' superare questa, non esserne superata.
        candidate = [r for r in conn.execute(
            "SELECT id, entita, motivo FROM decisioni WHERE azione = ? AND decisore IN "
            "('claude-immagine', 'claude', 'gemini-rilettura') AND id NOT IN "
            "(SELECT disfa FROM decisioni WHERE disfa IS NOT NULL) ORDER BY id DESC",
            (contraria,)) if sorted(json.loads(r["entita"])) == sorted(entita)
            and posizione.get((contraria, r["entita"], r["motivo"]), -1) < i_voce]
        voce = {**voce, "disfa": candidate[0]["id"] if candidate else None}
    if voce.get("disfa") in gia_disfatte:
        fatte["gia'"] += 1
        continue
    # Disfare una decisione vuol dire scrivere l'azione CONTRARIA, come fa
    # registro.disfa: una revoca scritta con la stessa azione annulla la
    # decisione vecchia e ne rimette una identica al suo posto, perche'
    # registro.imposizioni riapplica anche la revoca. E' successo il 20
    # settembre con sette revoche di separazioni: le righe restavano
    # divise, e i giri seguenti lo segnalavano come «una decisione presa
    # sulla pagina le tiene separate».
    if voce.get("disfa"):
        vecchia = conn.execute(
            "SELECT azione FROM decisioni WHERE id = ?", (voce["disfa"],)).fetchone()
        if vecchia is None:
            raise SystemExit(f"la decisione {voce['disfa']} non esiste")
        contraria = {"unione": "separazione", "separazione": "unione"}.get(
            vecchia["azione"], "correzione")
        if azione != contraria:
            raise SystemExit(
                f"per disfare la {voce['disfa']} ({vecchia['azione']}) ci vuole "
                f"una '{contraria}', non una '{azione}'")
    # Una voce che porta "disfa" supera una decisione vecchia: non e' un
    # doppione di se stessa, e va scritta anche se una decisione uguale
    # esiste gia' (quella che si sta superando ne e' spesso l'opposto).
    gia = () if voce.get("disfa") else (attive if voce.get("ripristina") else tutte)
    if azione == "correzione" and not voce.get("disfa"):
        chiave = (voce["menzione"], voce["campo"])
        if ultima_voce[chiave] == i_voce:
            doppione = in_vigore.get(chiave) == voce["valore"]
        else:
            doppione = any(g[0] == azione and g[1] == json.dumps(list(entita))
                           and chiave_evidenze in (g[2] or "").split(" | ") for g in gia)
    else:
        doppione = (azione, json.dumps(list(entita)), chiave_evidenze) in gia
    if doppione:
        fatte["gia'"] += 1
        continue
    if not prova:
        # Le decisioni prese dalle date e non dalla pagina portano il loro
        # decisore ('claude'): chi rilegge il registro deve saperlo.
        registro.annota(conn, azione, entita, voce["motivo"], confidenza=1.0,
                        decisore=voce.get("decisore", "claude-immagine"), evidenze=evidenze,
                        disfa=voce.get("disfa"))
    # Il file stesso porta doppioni (la stessa lettura rifatta in due giri):
    # su un registro vuoto le voci scritte adesso contano come gia' fatte.
    tutte.add((azione, json.dumps(list(entita)), chiave_evidenze))
    attive.add((azione, json.dumps(list(entita)), chiave_evidenze))
    if voce.get("disfa"):
        gia_disfatte.add(voce["disfa"])
    if azione == "correzione":
        in_vigore[(voce["menzione"], voce["campo"])] = voce["valore"]
    fatte[azione] += 1
if not prova:
    conn.commit()
print(("(prova) " if prova else "") + "registrate:", fatte)
