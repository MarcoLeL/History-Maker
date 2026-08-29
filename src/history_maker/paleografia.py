"""Somiglianza fra forme scritte da una stessa mano ottocentesca.

Il problema che questo modulo risolve: in un comune di poche migliaia di
anime i cognomi sono pochi e ricorrono migliaia di volte. Una forma che
compare una volta sola e' sospetta — ma non necessariamente sbagliata.
Puo' essere una lettura errata di un cognome frequente, oppure un
forestiero vero: una sposa di un paese vicino, un soldato, un prete di
passaggio. La seconda cosa e' un dato storico prezioso, non rumore.

Quindi qui non si corregge nulla: si misura solo *quanto* due forme siano
confondibili per chi legge una grafia dell'Ottocento, e la decisione
resta a chi guarda l'immagine.
"""

from __future__ import annotations

import unicodedata

# Scambi ricorrenti nelle grafie corsive italiane del XIX secolo. Sono
# coppie non orientate: costano poco perche' un occhio esperto le confonde
# davvero, non perche' siano "quasi uguali" come stringhe.
#
# Questa tabella e' il punto da rivedere dopo aver visto i registri veri:
# ogni scrivano ha le sue abitudini, e le confusioni che contano a
# Torrebruna potrebbero non essere quelle generiche.
CONFUSIONI_SEMPLICI: tuple[tuple[str, str], ...] = (
    ("f", "s"),   # la s lunga (ſ) e' la confusione piu' comune in assoluto
    ("c", "e"),
    ("u", "n"),
    ("u", "v"),
    ("i", "e"),
    ("a", "o"),   # soprattutto in finale di parola
    ("t", "l"),
    ("r", "s"),
    ("g", "q"),
    ("b", "h"),
    ("m", "n"),
    ("d", "cl"),
)

# Sequenze che una mano corsiva rende con lo stesso numero di gambe.
CONFUSIONI_MULTIPLE: tuple[tuple[str, str], ...] = (
    ("m", "ni"),
    ("m", "in"),
    ("m", "iii"),
    ("n", "ri"),
    ("n", "ii"),
    ("w", "vi"),
)

# Iniziali maiuscole che nelle grafie cancelleresche si somigliano molto.
CONFUSIONI_MAIUSCOLE: tuple[tuple[str, str], ...] = (
    ("C", "G"), ("C", "E"), ("M", "N"), ("P", "R"), ("S", "L"),
    ("F", "T"), ("B", "R"), ("I", "J"), ("U", "V"), ("D", "O"),
)

COSTO_CONFUSIONE = 0.3   # scambio plausibile per la mano
COSTO_PIENO = 1.0        # sostituzione arbitraria

_PREFISSI = ("di ", "de ", "d'", "lo ", "la ", "del ", "della ", "dello ")


def _senza_accenti(testo: str) -> str:
    scomposto = unicodedata.normalize("NFKD", testo)
    return "".join(c for c in scomposto if not unicodedata.combining(c))


def normalizza(cognome: str) -> str:
    """Forma di confronto: minuscolo, senza accenti, apostrofi e spazi doppi."""
    testo = _senza_accenti(cognome or "").casefold()
    testo = testo.replace("'", " ").replace("`", " ").replace("-", " ")
    return " ".join(testo.split())


def forma_canonica(cognome: str) -> str:
    """Forma che collassa le differenze piu' comuni fra grafie.

    Serve a raggruppare senza calcolare distanze: ``Dinardo``,
    ``Di Nardo`` e ``De Nardo`` finiscono tutti su ``dnardo``. E' un
    setaccio grossolano, applicato prima della distanza pesata.
    """
    testo = normalizza(cognome)
    # I registri scrivono lo stesso cognome come "Di Nardo", "Dinardo" o
    # "De Nardo" a seconda dello scrivano: il prefisso va ridotto a una
    # sola lettera sia che sia staccato sia che sia attaccato.
    for prefisso in _PREFISSI:
        staccato, attaccato = prefisso, prefisso.replace(" ", "")
        if testo.startswith(staccato):
            testo = prefisso[0] + testo[len(staccato) :]
            break
        if testo.startswith(attaccato) and len(testo) > len(attaccato) + 2:
            testo = prefisso[0] + testo[len(attaccato) :]
            break
    # Le doppie sono la variazione ortografica piu' instabile nei registri.
    collassato = []
    for carattere in testo.replace(" ", ""):
        if not collassato or collassato[-1] != carattere:
            collassato.append(carattere)
    return "".join(collassato)


def _tabella_costi() -> dict[frozenset[str], float]:
    tabella: dict[frozenset[str], float] = {}
    for a, b in CONFUSIONI_SEMPLICI:
        tabella[frozenset((a, b))] = COSTO_CONFUSIONE
    for a, b in CONFUSIONI_MAIUSCOLE:
        tabella[frozenset((a.lower(), b.lower()))] = COSTO_CONFUSIONE
    return tabella


_COSTI = _tabella_costi()


def _costo_sostituzione(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return _COSTI.get(frozenset((a, b)), COSTO_PIENO)


def distanza(prima: str, seconda: str) -> float:
    """Distanza di edit pesata sulle confusioni della mano ottocentesca.

    Rispetto a una Levenshtein normale, sostituire ``f`` con ``s`` costa
    0,3 invece di 1: e' uno scambio che un lettore fa di continuo, non un
    errore qualsiasi. Le sequenze come ``m``/``ni``, che in corsivo hanno
    lo stesso numero di gambe, sono trattate come un solo scambio.
    """
    a, b = normalizza(prima).replace(" ", ""), normalizza(seconda).replace(" ", "")
    if a == b:
        return 0.0
    if not a or not b:
        return float(len(a) + len(b))

    # riga[j] = distanza fra a[:i] e b[:j]
    precedenti: list[list[float]] = [
        [float(j) for j in range(len(b) + 1)]
    ]
    for i in range(1, len(a) + 1):
        riga = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            riga[j] = min(
                precedenti[-1][j] + 1.0,                                   # cancellazione
                riga[j - 1] + 1.0,                                         # inserzione
                precedenti[-1][j - 1] + _costo_sostituzione(a[i - 1], b[j - 1]),
            )
            # Scambi fra sequenze di lunghezza diversa (m <-> ni).
            for corta, lunga in CONFUSIONI_MULTIPLE:
                for x, y in ((corta, lunga), (lunga, corta)):
                    if (
                        i >= len(x) and j >= len(y)
                        and a[i - len(x) : i] == x
                        and b[j - len(y) : j] == y
                    ):
                        candidato = precedenti[-len(x)][j - len(y)] + COSTO_CONFUSIONE
                        riga[j] = min(riga[j], candidato)
        precedenti.append(riga)
    return precedenti[-1][-1]


def somiglianza(prima: str, seconda: str) -> float:
    """Distanza normalizzata sulla lunghezza: 1.0 identiche, 0.0 estranee."""
    a, b = normalizza(prima).replace(" ", ""), normalizza(seconda).replace(" ", "")
    massimo = max(len(a), len(b))
    if massimo == 0:
        return 1.0
    return max(0.0, 1.0 - distanza(prima, seconda) / massimo)
