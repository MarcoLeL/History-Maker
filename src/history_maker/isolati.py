"""Le persone che stanno nell'albero appese a un filo solo.

Nell'applicazione si riconoscono a occhio: una casella sola, attaccata
al resto da una riga sola, o da nessuna. Sono la forma visibile della
**frammentazione**, che in questo archivio conta piu' delle unioni
sbagliate — una famiglia spezzata in cinque schede non si vede da
nessuna statistica, ma chi cerca un antenato si ferma li'.

Non sono tutte un difetto. Una levatrice nominata una volta sola nel
1834 e' davvero una persona sola: l'archivio non ne sa altro, e va bene
cosi'. Il difetto e' l'altro caso — la scheda appesa a un filo che ha un
**omonimo esatto** da un'altra parte, magari con trenta menzioni. Quella
quasi sempre non e' una persona in piu': e' un pezzo staccato di una
persona che c'e' gia'.

Percio' l'elenco non ordina per nome ne' per anno, ma **per quanto e'
grosso il gemello**: in cima chi e' appeso a un filo mentre un omonimo
con mezza vita documentata sta due schede piu' in la'.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict


def _gradi(conn: sqlite3.Connection) -> tuple[Counter, dict]:
    """Quanti legami ha ciascuno, e quali.

    Contano allo stesso modo le tre cose che in un albero disegnano una
    riga: essere figlio di qualcuno, essere genitore di qualcuno, essere
    in coppia con qualcuno. Un'unione soltanto 'probabile' vale come le
    altre — sullo schermo la riga si vede lo stesso, ed e' la riga che
    fa sembrare una persona attaccata.
    """
    grado: Counter = Counter()
    legami: dict[int, list] = defaultdict(list)

    for riga in conn.execute("SELECT figlio, genitore, tipo FROM legami"):
        grado[riga["figlio"]] += 1
        grado[riga["genitore"]] += 1
        legami[riga["figlio"]].append(
            {"verso": "figlio di", "altro": riga["genitore"], "come": riga["tipo"]})
        legami[riga["genitore"]].append(
            {"verso": "genitore di", "altro": riga["figlio"], "come": riga["tipo"]})

    for riga in conn.execute("SELECT marito, moglie, stato FROM unioni"):
        if not (riga["marito"] and riga["moglie"]):
            continue
        grado[riga["marito"]] += 1
        grado[riga["moglie"]] += 1
        legami[riga["marito"]].append(
            {"verso": "coniuge di", "altro": riga["moglie"], "come": riga["stato"]})
        legami[riga["moglie"]].append(
            {"verso": "coniuge di", "altro": riga["marito"], "come": riga["stato"]})

    return grado, legami


def _omonimi(conn: sqlite3.Connection) -> dict:
    """Le schede che portano lo stesso nome e cognome, raggruppate.

    Il confronto e' esatto, a meno di maiuscole: non e' un tentativo di
    riconoscimento — quello lo fa la ricostruzione, con le date e i
    parenti — ma un modo di mettere sotto gli occhi i casi in cui due
    schede si chiamano proprio uguale e una delle due e' quasi vuota.
    """
    per_nome: dict[tuple, list] = defaultdict(list)
    for riga in conn.execute(
        "SELECT id, nome, cognome, menzioni, anno_primo, anno_ultimo FROM individui"
    ):
        if not (riga["nome"] and riga["cognome"]):
            continue
        per_nome[(riga["nome"].casefold(), riga["cognome"].casefold())].append(dict(riga))
    return per_nome


def elenco(conn: sqlite3.Connection, quanti_legami: int = 1) -> list[dict]:
    """Le schede con esattamente ``quanti_legami`` legami.

    Con ``0`` rende quelle staccate del tutto: nell'applicazione non
    compaiono in nessun albero, si trovano solo cercandole per nome.
    """
    conn.row_factory = sqlite3.Row
    grado, legami = _gradi(conn)
    per_nome = _omonimi(conn)

    fuori: list[dict] = []
    for riga in conn.execute(
        "SELECT id, nome, cognome, sesso, menzioni, anno_primo, anno_ultimo, "
        "anno_nascita, anno_morte, professioni, contrade FROM individui"
    ):
        if grado.get(riga["id"], 0) != quanti_legami:
            continue

        gemelli = []
        if riga["nome"] and riga["cognome"]:
            for altro in per_nome[(riga["nome"].casefold(), riga["cognome"].casefold())]:
                if altro["id"] != riga["id"]:
                    gemelli.append(altro)
        gemelli.sort(key=lambda g: -(g["menzioni"] or 0))

        attacchi = []
        for legame in legami.get(riga["id"], []):
            altro = conn.execute(
                "SELECT id, nome, cognome, menzioni FROM individui WHERE id = ?",
                (legame["altro"],),
            ).fetchone()
            if altro is not None:
                attacchi.append({
                    "verso": legame["verso"], "come": legame["come"],
                    "id": altro["id"], "menzioni": altro["menzioni"],
                    "chi": f"{altro['nome'] or ''} {altro['cognome'] or ''}".strip(),
                })

        atti = [
            {"id": a["id"], "anno": a["anno"], "tipo": a["tipo"],
             "numero": a["numero_atto"], "ruolo": a["ruolo"]}
            for a in conn.execute(
                "SELECT a.id, a.anno, a.tipo, a.numero_atto, p.ruolo FROM menzioni m "
                "JOIN persone p ON p.id = m.persona JOIN atti a ON a.id = p.atto "
                "WHERE m.individuo = ? ORDER BY a.anno", (riga["id"],)
            )
        ]

        fuori.append({
            "id": riga["id"],
            "nome": f"{riga['nome'] or ''} {riga['cognome'] or ''}".strip(),
            "sesso": riga["sesso"],
            "menzioni": riga["menzioni"],
            "anno_primo": riga["anno_primo"],
            "anno_ultimo": riga["anno_ultimo"],
            "professioni": riga["professioni"],
            "contrade": riga["contrade"],
            "attacchi": attacchi,
            "atti": atti,
            "gemelli": gemelli,
            # Quanto e' documentato il piu' grosso degli omonimi: e' il
            # numero che dice se questa scheda e' una persona sola o il
            # pezzo staccato di una che c'e' gia'.
            "gemello_massimo": gemelli[0]["menzioni"] if gemelli else 0,
        })

    fuori.sort(key=lambda s: (-s["gemello_massimo"], -(s["menzioni"] or 0)))
    return fuori


def riassunto(conn: sqlite3.Connection) -> dict:
    """Il quadro d'insieme: quante schede per quanti legami."""
    conn.row_factory = sqlite3.Row
    grado, _ = _gradi(conn)
    totale = conn.execute("SELECT COUNT(*) FROM individui").fetchone()[0]
    con_legami = Counter(grado.values())
    return {
        "individui": totale,
        "staccate": totale - len(grado),
        "un_filo": con_legami.get(1, 0),
        "due": con_legami.get(2, 0),
        "tre_o_piu": sum(v for k, v in con_legami.items() if k >= 3),
    }
