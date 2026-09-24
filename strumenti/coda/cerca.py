"""cerca.py <nome-parziale> <cognome-parziale>  -> righe con atto e ruolo"""
import sqlite3, sys
c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
c.row_factory = sqlite3.Row
nome = sys.argv[1] if len(sys.argv) > 1 else "%"
cogn = sys.argv[2] if len(sys.argv) > 2 else "%"
q = """select p.id,p.ruolo,p.nome_letto,p.cognome_letto,p.eta,a.anno,a.tipo,a.numero_atto,a.id atto
       from persone p join atti a on a.id=p.atto
       where p.atto in (select atto from persone where nome_letto like ? and cognome_letto like ?)
       order by a.anno, p.id"""
for r in c.execute(q, (nome, cogn)):
    print(f"{r['id']:6} {r['anno']} {r['tipo'][:4]} n.{r['numero_atto']} atto {r['atto']:5} | {r['ruolo']:11} {r['nome_letto']} | {r['cognome_letto']} | {r['eta'] or ''}")
