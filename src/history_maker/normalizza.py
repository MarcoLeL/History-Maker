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
from dataclasses import dataclass, replace

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

# Sui toponimi si puo' scendere: le contrade di un paese sono un insieme
# CHIUSO — non esiste la "via forestiera vera" che rende rischioso unire
# due cognomi — e soprattutto qui non si corregge niente in automatico,
# si propone soltanto. Una soglia piu' generosa mette qualche accostamento
# in piu' sotto gli occhi di chi sa, e non sporca nessun dato.
# Serve davvero: 'rua nuorro' e 'rua nuovo' stanno a 0,78 e 'lama nuorro'
# a 0,70, quindi con la soglia dei cognomi la stessa contrada resterebbe
# divisa in tre. Piu' in basso pero' non si scende: a 0,65 'Porta del
# Colle' si mangia 'Portamurella', che e' un altro posto.
SOGLIA_TOPONIMI = 0.70

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


def _raggruppa(forme: list[str], soglia: float = SOGLIA_GRUPPO) -> list[list[str]]:
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
                paleografia.somiglianza(a, b) >= soglia
            )
            if vicine:
                ra, rb = radice(a), radice(b)
                if ra != rb:
                    padre[ra] = rb

    gruppi: dict[str, list[str]] = {}
    for f in forme:
        gruppi.setdefault(radice(f), []).append(f)
    return list(gruppi.values())


def _piu_vicina(
    variante: str, gruppo: list[str], frequenze: Counter[str]
) -> tuple[str | None, float]:
    """La forma piu' simile alla variante, fra quelle piu' attestate di lei.

    Una proposta ha senso solo verso una forma **vicina**, e la vicinanza
    va misurata direttamente: attraverso la catena del gruppo si arriva a
    forme che non si somigliano affatto.

    Due forme ugualmente attestate restano una proposta — sono comunque
    la stessa famiglia scritta in due modi, e la scelta spetta a chi
    legge — ma la coppia va sottoposta una volta sola: a parita' di
    occorrenze si propone sempre dalla forma alfabeticamente successiva
    verso la precedente, altrimenti comparirebbero entrambe le direzioni.
    """
    migliore, punteggio = None, 0.0
    for altra in sorted(gruppo):
        if altra == variante:
            continue
        if frequenze[altra] < frequenze[variante]:
            continue
        if frequenze[altra] == frequenze[variante] and altra > variante:
            continue
        simile = paleografia.somiglianza(variante, altra)
        if simile >= SOGLIA_GRUPPO and simile > punteggio:
            migliore, punteggio = altra, simile
    return migliore, punteggio


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

        for variante in gruppo:
            # Ogni variante si giudica contro la sua parente piu' stretta
            # fra le forme almeno altrettanto attestate, non contro il
            # capofila del gruppo: 'Cicchilitto' e 'Cicchillitto' sono la
            # stessa forma a meno di una doppia e devono unirsi fra loro,
            # anche se il capofila del gruppo e' la forma in -i. La forma
            # piu' attestata di tutte non ha nessun bersaglio e resta dov'e'.
            bersaglio, simile = _piu_vicina(variante, gruppo, frequenze)
            if bersaglio is None:
                continue

            quante = frequenze[variante]
            dominanza = frequenze[bersaglio] / quante if quante else float("inf")
            canonica = bersaglio

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
                continue

            proposte.append(
                Proposta(
                    letto=variante,
                    proposto=bersaglio,
                    occorrenze_lette=quante,
                    occorrenze_proposte=frequenze[bersaglio],
                    somiglianza=simile,
                )
            )

    finali = _senza_catene(correzioni)

    # Una proposta deve puntare a una forma che esistera' ancora dopo le
    # correzioni automatiche. Senza questo si legge 'Bellicia -> Pellicia'
    # mentre 'Pellicia' e' a sua volta gia' ricondotta a 'Pelliccia': la
    # decisione verrebbe presa su una forma che nel database non c'e' piu'.
    proposte = [
        replace(
            p,
            proposto=finali.get(p.proposto, p.proposto),
            occorrenze_proposte=frequenze[finali.get(p.proposto, p.proposto)],
        )
        for p in proposte
        if finali.get(p.proposto, p.proposto) != p.letto
    ]

    proposte.sort(key=lambda p: (-p.occorrenze_lette, p.letto))
    return finali, proposte


def _senza_catene(correzioni: dict[str, str]) -> dict[str, str]:
    """Fa puntare ogni variante alla forma finale, non alla successiva.

    Giudicando ogni variante contro la parente piu' stretta si formano
    catene: 'Pellicia' verso 'Pellicci' e 'Pellicci' verso 'Pelliccia'.
    Applicate cosi' com'e', la prima resterebbe su una forma che a sua
    volta viene corretta.
    """
    risolte: dict[str, str] = {}
    for variante in correzioni:
        vista = {variante}
        destinazione = correzioni[variante]
        while destinazione in correzioni and destinazione not in vista:
            vista.add(destinazione)
            destinazione = correzioni[destinazione]
        risolte[variante] = destinazione
    return risolte


# --- le letture alternative che il modello scrive nelle note ---------------
#
# Quando e' incerto, il modello non si limita a marcare il dubbio: spesso
# scrive anche la seconda lettura che ha considerato — "(Tommolilli /
# Iemminilli)", "'Pope', 'Popa' o simile", "(Iorzo[?] / Iorio)". Leggere
# solo il campo 'cognome' butta via quell'informazione.
#
# E' un segnale di natura diversa dalla somiglianza, e arriva dove quella
# non puo': 'Tommolilli' dista 0,67 da 'Femminilli', sotto qualunque
# soglia sensata, ma la sua alternativa 'Iemminilli' dista 0,90. La catena
# arriva a destinazione passando per l'alternativa, non per la forma letta.

# Un cognome plausibile: iniziale maiuscola, poi lettere, apostrofi,
# spazi e gli eventuali marcatori di dubbio del modello.
_FORMA = r"[A-Z][A-Za-z\u00e0\u00e8\u00e9\u00ec\u00f2\u00f9'\u2019\[\]?. ]{2,24}"

SCHEMI_ALTERNATIVA = (
    re.compile(rf"\(({_FORMA})\s*/\s*({_FORMA})\)"),       # (Tommolilli / Iemminilli)
    re.compile(rf"'({_FORMA})'\s*[,/]?\s*'({_FORMA})'"),   # 'Pope', 'Popa'
    re.compile(rf"\b({_FORMA})\s*/\s*({_FORMA})\b"),       # Iorzo[?] / Iorio
)


def alternative_dalle_note(note: str | None) -> list[str]:
    """Le grafie che la nota propone in alternativa a quella scelta.

    Restituisce le forme ripulite dai marcatori di dubbio, senza
    duplicati e nell'ordine in cui compaiono. Una nota che non propone
    niente da' una lista vuota.
    """
    if not note:
        return []

    trovate: list[str] = []
    for schema in SCHEMI_ALTERNATIVA:
        for riscontro in schema.finditer(note):
            for gruppo in riscontro.groups():
                forma = separa_incertezza(gruppo)[0]
                forma = forma.strip(" '\u2019.") if forma else None
                if forma and forma not in trovate:
                    trovate.append(forma)
        if trovate:
            break  # il primo schema che riconosce qualcosa e' il piu' specifico
    return trovate


def correzioni_dalle_alternative(
    letture: list[tuple[str, str | None]],
    attestate: Counter[str],
    soglia: float = 0.85,
    minimo_attestazioni: int = 3,
) -> dict[str, str]:
    """Corregge una lettura quando la sua alternativa e' una forma nota.

    ``letture`` sono coppie ``(cognome, note)`` delle sole letture che il
    modello ha dichiarato incerte; ``attestate`` sono le forme lette
    **senza** dubbio, con quante volte ricorrono.

    L'evidenza qui e' forte per due motivi che si sommano: il modello si
    e' dichiarato insicuro, e ha proposto lui stesso una grafia che nel
    corpus esiste gia' e viene letta senza esitazione da altre parti.
    """
    corrette: dict[str, str] = {}
    for cognome, note in letture:
        if not cognome or cognome in corrette or cognome in attestate:
            continue
        for alternativa in alternative_dalle_note(note):
            migliore, punteggio = None, soglia
            for forma, quante in attestate.items():
                if quante < minimo_attestazioni:
                    continue
                simile = paleografia.somiglianza(alternativa, forma)
                if simile >= punteggio:
                    migliore, punteggio = forma, simile
            if migliore and migliore != cognome:
                corrette[cognome] = migliore
                break
    return corrette


# --- toponimi ---------------------------------------------------------------
#
# Le contrade di un paese sono un insieme CHIUSO: una via o esiste o non
# esiste, e non c'e' l'equivalente del "forestiero vero" che rende
# rischioso unificare i cognomi. Per questo qui si puo' essere piu'
# generosi. Restano proposte, non correzioni: a dire come si chiama
# davvero una strada e' chi ci e' passato, non una distanza fra stringhe.

# Le parole che introducono un toponimo nel formulario ottocentesco.
_INTRODUCE = r"(?:strada|via|rua|contrada|porta|piazza|piano|lama|vico|largo)"

# Il corpo del toponimo: quello che segue, fino alla prossima virgola.
_TOPONIMO = re.compile(rf"\b{_INTRODUCE}\b[\s\w'\u2019àèéìòù\[\]?.]{{0,40}}", re.IGNORECASE)

# Parole che non distinguono un toponimo dall'altro: compaiono in mezzo a
# tutte le formule ("strada a piedi la...", "strada vicino il...") e se
# entrassero nel confronto due vie diverse sembrerebbero simili solo
# perche' condividono il preambolo.
_VUOTE = {
    "strada", "via", "della", "delle", "dello", "degli", "del", "di", "da",
    "la", "il", "lo", "le", "li", "a", "al", "alla", "in", "vicino", "sotto",
    "sopra", "dinanzi", "davanti", "avanti", "piedi", "capo", "cima", "fuori",
    "questa", "questo", "detta", "detto", "comune", "sudetta", "suddetta",
    "medesima", "istessa", "nella", "nel", "e", "ed", "che",
}


def nucleo_toponimo(valore: str | None) -> str | None:
    """Il cuore distintivo di un toponimo, senza il preambolo di formula.

    ``strada a piedi la Lama di Nuorro`` e ``strada della Rua di Nuovo``
    si riducono a ``lama nuorro`` e ``rua nuovo``: quello che resta e' la
    parte che davvero distingue una contrada dall'altra.
    """
    if not valore:
        return None
    riscontro = _TOPONIMO.search(valore)
    if not riscontro:
        return None
    pulito = separa_incertezza(riscontro.group(0))[0] or ""
    parole = [
        p for p in re.split(r"[\s.]+", paleografia.normalizza(pulito)) if p and p not in _VUOTE
    ]
    return " ".join(parole) or None


# La formula degli atti nomina il comune e la contrada nello stesso
# respiro — "in Torrebruna, strada della Trascinella" — e i due modelli
# li distribuiscono diversamente: Gemini mette il comune in
# 'persona.residenza' e la contrada in 'atto.luogo', Claude infilava
# entrambi nella residenza. Separarli qui, invece che chiederli separati
# nel prompt, e' cio' che rende la colonna indipendente da chi ha letto.
_SEPARATORI_LUOGO = re.compile(r"^\s*[,;:\-–—]\s*|\s*[,;:]\s*$")


def separa_comune(valore: str | None, comune: str) -> tuple[str | None, str | None]:
    """Divide un luogo nel comune e in cio' che lo precisa dentro il comune.

    Restituisce ``(comune riconosciuto o None, resto o None)``.

    >>> separa_comune("Torrebruna, strada della Trascinella", "Torrebruna")
    ('Torrebruna', 'strada della Trascinella')
    >>> separa_comune("Torrebruna", "Torrebruna")
    ('Torrebruna', None)
    >>> separa_comune("strada di Portamurella", "Torrebruna")
    (None, 'strada di Portamurella')
    >>> separa_comune("Castiglione Messer Marino", "Torrebruna")
    (None, 'Castiglione Messer Marino')

    Il comune si riconosce solo in TESTA: un "Torrebruna" che compare in
    coda — "la Torre sopra Torrebruna" — e' parte della descrizione del
    luogo, non l'intestazione, e toglierlo storpierebbe il toponimo.
    """
    if not valore:
        return None, None
    testo = valore.strip()
    if not testo:
        return None, None

    atteso = paleografia.normalizza(comune)
    parole = testo.split()
    quante_del_comune = len(comune.split())
    # La punteggiatura va tolta PRIMA del confronto: nella formula degli
    # atti il comune e' quasi sempre seguito da una virgola attaccata
    # ("Torrebruna, strada della Trascinella"), e confrontare
    # "Torrebruna," con "Torrebruna" farebbe fallire il caso piu' comune
    # di tutti.
    testa = paleografia.normalizza(
        " ".join(parole[:quante_del_comune]).strip(",;:.-–— ")
    )
    if not atteso or testa != atteso:
        return None, testo or None

    resto = " ".join(parole[quante_del_comune:])
    resto = _SEPARATORI_LUOGO.sub("", resto).strip()
    return comune, resto or None


_HA_TOPONIMO = re.compile(rf"\b{_INTRODUCE}\b", re.IGNORECASE)

# Una maiuscola in mezzo alla frase e' il segno che li' c'e' un nome
# proprio — "sopra la Torre", "Coste delle Pecchie" — e non una formula
# di cancelleria.
_NOME_PROPRIO = re.compile(r"\b[A-ZÀ-Ü][a-zà-ü]")


def via_da(luogo: str | None, comune: str) -> str | None:
    """La parte di un luogo che precisa DOVE dentro il comune.

    Contrada, strada, porta, la casa nominata dall'atto: cio' che serve a
    sapere in che parte del paese viveva una famiglia. Un luogo che nomina
    solo il comune non ha via, e restituisce ``None`` — un campo vuoto
    dichiarato, non una stringa vuota che finge di essere un dato.

    **Il comune di un forestiero non e' una via.** Una sposa di
    ``Castiglione Messer Marino`` ha una residenza, non una contrada di
    Torrebruna, e metterla in questa colonna renderebbe inservibile
    proprio la domanda per cui la colonna esiste: in che parte del paese
    stava una famiglia. Quindi cio' che non segue il nome del comune entra
    solo se si annuncia come toponimo — ``strada``, ``rua``, ``contrada``,
    ``porta`` — perche' i due modelli a volte scrivono la sola contrada
    senza premettere il comune.

    >>> via_da("Torrebruna, strada della Trascinella", "Torrebruna")
    'strada della Trascinella'
    >>> via_da("strada di Portamurella", "Torrebruna")
    'strada di Portamurella'
    >>> via_da("Castiglione Messer Marino", "Torrebruna") is None
    True
    >>> via_da("Torrebruna", "Torrebruna") is None
    True
    """
    riconosciuto, resto = separa_comune(luogo, comune)
    if resto is None:
        return None
    if _HA_TOPONIMO.search(resto):
        return resto
    # Senza una parola che annunci il toponimo, resta contrada solo cio'
    # che porta un nome proprio: "sopra la Torre" si', "in casa di sua
    # abitazione" no. Quella e' la formula con cui l'atto dice che
    # l'evento e' avvenuto in casa — un'informazione vera, ma non un
    # luogo del paese, e in una colonna che serve a raggruppare le
    # famiglie per vicinato sarebbe solo rumore.
    if riconosciuto is not None and _NOME_PROPRIO.search(resto):
        return resto
    return None
