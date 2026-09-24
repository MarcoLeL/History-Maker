"""Il caso di Saba Mosca e delle tre donne Moretta, dopo la ricostruzione."""
import sqlite3

c = sqlite3.connect("data/dataset/torrebruna.sqlite")
c.row_factory = sqlite3.Row


def di(persona):
    r = c.execute("SELECT i.* FROM menzioni m JOIN individui i ON i.id = m.individuo "
                  "WHERE m.persona = ?", (persona,)).fetchone()
    return dict(r)


def genitori(i):
    return [(x["nome"], x["cognome"], x["tipo"]) for x in c.execute(
        "SELECT i.nome, i.cognome, l.tipo FROM legami l JOIN individui i ON i.id = l.genitore "
        "WHERE l.figlio = ?", (i,))]


def coniugi(i):
    return [(x["nome"], x["cognome"]) for x in c.execute(
        "SELECT i.nome, i.cognome FROM unioni u JOIN individui i ON i.id = "
        "CASE WHEN u.marito = ? THEN u.moglie ELSE u.marito END "
        "WHERE u.marito = ? OR u.moglie = ?", (i, i, i))]


def figli(i):
    return [(x["nome"], x["cognome"]) for x in c.execute(
        "SELECT i.nome, i.cognome FROM legami l JOIN individui i ON i.id = l.figlio "
        "WHERE l.genitore = ?", (i,))]


def mostra(eti, persona):
    r = di(persona)
    print(f"  {eti:<34} [{r['id']}] {r['nome']} {r['cognome']} ({r['menzioni']} menz.)")
    print(f"  {'':<34} genitori={genitori(r['id'])} coniugi={coniugi(r['id'])} figli={figli(r['id'])}")
    return r["id"]


print("== Saba Mosca, morta nel 1887 ==")
saba = mostra("Saba (morte n. 38/1887)", 42415)
padre = mostra("il padre «fu Giuseppe»", 42416)
madre = mostra("la madre «Motta Maria Giovanna»", 42417)
lella = mostra("Giuseppe Lella (1852)", 19839)
print("  il padre di Saba non e' Giuseppe Lella:", padre != lella)
print("  Saba ha due genitori:", len(genitori(saba)) == 2)

print("== Le tre donne Moretta ==")
fiorenza = mostra("Maria Fiorenza (sposa 1834)", 10852)
seconda = di(10855)["id"]
grazia = mostra("Grazia (madre 1852)", 19840)
print("  la sposa letta due volte e' una:", fiorenza == seconda)
print("  tre donne diverse:", len({fiorenza, grazia, madre}) == 3)

print("== Arcadio Moretta ==")
arcadio = mostra("Arcadio (padre della sposa 1834)", 10853)
print("  Orsodio Daba e Arcadio Montra sono uno:", arcadio == di(10856)["id"])

print("== L'altra Saba Mosca, morta nel 1886 ==")
altra = mostra("Saba (morte n. 19/1886)", 41308)
print("  due Saba Mosca distinte:", altra != saba)
