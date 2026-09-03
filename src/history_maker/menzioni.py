"""La lettura delle famiglie dentro gli atti.

Un atto di nascita dice "Maria, figlia di Giuseppe e di Calideta". Non e'
un'inferenza, non e' una probabilita': **e' scritto**. Da ogni atto esce
un pezzo di albero certo, perche' viene dalla fonte, e questo modulo non
fa altro che tirarlo fuori — chi e' il soggetto dell'atto, chi sono i
suoi genitori, chi il coniuge, e quale finestra di anni la sua eta'
dichiarata lascia aperta.

Fin qui **nessuna persona viene riconosciuta**. Due menzioni di
"Domenico Pelliccia" restano due righe, e la domanda se siano lo stesso
uomo non si pone: e' il mestiere di chi viene dopo.

Perche' e' un modulo a se'
--------------------------

Questa parte stava dentro :mod:`history_maker.identita`, insieme al
riconoscimento a pesi fissi che la fase 6b ha sostituito. Erano due cose
molto diverse in un file solo:

* la lettura degli atti, che e' **il pezzo di valore** e che tutt'e due i
  motori usano;
* il riconoscimento a pesi fissi, che il motore nuovo non chiama mai.

Il secondo era morto e teneva in vita il primo. Finche' sono stati
insieme, la fase 6b importava ``identita._chiavi_vicine`` — una funzione
**privata** di un modulo obsoleto — e nessuno poteva toccare la lettura
degli atti senza rischiare di rompere un matcher che non si usa piu'.
Qui quella funzione ha un nome pubblico, ``chiavi_vicine``, perche' e'
parte del confine e non un dettaglio interno.

Ora il confine e' scritto: di qua si legge cio' che l'atto dichiara, di
la' si decide chi e' chi. ``identita`` continua a esporre questi nomi
per chi li importava da li'.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from history_maker import nomi, paleografia


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
SOGLIA_NOME = 0.85
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
def chiavi_vicine(una: str, altra: str) -> bool:
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
