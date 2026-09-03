"""Dalle menzioni alle persone: chi, fra quarantamila righe, e' lo stesso.

La tabella ``persone`` della fase 4 non contiene persone: contiene
**menzioni**. Un uomo che nasce nel 1820, si sposa nel 1845, ha sei figli
e muore nel 1889 compare nei registri almeno dieci volte, ogni volta come
una riga nuova, e nessuna delle dieci sa delle altre. Finche' restano
dieci righe non c'e' nessun albero genealogico: c'e' un elenco.

Questo modulo fa due cose, in quest'ordine.

**Primo, legge la famiglia dentro ogni atto.** Un atto di nascita dice
"Maria, figlia di Giuseppe e di Calideta": non e' un'inferenza, e'
scritto. Da ogni atto esce un pezzo di albero *certo*, perche' viene
dalla fonte. E' qui che sta il valore, e per questo la lettura degli
atti e' prudente fino alla pignoleria — in particolare sui matrimoni,
dove i genitori nominati sono due coppie e il ruolo non dice quale sia
di chi.

**Secondo, riconosce la stessa persona in atti diversi.** Questa e'
un'inferenza, e come tale puo' sbagliare. La regola che la governa e' una
sola: *il nome non basta mai*. In un paese dove i Pelliccia sono 3.556
menzioni e i figli portano il nome del nonno, "Domenico Pelliccia" sono
molti uomini diversi, e unirli sarebbe peggio che lasciarli separati —
un albero con due rami cuciti a caso e' piu' dannoso di due alberi
tronchi, perche' non si vede che e' rotto. Quindi si unisce solo quando
oltre al nome c'e' dell'altro: i genitori, il coniuge, l'eta' che torna.

**Nessuna menzione va persa.** Una riga che non si riconosce in nessuna
persona gia' vista diventa una persona per conto suo: un testimone che
compare una volta sola nel 1834 e' comunque qualcuno che e' esistito, ha
un nome, un mestiere e un'eta', e sta nell'archivio come tutti gli altri.
Il conto si chiude a fine costruzione, e se non torna la costruzione si
ferma.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from history_maker import nomi, paleografia

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# I ruoli, raccolti per quello che dicono
# ---------------------------------------------------------------------------

# Chi e' il soggetto dell'atto: la persona di cui l'atto parla, e a cui
# vanno riferiti i genitori che l'atto nomina.
SOGGETTI = {
    "nascita": ("neonato", "neonata"),
    "morte": ("defunto", "defunta"),
    "matrimonio": ("sposo", "sposa"),
    "pubblicazione": ("sposo", "sposa"),
}

# I ruoli che implicano essere presenti e vivi il giorno dell'atto. Serve
# a una guardia precisa: una persona non puo' dichiarare una nascita
# dopo essere morta, e questo esclude accostamenti impossibili. Chi e'
# soltanto *nominato* — un padre defunto citato nell'atto di matrimonio
# del figlio — non e' in questo insieme.
RUOLI_PRESENTI = frozenset({
    "dichiarante", "testimone", "ufficiale", "levatrice", "sposo", "sposa",
    "neonato", "neonata",
})

RUOLI_CONIUGE = frozenset({"coniuge", "marito", "moglie", "sposo", "sposa"})

GENITORI = frozenset({"padre", "madre"})

# I ruoli che creano un legame anche quando la persona non ha genitori
# scritti: chi e' padre ha dei figli, chi e' sposo ha una moglie.
RUOLI_CHE_LEGANO = frozenset({
    "padre", "madre", "sposo", "sposa", "coniuge", "marito", "moglie",
    "padre dello sposo", "padre della sposa",
    "madre dello sposo", "madre della sposa",
})


# ---------------------------------------------------------------------------
# Una menzione
# ---------------------------------------------------------------------------

@dataclass
class Menzione:
    """Una riga di ``persone``, letta con tutto il contesto del suo atto.

    I campi ``*_letto`` non si toccano mai: sono quello che c'e' scritto
    sulla pagina. ``nome`` e ``cognome`` sono le forme corrette, che sono
    quelle su cui si riconosce.
    """

    id: int
    atto: int
    tipo_atto: str
    anno: int
    data: str | None
    ruolo: str
    nome: str | None
    cognome: str | None
    nome_letto: str | None
    cognome_letto: str | None
    cognome_origine: str | None
    incerto: bool
    eta_letta: str | None
    professione: str | None
    residenza: str | None
    via: str | None
    stato_vitale: str | None
    note: str | None
    immagine: str | None

    sesso: str | None = None
    patronimico: str | None = None
    eta: nomi.Eta | None = None
    invertita: bool = False

    # Le altre letture che la trascrizione stessa propone. Il modello che
    # legge le pagine, quando esita, scrive 'Chiehi [o Chichi]' o
    # 'Sepualdo [Epaldo/Gesualdo]': sono ipotesi gratuite di chi ha visto
    # l'immagine, e buttarle per tenere solo la prima e' uno spreco.
    # Chi confronta due menzioni puo' confrontare anche queste.
    alternative_nome: tuple[str, ...] = ()
    alternative_cognome: tuple[str, ...] = ()

    # Riempiti dalla lettura dell'atto: gli id di menzione dei familiari
    # nominati nello stesso atto. Sono riferimenti a righe vere, non nomi
    # ricopiati: e' cio' che rende la parentela verificabile.
    padre: int | None = None
    madre: int | None = None
    coniuge: int | None = None
    # Vero quando la paternita' non e' scritta in una riga sua ma
    # dedotta: il dichiarante di un atto di nascita, la nota che dice
    # 'padre del defunto'. L'albero la mostra come tale.
    padre_dedotto: bool = False
    coniuge_dedotto: bool = False

    # Fra quali anni questa persona puo' essere nata, per come lo dice
    # l'atto — non la sua riga, l'atto. Vedi finestre_dai_figli.
    finestra: tuple[int, int] | None = None

    def restringi(self, basso: int, alto: int) -> None:
        """Aggiunge un vincolo alla finestra di nascita, intersecando."""
        if self.finestra is None:
            self.finestra = (basso, alto)
            return
        vecchio_basso, vecchio_alto = self.finestra
        self.finestra = (max(vecchio_basso, basso), min(vecchio_alto, alto))

    @property
    def anno_nascita(self) -> int | None:
        """L'anno di nascita ricavato dall'eta' dichiarata nell'atto."""
        if self.eta is None:
            return None
        return self.anno - self.eta.anno_nascita

    @property
    def nascita_certa(self) -> int | None:
        """L'anno di nascita quando l'atto **e'** l'atto di nascita."""
        if self.tipo_atto == "nascita" and self.ruolo in ("neonato", "neonata"):
            return self.anno
        return None

    @property
    def presente(self) -> bool:
        return self.ruolo in RUOLI_PRESENTI

    @property
    def chiave_cognome(self) -> str:
        return paleografia.forma_canonica(self.cognome or "")

    @property
    def chiave_nome(self) -> str:
        return paleografia.forma_canonica(self.nome or "")

    @property
    def parti_nome(self) -> tuple[str, ...]:
        """Le parole del nome, ognuna in forma canonica, piu' il nome intero.

        Sono le chiavi con cui si cercano i candidati. Indicizzare sul
        solo nome intero perderebbe la meta' dei casi: chi e' 'Angela
        Maria' in un atto e 'Angela' in un altro finirebbe in due
        scaffali diversi, e i due non si incontrerebbero mai — nemmeno
        avendo lo stesso marito e gli stessi figli.

        Ma indicizzare **solo** parola per parola ne perde altrettanti,
        ed e' il difetto che questo archivio pagava piu' caro. Qui i nomi
        doppi si scrivono nei due modi, nello stesso registro:
        'Domenicantonio' ricorre 439 volte, 'Domenicangelo' 377,
        'Nicolangelo' 229. Scritto attaccato e' **una** parola, e non
        incontra mai le due di 'Domenico Antonio': i due non diventano
        nemmeno candidati, quindi nessun punteggio ha modo di dire che
        sono la stessa persona.

        Il rimedio e' la forma canonica del nome intero, che gli spazi li
        toglie: 'domenicoantonio' e 'domenicantonio' stanno a 0,93 e il
        vicinato delle chiavi le mette insieme. Cosi' Domenicantonio
        Lella, sposo nel 1884 come 'Domenico Antonio' e padre di sette
        figli come 'Domenicantonio', torna un uomo solo — con i suoi
        genitori, e quindi attaccato al resto dell'albero.
        """
        parti = [
            paleografia.forma_canonica(p) for p in nomi.parti_del_nome(self.nome)
        ]
        intero = self.chiave_nome
        if intero and intero not in parti:
            parti.append(intero)
        return tuple(parti)


# ---------------------------------------------------------------------------
# La famiglia dentro un atto
# ---------------------------------------------------------------------------

def famiglia_dell_atto(menzioni: list[Menzione]) -> None:
    """Collega, dentro un atto, i figli ai genitori e gli sposi fra loro.

    ``menzioni`` sono le righe di **un solo atto**, nell'ordine in cui il
    modello le ha lette, che e' l'ordine della pagina. Modifica sul posto.

    Il caso difficile e' il matrimonio, ed e' l'unico che meriti
    spiegazione. L'atto nomina quattro genitori con due soli ruoli,
    ``padre`` e ``madre``, ripetuti: il ruolo non dice se quel padre sia
    dello sposo o della sposa. La fase 4 di fronte a questo lascia il
    campo vuoto, che e' la scelta giusta per un CSV ma qui costerebbe
    metà dell'albero — 367 atti su 678 hanno tutte e due le coppie.

    Si decide con due prove, in quest'ordine:

    1. **il cognome del padre.** Il padre dello sposo porta il cognome
       dello sposo; quello della sposa il cognome della sposa. Quando il
       confronto indica *uno solo* dei due sposi, ha ragione lui, sempre,
       qualunque sia la posizione sulla pagina.
    2. **la posizione**, quando il cognome non decide — perche' i due
       sposi sono cugini e hanno lo stesso cognome, o perche' il padre
       non ce l'ha. Il formulario usa due schemi: quello alternato
       (sposo, i suoi genitori, sposa, i suoi) che vale 367 atti, e
       quello raggruppato (sposo, sposa, poi i quattro genitori) che ne
       vale 31. Il secondo, letto come il primo, darebbe alla sposa tutti
       e quattro i genitori: si riconosce dal fatto che i due sposi si
       presentano prima di qualsiasi genitore, e allora l'elenco dei
       genitori si divide a meta'.

    La madre non ha prova di cognome — porta quello da nubile — e segue
    percio' l'attribuzione del suo segmento.
    """
    tipo = menzioni[0].tipo_atto if menzioni else ""

    if tipo in ("matrimonio", "pubblicazione"):
        _famiglia_di_matrimonio(menzioni)
    else:
        _famiglia_a_soggetto_unico(menzioni, tipo)

    # Le note per ultime: aggiungono cio' che i ruoli non dicono, e non
    # sovrascrivono mai un legame gia' letto da un ruolo suo.
    parentele_dalle_note(menzioni)


# Le parentele che i registri scrivono a parole invece che nel ruolo.
#
# Sono 4.722 note su 8.434, e buttarle vuol dire buttare parentele che la
# fonte **dichiara**: "padre del dichiarante", "moglie del defunto",
# "padre della madre" — quest'ultima e' un nonno materno, cioe' una
# generazione intera che nessun ruolo del formulario esprime.
#
# Il lettore e' deliberatamente stretto. Riconosce solo le formule che
# nominano un ruolo **presente nello stesso atto**, e solo quando quel
# ruolo e' occupato da una persona sola: "moglie del dichiarante", in un
# atto con due dichiaranti, non dice di quale, e li' si lascia perdere.
_NOTA_PARENTELA = re.compile(
    r"^\s*(?:indicat[ao]\s+come\s+)?(?:su[ao]\s+)?"
    r"(?P<rel>padre|madre|figlio|figlia|moglie|marito|vedova|vedovo)"
    r"(?:\s+legittim[ao])?"
    r"\s+(?:del|della|dello|dei|di)\s+"
    r"(?P<chi>dichiarante|defunt[oa]|spos[oa]|madre|padre|neonat[oa]"
    r"|primo\s+testimone|secondo\s+testimone|testimone)",
    re.IGNORECASE,
)

_RUOLI_DEL_REFERENTE = {
    "dichiarante": ("dichiarante",),
    "defunto": ("defunto", "defunta"),
    "defunta": ("defunto", "defunta"),
    "sposo": ("sposo",),
    "sposa": ("sposa",),
    "madre": ("madre",),
    "padre": ("padre",),
    "neonato": ("neonato", "neonata"),
    "neonata": ("neonato", "neonata"),
    "testimone": ("testimone",),
}


def finestre_dai_figli(menzioni: list[Menzione]) -> None:
    """L'eta' che un genitore non dichiara, ma che l'atto dice lo stesso.

    Sessantun genitori su cento non hanno un'eta' scritta nella loro
    riga: 6.847 menzioni su 11.151. Fino a qui quelle righe non
    portavano **nessun** vincolo di tempo, e una riga senza vincoli di
    tempo si attacca a chiunque porti lo stesso nome, in qualunque
    decennio. E' cosi' che il padre di Filippo Lella — un uomo morto a
    settant'anni nel 1828, quindi un padre nato intorno al 1735 — si era
    unito a un taglialegna di quarantacinque anni del 1839, sulla sola
    forza del nome e del mestiere.

    Ma quell'eta' l'atto la dichiara: la dichiara nella riga del figlio.
    Un uomo di settant'anni ha un padre nato fra il 1683 e il 1745, e non
    e' una probabilita' — e' la biologia, con i due margini larghi che il
    modulo usa dappertutto. Qui il vincolo si legge e si scrive sulla
    riga del genitore, dove serve.

    Il margine in piu' e' per gli anni di nascita **stimati**: quelli
    ricavati da un'eta' dichiarata, che i registri arrotondano. Su un
    anno preso dall'atto di nascita non serve.
    """
    per_id = {m.id: m for m in menzioni}
    for figlio in menzioni:
        certa = figlio.nascita_certa
        anno = certa if certa is not None else figlio.anno_nascita
        if anno is None:
            continue
        margine = 0 if certa is not None else MARGINE_FINESTRA
        for riferimento, sesso in ((figlio.padre, "M"), (figlio.madre, "F")):
            if riferimento is None:
                continue
            genitore = per_id.get(riferimento)
            if genitore is None:
                continue
            massima = ETA_MASSIMA_GENITORE[sesso]
            genitore.restringi(
                anno - massima - margine, anno - ETA_MINIMA_GENITORE + margine
            )

    # E chi ha un atto di nascita e' nato quell'anno, senza margini.
    for menzione in menzioni:
        if menzione.nascita_certa is not None:
            menzione.restringi(menzione.nascita_certa, menzione.nascita_certa)


def _referente(menzioni: list[Menzione], chi: str, io: Menzione) -> Menzione | None:
    """La persona dell'atto a cui una nota si riferisce."""
    chiave = " ".join(chi.lower().split())
    if chiave.endswith("testimone") and chiave != "testimone":
        testimoni = [m for m in menzioni if m.ruolo == "testimone"]
        indice = 0 if chiave.startswith("primo") else 1
        return testimoni[indice] if len(testimoni) > indice else None

    ruoli = _RUOLI_DEL_REFERENTE.get(chiave)
    if not ruoli:
        return None
    candidati = [m for m in menzioni if m.ruolo in ruoli and m is not io]
    return candidati[0] if len(candidati) == 1 else None


def parentele_dalle_note(menzioni: list[Menzione]) -> None:
    """Legge le parentele che l'atto dichiara a parole. Modifica sul posto.

    Le note valgono **piu'** dell'attribuzione per posizione: "padre
    dello sposo" e' la fonte che parla, e dove c'e' non serve indovinare.
    Non toccano invece cio' che un ruolo proprio ha gia' stabilito.
    """
    for menzione in menzioni:
        if not menzione.note:
            continue
        trovato = _NOTA_PARENTELA.match(menzione.note)
        if not trovato:
            continue
        altro = _referente(menzioni, trovato.group("chi"), menzione)
        if altro is None:
            continue

        relazione = trovato.group("rel").lower()
        if relazione == "padre":
            altro.padre = altro.padre or menzione.id
            menzione.padre_dedotto = menzione.padre_dedotto or altro.padre == menzione.id
        elif relazione == "madre":
            altro.madre = altro.madre or menzione.id
        elif relazione in ("figlio", "figlia"):
            # "figlio del defunto": il genitore e' l'altro, e quale dei due
            # lo dice il sesso di chi e' nominato, non la formula.
            if altro.sesso == "F":
                menzione.madre = menzione.madre or altro.id
            elif altro.sesso == "M":
                menzione.padre = menzione.padre or altro.id
        elif relazione in ("moglie", "marito", "vedova", "vedovo"):
            if menzione.coniuge is None and altro.coniuge is None:
                menzione.coniuge, altro.coniuge = altro.id, menzione.id
                menzione.coniuge_dedotto = altro.coniuge_dedotto = True


def _famiglia_a_soggetto_unico(menzioni: list[Menzione], tipo: str) -> None:
    """Nascite e morti: un solo soggetto, quindi nessuna ambiguita'.

    Il vincolo che rende sicura l'attribuzione e' che il padre nominato
    sia **uno solo**. Un atto con due righe ``padre`` non e' un atto di
    nascita normale — capita quando la pagina ne contiene due e la
    divisione ha sbagliato — e li' si preferisce non attribuire niente.
    """
    soggetti = [m for m in menzioni if m.ruolo in SOGGETTI.get(tipo, ())]
    padri = [m for m in menzioni if m.ruolo == "padre"]
    madri = [m for m in menzioni if m.ruolo == "madre"]

    padre = padri[0] if len(padri) == 1 else None
    madre = madri[0] if len(madri) == 1 else None

    if padre is None and tipo == "nascita":
        padre = _padre_dal_dichiarante(menzioni, soggetti, madre)

    for soggetto in soggetti:
        if padre is not None:
            soggetto.padre = padre.id
        if madre is not None:
            soggetto.madre = madre.id

    # I genitori nominati in uno stesso atto sono marito e moglie: e'
    # cio' che il formulario dichiara ("e di sua moglie legittima"), ed e'
    # la fonte piu' abbondante di unioni che abbiamo — 2.962 atti di
    # nascita contro 678 di matrimonio. Senza questo, tutte le coppie
    # sposate prima del primo registro superstite resterebbero invisibili.
    if padre is not None and madre is not None:
        padre.coniuge, madre.coniuge = madre.id, padre.id

    # Nell'atto di morte il coniuge del defunto, quando c'e', e' scritto
    # come tale.
    if tipo == "morte" and len(soggetti) == 1:
        for menzione in menzioni:
            if menzione.ruolo in RUOLI_CONIUGE and menzione is not soggetti[0]:
                soggetti[0].coniuge = menzione.id
                menzione.coniuge = soggetti[0].id
                break


def _padre_dal_dichiarante(
    menzioni: list[Menzione], soggetti: list[Menzione], madre: Menzione | None
) -> Menzione | None:
    """In un atto di nascita senza riga 'padre', il padre e' il dichiarante.

    Non e' un'ipotesi ardita, e' il formulario: l'atto di nascita comincia
    con "e' comparso Tal dei Tali, il quale ha dichiarato che e' nato un
    bambino da lui e da sua moglie legittima". Chi denuncia il figlio e' il
    padre, e quando la lettura di quella pagina non produce una riga
    'padre' separata e' perche' la formula non la teneva separata.

    Vale la pena di farlo perche' e' tanto: **1.168 atti di nascita su
    2.960 — quattro su dieci — non hanno una riga 'padre'.** Senza questa
    deduzione, quattro bambini su dieci nascono di madre ignota di padre,
    e l'albero si spezza a ogni generazione.

    Ed e' misurabile che sia giusta: negli atti dove il padre c'e'
    **anche** come riga a se', il dichiarante e' la stessa persona in
    1.567 casi su 1.746, cioe' quasi nove volte su dieci. La decima e' la
    levatrice, o un parente quando il padre e' assente o morto — ed e'
    per escludere quelle che qui si pretendono tre cose insieme: che il
    dichiarante porti il cognome del neonato, che non sia una donna, e
    che sia **uno solo** a poterlo essere.
    """
    if madre is None or not soggetti:
        return None

    cognomi = {s.chiave_cognome for s in soggetti if s.cognome}
    if not cognomi:
        return None

    candidati = [
        m for m in menzioni
        if m.ruolo == "dichiarante"
        and m.cognome
        and m.chiave_cognome in cognomi
        and m.sesso != "F"
        and m is not madre
    ]
    if len(candidati) != 1:
        return None
    candidati[0].padre_dedotto = True
    return candidati[0]


def _famiglia_di_matrimonio(menzioni: list[Menzione]) -> None:
    sposi = [m for m in menzioni if m.ruolo in ("sposo", "sposa")]
    genitori = [m for m in menzioni if m.ruolo in ("padre", "madre")]

    sposo = next((m for m in sposi if m.ruolo == "sposo"), None)
    sposa = next((m for m in sposi if m.ruolo == "sposa"), None)

    if sposo is not None and sposa is not None:
        sposo.coniuge, sposa.coniuge = sposa.id, sposo.id

    if not genitori or (sposo is None and sposa is None):
        return

    attribuzione = _attribuisci_per_posizione(menzioni, sposo, sposa)

    # Il cognome del padre ha l'ultima parola dove parla chiaro.
    for genitore in genitori:
        if genitore.ruolo != "padre":
            continue
        scelto = _sposo_dal_cognome(genitore, sposo, sposa)
        if scelto is not None:
            attribuzione[genitore.id] = scelto

    # La madre segue il padre del suo stesso segmento: se il cognome ha
    # spostato il padre, la madre che gli sta accanto si sposta con lui.
    _allinea_madri(menzioni, attribuzione)

    for genitore in genitori:
        sposo_scelto = attribuzione.get(genitore.id)
        if sposo_scelto is None:
            continue
        if genitore.ruolo == "padre":
            sposo_scelto.padre = genitore.id
        else:
            sposo_scelto.madre = genitore.id

    _coniugi_fra_i_genitori(menzioni, sposo, sposa)


def _coniugi_fra_i_genitori(
    menzioni: list[Menzione], *sposi: Menzione | None
) -> None:
    """I due genitori di uno sposo sono marito e moglie fra loro.

    L'atto di matrimonio dice "figlio di Filippo Lella e di Margherita
    Rossi": e' la stessa dichiarazione che l'atto di nascita fa dei
    genitori del neonato, e li' viene registrata da sempre. Qui mancava,
    e la mancanza costava piu' di quanto sembri.

    Un padre nominato in un atto di matrimonio non ha genitori scritti,
    non ha eta', non ha mestiere: il coniuge e' **l'unico** indizio
    strutturale che quella riga possa portare. Senza, ogni comparsa dello
    stesso uomo come padre in un matrimonio diverso apriva una persona
    nuova che non aveva piu' modo di ricongiungersi alle altre — e con
    lei si staccava il ramo dei suoi figli. Sono 678 atti, cioe' fino a
    1.356 coppie: la seconda fonte di unioni dopo gli atti di nascita.
    """
    per_id = {m.id: m for m in menzioni}
    for sposo in sposi:
        if sposo is None or sposo.padre is None or sposo.madre is None:
            continue
        padre, madre = per_id.get(sposo.padre), per_id.get(sposo.madre)
        if padre is None or madre is None:
            continue
        # Non si sovrascrive un coniuge gia' scritto: quello viene da un
        # ruolo proprio o da una nota, e vale piu' di questa deduzione.
        if padre.coniuge is None and madre.coniuge is None:
            padre.coniuge, madre.coniuge = madre.id, padre.id
            padre.coniuge_dedotto = madre.coniuge_dedotto = True


def _attribuisci_per_posizione(
    menzioni: list[Menzione], sposo: Menzione | None, sposa: Menzione | None
) -> dict[int, Menzione | None]:
    """Assegna ogni genitore allo sposo che lo precede sulla pagina."""
    attribuzione: dict[int, Menzione | None] = {}

    # Lo schema raggruppato: i due sposi compaiono tutti e due prima di
    # qualsiasi genitore. Li' la posizione da sola direbbe che sono tutti
    # della sposa, e va invece divisa la lista a meta'.
    ordine = [m.ruolo for m in menzioni if m.ruolo in ("sposo", "sposa", "padre", "madre")]
    genitori = [m for m in menzioni if m.ruolo in ("padre", "madre")]
    primo_genitore = next((i for i, r in enumerate(ordine) if r in ("padre", "madre")), None)
    raggruppato = (
        primo_genitore is not None
        and ordine[:primo_genitore].count("sposo") >= 1
        and ordine[:primo_genitore].count("sposa") >= 1
    )

    if raggruppato:
        meta = len(genitori) // 2
        for indice, genitore in enumerate(genitori):
            attribuzione[genitore.id] = sposo if indice < meta else sposa
        return attribuzione

    corrente: Menzione | None = None
    for menzione in menzioni:
        if menzione.ruolo == "sposo":
            corrente = menzione
        elif menzione.ruolo == "sposa":
            corrente = menzione
        elif menzione.ruolo in ("padre", "madre"):
            # Un genitore prima di qualsiasi sposo appartiene al primo
            # che verra': e' lo schema 'padre, madre, sposo' dei registri
            # piu' tardi.
            attribuzione[menzione.id] = corrente if corrente is not None else (sposo or sposa)
    return attribuzione


# Quanto devono somigliarsi due cognomi perche' il confronto conti. E' la
# stessa soglia con cui la fase 4 raggruppa le varianti: sotto, sono due
# famiglie diverse.
SOGLIA_COGNOME = 0.80


def _sposo_dal_cognome(
    padre: Menzione, sposo: Menzione | None, sposa: Menzione | None
) -> Menzione | None:
    """Quale sposo ha il cognome di questo padre, se **uno solo** ce l'ha."""
    if not padre.cognome:
        return None
    somiglia = []
    for candidato in (sposo, sposa):
        if candidato is None or not candidato.cognome:
            somiglia.append(0.0)
            continue
        if padre.chiave_cognome == candidato.chiave_cognome:
            somiglia.append(1.0)
        else:
            somiglia.append(paleografia.somiglianza(padre.cognome, candidato.cognome))

    alto = [i for i, s in enumerate(somiglia) if s >= SOGLIA_COGNOME]
    if len(alto) != 1:
        # Nessuno combacia, oppure combaciano tutti e due — i due sposi
        # hanno lo stesso cognome, cosa comune fra cugini in un paese
        # piccolo. In nessuno dei due casi il cognome decide.
        return None
    return (sposo, sposa)[alto[0]]


def _allinea_madri(menzioni: list[Menzione], attribuzione: dict[int, Menzione | None]) -> None:
    """La madre va allo stesso sposo del padre che la precede."""
    ultimo_padre: Menzione | None = None
    for menzione in menzioni:
        if menzione.ruolo == "padre":
            ultimo_padre = menzione
        elif menzione.ruolo in ("sposo", "sposa"):
            ultimo_padre = None
        elif menzione.ruolo == "madre" and ultimo_padre is not None:
            scelto = attribuzione.get(ultimo_padre.id)
            if scelto is not None:
                attribuzione[menzione.id] = scelto


# ---------------------------------------------------------------------------
# Il riconoscimento: la stessa persona in atti diversi
# ---------------------------------------------------------------------------

# Quanto possono discordare due eta' della stessa persona. Nei registri
# ottocenteschi l'eta' e' quasi sempre dichiarata a voce e arrotondata:
# lo stesso uomo si dice di quaranta nel 1850 e di cinquantadue nel 1861.
# Misurato sui casi in cui l'identita' e' certa per altra via (stessi
# genitori, stesso coniuge), lo scarto tipico e' di tre anni e la coda
# arriva a otto.
TOLLERANZA_ETA = 6
TOLLERANZA_ETA_APPROSSIMATA = 10

# Fra due date di nascita **certe** — prese dai rispettivi atti di
# nascita — non c'e' tolleranza che tenga: sono due bambini diversi.
TOLLERANZA_NASCITA_CERTA = 1

SOGLIA_NOME = 0.85

# Per quanti anni una persona puo' avere figli. E' il veto piu' solido di
# tutto il modulo, perche' non viene da una statistica sui registri ma
# dalla biologia, e non ha eccezioni da negoziare: una donna che risulta
# partorire nel 1810 e nel 1871 non e' una donna longeva, sono due donne
# con lo stesso nome. Senza questa guardia il riconoscimento fabbricava
# proprio questo — una Vitaliana Ottaviano madre per sessantun anni —
# e l'errore e' insidioso perche' produce una scheda ricchissima, con
# quattro mariti e venti figli, che sembra un successo.
ARCO_FERTILE = {"F": 45, "M": 60}

# I limiti dell'eta' in cui si diventa genitori. Servono a leggere, in
# una riga che non dichiara nessuna eta', l'eta' che l'atto dichiara
# **accanto**: il padre di un uomo morto a settant'anni non ha un'eta'
# scritta, ma e' nato prima del figlio, e di parecchio.
ETA_MINIMA_GENITORE = 13
ETA_MASSIMA_GENITORE = {"M": 75, "F": 55}

# Il gioco da lasciare quando l'anno di nascita del figlio non viene
# dal suo atto di nascita ma da un'eta' dichiarata, che i registri
# arrotondano.
MARGINE_FINESTRA = 5

# Di quanto un figlio puo' nascere dopo la morte del genitore. Per la
# madre di niente; per il padre di una gravidanza, ed e' un caso vero
# che gli atti scrivono 'figlio postumo'.
POSTUMO_PADRE = 1

# Il punteggio da raggiungere perche' due menzioni siano la stessa
# persona. Il nome da solo vale 1,0 e non basta mai: serve almeno un
# indizio strutturale — un genitore, un coniuge — oppure due indizi
# deboli concordi.
SOGLIA_UNIONE = 3.0

# La soglia quando il candidato e' **uno solo**.
#
# E' la regola che decide la densita' dell'archivio, e vale la pena
# spiegarla per bene. Il pericolo di questo mestiere e' l'omonimia: in un
# paese dove il primogenito porta il nome del nonno, "Domenico Pelliccia"
# sono cinque uomini diversi nello stesso mezzo secolo, e cucirli insieme
# fabbrica una persona che non e' mai esistita. Ma quel pericolo *si
# misura*: e' alto quando i candidati compatibili sono molti, ed e' nullo
# quando ce n'e' uno solo.
#
# Un testimone "Egidio Colaneri, 33 anni, proprietario" nel 1834 e un
# testimone "Egidio Colaneri, 45 anni, proprietario" nel 1846 sono lo
# stesso uomo se in tutto il secolo non esiste nessun altro Egidio
# Colaneri con un'eta' compatibile. Tenerli separati non e' prudenza, e'
# un errore in senso opposto: fabbrica due mezzi uomini al posto di uno
# intero, e nasconde a chi guarda che quelle due righe parlano di una
# persona sola.
#
# Quindi con un candidato unico basta il nome piu' un indizio qualsiasi
# che concordi — l'eta', il mestiere, la contrada. Con due o piu'
# candidati non basta piu' niente: quello e' esattamente il caso
# dell'omonimo, e li' si lascia separato e lo si dichiara.
SOGLIA_CANDIDATO_UNICO = 1.5

PESO_DUE_GENITORI = 4.0
PESO_UN_GENITORE = 2.5
PESO_CONIUGE = 2.5
PESO_CONIUGE_DISCORDE = 1.5
PESO_PATRONIMICO = 2.0
PESO_ETA = 1.0
PESO_MESTIERE = 0.5
PESO_CONTRADA = 0.5
PESO_UFFICIO = 2.0


@dataclass
class Persona:
    """Un gruppo di menzioni riconosciute come la stessa persona."""

    id: int
    menzioni: list[Menzione] = field(default_factory=list)

    sesso: str | None = None
    nascita_certa: int | None = None
    # L'intersezione delle finestre delle sue menzioni: fra quali anni
    # questa persona puo' essere nata, secondo tutto cio' che gli atti
    # dicono di lei e dei suoi.
    finestra: tuple[int, int] | None = None
    nascite_stimate: list[tuple[int, bool]] = field(default_factory=list)
    morte: int | None = None
    # L'anno entro cui la persona risulta gia' morta, anche senza atto:
    # ogni volta che un atto la nomina come 'fu' o 'defunto'. E' il modo
    # di sapere qualcosa di chi muore negli anni i cui registri mancano.
    morta_entro: int | None = None

    padri: set[str] = field(default_factory=set)
    madri: set[str] = field(default_factory=set)
    coniugi: set[str] = field(default_factory=set)
    # Gli stessi legami, ma espressi come **persone gia' riconosciute**
    # invece che come nomi ricopiati. Si riempiono solo dopo il primo
    # passaggio, perche' prima le persone non esistono ancora; vedi
    # _accosta_per_identita, che e' il posto dove servono.
    padri_id: set[int] = field(default_factory=set)
    madri_id: set[int] = field(default_factory=set)
    coniugi_id: set[int] = field(default_factory=set)
    figli_id: set[int] = field(default_factory=set)
    patronimici: set[str] = field(default_factory=set)
    uffici: set[str] = field(default_factory=set)
    # Le menzioni attaccate per sola unicita' del candidato, senza
    # evidenza strutturale. Restano marcate perche' l'archivio possa
    # dirlo a chi guarda, e perche' siano le prime da ricontrollare.
    incerte: set[int] = field(default_factory=set)
    # Vero appena una menzione ricopre un ruolo che crea un legame:
    # genitore, coniuge. Serve a sapere se fondere questa persona
    # sposterebbe un ramo, e quindi con quanta prudenza farlo.
    genitore_di: bool = False
    # Gli anni in cui questa persona compare come genitore in un atto di
    # NASCITA, cioe' gli anni in cui ha davvero avuto un figlio. Non
    # basta il ruolo 'madre': una donna e' nominata madre anche nell'atto
    # di morte di un figlio adulto, trent'anni dopo averlo partorito.
    parti: list[int] = field(default_factory=list)

    # Le forme viste, tenute aggiornate a ogni aggiunta invece che
    # ricalcolate. Non e' prematuro: il sindaco firma 1.371 atti, e
    # ricostruire il suo elenco di nomi a ogni confronto costa piu' di
    # tutto il resto del riconoscimento messo insieme.
    _nomi: Counter[str] = field(default_factory=Counter)
    _cognomi: Counter[str] = field(default_factory=Counter)
    _nomi_canonici: set[str] = field(default_factory=set)
    _cognomi_canonici: set[str] = field(default_factory=set)
    mestieri: set[str] = field(default_factory=set)
    contrade: set[str] = field(default_factory=set)

    def aggiungi(self, menzione: Menzione, chiavi: "ChiaviFamiliari") -> None:
        self.menzioni.append(menzione)
        if menzione.nome:
            self._nomi[menzione.nome] += 1
            self._nomi_canonici.add(menzione.chiave_nome)
        if menzione.cognome:
            self._cognomi[menzione.cognome] += 1
            self._cognomi_canonici.add(menzione.chiave_cognome)
        if menzione.professione:
            self.mestieri.add(paleografia.normalizza(menzione.professione))
        contrada = menzione.via or menzione.residenza
        if contrada:
            self.contrade.add(paleografia.normalizza(contrada))
        if menzione.ruolo in RUOLI_CHE_LEGANO:
            self.genitore_di = True
        if menzione.tipo_atto == "nascita" and menzione.ruolo in GENITORI and menzione.anno:
            self.parti.append(menzione.anno)
        if menzione.sesso and not self.sesso:
            self.sesso = menzione.sesso
        if menzione.nascita_certa is not None:
            self.nascita_certa = menzione.nascita_certa
        if menzione.finestra is not None:
            self.finestra = _interseca(self.finestra, menzione.finestra)
        stimata = menzione.anno_nascita
        if stimata is not None and menzione.eta is not None:
            self.nascite_stimate.append((stimata, menzione.eta.approssimata))
        if menzione.tipo_atto == "morte" and menzione.ruolo in ("defunto", "defunta"):
            self.morte = menzione.anno
        elif (menzione.stato_vitale or "").startswith("defunt"):
            if self.morta_entro is None or menzione.anno < self.morta_entro:
                self.morta_entro = menzione.anno

        for chiave in chiavi.padre(menzione):
            self.padri.add(chiave)
        for chiave in chiavi.madre(menzione):
            self.madri.add(chiave)
        for chiave in chiavi.coniuge(menzione):
            self.coniugi.add(chiave)
        if menzione.patronimico:
            self.patronimici.add(paleografia.forma_canonica(menzione.patronimico))
        if menzione.ruolo == "ufficiale":
            # Il ripiego sul ruolo non e' pigrizia: la professione del
            # sindaco manca in centinaia di atti, e senza ripiego le sue
            # firme si spezzerebbero proprio dove il registro e' piu'
            # sbrigativo.
            self.uffici.add(paleografia.normalizza(menzione.professione or menzione.ruolo))

    @property
    def anno_nascita(self) -> int | None:
        if self.nascita_certa is not None:
            return self.nascita_certa
        if not self.nascite_stimate:
            return None
        anni = sorted(a for a, _ in self.nascite_stimate)
        return anni[len(anni) // 2]

    @property
    def nomi(self) -> Counter[str]:
        return self._nomi

    @property
    def cognomi(self) -> Counter[str]:
        return self._cognomi


class ChiaviFamiliari:
    """Traduce i riferimenti familiari di una menzione in chiavi confrontabili.

    Il collegamento dentro l'atto punta a una **riga**; per confrontare
    due menzioni di atti diversi serve invece qualcosa che si possa
    paragonare, cioe' il nome del genitore ridotto a forma canonica.
    """

    def __init__(self, per_id: dict[int, Menzione]) -> None:
        self.per_id = per_id

    def _chiavi(self, riferimento: int | None) -> list[str]:
        if riferimento is None:
            return []
        altra = self.per_id.get(riferimento)
        if altra is None or not altra.nome:
            return []
        # Il nome conserva gli spazi, il cognome no. Non e' una svista:
        # gli spazi del nome separano elementi che vanno confrontati uno
        # per uno — 'Angela' contro 'Angela Maria' — mentre in un cognome
        # lo spazio e' solo una convenzione di grafia ('Di Nardo',
        # 'Dinardo') e collassarlo e' proprio cio' che serve.
        return [f"{paleografia.normalizza(altra.nome)}|{altra.chiave_cognome}"]

    def padre(self, menzione: Menzione) -> list[str]:
        return self._chiavi(menzione.padre)

    def madre(self, menzione: Menzione) -> list[str]:
        return self._chiavi(menzione.madre)

    def coniuge(self, menzione: Menzione) -> list[str]:
        return self._chiavi(menzione.coniuge)


def _interseca(
    una: tuple[int, int] | None, altra: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Le due finestre messe insieme. ``None`` vuol dire 'nessun vincolo'."""
    if una is None:
        return altra
    if altra is None:
        return una
    return (max(una[0], altra[0]), min(una[1], altra[1]))


def _finestre_compatibili(
    una: tuple[int, int] | None,
    altra: tuple[int, int] | None,
    anno_una: int | None = None,
    anno_altra: int | None = None,
) -> bool:
    """Se due persone possono essere nate nello stesso momento.

    Confronta tre cose: le due finestre fra loro, e ciascuna finestra con
    l'anno di nascita **stimato** dell'altra parte. Quest'ultimo e' il
    confronto che conta di piu': la finestra di solito viene da un
    parente e l'anno stimato da un'eta' dichiarata, e sono proprio i due
    capi che il riconoscimento non metteva mai a confronto.

    Sull'anno stimato si lascia la tolleranza d'eta' consueta, perche' e'
    ricavato da un numero detto a voce; sulle finestre no, perche' il
    margine ce l'hanno gia' dentro.
    """
    if una is not None and altra is not None:
        if max(una[0], altra[0]) > min(una[1], altra[1]):
            return False
    for finestra, anno in ((una, anno_altra), (altra, anno_una)):
        if finestra is None or anno is None:
            continue
        if not (finestra[0] - TOLLERANZA_ETA <= anno <= finestra[1] + TOLLERANZA_ETA):
            return False
    return True


def _confronta_insiemi(uno: set[str], altro: set[str]) -> int:
    """1 se si incontrano, -1 se si contraddicono, 0 se non si sa.

    Il confronto non e' per uguaglianza. Le chiavi sono gia' in forma
    canonica, ma la forma canonica non appiana tutto: ``Cicchilliti`` e
    ``Cicchillito`` restano due chiavi diverse, e sono lo stesso uomo
    letto da due scrivani. Trattarle come diverse non fa solo perdere
    un'unione — fa peggio, perche' due genitori "diversi" sono un **veto**,
    e quel veto spezza in due una famiglia che i dati non contestano
    affatto.

    Da qui la terza risposta, che e' la piu' importante delle tre.
    Fra due chiavi che si somigliano ma non abbastanza da valere una
    prova, la risposta giusta non e' "diversi": e' **non lo so**. La
    bambina Maria Clementina Lella nasce nel 1810 da Filippo Lella e
    Margherita Rossi e muore nove giorni dopo; l'atto di morte scrive la
    madre 'Margarita'. Le due grafie stanno a 0,80, cioe' sotto la soglia
    che le dichiarerebbe la stessa donna — e con la sola risposta
    ``-1`` quel decimo di punto diventava un veto assoluto, che teneva
    separate per sempre la neonata e la neonata morta. Sono la stessa
    bambina, e nei registri non c'e' una sola riga che lo contesti.

    Il veto quindi lo si tiene per cio' che il veto sa: due genitori che
    sono **davvero** due persone diverse — Nicola contro Domenico,
    Rossi contro Pelliccia. Sulla grafia incerta si tace, e l'unione la
    deve guadagnare qualcun altro, con una prova sua.
    """
    if not uno or not altro:
        return 0
    if uno & altro:
        return 1
    dubbio = False
    for chiave in uno:
        for altra in altro:
            if _chiavi_vicine(chiave, altra):
                return 1
            if not _chiavi_estranee(chiave, altra):
                dubbio = True
    return 0 if dubbio else -1


# Sotto queste due soglie due chiavi non sono la stessa persona scritta
# male: sono due persone. Stanno **piu' in basso** di quelle che servono
# a dichiarare un'unione (0,85 sul nome, 0,80 sul cognome), e la
# distanza fra le due coppie di soglie e' esattamente la zona in cui
# questo modulo non sa e lo dice.
SOGLIA_NOME_ESTRANEO = 0.72
SOGLIA_COGNOME_ESTRANEO = 0.62


def _chiavi_estranee(una: str, altra: str) -> bool:
    """Due chiavi ``nome|cognome`` che sono due persone diverse davvero."""
    nome_uno, _, cognome_uno = una.partition("|")
    nome_altro, _, cognome_altro = altra.partition("|")
    if (
        nome_uno != nome_altro
        and nomi.somiglianza_nome(nome_uno, nome_altro) < SOGLIA_NOME_ESTRANEO
    ):
        return True
    if (
        cognome_uno != cognome_altro
        and paleografia.somiglianza(cognome_uno, cognome_altro) < SOGLIA_COGNOME_ESTRANEO
    ):
        return True
    return False


def _chiavi_vicine(una: str, altra: str) -> bool:
    """Due chiavi ``nome|cognome`` che sono la stessa persona scritta male.

    Nome e cognome si confrontano separatamente: una chiave intera,
    presa come una stringa sola, diluisce l'errore su una lunghezza
    doppia e finirebbe per accettare accostamenti che sul solo nome non
    passerebbero.
    """
    nome_uno, _, cognome_uno = una.partition("|")
    nome_altro, _, cognome_altro = altra.partition("|")
    if nome_uno != nome_altro and nomi.somiglianza_nome(nome_uno, nome_altro) < SOGLIA_NOME:
        return False
    if cognome_uno == cognome_altro:
        return True
    return paleografia.somiglianza(cognome_uno, cognome_altro) >= SOGLIA_COGNOME


def punteggio(persona: Persona, menzione: Menzione, chiavi: ChiaviFamiliari) -> float | None:
    """Quanto una menzione somiglia a una persona gia' formata.

    ``None`` significa **impossibile**: c'e' una contraddizione che
    nessuna quantita' di indizi favorevoli puo' compensare. Le
    contraddizioni sono di quattro tipi, e sono tutte e quattro casi in
    cui unire produrrebbe una persona che non puo' essere esistita.
    """
    # -- ha un nome? ------------------------------------------------------
    # Una riga senza nome ne' cognome non si riconosce in nessuno. Sono
    # 176 nel corpus: pagine in cui la lettura si e' fermata. Restano
    # nell'archivio una per una — sono persone esistite, e il loro atto
    # dice comunque qualcosa — ma non si attaccano a niente, perche'
    # attaccarle vorrebbe dire deciderlo sull'eta' e basta.
    if not menzione.nome and not menzione.cognome:
        return None

    # -- il sesso ---------------------------------------------------------
    if persona.sesso and menzione.sesso and persona.sesso != menzione.sesso:
        return None

    # -- il nome ----------------------------------------------------------
    #
    # Il confronto e' sulle forme **canoniche distinte**, non su ogni
    # menzione: una persona con quattrocento atti ha comunque due o tre
    # grafie del nome, e sono quelle a fare testo.
    if not menzione.nome:
        nome_migliore = SOGLIA_NOME if persona.nomi else 1.0
    elif menzione.chiave_nome in persona._nomi_canonici:
        nome_migliore = 1.0
    else:
        nome_migliore = max(
            (nomi.somiglianza_nome(forma, menzione.nome) for forma in persona.nomi),
            default=1.0,
        )
    if nome_migliore < SOGLIA_NOME:
        return None

    if menzione.cognome and persona._cognomi_canonici:
        if menzione.chiave_cognome not in persona._cognomi_canonici and not any(
            paleografia.somiglianza(forma, menzione.chiave_cognome) >= SOGLIA_COGNOME
            for forma in persona._cognomi_canonici
        ):
            return None

    # -- le date ----------------------------------------------------------
    nascita = menzione.nascita_certa
    if nascita is not None and persona.nascita_certa is not None:
        if abs(nascita - persona.nascita_certa) > TOLLERANZA_NASCITA_CERTA:
            return None

    stimata = menzione.anno_nascita
    riferimento = persona.anno_nascita
    accordo_eta = 0.0
    if stimata is not None and riferimento is not None:
        approssimata = (menzione.eta is not None and menzione.eta.approssimata) or any(
            a for _, a in persona.nascite_stimate
        )
        limite = TOLLERANZA_ETA_APPROSSIMATA if approssimata else TOLLERANZA_ETA
        # Un anno di nascita certo non si piega all'eta' dichiarata: e'
        # l'eta' a essere arrotondata, non l'atto di nascita.
        if persona.nascita_certa is not None or menzione.nascita_certa is not None:
            limite = max(limite, TOLLERANZA_ETA_APPROSSIMATA)
        scarto = abs(stimata - riferimento)
        if scarto > limite:
            return None
        accordo_eta = PESO_ETA * (1.0 - scarto / limite)

    # -- quando puo' essere nata -------------------------------------------
    # Il vincolo che viene dai parenti, e non dalla sua riga. E' quello
    # che tiene separato il padre di un settantenne dal taglialegna di
    # quarantacinque anni che porta il suo stesso nome.
    if not _finestre_compatibili(
        persona.finestra, menzione.finestra,
        persona.anno_nascita, menzione.anno_nascita,
    ):
        return None

    # -- la nascita -------------------------------------------------------
    # E nessuno compare prima di esserci. E' il veto piu' netto che questo
    # modulo abbia, perche' non poggia su un'eta' arrotondata ma sull'atto
    # di nascita, che e' una data scritta: la sposa del 1813 non e' la
    # bambina nata nel 1820, per quanto si chiamino uguale e abbiano lo
    # stesso padre. Vale per i ruoli che implicano la presenza; essere
    # *nominati* prima, no — un figlio puo' comparire nel matrimonio dei
    # genitori come nascituro di nessuno.
    if persona.nascita_certa is not None and menzione.presente:
        if menzione.anno < persona.nascita_certa:
            return None
    if menzione.nascita_certa is not None:
        if any(m.presente and m.anno < menzione.nascita_certa for m in persona.menzioni):
            return None

    # -- la morte ---------------------------------------------------------
    # Nessuno compare vivo dopo il proprio atto di morte. Vale solo per i
    # ruoli che implicano la presenza: un padre morto viene nominato per
    # decenni negli atti dei figli, ed e' giusto cosi'.
    if persona.morte is not None and menzione.presente and menzione.anno > persona.morte:
        return None
    if (
        menzione.tipo_atto == "morte"
        and menzione.ruolo in ("defunto", "defunta")
        and persona.morte is not None
        and persona.morte != menzione.anno
    ):
        return None

    # -- la fertilita' ----------------------------------------------------
    if menzione.tipo_atto == "nascita" and menzione.ruolo in GENITORI and menzione.anno:
        if _morta_prima(persona.morte, [menzione.anno], persona.sesso or menzione.sesso):
            return None
        if not _fertilita_plausibile(
            persona.parti + [menzione.anno], persona.sesso or menzione.sesso
        ):
            return None
    # E all'incontrario: l'atto di morte non si attacca a chi risulta
    # aver partorito dopo quella data.
    if (
        menzione.tipo_atto == "morte"
        and menzione.ruolo in ("defunto", "defunta")
        and _morta_prima(menzione.anno, persona.parti, persona.sesso or menzione.sesso)
    ):
        return None

    # -- i genitori -------------------------------------------------------
    padre = _confronta_insiemi(persona.padri, set(chiavi.padre(menzione)))
    madre = _confronta_insiemi(persona.madri, set(chiavi.madre(menzione)))
    if padre < 0 and madre < 0:
        return None
    # Un genitore che si contraddice mentre l'altro combacia capita
    # davvero — il padre e' lo stesso e la madre e' la seconda moglie,
    # oppure una delle due letture e' storta — ma due contraddizioni su
    # due sono un'altra famiglia.

    valore = nome_migliore + accordo_eta

    if padre > 0 and madre > 0:
        valore += PESO_DUE_GENITORI
    elif padre > 0 or madre > 0:
        valore += PESO_UN_GENITORE
    if padre < 0 or madre < 0:
        valore -= PESO_UN_GENITORE

    coniuge = _confronta_insiemi(persona.coniugi, set(chiavi.coniuge(menzione)))
    if coniuge > 0:
        valore += PESO_CONIUGE
    elif coniuge < 0:
        # Due mogli diverse non sono un veto — ci si risposa, e le vedove
        # in questi registri si risposano spesso — ma non sono nemmeno
        # niente, che e' quello che valevano finora. Tolgono il diritto di
        # unire sulla sola unicita' del candidato: perche' due righe
        # finiscano nella stessa scheda con due coniugi che si
        # contraddicono, deve esserci dell'altro che le tiene insieme.
        valore -= PESO_CONIUGE_DISCORDE

    if menzione.patronimico:
        chiave = paleografia.forma_canonica(menzione.patronimico)
        if chiave in persona.patronimici:
            valore += PESO_PATRONIMICO
        # Il patronimico che contraddice il padre gia' noto e' una prova
        # forte del contrario: e' proprio il caso in cui lo scrivano lo
        # ha scritto per distinguere due omonimi.
        elif persona.padri and not any(p.split("|")[0] == chiave for p in persona.padri):
            valore -= PESO_PATRONIMICO

    # Il sindaco e il cancelliere firmano centinaia di atti di seguito e
    # non hanno mai genitori scritti: senza questo resterebbero
    # frantumati in centinaia di persone diverse con lo stesso nome e lo
    # stesso ufficio negli stessi anni.
    if menzione.ruolo == "ufficiale" and menzione.professione:
        if paleografia.normalizza(menzione.professione) in persona.uffici:
            valore += PESO_UFFICIO

    if menzione.professione and paleografia.normalizza(menzione.professione) in persona.mestieri:
        valore += PESO_MESTIERE

    contrada = menzione.via or menzione.residenza
    if contrada and paleografia.normalizza(contrada) in persona.contrade:
        valore += PESO_CONTRADA

    return valore


# ---------------------------------------------------------------------------
# Caricamento
# ---------------------------------------------------------------------------

SELEZIONE = """
    SELECT p.id, p.atto, p.ruolo, p.nome, p.nome_letto, p.cognome,
           p.cognome_letto, p.cognome_origine, p.incerto, p.eta,
           p.professione, p.residenza, p.via AS via_persona, p.stato_vitale,
           p.note, a.tipo, a.anno, a.data_atto, a.data_evento,
           a.via AS via_atto, a.immagine
      FROM persone p JOIN atti a ON a.id = p.atto
     ORDER BY p.atto, p.id
"""


def carica_menzioni(
    conn: sqlite3.Connection,
    correzioni: dict[str, str] | None = None,
    genere: nomi.Genere | None = None,
    scambi: set[int] | None = None,
    interpreta: "Callable[[Menzione], None] | None" = None,
) -> list[Menzione]:
    """Legge le menzioni e ci applica l'igiene dei nomi.

    Le correzioni si applicano **qui**, in memoria, e non con una UPDATE
    sul database: la tabella ``persone`` e' l'esito della lettura delle
    pagine, e riscriverla vorrebbe dire perdere la possibilita' di
    ricostruire l'albero in un altro modo domani. Cio' che si costruisce
    da qui in poi sono tabelle nuove, che si buttano e si rifanno.
    """
    correzioni = correzioni or {}
    scambi = scambi or set()

    menzioni: list[Menzione] = []
    conn.row_factory = sqlite3.Row
    for riga in conn.execute(SELEZIONE):
        nome, cognome = riga["nome"], riga["cognome"]
        invertita = riga["id"] in scambi
        if invertita:
            nome, cognome = cognome, nome

        # Il 'fu' davanti al nome dice che quella persona era gia' morta:
        # e' un dato vero, e va nel campo che lo porta, non dentro il nome.
        nome, morto_nome = nomi.separa_stato_vitale(nome)
        cognome, morto_cognome = nomi.separa_stato_vitale(cognome)
        stato_vitale = riga["stato_vitale"]
        if (morto_nome or morto_cognome) and not stato_vitale:
            stato_vitale = "defunto"

        nome = nomi.applica_correzioni(nome, correzioni)
        nome, patronimico = nomi.separa_patronimico(nome)

        menzione = Menzione(
            id=riga["id"],
            atto=riga["atto"],
            tipo_atto=riga["tipo"] or "",
            anno=riga["anno"] or 0,
            data=riga["data_atto"] or riga["data_evento"],
            ruolo=(riga["ruolo"] or "").strip().casefold(),
            nome=nome,
            cognome=cognome,
            nome_letto=riga["nome_letto"] or riga["nome"],
            cognome_letto=riga["cognome_letto"] or riga["cognome"],
            cognome_origine=riga["cognome_origine"],
            incerto=bool(riga["incerto"]),
            eta_letta=riga["eta"],
            professione=riga["professione"],
            residenza=riga["residenza"],
            via=riga["via_persona"] or riga["via_atto"],
            stato_vitale=stato_vitale,
            note=riga["note"],
            immagine=riga["immagine"],
            patronimico=patronimico,
            invertita=invertita,
            eta=nomi.analizza_eta(riga["eta"]),
        )
        # Lo strato di interpretazione, quando c'e', lavora **prima**
        # della lettura delle famiglie: separa dal cognome cio' che
        # cognome non e' ('Lella fu Michele'), raccoglie le letture
        # alternative, e quello che ne esce e' il valore su cui l'atto
        # va letto. Dopo sarebbe tardi: la famiglia dentro l'atto si
        # ricostruisce anche confrontando i cognomi.
        if interpreta is not None:
            interpreta(menzione)
        if genere is not None:
            menzione.sesso = _sesso(menzione, genere)
        menzioni.append(menzione)

    for gruppo in _per_atto(menzioni).values():
        famiglia_dell_atto(gruppo)

    # Dopo, non dentro: la finestra di un genitore si ricava dal figlio,
    # e il figlio lo si sa solo quando la famiglia dell'atto e' letta.
    finestre_dai_figli(menzioni)

    return menzioni


def _per_atto(menzioni: list[Menzione]) -> dict[int, list[Menzione]]:
    per_atto: dict[int, list[Menzione]] = defaultdict(list)
    for menzione in menzioni:
        per_atto[menzione.atto].append(menzione)
    return per_atto


# Le professioni che dicono il sesso da sole. Non e' una lista di
# mestieri femminili: e' una lista di **forme** femminili, e vale perche'
# l'italiano dei registri declina il mestiere su chi lo esercita.
_MESTIERI_FEMMINILI = frozenset({
    "contadina", "filatrice", "levatrice", "tessitrice", "massara",
    "casalinga", "serva", "pettinatrice", "cucitrice", "fornaia", "ostetrica",
    "possidente donna", "bracciala", "sarta", "filandiera",
})


def _sesso(menzione: Menzione, genere: nomi.Genere) -> str | None:
    """Il sesso di una menzione, dalla prova piu' forte alla piu' debole.

    Il ruolo viene prima di tutto: ``madre`` e' una donna qualunque nome
    porti, e nessuna statistica sui nomi puo' contraddirlo. Solo dopo
    viene il nome, e per ultima la forma del mestiere.
    """
    if menzione.ruolo in nomi.RUOLI_MASCHILI:
        return "M"
    if menzione.ruolo in nomi.RUOLI_FEMMINILI:
        return "F"
    stato = (menzione.stato_vitale or "").casefold()
    if stato in ("defunta", "definta", "vedova", "nubile"):
        return "F"
    if stato in ("vedovo", "celibe"):
        return "M"
    dal_nome = genere.di(menzione.nome)
    if dal_nome:
        return dal_nome
    if paleografia.normalizza(menzione.professione or "") in _MESTIERI_FEMMINILI:
        return "F"
    return None


# ---------------------------------------------------------------------------
# Il riconoscimento
# ---------------------------------------------------------------------------

def _forza(menzione: Menzione) -> int:
    """In che ordine conviene guardare le menzioni.

    Prima quelle che portano piu' informazione — chi ha padre **e** madre
    scritti — perche' formano nuclei ben identificati a cui le menzioni
    piu' povere possono poi agganciarsi. All'incontrario, un testimone
    senza eta' guardato per primo aprirebbe una persona vaga, e quella
    persona vaga si prenderebbe poi tutti gli omonimi del secolo.
    """
    if menzione.padre is not None and menzione.madre is not None:
        return 0
    if menzione.padre is not None or menzione.madre is not None:
        return 1
    if menzione.coniuge is not None:
        return 2
    if menzione.eta is not None:
        return 3
    return 4


def _vicinato(chiavi: set[str], soglia: float) -> dict[str, tuple[str, ...]]:
    """Per ogni chiave, quelle abbastanza simili da valere un confronto.

    E' lo stesso taglio di lunghezza del raggruppamento delle varianti, e
    per la stessa ragione dimostrata: cancellare un carattere costa 1,
    quindi due forme troppo diverse in lunghezza non possono superare la
    soglia, e confrontarle e' tempo buttato.
    """
    ordinate = sorted(chiavi, key=len)
    vicini: dict[str, set[str]] = {c: {c} for c in ordinate}
    for i, corta in enumerate(ordinate):
        if not corta:
            continue
        for lunga in ordinate[i + 1 :]:
            if len(corta) < soglia * len(lunga):
                break
            if paleografia.somiglianza(corta, lunga) >= soglia:
                vicini[corta].add(lunga)
                vicini[lunga].add(corta)
    # Ordinate, per la stessa ragione dei bacini: da qui passa l'ordine in
    # cui i candidati vengono guardati, e a parita' di punteggio vince il
    # primo. Un insieme lo deciderebbe a caso a ogni esecuzione.
    return {chiave: tuple(sorted(valore)) for chiave, valore in vicini.items()}


def _scegli(
    candidate: list[Persona], menzione: Menzione, chiavi: ChiaviFamiliari
) -> tuple[Persona | None, bool]:
    """La persona a cui attaccare la menzione, e se la scelta e' sicura.

    Rende ``(persona, sicura)``. ``sicura`` distingue le due strade per
    cui una menzione si attacca: l'evidenza strutturale — genitori,
    coniuge — che vale in se', e l'unicita' del candidato, che vale solo
    perche' non c'e' nessun altro con cui confonderlo. La seconda e' una
    buona inferenza, non un fatto, e l'archivio la segna come tale.
    """
    ammesse: list[tuple[float, Persona]] = []
    for persona in candidate:
        valore = punteggio(persona, menzione, chiavi)
        if valore is not None and valore >= SOGLIA_CANDIDATO_UNICO:
            ammesse.append((valore, persona))

    if not ammesse:
        return None, True

    ammesse.sort(key=lambda coppia: -coppia[0])
    punti, migliore = ammesse[0]

    if punti >= SOGLIA_UNIONE:
        return migliore, True
    if len(ammesse) == 1:
        return migliore, False
    # Piu' candidati e nessuna evidenza forte: e' il caso dell'omonimo, e
    # qui si sbaglia comunque. Fra sbagliare unendo e sbagliare
    # separando, separare lascia il dato ispezionabile.
    return None, True


def riconosci(menzioni: list[Menzione]) -> list[Persona]:
    """Raggruppa le menzioni nelle persone che le hanno prodotte.

    E' un raggruppamento incrementale: ogni menzione cerca fra le persone
    gia' formate quella che le somiglia di piu', e se nessuna raggiunge
    la soglia ne apre una nuova. **Nessuna menzione resta fuori**: il
    caso peggiore e' una persona di una menzione sola, che e' esattamente
    cio' che merita un testimone visto una volta nel 1834.
    """
    per_id = {m.id: m for m in menzioni}
    chiavi = ChiaviFamiliari(per_id)

    vicini_cognome = _vicinato({m.chiave_cognome for m in menzioni}, SOGLIA_COGNOME)
    vicini_nome = _vicinato(
        {parte for m in menzioni for parte in m.parti_nome}, SOGLIA_NOME
    )

    indice: dict[tuple[str, str], list[Persona]] = defaultdict(list)
    # Una persona va nell'indice una volta per chiave, non una volta per
    # menzione: senza questo il bacino di 'Vincenzo Colella' — il sindaco,
    # 1.371 atti — crescerebbe di una voce a ogni firma, e il confronto
    # diventerebbe quadratico sul bacino piu' popolato del corpus.
    indicizzate: dict[tuple[str, str], set[int]] = defaultdict(set)
    persone: list[Persona] = []

    ordinate = sorted(menzioni, key=lambda m: (_forza(m), m.anno, m.id))
    for menzione in ordinate:
        candidate: list[Persona] = []
        visti: set[int] = set()
        for cognome in vicini_cognome.get(menzione.chiave_cognome, (menzione.chiave_cognome,)):
            for parte in menzione.parti_nome:
                for nome in vicini_nome.get(parte, (parte,)):
                    for persona in indice.get((cognome, nome), ()):
                        if id(persona) not in visti:
                            visti.add(id(persona))
                            candidate.append(persona)

        migliore, sicura = _scegli(candidate, menzione, chiavi)

        if migliore is None:
            migliore = Persona(id=len(persone) + 1)
            persone.append(migliore)
        elif not sicura:
            migliore.incerte.add(menzione.id)
        migliore.aggiungi(menzione, chiavi)

        for parte in menzione.parti_nome:
            chiave = (menzione.chiave_cognome, parte)
            if id(migliore) not in indicizzate[chiave]:
                indicizzate[chiave].add(id(migliore))
                indice[chiave].append(migliore)

    frequenze = Counter(m.cognome for m in menzioni if m.cognome)
    return consolida(persone, chiavi, frequenze)


# ---------------------------------------------------------------------------
# Il consolidamento
# ---------------------------------------------------------------------------

# Quanto devono concordare due anni di nascita stimati perche' due
# persone senza parentele note si considerino la stessa. Piu' stretto
# della tolleranza usata sulle singole menzioni: qui non c'e' nessun
# altro indizio a sostenere l'accostamento, e l'eta' deve reggerlo da sola.
TOLLERANZA_CONSOLIDAMENTO = 3

SOGLIA_CONSOLIDAMENTO = 2.0

# Quanti giri di consolidamento al massimo. Non e' una scelta di
# prudenza ma una rete: il ciclo si ferma da solo quando nessuna regola
# trova piu' niente, e in pratica ci arriva molto prima. Il tetto e' li'
# perche' un errore futuro in una delle regole — una che disfa quello
# che un'altra fa — non mandi la costruzione in eterno senza dirlo.
GIRI_MASSIMI = 40


def consolida(
    persone: list[Persona],
    chiavi: ChiaviFamiliari,
    frequenze: Counter[str] | None = None,
) -> list[Persona]:
    """Riunisce le persone che il passaggio incrementale ha lasciato divise.

    Serve perche' il riconoscimento guarda le menzioni **una alla volta**
    e in un ordine deciso prima di sapere come andra' a finire. Quando
    quattro testimoni "Vincenzo Colapietro, sessantotto anni" arrivano
    uno dopo l'altro senza che nessuno porti un genitore, ognuno apre una
    persona nuova, e da quel momento si bloccano a vicenda: sono in molti,
    quindi nessuno e' il candidato unico di nessuno. Il risultato e'
    quattro quarti d'uomo al posto di un uomo, ed e' un esito peggiore sia
    dell'unirli sia del non averli mai divisi.

    Il criterio giusto non e' *quanti* sono i candidati ma **se sono
    compatibili fra loro**. Quattro gruppi con lo stesso nome e lo stesso
    anno di nascita non sono quattro possibilita' fra cui scegliere: sono
    lo stesso uomo letto quattro volte. Quattro gruppi con quattro anni di
    nascita diversi sono quattro uomini, e restano quattro.

    Su una cosa il consolidamento e' deliberatamente **asimmetrico**, ed
    e' la scelta piu' importante di questo modulo. Quando anche una sola
    delle due parti porta con se' una parentela — un padre, una madre, un
    coniuge, un figlio — l'unione pretende evidenza strutturale, perche'
    sbagliare li' significa attaccare un ramo all'albero sbagliato, che e'
    il danno peggiore che questo archivio possa fare. Quando invece
    nessuna delle due porta parentele — due testimoni, due dichiaranti —
    unire non muove nessun ramo: cambia solo se quelle righe stiano in una
    scheda o in due, e li' basta che il nome e l'eta' concordino.
    """
    # Le regole stanno in scala di rischio: prima quelle che pretendono
    # una prova strutturale, per ultime quelle che si accontentano di
    # meno. Una regola piu' larga entra in gioco **solo quando tutte
    # quelle sopra di lei hanno finito**, cosi' che le unioni facili si
    # facciano per la ragione migliore che c'e', e non per la prima che
    # capita. E ogni volta che una qualunque riesce si ricomincia dalla
    # prima: la persona appena fatta e' piu' ricca di prima, e puo'
    # sostenere un accostamento che un momento fa non reggeva.
    for giro in range(GIRI_MASSIMI):
        if _accosta_nei_bacini(persone, chiavi):
            continue
        if _unifica_genitori(persone, chiavi):
            continue
        if frequenze is not None and _ricuci_grafie(persone, chiavi, frequenze):
            continue
        if _accosta_per_identita(persone, chiavi):
            continue
        if _assorbi_sciolte(persone, chiavi):
            continue
        # Per ultima la ricostituzione delle famiglie, che e' la regola
        # piu' larga di tutte e ha il diritto di esserlo perche' e'
        # l'unica che ragiona su coppie invece che su persone. Entra in
        # gioco solo quando tutte le altre hanno finito: cosi' cio' che
        # si poteva unire per il cognome e' gia' unito, e a lei restano
        # esattamente i casi in cui il cognome non era leggibile.
        from history_maker import famiglie

        if famiglie.ricostituisci(persone, chiavi, frequenze):
            continue
        logger.debug("consolidamento chiuso in %d giri", giro + 1)
        break
    else:
        logger.warning(
            "il consolidamento non si e' fermato in %d giri: restano unioni da fare",
            GIRI_MASSIMI,
        )

    _collega_generazioni(persone, chiavi)

    rimaste = [p for p in persone if p.menzioni]
    for numero, persona in enumerate(rimaste, start=1):
        persona.id = numero
    return rimaste


def _legami_come_identita(persone: list[Persona]) -> None:
    """Riscrive i legami di ogni persona come persone, non come nomi."""
    di_menzione: dict[int, Persona] = {}
    for persona in persone:
        for menzione in persona.menzioni:
            di_menzione[menzione.id] = persona

    for persona in persone:
        persona.padri_id.clear()
        persona.madri_id.clear()
        persona.coniugi_id.clear()
        persona.figli_id.clear()

    for persona in persone:
        if not persona.menzioni:
            continue
        for menzione in persona.menzioni:
            for riferimento, raccolta, genitore in (
                (menzione.padre, persona.padri_id, True),
                (menzione.madre, persona.madri_id, True),
                (menzione.coniuge, persona.coniugi_id, False),
            ):
                if riferimento is None:
                    continue
                altra = di_menzione.get(riferimento)
                if altra is None or altra is persona:
                    continue
                raccolta.add(id(altra))
                if genitore:
                    altra.figli_id.add(id(persona))


def _accosta_per_identita(persone: list[Persona], chiavi: ChiaviFamiliari) -> bool:
    """Riunisce chi ha in comune una **persona**, non un nome scritto.

    E' il passaggio che mancava, ed e' quello che spiega la maggior parte
    dei doppioni rimasti. Fino a qui due schede si confrontano attraverso
    il nome del parente che le lega, ricopiato dall'atto: il padre di
    questa e' 'Filippo Lella', il padre di quella e' 'Filippo Lella',
    quindi forse sono la stessa. Ma il nome ricopiato porta addosso
    l'errore di chi ha letto la pagina, e basta una lettera perche' due
    schede della stessa persona non si riconoscano piu'.

    Giuseppe Lella, contadino, sta nell'archivio in due schede. In una e'
    padre nel 1840, 1848 e 1856; nell'altra e' padre nel 1843. La moglie
    e' la stessa donna in tutti e quattro gli atti — l'archivio l'ha
    gia' riconosciuta, e' Annangela Colella, cinque menzioni, una scheda
    sola — ma nell'atto del 1843 lo scrivano scrive 'Coletti'. Le due
    schede del marito confrontavano 'anangela|colela' con
    'anangela|coleti', non si somigliavano abbastanza, e Giuseppe restava
    due uomini pur avendo, letteralmente, la stessa moglie.

    Quindi: finito il primo giro, i legami si riscrivono come identita' —
    non 'il padre si chiama X' ma 'il padre e' **quella** persona' — e si
    ricomincia. E' la prova piu' forte che questo modulo possa avere,
    perche' non e' un confronto fra stringhe: e' un riferimento a una
    persona che l'archivio ha gia' riconosciuto per conto suo.
    """
    _legami_come_identita(persone)

    cambiato = False
    vive = [p for p in persone if p.menzioni]
    for gruppo in _bacini(vive).values():
        if len(gruppo) < 2:
            continue
        for prima in gruppo:
            if not prima.menzioni:
                continue
            for seconda in gruppo:
                if seconda is prima or not seconda.menzioni:
                    continue
                if not _stesso_parente(prima, seconda):
                    continue
                # L'eta' dichiarata e' il dato piu' debole del registro:
                # la si dice a voce e la si arrotonda. Quando dall'altra
                # parte c'e' una moglie o un figlio che sono la **stessa
                # persona**, e' l'eta' a doversi piegare, non la
                # parentela. Carmine Moretta si dichiara di trentacinque
                # anni nel 1813 e di trentatre nel 1821: sono dieci anni
                # di scarto e una moglie sola, Maria Lella, con gli
                # stessi tre figli.
                if not _senza_contraddizioni(
                    prima, seconda, TOLLERANZA_ETA_APPROSSIMATA
                ):
                    continue
                _fondi(prima, seconda, chiavi, incerta=True)
                cambiato = True
    return cambiato


def _stesso_parente(prima: Persona, seconda: Persona) -> bool:
    """Se le due schede hanno in comune una persona gia' riconosciuta.

    Il coniuge basta da solo: due uomini con lo stesso nome sposati alla
    stessa donna sono un uomo solo. I genitori si pretendono tutti e due,
    perche' un padre in comune sono due fratelli, e i fratelli portano lo
    stesso cognome e spesso lo stesso nome del nonno.
    """
    if prima.figli_id & seconda.figli_id:
        # Due schede che risultano genitore dello **stesso** bambino, con
        # lo stesso nome e lo stesso cognome, sono una scheda sola. E' la
        # prova piu' forte di tutte: non passa per nessun nome ricopiato.
        return True
    if prima.coniugi_id & seconda.coniugi_id:
        return True
    if (prima.padri_id & seconda.padri_id) and (prima.madri_id & seconda.madri_id):
        return True
    return False


def _assorbi_sciolte(persone: list[Persona], chiavi: ChiaviFamiliari) -> bool:
    """Versa in una persona documentata le menzioni sciolte che le girano intorno.

    E' il caso piu' frequente di tutti, e prima restava fuori per un
    difetto della regola, non per prudenza vera.

    Manasse Franchella, agrimensore, sta nell'archivio con ottantun
    menzioni: la moglie, quattro figli, il suo atto di morte nel 1865. E
    accanto a lui stava un secondo "Manasse Franchella, agrimensore" di
    cinque menzioni — trentatre anni nel 1821, quarantatre nel 1832,
    settanta nel 1856 — senza genitori, senza moglie, senza figli. E' lo
    stesso uomo: il nome e' raro, il mestiere e' lo stesso, l'eta' torna
    a due anni. Ma la regola del consolidamento chiedeva una prova
    strutturale appena **una delle due** parti avesse una parentela, e
    quella prova la seconda non poteva darla per definizione — non ha
    parentele, e' fatta di righe da testimone.

    La regola giusta guarda cosa l'unione muove davvero. Fondere due
    persone che hanno tutte e due delle parentele sposta un ramo, e li'
    la prudenza va tenuta intera. Fondere un fascio di menzioni **senza
    nessuna parentela** dentro una persona documentata non sposta niente:
    nessun figlio cambia padre, nessun matrimonio cambia sposa. Cambia
    solo in quale scheda stiano quelle righe — che e' esattamente il caso
    in cui questo modulo ha sempre detto di poter essere generoso.

    Il pericolo che resta e' l'omonimia, e si disinnesca come altrove:
    **il fascio si attacca solo se la persona documentata a cui puo'
    appartenere e' una sola.** Dove ci sono tre Domenico Pelliccia
    compatibili, il fascio resta per conto suo e si vede che li' l'
    archivio non sa decidere. Le menzioni spostate restano marcate
    incerte, perche' questa e' un'inferenza e va detto.
    """
    vive = [p for p in persone if p.menzioni]
    documentate: dict[int, list[Persona]] = defaultdict(list)
    sciolte: dict[int, Persona] = {}
    for gruppo in _bacini(vive).values():
        radicate = [p for p in gruppo if _ha_parentele(p)]
        if not radicate:
            continue
        for persona in gruppo:
            # L'atto di nascita e' un'origine anche senza genitori
            # leggibili: quel bambino lo cuce all'adulto che e' diventato
            # _collega_generazioni, che pretende l'unicita' nei due sensi.
            if _ha_parentele(persona) or persona.nascita_certa is not None:
                continue
            sciolte[id(persona)] = persona
            documentate[id(persona)].extend(radicate)

    cambiato = False
    for chiave, sciolta in sciolte.items():
        if not sciolta.menzioni:
            continue
        candidate: list[Persona] = []
        viste: set[int] = set()
        for radicata in documentate[chiave]:
            if id(radicata) in viste or not radicata.menzioni:
                continue
            viste.add(id(radicata))
            if _senza_contraddizioni(radicata, sciolta) and _corrobora(radicata, sciolta):
                candidate.append(radicata)
        if len(candidate) == 1:
            _fondi(candidate[0], sciolta, chiavi, incerta=True)
            cambiato = True
    return cambiato


def _corrobora(documentata: Persona, sciolta: Persona) -> bool:
    """Cosa, oltre al nome, sostiene che il fascio sia di quella persona.

    Il nome da solo non basta mai, nemmeno qui dove l'unione non muove
    rami: in un paese di tremilacinquecento Pelliccia il nome e' la cosa
    che si ripete, non quella che distingue. Serve l'eta' che torna —
    con lo stesso metro stretto del consolidamento, tre anni — oppure
    l'ufficio, che e' cio' che tiene insieme le centinaia di firme del
    sindaco e del cancelliere.
    """
    if documentata.uffici & sciolta.uffici:
        return True
    return _eta_concorde(documentata, sciolta, TOLLERANZA_CONSOLIDAMENTO)


def _accosta_nei_bacini(persone: list[Persona], chiavi: ChiaviFamiliari) -> bool:
    """Un giro di accostamenti fra persone che stanno nello stesso bacino."""
    cambiato = False
    vive = [p for p in persone if p.menzioni]
    for gruppo in _bacini(vive).values():
        if len(gruppo) < 2:
            continue
        for prima in gruppo:
            if not prima.menzioni:
                continue
            for seconda in gruppo:
                if seconda is prima or not seconda.menzioni:
                    continue
                if _accostabili(prima, seconda):
                    _fondi(prima, seconda, chiavi)
                    cambiato = True
    return cambiato


def _collega_generazioni(persone: list[Persona], chiavi: ChiaviFamiliari) -> None:
    """Cuce l'adulto al bambino che era: e' il passaggio che fa l'albero.

    Senza questo si ottengono migliaia di famiglie nucleari perfette e
    scollegate fra loro. Il motivo e' che le due meta' di una vita
    lasciano tracce di natura diversa: l'atto di nascita di Giuseppe dice
    chi sono i suoi genitori ma non dira' mai chi sara' sua moglie;
    l'atto di nascita di suo figlio, trent'anni dopo, lo nomina come
    padre e non dice una parola di **suoi** genitori. Le due schede non
    hanno nessun campo in comune oltre al nome, e per questo tutti i
    controlli precedenti — che pretendono un genitore o un coniuge in
    comune — non possono trovarle.

    Cio' che le lega e' l'anno di nascita: certo da una parte, perche'
    scritto sull'atto, e ricavato dall'altra dall'eta' che l'uomo dichiara
    quando denuncia il figlio. Se in tutto il paese c'e' **un solo**
    bambino con quel nome, quel cognome e un anno di nascita compatibile,
    quel bambino e' lui.

    L'unicita' qui e' richiesta nei due sensi, e non e' pignoleria: e' la
    differenza fra cucire due meta' della stessa vita e attaccare un ramo
    all'albero sbagliato. Se ci sono due Giuseppe Pelliccia nati a un
    anno di distanza, nessuno dei due si aggancia — e resta scritto nella
    scheda che li' l'albero si interrompe per omonimia, che e' un'
    informazione utile invece che una parentela inventata.
    """
    for _ in range(3):
        cambiato = False
        vive = [p for p in persone if p.menzioni]
        for gruppo in _bacini(vive).values():
            if len(gruppo) < 2:
                continue
            adulti = [p for p in gruppo if p.menzioni and not _ha_origine(p)]
            bambini = [p for p in gruppo if p.menzioni and _ha_origine(p)]
            if not adulti or not bambini:
                continue
            for adulto in adulti:
                if not adulto.menzioni:
                    continue
                candidati = [b for b in bambini if b.menzioni and _stessa_vita(adulto, b)]
                if len(candidati) != 1:
                    continue
                bambino = candidati[0]
                inversi = [
                    a for a in adulti if a.menzioni and _stessa_vita(a, bambino)
                ]
                if len(inversi) != 1:
                    continue
                _fondi(bambino, adulto, chiavi)
                cambiato = True
        if not cambiato:
            return


def _ha_origine(persona: Persona) -> bool:
    """Se di questa persona si sa da dove viene: i genitori, o l'atto di nascita."""
    return persona.nascita_certa is not None or bool(persona.padri or persona.madri)


def _stessa_vita(adulto: Persona, bambino: Persona) -> bool:
    """Se l'adulto puo' essere quel bambino cresciuto."""
    if adulto is bambino or not _senza_contraddizioni(adulto, bambino):
        return False
    # Serve la data da tutte e due le parti: e' l'unica prova in gioco.
    nato = bambino.anno_nascita
    stimato = adulto.anno_nascita
    if nato is None or stimato is None:
        return False
    if abs(nato - stimato) > TOLLERANZA_ETA:
        return False
    # Un bambino morto in fasce non e' diventato il padre di nessuno.
    if bambino.morte is not None and any(
        m.presente and m.anno > bambino.morte for m in adulto.menzioni
    ):
        return False
    return True


def _bacini(persone: list[Persona]) -> dict[tuple[str, str], list[Persona]]:
    """Raggruppa per cognome e per ogni PAROLA del nome.

    Una persona finisce in piu' bacini, uno per elemento del suo nome. E'
    il solo modo perche' 'Angela Maria Di Nardo' e 'Angela Di Nardo' si
    trovino: sul nome intero stanno in due scaffali diversi e nessun
    confronto le mette mai una accanto all'altra.
    """
    bacini: dict[tuple[str, str], list[Persona]] = defaultdict(list)
    for persona in persone:
        # Le chiavi si ordinano prima di girarci sopra. Non e' pignoleria:
        # sono insiemi di stringhe, e Python randomizza l'ordine di
        # iterazione a ogni processo. Da li' passava l'ordine dei bacini,
        # dall'ordine dei bacini quello delle fusioni, e due ricostruzioni
        # dello stesso archivio davano due alberi leggermente diversi —
        # che in un archivio genealogico e' un difetto in se'.
        parti = sorted({
            paleografia.forma_canonica(parte)
            for forma in persona.nomi
            for parte in nomi.parti_del_nome(forma)
        }) or [""]
        for cognome in sorted(persona._cognomi_canonici) or [""]:
            for parte in parti:
                if parte or cognome:
                    bacini[(cognome, parte)].append(persona)
    # Una persona con due grafie del nome che condividono una parola
    # finirebbe due volte nello stesso bacino.
    return {
        chiave: list({id(p): p for p in gruppo}.values())
        for chiave, gruppo in bacini.items()
    }


def _ha_parentele(persona: Persona) -> bool:
    """Se unire questa persona sposterebbe un ramo dell'albero.

    Non basta guardare i genitori *della* persona: chi compare come
    ``padre`` non ha genitori scritti da nessuna parte, ma ha dei figli,
    e fonderlo con un altro trasferisce quei figli. Il ruolo che una
    menzione ricopre e' esso stesso un legame.
    """
    return bool(persona.padri or persona.madri or persona.coniugi or persona.genitore_di)


def _senza_contraddizioni(
    prima: Persona,
    seconda: Persona,
    tolleranza: int = TOLLERANZA_ETA,
    veto_genitori: bool = True,
) -> bool:
    """Se nulla, nei dati, vieta che siano la stessa persona.

    Sono i soli veti: il sesso, le date che non stanno insieme, i
    genitori dichiarati e diversi. Passarli non e' una ragione per unire
    — quella la cercano i chiamanti — ma non passarli e' una ragione
    definitiva per non farlo.

    ``veto_genitori`` puo' spegnere l'ultimo dei tre, e lo fa un solo
    chiamante: :mod:`famiglie`, che arriva con una prova piu' forte —
    stesso figlio e stesso coniuge — di quanto valga un nome di genitore
    letto una volta sola su una pagina rovinata. Non e' una scorciatoia:
    e' che in quei casi i genitori discordi *sono* l'errore che si sta
    correggendo. Ovunque altrove il veto resta, perche' altrove non c'e'
    niente che lo superi.
    """
    if prima.sesso and seconda.sesso and prima.sesso != seconda.sesso:
        return False

    # Due atti di nascita diversi sono due bambini diversi, sempre.
    if prima.nascita_certa is not None and seconda.nascita_certa is not None:
        if abs(prima.nascita_certa - seconda.nascita_certa) > TOLLERANZA_NASCITA_CERTA:
            return False
    if prima.morte is not None and seconda.morte is not None and prima.morte != seconda.morte:
        return False

    if not _finestre_compatibili(
        prima.finestra, seconda.finestra, prima.anno_nascita, seconda.anno_nascita
    ):
        return False

    # Nessuno compare prima di essere nato.
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.nascita_certa is None:
            continue
        if any(m.presente and m.anno < una.nascita_certa for m in altra.menzioni):
            return False

    # Nessuno compare vivo dopo essere morto.
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.morte is None:
            continue
        if any(m.presente and m.anno > una.morte for m in altra.menzioni):
            return False
        # Il ruolo di genitore non e' fra quelli che implicano la
        # presenza, ed e' giusto: una madre morta viene nominata negli
        # atti dei figli per trent'anni. Ma il genitore di un atto di
        # **nascita** e' un'altra cosa — quello e' il parto, e chi
        # partorisce quell'anno quell'anno era viva. Senza questa riga
        # la neonata Maria Clementina Lella, morta nel 1810 a nove
        # giorni, si prendeva le maternita' di una sua omonima del 1844.
        if _morta_prima(una.morte, altra.parti, una.sesso or altra.sesso):
            return False

    nascita_prima, nascita_seconda = prima.anno_nascita, seconda.anno_nascita
    if nascita_prima is not None and nascita_seconda is not None:
        limite = (
            TOLLERANZA_NASCITA_CERTA
            if (prima.nascita_certa is not None and seconda.nascita_certa is not None)
            else tolleranza
        )
        if abs(nascita_prima - nascita_seconda) > limite:
            return False

    if veto_genitori:
        # Genitori dichiarati e diversi: qui dentro sono due persone.
        # E' un veto e non un punteggio perche' sbagliare vuol dire
        # attaccare un ramo all'albero sbagliato — ma vale finche' non
        # arriva una prova di natura diversa, e :mod:`famiglie` ne porta
        # una: che il figlio e il coniuge siano gli stessi.
        if _confronta_insiemi(prima.padri, seconda.padri) < 0:
            return False
        if _confronta_insiemi(prima.madri, seconda.madri) < 0:
            return False

    if not _fertilita_plausibile(prima.parti + seconda.parti, prima.sesso or seconda.sesso):
        return False
    return True


def _morta_prima(morte: int | None, parti: list[int], sesso: str | None) -> bool:
    """Se qualcuno di questi parti viene dopo la morte, cioe' non puo' esserci."""
    if morte is None or not parti:
        return False
    margine = POSTUMO_PADRE if sesso == "M" else 0
    return max(parti) > morte + margine


def _fertilita_plausibile(parti: list[int], sesso: str | None) -> bool:
    """Se quegli anni di nascite stanno nella vita fertile di una persona."""
    if len(parti) < 2:
        return True
    return max(parti) - min(parti) <= ARCO_FERTILE.get(sesso or "M", 60)


def _accostabili(prima: Persona, seconda: Persona) -> bool:
    """Se due persone gia' formate sono la stessa, e c'e' di che dirlo."""
    if not _senza_contraddizioni(prima, seconda):
        return False

    padre = _confronta_insiemi(prima.padri, seconda.padri)
    madre = _confronta_insiemi(prima.madri, seconda.madri)

    if _ha_parentele(prima) or _ha_parentele(seconda):
        # Si muove un ramo dell'albero: serve una prova strutturale.
        if padre > 0 and madre > 0:
            return True
        if (padre > 0 or madre > 0) and _eta_concorde(prima, seconda):
            return True
        if _confronta_insiemi(prima.coniugi, seconda.coniugi) > 0:
            return True
        if prima.uffici & seconda.uffici and _eta_concorde(prima, seconda):
            return True
        return False

    # Nessuna parentela in gioco: unire non sposta nessun ramo.
    if prima.uffici & seconda.uffici:
        return True
    if _eta_concorde(prima, seconda, TOLLERANZA_CONSOLIDAMENTO):
        return True

    # Ne' parentele ne' eta': restano il nome e gli anni in cui le due
    # compaiono. Si uniscono se quegli anni stanno in una vita d'uomo —
    # se no sono due persone, perche' nessuno testimonia per sessant'anni.
    # E' la regola piu' generosa del modulo, e si puo' permettere di
    # esserlo proprio perche' qui non c'e' nessun ramo da sbagliare:
    # cambia solo se due righe stiano in una scheda o in due.
    if prima.anno_nascita is None and seconda.anno_nascita is None:
        anni = [m.anno for m in prima.menzioni] + [m.anno for m in seconda.menzioni]
        return max(anni) - min(anni) <= VITA_PLAUSIBILE
    return False


# Quanti anni possono passare fra la prima e l'ultima volta che una
# persona compare in un registro. Una vita adulta lunga, non una vita
# intera: chi fa da testimone a vent'anni e ricompare a settanta e' un
# caso, chi lo fa per sessant'anni di fila sono due persone con lo
# stesso nome.
VITA_PLAUSIBILE = 50


def _eta_concorde(prima: Persona, seconda: Persona, limite: int = TOLLERANZA_ETA) -> bool:
    uno, altro = prima.anno_nascita, seconda.anno_nascita
    if uno is None or altro is None:
        return False
    return abs(uno - altro) <= limite


def _fondi(
    prima: Persona, seconda: Persona, chiavi: ChiaviFamiliari, incerta: bool = False
) -> None:
    """Versa la seconda persona nella prima e la svuota."""
    da_spostare = list(seconda.menzioni)
    incerte = set(seconda.incerte)
    seconda.menzioni.clear()
    for menzione in da_spostare:
        prima.aggiungi(menzione, chiavi)
        if incerta:
            incerte.add(menzione.id)
    prima.incerte |= incerte


# ---------------------------------------------------------------------------
# Le grafie dello stesso cognome
# ---------------------------------------------------------------------------

# Quanto due cognomi devono somigliarsi per poter essere la stessa
# famiglia scritta in due modi. La soglia e' molto piu' bassa di quella
# usata altrove perche' qui la somiglianza **non porta il peso della
# prova**: quella la porta l'evidenza strutturale — lo stesso coniuge,
# gli stessi genitori, il nome di battesimo che combacia. La somiglianza
# serve solo a escludere due cognomi che non c'entrano niente.
#
# Serve che sia bassa. La moglie di Domenico Lella e' letta 'Di Nardo'
# nel 1821, 'Doro' nel 1823, 'Joro' nel 1824, 'Zoiro' nel 1826, 'Di
# Iorio' nel 1827 e 'Bardo' nel 1841: e' una donna sola, e 'Di Nardo'
# contro 'Doro' sta a 0,53. A 0,62 sarebbe rimasta divisa in sei donne.
SOGLIA_GRAFIA = 0.45

# E questa e' la guardia che conta.
#
# Il problema: 'Salvatore Cabella' e 'Salvatore Colella' compaiono
# tutti e due come marito di Vitaliana Ottaviano, negli stessi anni. La
# lettura giusta e' che siano lo stesso uomo, e la prova c'e' nei dati.
# Ma la stessa forma di ragionamento, applicata senza freni, unirebbe
# 'Colella' e 'Chielli' — che a Torrebruna sono due famiglie diverse,
# e la loro somiglianza (0,57) non e' nemmeno bassa abbastanza da
# fermarla da sola.
#
# Cio' che distingue i due casi non e' quanto le stringhe si somigliano,
# e' **quanto sono attestate**. 'Cabella' ricorre 7 volte accanto alle
# 2.295 di 'Colella': una forma che compare una volta su trecento non e'
# una famiglia del paese, e' una penna che ha sbagliato. 'Chielli'
# ricorre 1.271 volte accanto alle 2.295 di 'Colella': due famiglie
# grandi, e nessuna evidenza strutturale puo' farne una.
#
# E' lo stesso criterio con cui la fase 4 decide le varianti dei cognomi
# — somiglianza piu' dominanza — solo tarato piu' largo, perche' qui c'e'
# in piu' una prova che li' non c'era.
DOMINANZA_GRAFIA = 40


def _unifica_genitori(persone: list[Persona], chiavi: ChiaviFamiliari) -> bool:
    """Nessuno ha due padri: se ne risultano due, e' uno letto due volte.

    E' la deduzione piu' semplice e piu' forte di tutte, e nasce da un
    difetto che si vede a occhio nell'albero: una persona con tre
    genitori. Non e' che i registri ne dichiarino tre — e' che il padre
    e' finito in due schede, e il figlio le conta tutte e due.

    Il rimedio e' rovesciare il ragionamento. Finora due persone si
    univano se avevano gli **stessi genitori**; qui si uniscono se hanno
    lo **stesso figlio** nello stesso ruolo. La prova e' della stessa
    natura e altrettanto buona: gli atti di nascita e di morte dello
    stesso bambino nominano suo padre, e quel padre e' un uomo solo.

    Restano separati solo quando i due nomi non si somigliano affatto —
    li' non c'e' una scheda spezzata, c'e' un contrasto fra due letture
    della stessa pagina, ed e' giusto che si veda.
    """
    per_menzione: dict[int, Persona] = {
        menzione.id: persona
        for persona in persone if persona.menzioni
        for menzione in persona.menzioni
    }

    unito = False
    for persona in persone:
        if not persona.menzioni:
            continue
        for attributo in ("padre", "madre"):
            genitori: list[Persona] = []
            for menzione in persona.menzioni:
                riferimento = getattr(menzione, attributo)
                if riferimento is None:
                    continue
                genitore = per_menzione.get(riferimento)
                if genitore is not None and genitore is not persona:
                    if not any(g is genitore for g in genitori):
                        genitori.append(genitore)
            if len(genitori) < 2:
                continue
            primo = genitori[0]
            for altro in genitori[1:]:
                if not primo.menzioni or not altro.menzioni:
                    continue
                if _stesso_genitore(primo, altro):
                    _fondi(primo, altro, chiavi, incerta=True)
                    unito = True
    return unito


def _stesso_genitore(prima: Persona, seconda: Persona) -> bool:
    """Due schede che fanno da genitore allo stesso figlio, nello stesso ruolo.

    Qui **il nome non e' richiesto**, e questa e' la scelta piu' ardita
    del modulo. Ovunque altrove un nome che discorda ferma tutto; qui no,
    perche' la prova non e' il nome: e' che un figlio ha una madre sola.
    Se due schede sono madre della stessa bambina, una delle due letture
    e' sbagliata — non ci sono due donne.

    Il caso vero: Domenica Pepe nasce nel 1820 e muore due mesi dopo. Sua
    madre e' 'Paola Di Nardo' sull'atto di nascita e 'Piacoma Di Nardo'
    su quello di morte. Non sono due donne, e' una penna che ha letto
    male; e nel resto del secolo la forma attestata e' 'Giacoma'.
    Lasciarle divise dava a quella bambina tre genitori, che e' un dato
    falso e per giunta visibilmente falso.

    Il cognome invece si pretende, perche' e' il cognome a dire la
    famiglia e i registri lo sbagliano molto meno del nome di battesimo.
    Se discordano tutti e due, quello non e' piu' un genitore letto due
    volte: e' un contrasto fra due pagine, e va lasciato in vista.

    L'unione resta segnata come incerta, e nella scheda la forma
    scartata compare fra le grafie alternative: chi consulta vede che li'
    le due pagine dicevano cose diverse.
    """
    if not _senza_contraddizioni(prima, seconda):
        return False
    if not prima._cognomi_canonici or not seconda._cognomi_canonici:
        return True
    return any(
        uno == altro or paleografia.somiglianza(uno, altro) >= SOGLIA_GRAFIA
        for uno in prima._cognomi_canonici
        for altro in seconda._cognomi_canonici
    )


def _ricuci_grafie(
    persone: list[Persona], chiavi: ChiaviFamiliari, frequenze: Counter[str]
) -> bool:
    """Riunisce chi e' lo stesso uomo scritto con due grafie del cognome.

    Il consolidamento normale non puo' arrivarci: confronta solo dentro
    lo stesso cognome, e qui il cognome e' proprio la cosa che differisce.
    Ci si arriva dall'altro capo — dalla moglie, o dai genitori — e si
    accetta solo se la seconda grafia e' cosi' rara, accanto alla prima,
    da non poter essere una famiglia.
    """
    indice: dict[tuple, list[Persona]] = defaultdict(list)
    for persona in persone:
        if not persona.menzioni:
            continue
        parti = {
            paleografia.forma_canonica(parte)
            for forma in persona.nomi
            for parte in nomi.parti_del_nome(forma)
        }
        for nome in parti:
            for coniuge in persona.coniugi:
                indice[(nome, "coniuge", coniuge)].append(persona)
            for padre in persona.padri:
                for madre in persona.madri:
                    indice[(nome, "genitori", padre, madre)].append(persona)

    unito = False
    for gruppo in indice.values():
        if len(gruppo) < 2:
            continue
        for prima in gruppo:
            if not prima.menzioni:
                continue
            for seconda in gruppo:
                if seconda is prima or not seconda.menzioni:
                    continue
                if not _stessa_grafia(prima, seconda, frequenze):
                    continue
                if not _senza_contraddizioni(prima, seconda):
                    continue
                # La forma attestata assorbe quella rara, non viceversa.
                _fondi(prima, seconda, chiavi, incerta=True)
                unito = True
    return unito


def _stessa_grafia(
    prima: Persona, seconda: Persona, frequenze: Counter[str]
) -> bool:
    """Se il cognome della seconda e' una lettura storta di quello della prima."""
    if not prima.cognomi or not seconda.cognomi:
        return False
    uno = prima.cognomi.most_common(1)[0][0]
    altro = seconda.cognomi.most_common(1)[0][0]
    if paleografia.forma_canonica(uno) == paleografia.forma_canonica(altro):
        return False

    quante_uno = frequenze.get(uno, 0)
    quante_altro = frequenze.get(altro, 0)
    if not quante_altro or quante_uno < quante_altro * DOMINANZA_GRAFIA:
        return False
    return paleografia.somiglianza(uno, altro) >= SOGLIA_GRAFIA
