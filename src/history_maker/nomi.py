"""Igiene dei nomi e delle eta', prima di poter ricostruire le parentele.

La fase 4 normalizza i **cognomi** e li lascia in una forma su cui si puo'
contare. Per l'albero genealogico non basta, perche' l'identita' di una
persona in questi registri non sta nel cognome — a Torrebruna i Pelliccia
sono 3.556 menzioni — ma nella terna *nome di battesimo, cognome, nomi dei
genitori*. E il nome di battesimo, fino a qui, non e' mai stato toccato.

Questo modulo prepara i quattro dati su cui si regge il riconoscimento:

* **l'eta'**, che i registri scrivono in lettere (``trentatre'``, ``mesi
  nove``) e che tradotta in numero da' l'anno di nascita — la coordinata
  che separa il nonno dal nipote omonimo;
* **il patronimico**, che il formulario nasconde dentro il nome
  (``Giuseppe di Carmine``): e' l'unico caso in cui la fonte stessa
  disambigua due omonimi, e buttarlo sarebbe uno spreco;
* **i nomi invertiti**, perche' dal 1881 lo scrivano passa a "Cognome
  Nome" e chi legge lo riporta com'e' scritto: ``Pepe Clorinda`` e'
  Clorinda Pepe, e se resta cosi' diventa una famiglia inesistente;
* **le varianti del nome di battesimo**, che sono lo stesso problema dei
  cognomi (``Giyseppe`` per ``Giuseppe``) e si risolvono con lo stesso
  strumento gia' scritto per quelli.

Nessuna di queste correzioni distrugge la lettura originale: le colonne
``*_letto`` della fase 4 restano quelle che sono, e qui si producono
sempre valori nuovi accanto, mai al posto.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from history_maker import normalizza, paleografia

# ---------------------------------------------------------------------------
# L'eta'
# ---------------------------------------------------------------------------

_UNITA = {
    "uno": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9,
}

_FINO_A_VENTI = {
    "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14,
    "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18,
    "diciannove": 19,
}

_DECINE = {
    "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60,
    "settanta": 70, "ottanta": 80, "novanta": 90,
}


def _tabella_numeri() -> dict[str, int]:
    """Tutti i numeri da 1 a 109 come li scrive un atto di stato civile.

    Si generano invece di elencarli perche' la composizione italiana ha
    due sole irregolarita' — la decina perde la vocale davanti a *uno* e
    *otto* (``ventuno``, ``trentotto``), e *tre* prende l'accento
    (``ventitre'``) — e scriverle una volta sola vale piu' di novanta
    righe di tabella in cui e' facile sbagliare una voce.
    """
    numeri: dict[str, int] = {}
    numeri.update(_UNITA)
    numeri.update(_FINO_A_VENTI)

    for decina, valore in _DECINE.items():
        numeri[decina] = valore
        for unita, quanto in _UNITA.items():
            if unita == "un":
                continue
            radice = decina[:-1] if unita in ("uno", "otto") else decina
            numeri[radice + unita] = valore + quanto

    numeri["cento"] = 100
    for unita, quanto in _UNITA.items():
        if unita == "un":
            continue
        numeri["cento" + unita] = 100 + quanto
    return numeri


NUMERI = _tabella_numeri()

# Le parole che accompagnano l'eta' senza aggiungerci niente. Vanno tolte
# prima di cercare il numero, non dopo: 'di anni trentasei' e 'trentasei
# anni' devono arrivare alla stessa voce di tabella.
_ZAVORRA = re.compile(
    r"\b(?:di|d'|dell'|eta'|eta|anni|anno|circa|incirca|presso|verso|"
    r"compiuti|compiti|in|su|sui|sul|about)\b",
    re.IGNORECASE,
)

_APPROSSIMA = re.compile(r"\b(?:circa|incirca|verso|presso|about)\b", re.IGNORECASE)

# 'maggiore di eta'' non e' un numero: e' la formula con cui il formulario
# dice "ha l'eta' per firmare". Vale come limite inferiore — 21 anni nel
# Codice civile del Regno delle Due Sicilie e poi in quello italiano — e
# non come misura.
_MAGGIORENNE = re.compile(r"\bmagg?[io]or\w*\b", re.IGNORECASE)
_MINORENNE = re.compile(r"\bmin[oe]r\w*\b", re.IGNORECASE)

ETA_MAGGIORE = 21

_UNITA_TEMPO = (
    # (schema, quanti anni vale una unita')
    # L'apostrofo entra nello schema perche' i registri lo usano al posto
    # dello spazio: "mes'uoto", "d'mezi'anno".
    (re.compile(r"\bme[sz]['i]?[ei]?\b", re.IGNORECASE), 1 / 12),
    (re.compile(r"\bgiorn[oi]\b", re.IGNORECASE), 1 / 365),
    (re.compile(r"\b(?:or[ae]|minut[oi])\b", re.IGNORECASE), 0.0),
    (re.compile(r"\bsettiman[ae]\b", re.IGNORECASE), 7 / 365),
)

# Le formule che dicono "appena nato" senza dare un numero. Valgono zero
# anni, non "eta' ignota": un neonato morto il giorno dell'atto e' nato
# quell'anno, ed e' un'informazione piena per l'albero — proprio quella
# che serve a chiudere il buco fra una nascita e la morte che la segue di
# poche settimane.
_APPENA_NATO = re.compile(
    r"\b(?:neonat[oa]|neondat[oa]|nat[oa]\s+oggi|pochi\s+giorni|"
    r"poche\s+ore|di\s+giorni|giorno)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Eta:
    """Un'eta' letta su un atto, tradotta in anni.

    ``anni`` e' frazionario apposta: un bambino di ``mesi nove`` non ha
    zero anni, ne ha 0,75, e la differenza conta quando da quell'eta' si
    ricava l'anno di nascita di chi muore a cavallo di capodanno.

    ``approssimata`` distingue ``trentasei`` da ``trenta circa`` e da
    ``maggiore di eta'``. La ricostruzione delle parentele usa l'eta' per
    escludere accostamenti impossibili, e su un'eta' approssimata la
    tolleranza deve allargarsi.
    """

    anni: float
    approssimata: bool
    letta: str
    # L'eta' e' un **limite inferiore**, non una misura: e' cosi' per la
    # formula 'maggiore di eta'', che dice soltanto «ha l'eta' per
    # firmare». Chi la usa per ricavare un anno di nascita deve saperlo,
    # perche' un maggiorenne di sessant'anni e' maggiorenne come uno di
    # ventuno — e trattarla come una misura inventa una data.
    minima: bool = False

    @property
    def anno_nascita(self) -> int:
        """Quanti anni togliere all'anno dell'atto. Sempre per difetto.

        Chi compie gli anni a novembre, il primo gennaio ne ha ancora
        quelli dell'anno prima: l'anno di nascita ricavato da un'eta' e'
        vero a meno di uno, sempre, e questo modulo non finge il
        contrario. Chi lo usa deve tenersi la tolleranza.
        """
        return int(self.anni)


def analizza_eta(testo: str | None) -> Eta | None:
    """Traduce in anni l'eta' scritta su un atto.

    >>> analizza_eta("trentasei").anni
    36.0
    >>> analizza_eta("di anni ventitre'").anni
    23.0
    >>> analizza_eta("mesi nove").anni
    0.75
    >>> analizza_eta("trenta circa").approssimata
    True
    >>> analizza_eta("maggiore di eta'").anni
    21.0
    >>> analizza_eta(None) is None
    True
    """
    if not testo:
        return None

    grezzo = testo.strip()
    if not grezzo:
        return None

    approssimata = bool(_APPROSSIMA.search(grezzo))

    if _APPENA_NATO.search(grezzo) and not _numero_esplicito(grezzo):
        return Eta(0.0, True, grezzo)

    # L'unita' va cercata sul testo intero, prima di togliere la zavorra:
    # 'di mesi otto' perde il 'di' ma non il 'mesi'.
    fattore = 1.0
    for schema, quanto in _UNITA_TEMPO:
        if schema.search(grezzo):
            fattore = quanto
            break

    ripulito = _ZAVORRA.sub(" ", grezzo)
    for schema, _ in _UNITA_TEMPO:
        ripulito = schema.sub(" ", ripulito)
    ripulito = _senza_accenti(ripulito).casefold()
    # L'apostrofo fra due lettere unisce, non separa: nei registri
    # dell'Ottocento «vent'uno» e' ventuno, «cinquant'otto» cinquantotto.
    # Trattandolo come tutti gli altri segni, «vent'uno» diventava
    # «vent uno» e l'eta' letta era **uno**: il testimone del matrimonio
    # n. 1 del 1811 risultava un bambino di un anno.
    ripulito = re.sub(r"(?<=[a-z])'(?=[a-z])", "", ripulito)
    ripulito = re.sub(r"[^a-z0-9 ]+", " ", ripulito)
    ripulito = " ".join(ripulito.split())

    if not ripulito:
        # Restava solo la formula: 'maggiore di eta'', 'minore'.
        if _MINORENNE.search(grezzo):
            return None
        if _MAGGIORENNE.search(grezzo):
            return Eta(float(ETA_MAGGIORE), True, grezzo, minima=True)
        return None

    for pezzo in ripulito.split():
        if pezzo.isdigit():
            valore = int(pezzo)
            if 0 <= valore <= 120:
                return Eta(valore * fattore, approssimata, grezzo)

    letto = _numero_da_parole(ripulito)
    if letto is None:
        if _MAGGIORENNE.search(grezzo):
            return Eta(float(ETA_MAGGIORE), True, grezzo, minima=True)
        return None

    numero, esatto = letto
    return Eta(numero * fattore, approssimata or not esatto, grezzo)


def _numero_esplicito(grezzo: str) -> bool:
    """Se accanto a 'neonato' c'e' comunque un numero, comanda il numero."""
    ripulito = _senza_accenti(_ZAVORRA.sub(" ", grezzo)).casefold()
    return any(p in NUMERI or p.isdigit() for p in re.findall(r"[a-z0-9]+", ripulito))


# Quanto puo' sbagliare una parola e restare riconoscibile. 'trentacingue'
# per 'trentacinque' e' un tratto in piu' su una q, 'sessantatrre' una
# doppia di troppo: sono le stesse confusioni di mano che il modulo
# paleografia gia' pesa sui cognomi, e qui giovano perche' il bersaglio e'
# un insieme CHIUSO — i numeri fino a cento — dove un accostamento
# sbagliato non puo' inventare una parola che non esiste.
#
# La soglia e' generosa (0,78: 'sessandue' sta a 0,82 da 'sessantadue',
# 'ventinovene' a 0,80 da 'ventinove') ma cio' che entra da questa porta
# esce sempre marcato **approssimato**, e chi ricostruisce le parentele
# allarga la tolleranza di conseguenza. E' il compromesso giusto: senza,
# una trentina di eta' resterebbe ignota, cioe' persa del tutto; cosi'
# valgono come indizio e non come misura.
SOGLIA_NUMERO = 0.78

# Le parole corte si riconoscono solo per uguaglianza: 'sei', 'tre' e
# 'due' stanno tutte a un passo l'una dall'altra, e su tre lettere la
# somiglianza non distingue piu' niente.
LUNGHEZZA_MINIMA_APPROSSIMATA = 5

# Due parole di lunghezza troppo diversa non sono la stessa parola
# scritta male: 'otto' e 'ottantotto' si somigliano per forza.
DIVARIO_MASSIMO = 3


def _unisci_decine(parole: list[str]) -> list[str]:
    """'cinquanta sei' e' cinquantasei, non cinquanta.

    Lo scrivano stacca la decina dall'unita' piu' spesso di quanto si
    creda, e la lettura la consegna staccata com'e' scritta. Cercata
    parola per parola vince allora la decina, che e' quella che compare
    per prima, e l'eta' perde fino a nove anni: 'cinquanta sei' diventava
    cinquanta, e l'anno di nascita sbagliava di sei — abbastanza da
    mandare fuori tolleranza due menzioni dello stesso uomo.
    """
    unite: list[str] = []
    salta = 0
    for posto, parola in enumerate(parole):
        if salta:
            salta -= 1
            continue
        if parola in _DECINE:
            passo = 2 if parole[posto + 1 : posto + 2] == ["e"] else 1
            seguente = parole[posto + passo] if posto + passo < len(parole) else None
            if seguente in _UNITA and seguente != "un":
                radice = parola[:-1] if seguente in ("uno", "otto") else parola
                composto = radice + seguente
                if composto in NUMERI:
                    unite.append(composto)
                    salta = passo
                    continue
        unite.append(parola)
    return unite


def _numero_da_parole(ripulito: str) -> tuple[int, bool] | None:
    """Cerca il numero. Rende ``(valore, esatto)``, ``None`` se non c'e'."""
    parole = _unisci_decine(ripulito.split())

    for parola in parole:
        if parola in NUMERI:
            return NUMERI[parola], True

    # 'mesi quattordici' o 'anni sette e mesi due': puo' esserci piu' di
    # una parola utile, e vince quella che somiglia di piu'.
    migliore: tuple[float, int] | None = None
    for parola in parole:
        if len(parola) < LUNGHEZZA_MINIMA_APPROSSIMATA:
            continue
        for forma, valore in NUMERI.items():
            if abs(len(forma) - len(parola)) > DIVARIO_MASSIMO:
                continue
            simile = paleografia.somiglianza(parola, forma)
            if simile >= SOGLIA_NUMERO and (migliore is None or simile > migliore[0]):
                migliore = (simile, valore)
    return (migliore[1], False) if migliore else None


def _senza_accenti(testo: str) -> str:
    scomposto = unicodedata.normalize("NFKD", testo)
    return "".join(c for c in scomposto if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Il patronimico nascosto nel nome
# ---------------------------------------------------------------------------

# 'Giuseppe di Carmine Pepe' vuol dire "Giuseppe, figlio di Carmine, dei
# Pepe". E' l'unica volta che la fonte disambigua da sola due omonimi, ed
# e' proprio nei paesi dove il nome si ripete che lo scrivano ne sente il
# bisogno: quando in un anno ci sono tre Domenico Pelliccia, il registro
# li distingue cosi'.
_PATRONIMICO = re.compile(
    r"^(?P<nome>[^\s].*?)\s+(?:di|de|d'|fu)\s+(?P<padre>[A-ZÀ-Ü][\w'’à-ü]+"
    r"(?:\s+[A-ZÀ-Ü][\w'’à-ü]+)?)\s*$",
    re.UNICODE,
)


def separa_patronimico(nome: str | None) -> tuple[str | None, str | None]:
    """Divide ``Giuseppe di Carmine`` in nome e nome del padre.

    >>> separa_patronimico("Giuseppe di Carmine")
    ('Giuseppe', 'Carmine')
    >>> separa_patronimico("Maria Teresa")
    ('Maria Teresa', None)
    >>> separa_patronimico("Di Nardo")
    ('Di Nardo', None)

    La guardia sulla lunghezza serve a non spezzare i cognomi che
    *cominciano* per 'Di': ``Di Nardo`` non e' un patronimico, e' un
    cognome finito per sbaglio nella colonna del nome.
    """
    if not nome:
        return nome, None
    trovato = _PATRONIMICO.match(nome.strip())
    if not trovato:
        return nome, None
    proprio = trovato.group("nome").strip()
    padre = trovato.group("padre").strip()
    if not proprio or len(proprio) < 3:
        return nome, None
    return proprio, padre


# Le parole che i registri mettono davanti a un nome per dire che quella
# persona e' gia' morta. Non fanno parte del nome, e lasciarcele dentro
# costa caro: 'fu Margherita Profio' diventa una donna diversa da
# 'Margherita Profio', e le due non si incontrano mai perche' il
# confronto fra nomi le vede come 'fu Margherita' contro 'Margherita'.
_GIA_MORTO = re.compile(r"^\s*(?:il\s+|la\s+)?fu\s+", re.IGNORECASE)


def separa_stato_vitale(valore: str | None) -> tuple[str | None, bool]:
    """Toglie il 'fu' davanti al nome e dice che quella persona era morta.

    Rende ``(valore ripulito, era_gia_morta)``. Il 'fu' e' un'informazione
    vera e non va persa — finisce in ``stato_vitale``, che e' il campo
    che gia' la porta — ma non e' parte del nome di nessuno.

    >>> separa_stato_vitale("fu Margherita")
    ('Margherita', True)
    >>> separa_stato_vitale("Margherita")
    ('Margherita', False)
    >>> separa_stato_vitale("Fulvio")
    ('Fulvio', False)
    """
    if not valore:
        return valore, False
    ripulito = _GIA_MORTO.sub("", valore).strip()
    if not ripulito or ripulito == valore.strip():
        return valore, False
    return ripulito, True


# ---------------------------------------------------------------------------
# Nome e cognome scambiati
# ---------------------------------------------------------------------------

# Quanto deve essere netta l'evidenza perche' una menzione si consideri
# invertita. 0,9 vuol dire: quella stringa, nel resto del corpus, sta nella
# colonna opposta nove volte su dieci. Sotto questa soglia ci sono le
# stringhe che vivono onestamente in tutte e due — 'Salvatore' e' un
# cognome da 774 menzioni ed e' anche un nome di battesimo — e quelle non
# si toccano mai.
SOGLIA_INVERSIONE = 0.90

# Quante volte una stringa deve essersi vista perche' la sua proporzione
# significhi qualcosa. Su due occorrenze il 100% non e' evidenza.
MINIME_OCCORRENZE = 5


@dataclass
class Bilancia:
    """Quanto una stringa 'sa' di nome e quanto di cognome, nel corpus.

    Si costruisce una volta sull'intero archivio e poi si interroga per
    ogni menzione: la domanda "questa riga e' invertita?" non ha risposta
    guardando la riga, ce l'ha solo confrontandola con come le stesse due
    parole si comportano nelle altre quarantamila.
    """

    come_nome: Counter[str]
    come_cognome: Counter[str]

    @classmethod
    def dal_corpus(cls, menzioni) -> "Bilancia":
        """``menzioni`` e' una sequenza di coppie ``(nome, cognome)``."""
        come_nome: Counter[str] = Counter()
        come_cognome: Counter[str] = Counter()
        for nome, cognome in menzioni:
            if nome:
                come_nome[_chiave(nome)] += 1
            if cognome:
                come_cognome[_chiave(cognome)] += 1
        return cls(come_nome, come_cognome)

    def sa_di_cognome(self, valore: str | None) -> float | None:
        """Fra 0 e 1: quanto quella stringa e' un cognome. ``None`` se rara."""
        if not valore:
            return None
        chiave = _chiave(valore)
        nomi = self.come_nome[chiave]
        cognomi = self.come_cognome[chiave]
        if nomi + cognomi < MINIME_OCCORRENZE:
            return None
        return cognomi / (nomi + cognomi)

    def invertita(self, nome: str | None, cognome: str | None) -> bool:
        """Vero se le due colonne vanno scambiate.

        Servono **tutte e due** le evidenze. Con una sola si scambierebbe
        'Vincenzo Salvatore' — un nome e un cognome perfettamente
        ordinari — solo perche' 'Salvatore' e' anche un cognome frequente.
        """
        sa_nome = self.sa_di_cognome(nome)
        sa_cognome = self.sa_di_cognome(cognome)
        if sa_nome is None or sa_cognome is None:
            return False
        return sa_nome >= SOGLIA_INVERSIONE and sa_cognome <= 1 - SOGLIA_INVERSIONE


def _chiave(valore: str) -> str:
    return paleografia.normalizza(valore)


# Quante menzioni invertite servono in un atto perche' l'inversione si
# consideri una scelta dello scrivano e non un caso isolato. Due bastano:
# un atto di morte del 1887 ne ha cinque su sei, e chi scrive l'intestazione
# 'Pepe Modesto' scrive allo stesso modo tutta la pagina.
INVERSIONI_PER_ATTO = 2


def inversioni_per_atto(
    atto_di: dict[int, int], nomi: dict[int, tuple[str | None, str | None]],
    bilancia: Bilancia,
) -> set[int]:
    """Quali menzioni vanno scambiate, decidendo per atto e non per riga.

    ``atto_di`` mappa id di menzione -> id di atto, ``nomi`` id di menzione
    -> ``(nome, cognome)``.

    L'inversione e' una **abitudine della pagina**: dal 1881 il registro
    di Torrebruna passa alla forma 'Cognome Nome' e la tiene per tutto
    l'atto. Decidere riga per riga lascerebbe indietro proprio le righe
    che l'evidenza da sola non regge — un ``Petti Marianna`` in cui
    'Marianna' non e' abbastanza attestata come nome — e produrrebbe un
    atto meta' dritto e meta' rovescio, che e' il peggiore dei due mondi.

    Quindi: si contano le righe su cui l'evidenza e' netta, e se in un
    atto sono almeno due si scambiano anche le altre, **tranne** quelle
    che l'evidenza dice esplicitamente dritte.
    """
    certe: dict[int, set[int]] = {}
    for menzione, (nome, cognome) in nomi.items():
        if bilancia.invertita(nome, cognome):
            certe.setdefault(atto_di[menzione], set()).add(menzione)

    da_scambiare: set[int] = set()
    for atto, righe in certe.items():
        da_scambiare |= righe
        if len(righe) < INVERSIONI_PER_ATTO:
            continue
        for menzione, (nome, cognome) in nomi.items():
            if atto_di[menzione] != atto or menzione in righe:
                continue
            # Una riga con una colonna sola non ha niente da scambiare.
            # Scambiarla sposterebbe la stessa parola da una casella
            # all'altra e basta: il 'padre: Carmine' di un atto del 1882
            # diventava un cognome 'Carmine' senza nome di battesimo,
            # cioe' una persona che nell'albero non si capisce chi sia.
            # La si tocca solo quando la parola che porta e'
            # riconoscibilmente dell'altra specie.
            if not nome or not cognome:
                sola = bilancia.sa_di_cognome(nome or cognome)
                if sola is None:
                    continue
                fuori_posto = sola >= 0.5 if nome else sola <= 0.5
                if fuori_posto:
                    da_scambiare.add(menzione)
                continue
            # Una riga si trascina solo se non contraddice: se 'nome' e'
            # un nome vero e 'cognome' un cognome vero, quella riga era
            # gia' a posto e l'abitudine della pagina non la riguarda.
            sa_nome = bilancia.sa_di_cognome(nome)
            sa_cognome = bilancia.sa_di_cognome(cognome)
            dritta = (
                sa_nome is not None and sa_nome <= 0.5
                and sa_cognome is not None and sa_cognome >= 0.5
            )
            if not dritta:
                da_scambiare.add(menzione)
    return da_scambiare


# ---------------------------------------------------------------------------
# Le varianti del nome di battesimo
# ---------------------------------------------------------------------------

# I nomi composti sono la norma nei registri ottocenteschi — 'Maria
# Giuseppa', 'Maria Teresa', 'Domenico Antonio' — e vanno trattati parola
# per parola. Confrontare le stringhe intere metterebbe 'Maria Giuseppa'
# e 'Maria Giyseppa' a somiglianza alta ma 'Giyseppa' da sola non
# arriverebbe mai a incontrare 'Giuseppa': l'errore sta in un pezzo, e il
# pezzo e' l'unita' su cui si conta.
_SEPARA_NOME = re.compile(r"[\s]+")

# Particelle che dentro un nome non sono nomi. Non vanno contate come
# forme, altrimenti finiscono nelle frequenze e si prendono correzioni.
PARTICELLE = {"di", "de", "del", "della", "dello", "d", "da", "e", "fu", "detto", "detta"}


def parti_del_nome(nome: str | None) -> list[str]:
    """I pezzi contabili di un nome di battesimo.

    >>> parti_del_nome("Maria Giuseppa")
    ['Maria', 'Giuseppa']
    >>> parti_del_nome("Giovanni detto Nanni")
    ['Giovanni', 'Nanni']
    """
    if not nome:
        return []
    pezzi = []
    for pezzo in _SEPARA_NOME.split(nome.strip()):
        pulito = pezzo.strip(".,;:")
        if not pulito:
            continue
        if paleografia.normalizza(pulito) in PARTICELLE:
            continue
        pezzi.append(pulito)
    return pezzi


# ---------------------------------------------------------------------------
# Il genere dei nomi di battesimo
# ---------------------------------------------------------------------------

# I ruoli che dicono il sesso senza margine di dubbio. Sono 12.750
# menzioni su 40.631: abbastanza perche' ogni nome che ricorre nel paese
# ci passi dentro molte volte.
RUOLI_MASCHILI = frozenset({
    "padre", "sposo", "marito", "nonno", "avo paterno", "avo_paterno",
    "padre dello sposo", "padre della sposa",
})
RUOLI_FEMMINILI = frozenset({
    "madre", "sposa", "moglie", "levatrice", "neonata", "avola",
    "madre dello sposo", "madre della sposa",
})

# Ripiego per i nomi che nel corpus non capitano mai in un ruolo sessato.
# Le eccezioni sono quelle vere dell'italiano: un nome in -a maschile
# esiste, ed e' proprio fra i piu' portati del paese.
MASCHILI_IN_A = frozenset({
    "nicola", "andrea", "luca", "battista", "elia", "mattia", "geremia",
    "zaccaria", "tobia", "sabba", "barnaba", "aniceta",
})

# Quanto deve pendere l'evidenza perche' un nome si dica di un sesso.
# Sotto questa soglia il nome resta ambiguo, e ambiguo vuol dire che nel
# raggruppamento non si unisce a nulla: e' il caso di 'Celeste' o
# 'Amabile', che nei registri portano tutti e due i sessi.
SOGLIA_GENERE = 0.85
MINIME_PER_GENERE = 3


@dataclass
class Genere:
    """Il sesso dei nomi di battesimo, imparato dagli atti invece che dedotto.

    Serve due volte, e la seconda e' la piu' importante.

    La prima: dare un sesso a ogni persona dell'albero, anche quando
    compare in un ruolo che non lo dice — un testimone, un dichiarante.

    La seconda: **impedire che la correzione delle varianti fonda un uomo
    con una donna.** Il modulo ``paleografia`` tratta lo scambio a/o come
    una confusione di mano, il che sui cognomi e' giusto ('Colella' e
    'Colello' sono la stessa famiglia) e sui nomi di battesimo e'
    rovinoso: ``Domenico`` e ``Domenica`` stanno a somiglianza 0,963,
    piu' vicini di quanto ``Giyseppe`` stia a ``Giuseppe`` (0,875). Senza
    questa tavola, la correzione dei nomi collasserebbe padre e madre
    nella stessa persona — e un albero genealogico in cui i genitori sono
    la stessa persona non e' un albero sbagliato, e' carta straccia.
    """

    maschili: Counter[str]
    femminili: Counter[str]

    @classmethod
    def dal_corpus(cls, menzioni) -> "Genere":
        """``menzioni`` e' una sequenza di coppie ``(ruolo, nome)``."""
        maschili: Counter[str] = Counter()
        femminili: Counter[str] = Counter()
        for ruolo, nome in menzioni:
            chiave = (ruolo or "").strip().casefold()
            if chiave in RUOLI_MASCHILI:
                bersaglio = maschili
            elif chiave in RUOLI_FEMMINILI:
                bersaglio = femminili
            else:
                continue
            for parte in parti_del_nome(nome):
                bersaglio[_chiave(parte)] += 1
        return cls(maschili, femminili)

    def della_forma(self, forma: str) -> str | None:
        """``'M'``, ``'F'`` o ``None`` per una singola parola.

        Prima l'evidenza del corpus, poi — solo se il corpus tace — la
        desinenza. In quest'ordine, perche' la desinenza sbaglia su nomi
        veri e frequenti e il corpus no.
        """
        chiave = _chiave(forma)
        maschi, femmine = self.maschili[chiave], self.femminili[chiave]
        totale = maschi + femmine
        if totale >= MINIME_PER_GENERE:
            if maschi / totale >= SOGLIA_GENERE:
                return "M"
            if femmine / totale >= SOGLIA_GENERE:
                return "F"
            return None
        return _genere_dalla_desinenza(chiave)

    @staticmethod
    def probabile_maschile(nome: str | None) -> bool:
        """Il nome, per come e' fatto, e' di un uomo.

        Guarda la sola desinenza del primo nome, senza corpus: serve dove
        il corpus non c'e' ancora o non serve — a decidere, per esempio,
        se il dichiarante di una nascita puo' essere il padre o e' la
        levatrice. E' una domanda a cui la desinenza risponde bene,
        perche' ``MASCHILI_IN_A`` tiene gia' le eccezioni vere.
        """
        parti = parti_del_nome(nome)
        if not parti:
            return False
        return _genere_dalla_desinenza(_chiave(parti[0])) == "M"

    def di(self, nome: str | None) -> str | None:
        """Il sesso di un nome intero, anche composto.

        Su ``Maria Giuseppe`` — che nei registri esiste, come voto — le
        due parti dicono cose diverse: si guarda la **prima**, che e' il
        nome che la persona porta, e le altre valgono solo se la prima
        tace.

        E quando la prima e' ambigua nel corpus, prima di passare la
        parola alle altre si guarda la sua **desinenza**. Il caso che lo
        impone e' 'Nicola': come parola vale 534 volte da uomo e 103 da
        donna — perche' 'Maria Nicola' e' un nome femminile e il
        conteggio non sa in che posizione stia — quindi resta sotto la
        soglia e non dice niente. A quel punto parlava 'Maria', e
        **Nicola Maria Lella, nato nel 1885, risultava una bambina**:
        con lui spariva il primo maschio di Domenicantonio Lella, cioe'
        l'unico indizio che attaccasse suo padre all'albero.

        In prima posizione la desinenza non e' un ripiego debole: e' il
        nome che quella persona porta, e ``MASCHILI_IN_A`` tiene gia' le
        eccezioni vere dell'italiano. In seconda posizione no — li'
        'Maria' e' un voto, non un sesso.
        """
        for indice, parte in enumerate(parti_del_nome(nome)):
            sesso = self.della_forma(parte)
            if sesso:
                return sesso
            if indice == 0:
                dalla_desinenza = _genere_dalla_desinenza(_chiave(parte))
                if dalla_desinenza:
                    return dalla_desinenza
        return None


def _genere_dalla_desinenza(chiave: str) -> str | None:
    if not chiave:
        return None
    ultima = chiave.rsplit(" ", 1)[-1]
    if not ultima:
        return None
    if ultima in MASCHILI_IN_A:
        return "M"
    if ultima.endswith("a"):
        return "F"
    if ultima.endswith(("o", "i")):
        return "M"
    return None


def somiglianza_nome(uno: str | None, altro: str | None) -> float:
    """Quanto due nomi di battesimo sono lo stesso nome.

    **Non** e' la distanza fra le due stringhe, e la differenza decide se
    un albero si tiene insieme o no. Nei registri la stessa donna e'
    ``Angela`` in un atto e ``Angela Maria`` nel successivo: come stringhe
    stanno a 0,545 — piu' lontane di due nomi che non c'entrano niente —
    e un confronto letterale le tratta come due persone.

    Qui il nome si confronta **pezzo per pezzo**, con questa regola: tutte
    le parti del nome piu' corto devono ritrovarsi in quello piu' lungo.

    >>> somiglianza_nome("Angela", "Angela Maria")
    1.0
    >>> somiglianza_nome("Angela Maria", "Angela")
    1.0

    Ed e' proprio la stessa regola a tenere separati i nomi composti
    diversi, che il solo primo elemento confonderebbe: ``Maria Teresa`` e
    ``Maria Giuseppa`` cominciano uguali, ma 'Teresa' non si ritrova
    dall'altra parte, e quindi non sono la stessa persona.

    >>> somiglianza_nome("Maria Teresa", "Maria Giuseppa") < 0.7
    True
    """
    parti_uno = [paleografia.normalizza(p) for p in parti_del_nome(uno)]
    parti_altro = [paleografia.normalizza(p) for p in parti_del_nome(altro)]
    if not parti_uno or not parti_altro:
        return 0.0
    if parti_uno == parti_altro:
        return 1.0

    corto, lungo = (
        (parti_uno, parti_altro)
        if len(parti_uno) <= len(parti_altro)
        else (parti_altro, parti_uno)
    )
    # Il punteggio e' quello della parte che si ritrova PEGGIO: basta un
    # elemento estraneo — la 'Teresa' di 'Maria Teresa' — perche' i due
    # nomi non siano lo stesso nome, per quanto il resto combaci.
    res = min(
        max(paleografia.somiglianza(parte, altra) for altra in lungo)
        for parte in corto
    )

    # Fallback per nomi composti concatenati (es. 'Domenico Antonio' vs 'Domenicantonio')
    concatenato_uno = "".join(parti_uno)
    concatenato_altro = "".join(parti_altro)
    somiglianza_concatenata = paleografia.somiglianza(concatenato_uno, concatenato_altro)

    return max(res, somiglianza_concatenata)


# I nomi di battesimo si correggono con la mano piu' leggera dei cognomi,
# e c'e' una ragione precisa. Un cognome visto una volta sola puo' essere
# un forestiero vero — una sposa del paese vicino, un soldato — e
# correggerlo cancellerebbe un dato storico. Un nome di battesimo no: il
# calendario dei santi e' un insieme quasi chiuso, e una forma vista
# cinquanta volte accanto a una vista cinquecento e' quasi sempre la
# stessa, scritta da una mano diversa.
#
# La regola in piu' e' quella a 0,80: serve per 'Angiola' accanto ad
# 'Angela' (somiglianza 0,814, cinquantuno contro cinquecentosettantatre),
# che sono lo stesso nome e che le regole dei cognomi lascerebbero
# divisi. Il vincolo di genere resta sopra a tutto: non c'e' dominanza
# che possa far diventare 'Domenica' un 'Domenico'.
REGOLE_NOMI = (
    (0.80, 8.0),
    (0.85, 4.0),
    (0.92, 3.0),
)


def correzioni_dei_nomi(
    nomi: list[str | None], genere: Genere
) -> tuple[dict[str, str], list[normalizza.Proposta]]:
    """Le varianti del nome di battesimo, con lo stesso metro dei cognomi.

    Restituisce ``(correzioni, proposte)`` come
    :func:`normalizza.raggruppa_varianti`, che e' la stessa funzione che
    lavora sui cognomi: il problema e' identico — una mano dell'Ottocento,
    un insieme ristretto di forme che ricorrono migliaia di volte, e
    qualche lettura storta da ricondurre.

    Con una differenza che non si puo' saltare. I cognomi si confrontano
    tutti contro tutti; i nomi **no**, perche' fra due nomi la vocale
    finale non e' una grafia, e' il sesso di chi la porta. Quindi il
    confronto si fa dentro tre insiemi separati — maschili, femminili,
    ambigui — e una forma di un insieme non puo' correggere quella di un
    altro. ``Giyseppe`` trova ``Giuseppe`` perche' sono tutti e due
    maschili; ``Domenica`` non trova ``Domenico`` perche' non si
    incontrano mai.

    Su un punto i nomi sono piu' facili dei cognomi, e vale la pena
    dirlo: il calendario dei santi e' un insieme quasi chiuso. Un cognome
    visto una volta sola puo' benissimo essere un forestiero vero, e per
    questo la fase 4 e' prudente; un ``Giyseppe`` visto una volta sola,
    accanto a 268 ``Giuseppe``, non e' un santo raro.
    """
    per_genere: dict[str | None, Counter[str]] = {"M": Counter(), "F": Counter(), None: Counter()}
    for nome in nomi:
        for parte in parti_del_nome(nome):
            per_genere[genere.della_forma(parte)][parte] += 1

    # Le forme di sesso **ignoto** entrano in tutti e due i confronti.
    #
    # Senza, la correzione mancherebbe proprio i casi per cui esiste. Una
    # lettura storta non ha un sesso: 'Giyseppe' non compare mai in un
    # ruolo che lo dica, e la desinenza in -e non decide. Chiusa nel suo
    # gruppo, non incontrerebbe mai 'Giuseppe', che sta fra i maschili —
    # e resterebbe un nome a se' per sempre.
    #
    # Farle entrare in tutti e due non riapre il pericolo che il gruppo
    # per genere serviva a chiudere: li' il rischio era unire due forme
    # **di sesso noto e diverso**, e questo resta impossibile, perche' una
    # forma di sesso noto compare in un confronto solo.
    candidate: dict[str, list[tuple[int, str]]] = {}
    proposte: list[normalizza.Proposta] = []
    viste: set[tuple[str, str]] = set()

    for sesso in ("M", "F"):
        frequenze = per_genere[sesso] + per_genere[None]
        if len(frequenze) < 2:
            continue
        parziali, sospese = normalizza.raggruppa_varianti(frequenze, REGOLE_NOMI)
        for letto, forma in parziali.items():
            candidate.setdefault(letto, []).append((frequenze[forma], forma))
        for proposta in sospese:
            chiave = (proposta.letto, proposta.proposto)
            if chiave not in viste:
                viste.add(chiave)
                proposte.append(proposta)

    # Una forma ambigua puo' aver trovato un bersaglio in tutti e due i
    # confronti: vince quello piu' attestato, che e' lo stesso criterio
    # con cui si sceglie fra due varianti dentro un gruppo solo.
    correzioni = {
        letto: max(bersagli)[1] for letto, bersagli in candidate.items()
    }
    proposte.sort(key=lambda p: -p.occorrenze_lette)
    return correzioni, proposte


def applica_correzioni(
    nome: str | None, correzioni: dict[str, str],
    ruolo: str | None = None, genere: "Genere | None" = None,
) -> str | None:
    """Riscrive un nome sostituendo le sue parti corrette, e nient'altro.

    Le particelle e la punteggiatura restano dove sono: qui si cambia una
    lettura, non si riscrive la formula dell'atto.

    >>> applica_correzioni("Maria Giyseppa", {"Giyseppa": "Giuseppa"})
    'Maria Giuseppa'

    Con ``ruolo`` e ``genere``, **una correzione non puo' cambiare il
    sesso di chi la porta.** I nomi che finiscono in *-e* non hanno un
    sesso nella desinenza, quindi entrano in tutti e due i confronti di
    :func:`correzioni_dei_nomi` — ed e' li' che si apre il varco:
    ``Innocente``, scritto una volta sola e sempre come padre, veniva
    ricondotto a ``Innocenta``, che in paese hanno nove donne. Il padre
    di Maria Lella diventava cosi' una donna di nome Innocenta, e chi
    guarda l'albero se ne accorge subito — perche' e' assurdo.

    Il ruolo, quando c'e', e' piu' forte di qualunque frequenza: un padre
    e' un uomo perche' lo dice l'atto, non perche' lo dica una statistica.

    >>> genere = Genere(maschili=Counter(), femminili=Counter({'innocenta': 9}))
    >>> applica_correzioni("Innocente", {"Innocente": "Innocenta"},
    ...                    ruolo="padre", genere=genere)
    'Innocente'
    """
    if not nome or not correzioni:
        return nome
    atteso = None
    if ruolo is not None:
        chiave = (ruolo or "").strip().casefold()
        if chiave in RUOLI_MASCHILI:
            atteso = "M"
        elif chiave in RUOLI_FEMMINILI:
            atteso = "F"

    def corretto(pezzo: str) -> str:
        nuovo = correzioni.get(pezzo)
        if nuovo is None:
            return pezzo
        if atteso is not None and genere is not None:
            if genere.della_forma(nuovo) not in (None, atteso):
                return pezzo
        return nuovo

    pezzi = _SEPARA_NOME.split(nome.strip())
    return " ".join(corretto(pezzo) for pezzo in pezzi if pezzo)
