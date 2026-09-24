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

# Come comincia un atto: il numero d'ordine o la formula dell'anno.
# Una facciata che comincia in mezzo a una frase — «E Maria Maddalena
# Clementina Jorio d'anni venti» — non apre niente: finisce.
_APERTURA = re.compile(
    r"\s*(?:num(?:ero)?\b|n\.?\s*d['’ ]?ordine|l['’ ]?anno\b)",
                       re.IGNORECASE)


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


# Quante letture dello stesso protagonista si accettano. Due sono la
# testa e la coda dello stesso atto; tre non sono piu' una rilettura, sono
# tre atti diversi che per qualche ragione portano lo stesso numero.
LETTURE_DELLO_STESSO = 2


def _e_lo_stesso_atto_riletto(nomi_per_ruolo: dict, limiti: dict) -> bool:
    """Il conflitto e' una rilettura, non due atti diversi?

    Il conto sui protagonisti unici protegge da un errore vero — cucire
    due morti che portano lo stesso numero d'ordine — ma su un matrimonio
    e' troppo severo. Testa e coda si nominano tutt'e due gli sposi, e
    basta che il cognome della sposa sia letto in due modi perche' il
    guardiano veda due spose e lasci l'atto a pezzi: e' il matrimonio n. 5
    del 1815, dove la stessa donna e' «Angela Maria Torzi» in testa e
    «Angela Maria Poyo» in coda.

    Due condizioni, e servono tutt'e due:

    * **almeno un protagonista deve coincidere.** Se lo sposo e' lo stesso
      in tutti i frammenti, la sposa letta in due modi e' la stessa donna.
      Su una morte o una nascita, dove il protagonista e' uno solo, non
      c'e' nessun altro con cui concordare e la regola non scatta mai:
      quelle restano protette come prima.
    * **nessuno deve avere piu' di due letture.** Cinque frammenti con tre
      spose diverse non sono un atto lungo, sono tre atti.
    """
    if not any(len(nomi_per_ruolo.get(ruolo, ())) == 1 for ruolo in limiti):
        return False
    return all(
        len(nomi_per_ruolo.get(ruolo, ())) <= LETTURE_DELLO_STESSO
        for ruolo in limiti
    )


def _e_una_coda(riga) -> bool:
    """Un frammento che porta un numero, ma e' la coda dell'atto di prima.

    Il numero non basta a distinguerli: la trascrizione lo mette in testa
    a ogni facciata, e dove la carta non lo porta lo indovina. Il
    matrimonio n. 4 del 1822 finisce cosi' nell'«atto 871, n. 5», che non
    ha nessuno sposo e comincia con «E Maria Maddalena Clementina Jorio
    d'anni venti»: la sposa dell'atto di prima, letta una seconda volta a
    pagina voltata. Cucito sul numero non lo si raggiunge — i numeri sono
    due, 4 e 5 — e la donna resta due donne, moglie due volte dello
    stesso uomo.

    Tre condizioni insieme, e nessuna da sola basterebbe: e' un
    matrimonio o una pubblicazione, gli manca uno dei due sposi, e il suo
    testo non comincia come cominciano gli atti. La prima esclude nascite
    e morti, dove il protagonista e' uno solo; la seconda esclude gli
    atti interi; la terza esclude le pagine che aprono davvero, comprese
    quelle che aprono in modi insoliti («Vedi 2 NUM. 3», «ATTI PE' NATI
    MORTI»).
    """
    if riga["tipo"] not in ("matrimonio", "pubblicazione"):
        return False
    if riga["sposi"] and riga["spose"]:
        return False
    testo = riga["testo_integrale"]
    return bool(testo) and not _APERTURA.match(testo)


def _code_senza_numero(conn: sqlite3.Connection) -> dict[int, tuple]:
    """I frammenti che non portano un numero, e a quale atto appartengono.

    Una facciata che comincia con la coda dell'atto di prima non ha
    nessun "Num. d'ordine" in testa: sulla carta non c'e', perche' il
    numero sta sulla facciata dove l'atto e' cominciato. La trascrizione
    la rende dunque con ``numero_atto`` nullo, e senza numero
    :func:`ricuci_atti` non puo' raggrupparla: resterebbe un atto
    spaiato, con dentro i testimoni e le firme di qualcun altro.

    Il numero pero' si sa senza indovinarlo: e' quello dell'**ultimo atto
    numerato della scansione precedente**, che e' esattamente l'atto che
    stava finendo. Si richiede che il frammento sia il primo della sua
    scansione — se ne ha altri prima, allora non e' una coda — e che i
    due siano dello stesso tipo.

    Rende ``{id del frammento: (anno, numero) dell'atto a cui appartiene}``.
    L'anno serve quanto il numero: una facciata di coda non porta nessuna
    data, quindi il suo anno e' quello del registro — e un registro
    intitolato 1813 contiene anche i matrimoni del 1814. Cucire sul solo
    numero lascerebbe le code appese, come e' successo davvero: dieci
    frammenti perfetti, con gli sposi giusti, e nessuno cucito.
    """
    primi: dict[tuple, tuple[int, int]] = {}
    numerati: dict[tuple, list[tuple]] = defaultdict(list)
    senza: list[tuple] = []
    for riga in conn.execute(
        """SELECT a.id, a.registro, a.tipo, a.anno, a.immagine, a.numero_atto,
                  a.testo_integrale,
                  (SELECT COUNT(*) FROM persone p
                    WHERE p.atto = a.id AND p.ruolo = 'sposo') AS sposi,
                  (SELECT COUNT(*) FROM persone p
                    WHERE p.atto = a.id AND p.ruolo = 'sposa') AS spose
             FROM atti a ORDER BY a.id"""
    ):
        pag = pagina(riga["immagine"])
        if pag is None:
            continue
        chiave = (riga["registro"], pag)
        # 'primi' serve a sapere se il frammento apre la sua scansione.
        primi.setdefault(chiave, (riga["id"], pag))
        n = numero(riga["numero_atto"])
        if n is None:
            senza.append((riga["id"], riga["registro"], pag, riga["tipo"]))
        else:
            numerati[riga["registro"]].append(
                (pag, n, riga["tipo"], riga["anno"]))

    fuori: dict[int, int] = {}
    for atto, registro, pag, tipo in senza:
        if primi.get((registro, pag), (None,))[0] != atto:
            continue        # non apre la scansione: non e' una coda
        prima = [
            (p, n, a) for p, n, t, a in numerati.get(registro, ())
            if p < pag and pag - p <= DISTANZA_MASSIMA and t == tipo
        ]
        if not prima:
            continue
        # L'ultimo atto della scansione piu' vicina: quello che finiva.
        pagina_prima = max(p for p, _, _ in prima)
        ultimo = max((n, a) for p, n, a in prima if p == pagina_prima)
        fuori[atto] = (ultimo[1], ultimo[0])
    return fuori


def _cuci_le_code_numerate(conn: sqlite3.Connection) -> tuple[int, int]:
    """Attacca ogni coda di matrimonio all'atto della facciata precedente.

    Non passa dal numero d'atto, e non puo' passarci: il numero che la
    coda porta scritto e' proprio quello sbagliato (vedi
    :func:`_e_una_coda`). Passa dalla carta — la facciata di prima, dello
    stesso registro e dello stesso tipo — e si ferma davanti alla stessa
    guardia di sempre: se cucendo venissero fuori due sposi o due spose
    che non sono due letture della stessa persona, non si cuce.

    Va prima del raggruppamento per numero: cucita alla sua testa, la
    coda sparisce, e il numero sbagliato che portava non trascina piu'
    nel gruppo l'atto vero che quel numero ce l'ha per davvero - era il
    caso del matrimonio n. 4 del 1822, dove il gruppo del «4» si trovava
    dentro tre frammenti e due sposi, e rinunciava a cucire.
    """
    frammenti = [
        riga for riga in conn.execute(
            """SELECT a.id, a.registro, a.tipo, a.anno, a.immagine, a.numero_atto,
                      a.testo_integrale,
                      (SELECT COUNT(*) FROM persone p
                        WHERE p.atto = a.id AND p.ruolo = 'sposo') AS sposi,
                      (SELECT COUNT(*) FROM persone p
                        WHERE p.atto = a.id AND p.ruolo = 'sposa') AS spose
                 FROM atti a ORDER BY a.id"""
        )
    ]
    pagine = {riga["id"]: pagina(riga["immagine"]) for riga in frammenti}
    code = [
        riga for riga in frammenti
        if numero(riga["numero_atto"]) is not None
        and pagine[riga["id"]] is not None
        and _e_una_coda(riga)
    ]
    cuciti = tolti = 0
    for coda in code:
        pag = pagine[coda["id"]]
        prima = [
            riga for riga in frammenti
            if riga["registro"] == coda["registro"] and riga["tipo"] == coda["tipo"]
            and pagine[riga["id"]] is not None
            and 0 < pag - pagine[riga["id"]] <= DISTANZA_MASSIMA
            and not _e_una_coda(riga)
        ]
        if not prima:
            continue
        testa = max(prima, key=lambda riga: (pagine[riga["id"]], riga["id"]))
        limiti = UNICI.get(coda["tipo"] or "", {})
        nomi_per_ruolo: dict[str, set] = defaultdict(set)
        for r in conn.execute(
            "SELECT ruolo, nome, cognome FROM persone WHERE atto IN (?, ?)",
            (testa["id"], coda["id"]),
        ):
            if r["ruolo"] in limiti:
                nomi_per_ruolo[r["ruolo"]].add(
                    ((r["nome"] or "")[:5], (r["cognome"] or "")[:5])
                )
        if any(
            len(nomi_per_ruolo.get(ruolo, ())) > quanti
            for ruolo, quanti in limiti.items()
        ) and not _e_lo_stesso_atto_riletto(nomi_per_ruolo, limiti):
            logger.debug("non cucio la coda %s a %s", coda["id"], testa["id"])
            continue
        tolti += _cuci(conn, [testa["id"], coda["id"]])
        cuciti += 1
    return cuciti, tolti


def ricuci_atti(conn: sqlite3.Connection) -> tuple[int, int]:
    """Rimette insieme i frammenti dello stesso atto.

    Torna (atti cuciti, frammenti tolti).
    """
    conn.row_factory = sqlite3.Row
    cuciti, tolti = _cuci_le_code_numerate(conn)
    code = _code_senza_numero(conn)
    gruppi: dict[tuple, list[tuple[int | None, int]]] = defaultdict(list)
    for riga in conn.execute(
        "SELECT id, registro, tipo, anno, numero_atto, immagine FROM atti"
    ):
        n = numero(riga["numero_atto"])
        anno = riga["anno"]
        if n is None:
            # Una coda prende numero E anno dall'atto che stava finendo:
            # la sua facciata non porta nessuna data propria.
            anno, n = code.get(riga["id"], (anno, None))
        if n is None:
            continue
        gruppi[(riga["registro"], riga["tipo"], anno, n)].append(
            (pagina(riga["immagine"]), riga["id"])
        )

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
                ) and not _e_lo_stesso_atto_riletto(nomi_per_ruolo, limiti):
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
