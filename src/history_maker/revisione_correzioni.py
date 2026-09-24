"""Le correzioni da guardare, con accanto la pagina da cui vengono.

Una correzione e' una decisione presa da qualcuno — un modello che ha
riguardato l'immagine, o una persona — e questo modulo la prepara per la
sola verifica che conta davvero: **qualcuno che apre la carta e guarda**.

Non giudica: mette in fila. Per ogni correzione dice cosa diceva la
trascrizione, cosa dice la correzione, chi l'ha proposta, e con che
confidenza; poi ci attacca l'atto, l'immagine e la frase del testo
integrale in cui quella parola compare, perche' cercarla a mano su una
pagina di corsivo e' il lavoro che fa venir voglia di non farlo.

L'ordine non e' cronologico: **prima quelle su cui c'e' meno da fidarsi**.
Una grafia raddrizzata di poco — 'aprimensore' che diventa 'agrimensore' —
si giudica dalla stringa e non ha bisogno di nessuno; una parola
sostituita da un'altra parola ha bisogno di occhi. Chi ha dieci minuti
deve poterli spendere sulle dieci che contano.
"""

from __future__ import annotations

import json
import re
import sqlite3

from history_maker import paleografia

# I campi su cui una correzione si puo' confrontare con la trascrizione.
CAMPI = ("nome", "cognome", "professione", "via", "eta")

# Sopra questa somiglianza la correzione e' un refuso raddrizzato: la si
# puo' giudicare leggendo le due parole, senza aprire l'immagine.
SOMIGLIANZA_DA_REFUSO = 0.85

# Ogni motivo scritto dalla rilettura comincia dichiarando cosa c'era:
# «cognome: letto «Torzi», sull'immagine «Lozzi»; ...». Quella premessa si
# puo' confrontare con quello che il campo dice adesso.
_LETTO = re.compile(r"^(\w+): letto «([^»]*)»")


def _premessa_regge(motivo: str | None, campo: str, valore: str) -> bool | None:
    """La correzione parla ancora della persona che ha davanti?

    Rende ``None`` quando il motivo non dichiara nulla da confrontare.

    Una decisione porta con se' la lettura che intendeva correggere. Se
    oggi il campo dice un'altra cosa, o la correzione e' gia' arrivata a
    destinazione per un'altra strada — il glossario, la
    normalizzazione — oppure **e' attaccata alla persona sbagliata**, ed
    e' il caso che conta. Il modo in cui e' successo davvero: un'orfana
    riattaccata con un identificatore vecchio ha portato l'eta' di una
    bambina di sedici mesi addosso a un Sindaco, e nessun controllo sul
    valore nuovo poteva accorgersene — perche' il valore nuovo era
    perfettamente sensato, solo su un'altra persona.
    """
    trovato = _LETTO.match(motivo or "")
    if not trovato or trovato.group(1) != campo:
        return None
    return (paleografia.normalizza(trovato.group(2).strip())
            == paleografia.normalizza(valore))


def _frase(testo: str | None, parola: str, intorno: int = 90) -> str | None:
    """Il pezzo di trascrizione in cui la parola compare.

    Serve a orientarsi sulla pagina: sapere che 'Metro' sta dentro
    «comparso il Signor Metro Marianacci di professione Calzolajo»
    dice dove guardare, e vale piu' di qualunque coordinata.
    """
    if not testo or not parola:
        return None
    dove = testo.lower().find(parola.lower())
    if dove < 0:
        return None
    inizio = max(0, dove - intorno)
    fine = min(len(testo), dove + len(parola) + intorno)
    return ("… " if inizio else "") + testo[inizio:fine].strip() + (" …" if fine < len(testo) else "")


def _confermate(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    """Le correzioni che qualcuno ha gia' guardato e approvato.

    Una verifica che resta in una chat non serve a niente: l'elenco non
    si accorcia, e la volta dopo la stessa correzione torna in cima. Una
    decisione ``conferma`` sulla stessa menzione e con la stessa
    evidenza dice «questa l'ho vista, ed e' giusta», e vale quanto una
    che la disfa — solo nel verso opposto.
    """
    fuori: set[tuple[int, str]] = set()
    try:
        righe = conn.execute(
            "SELECT entita, evidenze FROM decisioni WHERE azione = 'conferma'"
        ).fetchall()
    except sqlite3.OperationalError:
        return fuori
    for riga in righe:
        try:
            menzione = json.loads(riga["entita"] or "[]")[0]
        except (TypeError, ValueError, IndexError):
            continue
        for prova in (riga["evidenze"] or "").split(" | "):
            if prova.strip():
                fuori.add((menzione, prova.strip()))
    return fuori


def elenco(conn: sqlite3.Connection) -> list[dict]:
    """Tutte le correzioni in vigore, con il necessario per verificarle.

    In vigore vuol dire: non superate da una decisione successiva. Le
    superate non si mostrano — sono gia' state giudicate e tolte, e
    rimetterle in coda vorrebbe dire far rifare un lavoro fatto.
    """
    conn.row_factory = sqlite3.Row
    confermate = _confermate(conn)
    fuori: list[dict] = []
    try:
        righe = conn.execute(
            "SELECT d.id, d.entita, d.evidenze, d.motivo, d.confidenza, d.decisore, "
            "d.modello, d.quando FROM decisioni d WHERE d.azione = 'correzione' "
            "AND d.decisore <> 'algoritmo' AND NOT EXISTS "
            "(SELECT 1 FROM decisioni x WHERE x.disfa = d.id) ORDER BY d.id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    for riga in righe:
        try:
            menzione = json.loads(riga["entita"] or "[]")[0]
        except (TypeError, ValueError, IndexError):
            continue
        persona = conn.execute(
            "SELECT p.id, p.ruolo, p.nome, p.cognome, p.professione, p.via, p.eta, "
            "p.atto FROM persone p WHERE p.id = ?", (menzione,)
        ).fetchone()
        if persona is None:
            continue
        atto = conn.execute(
            "SELECT id, anno, tipo, numero_atto, immagine, registro, testo_integrale "
            "FROM atti WHERE id = ?", (persona["atto"],)
        ).fetchone()
        if atto is None:
            continue
        try:
            scheda = conn.execute(
                "SELECT i.id, i.nome, i.cognome, i.menzioni FROM menzioni m "
                "JOIN individui i ON i.id = m.individuo WHERE m.persona = ?",
                (menzione,),
            ).fetchone()
        except sqlite3.OperationalError:
            # Un database appena costruito non ha ancora l'albero: le
            # correzioni si possono guardare lo stesso, solo senza il
            # collegamento alla scheda.
            scheda = None

        for prova in (riga["evidenze"] or "").split(" | "):
            campo, _, nuovo = prova.partition("=")
            campo, nuovo = campo.strip(), nuovo.strip()
            if campo not in CAMPI or not nuovo:
                continue
            vecchio = (persona[campo] or "").strip() if campo in persona.keys() else ""
            somiglianza = paleografia.somiglianza(vecchio, nuovo) if vecchio else 0.0
            fuori.append({
                "decisione": riga["id"],
                "menzione": menzione,
                "campo": campo,
                "vecchio": vecchio,
                "nuovo": nuovo,
                "somiglianza": round(somiglianza, 2),
                "riempie_un_vuoto": not vecchio,
                "premessa_regge": _premessa_regge(riga["motivo"], campo, vecchio),
                "confermata": (menzione, prova.strip()) in confermate,
                "da_refuso": bool(vecchio) and somiglianza >= SOMIGLIANZA_DA_REFUSO,
                "decisore": riga["decisore"],
                "modello": riga["modello"] or "",
                "confidenza": riga["confidenza"],
                "motivo": riga["motivo"] or "",
                "quando": riga["quando"],
                "ruolo": persona["ruolo"],
                "chi": f"{persona['nome'] or ''} {persona['cognome'] or ''}".strip(),
                "atto": atto["id"],
                "anno": atto["anno"],
                "tipo": atto["tipo"],
                "numero": atto["numero_atto"],
                "registro": atto["registro"],
                "immagine": atto["immagine"],
                "frase": _frase(atto["testo_integrale"], vecchio or nuovo),
                "scheda": scheda["id"] if scheda else None,
                "scheda_nome": (f"{scheda['nome']} {scheda['cognome']}"
                                if scheda else None),
                "scheda_menzioni": scheda["menzioni"] if scheda else None,
            })

    # Prima quelle che hanno davvero bisogno di occhi: la parola
    # sostituita da un'altra parola, non il refuso raddrizzato.
    # Prima cio' che nessuno ha ancora guardato; in coda le confermate.
    fuori.sort(key=lambda c: (c["confermata"], c["premessa_regge"] is not False,
                              c["da_refuso"], c["riempie_un_vuoto"],
                              c["somiglianza"]))
    return fuori


def riassunto(correzioni: list[dict]) -> dict:
    """Quante sono e di che specie, per la testata della pagina."""
    return {
        "totale": len(correzioni),
        "da_guardare": sum(
            1 for c in correzioni
            if not c["da_refuso"] and not c["riempie_un_vuoto"]
            and not c["confermata"]
        ),
        "confermate": sum(1 for c in correzioni if c["confermata"]),
        "refusi": sum(1 for c in correzioni if c["da_refuso"]),
        "riempimenti": sum(1 for c in correzioni if c["riempie_un_vuoto"]),
        "di_persona": sum(1 for c in correzioni if c["decisore"] == "persona"),
        "premessa_caduta": sum(
            1 for c in correzioni if c["premessa_regge"] is False),
    }
