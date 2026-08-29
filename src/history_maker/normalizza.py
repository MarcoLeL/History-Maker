"""Ripulitura delle letture prima di contarle.

Il prompt chiede al modello di lasciare ``null`` un dato che non riesce a
leggere e di spiegarlo in ``parti_illeggibili``. Sulle pagine vere il
modello fa spesso una terza cosa: scrive il dato **e** ci attacca un
marcatore di dubbio, ``Bracchi [?]``, ``Trinchella[?]``, ``Muselle[?]``.

Preso alla lettera, ``Bracchi [?]`` e ``Bracchi`` sono due cognomi
diversi: la stessa famiglia si spezza in due, le frequenze si sfaldano e
la fase 5 — che ragiona proprio sulle frequenze — perde il segnale che
deve trovare. Non e' una questione di interpretazione, e' rumore di
formato: il marcatore appartiene ai metadati della lettura, non al nome.

Qui il dubbio viene **separato**, non buttato: il valore torna pulito e
il fatto che fosse incerto sopravvive a parte, per finire in una colonna
del database. Nessuna informazione si perde, e i conteggi tornano a
contare le famiglie invece delle grafie del dubbio.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from history_maker import paleografia

# ``[?]`` e ``(?)`` sono i modi in cui il modello segna "l'ho letto cosi',
# ma non ne sono sicuro". Vanno tolti **ovunque compaiano**, non solo in
# coda: su un nome composto il modello marca ogni elemento dubbio, e
# 'Petriccio[?] Flajo[?]' ripulito solo in fondo resterebbe
# 'Petriccio[?] Flajo' — un marcatore in mezzo a un cognome, che nei
# conteggi vale come una famiglia a se'.
MARCATORE = re.compile(r"\s*(?:\[\s*\?\s*\]|\(\s*\?\s*\))")

# Un punto interrogativo nudo si toglie invece solo in fondo: in mezzo a
# un valore non e' detto che sia un marcatore.
MARCATORE_FINALE = re.compile(r"\s*\?\s*$")

# ``B[...]`` segnala un pezzo che non si legge affatto. Va tenuto — dice
# dove sta il buco — ma il valore che lo contiene resta incerto.
LACUNA = re.compile(r"\[\s*\.\.\.\s*\]")


def separa_incertezza(valore: str | None) -> tuple[str | None, bool]:
    """Divide una lettura nel suo valore e nel dubbio che la accompagna.

    Restituisce ``(valore ripulito, era incerto)``. Un valore che resta
    vuoto dopo la ripulitura — un ``[?]`` da solo — diventa ``None``:
    e' un dato che il modello non ha letto, non un nome fatto di dubbio.

    >>> separa_incertezza("Bracchi [?]")
    ('Bracchi', True)
    >>> separa_incertezza("Franchella")
    ('Franchella', False)
    >>> separa_incertezza("[?]")
    (None, True)
    """
    if valore is None:
        return None, False

    ripulito = valore.strip()

    ripulito, quanti = MARCATORE.subn(" ", ripulito)
    incerto = bool(quanti)

    # Il marcatore nudo puo' essere ripetuto ("Muselle [?]?"): si toglie
    # finche' ce n'e', altrimenti ne resterebbe uno attaccato al valore.
    while True:
        senza, quanti = MARCATORE_FINALE.subn("", ripulito)
        if not quanti:
            break
        ripulito, incerto = senza, True

    # Togliendo un marcatore interno resta un doppio spazio.
    ripulito = re.sub(r"\s+", " ", ripulito).strip()

    if LACUNA.search(ripulito):
        incerto = True

    return (ripulito or None), incerto


# --- raggruppamento delle varianti sull'intero corpus ----------------------
#
# Il criterio non e' la fascia di frequenza ma la somiglianza reciproca.
# La differenza non e' teorica: nel 1809 di Torrebruna le quattro forme di
# 'Cicchillitto' contano 6, 5, 3 e 3 occorrenze, quindi nessuna e' "rara"
# (<=2) ne' "frequente" (>=10). Una regola che confronti raro contro
# frequente sul gruppo piu' numeroso dei dati non scatterebbe mai.

# Sotto questa somiglianza due forme sono due famiglie diverse: 'Lella' e
# 'Colella' stanno a 0,71 e devono restare separate.
SOGLIA_GRUPPO = 0.80

# Quando applicare da soli, invece di proporre. Le due regole si leggono
# come un compromesso fra quanto le forme si somigliano e quanto la
# maggioritaria domina: piu' sono simili, meno dominanza serve.
REGOLE_AUTOMATICHE = (
    (0.85, 4.0),   # 'Trinchella' (2) accanto a 'Franchella' (12): Tr/Fr e' mano
    (0.92, 3.0),
)


@dataclass(frozen=True)
class Proposta:
    """Una variante che ricorre e su cui il calcolo non se la sente.

    Va nel glossario, cioe' sotto gli occhi di chi conosce il paese: e'
    esattamente il caso in cui la statistica ha esaurito quello che sa
    fare e la decisione spetta a una persona.
    """

    letto: str
    proposto: str
    occorrenze_lette: int
    occorrenze_proposte: int
    somiglianza: float

    @property
    def motivo(self) -> str:
        return (
            f"'{self.letto}' ricorre {self.occorrenze_lette} volte, "
            f"'{self.proposto}' {self.occorrenze_proposte} "
            f"(somiglianza {self.somiglianza:.2f}): troppo simili per essere "
            f"estranee, troppo alla pari per decidere da soli"
        )


def _raggruppa(forme: list[str]) -> list[list[str]]:
    """Raccoglie in gruppi le forme che si somigliano.

    Le forme vengono unite per contatto: se A somiglia a B e B a C,
    stanno tutte e tre nello stesso gruppo anche se A e C si somigliano
    meno. E' il comportamento giusto per una catena di grafie dello
    stesso cognome, dove ogni scrivano si scosta un poco dal precedente.
    """
    padre = {f: f for f in forme}

    def radice(f: str) -> str:
        while padre[f] != f:
            padre[f] = padre[padre[f]]
            f = padre[f]
        return f

    for i, a in enumerate(forme):
        for b in forme[i + 1 :]:
            vicine = paleografia.forma_canonica(a) == paleografia.forma_canonica(b) or (
                paleografia.somiglianza(a, b) >= SOGLIA_GRUPPO
            )
            if vicine:
                ra, rb = radice(a), radice(b)
                if ra != rb:
                    padre[ra] = rb

    gruppi: dict[str, list[str]] = {}
    for f in forme:
        gruppi.setdefault(radice(f), []).append(f)
    return list(gruppi.values())


def _abbastanza_evidente(somiglianza: float, dominanza: float) -> bool:
    return any(
        somiglianza >= s_min and dominanza >= d_min for s_min, d_min in REGOLE_AUTOMATICHE
    )


def raggruppa_varianti(
    frequenze: Counter[str],
) -> tuple[dict[str, str], list[Proposta]]:
    """Divide le varianti fra quelle da unire e quelle da sottoporre.

    Restituisce ``(correzioni, proposte)``: le prime si applicano da sole
    perche' l'evidenza e' schiacciante, le seconde finiscono nel glossario
    ordinate per quanto ricorrono — una forma vista una volta sola non
    merita il tempo di nessuno, una vista cinque volte si'.

    Il confronto e' sull'**intero corpus**: una variante si giudica per
    quante volte ricorre ovunque, non dentro la pagina o l'anno in cui
    capita.
    """
    correzioni: dict[str, str] = {}
    proposte: list[Proposta] = []

    for gruppo in _raggruppa(list(frequenze)):
        if len(gruppo) < 2:
            continue
        # La forma piu' attestata fa da capofila; a parita' vince
        # l'ordine alfabetico, per non dipendere dall'ordine di lettura.
        canonica = max(sorted(gruppo), key=lambda f: frequenze[f])

        for variante in gruppo:
            if variante == canonica:
                continue
            quante = frequenze[variante]
            dominanza = frequenze[canonica] / quante if quante else float("inf")
            simile = paleografia.somiglianza(variante, canonica)

            # Due gradi di "e' la stessa cosa scritta diversamente".
            # La prima e' sola punteggiatura — "Dettorre" e "d'Ettorre"
            # sono la stessa stringa una volta tolti apostrofo e
            # maiuscole: non c'e' nessuna lettura da decidere, e la
            # dominanza non deve entrarci. La seconda sono le convenzioni
            # note dei cognomi meridionali (prefissi staccati o attaccati,
            # doppie instabili) che 'forma_canonica' gia' collassa.
            stessa_stringa = paleografia.normalizza(variante).replace(
                " ", ""
            ) == paleografia.normalizza(canonica).replace(" ", "")
            stessa_canonica = paleografia.forma_canonica(
                variante
            ) == paleografia.forma_canonica(canonica)

            # Nessuna evidenza sulla direzione: due forme viste lo stesso
            # numero di volte non si correggono a vicenda, per quanto si
            # somiglino. Senza questa guardia 'Pinti' (1) e 'Pinnti' (1)
            # venivano unite d'ufficio, e su quale delle due fosse la
            # forma buona decideva l'ordine alfabetico.
            alla_pari = quante >= frequenze[canonica]

            if not alla_pari and (
                stessa_stringa or stessa_canonica or _abbastanza_evidente(simile, dominanza)
            ):
                correzioni[variante] = canonica
            else:
                proposte.append(
                    Proposta(
                        letto=variante,
                        proposto=canonica,
                        occorrenze_lette=quante,
                        occorrenze_proposte=frequenze[canonica],
                        somiglianza=simile,
                    )
                )

    proposte.sort(key=lambda p: (-p.occorrenze_lette, p.letto))
    return correzioni, proposte
