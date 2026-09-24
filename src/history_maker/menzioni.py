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
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from history_maker import nomi, paleografia


def _mese(data: str | None) -> int | None:
    """Il mese di una data d'atto, quando la data e' scritta per intero.

    Le date dell'archivio non sono tutte ISO: qualcuna e' un resto di
    trascrizione ('i ', 'senza data'), e leggerne i caratteri 5 e 6 come
    un numero fermava l'intera ricostruzione. Qui una data che non si
    legge vale come data assente.
    """
    if not data:
        return None
    trovato = re.match(r"^(\d{4})-(\d{2})", str(data))
    if not trovato:
        return None
    mese = int(trovato.group(2))
    return mese if 1 <= mese <= 12 else None


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
    # Vero quando 'padre_che_e_il_nonno' ha riconosciuto che questa
    # riga del «padre» e' in realta' il dichiarante.
    e_il_dichiarante: bool = False
    # Vero quando l'atto dice che questa madre e' vedova: il padre del
    # bambino e' il marito morto, non chi lo denuncia.
    vedova_nell_atto: bool = False

    # L'**evento** a cui la riga appartiene, che non sempre coincide con
    # l'atto. Un matrimonio del formulario napoletano occupa tre pagine e
    # l'estrazione ne fa tre atti; una promessa viene affissa, notificata
    # e trascritta due volte nello stesso registro. In tutti questi casi
    # le stesse persone tornano in righe diverse, e riconoscerle e' cio'
    # che distingue una scheda intera da due mezze schede. Vale l'atto
    # finche' non si trova un gemello: vedi 'atti_gemelli'.
    evento: int = 0
    # L'atto dichiara che questa persona non si sa chi sia: «padre
    # ignoto», «madre ignota», «non nominato in quanto figlio di donna
    # non maritata». Non e' una lettura mancata, e' un dato — e non e'
    # una persona. Vedi 'dichiarata_ignota'.
    ignota: bool = False
    # La riga e' la ripetizione di un'altra riga dello stesso atto: la
    # stessa sposa letta una seconda volta, con i suoi genitori. Resta
    # nell'archivio, ma nella famiglia dell'atto non conta, e la
    # ricostruzione la rimette nella scheda dell'originale. Vedi
    # 'righe_lette_due_volte'.
    doppia_di: int | None = None
    # Il casato di questa riga lo scrive uguale anche un'altra riga dello
    # stesso atto: il figlio e suo padre. Due letture indipendenti della
    # stessa parola, e l'unico cognome di cui ci si possa fidare davvero.
    # Vedi 'casati_confermati'.
    casato_confermato: bool = False

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

    # L'anno in cui questa persona ha avuto un figlio, quando l'atto lo
    # dice **senza essere un atto di nascita**: la madre nominata in un
    # atto di morte ha partorito l'anno in cui il defunto e' nato, e
    # l'eta' del defunto quell'anno lo dice. E' un parto documentato
    # quanto quelli degli atti di nascita, e non contarlo lascia passare
    # fusioni impossibili — la donna nata nel 1851 che risulta madre di
    # un uomo nato nel 1828. Vedi finestre_dai_figli.
    parto_implicito: int | None = None

    def restringi(self, basso: int, alto: int) -> None:
        """Aggiunge un vincolo alla finestra di nascita, intersecando."""
        if self.finestra is None:
            self.finestra = (basso, alto)
            return
        vecchio_basso, vecchio_alto = self.finestra
        self.finestra = (max(vecchio_basso, basso), min(vecchio_alto, alto))

    @property
    def anno_nascita(self) -> int | None:
        """L'anno di nascita ricavato dall'eta' dichiarata nell'atto.

        Nessuno, quando l'eta' e' un **limite** e non una misura:
        «maggiore di eta'» dice che chi firma ha almeno ventun anni, e
        contarli come ventuno inventa una data di nascita. Cinquantacinque
        menzioni la portano, e su una di queste — Angela Felicia Moretta,
        sposa nella promessa del 1863 — la data inventata (1842) litigava
        con quella vera (1815) e teneva la stessa donna divisa in due.
        Il vincolo vero, «nata non dopo il 1842», entra invece nella
        finestra: vedi 'finestre_dalla_maggiore_eta'.

        Sotto l'anno di eta' l'anno dell'atto non basta, e la data si',
        perche' i mesi possono non entrarci: Nicola Troilo, morto di
        cinque mesi il 23 marzo 1854, e' nato nell'ottobre del 1853. Sua
        madre Antonia Ferrara era morta il 21 novembre 1853, e contare
        quel parto nel 1854 la faceva partorire da morta — il veto
        teneva divisa in due la famiglia di Felice Americo Troilo.
        """
        if self.eta is None or self.eta.minima:
            return None
        anno = self.anno - self.eta.anno_nascita
        mesi = round(self.eta.anni * 12)
        if 0 < mesi < 12:
            mese = _mese(self.data)
            if mese is not None and mese <= mesi:
                anno -= 1
        return anno

    @property
    def nascita_certa(self) -> int | None:
        """L'anno di nascita quando l'atto **e'** l'atto di nascita."""
        if self.tipo_atto == "nascita" and self.ruolo in ("neonato", "neonata"):
            return self.anno
        return None

    @property
    def presente(self) -> bool:
        # Il coniuge morto nominato nell'atto di morte — «vedovo di D'Ettore
        # Angela» — non e' li': e' nominato. Contarlo presente lo faceva
        # comparire vivo sedici anni dopo il proprio funerale.
        if self.stato_vitale == "defunto" and self.ruolo in RUOLI_CONIUGE:
            return False
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
            # Lo stesso anno, letto dall'altra parte: per il genitore non
            # e' un vincolo di nascita, e' un parto. Sugli atti di
            # nascita lo si sa gia' dal tipo d'atto; sugli altri no, ed
            # e' li' che serve.
            if figlio.tipo_atto != "nascita":
                genitore.parto_implicito = (
                    anno if genitore.parto_implicito is None
                    else min(genitore.parto_implicito, anno)
                )

    # E chi ha un atto di nascita e' nato quell'anno, senza margini.
    for menzione in menzioni:
        if menzione.nascita_certa is not None:
            menzione.restringi(menzione.nascita_certa, menzione.nascita_certa)
def _nome_dopo_il_ruolo(coda: str) -> bool:
    """Dopo «figlio del defunto» viene un nome? Allora era un patronimico.

    Le due note si scrivono uguali per i primi quattro quinti: «figlio
    del defunto» lega chi parla al morto dell'atto, «figlio del defunto
    Saverio» dice soltanto che suo padre, Saverio, non c'e' piu'. La
    differenza e' la parola dopo, e senza guardarla si inventano figli.
    """
    coda = coda.lstrip(" 	")
    if not coda:
        return False
    if coda[0] in ",;.:)-":
        return False
    prima = coda.split()[0].strip(",;.:")
    # 'di questo atto', 'e di ...': parole di servizio, non nomi.
    if prima.lower() in {"di", "e", "in", "del", "della", "dello", "dei", "qui", "presente", "medesimo", "suddetto", "sopradetto"}:
        return False
    return prima[:1].isupper()


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
        if _nome_dopo_il_ruolo(menzione.note[trovato.end():]):
            # «figlio del defunto Saverio» non dice «figlio del defunto»
            # di quest'atto: dice di chi e' figlio, e il nome che segue e'
            # suo padre — il «defunto» e' il fu, non il morto dell'atto.
            # Nella morte del 1867 (atto 3970) i due dichiaranti, cugini,
            # sono «figlio del defunto Saverio»: presi come figli di
            # Lorenzo Desiderio gli davano un figlio nato nel 1817, due
            # anni dopo di lui, e la sua scheda si spaccava in due a ogni
            # giro — l'atto di morte da una parte e tutta la vita
            # dall'altra.
            continue
        altro = _referente(menzioni, trovato.group("chi"), menzione)
        if altro is None:
            continue

        relazione = trovato.group("rel").lower()
        if relazione == "padre":
            # Nessuno e' padre di se stesso. Dove 'padre_che_e_il_nonno' ha
            # riconosciuto che la riga del «padre» e' il dichiarante, la
            # nota «padre del dichiarante» parla del nome di prima — il
            # nonno — non di questa riga. Applicarla faceva del dichiarante
            # il figlio di se stesso, e allora il veto delle righe legate
            # nello stesso atto teneva le due righe separate per sempre:
            # nella nascita del 1855 (atto 3391) il bambino restava figlio
            # di un Raimondo fantasma, sposato con sua madre, e nemmeno
            # un'unione imposta a mano riusciva a rimetterli insieme.
            # Sono undici atti, tutti fra il 1855 e il 1858.
            if menzione.e_il_dichiarante and altro.ruolo == "dichiarante":
                continue
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
                # Il figlio porta il cognome del padre. Il dichiarante
                # Francesco Pelliccia, «figlio del defunto» Giovanni Pepe, e'
                # un genero o una nota letta male: col padre defunto un
                # cognome diverso non fa un figlio. Con la madre defunta
                # invece il cognome diverso e' la regola, e resta.
                if (menzione.cognome and altro.cognome
                        and not _stesso_casato(menzione.cognome, altro.cognome)):
                    continue
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

    padre = padri[0] if len(padri) == 1 and not padri[0].ignota else None
    madre = madri[0] if len(madri) == 1 and not madri[0].ignota else None

    # Il dichiarante subentra al padre quando la riga 'padre' **manca**,
    # non quando c'e' e dice «ignoto»: li' l'atto ha gia' risposto, e la
    # risposta e' che il padre non si sa. Chi denuncia e' la levatrice o
    # un parente della madre, e farne il padre sarebbe inventare proprio
    # il dato che l'atto si e' preoccupato di negare.
    if not padri and tipo == "nascita":
        # La madre dichiarata ignota non toglie il padre: «dalla sua unione
        # con donna non maritata» e' proprio il padre che parla.
        madre_ignota = len(madri) == 1 and madri[0].ignota
        padre = _padre_dal_dichiarante(menzioni, soggetti, madre, madre_ignota)

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
    menzioni: list[Menzione], soggetti: list[Menzione], madre: Menzione | None,
    madre_ignota: bool = False,
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

    La madre dev'esserci, ma puo' essere dichiarata ignota: negli atti di
    fine secolo il padre denuncia da solo il figlio avuto «dalla sua unione
    con donna non maritata», e la riga della madre e' la formula. Quando
    la formula e' stata riconosciuta, diciannove bambini avevano perso il
    padre che li aveva dichiarati.
    """
    if (madre is None and not madre_ignota) or not soggetti:
        return None
    # La vedova ha gia' un marito, e l'atto dice che e' morto: chi
    # denuncia il bambino e' un parente, non il padre (vedi
    # 'madre_vedova_del_fu').
    if madre is not None and madre.vedova_nell_atto:
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
    sposi = [m for m in menzioni if m.ruolo in ("sposo", "sposa") and m.doppia_di is None]
    genitori = [m for m in menzioni if m.ruolo in ("padre", "madre") and m.doppia_di is None]

    sposo = next((m for m in sposi if m.ruolo == "sposo"), None)
    sposa = next((m for m in sposi if m.ruolo == "sposa"), None)

    if sposo is not None and sposa is not None:
        sposo.coniuge, sposa.coniuge = sposa.id, sposo.id

    if not genitori or (sposo is None and sposa is None):
        return

    attribuzione = _attribuisci_per_posizione(menzioni, sposo, sposa)

    # Il cognome del padre ha l'ultima parola dove parla chiaro.
    per_cognome: set[int] = set()
    for genitore in genitori:
        if genitore.ruolo != "padre":
            continue
        scelto = _sposo_dal_cognome(genitore, sposo, sposa)
        if scelto is not None:
            attribuzione[genitore.id] = scelto
            per_cognome.add(genitore.id)

    # Due padri per lo stesso sposo, uno scelto dal cognome e l'altro dalla
    # sola posizione: il secondo e' dell'altro sposo. Nel matrimonio del 1833
    # di Luigi Bosi e Maria Antonia Di Nardo (atto 1613, sposi scritti prima
    # dei genitori) la meta' della lista dava alla sposa Domenico Pepe, il
    # cognome le dava Nicola Di Nardo, e vinceva l'ultimo: la sposa usciva
    # figlia di Pepe. Trentuno matrimoni con un padre che non combacia con
    # nessuno dei due sposi.
    padri = [g for g in genitori if g.ruolo == "padre"]
    for questo, altro in ((sposo, sposa), (sposa, sposo)):
        if questo is None or altro is None:
            continue
        suoi = [p for p in padri if attribuzione.get(p.id) is questo]
        if (len(suoi) == 2 and sum(p.id in per_cognome for p in suoi) == 1
                and not any(attribuzione.get(p.id) is altro for p in padri)):
            spostato = next(p for p in suoi if p.id not in per_cognome)
            attribuzione[spostato.id] = altro

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
    bilancia: nomi.Bilancia | None = None,
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

        # Il ruolo entra nella correzione, non solo nel sesso: una
        # lettura non puo' essere ricondotta a una forma dell'altro
        # sesso quando l'atto dice gia' chi e' (vedi
        # 'nomi.applica_correzioni').
        nome = nomi.applica_correzioni(nome, correzioni, riga["ruolo"], genere)
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
            via=riga["via_persona"] or _via_della_casa(riga),
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
        # Prima di ogni altra cosa: se l'atto dichiara che questa
        # persona non si sa chi sia, la riga resta nell'archivio ma non
        # diventa nessuno. Va qui e non dopo, perche' tutto quello che
        # segue — la famiglia dentro l'atto, i cognomi derivati, le
        # fusioni — lavora su nome e cognome.
        if dichiarata_ignota(menzione.nome, menzione.cognome, menzione.note):
            menzione.ignota = True
            menzione.nome = None
            menzione.cognome = None
            menzione.cognome_origine = None
        elif _e_la_formula(menzione.cognome) or formula_di_moglie(menzione.cognome):
            # Il nome c'e' e il casato no: si butta la formula, non lei.
            menzione.cognome = None
            menzione.cognome_origine = None
        if interpreta is not None:
            interpreta(menzione)
        if genere is not None:
            menzione.sesso = _sesso(menzione, genere)
        menzioni.append(menzione)

    # Il testo delle nascite, per riconoscere il padre scritto come
    # patronimico del dichiarante (vedi 'padre_che_e_il_nonno'). Solo le
    # nascite: e' li' che il padre dichiara, e il testo di tutto
    # l'archivio in memoria costerebbe per niente.
    testi = {
        riga[0]: riga[1]
        for riga in conn.execute("SELECT id, testo_integrale FROM atti WHERE tipo = 'nascita'")
    }
    # E quello dei matrimoni, per l'avo che presenta i documenti dello sposo.
    testi_dei_matrimoni = {
        riga[0]: riga[1]
        for riga in conn.execute(
            "SELECT id, testo_integrale FROM atti WHERE tipo IN ('matrimonio', 'pubblicazione')")
    }
    # E quello delle morti: il cognome del figlio che l'atto non scrive lo
    # si vede solo sul testo (vedi 'casato_che_l_atto_non_scrive').
    testi_delle_morti = {
        riga[0]: riga[1]
        for riga in conn.execute("SELECT id, testo_integrale FROM atti WHERE tipo = 'morte'")
    }
    for atto, gruppo in _per_atto(menzioni).items():
        nome_dato_dall_atto(gruppo, testi.get(atto), bilancia)
        nome_nella_casella_del_cognome(gruppo, bilancia)
        nome_in_testa_al_cognome(gruppo, bilancia, genere)
        padre_scritto_al_contrario(gruppo, bilancia)
        casato_del_figlio_in_coda(gruppo, genere)
        nome_composto_spezzato(gruppo, bilancia)
        righe_lette_due_volte(gruppo)
        padre_che_e_il_nonno(gruppo, testi.get(atto))
        madre_vedova_del_fu(gruppo, testi.get(atto))
        genitore_che_e_l_avo(gruppo, testi_dei_matrimoni.get(atto))
        genitore_morto_del_matrimonio(gruppo, testi_dei_matrimoni.get(atto), genere)
        coniuge_morto_del_defunto(gruppo, testi_delle_morti.get(atto))
        genitore_che_e_il_coniuge(gruppo, testi_delle_morti.get(atto))
        padre_che_e_la_moglie(gruppo, testi.get(atto))
        genitore_dell_altro_sesso(gruppo, genere)
        famiglia_dell_atto(gruppo)
        casato_che_l_atto_non_scrive(gruppo, testi.get(atto) or testi_delle_morti.get(atto))
        cognome_dal_dichiarante(gruppo)
        cognome_dal_figlio(gruppo)
        cognome_dal_padre(gruppo)
        cognome_della_madre_al_neonato(gruppo)
        casati_confermati(gruppo)

    atti_gemelli(menzioni)
    finestre_dalla_maggiore_eta(menzioni)

    # Dopo, non dentro: la finestra di un genitore si ricava dal figlio,
    # e il figlio lo si sa solo quando la famiglia dell'atto e' letta.
    finestre_dai_figli(menzioni)

    return menzioni
# Lo stesso atto trascritto due volte - nei due registri, o una volta
# sulla sua pagina e una sulla pagina di chiusura dove ne e' rimasta la
# coda - e' letto due volte, e due letture non sono mai identiche lettera
# per lettera. Il confronto esatto delle firme lo perdeva per una vocale:
# la nascita di Rosa Femminilli del 12 maggio 1889 e' scritta nell'atto
# 6947 da «Concetto» e nell'atto 6950 da «Concetta», stessa bambina,
# stessa casa, stessi due testimoni; e la nascita di Carmine Antonio
# Ferrara del dicembre 1826 torna nella pagina che chiude il registro con
# lo stesso dichiarante e il neonato senza cognome. In tutti e due i casi
# restava una firma sola in comune, e il bambino aveva due atti di nascita:
# un veto che nessuna decisione poteva superare.
#
# Quando c'e' una firma identica, la seconda si cerca fra le letture vicine
# - stesso ruolo, nome quasi uguale, cognome quasi uguale o mancante da una
# delle due parti - e deve esserci fra le due la persona di cui l'atto
# parla: il neonato, il defunto, gli sposi. Senza di lei due atti dello
# stesso registro possono avere in comune un dichiarante e un padre, e sono
# due fratelli.
SOMIGLIANZA_FRA_FIRME = 0.9
RUOLI_DI_CUI_L_ATTO_PARLA = frozenset({"neonato", "neonata", "defunto", "defunta", "sposo", "sposa"})


def _gemelli_letti_diversi(una: set, altra: set, esatte: set) -> bool:
    """Due atti con una firma identica e una letta diversa sono lo stesso evento."""
    ruoli = {firma[0] for firma in esatte}
    usate = set()
    vicine = 0
    for mia in sorted(una - esatte):
        for sua in sorted(altra - esatte):
            if sua in usate or mia[0] != sua[0]:
                continue
            if paleografia.somiglianza(mia[1], sua[1]) < SOMIGLIANZA_FRA_FIRME:
                continue
            if mia[2] and sua[2] and paleografia.somiglianza(mia[2], sua[2]) < SOMIGLIANZA_FRA_FIRME:
                continue
            usate.add(sua)
            ruoli.add(mia[0])
            vicine += 1
            break
    return vicine >= 1 and bool(ruoli & RUOLI_DI_CUI_L_ATTO_PARLA)


# Quante persone devono ritornare uguali perche' due atti siano lo
# stesso evento. Due bastano: sono due nomi e due cognomi che tornano
# nello stesso ruolo, ed e' gia' molto piu' di una coincidenza in un
# paese di duemila anime. Uno solo no — l'ufficiale dello stato civile e'
# lo stesso in tutto il registro.
PERSONE_PER_RICONOSCERE_UN_EVENTO = 2

# I ruoli che non contano per il riconoscimento: chi compare in tutti gli
# atti del registro non distingue un atto da un altro.
RUOLI_DI_SERVIZIO = frozenset({"ufficiale", "testimone", "levatrice"})


def atti_gemelli(menzioni: list[Menzione]) -> int:
    """Riconosce gli atti che raccontano lo stesso evento, e li unisce.

    Rende quanti atti sono stati ricondotti a un gemello. Riempie
    ``Menzione.evento``: chi non ha gemelli tiene il proprio atto.

    Succede in due modi, e tutti e due sono formulario, non errore.

    Il primo: **l'atto lungo su piu' pagine.** Il matrimonio del
    formulario napoletano occupa tre facciate — gli sposi, i genitori,
    i testimoni — e la trascrizione, che lavora per pagina, ne fa tre
    atti con lo stesso numero d'ordine.

    Il secondo: **la promessa pubblicata due volte.** Nel registro delle
    notificazioni del 1863 la stessa promessa compare al foglio 4 e al
    foglio 15, con gli stessi quattro genitori. Da li' Angela Felicia
    Moretta, figlia di Carmine e di Maria Lella, era due donne — una per
    ciascuna affissione — e nell'albero di Filippo Lella compariva due
    volte, a ventisette anni di distanza l'una dall'altra.

    Il criterio e' il **contenuto**, non il numero d'ordine: due atti
    dello stesso tipo, dello stesso anno e dello stesso registro in cui
    tornano almeno due persone con lo stesso ruolo e lo stesso nome.
    Chiedere il numero d'ordine non basterebbe e non servirebbe: nel
    registro dei matrimoni del 1832 ci sono due atti diversi numerati
    entrambi «sei», e le loro spose sono due donne diverse.
    """
    for menzione in menzioni:
        menzione.evento = menzione.atto

    firme: dict[int, set] = defaultdict(set)
    # Le stesse firme, ma con cio' che la pagina dice e non con cio' che le
    # regole ne hanno dedotto: il neonato del registro del 1826 porta nel
    # primo atto il cognome della madre, Santoro, e nella pagina di chiusura
    # quello del dichiarante, Ferrara - due cognomi aggiunti dopo, che non
    # si somigliano, su una pagina che il cognome non lo scrive affatto.
    lette: dict[int, set] = defaultdict(set)
    gruppi: dict[tuple, list[int]] = defaultdict(list)
    for atto, righe in _per_atto(menzioni).items():
        prima = righe[0]
        gruppi[(prima.tipo_atto, prima.anno, (prima.immagine or "").split("/")[0])].append(atto)
        for riga in righe:
            if riga.ruolo in RUOLI_DI_SERVIZIO or not riga.nome:
                continue
            firme[atto].add((
                riga.ruolo,
                paleografia.forma_canonica(riga.nome or ""),
                paleografia.forma_canonica(riga.cognome or ""),
            ))
            lette[atto].add((
                riga.ruolo,
                paleografia.forma_canonica(riga.nome or ""),
                paleografia.forma_canonica(riga.cognome_letto or ""),
            ))

    canonico: dict[int, int] = {}
    for atti in gruppi.values():
        if len(atti) < 2:
            continue
        for indice, uno in enumerate(sorted(atti)):
            for altro in sorted(atti)[indice + 1:]:
                if altro in canonico:
                    continue
                comuni = firme[uno] & firme[altro]
                comuni_lette = lette[uno] & lette[altro]
                if len(comuni) >= PERSONE_PER_RICONOSCERE_UN_EVENTO or (
                    len(comuni) == 1 and _gemelli_letti_diversi(firme[uno], firme[altro], comuni)
                ) or (
                    comuni_lette and _gemelli_letti_diversi(lette[uno], lette[altro], comuni_lette)
                ):
                    canonico[altro] = canonico.get(uno, uno)
    if not canonico:
        return 0
    for menzione in menzioni:
        menzione.evento = canonico.get(menzione.atto, menzione.atto)
    return len(canonico)


# I ruoli che l'atto colloca **dentro la casa** di cui scrive
# l'indirizzo. Un atto di nascita dice "nella casa posta in strada Porta
# Murella": quella e' la casa del neonato e dei suoi genitori, non del
# testimone che passava di li'.
RUOLI_DELLA_CASA = frozenset({
    "neonato", "neonata", "defunto", "defunta", "padre", "madre",
})


def _via_della_casa(riga) -> str | None:
    """La via dell'atto, ma solo a chi ci abita.

    La fase 4 registra l'indirizzo sull'**atto**, ed e' giusto: l'atto lo
    scrive una volta sola. Passarlo a tutte le sue righe pero' e' un
    errore, e si vede: Antonio De Plato, pastore, compare da testimone in
    trentun atti e si porta dietro **diciannove contrade** — via
    Vignevecchie, piazza Santa Vittoria, via De Amicis — che sono le case
    dei bambini che ha visto nascere.

    Costa due volte. Sporca la scheda di chi consulta, e sporca il
    confronto: la contrada e' una delle prove con cui si decide se due
    menzioni sono la stessa persona, e un testimone che "abita" ovunque
    somiglia a chiunque.

    Sono 2.491 testimoni e 1.287 ufficiali di stato civile.
    """
    if (riga["tipo"] or "") not in ("nascita", "morte"):
        return None
    if (riga["ruolo"] or "").strip().casefold() not in RUOLI_DELLA_CASA:
        return None
    return riga["via_atto"]


def finestre_dalla_maggiore_eta(menzioni: list[Menzione]) -> int:
    """«Maggiore di eta'» non e' un'eta': e' un limite, e va in finestra.

    Chi il formulario dichiara maggiore di eta' e' nato **non dopo**
    l'anno dell'atto meno ventuno. E' meno di una data e non e' niente:
    esclude i bambini, e sopra non dice nulla — un uomo di sessant'anni
    e' maggiorenne quanto uno di ventidue.
    """
    quante = 0
    for menzione in menzioni:
        if menzione.eta is None or not menzione.eta.minima or not menzione.anno:
            continue
        menzione.restringi(0, menzione.anno - int(menzione.eta.anni))
        quante += 1
    return quante


# Quanto poco una stringa deve 'sapere di cognome' perche' la si consideri
# la seconda meta' di un nome composto e non un casato. Un decimo: sotto
# questa soglia stanno 'Margherita' (tre volte cognome, ventotto nome) e
# 'Maria' (trentuno contro ottocentouno), sopra ci resta ogni famiglia
# vera, anche quelle che portano un nome di battesimo per casato —
# 'Desiderio' e 'Salvatore' vivono onestamente in tutte e due le colonne.
SOGLIA_DEL_NOME_COMPOSTO = 0.10


def nome_composto_spezzato(
    menzioni: list[Menzione], bilancia: "nomi.Bilancia | None"
) -> int:
    """Rimette insieme i nomi composti che l'estrazione ha spaccato in due.

    «Maria Margherita» e' un nome solo. Chi legge la pagina vede due
    parole nella casella del nato e mette la prima in ``nome`` e la
    seconda in ``cognome``: ne esce una bambina di casato Margherita,
    che a Torrebruna non esiste. E il danno non finisce li' — con un
    cognome, per quanto falso, addosso, ``cognome_dal_dichiarante`` non
    interviene piu', e la bambina resta senza il casato **vero**.

    Il caso: la nascita n. 34 del 1850 (atto 3095). Dichiarante Giovanni
    Desiderio, madre Lucia Lella, e in mezzo «Maria Margherita». Tolto il
    finto cognome, il dichiarante le da' il suo: Maria Margherita
    Desiderio, nipote di Domenico Lella.

    Tre condizioni, e servono tutte e tre:

      1. la stringa nel campo cognome, nel resto dell'archivio, e' un
         nome di battesimo e non un casato (``SOGLIA_DEL_NOME_COMPOSTO``);
      2. **anche** quella nel campo nome lo e'. Senza questa si
         rovescerebbero le menzioni invertite invece di ricomporle:
         «Pelliccia Corinto», centosette volte, e' Corinto Pelliccia
         scritto al contrario, non un uomo che si chiama in due modi.
         Quelle le raddrizza ``nomi.inversioni_per_atto``, non questa;
      3. nessun altro nell'atto porta quel cognome. Se il padre e il
         figlio sono tutti e due 'Antonio', il casato in quell'atto si
         comporta da casato, e non lo si tocca.

    Va **prima** di ``famiglia_dell_atto``: la famiglia dentro l'atto si
    legge anche sui cognomi, e un cognome falso ci fa sbagliare i legami
    prima ancora che qualcuno provi a correggerlo.

    Rende quante menzioni ha ricomposto. Modifica sul posto.
    """
    if bilancia is None:
        return 0
    quante_volte: Counter[str] = Counter(
        (m.cognome or "").casefold() for m in menzioni if m.cognome
    )
    quante = 0
    for menzione in menzioni:
        if not menzione.cognome or not menzione.nome:
            continue
        if quante_volte[menzione.cognome.casefold()] > 1:
            continue
        sa_il_cognome = bilancia.sa_di_cognome(menzione.cognome)
        sa_il_nome = bilancia.sa_di_cognome(menzione.nome)
        if sa_il_cognome is None or sa_il_cognome > SOGLIA_DEL_NOME_COMPOSTO:
            continue
        if sa_il_nome is None or sa_il_nome > SOGLIA_DEL_NOME_COMPOSTO:
            continue
        menzione.nome = f"{menzione.nome} {menzione.cognome}"
        menzione.cognome = None
        menzione.cognome_origine = None
        quante += 1
    return quante


# Le formule con cui i registri dicono che un genitore non si sa chi sia.
# Sono ottantasette righe, e non sono un buco nella lettura: sono il
# contrario, la fonte che dichiara. Il codice civile del Regno prevedeva
# il caso e gli dava una formula fissa.
_IGNOTA = re.compile(
    # Anche al plurale: «figlia d'ignoti genitori» (morte del 1837 di
    # Orsola Femminilli) era diventato il padre «ignoti ignoti».
    r"ignot[oaie]"
    r"|s'?ignorano\s+i\s+genitori"
    r"|non\s+nominat[oa]"
    r"|non\s+consente\s+di\s+essere\s+nominata"
    r"|donna\s+che\s+non\s+(?:consente|vuol)"
    # «Dalla sua unione con donna non maritata», la formula dei padri che
    # dichiarano da soli: sei madri dell'archivio si chiamavano cosi'.
    r"|donna\s+non\s+maritata"
    # «figlio naturale» nella nota di un padre senza nome: e' la stessa
    # formula detta dal lato del figlio. Con un nome accanto non conta
    # (vedi sotto), perche' li' racconta il figlio e non nasconde il padre.
    r"|figli[oa]\s+natural[ei]",
    re.IGNORECASE,
)


def dichiarata_ignota(nome, cognome, note) -> bool:
    """L'atto dice che di questa persona non si sa il nome.

    «Padre ignoto» non e' una riga vuota: e' una riga **piena** di un
    dato preciso, e cioe' che il padre non c'e'. Trattarla come una
    persona senza nome costa tre volte:

      1. il cognome le arriva lo stesso, dal figlio o dal formulario, e
         nasce un «Desiderio» senza nome che risulta marito di Teresa
         Desiderio;
      2. quel finto casato la fa somigliare a tutti gli altri di quel
         casato, e la fusione la attacca a un uomo vero — Petronito
         Ottaviano si e' preso quattro «padri ignoti» di quattro atti
         diversi;
      3. il figlio esce con un padre, e non ce l'ha: e' un figlio
         naturale, e quello e' il dato che l'atto voleva dare.

    Si guarda la nota, che e' dove il formulario lo scrive, ma anche il
    nome e il cognome, perche' qualche lettura ci mette dentro la formula
    («Padre ignoto» come nome, «ignota» come cognome).

    Vale solo dove non c'e' anche un nome vero: «Nazario Pelliccia» con
    la nota sulla donna non maritata e' un padre che si e' dichiarato, e
    la nota riguarda la madre, non lui.
    """
    if _e_la_formula(nome):
        return True
    if (nome or "").strip():
        # Un nome vero c'e'. «Donna Filomena, cognome ignota» e' una
        # donna che l'atto nomina a meta': il casato si butta (ci pensa
        # il chiamante), lei no. E «Enrico Pelliccia, non nominato in
        # quanto figlio naturale» ha nome e casato: quella formula
        # racconta la condizione del figlio, non nasconde lui.
        return False
    if _e_la_formula(cognome):
        return True
    if (cognome or "").strip():
        return False
    return bool(note and _IGNOTA.search(note))


_FORMULA_DI_MOGLIE = re.compile(r"(?:sua\s+)?moglie\b")


def formula_di_moglie(cognome: str | None) -> bool:
    """Il casato e' la formula dell'atto: «sua moglie», «Moglie», «sua moglie legittima».

    Il caso: nove madri dell'archivio col cognome «sua moglie» — «Maria
    Lella, sua moglie», «Maria Felicia, sua moglie» — e la moglie di Michele
    Torzi del 1858 con «sua moglie legittima». Tutte le Marie di casato «sua
    moglie» sembravano della stessa famiglia. Non e' una persona ignota: e'
    una donna con la formula al posto del casato, e si butta solo la
    formula (non va in :func:`_e_la_formula`, che dichiara ignota la riga).
    """
    return bool(cognome) and _FORMULA_DI_MOGLIE.match(cognome.strip().casefold()) is not None


def _e_la_formula(valore: str | None) -> bool:
    """La cella contiene la formula e nient'altro: 'ignoto', 'Padre ignoto'."""
    if not valore:
        return False
    pulito = valore.strip().casefold()
    for premessa in ("padre ", "madre ", "di padre ", "di madre "):
        if pulito.startswith(premessa):
            pulito = pulito[len(premessa):]
    pulito = pulito.strip()
    # La formula della donna che non si nomina arriva intera nella casella
    # del nome, con tutto il suo giro di parole: «Donna che non consente
    # l'esposizione nominata», «dalla sua unione illegittima con donna che
    # non vuol consentire...». Quando la casella comincia cosi', basta
    # trovarci la formula; «sua unione naturale con Celeste» invece la
    # donna la nomina, e resta una persona.
    if pulito.startswith(("donna ", "dalla sua unione", "sua unione")):
        return bool(_IGNOTA.search(pulito))
    return bool(_IGNOTA.fullmatch(pulito))


# Quanto devono somigliarsi due nomi di battesimo, nella stessa casella
# di un atto ripetuto, perche' siano la stessa lettura. Per gli sposi il
# primo nome uguale basta; per i genitori, che in un segmento ripetuto
# stanno esattamente allo stesso posto, basta molto meno: «Orsodio» e
# «Arcadio» sono la stessa parola della stessa mano.
SOMIGLIANZA_DELLO_SPOSO_RILETTO = 0.80
SOMIGLIANZA_DEL_GENITORE_RILETTO = 0.70
ANNI_FRA_DUE_LETTURE = 3


def _riletta(una: Menzione, altra: Menzione, soglia: float) -> bool:
    """Due righe che possono essere la stessa persona letta due volte."""
    if (
        una.anno_nascita is not None and altra.anno_nascita is not None
        and abs(una.anno_nascita - altra.anno_nascita) > ANNI_FRA_DUE_LETTURE
    ):
        return False
    mio = paleografia.normalizza(una.nome or "")
    suo = paleografia.normalizza(altra.nome or "")
    if mio and suo:
        if mio.split()[0] == suo.split()[0]:
            return True
        if paleografia.somiglianza(mio, suo) >= soglia:
            return True
    return bool(una.chiave_cognome) and una.chiave_cognome == altra.chiave_cognome


def _genitori_del_segmento(menzioni: list[Menzione], riga: Menzione) -> dict[str, Menzione]:
    """Il padre e la madre scritti subito dopo uno sposo, prima del prossimo."""
    trovati: dict[str, Menzione] = {}
    dopo = False
    for menzione in menzioni:
        if menzione is riga:
            dopo = True
            continue
        if not dopo:
            continue
        if menzione.ruolo in ("sposo", "sposa"):
            break
        if menzione.ruolo in ("padre", "madre"):
            trovati.setdefault(menzione.ruolo, menzione)
    return trovati


def righe_lette_due_volte(menzioni: list[Menzione]) -> int:
    """Un atto di matrimonio sposa una coppia sola.

    Il matrimonio n. 11 del 1834 ha una sposa sola — «Maria Fiorenza
    Moretta, figlia di Arcadio Moretta e di Felicia Desiderio» — e
    l'estrazione ne ha fatte due: «Maria Fiorenza Daba», figlia di
    «Orsodio Daba», e «Maria Fiorentina Deba Montra», figlia di «Arcadio
    Montra». La madre era identica nelle due righe. Ne erano uscite due
    donne e due padri, e nessun confronto poteva rimetterli insieme: i
    candidati si scelgono per somiglianza di cognome, e 'Daba' e
    'Montra' non si somigliano.

    Qui non serve la somiglianza, serve la struttura. In un atto di
    matrimonio o di pubblicazione c'e' uno sposo e c'e' una sposa: una
    seconda riga con lo stesso ruolo e' la stessa persona letta di nuovo,
    e i genitori scritti accanto a lei sono i genitori di prima letti di
    nuovo. Le prudenze sono due, contro la pagina che contiene due atti
    divisi male: il nome di battesimo deve poter essere lo stesso, e le
    eta', quando ci sono tutte e due, non devono distare piu' di tre anni.

    Marca le ripetizioni e rende quante ne ha trovate. Modifica sul posto.
    """
    if not menzioni or menzioni[0].tipo_atto not in ("matrimonio", "pubblicazione"):
        return 0
    marcate = 0
    for ruolo in ("sposo", "sposa"):
        righe = [m for m in menzioni if m.ruolo == ruolo and m.doppia_di is None]
        if len(righe) < 2:
            continue
        prima = righe[0]
        suoi = _genitori_del_segmento(menzioni, prima)
        for altra in righe[1:]:
            if not _riletta(prima, altra, SOMIGLIANZA_DELLO_SPOSO_RILETTO):
                continue
            altra.doppia_di = prima.id
            marcate += 1
            for chi, genitore in _genitori_del_segmento(menzioni, altra).items():
                originale = suoi.get(chi)
                if originale is not None and _riletta(
                    originale, genitore, SOMIGLIANZA_DEL_GENITORE_RILETTO
                ):
                    genitore.doppia_di = originale.id
                    marcate += 1
    return marcate


def nome_nella_casella_del_cognome(
    menzioni: list[Menzione], bilancia: "nomi.Bilancia | None"
) -> int:
    """Il nome di battesimo finito nella casella del cognome.

    Il rovescio di ``nome_composto_spezzato``: li' il nome era troppo
    lungo e ne era uscito un pezzo, qui la casella del nome e' vuota e
    tutto e' finito nell'altra. Quattro righe nell'archivio — «Michele
    Lella» e «Luciano Cicchillitti» padri, «Di Luca Concetta» e «Di Mucci
    Annarosa» madri, queste ultime scritte alla maniera del 1895, casato
    prima — e tre di loro risultavano schede senza nome sposate a qualcuno.

    Le parole che nell'archivio sono nomi di battesimo vanno nel nome, le
    altre restano nel casato. Una parola dopo una particella fa parte del
    casato qualunque cosa sia: 'Luca' e' un nome, ma in «Di Luca» e' un
    cognome.

    Rende quante menzioni ha rimesso a posto. Modifica sul posto.
    """
    if bilancia is None:
        return 0
    quante = 0
    for menzione in menzioni:
        if (menzione.nome or "").strip() or not menzione.cognome:
            continue
        parole = menzione.cognome.split()
        if len(parole) < 2:
            continue
        nome, casato = [], []
        dopo_particella = False
        for parola in parole:
            if paleografia.normalizza(parola.strip("'")) in nomi.PARTICELLE:
                casato.append(parola)
                dopo_particella = True
                continue
            sa = bilancia.sa_di_cognome(parola)
            if not dopo_particella and sa is not None and sa <= SOGLIA_DEL_NOME_COMPOSTO:
                nome.append(parola)
            else:
                casato.append(parola)
            dopo_particella = False
        if not nome or not casato:
            continue
        menzione.nome = " ".join(nome)
        menzione.cognome = " ".join(casato)
        quante += 1
    return quante


# I nomi di donna che chiudono un nome d'uomo: Angelo Maria, Giuseppe Maria.
NOMI_IN_CODA_A_UN_NOME_D_UOMO = frozenset({"maria"})


def nome_in_testa_al_cognome(
    menzioni: list[Menzione], bilancia: "nomi.Bilancia | None", genere=None
) -> int:
    """La seconda parte di un nome composto finita davanti al cognome.

    Il fratello di ``nome_nella_casella_del_cognome``: li' la casella del
    nome era vuota, qui c'e' la prima parte del nome e la seconda e'
    scivolata nell'altra. «Figlia del fu Simone Giovanni Franchella»
    (matrimonio n. 4 del 1830) diventa il padre Simome, di casato
    «Giovanni Franchella»; e cosi' «Antonio | Fedele Tilli», «Nicola |
    Felice Petta». Il casato con un nome davanti non somiglia a quello
    del figlio, e la figlia risulta di un'altra famiglia.

    Le parole in testa che nell'archivio sono nomi di battesimo passano
    al nome; il resto deve restare, e non essere un nome anch'esso.
    Tre contrappesi:

      1. una particella chiude la testa: «Di Luca» e' un casato;
      2. se un altro nell'atto porta lo stesso casato di due parole, in
         quell'atto si comporta da casato, e non lo si tocca;
      3. se la stringa intera e' un casato conosciuto, resta com'e'.

    Con ``genere``, una parola in testa dell'altro sesso non e' la
    seconda meta' del nome:

      - in una donna e' il nome del padre. «Maria | Fioravante
        Cicchillitti» e' Maria di Fioravante Cicchillitti: Fioravante va
        nel patronimico, e il casato resta Cicchillitti;
      - in un padre e' la moglie. «figlio del fu Mauro e Chiara Derudi»
        (morte n. 23 del 1832) aveva dato il padre Mauro di casato
        «Chiara Derudi»: il casato gli si toglie, e glielo da' il figlio
        (``cognome_dal_figlio``).

    'Maria' fa eccezione: dopo un nome d'uomo e' la seconda meta' del
    nome — Angelo Maria, Giuseppe Maria — e ci si comporta come per ogni
    nome composto. Se e' il nome stesso a essere dell'altro sesso, la
    riga e' di un'altra persona e qui non si tocca.

    Rende quante menzioni ha rimesso a posto. Modifica sul posto.
    """
    if bilancia is None:
        return 0
    quante_volte: Counter[str] = Counter(
        (m.cognome or "").casefold() for m in menzioni if m.cognome
    )
    quante = 0
    for menzione in menzioni:
        if not (menzione.nome or "").strip() or not menzione.cognome:
            continue
        parole = menzione.cognome.split()
        if len(parole) < 2 or quante_volte[menzione.cognome.casefold()] > 1:
            continue
        intero = bilancia.sa_di_cognome(menzione.cognome)
        if intero is not None and intero > SOGLIA_DEL_NOME_COMPOSTO:
            continue
        testa = []
        for parola in parole[:-1]:
            if paleografia.normalizza(parola.strip("'")) in nomi.PARTICELLE:
                break
            sa = bilancia.sa_di_cognome(parola)
            if sa is None or sa > SOGLIA_DEL_NOME_COMPOSTO:
                break
            testa.append(parola)
        if not testa:
            continue
        casato = " ".join(parole[len(testa):])
        resto = bilancia.sa_di_cognome(casato)
        if resto is not None and resto <= SOGLIA_DEL_NOME_COMPOSTO:
            continue
        if genere is not None and menzione.sesso in ("M", "F"):
            if _sesso_netto(genere, menzione.nome) not in (None, menzione.sesso):
                continue
            della_testa = _sesso_netto(genere, testa[0])
            if (della_testa not in (None, menzione.sesso)
                    and nomi._chiave(testa[0]) not in NOMI_IN_CODA_A_UN_NOME_D_UOMO):
                if menzione.sesso == "F":
                    menzione.patronimico = menzione.patronimico or " ".join(testa)
                    menzione.cognome = casato
                elif menzione.ruolo == "padre":
                    menzione.cognome = None
                    menzione.cognome_origine = None
                else:
                    continue
                quante += 1
                continue
        menzione.nome = f"{menzione.nome} {' '.join(testa)}"
        menzione.cognome = casato
        quante += 1
    return quante


# Quanto devono somigliarsi la parola del padre e il casato del figlio per
# leggerle come lo stesso casato: la soglia delle varianti di lettura.
SOMIGLIANZA_DEL_CASATO_DEL_FIGLIO = 0.80
RUOLI_DEI_FIGLI = frozenset({"neonato", "neonata", "defunto", "defunta", "sposo", "sposa"})


def _stesso_casato(uno: str, altro: str) -> bool:
    uno, altro = paleografia.forma_canonica(uno), paleografia.forma_canonica(altro)
    return uno == altro or paleografia.somiglianza(uno, altro) >= SOMIGLIANZA_DEL_CASATO_DEL_FIGLIO


def _casati_dei_figli(menzioni: list[Menzione]) -> list[str]:
    return [m.cognome for m in menzioni if m.ruolo in RUOLI_DEI_FIGLI and m.cognome]


def padre_scritto_al_contrario(
    menzioni: list[Menzione], bilancia: "nomi.Bilancia | None"
) -> int:
    """Il padre scritto col casato davanti, solo lui nel suo atto.

    «Di Bello Sabatino» e' diventato il padre di nome 'Di Bello' e di
    casato 'Sabatino', e la neonata Angela Di Bello figlia di un'altra
    famiglia. Quattordici padri cosi' nell'archivio, quasi tutti nelle
    nascite di fine secolo. ``nomi.inversioni_per_atto`` raddrizza gli
    atti in cui le righe al contrario sono almeno due; qui la riga e'
    una, e a dirlo e' il figlio: il nome del padre e' il suo casato, e il
    cognome del padre comincia con un nome di battesimo.

    Il contrappeso: se il cognome del padre e' gia' il casato del figlio,
    o e' un casato e non un nome, la riga resta com'e'.

    Rende quante menzioni ha raddrizzato. Modifica sul posto.
    """
    if bilancia is None:
        return 0
    casati = _casati_dei_figli(menzioni)
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo != "padre" or not menzione.nome or not menzione.cognome:
            continue
        if any(_stesso_casato(menzione.cognome, casato) for casato in casati):
            continue
        if not any(_stesso_casato(menzione.nome, casato) for casato in casati):
            continue
        sa = bilancia.sa_di_cognome(menzione.cognome.split()[0])
        if sa is None or sa > SOGLIA_DEL_NOME_COMPOSTO:
            continue
        menzione.nome, menzione.cognome = menzione.cognome, menzione.nome
        menzione.invertita = True
        quante += 1
    return quante


def casato_del_figlio_in_coda(menzioni: list[Menzione], genere=None) -> int:
    """Il cognome del padre che finisce col casato del figlio.

    La sposa Rosa Lattanzio e il padre «Tommaso | Remigio Lattanzi»:
    'Remigio' e' troppo raro nell'archivio perche'
    ``nome_in_testa_al_cognome`` lo riconosca come nome, ma il figlio dice
    dove comincia il casato. La testa torna al nome; e se comincia con
    una particella — «Domenico | Di Vito Lella», il padre del neonato
    Vito Antonio Lella — e' il nome del nonno, e va nel patronimico.

    I contrappesi: un «padre» con un nome di donna e' un'altra persona,
    e lo guarda ``genitore_dell_altro_sesso``; un cognome con le
    alternative («D'Amico / Desiderio») e' una lettura in dubbio, non un
    casato di due parole.

    Rende quante menzioni ha rimesso a posto. Modifica sul posto.
    """
    casati = _casati_dei_figli(menzioni)
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo != "padre" or not menzione.nome or not menzione.cognome:
            continue
        parole = menzione.cognome.split()
        if len(parole) < 2 or "/" in menzione.cognome:
            continue
        if any(_stesso_casato(menzione.cognome, casato) for casato in casati):
            continue
        if genere is not None and _sesso_netto(genere, menzione.nome) == "F":
            continue
        particelle = [paleografia.normalizza(p.strip("'")) in nomi.PARTICELLE for p in parole]
        taglio = next(
            (k for k in range(1, len(parole))
             if not particelle[k - 1]
             and any(_stesso_casato(" ".join(parole[k:]), casato) for casato in casati)),
            None,
        )
        if taglio is None:
            continue
        testa = parole[:taglio]
        if particelle[0] and len(testa) >= 2 and not any(particelle[1:taglio]):
            menzione.patronimico = menzione.patronimico or " ".join(testa[1:])
        elif any(particelle[:taglio]):
            continue
        else:
            menzione.nome = f"{menzione.nome} {' '.join(testa)}"
        menzione.cognome = " ".join(parole[taglio:])
        quante += 1
    return quante


# La formula con cui la nascita dice il nome dato al bambino: «a cui si
# e' dato il nome di Amato Felice», «di dare alla neonata il nome di Maria
# Nicola», «se li sono imposti i seguenti nomi: Giuseppe Antonio».
_NOME_DATO = re.compile(
    r"(?:dat[oa]|dare|d[àa]|impost[oi]|post[oa])\b[^.;]{0,40}?\bnom[ei]\s+(?:di\s+)?:?\s*"
    r"([A-Z][a-zàèéìòù']+(?:\s+[A-Z][a-zàèéìòù']+){0,3})"
)
# Sopra questa quota la parola nella casella del cognome e' un casato
# vero, anche se l'atto la scrive accanto al nome: «il nome di Ferdinando
# Maria Pepe» porta il cognome dentro la formula.
SA_DI_UN_CASATO = 0.50


def nome_dato_dall_atto(
    menzioni: list[Menzione], testo: str | None, bilancia: "nomi.Bilancia | None"
) -> int:
    """Il secondo nome del neonato finito nella casella del cognome.

    La nascita del 1819 di Amato Felice: «a cui si e' dato il nome di Amato
    Felice». La trascrizione ha messo Amato nel nome e Felice nel cognome,
    e il bambino risultava un Felice di padre Desiderio. Ventisei neonati
    cosi' nell'archivio, quasi tutti del 1819-1820: «Nicola | Maria»,
    «Giovanni | Domenico», «Angela | Maria».

    ``nome_composto_spezzato`` non li prende quando la seconda parola e'
    anche un casato del paese — Felice lo e' — perche' senza altro da
    guardare non si puo' dire quale dei due sia. Qui c'e' l'atto a dirlo:
    se il nome e il cognome letti, messi in fila, sono esattamente il nome
    dato dalla formula, sono un nome solo, e il casato gli verra' dal padre.

    Il contrappeso: la parola nel cognome dev'essere, nell'archivio, un
    nome di battesimo piu' che un casato (:data:`SA_DI_UN_CASATO`). Dagli
    anni Quaranta la formula scrive il nome col cognome — «il nome di
    Maria Puntone», «di Maria Nunzia Ammirati» — e un casato raro, che
    l'archivio non sa dire, resta un casato.

    Quel contrappeso pero' si scavalca quando l'atto **nomina il padre**
    e il suo casato non e' quello scritto nella casella: nell'Ottocento
    il figlio porta il cognome del padre, e tenere «Amabile» per casato
    di una figlia di Ferdinando Lozzi vorrebbe dire darle un cognome che
    non e' del padre. Sono trentacinque neonati — «Giuseppe | Nicola» di
    padre D'Ettore, «Angela | Maria» di padre Pepe, «Maria Rosa |
    Amabile» di padre Lozzi — e in tutti la parola e' un secondo nome di
    battesimo. Dove invece la formula porta davvero il casato — «il nome
    di Ferdinando Maria Pepe», con il padre Pepe — i due coincidono e il
    contrappeso vale come prima.

    Rende quante menzioni ha ricomposto. Modifica sul posto.
    """
    if not testo or bilancia is None:
        return 0
    dati = {trovato.group(1).casefold() for trovato in _NOME_DATO.finditer(testo)}
    if not dati:
        return 0
    casati_del_padre = {
        paleografia.normalizza(m.cognome)
        for m in menzioni if m.ruolo == "padre" and m.cognome
    }
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("neonato", "neonata") or not menzione.nome or not menzione.cognome:
            continue
        if f"{menzione.nome} {menzione.cognome}".casefold() not in dati:
            continue
        del_padre = paleografia.normalizza(menzione.cognome) in casati_del_padre
        sa = bilancia.sa_di_cognome(menzione.cognome)
        if del_padre or not casati_del_padre:
            if sa is None or sa > SA_DI_UN_CASATO:
                continue
        menzione.nome = f"{menzione.nome} {menzione.cognome}"
        menzione.cognome = None
        menzione.cognome_origine = None
        quante += 1
    return quante


def genitore_che_e_l_avo(menzioni: list[Menzione], testo: str | None) -> int:
    """Nel matrimonio lo sposo privo di genitori porta i documenti dell'avo.

    Il matrimonio del 1831 di Erminia Franchella: «la sposa similmente
    priva di genitori, correda documenti... dell'avo paterno Giovanni
    Franchella d'anni settantanove». La trascrizione ha messo Giovanni fra
    i padri, e la sposa risultava figlia del nonno. Undici righe cosi' nei
    matrimoni dal 1831 al 1850.

    La riga che il testo nomina subito dopo «avo paterno» (o «materno»)
    prende quel ruolo e smette di essere un genitore. Il contrappeso: se il
    testo scrive lo stesso nome anche come genitore dello sposo — «figlio
    del fu Nicola Ottaviano», col primogenito che porta il nome del nonno —
    non si sa quale dei due sia la riga, e resta com'e'.

    Rende quante righe ha cambiato. Modifica sul posto.
    """
    if not testo:
        return 0
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("padre", "madre") or not menzione.nome:
            continue
        nome = re.escape(menzione.nome)
        avo = re.search(
            r"\bav[oa]\s+(paterno|materno|paterna|materna)?\s*"
            r"(?:del(?:la|lo)?\s+(?:sposo|sposa)\s+)?(?:fu\s+)?" + nome + r"\b",
            testo, re.IGNORECASE,
        )
        if not avo:
            continue
        if re.search(r"\bfigli[oa]\s+(?:del(?:li)?\s+fu\s+|di\s+fu\s+|del\s+|di\s+)" + nome + r"\b",
                     testo, re.IGNORECASE):
            continue
        lato = (avo.group(1) or "paterno").casefold()
        menzione.ruolo = "avo materno" if lato.startswith("matern") else "avo paterno"
        quante += 1
    return quante


def cognome_dal_figlio(menzioni: list[Menzione]) -> int:
    """Il cognome del padre quando l'atto non lo scrive, preso dal figlio.

    E' il rovescio di ``dataset._deriva_cognome``, che da' al figlio il
    cognome del padre: dentro un atto i due lo condividono, quindi la
    deduzione vale nei due versi, e l'unica differenza e' quale delle due
    righe l'ha per esteso.

    Il caso: l'atto 6258 del 1884 dichiara la morte di **Stella
    Colella** e ne nomina il padre «Nicola Maria», senza cognome. Nella
    scheda restava un «Nicola Maria» senza casato, appeso all'albero per
    un filo. E' Nicola Maria Colella.

    Non tocca la madre: nei registri porta il cognome da nubile, che con
    quello del figlio non c'entra.

    Rende quanti cognomi ha derivato. Modifica sul posto.
    """
    figli = [
        m for m in menzioni
        if m.ruolo in ("neonato", "neonata", "defunto", "defunta", "sposo", "sposa")
        and m.cognome
    ]
    if len(figli) != 1:
        return 0
    quanti = 0
    for menzione in menzioni:
        if menzione.ruolo != "padre" or menzione.cognome or menzione.ignota:
            continue
        # Solo a chi un nome di battesimo ce l'ha. Una riga senza nome e
        # senza cognome non e' un uomo di cui manca il casato: e' nessuno,
        # e dargli il cognome del figlio lo faceva diventare qualcuno — un
        # «Cannavina» senza nome, marito della madre, in quattro atti.
        if not (menzione.nome or "").strip():
            continue
        menzione.cognome = figli[0].cognome
        menzione.cognome_origine = "figlio"
        quanti += 1
    return quanti


# Chi porta il casato del padre scritto nella stessa riga dell'atto: il
# soggetto, cioe' il nato, il defunto, lo sposo e la sposa.
SOGGETTI_COL_PADRE = frozenset({"neonato", "neonata", "defunto", "defunta", "sposo", "sposa"})


def cognome_dal_padre(menzioni: list[Menzione]) -> int:
    """Il cognome del soggetto quando l'atto scrive il padre ma non lui.

    Il caso: la nascita n. 9 del 1819 (atto 713). Il neonato e' letto
    «Giuseppe» «Nicola»; ``nome_composto_spezzato`` lo ricompone in
    Giuseppe Nicola, e il finto cognome se ne va. Il padre c'e' —
    Domenico di Vito Lella — ma ``cognome_dal_dichiarante`` non
    interviene quando c'e' una riga di padre, perche' il cognome l'ha
    gia' dato la fase 4: e la fase 4 aveva dato quello finto. Il bambino
    restava senza casato, e senza casato si attaccava a chiunque avesse
    il suo nome e il suo anno: era finito dentro Giuseppe Franchini.
    Diciannove bambini cosi', quasi tutti del registro del 1819.

    Un figlio legittimo porta il casato del padre; una donna, anche
    sposata, nei registri porta quello di suo padre. Non si tocca chi e'
    dichiarato ignoto, ne' chi un cognome ce l'ha gia'.

    Rende quanti cognomi ha derivato. Modifica sul posto.
    """
    per_id = {m.id: m for m in menzioni}
    quanti = 0
    for menzione in menzioni:
        if menzione.cognome or menzione.ignota or menzione.padre is None:
            continue
        if menzione.ruolo not in SOGGETTI_COL_PADRE:
            continue
        padre = per_id.get(menzione.padre)
        if padre is None or padre.ignota or not padre.cognome:
            continue
        menzione.cognome = padre.cognome
        menzione.cognome_origine = "padre"
        quanti += 1
    return quanti


# Il ruolo che prende una riga di genitore che non si sa di chi sia: non
# padre, non madre. Nessun legame di famiglia la tocca.
GENITORE_DUBBIO = "genitore dubbio"

# Quante volte, e quanto piu' dell'altro sesso, un nome deve comparire
# nell'archivio perche' basti a smentire il ruolo scritto. ``Genere.di``
# qui non serve: quando il conto e' pari ricade sulla desinenza, e la
# desinenza da sola non puo' dire che una «madre» e' un uomo.
VOLTE_DI_UN_NOME_NETTO = 5
PREVALENZA_DI_UN_NOME_NETTO = 10


def _sesso_netto(genere, nome: str | None) -> str | None:
    """Il sesso del primo nome, solo quando l'archivio lo dice senza dubbi."""
    parti = nomi.parti_del_nome(nome)
    if not parti:
        return None
    chiave = nomi._chiave(parti[0])
    maschi, femmine = genere.maschili.get(chiave, 0), genere.femminili.get(chiave, 0)
    if femmine >= VOLTE_DI_UN_NOME_NETTO and femmine >= PREVALENZA_DI_UN_NOME_NETTO * maschi:
        return "F"
    if maschi >= VOLTE_DI_UN_NOME_NETTO and maschi >= PREVALENZA_DI_UN_NOME_NETTO * femmine:
        return "M"
    return None


def coniuge_morto_del_defunto(menzioni: list[Menzione], testo: str | None) -> int:
    """Nella morte, il coniuge di cui il defunto «e' vedovo» e' morto anche lui.

    Il caso: la morte del 1881 di Carmine Pizzi, «vedovo di D'Ettore
    Angela». Angela era morta nel 1865, ma la trascrizione la metteva
    nella casella della sposa come viva, e l'albero la faceva comparire
    viva sedici anni dopo il suo funerale: l'unione con la sua scheda era
    rifiutata. Negli atti di morte sono una quindicina di coniugi «di cui
    e' vedovo», e tre restavano vivi.

    Il nome del coniuge deve stare dopo il «vedovo di», anche dopo il
    cognome (negli atti del 1880-1900 il cognome viene prima). Rende
    quanti coniugi ha segnato morti. Modifica sul posto.
    """
    if not testo or not menzioni or menzioni[0].tipo_atto != "morte":
        return 0
    quante = 0
    for menzione in menzioni:
        if (menzione.ruolo not in RUOLI_CONIUGE or not menzione.nome
                or menzione.stato_vitale == "defunto"):
            continue
        if nominato_vedovo(menzione.nome, testo):
            menzione.stato_vitale = "defunto"
            quante += 1
    return quante


def nominato_vedovo(nome: str | None, testo: str | None) -> bool:
    """Se il testo dice il defunto «vedovo di» questa persona.

    Il nome deve stare dopo il «vedovo di», anche dopo il cognome: negli
    atti del 1880-1900 il cognome viene prima («vedovo di D'Ettore
    Angela»). Lo usano la lettura (:func:`coniuge_morto_del_defunto`) e il
    controllo di qualita' degli atti dopo la morte: una definizione sola.
    """
    if not nome or not testo:
        return False
    primo = paleografia.normalizza(nome).split()[0]
    return re.search(
        r"\bvedov[oa]\s+(?:del(?:la)?|di|dal)\s+(?:fu\s+)?[^,.;]{0,30}?\b"
        + re.escape(primo) + r"\b", paleografia.normalizza(testo)
    ) is not None


def genitore_che_e_il_coniuge(menzioni: list[Menzione], testo: str | None) -> int:
    """Nella morte, il «genitore» che l'atto dice coniuge del defunto.

    Il caso: la morte del 1870 di Antonia Felice, «figlia della defunta
    Maria Felice, di professione contadina, domiciliata in Celenza sul
    Trigno, e padre ignoto, vedova del defunto Carmine Finarelli». La
    trascrizione ha messo Carmine Finarelli nella casella del padre:
    Antonia diventava figlia di suo marito, e il suo cognome — che e'
    quello della madre, perche' il padre l'atto lo dichiara ignoto —
    risultava diverso da quello del padre. Nell'archivio sono tre atti su
    trecentosedici che dicono «vedov* di».

    Piu' stretta di :func:`coniuge_morto_del_defunto`, che guarda le
    righe gia' messe fra i coniugi: qui si sposta un **ruolo**, e per
    farlo si chiede che nella frase del «vedovo di» ci siano tutt'e due
    le parole della riga, il nome di battesimo e il casato. Un padre che
    per caso si chiama come il marito morto non basta. Rende quante righe
    ha spostato. Modifica sul posto.
    """
    if not testo or not menzioni or menzioni[0].tipo_atto != "morte":
        return 0
    frasi = [
        f.group(1) for f in _VEDOVO_DI.finditer(paleografia.normalizza(testo))
    ]
    if not frasi:
        return 0
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("padre", "madre") or not menzione.nome or not menzione.cognome:
            continue
        nome = paleografia.normalizza(menzione.nome).split()[0]
        cognome = paleografia.normalizza(menzione.cognome)
        if any(nome in frase and cognome in frase for frase in frasi):
            menzione.ruolo = "coniuge"
            quante += 1
    return quante


# La frase che nomina il coniuge morto, nei due ordini che i registri
# usano: «vedova del defunto Carmine Finarelli» e «vedovo di D'Ettore
# Angela». Si ferma alla punteggiatura, perche' oltre comincia un'altra
# persona.
_VEDOVO_DI = re.compile(r"\bvedov[oa]\s+(?:del(?:la)?|di|dal)\s+([^,.;]{0,40})")


def genitore_morto_del_matrimonio(
    menzioni: list[Menzione], testo: str | None, genere=None
) -> int:
    """Nel matrimonio il padre morto dello sposo, trascritto come «defunto».

    Il caso: il matrimonio del 1811 di Felice Lella e Maria Catarina
    Pelliccia, «figlia del fu Prospero Pelliccia, e Giuseppa Serafini». La
    trascrizione ha messo Prospero nella casella del defunto, come se
    l'atto fosse la sua morte, e la sposa restava senza padre — o prendeva
    quello dello sposo. Nei matrimoni dell'archivio le righe «defunto» sono
    centocinquanta, e piu' di cento portano il cognome di uno degli sposi.

    La riga che porta il cognome di uno solo dei due sposi e' suo padre:
    diventa «padre», e resta morta. Non se nell'atto c'e' gia' un padre con
    quel cognome, non se il nome e' di certo di donna, e non se il testo lo
    dice coniuge morto («vedova del fu Giuseppe Salvatore»). Rende quante
    righe ha corretto. Modifica sul posto.
    """
    if not menzioni or menzioni[0].tipo_atto not in ("matrimonio", "pubblicazione"):
        return 0
    sposi = [m for m in menzioni if m.ruolo in ("sposo", "sposa") and m.cognome]
    forma = paleografia.normalizza(testo or "")
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("defunto", "defunta") or not menzione.cognome:
            continue
        if len([s for s in sposi if _stesso_casato(s.cognome, menzione.cognome)]) != 1:
            continue
        if any(m.ruolo == "padre" and m.cognome and _stesso_casato(m.cognome, menzione.cognome)
               for m in menzioni):
            continue
        if genere is not None and menzione.nome and _sesso_netto(genere, menzione.nome) == "F":
            continue
        if menzione.nome and re.search(
            r"\b(?:vedov[oa]|dota)\s+(?:del|dal|al|di)\s+(?:fu\s+)?"
            + re.escape(paleografia.normalizza(menzione.nome).split()[0]), forma
        ):
            continue
        menzione.ruolo = "padre"
        menzione.sesso = "M"
        quante += 1
    return quante


_SUA_MOGLIE = re.compile(r"\bda\s+(.{3,60}?),[^,]{0,60}?\bsua\s+moglie")


def padre_che_e_la_moglie(menzioni: list[Menzione], testo: str | None) -> int:
    """La moglie scritta nella casella del padre: «da Troilo Rosa, sua moglie».

    Il caso: le nascite del 1879 e del 1887, dove il padre presenta il
    figlio avuto «da Marianacci Emanuele, di anni ventiquattro, filatrice,
    sua moglie legittima», o «da Troilo Rosa, sua moglie». La trascrizione
    ha messo la donna fra i padri — Emanuele per Emanuela, o il cognome
    scritto per primo preso per nome — e il bambino finiva figlio di due
    uomini e di nessuna madre.

    Si riconosce dal testo: nome e cognome della riga stanno fra il «da» e
    il «sua moglie». Solo nelle nascite, e solo se l'atto non ha gia' una
    madre con un nome. Rende quante righe ha corretto. Modifica sul posto.
    """
    if not testo or not menzioni or menzioni[0].tipo_atto != "nascita":
        return 0
    if any(m.ruolo == "madre" and (m.nome or m.cognome) for m in menzioni):
        return 0
    frasi = [f.group(1) for f in _SUA_MOGLIE.finditer(paleografia.normalizza(testo))]
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo != "padre" or not menzione.nome or not menzione.cognome:
            continue
        cognome = paleografia.normalizza(menzione.cognome)
        nome = paleografia.normalizza(menzione.nome).split()[0]
        if any(cognome in frase and nome in frase for frase in frasi):
            menzione.ruolo = "madre"
            menzione.sesso = "F"
            quante += 1
    return quante


def genitore_dell_altro_sesso(menzioni: list[Menzione], genere) -> int:
    """Il «padre» con un nome di donna, la «madre» con un nome d'uomo.

    Il caso: il matrimonio del 1811 di Felice Lella, «assistito dai suoi
    genitori Benardo Lella e Irene Dozio». La trascrizione ha messo Irene
    nella casella del padre, e l'albero dava a Felice un padre di nome
    Irene e di cognome Dozio. Nell'archivio sono una sessantina di righe.

    Il sesso lo dice il nome, imparato da tutto l'archivio
    (``nomi.Genere.di``), e solo quando e' netto. Se nell'atto manca la
    riga dell'altro genitore, il ruolo si scambia: e' l'unico posto dove
    quella persona puo' stare. Se c'e' gia', la riga perde il ruolo di
    genitore (:data:`GENITORE_DUBBIO`): meglio un legame in meno che un
    figlio con due madri. Rende quante righe ha toccato. Modifica sul posto.
    """
    if genere is None:
        return 0
    quante = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("padre", "madre") or not menzione.nome:
            continue
        sesso = _sesso_netto(genere, menzione.nome)
        atteso = "M" if menzione.ruolo == "padre" else "F"
        if sesso is None or sesso == atteso:
            continue
        # Solo dove lo scambio e' sicuro. Un «padre» di nome Domenicantonia
        # e' spesso Domenicantonio letto male — la mano confonde la a e la
        # o — e va nella coda dei dubbi, non tolto: e' il padre vero.
        altri = [riga for riga in menzioni if riga is not menzione]
        if menzione.ruolo == "padre":
            # La donna nella casella del padre, quando il padre c'e' gia'.
            # Basta che l'altra riga di padre **possa** essere un uomo: due
            # righe di padre nello stesso atto dicono gia' da sole che una
            # delle due non lo e'. Pretendere che l'altra portasse un nome
            # nettamente maschile lasciava fuori proprio il caso da cui la
            # regola e' nata: nel matrimonio del 1811 il padre vero e'
            # «Benardo», cioe' Bernardo scritto male, e un nome storpiato
            # non e' netto in nessun archivio.
            if not any(
                r.ruolo == "padre" and (r.nome or r.cognome)
                and _sesso_netto(genere, r.nome) != "F"
                for r in altri
            ):
                continue
            menzione.ruolo = GENITORE_DUBBIO
        else:
            # L'uomo nella casella della madre, quando il padre manca. Una
            # riga di padre vuota non e' un padre: nella nascita del 1900
            # di Giuseppe Nicola Moretta il padre dichiara, «dalla sua unione
            # con donna non maritata», e la trascrizione l'aveva messo fra
            # le madri lasciando vuota la casella del padre.
            vuote = [r for r in altri if r.ruolo == "padre" and not (r.nome or r.cognome)]
            if any(r.ruolo == "padre" and (r.nome or r.cognome) for r in altri):
                continue
            # Il padre che dichiara. Nella nascita del 1900 di Alessandro
            # Pelliccia «e' comparso Pelliccia Quinto», e la madre «Pinetti
            # Angelo Maria» e' Angela Maria letta male, non un secondo
            # padre. Un uomo col casato del neonato fra i dichiaranti e' il
            # padre; la madre resta madre — a meno che la riga non sia
            # proprio la sua, ricopiata fra le madri (Amedeo Moretta, 1900).
            # Basta che il dichiarante non sia di certo una donna: nel 1888
            # Nicola Chielli presenta il figlio avuto «da Mastrovincenzo
            # Maria Domenica, sua moglie», e Nicola nell'archivio e' anche
            # nome di donna (Maria Nicola Cospite); Aquinto e' troppo raro.
            casati = [r.cognome for r in altri if r.ruolo in ("neonato", "neonata") and r.cognome]
            if any(
                r.ruolo == "dichiarante" and r.nome and r.cognome and r.nome != menzione.nome
                and _sesso_netto(genere, r.nome) != "F"
                and any(_stesso_casato(r.cognome, casato) for casato in casati)
                for r in altri
            ):
                continue
            for vuota in vuote:
                vuota.ruolo = GENITORE_DUBBIO
            menzione.ruolo = "padre"
        menzione.sesso = sesso
        quante += 1
    return quante


_MADRE_VEDOVA = re.compile(
    r"\b(?:moglie|vedova)\s+(?:del|di)\s+fu\s+[A-Z]", re.IGNORECASE)


def madre_vedova_del_fu(menzioni: list[Menzione], testo: str | None) -> int:
    """Nella nascita la madre e' vedova: il padre e' il marito morto.

    Quattro atti su duemilanovecento lo dicono - «da Serafina Femminilli
    di anni trentatre, moglie del fu Salvatore Salvatore» - e in tutti e
    quattro la riga del padre manca, perche' il padre e' morto prima che
    il bambino nascesse. Senza saperlo, :func:`_padre_dal_dichiarante`
    prende per padre chi denuncia: un Nicola Femminilli di
    settantanove anni che il formulario non chiama mai padre, e che la
    ricostruzione si ritrova sposato con la vedova.

    Non si puo' dare al bambino il padre giusto - il morto non ha una
    riga nell'atto - ma si puo' non dargliene uno falso. Marca la madre e
    rende quante ne ha marcate. Modifica sul posto.
    """
    if not testo or not menzioni or menzioni[0].tipo_atto != "nascita":
        return 0
    if any(m.ruolo == "padre" for m in menzioni):
        return 0
    madri = [m for m in menzioni if m.ruolo == "madre"]
    if len(madri) != 1 or not _MADRE_VEDOVA.search(testo):
        return 0
    madri[0].vedova_nell_atto = True
    return 1


def padre_che_e_il_nonno(menzioni: list[Menzione], testo: str | None) -> int:
    """Nella nascita il padre e' il dichiarante, e la riga del «padre» e' il nonno.

    Il caso: la nascita del 1857 di Carmine, «e' comparso Savino Pelliccia,
    figlio di fu Samuele Pelliccia, di anni ventiquattro», e quella del
    1855, «e' comparso Raimondo Marianacci di Vincenzo, di anni
    trentotto». La trascrizione mette il nome che viene dopo «figlio di»
    nella casella del padre del neonato, e il padre vero fra i
    dichiaranti: il bambino finiva figlio del nonno, e il nonno — spesso
    gia' morto — marito della nuora. Nei registri del 1855-1857 sono una
    quarantina di atti.

    Si riconosce da tre cose insieme: il dichiarante ha un'eta' e il
    «padre» no; hanno lo stesso cognome e due nomi diversi; e il testo
    dell'atto scrive il nome del «padre» subito dopo quello del
    dichiarante, come suo patronimico. Allora la riga del padre prende il
    nome e l'eta' del dichiarante, e il nome di prima resta come
    patronimico. Rende quante righe ha corretto. Modifica sul posto.
    """
    if not testo:
        return 0
    padri = [m for m in menzioni if m.ruolo == "padre"]
    dichiaranti = [m for m in menzioni if m.ruolo == "dichiarante"]
    if len(padri) != 1 or len(dichiaranti) != 1:
        return 0
    padre, dichiarante = padri[0], dichiaranti[0]
    if not padre.nome or not dichiarante.nome or padre.eta_letta or not dichiarante.eta_letta:
        return 0
    if paleografia.forma_canonica(padre.cognome or "") != paleografia.forma_canonica(
        dichiarante.cognome or ""
    ):
        return 0
    # I nomi si confrontano come li ha scritti la trascrizione, non come
    # li ha lasciati una correzione: dove qualcuno aveva gia' corretto a
    # mano il nome del «padre» in quello del dichiarante — e' successo
    # nella nascita del 1855, atto 3391 — i due nomi risultavano uguali,
    # la regola non scattava piu', e la nota «padre del dichiarante»
    # tornava a fare del dichiarante il figlio di se stesso. La
    # correzione aveva ragione sul nome; qui serve sapere che quella riga
    # E' il dichiarante, e la prova sta nella lettura di partenza.
    padre_letto = padre.nome_letto or padre.nome
    dichiarante_letto = dichiarante.nome_letto or dichiarante.nome
    if paleografia.normalizza(padre_letto) == paleografia.normalizza(dichiarante_letto):
        return 0
    # Il titolo davanti al nome del padre non rompe il patronimico: la
    # nascita del 1855 scrive «e' comparso Raimondo marianacci figlio di
    # Signor Vincenzo marianacci», e senza il titolo nello schema Raimondo
    # restava dichiarante e il bambino figlio del nonno.
    schema = re.compile(
        re.escape(dichiarante_letto)
        + r"[^.;]{0,40}?\b(?:figlio\s+di\s+fu|figlio\s+di|del\s+fu|del\s+vivente|di\s+vivente|di\s+fu|di|del|fu)\s+"
        + r"(?:(?:signor|sig\.?|don)\s+)?"
        + re.escape(padre_letto) + r"\b",
        re.IGNORECASE,
    )
    if not schema.search(testo):
        return 0
    padre.patronimico = padre.patronimico or padre_letto
    padre.nome = dichiarante.nome
    padre.eta_letta = dichiarante.eta_letta
    padre.eta = dichiarante.eta
    padre.e_il_dichiarante = True
    # E' la stessa riga letta due volte, come quelle dei matrimoni divisi
    # male: marcarla lo dice alla ricostruzione, che la rimette col
    # dichiarante da sola. Senza, la riga riscritta si faceva una scheda
    # per conto suo - il Vincenzo Pannunzio del 1858, due righe accanto
    # alle ventinove dello stesso uomo - e ci voleva un'unione decisa a
    # mano per ognuno degli undici atti.
    padre.doppia_di = dichiarante.id
    return 1


def cognome_della_madre_al_neonato(menzioni: list[Menzione]) -> int:
    """Il neonato col cognome della madre, quando il padre e' scritto.

    Il caso: le nascite degli anni Trenta e Quaranta, «di dare alla
    Neonata il nome di Maria Enrica». La pagina da' al bambino solo il
    nome, e la trascrizione gli mette il primo cognome che trova, quello
    della madre: Maria Enrica «Marianacci», figlia di Casimiro Chielli e di
    Pulcheria Marianacci; Teodora «Pelliccia», figlia di Giuseppe Colella.
    Un figlio legittimo porta il casato del padre.

    Solo il neonato, e solo quando il suo cognome e' proprio quello della
    madre e non quello del padre: un cognome diverso da tutti e due e'
    un'altra storia (vedi ``esecuzione.cognomi_dal_padre``). Rende quanti
    cognomi ha ricondotto. Modifica sul posto.
    """
    per_id = {m.id: m for m in menzioni}
    quanti = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("neonato", "neonata") or not menzione.cognome:
            continue
        padre = per_id.get(menzione.padre) if menzione.padre is not None else None
        madre = per_id.get(menzione.madre) if menzione.madre is not None else None
        if padre is None or madre is None or padre.ignota:
            continue
        if not padre.cognome or not madre.cognome:
            continue
        if menzione.chiave_cognome != madre.chiave_cognome:
            continue
        if menzione.chiave_cognome == padre.chiave_cognome:
            continue
        menzione.cognome = padre.cognome
        menzione.cognome_origine = "padre"
        quanti += 1
    return quanti


def casati_confermati(menzioni: list[Menzione]) -> int:
    """Il casato che il figlio e suo padre scrivono uguale nello stesso atto.

    Due righe, due letture della stessa parola: e' il cognome piu'
    affidabile dell'archivio, e il solo che possa dire «questa persona
    non e' di quell'altra famiglia». Il caso: la morte n. 14 del 1836,
    «Maria Catolini, figlia di Prospero Catolini», finita dentro Maria
    Pelliccia, moglie di Felice Lella — e da li' Felice Lella aveva una
    moglie morta figlia di un Catolini. Vedi ``evidenza._un_altro_casato``.

    Conta solo il cognome **letto** in tutte e due le righe: quello
    derivato dall'altra riga non e' una seconda lettura, e' la stessa.
    E non conta la riga letta due volte (``righe_lette_due_volte``):
    li' la lettura e' incerta per definizione — «Daba» e «Montra» erano
    tutte e due Moretta.

    Rende quante coppie figlio-padre ha confermato. Modifica sul posto.
    """
    per_id = {m.id: m for m in menzioni}
    ripetute = {m.id for m in menzioni if m.doppia_di is not None}
    ripetute |= {m.doppia_di for m in menzioni if m.doppia_di is not None}
    quante = 0
    for menzione in menzioni:
        if menzione.padre is None or menzione.id in ripetute or not menzione.cognome:
            continue
        padre = per_id.get(menzione.padre)
        if padre is None or padre.id in ripetute or not padre.cognome:
            continue
        if menzione.cognome_origine != "atto" or padre.cognome_origine != "atto":
            continue
        if menzione.chiave_cognome != padre.chiave_cognome:
            continue
        menzione.casato_confermato = padre.casato_confermato = True
        quante += 1
    return quante


def cognome_dal_dichiarante(menzioni: list[Menzione]) -> int:
    """Il cognome del neonato quando l'atto non scrive il padre.

    La fase 4 da' al neonato il cognome del padre o della madre quando
    uno dei due c'e' scritto. Restano trentatre bambini a cui l'atto non
    da' nessun cognome, e in trentuno di quei casi il padre **c'e'**: e'
    il dichiarante, e l'estrazione non gli ha dato anche il ruolo di
    genitore. Il formulario della nascita non lascia dubbi su chi sia —
    «e' comparso X ... il quale ci ha presentato un maschio ... nato da Y
    sua moglie legittima e da lui dichiarante X».

    Il caso che l'ha voluta: la nascita n. 34 del 1850. Il dichiarante e'
    Giovanni Desiderio, la madre Lucia Lella — figlia di Domenico, nipote
    di Filippo — e la bambina resta **«Maria Margherita»**, senza
    cognome, appesa alla sola madre. Con il cognome del padre e' Maria
    Margherita Desiderio, e la famiglia si chiude.

    Due prudenze. Si applica **solo agli atti di nascita**, perche' solo
    li' il dichiarante e' per formula il padre; e solo se il dichiarante
    e' un uomo, perche' a dichiarare puo' andare la levatrice — e in uno
    di questi trentatre atti ci va davvero.

    Rende quanti cognomi ha derivato. Modifica sul posto.
    """
    if not menzioni or menzioni[0].tipo_atto != "nascita":
        return 0
    if any(m.ruolo == "padre" for m in menzioni):
        return 0        # il padre c'e': ha gia' deciso la fase 4
    dichiaranti = [
        m for m in menzioni
        if m.ruolo == "dichiarante" and m.cognome
        and nomi.Genere.probabile_maschile(m.nome)
    ]
    if len(dichiaranti) != 1:
        return 0
    padre = dichiaranti[0]
    quanti = 0
    for menzione in menzioni:
        if menzione.ruolo not in ("neonato", "neonata") or menzione.cognome:
            continue
        if menzione.ignota:
            continue
        menzione.cognome = padre.cognome
        menzione.cognome_origine = "dichiarante"
        quanti += 1
    return quanti


# Quanto deve somigliare una parola del testo a un cognome perche' lo si
# dica scritto: la riga e il testo non sempre coincidono lettera per
# lettera — la morte del 1820 scrive «Colletta», la riga dice Colella.
SOMIGLIANZA_DEL_CASATO_NEL_TESTO = 0.75
RUOLI_DEL_FIGLIO_DELL_ATTO = frozenset({"neonato", "neonata", "defunto", "defunta"})


def _scritto_nel_testo(cognome: str | None, testo: str, parole: list[str]) -> bool:
    forma = paleografia.normalizza(cognome or "")
    if not forma or forma in testo:
        return bool(forma)
    lunghezza = len(forma.split())
    return any(
        paleografia.somiglianza(" ".join(parole[i:i + lunghezza]), forma)
        >= SOMIGLIANZA_DEL_CASATO_NEL_TESTO
        for i in range(len(parole) - lunghezza + 1)
    )


def casato_che_l_atto_non_scrive(menzioni: list[Menzione], testo: str | None) -> int:
    """Il cognome del figlio che l'atto non scrive: quello del padre.

    Il caso: la nascita del 1837, «e' comparso Giovanni Nicodemo ... la
    stessa e' nata da Maria di Paolo Santhoro moglie legittima e da lui
    dichiarante ... di dare alla neonata il nome di Eufrasia». Il testo non
    da' alla bambina nessun cognome, e la trascrizione gliene ha messo uno
    — Petta — che nell'atto non c'e'. Cosi' la morte del 1840 di «Marta di
    Maria Rosa», figlia di Felice Montecchio, e la nascita del 1833 di
    «Clementina Ferrara», figlia di Felice Nicodemo: negli atti di nascita
    e di morte dell'archivio sono una quarantina.

    Si applica solo se il cognome del figlio non si legge da nessuna parte
    nel testo — ne' come sta nella riga ne' com'era prima di una
    correzione, ne' in una forma che gli somigli — e quello del padre si'.
    Rende quanti cognomi ha cambiato. Modifica sul posto.
    """
    if not testo or not menzioni or menzioni[0].tipo_atto not in ("nascita", "morte"):
        return 0
    padri = [m for m in menzioni if m.ruolo == "padre" and m.cognome and not m.ignota]
    figli = [m for m in menzioni if m.ruolo in RUOLI_DEL_FIGLIO_DELL_ATTO and m.cognome and not m.ignota]
    if len(padri) != 1 or len(figli) != 1:
        return 0
    padre, figlio = padri[0], figli[0]
    if _stesso_casato(figlio.cognome, padre.cognome):
        return 0
    forma = paleografia.normalizza(testo)
    parole = forma.split()
    if any(_scritto_nel_testo(c, forma, parole) for c in {figlio.cognome, figlio.cognome_letto} if c):
        return 0
    if not _scritto_nel_testo(padre.cognome, forma, parole):
        return 0
    figlio.cognome = padre.cognome
    figlio.cognome_origine = "padre"
    return 1


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
