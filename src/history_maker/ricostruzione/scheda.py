"""La scheda: un gruppo di menzioni e tutto cio' che ne consegue.

Una scheda **non e' una persona**: e' l'ipotesi corrente che quelle
menzioni parlino della stessa persona. La differenza non e' filosofica.
Una persona ha un nome; una scheda ha l'elenco dei nomi con cui e' stata
letta, quante volte ciascuno, e quale il sistema mostra oggi. Una persona
ha una professione; una scheda ha una serie di professioni datate. Una
persona esiste; una scheda si puo' fondere con un'altra e si puo'
dividere in due, e ogni volta resta scritto perche'.

La chiave di una scheda e' l'identificatore della sua **menzione piu'
antica**. Non e' un dettaglio tecnico: rinumerare le persone a ogni
ricostruzione — come faceva la fase 6 precedente — rende impossibile sia
l'annotazione umana sia l'audit trail, perche' il '#2396' di ieri oggi e'
un'altra persona. Con questa regola una fusione conserva la chiave della
piu' antica delle due, e una scheda che non cambia mantiene il suo nome
per sempre.

Un'avvertenza sul perche' i valori sono **liste e non singoli**. Se una
scheda tenesse un solo ``anno di morte``, una fusione sbagliata lo
sovrascriverebbe e l'incoerenza sparirebbe insieme alla prova che c'era:
la scheda risulterebbe sana. Tenendoli tutti, la contraddizione resta
visibile, e la separazione puo' rimediare a una fusione che non andava
fatta. E' la condizione perche' il sistema possa **tornare indietro**.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass, field

from history_maker import menzioni as lettura_atti, nomi, paleografia
from history_maker.ricostruzione import modello

# I ruoli da cui il sesso si sa senza margine di dubbio: una madre e' una
# donna qualunque nome porti. Dal nome invece si deduce, e una deduzione
# non puo' diventare un veto.
RUOLI_CERTI = frozenset(nomi.RUOLI_MASCHILI | nomi.RUOLI_FEMMINILI)
# Quello che 'Scheda.separati_a_mano' rende per il 99,9% delle schede: il
# controllo gira un milione e mezzo di volte, e non deve costruire niente.
_NESSUNA: frozenset = frozenset()


@dataclass
class Scheda:
    """Un'ipotesi di persona, con tutte le sue letture."""

    chiave: int
    menzioni: list = field(default_factory=list)

    # --- come si chiama --------------------------------------------------
    nomi: Counter = field(default_factory=Counter)
    cognomi: Counter = field(default_factory=Counter)
    # Le stesse forme ridotte a chiave canonica, **contate**. Il conteggio
    # non e' un di piu': serve a distinguere la grafia con cui una scheda
    # e' scritta di solito da quella che compare una volta sola, e senza
    # quella distinzione una scheda ricca di varianti finisce per
    # somigliare a chiunque. Vedi 'principali'.
    chiavi_nome: set = field(default_factory=set)
    chiavi_cognome: set = field(default_factory=set)
    conteggi_nome: Counter = field(default_factory=Counter)
    conteggi_cognome: Counter = field(default_factory=Counter)
    parti_nome: set = field(default_factory=set)
    alternative_nome: set = field(default_factory=set)
    alternative_cognome: set = field(default_factory=set)

    # --- chi e' -----------------------------------------------------------
    sesso: str | None = None
    sesso_certo: bool = False
    professioni: Counter = field(default_factory=Counter)
    contrade: Counter = field(default_factory=Counter)
    residenze: Counter = field(default_factory=Counter)
    uffici: set = field(default_factory=set)
    patronimici: set = field(default_factory=set)
    ruoli: Counter = field(default_factory=Counter)

    # --- quando -----------------------------------------------------------
    # Tutti gli anni di nascita che vengono da un atto di nascita, e tutti
    # quelli di morte che vengono da un atto di morte. Al plurale apposta:
    # se ce n'e' piu' di uno, la scheda e' sbagliata e deve poterlo dire.
    nascite_certe: list = field(default_factory=list)
    morti: list = field(default_factory=list)
    # Gli EVENTI, non gli anni: due atti di nascita distinti sono due
    # persone anche quando cadono nello stesso anno, e a Torrebruna
    # capita — la nascita n. 30 e la n. 34 del 1834 sono due bambini che
    # si chiamano tutti e due Giuseppe. Il confronto sugli anni, con la
    # sua tolleranza, non poteva vederlo. Il numero e' quello
    # dell'evento (vedi 'menzioni.atti_gemelli'), cosi' lo stesso atto
    # letto due volte resta uno.
    atti_di_nascita: set = field(default_factory=set)
    atti_di_morte: set = field(default_factory=set)
    nascite_stimate: list = field(default_factory=list)   # (anno, approssimata)
    # Gli stessi anni, tenuti in ordine mentre entrano. La mediana e il
    # controllo di concordia si chiedono a ogni confronto — un milione
    # e mezzo di volte — e riordinare la lista ogni volta costava
    # quattro volte il primo giro.
    anni_stimati: list = field(default_factory=list)
    finestra: tuple | None = None
    morta_entro: int | None = None
    # Gli anni in cui compare come genitore in un atto di NASCITA: gli
    # anni in cui ha davvero avuto un figlio, che sono l'unico dato su
    # cui l'arco fertile si possa misurare.
    parti: list = field(default_factory=list)
    # Le date dei parti, con l'atto che le dichiara. Servono a un
    # controllo che sugli anni non si puo' fare: due nascite in anni
    # diversi possono distare due mesi, e due mesi non sono due parti.
    parti_date: list = field(default_factory=list)
    anni: list = field(default_factory=list)
    # Gli anni in cui risulta **presente e viva**: e' il vincolo che
    # impedisce di attaccare a un uomo un atto di dieci anni dopo il suo
    # funerale.
    anni_presente: list = field(default_factory=list)
    # L'anno piu' tardi in cui questa persona puo' essere nata, dedotto
    # dai ruoli che ricopre: chi fa da testimone nel 1834 non e' nato nel
    # 1830. Sta qui, aggregato, e non si ricava dalle menzioni a ogni
    # confronto: e' un controllo che gira un milione e mezzo di volte, e
    # scorrere le menzioni ogni volta l'ha reso il collo di bottiglia
    # dell'intera fase.
    nascita_al_piu_tardi: int | None = None
    # E l'altro capo: chi fa da sposo nel 1834 non e' nato nel 1740.
    nascita_al_piu_presto: int | None = None
    # Quale ruolo, e in che anno, ha fissato ciascuno dei due limiti:
    # serve a dirlo in italiano quando il limite fa scattare un veto.
    ruolo_del_limite: tuple | None = None
    ruolo_del_limite_alto: tuple | None = None
    atti: set = field(default_factory=set)
    # Gli **eventi**: gli atti, ma con i gemelli ricondotti a uno solo.
    # Un matrimonio scritto su tre pagine e' un evento, non tre, e due
    # affissioni della stessa promessa nemmeno. Vedi
    # ``menzioni.atti_gemelli``.
    eventi: set = field(default_factory=set)
    # Gli identificatori delle menzioni, per i confronti che devono
    # essere immediati.
    ids: set = field(default_factory=set)
    # Le menzioni che questa scheda non puo' accogliere, perche' una
    # decisione ha stabilito che appartengono a un'altra persona. E'
    # quasi sempre vuoto, e per questo il controllo costa niente:
    # 'if scheda.vietati' e' falso per il 99,9% delle schede.
    vietati: set = field(default_factory=set)
    # Le separazioni decise guardando la pagina, non da una divisione del
    # calcolo: il calcolo puo' tornare sui propri passi, una persona no.
    # Vedi 'evidenza.veti'. Si tengono **riga per riga** — per ogni
    # menzione della scheda, le menzioni con cui non puo' stare — perche'
    # una decisione parla di righe, non di schede. Tenute per scheda, a
    # ogni divisione i divieti passavano a righe che la decisione non
    # nominava: il dichiarante del 1825 staccato da Lorenzo Marianacci si
    # portava dietro il divieto contro Loreto, che era proprio lui.
    # 'separati_a_mano' e' la loro somma sulla scheda.
    divieti_a_mano: dict = field(default_factory=dict)
    # I divieti che valgono per tutta la scheda, senza una riga: li mette
    # chi assegna 'separati_a_mano' direttamente.
    _separati_in_blocco: set = field(default_factory=set)
    # I casati che il padre conferma nello stesso atto: vedi
    # 'menzioni.casati_confermati' e 'evidenza._un_altro_casato'.
    casati_certi: set = field(default_factory=set)
    # Le **forme** del casato che un atto conferma, cioe' che il figlio e
    # suo padre scrivono uguale nella stessa pagina. 'casati_certi' ne
    # tiene le chiavi, per i veti; qui servono le parole come sono
    # scritte, perche' sono quelle che la scheda mostra. Vedi
    # 'cognome_migliore'.
    cognomi_certi: Counter = field(default_factory=Counter)

    # --- i legami come nomi ricopiati -------------------------------------
    padri_nome: set = field(default_factory=set)
    madri_nome: set = field(default_factory=set)
    coniugi_nome: set = field(default_factory=set)
    # Le righe vere a cui i legami puntano dentro l'atto: servono a
    # tradurre i legami in identita' quando le schede ci sono.
    padri_menzione: set = field(default_factory=set)
    madri_menzione: set = field(default_factory=set)
    coniugi_menzione: set = field(default_factory=set)

    # --- i legami come persone gia' riconosciute --------------------------
    # Si riempiono fra un giro e l'altro. Sono l'evidenza piu' forte che
    # questo sistema abbia, perche' non passano per nessun nome
    # ricopiato: 'suo padre e' **quella** scheda'.
    padri: set = field(default_factory=set)
    madri: set = field(default_factory=set)
    coniugi: set = field(default_factory=set)
    figli: set = field(default_factory=set)

    # --- la storia della scheda -------------------------------------------
    incerte: set = field(default_factory=set)
    prove: list = field(default_factory=list)
    confidenza: float = 1.0

    # -----------------------------------------------------------------
    @classmethod
    def dalla_menzione(cls, menzione, chiavi) -> "Scheda":
        scheda = cls(chiave=menzione.id)
        scheda.aggiungi(menzione, chiavi)
        return scheda

    @classmethod
    def unione(cls, una: "Scheda", altra: "Scheda", chiavi) -> "Scheda":
        """Le due schede in una sola, con la chiave della piu' antica."""
        scheda = cls(
            chiave=min(una.chiave, altra.chiave),
            menzioni=sorted(una.menzioni + altra.menzioni, key=lambda m: m.id),
        )
        scheda.incerte = set(una.incerte) | set(altra.incerte)
        scheda.prove = list(una.prove) + list(altra.prove)
        # Le divisioni gia' decise sopravvivono alla fusione: unire A e B
        # non autorizza nessuna delle due a riprendersi C.
        scheda.vietati = (una.vietati | altra.vietati) - scheda.ids
        for fonte in (una, altra):
            for riga, vietate in fonte.divieti_a_mano.items():
                scheda.vieta(riga, vietate)
        scheda._separati_in_blocco = (
            una._separati_in_blocco | altra._separati_in_blocco
        ) - scheda.ids
        scheda.ricalcola(chiavi)
        return scheda

    # --- le separazioni decise sulla pagina -----------------------------
    @property
    def separati_a_mano(self):
        """Le menzioni con cui questa scheda non puo' stare, per decisione."""
        if not self.divieti_a_mano and not self._separati_in_blocco:
            return _NESSUNA
        tutte = set(self._separati_in_blocco)
        for vietate in self.divieti_a_mano.values():
            tutte |= vietate
        return tutte - self.ids

    @separati_a_mano.setter
    def separati_a_mano(self, valore) -> None:
        self.divieti_a_mano = {}
        self._separati_in_blocco = set(valore)

    def vieta(self, riga: int, altre) -> None:
        """La riga non puo' stare con nessuna delle altre."""
        if altre:
            self.divieti_a_mano.setdefault(riga, set()).update(altre)

    def eredita_divieti(self, da: "Scheda") -> None:
        """I divieti delle sole righe che questa scheda porta via da un'altra."""
        self.divieti_a_mano = {
            riga: set(vietate) for riga, vietate in da.divieti_a_mano.items()
            if riga in self.ids
        }
        self._separati_in_blocco = set(da._separati_in_blocco)

    # -----------------------------------------------------------------
    def aggiungi(self, menzione, chiavi) -> None:
        """Attacca una menzione, aggiornando gli aggregati."""
        self.menzioni.append(menzione)
        self._assorbi(menzione, chiavi)

    def ricalcola(self, chiavi) -> None:
        """Ricostruisce tutti gli aggregati dalle sole menzioni.

        Serve dopo una fusione o una separazione. Passa per lo stesso
        ``_assorbi`` di :meth:`aggiungi`, e non per una seconda copia
        della stessa logica: due strade che devono dare lo stesso
        risultato prima o poi non lo danno piu'.
        """
        self._azzera()
        for menzione in self.menzioni:
            self._assorbi(menzione, chiavi)

    def _azzera(self) -> None:
        self.nomi = Counter()
        self.cognomi = Counter()
        self.chiavi_nome = set()
        self.chiavi_cognome = set()
        self.conteggi_nome = Counter()
        self.conteggi_cognome = Counter()
        self.parti_nome = set()
        self.alternative_nome = set()
        self.alternative_cognome = set()
        self.professioni = Counter()
        self.contrade = Counter()
        self.residenze = Counter()
        self.uffici = set()
        self.patronimici = set()
        self.ruoli = Counter()
        self.nascite_certe = []
        self.morti = []
        self.atti_di_nascita = set()
        self.atti_di_morte = set()
        self.nascite_stimate = []
        self.anni_stimati = []
        self.parti = []
        self.parti_date = []
        self.anni = []
        self.anni_presente = []
        self.nascita_al_piu_tardi = None
        self.nascita_al_piu_presto = None
        self.ruolo_del_limite = None
        self.ruolo_del_limite_alto = None
        self.atti = set()
        self.eventi = set()
        self.ids = set()
        self.padri_nome = set()
        self.madri_nome = set()
        self.coniugi_nome = set()
        self.padri_menzione = set()
        self.madri_menzione = set()
        self.coniugi_menzione = set()
        self.finestra = None
        self.morta_entro = None
        self.casati_certi = set()
        self.cognomi_certi = Counter()
        self.sesso = None
        self.sesso_certo = False

    def _assorbi(self, menzione, chiavi) -> None:
        if menzione.nome:
            self.nomi[menzione.nome] += 1
            self.chiavi_nome.add(menzione.chiave_nome)
            self.conteggi_nome[menzione.chiave_nome] += 1
            self.parti_nome.update(menzione.parti_nome)
        if menzione.cognome:
            self.cognomi[menzione.cognome] += 1
            self.chiavi_cognome.add(menzione.chiave_cognome)
            self.conteggi_cognome[menzione.chiave_cognome] += 1
            if getattr(menzione, "casato_confermato", False):
                self.casati_certi.add(menzione.chiave_cognome)
                self.cognomi_certi[menzione.cognome] += 1
        for forma in menzione.alternative_nome:
            self.alternative_nome.add(paleografia.forma_canonica(forma))
        for forma in menzione.alternative_cognome:
            self.alternative_cognome.add(paleografia.forma_canonica(forma))

        # Mestieri e contrade entrano gia' ricondotti alla forma del
        # vocabolario: e' cosi' che 'Agrimenfore' e 'Agrimensore' contano
        # come lo stesso mestiere quando due schede si confrontano.
        if menzione.professione:
            forma = _canonica(chiavi, "mestiere", menzione.professione)
            if forma:
                self.professioni[forma] += 1
        if menzione.residenza:
            self.residenze[paleografia.normalizza(menzione.residenza)] += 1
        contrada = menzione.via or menzione.residenza
        if contrada:
            forma = _canonica(chiavi, "contrada", contrada)
            if forma:
                self.contrade[forma] += 1
        if menzione.patronimico:
            self.patronimici.add(paleografia.forma_canonica(menzione.patronimico))
        if menzione.ruolo:
            self.ruoli[menzione.ruolo] += 1
        if menzione.ruolo == "ufficiale":
            # Il ripiego sul ruolo non e' pigrizia: la professione del
            # sindaco manca in centinaia di atti, e senza ripiego le sue
            # firme si spezzerebbero proprio dove il registro e' piu'
            # sbrigativo.
            self.uffici.add(
                paleografia.normalizza(menzione.professione or menzione.ruolo)
            )

        if menzione.sesso and not self.sesso_certo:
            if menzione.ruolo in RUOLI_CERTI:
                self.sesso = menzione.sesso
                self.sesso_certo = True
            elif self.sesso is None:
                self.sesso = menzione.sesso

        if menzione.anno:
            self.anni.append(menzione.anno)
            if menzione.presente:
                self.anni_presente.append(menzione.anno)
            # L'eta' minima di un ruolo vale sempre: chi e' nominato
            # come madre in un atto del 1847 non e' nato nel 1849. Il
            # tetto invece, per 'padre' e 'madre', vale solo in un atto
            # di nascita — altrove sono un legame, non un parto.
            ruolo = menzione.ruolo
            minima = modello.ETA_MINIMA_RUOLO.get(ruolo)
            if (
                ruolo in modello.RUOLI_LEGATI_AL_PARTO
                and menzione.tipo_atto != "nascita"
            ):
                ruolo = ""
            if minima is not None:
                limite = menzione.anno - minima
                if self.nascita_al_piu_tardi is None or limite < self.nascita_al_piu_tardi:
                    self.nascita_al_piu_tardi = limite
                    self.ruolo_del_limite = (menzione.ruolo, menzione.anno)
            massima = modello.ETA_MASSIMA_RUOLO.get(ruolo)
            if massima is not None:
                limite = menzione.anno - massima
                if (
                    self.nascita_al_piu_presto is None
                    or limite > self.nascita_al_piu_presto
                ):
                    self.nascita_al_piu_presto = limite
                    self.ruolo_del_limite_alto = (ruolo, menzione.anno)
        self.atti.add(menzione.atto)
        self.eventi.add(menzione.evento or menzione.atto)
        self.ids.add(menzione.id)

        if menzione.nascita_certa is not None:
            self.nascite_certe.append(menzione.nascita_certa)
            self.atti_di_nascita.add(menzione.evento or menzione.atto)
        stimata = menzione.anno_nascita
        if stimata is not None and menzione.eta is not None:
            self.nascite_stimate.append((stimata, menzione.eta.approssimata))
            bisect.insort(self.anni_stimati, stimata)
        if menzione.finestra is not None:
            self.finestra = _interseca(self.finestra, menzione.finestra)

        if menzione.tipo_atto == "morte" and menzione.ruolo in ("defunto", "defunta"):
            self.atti_di_morte.add(menzione.evento or menzione.atto)
            if menzione.anno:
                self.morti.append(menzione.anno)
        elif (menzione.stato_vitale or "").startswith("defunt"):
            if menzione.anno and (
                self.morta_entro is None or menzione.anno < self.morta_entro
            ):
                self.morta_entro = menzione.anno

        if (
            menzione.tipo_atto == "nascita"
            and menzione.ruolo in lettura_atti.GENITORI
            and menzione.anno
        ):
            self.parti.append(menzione.anno)
            if menzione.data:
                self.parti_date.append((menzione.data, menzione.atto))
        elif menzione.parto_implicito is not None:
            # Il parto che l'atto documenta senza essere un atto di
            # nascita: la madre di un defunto di cinquantotto anni ha
            # partorito cinquantotto anni fa, e la sua scheda deve
            # portarselo dietro come tutti gli altri.
            self.parti.append(menzione.parto_implicito)

        if menzione.padre is not None:
            self.padri_menzione.add(menzione.padre)
            self.padri_nome.update(chiavi.padre(menzione))
        if menzione.madre is not None:
            self.madri_menzione.add(menzione.madre)
            self.madri_nome.update(chiavi.madre(menzione))
        if menzione.coniuge is not None:
            self.coniugi_menzione.add(menzione.coniuge)
            self.coniugi_nome.update(chiavi.coniuge(menzione))

    # -----------------------------------------------------------------
    @property
    def nascita_certa(self) -> int | None:
        """L'anno di nascita dall'atto di nascita, quando ce n'e' uno solo."""
        return min(self.nascite_certe) if self.nascite_certe else None

    @property
    def morte(self) -> int | None:
        return min(self.morti) if self.morti else None


    @property
    def anno_nascita(self) -> int | None:
        """L'anno di nascita: l'atto se c'e', altrimenti la mediana delle eta'.

        Viene la tentazione di correggere la mediana con i **ruoli**, che
        sono fatti documentari e dicono qualcosa su quando una persona
        puo' essere nata: chi si sposa nel 1834 non e' nato nel 1830,
        qualunque eta' il registro gli attribuisca. E' stato provato, e
        non funziona: spostare la stima dentro la finestra dei ruoli
        toglie centoventi matrimoni a un'eta' impossibile e ne aggiunge
        ventisei di genitori troppo vecchi, perche' rendere qualcuno piu'
        vecchio al matrimonio lo rende piu' vecchio anche alla nascita
        dei figli. La contraddizione non sparisce: **cambia posto**.

        Quando i dati si contraddicono, quindi, la stima resta quella che
        dicono i dati e la contraddizione resta scritta fra le anomalie.
        E' l'unica cosa onesta da fare: aggiustare il numero perche' il
        controllo passi vorrebbe dire nascondere il problema, non
        risolverlo.
        """
        if self.nascite_certe:
            return min(self.nascite_certe)
        if not self.anni_stimati:
            return None
        return self.anni_stimati[len(self.anni_stimati) // 2]

    def nascita_solida(self, vicino: int = 5) -> bool:
        """Se l'anno di nascita regge il peso di un veto.

        Distingue i due modi di arrivare a un'eta' impossibile, che
        chiedono rimedi opposti. Se l'anno viene da **una sola** eta'
        dichiarata, il colpevole probabile e' quella lettura, e vietare
        una fusione per una parola letta male costa piu' di quanto renda.
        Se viene da un atto di nascita, o da piu' eta' che concordano fra
        loro, allora l'anno e' buono e a non tornare e' l'identita': quel
        ruolo, a quell'eta', e' di un'altra persona.
        """
        if self.nascite_certe:
            return True
        anni = self.anni_stimati
        if len(anni) < 2:
            return False
        mediana = anni[len(anni) // 2]
        concordi = bisect.bisect_right(anni, mediana + vicino) - bisect.bisect_left(
            anni, mediana - vicino
        )
        return concordi >= 2

    @property
    def approssimata(self) -> bool:
        return any(a for _, a in self.nascite_stimate)

    @property
    def anno_primo(self) -> int | None:
        return min(self.anni) if self.anni else None

    @property
    def anno_ultimo(self) -> int | None:
        return max(self.anni) if self.anni else None

    @property
    def quante(self) -> int:
        return len(self.menzioni)

    def nome_migliore(self, attestazione: Counter | None = None) -> str | None:
        return _piu_attestata(self.nomi, attestazione)

    # Provato e scartato: far scegliere alla scheda, fra le forme del suo
    # casato, quella che un atto **conferma** — scritta uguale dal figlio
    # e da suo padre nella stessa pagina — invece della piu' attestata.
    # Nasceva da un caso vero, anzi da trentuno: padre e figlio con due
    # casati diversi sulle schede pur scrivendolo uguale nell'atto che li
    # lega (Monaco e Manes, Lorri e Lozzi, Leandro e Landea, Torzi e
    # Corzi).
    #
    # Misurato: di quei trentuno non se n'e' chiuso **nessuno** — le
    # classi sono rimaste 27, 14, 13, 4, 1 — e in cambio una
    # frammentazione e un coniuge doppio in piu'. Il casato confermato e'
    # frequente (dodicimilasettecento righe su cinquantaduemila), e quando
    # una scheda ne ha piu' d'uno confermato la piu' attestata fra quelle
    # resta la stessa di prima. Il nodo non e' quale forma la scheda
    # mostri: e' che padre e figlio scelgono ognuno per conto suo.

    def cognome_migliore(self, attestazione: Counter | None = None) -> str | None:
        return _piu_attestata(self.cognomi, attestazione)

    def etichetta(self) -> str:
        """Come si chiama questa scheda quando bisogna nominarla in un rapporto."""
        nome = self.nome_migliore() or "?"
        cognome = self.cognome_migliore() or ""
        anno = self.anno_nascita
        pezzo = " ".join(p for p in (nome, cognome) if p)
        return f"{pezzo} n.{anno} [{self.chiave}]" if anno else f"{pezzo} [{self.chiave}]"

    def legami(self) -> set:
        """Tutte le schede a cui questa e' legata da una parentela."""
        return self.padri | self.madri | self.coniugi | self.figli

    def fondamento(self) -> str:
        """Su che cosa si regge questa scheda, in una parola.

        Serve a chi consulta: una scheda che sta in piedi per l'atto di
        nascita e una che sta in piedi perche' nessun altro le somigliava
        non vanno lette allo stesso modo.
        """
        if len(self.menzioni) == 1:
            return "una sola menzione"
        if self.nascite_certe:
            return "atto di nascita"
        if self.padri_nome and self.madri_nome:
            return "genitori dichiarati"
        if self.coniugi_nome:
            return "coniuge dichiarato"
        if self.padri_nome or self.madri_nome:
            return "un genitore dichiarato"
        if self.patronimici:
            return "patronimico"
        if self.uffici:
            return "ufficio ricoperto"
        return "menzioni concordi"


# Sotto questa frazione della forma dominante, una grafia e' un caso
# isolato e non fa testo nel confronto. Serve a una cosa precisa: una
# scheda con venti menzioni raccoglie inevitabilmente qualche lettura
# strampalata, e se ogni lettura strampalata potesse decidere un
# confronto, quella scheda finirebbe per somigliare a chiunque. E'
# successo: 'Maria Nicola D'Ettore', ventidue menzioni, si e' presa una
# sorella perche' fra le sue grafie c'era una volta 'Lucia Antonia'.
#
# Le forme minori NON spariscono: restano negli scaffali dei candidati,
# quindi chi porta quella grafia viene comunque confrontato. Non possono
# solo **provare** l'identita' da sole.
QUOTA_FORMA_PRINCIPALE = 0.25


def principali(conteggi: Counter) -> set:
    """Le grafie con cui una scheda e' scritta di solito.

    >>> sorted(principali(Counter({"lela": 8, "leli": 3, "sela": 1})))
    ['lela', 'leli']
    >>> sorted(principali(Counter({"maria": 1, "anna": 1})))
    ['anna', 'maria']
    """
    if not conteggi:
        return set()
    massimo = max(conteggi.values())
    soglia = max(1, round(QUOTA_FORMA_PRINCIPALE * massimo))
    return {forma for forma, quante in conteggi.items() if quante >= soglia}


def _canonica(chiavi, come: str, valore: str) -> str | None:
    """La forma con cui un mestiere o una contrada si confronta.

    Quando le chiavi non portano il vocabolario — nei test, o nel primo
    giro di un archivio nuovo — si ripiega sulla normalizzazione, che e'
    quello che si faceva prima.
    """
    metodo = getattr(chiavi, come, None)
    return metodo(valore) if metodo else paleografia.normalizza(valore)


def _interseca(una: tuple | None, altra: tuple | None) -> tuple | None:
    if una is None:
        return altra
    if altra is None:
        return una
    return (max(una[0], altra[0]), min(una[1], altra[1]))


def _piu_attestata(valori: Counter, attestazione: Counter | None) -> str | None:
    """La forma da mostrare fra quelle con cui la scheda e' stata letta.

    Contare solo le occorrenze **sue** non basta: la moglie di Domenico
    Lella e' letta 'Di Nardo' una volta e 'Doro' due. A maggioranza
    vincerebbe 'Doro', che nel secolo compare una manciata di volte,
    mentre 'Di Nardo' e' una delle famiglie del paese. Ogni forma vale
    quindi per quante volte questa scheda la porta, moltiplicato per
    quanto quella forma e' attestata in tutto l'archivio; il logaritmo
    tiene la seconda parte al suo posto, perche' deve rompere i pareggi,
    non ribaltare una lettura ripetuta cinque volte.
    """
    if not valori:
        return None
    if attestazione is None:
        return valori.most_common(1)[0][0]

    def peso(forma: str):
        return (
            valori[forma] * (1.0 + math.log10(attestazione.get(forma, 0) + 1)),
            valori[forma],
            forma,
        )

    return max(sorted(valori), key=peso)
