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
for voce in voci:
    if voce["tipo"] in ("unione", "separazione"):
        entita = tuple(voce["menzioni"])
    else:
        entita = (voce["menzione"],)
    azione = voce["tipo"]
    evidenze = (f"{voce['campo']}={voce['valore']}",) if azione == "correzione" else ()
    chiave_evidenze = evidenze[0] if evidenze else ""
    # Senza "ripristina" contano anche le decisioni superate: una
    # decisione disfatta a mano non torna da sola al giro dopo.
    # Una voce che porta "disfa" supera una decisione vecchia: non e' un
    # doppione di se stessa, e va scritta anche se una decisione uguale
    # esiste gia' (quella che si sta superando ne e' spesso l'opposto).
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
    if (azione, json.dumps(list(entita)), chiave_evidenze) in gia or (
            azione == "correzione" and any(g[0] == azione and g[1] == json.dumps(list(entita))
                                           and chiave_evidenze in (g[2] or "") for g in gia)):
        fatte["gia'"] += 1
        continue
    if not prova:
        # Le decisioni prese dalle date e non dalla pagina portano il loro
        # decisore ('claude'): chi rilegge il registro deve saperlo.
        registro.annota(conn, azione, entita, voce["motivo"], confidenza=1.0,
                        decisore=voce.get("decisore", "claude-immagine"), evidenze=evidenze,
                        disfa=voce.get("disfa"))
    fatte[azione] += 1
if not prova:
    conn.commit()
print(("(prova) " if prova else "") + "registrate:", fatte)
