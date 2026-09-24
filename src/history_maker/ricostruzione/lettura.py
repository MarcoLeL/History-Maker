"""Dalle trascrizioni alle menzioni documentarie, passando per l'interpretazione.

Fra "cio' che la trascrizione dice" e "chi e' questa persona" ci sono due
passaggi, non uno, e confonderli e' il primo modo di sbagliare::

    RAW           'Domenico Lella fu Michele'
    INTERPRETATO   nome 'Domenico', cognome 'Lella', padre 'Michele'
    IDENTITA'      INDIVIDUO_184

Il secondo passaggio e' un'inferenza e sta altrove (:mod:`risoluzione`).
Il primo invece e' **lettura del formulario**: 'fu Michele' non e' parte
del cognome di nessuno, e' il modo in cui l'atto dice chi era il padre.
Lasciarcelo dentro non fa solo perdere un dato — fa peggio, perche'
'Lella fu Michele' e 'Lella' non si somigliano abbastanza da sembrare la
stessa famiglia, e il cognome, da indizio a favore, diventa un veto. Sui
registri di Torrebruna questo da solo spiega una trentina delle ottanta
famiglie spezzate in due.

Le tre cose che questo modulo separa dal nome e dal cognome, tenendo
sempre il grezzo:

**La filiazione.** ``Pepe fu Giosia``, ``Pelliccia di Donato``: il
cognome e' il primo pezzo, il resto e' il nome del padre — e un
patronimico e' l'unico caso in cui la fonte stessa **distingue** due
omonimi, quindi vale il doppio di quanto costa estrarlo. La guardia e' il
corpus: si separa solo se la coda si comporta da nome di battesimo nel
resto dell'archivio. ``Colapietro di Castiglione`` resta intero, perche'
'Castiglione' nei registri non e' il nome di nessuno.

**Le letture alternative.** Chi trascrive, quando esita, scrive
``Chiehi [o Chichi]`` oppure ``Sepualdo [Epaldo/Gesualdo]``. Sono ipotesi
di qualcuno che ha visto l'immagine — l'evidenza migliore che ci sia dopo
l'immagine stessa — e tenere solo la prima le butta via.

**Le apposizioni.** ``Antonia Felice sua moglie``, ``Maria d'Ettorre
defunta``: il formulario spiega chi e' la persona, e quella spiegazione
finisce nella colonna del cognome. Va tolta di li' e messa dove
significa qualcosa.

Cio' che questo modulo **non** fa: correggere. Non sceglie fra 'Lella' e
'Lelli', non decide che 'Doro' sia 'Di Nardo'. Quelle sono conclusioni
che si possono trarre solo guardando tutto il grafo, e stanno in
:mod:`risoluzione`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from history_maker import menzioni as lettura_atti, nomi, paleografia
from history_maker.ricostruzione import modello

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Le letture alternative che la trascrizione propone da sola
# ---------------------------------------------------------------------------

# 'Chiehi [o Chichi]', 'Sepualdo [Epaldo/Gesualdo]', 'nacci [Marianacci]'.
_ALTERNATIVA = re.compile(r"\[([^\]]+)\]")
# 'Giquado [Giacuado] / Giquado': la barra separa due letture intere.
_BARRA = re.compile(r"\s+/\s+")

# Quello che dentro le quadre non e' una lettura alternativa.
_NON_LETTURE = frozenset({"sic", "?", "illeggibile", "o", "oppure"})


def separa_alternative(valore: str | None) -> tuple[str | None, tuple[str, ...]]:
    """Divide ``Chiehi [o Chichi]`` nella lettura principale e nelle altre.

    >>> separa_alternative("Chiehi [o Chichi]")
    ('Chiehi', ('Chichi',))
    >>> separa_alternative("Sepualdo [Epaldo/Gesualdo]")
    ('Sepualdo', ('Epaldo', 'Gesualdo'))
    >>> separa_alternative("Pelliccia")
    ('Pelliccia', ())

    Le quadre dentro una parola sono un'altra cosa: ``B[e]r[t]i`` non
    propone un'alternativa, segnala le lettere che chi legge ha dovuto
    indovinare. La lettura principale e' con quelle lettere; l'alternativa
    e' senza, che e' l'altra cosa che potrebbe esserci scritto.
    """
    if not valore or "[" not in valore and "/" not in valore:
        return valore, ()

    alternative: list[str] = []
    testo = valore

    # Prima le quadre che stanno dentro una parola: se togliendo le
    # parentesi (ma tenendo il contenuto) e togliendo il contenuto si
    # ottengono due parole diverse, sono due letture.
    if _ALTERNATIVA.search(testo) and not re.search(r"(^|\s)\[", testo):
        con = _ALTERNATIVA.sub(r"\1", testo).strip()
        senza = _ALTERNATIVA.sub("", testo).strip()
        if len(senza) >= 3 and senza != con:
            alternative.append(senza)
        testo = con
    else:
        for dentro in _ALTERNATIVA.findall(testo):
            for pezzo in re.split(r"[/,]|\bo\b", dentro):
                pulito = pezzo.strip(" .;")
                if len(pulito) >= 3 and pulito.casefold() not in _NON_LETTURE:
                    alternative.append(pulito)
        testo = _ALTERNATIVA.sub(" ", testo)

    pezzi = [p.strip() for p in _BARRA.split(testo) if p.strip()]
    principale = pezzi[0] if pezzi else None
    for altro in pezzi[1:]:
        if altro != principale:
            alternative.append(altro)

    if principale:
        principale = re.sub(r"\s{2,}", " ", principale).strip(" .,;")
    # Ordine stabile e senza doppioni: l'ordine di queste forme entra nei
    # confronti, e un insieme lo deciderebbe a caso a ogni esecuzione.
    viste: dict[str, None] = {}
    for forma in alternative:
        if forma and forma != principale:
            viste.setdefault(forma, None)
    return (principale or None), tuple(viste)


# ---------------------------------------------------------------------------
# La filiazione finita dentro il cognome
# ---------------------------------------------------------------------------

_FILIAZIONE = re.compile(
    r"^(?P<cognome>.+?)\s+(?:fu|di|de|d')\s+"
    r"(?P<padre>[A-ZÀ-Ü][\w'’à-ü]+(?:\s+[A-ZÀ-Ü][\w'’à-ü]+)?)\s*$",
    re.UNICODE,
)

# Sotto questa proporzione la coda si comporta da nome di battesimo e non
# da cognome, e allora e' un patronimico. E' volutamente severa: sbagliare
# qui vuol dire amputare un cognome vero.
SA_DI_NOME = 0.35

# Il cognome che resta deve reggersi in piedi da solo. Due lettere non
# sono un cognome, sono cio' che avanza da un taglio sbagliato.
COGNOME_MINIMO = 3


def separa_filiazione(
    cognome: str | None, bilancia: nomi.Bilancia | None
) -> tuple[str | None, str | None]:
    """Divide ``Lella fu Michele`` in cognome e nome del padre.

    >>> from history_maker import nomi
    >>> b = nomi.Bilancia.dal_corpus([("Michele", "Lella")] * 10)
    >>> separa_filiazione("Lella fu Michele", b)
    ('Lella', 'Michele')
    >>> separa_filiazione("Di Nardo", b)
    ('Di Nardo', None)

    Senza corpus non si separa niente: la guardia **e'** il corpus, e
    senza sarebbe una regola cieca del tipo che questo progetto sta
    smettendo di usare.
    """
    if not cognome or not bilancia:
        return cognome, None
    trovato = _FILIAZIONE.match(cognome.strip())
    if not trovato:
        return cognome, None
    testa = trovato.group("cognome").strip()
    coda = trovato.group("padre").strip()
    if len(testa) < COGNOME_MINIMO:
        return cognome, None
    # La coda deve comportarsi da nome di battesimo nel resto
    # dell'archivio. 'Michele' si', 'Castiglione' no — e nel dubbio
    # (troppo rara per dire qualcosa) si lascia com'e'.
    sa_di_cognome = bilancia.sa_di_cognome(coda)
    if sa_di_cognome is None or sa_di_cognome > SA_DI_NOME:
        return cognome, None
    return testa, coda


# ---------------------------------------------------------------------------
# I titoli davanti al nome
# ---------------------------------------------------------------------------

# 'Don Felice Calidonio', 'Donna Federica', e nel matrimonio del 1877
# «figlia di Signor Vincenzo», col casato taciuto perche' e' quello della
# sposa. Il titolo dice il rango, non chi e' la persona: nella colonna del
# nome diventa la prima parte del nome — e il sesso si legge da li' — in
# quella del cognome diventa un casato che nessun altro porta.
_TITOLI = re.compile(r"(?:donna|don|signora|signor|sigr?a?\.?)\s+", re.IGNORECASE)


def separa_titolo(valore: str | None) -> tuple[str | None, str | None]:
    """Toglie il titolo dalla testa del nome o del cognome, e lo rende.

    >>> separa_titolo("Don Felice Calidonio")
    ('Felice Calidonio', 'Don')
    >>> separa_titolo("Signor Colapietra")
    ('Colapietra', 'Signor')
    >>> separa_titolo("Donato")
    ('Donato', None)
    """
    if not valore:
        return valore, None
    testo = valore.strip()
    trovato = _TITOLI.match(testo)
    if not trovato:
        return valore, None
    return testo[trovato.end():].strip() or None, trovato.group(0).strip()


# ---------------------------------------------------------------------------
# Le apposizioni del formulario
# ---------------------------------------------------------------------------

# 'Antonia Felice sua moglie', 'Maria d'Ettorre defunta', 'Lella vedova'.
# Sono spiegazioni del formulario che spiegano chi e' la persona, e che
# nella colonna del nome diventano parte del nome.
_APPOSIZIONI = re.compile(
    r"\s+(?:sua\s+moglie|suo\s+marito|sua\s+madre|suo\s+padre|sua\s+figlia|"
    r"suo\s+figlio|defunt[oa]|vedov[oa]|nubile|celibe|coniugat[oa]|\[sic\])\s*$",
    re.IGNORECASE,
)

_STATO_DALL_APPOSIZIONE = {
    "defunto": "defunto", "defunta": "defunta",
    "vedovo": "vedovo", "vedova": "vedova",
    "nubile": "nubile", "celibe": "celibe",
    "coniugato": "coniugato", "coniugata": "coniugata",
}


def separa_apposizione(valore: str | None) -> tuple[str | None, str | None]:
    """Toglie ``sua moglie`` dal cognome e dice cosa quella parola diceva.

    >>> separa_apposizione("Felice sua moglie")
    ('Felice', None)
    >>> separa_apposizione("d'Ettorre defunta")
    ("d'Ettorre", 'defunta')
    >>> separa_apposizione("sua moglie")
    (None, None)

    Il terzo caso e' quello che costa di piu' se sfugge. Quando l'atto
    non da' il cognome della donna, chi trascrive scrive nella colonna
    del cognome cio' che l'atto dice di lei — ``sua moglie`` — e quella
    stringa diventa un cognome rarissimo, quindi un indizio fortissimo:
    due donne senza cognome risultano della stessa famiglia con +3,0 di
    prova. Un cognome che non e' un cognome vale **meno** di nessun
    cognome, non di piu'.
    """
    if not valore:
        return valore, None
    intero = re.sub(r"\s+", " ", valore.strip()).casefold()
    if _APPOSIZIONI.fullmatch(" " + intero) or intero in _STATO_DALL_APPOSIZIONE:
        return None, _STATO_DALL_APPOSIZIONE.get(intero)
    stato = None
    testo = valore
    for _ in range(3):        # 'Felice sua moglie defunta' esiste
        trovato = _APPOSIZIONI.search(testo)
        if not trovato:
            break
        parola = re.sub(r"\s+", " ", trovato.group(0).strip()).casefold()
        stato = _STATO_DALL_APPOSIZIONE.get(parola, stato)
        ridotto = testo[: trovato.start()].strip()
        if len(ridotto) < COGNOME_MINIMO:
            break
        testo = ridotto
    return (testo or None), stato


# ---------------------------------------------------------------------------
# L'interprete
# ---------------------------------------------------------------------------

@dataclass
class Interprete:
    """Applica lo strato di interpretazione a una menzione appena letta.

    Non e' una funzione libera perche' ha bisogno del corpus intero — la
    bilancia nome/cognome — e il corpus si costruisce una volta sola.
    """

    bilancia: nomi.Bilancia | None = None
    # Le letture corrette da chi ha guardato l'immagine, o da una
    # persona: ``{(menzione, campo): valore}``. Entrano **prima** di
    # tutto il resto, perche' chi ha visto la carta ne sa piu' della
    # trascrizione — ma la trascrizione resta dov'e', nei campi
    # ``*_letto`` e nel grezzo dei fatti.
    corrette: dict = field(default_factory=dict)
    # Contatori di cio' che e' stato separato, per il rapporto: un
    # intervento silenzioso su quarantamila righe non e' verificabile.
    conteggi: Counter[str] = field(default_factory=Counter)

    def __call__(self, menzione: lettura_atti.Menzione) -> None:
        self._applica_correzioni(menzione)
        nome, alternative_nome = separa_alternative(menzione.nome)
        cognome, alternative_cognome = separa_alternative(menzione.cognome)
        if alternative_nome or alternative_cognome:
            self.conteggi["letture alternative"] += 1

        nome, titolo_del_nome = separa_titolo(nome)
        cognome, titolo_del_cognome = separa_titolo(cognome)
        if titolo_del_nome or titolo_del_cognome:
            self.conteggi["titoli"] += 1
        cognome, stato = separa_apposizione(cognome)
        if stato and not menzione.stato_vitale:
            menzione.stato_vitale = stato
        if cognome != menzione.cognome and cognome is not None:
            self.conteggi["apposizioni tolte dal cognome"] += 1

        cognome, padre = separa_filiazione(cognome, self.bilancia)
        if padre:
            self.conteggi["filiazioni tolte dal cognome"] += 1
            # Il patronimico dal nome ha la precedenza: viene da una
            # formula piu' regolare e piu' spesso giusta.
            if not menzione.patronimico:
                menzione.patronimico = padre

        menzione.nome = nome
        menzione.cognome = cognome
        menzione.alternative_nome = alternative_nome
        menzione.alternative_cognome = alternative_cognome
        self._genitore_ignoto(menzione)

    def _genitore_ignoto(self, menzione: lettura_atti.Menzione) -> None:
        """La casella del genitore che l'atto lascia in bianco non e' una persona.

        Gli atti lo dicono in chiaro — «e da padre incerto» nella nascita
        del 1813 n. 32, «figlia delli quondam» e poi piu' niente nella
        morte del 1812 n. 26 — e la trascrizione lascia la riga vuota. Chi
        la contava apriva una scheda senza nome per ognuna: duecento
        sedici righe, duecentottantasei schede vuote nell'albero, tutte
        col nome «senza nome». Un padre che l'atto non sa non e' un padre
        ignoto da mostrare: e' un padre che non c'e', e il posto va
        lasciato libero.
        """
        if menzione.ignota:
            # «Padre ignoto», «dalla sua unione con donna non maritata»:
            # qui l'atto ha parlato, e la riga serve dov'e'. E' il segno
            # che il figlio e' naturale, e la lettura delle famiglie lo
            # usa per dare al bambino il padre che lo dichiara.
            return
        if menzione.ruolo in lettura_atti.GENITORI and not (menzione.nome or menzione.cognome):
            menzione.ruolo = "altro"
            self.conteggi["genitore che l'atto lascia in bianco"] += 1

    def _applica_correzioni(self, menzione: lettura_atti.Menzione) -> None:
        """Rimette nel dato cio' che l'immagine ha detto.

        E' il ritorno della verifica mirata: senza, una risposta
        dall'immagine costerebbe quota e non cambierebbe niente.
        """
        for campo in ("nome", "cognome", "professione", "via"):
            valore = self.corrette.get((menzione.id, campo))
            if valore:
                setattr(menzione, campo, valore)
                self.conteggi[f"{campo} corretto sull'immagine"] += 1
        # Anche la casella puo' essere sbagliata, non solo cio' che c'e' scritto
        # dentro. Nella promessa del 1822 n. 6 la madre dello sposo, «Celute
        # Monno ... domiciliata col marito», e' finita nella casella della
        # sposa accanto alla sposa vera, e Domenicangelo Moretta risultava con
        # due mogli; nel frammento del 1833 il nome che viene da un certificato
        # di morte del 1795 - «Luigi Bosi ... ivi morta» - faceva lo sposo di
        # Maria Di Nardo. La correzione rimette la riga nel ruolo che l'atto le
        # da', e la famiglia dell'atto si legge dopo: e' questo che conta.
        # «altro» la toglie da ogni famiglia senza toglierla dall'archivio.
        ruolo = self.corrette.get((menzione.id, "ruolo"))
        if ruolo:
            menzione.ruolo = ruolo.strip().casefold()
            self.conteggi["ruolo corretto sull'immagine"] += 1
        # «Figlia delli furono Tobia Pelliccia e di Anna Lella»: i genitori gia'
        # morti restano genitori. Il «fu» va nello stato civile, non nel ruolo:
        # nella morte «defunto» e' il morto dell'atto, e una madre scritta cosi'
        # diventava una figlia del padre (morte del 1827 n. 14, Anna Desiderio
        # «figlia» di suo marito Vincenzo Pelliccia).
        stato = self.corrette.get((menzione.id, "stato_vitale"))
        if stato:
            menzione.stato_vitale = stato.strip().casefold()
            self.conteggi["stato civile corretto sull'immagine"] += 1
        # Il patronimico si stacca dal nome **prima** della correzione: «Maria
        # di Rondo», corretta in Maria Di Nardo, restava figlia di Rondo, e il
        # veto sui padri diversi la teneva lontana da se stessa. Corretto il
        # nome, il patronimico si rifa' da quello nuovo.
        if self.corrette.get((menzione.id, "nome")):
            menzione.nome, menzione.patronimico = nomi.separa_patronimico(menzione.nome)
        # Il patronimico si legge come il nome, e si legge male come il nome.
        # Nella nascita del 1858 n. 36 Vincenzo Pannunzio e' «figlio di
        # Giudeto»: e' Diodato, come in tutto il resto dell'archivio, ma le due
        # letture restano sotto la soglia che separa due padri, e la scheda che
        # le portava tutte e due era incoerente per costruzione - rifiutata
        # all'unione, e ridivisa se ci arrivava. La correzione lo rimette com'e'.
        patronimico = self.corrette.get((menzione.id, "patronimico"))
        if patronimico:
            menzione.patronimico = patronimico.strip()
            self.conteggi["patronimico corretto sull'immagine"] += 1
        eta = self.corrette.get((menzione.id, "eta"))
        if eta == ETA_DA_TOGLIERE:
            # L'eta' che l'atto stesso smentisce. Nella morte del 1821
            # (atto 820) comparisce «Menasse Franchella di anni trentatre
            # ... padre della defunta», e la defunta ha «anni
            # trentacinque»: la pagina, riletta, dice proprio cosi'.
            # Una delle due e' una svista della penna, e non si puo'
            # sapere quale cifra fosse: si toglie quella che l'aritmetica
            # esclude, invece di inventarne una. Senza, quella riga da'
            # alla madre un figlio nato prima di lei, e il veto della
            # fertilita' le impedisce per sempre di ricomporsi.
            menzione.eta_letta = None
            menzione.eta = None
            self.conteggi["eta' tolta perche' l'atto la smentisce"] += 1
        elif eta:
            menzione.eta_letta = eta
            menzione.eta = nomi.analizza_eta(eta)
            self.conteggi["eta' corretta sull'immagine"] += 1


# ---------------------------------------------------------------------------
# Il corpus
# ---------------------------------------------------------------------------

@dataclass
class Corpus:
    """Tutte le menzioni lette, piu' cio' che serve a pesarle.

    Le frequenze non sono statistiche di contorno: sono **il modello
    probabilistico**. Quanto vale l'indizio "si chiamano tutti e due
    Pelliccia" non si puo' decidere a tavolino, si legge qui.
    """

    menzioni: list[lettura_atti.Menzione]
    per_id: dict[int, lettura_atti.Menzione]
    per_atto: dict[int, list[lettura_atti.Menzione]]
    frequenze_cognome: Counter[str]
    frequenze_nome: Counter[str]
    frequenze_parte_nome: Counter[str]
    frequenze_professione: Counter[str]
    frequenze_contrada: Counter[str]
    correzioni: dict[str, str] = field(default_factory=dict)
    scambi: set[int] = field(default_factory=set)
    genere: nomi.Genere | None = None
    bilancia: nomi.Bilancia | None = None
    interpretazioni: Counter[str] = field(default_factory=Counter)
    # I vocabolari dei mestieri e delle contrade. Si riempiono dopo la
    # lettura — si costruiscono su queste stesse menzioni — e prima della
    # risoluzione, che li usa per confrontare.
    vocabolari: dict = field(default_factory=dict)
    # Gli atti, per la parte che serve qui: anno, tipo, immagine, testo.
    atti: dict[int, dict] = field(default_factory=dict)

    @property
    def totale(self) -> int:
        return len(self.menzioni)

    def chiavi(self) -> "Chiavi":
        return Chiavi(self.per_id, self.vocabolari)


class Chiavi(lettura_atti.ChiaviFamiliari):
    """Tutto cio' che serve a una scheda per leggere una menzione.

    Le chiavi di parentela — 'il padre di questa riga e' quella li' — piu'
    il **vocabolario del paese**, che dice quali grafie di un mestiere o
    di una contrada sono la stessa cosa.

    Il vocabolario sta qui, e non solo nella scrittura finale, per una
    ragione misurata: senza, il confronto fra due schede guardava la
    forma normalizzata grezza, e due menzioni scritte 'Agrimenfore' e
    'Agrimensore' non prendevano il punto del mestiere in comune — anche
    se il sistema, dieci righe piu' in la', sapeva benissimo che sono la
    stessa parola.
    """

    def __init__(self, per_id: dict, vocabolari: dict | None = None):
        super().__init__(per_id)
        self.vocabolari = vocabolari or {}

    def _canonica(self, campo: str, valore: str | None) -> str | None:
        vocabolario = self.vocabolari.get(campo)
        if vocabolario is None:
            return paleografia.normalizza(valore) if valore else None
        return vocabolario.canonica(valore)

    def mestiere(self, valore: str | None) -> str | None:
        return self._canonica("professione", valore)

    def contrada(self, valore: str | None) -> str | None:
        return self._canonica("contrada", valore)


def carica(
    conn: sqlite3.Connection, deposito=None, correzioni: dict | None = None
) -> Corpus:
    """Legge la fase 4 e ne fa menzioni interpretate.

    L'igiene dei nomi (inversioni Cognome/Nome dal 1881, varianti del nome
    di battesimo) e la lettura della famiglia dentro l'atto restano quelle
    gia' scritte e collaudate: sono **lettura della fonte**, non
    inferenza, e riscriverle non avrebbe migliorato niente.

    Con un ``deposito`` il risultato si mette da parte, indicizzato su
    un'impronta delle tabelle di partenza. E' il pezzo piu' caro che non
    fosse ancora in cache — un minuto abbondante su cinquantamila righe —
    e si ripaga subito: ``verifica`` e ``arbitra`` ricostruiscono il
    grafo in memoria ogni volta che partono, e senza cache pagherebbero
    quel minuto prima di poter fare una sola domanda.
    """
    from history_maker.ricostruzione import registro

    corrette = correzioni if correzioni is not None else registro.correzioni(conn)
    if deposito is not None:
        from history_maker.ricostruzione import cache

        # Le correzioni entrano nella chiave: una lettura corretta ieri
        # deve far rifare il corpus, non riprendere quello di prima.
        chiave = cache.impronta(
            _impronta_sorgente(conn), VERSIONE_LETTURA,
            sorted((f"{m}:{c}", v) for (m, c), v in corrette.items()),
        )
        return deposito.ottieni(
            "corpus", chiave, lambda: carica(conn, correzioni=corrette)
        )

    conn.row_factory = sqlite3.Row
    righe = list(conn.execute("SELECT id, atto, ruolo, nome, cognome FROM persone"))
    if not righe:
        raise ValueError("la tabella 'persone' e' vuota: non c'e' niente da ricostruire")

    genere = nomi.Genere.dal_corpus([(r["ruolo"], r["nome"]) for r in righe])
    bilancia = nomi.Bilancia.dal_corpus([(r["nome"], r["cognome"]) for r in righe])
    scambi = nomi.inversioni_per_atto(
        {r["id"]: r["atto"] for r in righe},
        {r["id"]: (r["nome"], r["cognome"]) for r in righe},
        bilancia,
    )
    correzioni, _proposte = nomi.correzioni_dei_nomi([r["nome"] for r in righe], genere)

    interprete = Interprete(bilancia, corrette=corrette)
    menzioni = lettura_atti.carica_menzioni(
        conn, correzioni, genere, scambi, interpreta=interprete,
        bilancia=bilancia,
    )

    per_atto: dict[int, list[lettura_atti.Menzione]] = defaultdict(list)
    for menzione in menzioni:
        per_atto[menzione.atto].append(menzione)

    atti = {
        riga["id"]: {
            "anno": riga["anno"], "tipo": riga["tipo"], "immagine": riga["immagine"],
            "numero": riga["numero_atto"], "testo": riga["testo_integrale"],
            "registro": riga["registro"],
            # La data dell'evento prima di quella dell'atto: fra due parti
            # contano i giorni fra le nascite, non fra le denunce.
            "data": riga["data_evento"] or riga["data_atto"],
        }
        for riga in conn.execute(
            "SELECT id, anno, tipo, immagine, numero_atto, testo_integrale, registro, "
            "data_atto, data_evento FROM atti"
        )
    }

    return Corpus(
        menzioni=menzioni,
        per_id={m.id: m for m in menzioni},
        per_atto=dict(per_atto),
        frequenze_cognome=_conta(m.chiave_cognome for m in menzioni),
        frequenze_nome=_conta(m.chiave_nome for m in menzioni),
        frequenze_parte_nome=_conta(p for m in menzioni for p in m.parti_nome),
        frequenze_professione=_conta(
            paleografia.normalizza(m.professione) for m in menzioni if m.professione
        ),
        frequenze_contrada=_conta(
            paleografia.normalizza(m.via or m.residenza or "") for m in menzioni
        ),
        correzioni=correzioni,
        scambi=scambi,
        genere=genere,
        bilancia=bilancia,
        interpretazioni=interprete.conteggi,
        atti=atti,
    )


# La versione di questo modulo, che entra nella chiave della cache. Va
# alzata quando cambia il modo di leggere o di interpretare: senza,
# un'esecuzione dopo una modifica rileggerebbe dalla cache il corpus
# vecchio e la modifica non si vedrebbe.
# Il valore con cui una correzione dice «questa eta' non si puo'
# leggere»: la toglie invece di sostituirla.
ETA_DA_TOGLIERE = "?"

VERSIONE_LETTURA = "1.8.0"


def _impronta_sorgente(conn: sqlite3.Connection) -> tuple:
    """Un'impronta delle tabelle da cui il corpus dipende.

    Non un checksum riga per riga, che su cinquantamila righe costerebbe
    quasi quanto rileggerle: quante sono, fin dove arrivano gli
    identificatori, e la somma delle lunghezze dei campi che contano.
    Basta a distinguere un dataset ricostruito da uno solo riletto.
    """
    riga = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0), "
        "COALESCE(SUM(LENGTH(COALESCE(nome, '')) + LENGTH(COALESCE(cognome, '')) "
        "+ LENGTH(COALESCE(eta, '')) + LENGTH(COALESCE(professione, ''))), 0) "
        "FROM persone"
    ).fetchone()
    atti = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM atti"
    ).fetchone()
    return tuple(riga) + tuple(atti)


def sottoinsieme(corpus: Corpus, menzioni: list) -> Corpus:
    """Un corpus ridotto a queste menzioni, con le frequenze rifatte.

    Le frequenze **devono** essere ricalcolate, e non e' un dettaglio da
    poco: i pesi sono ``log10(m * totale / frequenza)``, quindi tenere le
    frequenze del corpus intero accanto al totale del sottoinsieme
    abbassa ogni peso di ``log10(intero/parte)``. Su un settimo del
    corpus fa 1,2 ban su ogni indizio — abbastanza da far sembrare che
    l'algoritmo non unisca niente, quando invece e' il metro a essere
    sbagliato. E' successo, e questa funzione esiste per quello.
    """
    import dataclasses

    tenute = sorted(menzioni, key=lambda m: m.id)
    return dataclasses.replace(
        corpus,
        menzioni=tenute,
        per_id={m.id: m for m in tenute},
        per_atto={
            atto: [m for m in gruppo if m.id in {t.id for t in tenute}]
            for atto, gruppo in corpus.per_atto.items()
        },
        frequenze_cognome=_conta(m.chiave_cognome for m in tenute),
        frequenze_nome=_conta(m.chiave_nome for m in tenute),
        frequenze_parte_nome=_conta(p for m in tenute for p in m.parti_nome),
        frequenze_professione=_conta(
            paleografia.normalizza(m.professione) for m in tenute if m.professione
        ),
        frequenze_contrada=_conta(
            paleografia.normalizza(m.via or m.residenza or "") for m in tenute
        ),
    )


def _conta(valori) -> Counter[str]:
    contatore: Counter[str] = Counter()
    for valore in valori:
        if valore:
            contatore[valore] += 1
    return contatore


def fatti_dalla_menzione(
    menzione: lettura_atti.Menzione, individuo: int, vocabolari: dict | None = None
) -> list[modello.Fatto]:
    """I fatti documentari che una riga afferma, uno per campo.

    Ogni fatto porta con se' la sua fonte e il suo anno, ed e' questo che
    permette a una persona di essere bovaro nel 1850 e contadino nel 1875
    senza che il secondo cancelli il primo. La differenza fra un
    cambiamento di mestiere e un errore di lettura non si vede sul campo:
    si vede sulla **serie**, e la serie esiste solo se i valori non si
    sovrascrivono.
    """
    fatti: list[modello.Fatto] = []

    def aggiungi(tipo: str, grezzo, interpretato=None, confidenza: float = 1.0) -> None:
        if grezzo in (None, ""):
            return
        fatti.append(modello.Fatto(
            individuo=individuo,
            tipo=tipo,
            valore=modello.Valore(
                grezzo=str(grezzo),
                normalizzato=paleografia.normalizza(str(grezzo)) or None,
                interpretato=(
                    str(interpretato)
                    if interpretato is not None and str(interpretato) != str(grezzo)
                    else None
                ),
                confidenza=confidenza,
                stato=modello.stato_da_confidenza(confidenza),
            ),
            menzione=menzione.id,
            atto=menzione.atto,
            anno=menzione.anno or None,
            confidenza=confidenza,
        ))

    # La lettura incerta vale meno, e lo dice: e' il marcatore che il
    # modello ha messo lui sulla pagina che non riusciva a leggere.
    fiducia = 0.7 if menzione.incerto else 1.0
    aggiungi("nome", menzione.nome_letto, menzione.nome, fiducia)
    aggiungi("cognome", menzione.cognome_letto, menzione.cognome, fiducia)
    aggiungi("ruolo", menzione.ruolo)
    # Mestieri e contrade portano anche la forma che il vocabolario del
    # paese propone per loro: 'bovajo' letto come 'bovaro'. E' una
    # proposta e sta nel campo dell'interpretazione — il grezzo, qui
    # accanto, non si tocca.
    vocabolari = vocabolari or {}
    aggiungi(
        "professione", menzione.professione,
        _interpretato(vocabolari.get("professione"), menzione.professione),
    )
    aggiungi("residenza", menzione.residenza)
    aggiungi(
        "contrada", menzione.via,
        _interpretato(vocabolari.get("contrada"), menzione.via),
    )
    aggiungi("stato civile", menzione.stato_vitale)
    aggiungi("patronimico", menzione.patronimico)
    if menzione.eta is not None:
        aggiungi(
            "eta", menzione.eta_letta, menzione.eta.anni,
            0.75 if menzione.eta.approssimata else 0.95,
        )
    if menzione.nascita_certa is not None:
        aggiungi("nascita", menzione.nascita_certa)
    if menzione.tipo_atto == "morte" and menzione.ruolo in ("defunto", "defunta"):
        aggiungi("morte", menzione.anno)
    return fatti


def _interpretato(vocabolario, grezzo: str | None) -> str | None:
    """La forma che il vocabolario propone, se diversa da quella letta."""
    if vocabolario is None or not grezzo:
        return None
    proposta = vocabolario.interpreta(grezzo)
    if not proposta or proposta == paleografia.normalizza(grezzo):
        return None
    return proposta
