"""Le righe di una scheda: ps.py <individuo> [individuo...]"""
import sqlite3, sys
c = sqlite3.connect("file:data/dataset/torrebruna.sqlite?mode=ro", uri=True, timeout=60)
c.row_factory = sqlite3.Row
for i in sys.argv[1:]:
    ind = c.execute("select * from individui where id=?", (i,)).fetchone()
    print(f"##### [{i}] {ind['nome']} {ind['cognome']} {ind['anno_nascita'] or ''}-{ind['anno_morte'] or ''} ({ind['menzioni']} menz.)" if ind else f"##### [{i}] ?")
    for m in c.execute("""select p.id riga, p.ruolo, p.nome_letto, p.cognome_letto, p.eta, p.note,
                                 a.anno, a.tipo, a.numero_atto, a.id atto, a.immagine
                          from menzioni m join persone p on p.id=m.persona join atti a on a.id=p.atto
                          where m.individuo=? order by a.anno, a.numero_atto""", (i,)):
        print(f"  riga {m['riga']} atto {m['atto']} {m['anno']} {m['tipo']} n.{m['numero_atto']} | {m['ruolo']} «{m['nome_letto']} {m['cognome_letto']}» {m['eta'] or ''} | {m['immagine']}")
