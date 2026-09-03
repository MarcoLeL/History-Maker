"""Rimettere insieme gli atti che la paginazione ha fatto a pezzi.

Un atto di matrimonio dell'Ottocento non sta in una pagina. Occupa la
facciata, prosegue sulla successiva e a volte su una terza, e il
registro lo tiene insieme con una cosa sola: il **numero d'atto**,
scritto in testa a ogni facciata.

La fase 3 legge una pagina alla volta, quindi produce un atto per
pagina, e il risultato e' una famiglia fatta a pezzi prima ancora che il
riconoscimento cominci. Il matrimonio n. 2 del 1849:

    pagina 7   sposo Stanislao Ottaviano, suo padre, sua madre
    pagina 8   sposo Domenico Cicchillitti, suo padre, sua madre
    pagina 9   sposo Domenico Cicchillitti, sposa Maria Cicchillitti

Nessuno di questi tre "atti" contiene una coppia. :func:`identita.
famiglia_dell_atto` legge le parentele **dentro un atto** — e' li' che
'padre' e 'madre' vogliono dire qualcosa — quindi da queste tre pagine
non ricava ne' che i due sposi siano marito e moglie, ne' di chi siano
figli. Misurato sull'archivio di Torrebruna: 202 matrimoni su 1.200
risultavano senza due sposi, e ogni volta era questo.

Cucire non e' un'interpretazione: e' rimettere insieme quello che il
registro aveva scritto come una cosa sola, e che siamo stati noi a
dividere ritagliando le pagine.

Le tre condizioni per cucire
----------------------------

Due frammenti sono lo stesso atto solo se hanno **lo stesso registro, lo
stesso tipo, lo stesso anno e lo stesso numero**, e se stanno su pagine
**vicine**. L'ultima e' quella che salva dagli sbagli: un registro puo'
contenere due anni — 1813-matrimoni ne contiene anche di 1814 — e li' un
"n. Uno" c'e' due volte, ma a venti pagine di distanza.

E il numero d'atto va normalizzato prima di confrontarlo, perche' i
registri lo scrivono come capita: '2', 'due', 'Uno', 'ventidue'.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, defaultdict

from history_maker import nomi

logger = logging.getLogger(__name__)

# Quante pagine possono passare fra due frammenti dello stesso atto. Uno
# vuol dire "la pagina dopo", che e' il caso normale; si concede due
# perche' ogni tanto una facciata bianca o un margine tagliato produce
# una pagina che non contiene atti.
DISTANZA_MASSIMA = 2

# I ruoli che in un atto ci stanno una volta sola. Se cucendo due
# frammenti ne venissero fuori due, quelli non erano due pezzi di un
# atto: erano due atti diversi con lo stesso numero, e cucirli
# fabbricherebbe un matrimonio che non e' mai stato celebrato.
UNICI = {
    "matrimonio": {"sposo": 1, "sposa": 1},
    "pubblicazione": {"sposo": 1, "sposa": 1},
    "nascita": {"neonato": 1, "neonata": 1},
    "morte": {"defunto": 1, "defunta": 1},
}

_PAGINA = re.compile(r"(\d+)[-_]pag")


def numero(valore: str | None) -> int | None:
    """Il numero d'atto come intero, scritto in cifre o in lettere."""
    if not valore:
        return None
    ripulito = str(valore).strip().lower()
    if ripulito.isdigit():
        return int(ripulito)
    return nomi.NUMERI.get(ripulito)


def pagina(immagine: str | None) -> int | None:
    trovato = _PAGINA.search(immagine or "")
    return int(trovato.group(1)) if trovato else None


def _sequenze(pagine: list[tuple[int | None, int]]) -> list[list[int]]:
    """Spezza i frammenti in gruppi di pagine vicine fra loro.

    Due 'n. Uno' nello stesso registro ma a venti pagine di distanza sono
    due atti di due anni diversi, non un atto lungo venti pagine.
    """
    ordinate = sorted(p for p in pagine if p[0] is not None)
    gruppi: list[list[int]] = []
    corrente: list[int] = []
    ultima: int | None = None
    for numero_pagina, atto in ordinate:
        if ultima is not None and numero_pagina - ultima > DISTANZA_MASSIMA:
            gruppi.append(corrente)
            corrente = []
        corrente.append(atto)
        ultima = numero_pagina
    if corrente:
        gruppi.append(corrente)
    return [g for g in gruppi if len(g) > 1]


def _firma(conn: sqlite3.Connection, atto: int) -> tuple:
    """Chi c'e' dentro un frammento, per riconoscere due letture uguali."""
    return tuple(sorted(
        ((r["ruolo"] or ""), (r["nome"] or "")[:6], (r["cognome"] or "")[:6])
        for r in conn.execute(
            "SELECT ruolo, nome, cognome FROM persone WHERE atto = ?", (atto,)
        )
    ))


def _ruoli(conn: sqlite3.Connection, atti: list[int]) -> Counter[str]:
    segnaposti = ",".join("?" * len(atti))
    return Counter(
        r["ruolo"] for r in conn.execute(
            f"SELECT ruolo FROM persone WHERE atto IN ({segnaposti})", atti
        ) if r["ruolo"]
    )


# I campi che si prendono dal primo frammento che ce l'ha. Sono tutti
# proprieta' dell'atto, non della pagina: la data del matrimonio e' una
# sola anche se sta scritta solo sulla prima facciata.
DA_COMPLETARE = (
    "numero_atto", "data_atto", "data_evento", "ora_evento",
    "luogo", "luogo_letto", "via", "affidabilita",
)


def _cuci(conn: sqlite3.Connection, atti: list[int]) -> int:
    """Versa i frammenti nel primo e cancella gli altri. Torna quanti ne toglie."""
    primo, altri = atti[0], atti[1:]

    # Due frammenti identici sono la stessa facciata letta due volte —
    # succede dove le due meta' della scansione si sovrappongono. Li' non
    # si cuce niente: si butta il doppione, altrimenti l'atto finirebbe
    # con due sposi che sono lo stesso uomo.
    firme = {primo: _firma(conn, primo)}
    da_unire = []
    doppioni = []
    for atto in altri:
        firma = _firma(conn, atto)
        if firma and firma in firme.values():
            doppioni.append(atto)
        else:
            firme[atto] = firma
            da_unire.append(atto)

    for atto in doppioni:
        conn.execute("DELETE FROM persone WHERE atto = ?", (atto,))
        conn.execute("DELETE FROM atti WHERE id = ?", (atto,))

    if da_unire:
        conn.execute(
            f"UPDATE persone SET atto = ? WHERE atto IN "
            f"({','.join('?' * len(da_unire))})",
            (primo, *da_unire),
        )
        for campo in DA_COMPLETARE:
            valore = conn.execute(
                f"SELECT {campo} FROM atti WHERE id IN "
                f"({','.join('?' * (len(da_unire) + 1))}) AND {campo} IS NOT NULL "
                f"ORDER BY id LIMIT 1",
                (primo, *da_unire),
            ).fetchone()
            if valore and valore[0] is not None:
                conn.execute(
                    f"UPDATE atti SET {campo} = ? WHERE id = ? AND {campo} IS NULL",
                    (valore[0], primo),
                )
        # Il testo diplomatico si concatena invece di sostituirsi: le
        # pagine sono diverse e il testo dell'atto e' la loro somma.
        pezzi = [
            r["testo_integrale"] for r in conn.execute(
                f"SELECT testo_integrale FROM atti WHERE id IN "
                f"({','.join('?' * (len(da_unire) + 1))}) ORDER BY id",
                (primo, *da_unire),
            ) if r["testo_integrale"]
        ]
        if pezzi:
            conn.execute(
                "UPDATE atti SET testo_integrale = ? WHERE id = ?",
                ("\n\n".join(pezzi), primo),
            )
        conn.execute(
            f"DELETE FROM atti WHERE id IN ({','.join('?' * len(da_unire))})",
            da_unire,
        )
    return len(doppioni) + len(da_unire)


def ricuci_atti(conn: sqlite3.Connection) -> tuple[int, int]:
    """Rimette insieme i frammenti dello stesso atto.

    Torna (atti cuciti, frammenti tolti).
    """
    conn.row_factory = sqlite3.Row
    gruppi: dict[tuple, list[tuple[int | None, int]]] = defaultdict(list)
    for riga in conn.execute(
        "SELECT id, registro, tipo, anno, numero_atto, immagine FROM atti"
    ):
        n = numero(riga["numero_atto"])
        if n is None:
            continue
        gruppi[(riga["registro"], riga["tipo"], riga["anno"], n)].append(
            (pagina(riga["immagine"]), riga["id"])
        )

    cuciti = tolti = 0
    for (_, tipo, _, _), frammenti in gruppi.items():
        if len(frammenti) < 2:
            continue
        for sequenza in _sequenze(frammenti):
            limiti = UNICI.get(tipo or "", {})
            if limiti:
                ruoli = _ruoli(conn, sequenza)
                # Il conto si fa sui ruoli **distinti per persona**: due
                # frammenti che nominano lo stesso sposo lo contano due
                # volte, e quello e' il caso da cucire, non da scartare.
                nomi_per_ruolo: dict[str, set] = defaultdict(set)
                segnaposti = ",".join("?" * len(sequenza))
                for r in conn.execute(
                    f"SELECT ruolo, nome, cognome FROM persone "
                    f"WHERE atto IN ({segnaposti})", sequenza
                ):
                    if r["ruolo"] in limiti:
                        nomi_per_ruolo[r["ruolo"]].add(
                            ((r["nome"] or "")[:5], (r["cognome"] or "")[:5])
                        )
                if any(
                    len(nomi_per_ruolo.get(ruolo, ())) > quanti
                    for ruolo, quanti in limiti.items()
                ):
                    logger.debug(
                        "non cucio %s: verrebbero fuori %s",
                        sequenza,
                        {k: len(v) for k, v in nomi_per_ruolo.items()},
                    )
                    continue
                del ruoli
            tolti += _cuci(conn, sequenza)
            cuciti += 1
    return cuciti, tolti
