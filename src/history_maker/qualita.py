"""Fase 7: contare tutto quello che nell'albero non puo' essere vero.

Le fasi precedenti costruiscono. Questa **misura**, e serve perche' un
albero genealogico ricostruito da una macchina ha un difetto che un
albero costruito a mano non ha: sembra sempre finito. Ogni scheda ha un
nome, delle date, dei genitori; niente, guardandola, dice che quella
donna ha partorito a novantun anni o che quel bambino ha due madri.

Il criterio di questo modulo e' uno solo: **cio' che il mondo non
consente**. Non "cio' che e' improbabile", non "cio' che insospettisce" —
cio' che e' impossibile. Una donna non partorisce due volte a tre mesi di
distanza, un uomo non fa da testimone otto anni dopo il proprio funerale,
un bambino non ha due padri. Ogni caso contato qui e' un errore certo
della ricostruzione: non c'e' interpretazione storica che lo salvi.

Accanto agli impossibili si contano le **frammentazioni**, che sono
l'errore opposto e piu' insidioso: la stessa persona spezzata in due
schede. Un albero frammentato non mostra nessuna assurdita' — mostra solo
meno parentele di quante l'archivio ne contenga, e chi guarda non ha modo
di accorgersene. E' il motivo per cui vanno misurate insieme: correggere
gli assurdi stringendo i criteri aumenta la frammentazione, correggere la
frammentazione allargandoli aumenta gli assurdi. Il numero che conta e'
la somma dei due.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from history_maker import nomi

# --- le costanti del possibile ---------------------------------------------
#
# Sono volutamente larghe. Non servono a dire cosa e' tipico — su questo
# ci sono statistiche vere, e non e' il mestiere di questo modulo — ma a
# dire cosa e' escluso; e un limite largo che nessuno discute vale piu' di
# un limite giusto che tutti discutono.

# Fra due parti della stessa donna. Nove mesi meno un margine: sotto, o
# sono gemelli — e allora nascono lo stesso giorno — o una delle due
# maternita' non e' sua.
GIORNI_FRA_DUE_PARTI = 250

ETA_MINIMA_GENITORE = 13
ETA_MASSIMA_MADRE = 52
ETA_MASSIMA_PADRE = 78

# L'arco fra il primo e l'ultimo parto di una donna. Trentacinque anni
# sono gia' oltre il possibile: sarebbe una che partorisce dai 15 ai 50.
ARCO_MASSIMO_DEI_PARTI = 35

# Di quanto un figlio puo' nascere dopo la morte del genitore. Per il
# padre e' una gravidanza, e i registri lo scrivono 'postumo'; per la
# madre non e' niente.
POSTUMO = {"padre": 1, "madre": 0}

VITA_MASSIMA = 105

# Di quanto l'eta' dichiarata in un atto puo' discostarsi dall'anno di
# nascita che risulta dal suo atto di nascita. I registri arrotondano, e
# chi dichiara l'eta' di un altro tira a indovinare; oltre i dieci anni
# non e' piu' un arrotondamento.
SCARTO_ETA_MASSIMO = 10

# Quanto due nomi devono somigliarsi perche' due schede portino lo stesso
# nome scritto in due modi, invece che due nomi diversi.
SOGLIA_STESSO_NOME = 0.80


@dataclass(frozen=True)
class Caso:
    """Un singolo guaio, con dentro come si fa a ritrovarlo."""

    persone: tuple[int, ...]
    descrizione: str


@dataclass(frozen=True)
class Controllo:
    nome: str
    categoria: str
    spiega: str
    trova: Callable[[sqlite3.Connection], list[Caso]]
    # Quante persone dell'archivio sono esposte a questo controllo. Senza
    # il denominatore un numero grande non si distingue da un problema
    # grande: 36 figli con due padri su 40 sarebbe un disastro, su 5.000
    # e' un margine.
    denominatore: str = ""


@dataclass(frozen=True)
class Esito:
    controllo: Controllo
    casi: list[Caso]

    @property
    def quanti(self) -> int:
        return len(self.casi)


# ---------------------------------------------------------------------------
# Gli aiuti
# ---------------------------------------------------------------------------

def _nome(conn: sqlite3.Connection, individuo: int) -> str:
    riga = conn.execute(
        "SELECT nome, cognome, anno_nascita FROM individui WHERE id = ?", (individuo,)
    ).fetchone()
    if riga is None:
        return f"#{individuo}"
    pezzi = " ".join(p for p in (riga["nome"], riga["cognome"]) if p)
    anno = f" n.{riga['anno_nascita']}" if riga["anno_nascita"] else ""
    return f"{pezzi or '?'}{anno} [#{individuo}]"


def _testo(conn: sqlite3.Connection, individuo: int) -> str:
    riga = conn.execute(
        "SELECT nome, cognome FROM individui WHERE id = ?", (individuo,)
    ).fetchone()
    if riga is None:
        return ""
    return " ".join(p for p in (riga["nome"], riga["cognome"]) if p)


def _data(riga: sqlite3.Row) -> date | None:
    """La data dell'evento, o quella della registrazione se manca."""
    for campo in ("data_evento", "data_atto"):
        valore = riga[campo]
        if valore:
            try:
                return date.fromisoformat(str(valore)[:10])
            except ValueError:
                continue
    return None


def _parti(conn: sqlite3.Connection) -> dict[int, list[tuple[date | None, int, int]]]:
    """Per ogni genitore, i suoi parti: quando, con quale atto, quale figlio.

    Si guarda l'atto di **nascita** e non il legame in se': un atto di
    morte nomina i genitori del defunto trent'anni dopo il parto, e
    prendere quella data come data di un parto sposterebbe tutto.
    """
    per_genitore: dict[int, list[tuple[date | None, int, int]]] = defaultdict(list)
    for riga in conn.execute(
        """
        SELECT l.genitore, l.figlio, a.id AS atto, a.data_evento, a.data_atto, a.anno
          FROM legami l
          JOIN atti a ON a.id = l.atto
         WHERE a.tipo = 'nascita'
        """
    ):
        quando = _data(riga)
        if quando is None and riga["anno"]:
            # Senza giorno si mette la meta' dell'anno: basta per gli
            # ordini di grandezza, e i controlli che pretendono il giorno
            # scartano da soli le date incerte.
            quando = date(riga["anno"], 7, 1)
        per_genitore[riga["genitore"]].append((quando, riga["atto"], riga["figlio"]))
    for elenco in per_genitore.values():
        elenco.sort(key=lambda v: (v[0] or date.min, v[1]))
    return per_genitore


def _coppie(conn: sqlite3.Connection) -> dict[tuple[int, int], list[int]]:
    """I nuclei: per ogni (padre, madre), i figli che l'archivio gli da'."""
    coppie: dict[tuple[int, int], list[int]] = defaultdict(list)
    for riga in conn.execute(
        """
        SELECT p.genitore AS padre, m.genitore AS madre, p.figlio
          FROM legami p
          JOIN legami m ON m.figlio = p.figlio AND m.tipo = 'madre'
         WHERE p.tipo = 'padre'
        """
    ):
        coppie[(riga["padre"], riga["madre"])].append(riga["figlio"])
    return coppie


# ---------------------------------------------------------------------------
# Gli impossibili
# ---------------------------------------------------------------------------

def parti_troppo_ravvicinati(conn: sqlite3.Connection) -> list[Caso]:
    sesso = {
        r["id"]: r["sesso"]
        for r in conn.execute("SELECT id, sesso FROM individui")
    }
    casi = []
    for genitore, elenco in _parti(conn).items():
        if sesso.get(genitore) != "F":
            continue
        for (prima, _, figlio_a), (dopo, _, figlio_b) in zip(elenco, elenco[1:]):
            if prima is None or dopo is None:
                continue
            distanza = (dopo - prima).days
            # Zero giorni sono gemelli, e i gemelli esistono.
            if 0 < distanza < GIORNI_FRA_DUE_PARTI:
                casi.append(Caso(
                    (genitore, figlio_a, figlio_b),
                    f"{_nome(conn, genitore)} partorisce a {distanza} giorni di "
                    f"distanza ({prima} e {dopo})",
                ))
    return casi


def genitore_troppo_giovane(conn: sqlite3.Connection) -> list[Caso]:
    casi = []
    for riga in conn.execute(
        """
        SELECT l.genitore, l.figlio, l.tipo,
               g.anno_nascita AS suo, f.anno_nascita AS del_figlio
          FROM legami l
          JOIN individui g ON g.id = l.genitore
          JOIN individui f ON f.id = l.figlio
         WHERE g.anno_nascita IS NOT NULL AND f.anno_nascita IS NOT NULL
        """
    ):
        eta = riga["del_figlio"] - riga["suo"]
        if eta < ETA_MINIMA_GENITORE:
            casi.append(Caso(
                (riga["genitore"], riga["figlio"]),
                f"{_nome(conn, riga['genitore'])} avrebbe avuto un figlio a "
                f"{eta} anni: {_nome(conn, riga['figlio'])}",
            ))
    return casi


def genitore_troppo_vecchio(conn: sqlite3.Connection) -> list[Caso]:
    anagrafe = {
        r["id"]: (r["sesso"], r["anno_nascita"])
        for r in conn.execute("SELECT id, sesso, anno_nascita FROM individui")
    }
    casi = []
    for genitore, elenco in _parti(conn).items():
        sesso, nascita = anagrafe.get(genitore, (None, None))
        if nascita is None:
            continue
        limite = ETA_MASSIMA_MADRE if sesso == "F" else ETA_MASSIMA_PADRE
        for quando, _, figlio in elenco:
            if quando is None:
                continue
            eta = quando.year - nascita
            if eta > limite:
                casi.append(Caso(
                    (genitore, figlio),
                    f"{_nome(conn, genitore)} avrebbe avuto un figlio a {eta} anni, "
                    f"nel {quando.year}",
                ))
    return casi


def arco_dei_parti_impossibile(conn: sqlite3.Connection) -> list[Caso]:
    sesso = {
        r["id"]: r["sesso"] for r in conn.execute("SELECT id, sesso FROM individui")
    }
    casi = []
    for genitore, elenco in _parti(conn).items():
        if sesso.get(genitore) != "F":
            continue
        anni = [q.year for q, _, _ in elenco if q is not None]
        if len(anni) < 2:
            continue
        arco = anni[-1] - anni[0]
        if arco > ARCO_MASSIMO_DEI_PARTI:
            casi.append(Caso(
                (genitore,),
                f"{_nome(conn, genitore)} risulta madre per {arco} anni, dal "
                f"{anni[0]} al {anni[-1]} ({len(anni)} parti)",
            ))
    return casi


def figlio_nato_dopo_la_morte(conn: sqlite3.Connection) -> list[Caso]:
    anagrafe = {
        r["id"]: (r["sesso"], r["anno_morte"])
        for r in conn.execute("SELECT id, sesso, anno_morte FROM individui")
    }
    casi = []
    for genitore, elenco in _parti(conn).items():
        sesso, morte = anagrafe.get(genitore, (None, None))
        if morte is None:
            continue
        tipo = "madre" if sesso == "F" else "padre"
        for quando, _, figlio in elenco:
            if quando is None:
                continue
            if quando.year > morte + POSTUMO[tipo]:
                casi.append(Caso(
                    (genitore, figlio),
                    f"{_nome(conn, genitore)} muore nel {morte} e ha un figlio "
                    f"nel {quando.year}",
                ))
    return casi


# I ruoli che si possono ricoprire solo da vivi. 'padre' e 'madre' non ci
# sono: un atto nomina i genitori del defunto anche quando sono morti da
# trent'anni, ed e' il modo normale di scrivere un atto di morte.
RUOLI_DA_VIVI = ("testimone", "dichiarante", "ufficiale", "sposo", "sposa", "levatrice")


def atti_dopo_la_propria_morte(conn: sqlite3.Connection) -> list[Caso]:
    segnaposti = ",".join("?" * len(RUOLI_DA_VIVI))
    return [
        Caso(
            (riga["individuo"],),
            f"{_nome(conn, riga['individuo'])} muore nel {riga['anno_morte']} ed e' "
            f"{riga['ruolo']} in un atto del {riga['anno']}",
        )
        for riga in conn.execute(
            f"""
            SELECT m.individuo, a.anno, p.ruolo, i.anno_morte
              FROM menzioni m
              JOIN persone p ON p.id = m.persona
              JOIN atti a ON a.id = p.atto
              JOIN individui i ON i.id = m.individuo
             WHERE i.anno_morte IS NOT NULL
               AND a.anno > i.anno_morte
               AND p.ruolo IN ({segnaposti})
               AND COALESCE(p.stato_vitale, '') NOT LIKE '%fu%'
               AND COALESCE(p.stato_vitale, '') NOT LIKE '%defunt%'
            """,
            RUOLI_DA_VIVI,
        )
    ]


def _due_atti_dello_stesso_tipo(
    conn: sqlite3.Connection, tipo: str, ruoli: tuple[str, ...], detto: str
) -> list[Caso]:
    segnaposti = ",".join("?" * len(ruoli))
    per_individuo: dict[int, set[int]] = defaultdict(set)
    for riga in conn.execute(
        f"""
        SELECT m.individuo, a.anno
          FROM menzioni m
          JOIN persone p ON p.id = m.persona
          JOIN atti a ON a.id = p.atto
         WHERE a.tipo = ? AND p.ruolo IN ({segnaposti})
        """,
        (tipo, *ruoli),
    ):
        per_individuo[riga["individuo"]].add(riga["anno"])
    return [
        Caso((individuo,),
             f"{_nome(conn, individuo)} {detto} {len(anni)} volte: "
             + ", ".join(str(a) for a in sorted(anni)))
        for individuo, anni in per_individuo.items() if len(anni) > 1
    ]


def due_atti_di_morte(conn: sqlite3.Connection) -> list[Caso]:
    return _due_atti_dello_stesso_tipo(conn, "morte", ("defunto", "defunta"), "muore")


def due_atti_di_nascita(conn: sqlite3.Connection) -> list[Caso]:
    return _due_atti_dello_stesso_tipo(conn, "nascita", ("neonato", "neonata"), "nasce")


def morto_prima_di_nascere(conn: sqlite3.Connection) -> list[Caso]:
    return [
        Caso((r["id"],),
             f"{_nome(conn, r['id'])} muore nel {r['anno_morte']} ed e' nato "
             f"nel {r['anno_nascita']}")
        for r in conn.execute(
            "SELECT id, anno_nascita, anno_morte FROM individui "
            "WHERE anno_nascita IS NOT NULL AND anno_morte IS NOT NULL "
            "AND anno_morte < anno_nascita"
        )
    ]


def vita_impossibile(conn: sqlite3.Connection) -> list[Caso]:
    return [
        Caso((r["id"],),
             f"{_nome(conn, r['id'])} vivrebbe {r['anno_morte'] - r['anno_nascita']} anni")
        for r in conn.execute(
            "SELECT id, anno_nascita, anno_morte FROM individui "
            "WHERE anno_nascita IS NOT NULL AND anno_morte IS NOT NULL "
            "AND anno_morte - anno_nascita > ?",
            (VITA_MASSIMA,),
        )
    ]


def _piu_di_un_genitore(conn: sqlite3.Connection, tipo: str) -> list[Caso]:
    per_figlio: dict[int, list[int]] = defaultdict(list)
    for riga in conn.execute(
        "SELECT figlio, genitore FROM legami WHERE tipo = ?", (tipo,)
    ):
        per_figlio[riga["figlio"]].append(riga["genitore"])
    return [
        Caso((figlio, *genitori),
             f"{_nome(conn, figlio)} ha {len(genitori)} {tipo}i: "
             + "; ".join(_nome(conn, g) for g in genitori))
        for figlio, genitori in per_figlio.items() if len(genitori) > 1
    ]


def piu_di_un_padre(conn: sqlite3.Connection) -> list[Caso]:
    return _piu_di_un_genitore(conn, "padre")


def piu_di_una_madre(conn: sqlite3.Connection) -> list[Caso]:
    return _piu_di_un_genitore(conn, "madre")


def figlio_di_piu_coppie(conn: sqlite3.Connection) -> list[Caso]:
    per_figlio: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for (padre, madre), figli in _coppie(conn).items():
        for figlio in figli:
            per_figlio[figlio].add((padre, madre))
    return [
        Caso((figlio, *[x for c in coppie for x in c]),
             f"{_nome(conn, figlio)} appartiene a {len(coppie)} coppie: "
             + " | ".join(f"{_nome(conn, p)} x {_nome(conn, m)}" for p, m in coppie))
        for figlio, coppie in per_figlio.items() if len(coppie) > 1
    ]


def antenato_di_se_stesso(conn: sqlite3.Connection) -> list[Caso]:
    """Un ciclo nell'albero.

    Non e' esotico come sembra: basta che due generazioni della stessa
    famiglia — il nonno e il nipote che portano lo stesso nome — si
    fondano in una persona sola, e da quel momento uno e' antenato di
    se stesso.
    """
    genitori: dict[int, set[int]] = defaultdict(set)
    for riga in conn.execute("SELECT figlio, genitore FROM legami"):
        genitori[riga["figlio"]].add(riga["genitore"])

    casi: list[Caso] = []
    visti: set[int] = set()

    # Iterativa e non ricorsiva: le catene di filiazione di un paese
    # arrivano a qualche decina di generazioni, ma un ciclo le rende
    # infinite, e con la ricorsione si esaurirebbe lo stack invece di
    # trovare il ciclo.
    for partenza in list(genitori):
        if partenza in visti:
            continue
        pila = [(partenza, iter(genitori.get(partenza, ())))]
        in_corso = {partenza}
        percorso = [partenza]
        while pila:
            nodo, prossimi = pila[-1]
            avanzato = False
            for su in prossimi:
                if su in in_corso:
                    giro = percorso[percorso.index(su):]
                    casi.append(Caso(
                        tuple(giro),
                        "ciclo: " + " -> ".join(_nome(conn, x) for x in giro + [su]),
                    ))
                    continue
                if su in visti:
                    continue
                pila.append((su, iter(genitori.get(su, ()))))
                in_corso.add(su)
                percorso.append(su)
                avanzato = True
                break
            if not avanzato:
                pila.pop()
                in_corso.discard(nodo)
                if percorso and percorso[-1] == nodo:
                    percorso.pop()
                visti.add(nodo)
    return casi


def sposato_con_un_parente(conn: sqlite3.Connection) -> list[Caso]:
    """Coniugi che risultano anche genitore-figlio, o fratelli.

    Il matrimonio fra cugini, in un paese di montagna, e' normale e non
    e' un errore. Fra fratelli, o fra padre e figlia, no: e' il segno di
    due persone diverse cucite in una.
    """
    return [
        Caso((riga["marito"], riga["moglie"]),
             f"{_nome(conn, riga['marito'])} risulta sposato a una parente "
             f"stretta: {_nome(conn, riga['moglie'])}")
        for riga in conn.execute(
            """
            SELECT u.marito, u.moglie FROM unioni u
             WHERE u.marito IS NOT NULL AND u.moglie IS NOT NULL
               AND (EXISTS (SELECT 1 FROM legami l
                             WHERE (l.figlio = u.marito AND l.genitore = u.moglie)
                                OR (l.figlio = u.moglie AND l.genitore = u.marito))
                 OR EXISTS (SELECT 1 FROM legami a
                              JOIN legami b ON b.genitore = a.genitore
                                           AND b.figlio <> a.figlio
                            WHERE a.figlio = u.marito AND b.figlio = u.moglie))
            """
        )
    ]


def sposato_con_se_stesso(conn: sqlite3.Connection) -> list[Caso]:
    return [
        Caso((r["marito"],),
             f"{_nome(conn, r['marito'])} risulta sposato con se stesso")
        for r in conn.execute(
            "SELECT marito FROM unioni WHERE marito = moglie AND marito IS NOT NULL"
        )
    ]


def eta_incoerente(conn: sqlite3.Connection) -> list[Caso]:
    """L'eta' dichiarata non torna con il suo atto di nascita.

    Quando lo scarto e' grosso una delle due letture e' sbagliata, oppure
    — ed e' il caso che interessa — quella menzione e' stata attribuita
    alla persona sbagliata.
    """
    casi = []
    for riga in conn.execute(
        """
        SELECT m.individuo, p.eta, a.anno, i.anno_nascita
          FROM menzioni m
          JOIN persone p ON p.id = m.persona
          JOIN atti a ON a.id = p.atto
          JOIN individui i ON i.id = m.individuo
         WHERE p.eta IS NOT NULL AND i.nascita_origine = 'certa'
           AND i.anno_nascita IS NOT NULL
        """
    ):
        analisi = nomi.analizza_eta(riga["eta"])
        if analisi is None:
            continue
        atteso = riga["anno"] - analisi.anno_nascita
        scarto = abs(atteso - riga["anno_nascita"])
        if scarto > SCARTO_ETA_MASSIMO:
            casi.append(Caso(
                (riga["individuo"],),
                f"{_nome(conn, riga['individuo'])} dichiara {analisi.anni} anni nel "
                f"{riga['anno']} — sarebbe nato nel {atteso} — ma il suo atto di "
                f"nascita e' del {riga['anno_nascita']}",
            ))
    return casi


# Quanti anni puo' avere chi si sposa. Larghi da tutte e due le parti:
# ci si sposava presto, e un vedovo si risposava anche a settanta. Oltre
# non e' longevita', e' un'altra persona con lo stesso nome.
ETA_MINIMA_SPOSI = 13
ETA_MASSIMA_SPOSI = 75


def sposi_a_eta_impossibile(conn: sqlite3.Connection) -> list[Caso]:
    """Chi si sposa a sei anni, o a centosei.

    E' il controllo che mancava, e non era un dettaglio: l'atto di
    matrimonio e' la fonte piu' ricca dell'archivio — porta due sposi e
    quattro genitori — quindi attribuirlo alla persona sbagliata sposta
    sei parentele in un colpo. Tertulliano Lalli, nato nel 1791,
    risultava sposarsi nel 1897.
    """
    return [
        Caso(
            (riga["individuo"],),
            f"{_nome(conn, riga['individuo'])} si sposerebbe nel {riga['anno']} "
            f"a {riga['eta']} anni",
        )
        for riga in conn.execute(
            """
            SELECT i.id AS individuo, u.anno, u.anno - i.anno_nascita AS eta
              FROM unioni u
              JOIN individui i ON i.id IN (u.marito, u.moglie)
             WHERE u.origine = 'matrimonio' AND u.anno IS NOT NULL
               AND i.anno_nascita IS NOT NULL
               AND (u.anno - i.anno_nascita > ? OR u.anno - i.anno_nascita < ?)
            """,
            (ETA_MASSIMA_SPOSI, ETA_MINIMA_SPOSI),
        )
    ]


def unione_fra_lo_stesso_sesso(conn: sqlite3.Connection) -> list[Caso]:
    """Due uomini o due donne sposati, nell'Ottocento.

    Non e' un giudizio sulla cosa: e' che nei registri di stato civile
    del Regno una coppia cosi' non esiste, quindi la riga e' sbagliata —
    o e' sbagliato il sesso di uno dei due, o non sono una coppia.
    """
    return [
        Caso(
            (riga["marito"], riga["moglie"]),
            f"{_nome(conn, riga['marito'])} e {_nome(conn, riga['moglie'])} "
            f"risultano coniugi e hanno lo stesso sesso ({riga['sesso']})",
        )
        for riga in conn.execute(
            """
            SELECT u.marito, u.moglie, m.sesso
              FROM unioni u
              JOIN individui m ON m.id = u.marito
              JOIN individui f ON f.id = u.moglie
             WHERE m.sesso IS NOT NULL AND m.sesso = f.sesso
            """
        )
    ]


def unione_scritta_due_volte(conn: sqlite3.Connection) -> list[Caso]:
    """La stessa coppia memorizzata due volte, a ruoli invertiti."""
    return [
        Caso(
            (riga["marito"], riga["moglie"]),
            f"{_nome(conn, riga['marito'])} e {_nome(conn, riga['moglie'])} "
            f"compaiono come coppia due volte, una per verso",
        )
        for riga in conn.execute(
            """
            SELECT a.marito, a.moglie FROM unioni a
              JOIN unioni b ON a.marito = b.moglie AND a.moglie = b.marito
             WHERE a.id < b.id
            """
        )
    ]


# ---------------------------------------------------------------------------
# Le frammentazioni
# ---------------------------------------------------------------------------

def coniugi_duplicati(conn: sqlite3.Connection) -> list[Caso]:
    """Due coniugi della stessa persona che portano lo stesso nome.

    Le seconde nozze sono frequentissime — si restava vedovi presto — ma
    non ci si risposava con un'omonima della prima moglie. Quando i due
    nomi si somigliano e' una donna sola, letta due volte.
    """
    casi = []
    for ruolo, altro in (("marito", "moglie"), ("moglie", "marito")):
        per_persona: dict[int, list[int]] = defaultdict(list)
        for riga in conn.execute(
            f"SELECT {ruolo} AS uno, {altro} AS altro FROM unioni "
            f"WHERE {ruolo} IS NOT NULL AND {altro} IS NOT NULL"
        ):
            per_persona[riga["uno"]].append(riga["altro"])
        for persona, altri in per_persona.items():
            if len(altri) < 2:
                continue
            testi = {a: _testo(conn, a) for a in altri}
            for indice, uno in enumerate(altri):
                for due in altri[indice + 1:]:
                    if nomi.somiglianza_nome(testi[uno], testi[due]) >= SOGLIA_STESSO_NOME:
                        casi.append(Caso(
                            (persona, uno, due),
                            f"{_nome(conn, persona)} ha due coniugi con lo stesso "
                            f"nome: {_nome(conn, uno)} e {_nome(conn, due)}",
                        ))
    return casi


def omonimi_con_la_stessa_nascita(conn: sqlite3.Connection) -> list[Caso]:
    """Stesso nome, stesso cognome, stesso anno di nascita **certo**.

    Due atti di nascita distinti nello stesso anno per lo stesso nome
    sarebbero due bambini diversi, e capita. Ma qui l'anno viene dal
    proprio atto di nascita in entrambi i casi: o e' lo stesso atto letto
    due volte, o e' la stessa persona rimasta in due schede.
    """
    gruppi: dict[tuple, list[int]] = defaultdict(list)
    for riga in conn.execute(
        "SELECT id, nome, cognome, anno_nascita FROM individui "
        "WHERE nascita_origine = 'certa' AND anno_nascita IS NOT NULL "
        "AND nome IS NOT NULL AND cognome IS NOT NULL"
    ):
        gruppi[(riga["nome"], riga["cognome"], riga["anno_nascita"])].append(riga["id"])
    return [
        Caso(tuple(elenco),
             f"{len(elenco)} schede per {nome} {cognome}, tutte nate nel {anno}")
        for (nome, cognome, anno), elenco in gruppi.items() if len(elenco) > 1
    ]


MASSIMO_CONIUGI = 2       # oltre, non e' vedovanza: e' una fusione sbagliata
MASSIMO_COGNOMI = 4       # grafie inconciliabili tollerate in una scheda
COGNOMI_SIMILI = 0.72     # sotto, sono due cognomi e non due grafie


def troppi_coniugi(conn: sqlite3.Connection) -> list[Caso]:
    """Chi ha piu' coniugi di quanti l'epoca ne conceda.

    Le seconde nozze esistono — sull'archivio, uno sposo su 75 — ma le
    terze sono cosi' rare che una scheda con tre coniugi e' quasi sempre
    due persone cucite insieme. Oppure, e capita, e' una persona sola con
    una moglie letta sotto tre cognomi diversi: anche quello e' un guaio,
    ed e' giusto che esca di qui.
    """
    casi = []
    for riga in conn.execute(
        "SELECT i.id, i.nome, i.cognome, COUNT(*) n FROM individui i "
        "JOIN unioni u ON u.marito = i.id OR u.moglie = i.id "
        "GROUP BY i.id HAVING n > ? ORDER BY n DESC", (MASSIMO_CONIUGI,)
    ):
        casi.append(Caso(
            persone=(riga["id"],),
            descrizione=(
                f"{riga['nome']} {riga['cognome']} [{riga['id']}] risulta "
                f"sposato {riga['n']} volte"
            ),
        ))
    return casi


def cognomi_inconciliabili(conn: sqlite3.Connection) -> list[Caso]:
    """Schede che portano cognomi che non sono grafie l'uno dell'altro.

    Non basta contare le grafie: un cognome letto in otto modi da otto
    mani diverse e' normale, e infatti il vocabolario le riconduce. Qui
    si guarda se restano gruppi **inconciliabili** — 'Fidelibus' e
    'Antonucci' non sono la stessa parola scritta male — perche' e' quel
    resto a dire che la scheda ha inghiottito due famiglie.
    """
    from history_maker import paleografia

    casi = []
    for riga in conn.execute(
        "SELECT m.individuo id, i.nome, i.cognome, "
        "  GROUP_CONCAT(DISTINCT p.cognome_letto) letti "
        "FROM menzioni m JOIN persone p ON p.id = m.persona "
        "JOIN individui i ON i.id = m.individuo "
        "WHERE p.cognome_letto IS NOT NULL GROUP BY m.individuo"
    ):
        forme = {
            paleografia.normalizza(pezzo)
            for pezzo in (riga["letti"] or "").split(",") if pezzo.strip()
        }
        if len(forme) <= MASSIMO_COGNOMI:
            continue
        gruppi: list[str] = []
        for forma in sorted(forme):
            if not any(
                paleografia.somiglianza(forma, testa) >= COGNOMI_SIMILI
                for testa in gruppi
            ):
                gruppi.append(forma)
        if len(gruppi) > MASSIMO_COGNOMI:
            casi.append(Caso(
                persone=(riga["id"],),
                descrizione=(
                    f"{riga['nome']} {riga['cognome']} [{riga['id']}] porta "
                    f"{len(gruppi)} cognomi diversi: {', '.join(gruppi[:6])}"
                ),
            ))
    return casi


def coppie_gemelle(conn: sqlite3.Connection) -> list[Caso]:
    """Due coppie con gli stessi due nomi, e i figli divisi fra loro.

    E' la frammentazione che fa piu' danno, perche' non spezza una
    persona ma una famiglia: i fratelli finiscono in due nuclei e da
    quel momento non si riconoscono piu' come fratelli.
    """
    coppie = _coppie(conn)
    testi = {
        persona: _testo(conn, persona)
        for coppia in coppie for persona in coppia
    }

    # Si confrontano solo le coppie che condividono l'inizio di tutti e
    # due i cognomi: senza questo il confronto sarebbe fra tutte le
    # coppie a due a due, che su qualche migliaio di nuclei non finisce.
    per_chiave: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for padre, madre in coppie:
        chiave = (testi[padre][:3].lower(), testi[madre][:3].lower())
        per_chiave[chiave].append((padre, madre))

    casi = []
    for vicine in per_chiave.values():
        for indice, una in enumerate(vicine):
            for altra in vicine[indice + 1:]:
                if una[0] == altra[0] and una[1] == altra[1]:
                    continue
                if nomi.somiglianza_nome(
                    testi[una[0]], testi[altra[0]]
                ) < SOGLIA_STESSO_NOME:
                    continue
                if nomi.somiglianza_nome(
                    testi[una[1]], testi[altra[1]]
                ) < SOGLIA_STESSO_NOME:
                    continue
                casi.append(Caso(
                    (una[0], una[1], altra[0], altra[1]),
                    "due nuclei per la stessa coppia: "
                    f"{_nome(conn, una[0])} x {_nome(conn, una[1])} "
                    f"({len(coppie[una])} figli) e "
                    f"{_nome(conn, altra[0])} x {_nome(conn, altra[1])} "
                    f"({len(coppie[altra])} figli)",
                ))
    return casi


def figli_omonimi_vivi(conn: sqlite3.Connection) -> list[Caso]:
    """Due figli della stessa coppia con lo stesso nome, e nessuno morto.

    Rimettere a un neonato il nome di un fratello morto e' un uso
    documentato e frequente, e non va segnalato. Ma se del primo non
    risulta nessuna morte, o sono lo stesso bambino contato due volte,
    o una delle due nascite e' finita nella famiglia sbagliata.
    """
    morte = {
        r["id"]: (r["anno_morte"], r["morta_entro"], r["nome"])
        for r in conn.execute(
            "SELECT id, nome, anno_morte, morta_entro FROM individui"
        )
    }
    casi = []
    for (padre, madre), figli in _coppie(conn).items():
        visti: dict[str, int] = {}
        for figlio in figli:
            _, _, nome_figlio = morte.get(figlio, (None, None, None))
            chiave = (nome_figlio or "").lower()
            if not chiave:
                continue
            gemello = visti.get(chiave)
            if gemello is not None:
                anno_morte, entro, _ = morte.get(gemello, (None, None, None))
                if anno_morte is None and entro is None:
                    casi.append(Caso(
                        (padre, madre, gemello, figlio),
                        f"{_nome(conn, padre)} x {_nome(conn, madre)} hanno due figli "
                        f"di nome {nome_figlio} e del primo non risulta la morte: "
                        f"{_nome(conn, gemello)}, {_nome(conn, figlio)}",
                    ))
            visti[chiave] = figlio
    return casi


# ---------------------------------------------------------------------------
# L'elenco dei controlli
# ---------------------------------------------------------------------------

CONTROLLI: tuple[Controllo, ...] = (
    Controllo("figlio con due padri", "impossibile",
              "un atto dichiara un padre, un altro ne dichiara un altro",
              piu_di_un_padre, "figli con un padre"),
    Controllo("figlio con due madri", "impossibile",
              "un atto dichiara una madre, un altro ne dichiara un'altra",
              piu_di_una_madre, "figli con una madre"),
    Controllo("figlio di due coppie", "impossibile",
              "lo stesso bambino appeso a due nuclei familiari",
              figlio_di_piu_coppie, "figli con entrambi i genitori"),
    Controllo("parti troppo ravvicinati", "impossibile",
              "due parti della stessa donna a meno di nove mesi, e non gemelli",
              parti_troppo_ravvicinati, "madri con due o piu' parti"),
    Controllo("genitore troppo giovane", "impossibile",
              "un figlio prima dei tredici anni",
              genitore_troppo_giovane, "legami con entrambe le nascite note"),
    Controllo("genitore troppo vecchio", "impossibile",
              "un figlio dopo i 52 anni per una donna, i 78 per un uomo",
              genitore_troppo_vecchio, "parti con la nascita del genitore nota"),
    Controllo("arco dei parti impossibile", "impossibile",
              "una donna madre per piu' di trentacinque anni",
              arco_dei_parti_impossibile, "madri con due o piu' parti"),
    Controllo("figlio nato dopo la morte del genitore", "impossibile",
              "oltre l'anno di gravidanza che i registri chiamano postumo",
              figlio_nato_dopo_la_morte, "parti con la morte del genitore nota"),
    Controllo("atti dopo la propria morte", "impossibile",
              "testimone, dichiarante o sposo dopo il proprio funerale",
              atti_dopo_la_propria_morte, "menzioni di persone con atto di morte"),
    Controllo("due atti di morte", "impossibile",
              "la stessa persona muore due volte",
              due_atti_di_morte, "persone con un atto di morte"),
    Controllo("due atti di nascita", "impossibile",
              "la stessa persona nasce due volte",
              due_atti_di_nascita, "persone con un atto di nascita"),
    Controllo("morto prima di nascere", "impossibile", "",
              morto_prima_di_nascere, "persone con nascita e morte"),
    Controllo("vita oltre i 105 anni", "impossibile", "",
              vita_impossibile, "persone con nascita e morte"),
    Controllo("sposato con un parente stretto", "impossibile",
              "coniugi che risultano anche genitore-figlio o fratelli",
              sposato_con_un_parente, "unioni"),
    Controllo("sposato con se stesso", "impossibile", "",
              sposato_con_se_stesso, "unioni"),
    Controllo("sposi a un'eta' impossibile", "impossibile",
              "sotto i tredici anni o sopra i settantacinque",
              sposi_a_eta_impossibile, "unioni documentate da un atto"),
    Controllo("coniugi dello stesso sesso", "impossibile",
              "nei registri del Regno una coppia cosi' non esiste: "
              "o e' sbagliato il sesso, o non sono una coppia",
              unione_fra_lo_stesso_sesso, "unioni"),
    Controllo("unione scritta due volte", "impossibile",
              "la stessa coppia memorizzata a ruoli invertiti",
              unione_scritta_due_volte, "unioni"),
    Controllo("antenato di se stesso", "impossibile",
              "un ciclo nell'albero",
              antenato_di_se_stesso, "persone con genitori"),
    Controllo("coniugi duplicati", "frammentazione",
              "due coniugi dello stesso nome per la stessa persona",
              coniugi_duplicati, "persone con due o piu' unioni"),
    Controllo("omonimi con la stessa nascita", "frammentazione",
              "stesso nome, stesso cognome, stesso anno di nascita certo",
              omonimi_con_la_stessa_nascita, "persone con nascita certa"),
    Controllo("coppie gemelle", "frammentazione",
              "due nuclei per la stessa coppia, con i figli divisi",
              coppie_gemelle, "coppie con figli"),
    Controllo("figli omonimi entrambi vivi", "frammentazione",
              "due figli dello stesso nome, del primo non risulta la morte",
              figli_omonimi_vivi, "coppie con figli"),
    # --- l'errore opposto: una scheda che non e' una persona ----------
    #
    # Questi controlli sono nati tardi, e da un caso: 'Angela Lella' con
    # sei coniugi, e una scheda con 84 menzioni e 29 grafie di cognome.
    # Fino ad allora la qualita' guardava solo da una parte — assurdi e
    # frammentazione — e un accorpamento sbagliato non produce ne' l'uno
    # ne' l'altra: due madri diverse fuse in una non violano niente. Il
    # sistema poteva quindi peggiorare in precisione mentre tutti i
    # numeri miglioravano, ed e' esattamente quello che e' successo.
    Controllo("troppi coniugi", "accorpamento",
              "a Torrebruna si risposa uno su 75: tre coniugi sono un errore",
              troppi_coniugi, "persone con almeno un'unione"),
    Controllo("cognomi inconciliabili", "accorpamento",
              "una scheda che porta cinque cognomi che non si somigliano",
              cognomi_inconciliabili, "persone con piu' di una menzione"),
    Controllo("eta' incoerente con l'atto di nascita", "sospetto",
              "oltre dieci anni di scarto: o l'eta' e' letta male, o non e' lui",
              eta_incoerente, "menzioni con eta' e nascita certa"),
)

# Quante persone dell'archivio ogni controllo poteva colpire. Sono query
# a parte e non un campo del controllo perche' piu' controlli guardano la
# stessa popolazione, e ripetere la query sarebbe solo un'occasione per
# scriverla in due modi diversi.
DENOMINATORI: dict[str, str] = {
    "persone con almeno un'unione":
        "SELECT COUNT(*) FROM (SELECT marito p FROM unioni UNION "
        "SELECT moglie FROM unioni)",
    "persone con piu' di una menzione":
        "SELECT COUNT(*) FROM individui WHERE menzioni > 1",
    "figli con un padre":
        "SELECT COUNT(DISTINCT figlio) FROM legami WHERE tipo='padre'",
    "figli con una madre":
        "SELECT COUNT(DISTINCT figlio) FROM legami WHERE tipo='madre'",
    "figli con entrambi i genitori":
        "SELECT COUNT(*) FROM (SELECT figlio FROM legami GROUP BY figlio "
        "HAVING COUNT(DISTINCT tipo)=2)",
    "madri con due o piu' parti":
        "SELECT COUNT(*) FROM (SELECT l.genitore FROM legami l "
        "JOIN atti a ON a.id=l.atto WHERE a.tipo='nascita' AND l.tipo='madre' "
        "GROUP BY l.genitore HAVING COUNT(*)>1)",
    "legami con entrambe le nascite note":
        "SELECT COUNT(*) FROM legami l JOIN individui g ON g.id=l.genitore "
        "JOIN individui f ON f.id=l.figlio WHERE g.anno_nascita IS NOT NULL "
        "AND f.anno_nascita IS NOT NULL",
    "parti con la nascita del genitore nota":
        "SELECT COUNT(*) FROM legami l JOIN atti a ON a.id=l.atto "
        "JOIN individui g ON g.id=l.genitore WHERE a.tipo='nascita' "
        "AND g.anno_nascita IS NOT NULL",
    "parti con la morte del genitore nota":
        "SELECT COUNT(*) FROM legami l JOIN atti a ON a.id=l.atto "
        "JOIN individui g ON g.id=l.genitore WHERE a.tipo='nascita' "
        "AND g.anno_morte IS NOT NULL",
    "menzioni di persone con atto di morte":
        "SELECT COUNT(*) FROM menzioni m JOIN individui i ON i.id=m.individuo "
        "WHERE i.anno_morte IS NOT NULL",
    "persone con un atto di morte":
        "SELECT COUNT(*) FROM individui WHERE anno_morte IS NOT NULL",
    "persone con un atto di nascita":
        "SELECT COUNT(*) FROM individui WHERE nascita_origine='certa'",
    "persone con nascita e morte":
        "SELECT COUNT(*) FROM individui WHERE anno_nascita IS NOT NULL "
        "AND anno_morte IS NOT NULL",
    "persone con nascita certa":
        "SELECT COUNT(*) FROM individui WHERE nascita_origine='certa'",
    "unioni": "SELECT COUNT(*) FROM unioni",
    "unioni documentate da un atto":
        "SELECT COUNT(*) FROM unioni WHERE origine='matrimonio'",
    "persone con genitori": "SELECT COUNT(DISTINCT figlio) FROM legami",
    "persone con due o piu' unioni":
        "SELECT COUNT(*) FROM (SELECT marito FROM unioni WHERE marito IS NOT NULL "
        "GROUP BY marito HAVING COUNT(*)>1)",
    "coppie con figli":
        "SELECT COUNT(*) FROM (SELECT p.genitore, m.genitore FROM legami p "
        "JOIN legami m ON m.figlio=p.figlio AND m.tipo='madre' "
        "WHERE p.tipo='padre' GROUP BY p.genitore, m.genitore)",
    "menzioni con eta' e nascita certa":
        "SELECT COUNT(*) FROM menzioni m JOIN persone p ON p.id=m.persona "
        "JOIN individui i ON i.id=m.individuo WHERE p.eta IS NOT NULL "
        "AND i.nascita_origine='certa'",
}


def analizza(conn: sqlite3.Connection) -> list[Esito]:
    conn.row_factory = sqlite3.Row
    return [Esito(controllo, controllo.trova(conn)) for controllo in CONTROLLI]


# ---------------------------------------------------------------------------
# Il collegamento: la frammentazione che nessun controllo vede
# ---------------------------------------------------------------------------
#
# Tutto cio' che sta sopra conta **errori**. Ma l'errore piu' costoso di
# questa ricostruzione non e' contabile in quel modo: una persona spezzata
# in due schede non viola niente. Non produce un'assurdita', non
# contraddice nessun atto — produce soltanto un albero con meno parentele
# di quante l'archivio ne contenga, e chi guarda non ha modo di
# accorgersene.
#
# Queste misure lo guardano dal verso opposto: invece di contare cio' che
# e' rotto, contano **quanto l'albero tiene**. Quanti bambini nati qui
# l'archivio ritrova da adulti, quante vite si chiudono fra la nascita e
# la morte, quante generazioni si tengono in fila. Se una modifica al
# riconoscimento le fa salire senza far salire gli impossibili, ha
# funzionato; se le fa scendere, ha spezzato qualcosa anche quando tutti
# gli altri numeri migliorano.
#
# Vanno lette **insieme** ai controlli, mai da sole. Un sistema che
# fonde tutto con tutto le porta al massimo e produce un albero falso.

# Gli anni dopo i quali un bambino ritrovato e' un bambino ritrovato
# *da adulto*. La misura e' insensibile alla soglia — fra dieci e
# ventun anni il conto si muove del 10% — e questo e' il motivo per cui
# ci si puo' fidare: non sta misurando la soglia, sta misurando i
# collegamenti.
ETA_ADULTA = 18

COLLEGAMENTO: dict[str, str] = {
    "persone riconosciute":
        "SELECT COUNT(*) FROM individui",
    "che si reggono su una menzione sola":
        "SELECT COUNT(*) FROM individui WHERE menzioni = 1",
    "con atto di nascita":
        "SELECT COUNT(*) FROM individui WHERE nascita_origine = 'certa'",
    "nati qui e ritrovati almeno una volta":
        "SELECT COUNT(*) FROM individui "
        "WHERE nascita_origine = 'certa' AND menzioni > 1",
    "nati qui e ritrovati da adulti":
        f"SELECT COUNT(*) FROM individui WHERE nascita_origine = 'certa' "
        f"AND anno_ultimo - anno_nascita >= {ETA_ADULTA}",
    "nati qui di cui si conoscono i figli":
        "SELECT COUNT(*) FROM individui i WHERE i.nascita_origine = 'certa' "
        "AND EXISTS (SELECT 1 FROM legami l WHERE l.genitore = i.id)",
    "morti di cui si conosce anche la nascita":
        "SELECT COUNT(*) FROM individui "
        "WHERE anno_morte IS NOT NULL AND nascita_origine = 'certa'",
    "persone di cui si conoscono i genitori":
        "SELECT COUNT(DISTINCT figlio) FROM legami",
    "persone di cui si conoscono i figli":
        "SELECT COUNT(DISTINCT genitore) FROM legami",
    "catene nonno-genitore-figlio":
        "SELECT COUNT(*) FROM legami nipote "
        "JOIN legami figlio ON figlio.figlio = nipote.genitore",
    "coppie":
        "SELECT COUNT(*) FROM unioni",
    "coppie con atto di matrimonio":
        "SELECT COUNT(*) FROM unioni WHERE origine = 'matrimonio'",
}


def collegamento(conn: sqlite3.Connection) -> dict[str, int]:
    """Quanto l'albero tiene insieme le vite, misura per misura."""
    return {
        voce: conn.execute(sql).fetchone()[0]
        for voce, sql in COLLEGAMENTO.items()
    }


def conteggi(esiti: list[Esito]) -> dict[str, int]:
    totali: dict[str, int] = defaultdict(int)
    for esito in esiti:
        totali[esito.controllo.categoria] += esito.quanti
    return dict(totali)


def scrivi_report(
    conn: sqlite3.Connection, destinazione: Path, esempi: int = 10
) -> tuple[Path, list[Esito]]:
    esiti = analizza(conn)
    totali = conteggi(esiti)

    righe = [
        "# Quanto regge l'albero",
        "",
        "Ogni riga di questo elenco e' un errore **certo** della "
        "ricostruzione, non un dubbio: sono cose che nel mondo non "
        "possono essere accadute.",
        "",
        f"- **{totali.get('impossibile', 0)}** cose che non possono essere vere",
        f"- **{totali.get('frammentazione', 0)}** persone o famiglie spezzate in due",
        f"- **{totali.get('sospetto', 0)}** letture da ricontrollare",
        "",
        "| controllo | categoria | casi | su |",
        "|---|---|---:|---:|",
    ]
    for esito in sorted(esiti, key=lambda e: (-e.quanti, e.controllo.nome)):
        query = DENOMINATORI.get(esito.controllo.denominatore)
        su = str(conn.execute(query).fetchone()[0]) if query else ""
        righe.append(
            f"| {esito.controllo.nome} | {esito.controllo.categoria} "
            f"| {esito.quanti} | {su} |"
        )

    for esito in sorted(esiti, key=lambda e: -e.quanti):
        if not esito.casi:
            continue
        righe += ["", f"## {esito.controllo.nome} — {esito.quanti}", ""]
        if esito.controllo.spiega:
            righe += [f"*{esito.controllo.spiega}*", ""]
        righe += [f"- {caso.descrizione}" for caso in esito.casi[:esempi]]
        if esito.quanti > esempi:
            righe.append(f"- …e altri {esito.quanti - esempi}")

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text("\n".join(righe) + "\n", encoding="utf-8")
    return destinazione, esiti
