"""Le entita' della ricostruzione, e dove vivono.

Il modello non e' ``PERSONA -> nome, cognome, via, professione``. Quello
e' il modello che fa perdere l'informazione: sovrascrive, sceglie da solo,
e quando due atti dicono cose diverse ne resta una sola senza che si sappia
piu' quale fosse l'altra.

Qui la catena e' piu' lunga, e ogni anello serve::

    ATTO                  una pagina di registro, con la sua immagine
      |
    MENZIONE              una riga di quell'atto: 'Domenico Lelli,
      |                   contadino, trentotto anni, marito di Angela'
      |
    FATTO                 un'affermazione ricavata dalla menzione, con la
      |                   sua fonte, il suo anno e la sua confidenza:
      |                   professione='bovaro' nel 1850 secondo l'atto 412
      |
    INDIVIDUO             la persona reale che il sistema ricostruisce
      |
    RELAZIONE             padre, madre, coniuge, figlio: fra individui,
                          non fra nomi ricopiati

Attorno a questa catena stanno le quattro cose che rendono la
ricostruzione una ricostruzione invece di un elenco:

``EVIDENZA``    perche' si e' concluso qualcosa, indizio per indizio, con
                il peso di ciascuno.
``ANOMALIA``    cio' che non torna, con la sua gravita' e il suo impatto
                sul resto dell'albero.
``DECISIONE``   cosa e' stato deciso, da chi, con quale versione di quale
                algoritmo o modello. Si puo' rileggere e si puo' disfare.
``VERIFICA``    una domanda mirata all'immagine originale, con la
                risposta e il suo costo.

Nessuna di queste tabelle contiene la trascrizione: quella resta dov'e',
nella fase 4, e non viene mai riscritta. Da qui in poi si costruisce
soltanto **sopra**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Gli stati dell'incertezza
# ---------------------------------------------------------------------------
#
# Non forzare sempre un valore e' meta' del mestiere. Un sistema che
# risponde sempre 'si' o 'no' costringe chi consulta a fidarsi o a
# rifare tutto; uno che sa dire 'non lo so' gli dice dove guardare.

CONFERMATO = "confermato"
PROBABILE = "probabile"
POSSIBILE = "possibile"
IRRISOLTO = "irrisolto"

# Per l'identita' c'e' uno stato in piu', e non e' una sfumatura: dire
# 'non so se sono la stessa persona' e' diverso da dire '**so** che sono
# due omonimi diversi'. Il primo e' ignoranza, il secondo e' un
# risultato.
IDENTITA_CONFERMATA = "identita confermata"
IDENTITA_PROBABILE = "identita probabile"
IDENTITA_POSSIBILE = "identita possibile"
IDENTITA_IRRISOLTA = "identita irrisolta"
POSSIBILE_OMONIMO = "possibile omonimo"

STATI_IDENTITA = (
    IDENTITA_CONFERMATA, IDENTITA_PROBABILE, IDENTITA_POSSIBILE,
    IDENTITA_IRRISOLTA, POSSIBILE_OMONIMO,
)


def stato_da_confidenza(confidenza: float) -> str:
    """Traduce una probabilita' in una delle quattro parole.

    Le soglie non sono sacre — si spostano in configurazione — ma il
    principio si': fra 'probabile' e 'possibile' ci deve essere un salto
    visibile, altrimenti chi legge il rapporto tratta tutto allo stesso
    modo e tanto valeva non distinguerli.
    """
    if confidenza >= 0.95:
        return CONFERMATO
    if confidenza >= 0.80:
        return PROBABILE
    if confidenza >= 0.55:
        return POSSIBILE
    return IRRISOLTO


# ---------------------------------------------------------------------------
# Un valore letto, normalizzato, interpretato
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Valore:
    """Le tre facce di un dato, che non vanno mai confuse.

    ``grezzo``       cio' che la trascrizione dice: 'Lelli'.
    ``normalizzato`` la stessa cosa in forma confrontabile: 'leli'. Serve
                     alla macchina, non a chi legge.
    ``interpretato`` cio' che probabilmente c'era scritto sull'atto:
                     'Lella', se il resto del grafo lo sostiene.

    Il grezzo non si tocca mai. Un'interpretazione che cancella la
    lettura originale non e' una correzione, e' una perdita: domani un
    altro indizio potrebbe dire che quella lettura era giusta, e senza il
    grezzo non si potrebbe piu' tornare indietro.
    """

    grezzo: str | None
    normalizzato: str | None = None
    interpretato: str | None = None
    confidenza: float = 1.0
    stato: str = CONFERMATO
    motivo: str = ""

    @property
    def migliore(self) -> str | None:
        """Il valore da mostrare: l'interpretazione se c'e', altrimenti il letto."""
        return self.interpretato if self.interpretato is not None else self.grezzo


# ---------------------------------------------------------------------------
# I fatti
# ---------------------------------------------------------------------------

# I tipi di fatto che il sistema sa raccogliere. Sono aperti per
# costruzione — aggiungerne uno non rompe niente — ma elencarli qui serve
# a non ritrovarsi con 'professione' e 'mestiere' come due tipi diversi.
TIPI_FATTO = (
    "nome", "cognome", "sesso", "eta", "nascita", "morte", "gia morto",
    "professione", "residenza", "contrada", "stato civile", "ruolo",
    "patronimico",
)


@dataclass
class Fatto:
    """Un'affermazione documentaria su una persona, con la sua data.

    E' l'unita' che permette a una persona di avere tre professioni senza
    che nessuna cancelli le altre: sono tre fatti, ciascuno con il suo
    anno e la sua fonte, e messi in fila sono una biografia.
    """

    individuo: int
    tipo: str
    valore: Valore
    menzione: int
    atto: int
    anno: int | None = None
    confidenza: float = 1.0

    @property
    def chiave(self) -> tuple:
        return (self.individuo, self.tipo, self.valore.grezzo, self.menzione)


# ---------------------------------------------------------------------------
# Le relazioni
# ---------------------------------------------------------------------------

TIPI_RELAZIONE = ("padre", "madre", "coniuge", "figlio")

# L'eta' minima che un ruolo implica. Non e' una statistica sui registri:
# e' cio' che il ruolo **significa**. Un neonato non fa da testimone, e
# chi si sposa non ha tre anni.
#
# Serve a un caso preciso e frequente: quando l'atto non dichiara nessuna
# eta', questo e' l'unico vincolo che impedisce di attaccare l'atto di
# nascita di un bambino alla vita di un adulto che porta il suo nome — ed
# e' un errore che si propaga, perche' quella scheda finisce per avere due
# padri, due madri e un matrimonio a zero anni.
ETA_MINIMA_RUOLO = {
    "sposo": 12, "sposa": 12, "marito": 12, "moglie": 12, "coniuge": 12,
    "padre": 12, "madre": 12,
    "dichiarante": 14, "testimone": 14,
    "ufficiale": 18, "levatrice": 18,
}

# E l'altro capo. Piu' largo, perche' di qua l'eccezione esiste — ci sono
# padri di settant'anni e testimoni di novanta — ma non e' infinita: a
# centoquindici anni non si fa da testimone a niente.
ETA_MASSIMA_RUOLO = {
    "sposo": 80, "sposa": 75, "marito": 85, "moglie": 80,
    "padre": 78, "madre": 55,
    "dichiarante": 95, "testimone": 95, "ufficiale": 90, "levatrice": 85,
}

# Per 'padre' e 'madre' il limite **superiore** vale solo in un atto di
# nascita, e la distinzione costa cara a chi la salta. 'Madre' in un atto
# di morte non vuol dire che quella donna abbia partorito quell'anno:
# vuol dire che il defunto era suo figlio, e il figlio poteva avere
# sessant'anni. Applicando il tetto dei cinquantacinque anni anche li' si
# vietavano duecento fusioni buone e i coniugi duplicati raddoppiavano.
#
# Il limite **inferiore** invece vale sempre, e va tenuto: una donna
# nominata come madre in un atto del 1847 non puo' essere nata nel 1849,
# in nessun tipo di atto. Toglierlo insieme all'altro lasciava passare
# proprio quel caso.
RUOLI_LEGATI_AL_PARTO = frozenset({"padre", "madre"})


@dataclass
class Relazione:
    """Un legame fra due individui, con l'atto che lo dichiara."""

    da: int
    a: int
    tipo: str
    atto: int | None = None
    anno: int | None = None
    confidenza: float = 1.0
    stato: str = CONFERMATO
    motivo: str = ""


# ---------------------------------------------------------------------------
# L'evidenza
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Indizio:
    """Un pezzo di prova, con quanto sposta la bilancia.

    ``peso`` e' in **ban**, cioe' logaritmi in base dieci del rapporto di
    verosimiglianza: +1 vuol dire 'dieci volte piu' probabile se sono la
    stessa persona', -1 il contrario. Sommarli e' legittimo perche' sono
    logaritmi, e leggere il totale e' immediato: +3 e' mille contro uno.

    Perche' non pesi scelti a mano. Un peso a mano dice quanto **chi ha
    scritto il codice** crede a quell'indizio; un rapporto di
    verosimiglianza dice quanto quell'indizio e' raro **in questo paese**.
    A Torrebruna 'Pelliccia' sono 5.121 menzioni su 49.889: due Pelliccia
    non sono un indizio, sono la normalita'. 'Genualdi' compare sei
    volte: due Genualdi sono quasi certamente parenti. Nessun peso fisso
    puo' distinguere i due casi, ed e' esattamente la ragione per cui il
    sistema precedente non ci riusciva.
    """

    tipo: str
    peso: float
    dettaglio: str = ""

    @property
    def pro(self) -> bool:
        return self.peso > 0


# L'a priori in ban: quanto sono probabili due menzioni della stessa
# persona **prima** di guardare qualunque indizio.
#
# Il numero non e' un'opinione, si conta. In un archivio di cinquantamila
# menzioni le coppie possibili sono un miliardo e un quarto; le coppie che
# sono davvero la stessa persona, contate sulla ricostruzione, sono
# dell'ordine del mezzo milione. Il rapporto e' quattro su diecimila, cioe'
# -3,4 in logaritmo.
#
# Metterlo troppo alto e' l'errore che si paga piu' caro, e vale la pena
# vederlo in numeri. Con -1,3 due menzioni 'Domenico Pelliccia' senza
# nient'altro — nome +1,41, cognome +0,89 — arrivano a +1,0 di logit e si
# uniscono: in un paese dove il primogenito porta il nome del nonno, quello
# vuol dire cucire insieme cinque uomini diversi. Provato: 2.129 fatti
# impossibili contro gli zero del sistema precedente. Con -4,1 la stessa
# coppia sta a -1,8 e resta divisa, che e' la risposta giusta: **il nome
# non basta mai**, e serve dell'altro — i genitori, il coniuge, i figli.
#
# Il valore qui e' solo il ripiego: quello vero lo calcola il modello dal
# corpus (:meth:`evidenza.Modello.dal_corpus`), perche' dipende da quante
# persone ci sono nell'archivio e non da quante ce ne sono a Torrebruna.
A_PRIORI = -4.1


@dataclass
class Evidenza:
    """Tutti gli indizi su una coppia di entita', e il loro totale."""

    indizi: list[Indizio] = field(default_factory=list)
    # L'a priori con cui leggere il totale. Di norma lo mette il modello,
    # che lo ricava dal corpus: su un archivio di quattrocento menzioni
    # due omonimi sono molto piu' probabilmente la stessa persona che su
    # uno di cinquantamila, e una soglia che non lo sapesse funzionerebbe
    # su un solo comune.
    a_priori: float = 0.0
    # Un veto: qualcosa che nessuna quantita' di indizi favorevoli puo'
    # compensare, perche' e' fisicamente impossibile. Sono pochi e sono
    # elencati in 'evidenza.py'.
    impossibile: str | None = None

    def aggiungi(self, tipo: str, peso: float, dettaglio: str = "") -> None:
        if peso:
            self.indizi.append(Indizio(tipo, peso, dettaglio))

    def vieta(self, motivo: str) -> None:
        if self.impossibile is None:
            self.impossibile = motivo

    @property
    def totale(self) -> float:
        if self.impossibile is not None:
            return float("-inf")
        return sum(i.peso for i in self.indizi)

    @property
    def logit(self) -> float:
        """Il totale letto insieme all'a priori: quanto ci si crede, in ban.

        E' il numero su cui si decide, e non il totale grezzo. La
        differenza si vede su archivi di dimensioni diverse: le stesse
        prove valgono di piu' in un comune piccolo, dove gli omonimi
        sono pochi, e meno in uno grande. Decidere sul totale grezzo
        vorrebbe dire tarare le soglie a mano per ogni comune.
        """
        if self.impossibile is not None:
            return float("-inf")
        return (self.a_priori or A_PRIORI) + self.totale

    @property
    def confidenza(self) -> float:
        """Il logit tradotto in probabilita'.

        L'a priori conta: due menzioni prese a caso in un archivio di
        cinquantamila righe **non** sono la stessa persona, e partire da
        mezzo e mezzo vorrebbe dire fondere mezzo paese.
        """
        if self.impossibile is not None:
            return 0.0
        logit = self.logit
        if logit > 12:
            return 1.0
        if logit < -12:
            return 0.0
        odds = 10.0 ** logit
        return odds / (1.0 + odds)

    def pro(self) -> list[Indizio]:
        return sorted([i for i in self.indizi if i.peso > 0], key=lambda i: -i.peso)

    def contro(self) -> list[Indizio]:
        return sorted([i for i in self.indizi if i.peso < 0], key=lambda i: i.peso)

    def racconta(self, quanti: int = 6) -> str:
        """Le prove in italiano, dalla piu' pesante alla piu' leggera."""
        if self.impossibile is not None:
            return f"impossibile: {self.impossibile}"
        pezzi = [
            f"{i.dettaglio or i.tipo} ({i.peso:+.2f})"
            for i in sorted(self.indizi, key=lambda i: -abs(i.peso))[:quanti]
        ]
        return "; ".join(pezzi)


# ---------------------------------------------------------------------------
# Le anomalie
# ---------------------------------------------------------------------------

TIPI_ANOMALIA = (
    "IDENTITY_ANOMALY", "NAME_ANOMALY", "SURNAME_ANOMALY", "AGE_ANOMALY",
    "DATE_ANOMALY", "RELATIONSHIP_ANOMALY", "PROFESSION_ANOMALY",
    "ADDRESS_ANOMALY", "LOCATION_ANOMALY", "MARITAL_ANOMALY",
    "DUPLICATE_PERSON", "POSSIBLE_HOMONYM", "TRANSCRIPTION_ANOMALY",
)


@dataclass
class Anomalia:
    """Qualcosa che non torna, con quanto costa lasciarlo com'e'.

    ``impatto`` e' il numero di persone che la decisione su questa
    anomalia sposterebbe: unire due schede con quaranta discendenti
    ciascuna cambia ottanta schede, unire due testimoni ne cambia due. E'
    la ragione per cui la coda non e' ordinata per confidenza ma per
    dubbio **per** impatto: il caso incerto che muove un ramo intero va
    guardato prima di quello quasi certo che non muove niente.
    """

    tipo: str
    individui: tuple[int, ...]
    descrizione: str
    atti: tuple[int, ...] = ()
    spiegazioni: tuple[str, ...] = ()
    evidenza: Evidenza | None = None
    confidenza: float = 0.5
    impatto: int = 1
    gravita: str = "media"          # alta | media | bassa
    campo: str = ""                 # il campo su cui si dubita, se e' uno solo
    # Le letture fra cui il dubbio oscilla, quando sono poche e note.
    # Servono alla domanda da fare all'immagine: 'e' Lella o Lelli?' si
    # risponde meglio di 'come e' scritto il cognome?'.
    alternative: tuple = ()

    @property
    def priorita(self) -> float:
        """Quanto vale guardare questo caso prima degli altri.

        Il prodotto di tre cose: quanto e' grosso il dubbio (l'incertezza
        e' massima a meta' strada, non agli estremi), quanto muove
        l'albero, e quanto e' grave la categoria.
        """
        dubbio = 1.0 - abs(self.confidenza - 0.5) * 2.0
        peso_gravita = {"alta": 3.0, "media": 1.0, "bassa": 0.3}.get(self.gravita, 1.0)
        return (0.15 + dubbio) * (1.0 + self.impatto) ** 0.5 * peso_gravita


# ---------------------------------------------------------------------------
# Le decisioni
# ---------------------------------------------------------------------------

@dataclass
class Decisione:
    """Cosa e' stato deciso, e tutto quello che serve per disfarlo.

    Il campo che rende questo archivio diverso da un database e' ``disfa``:
    una decisione non si cancella, si **supera**. Se domani un atto nuovo
    dimostra che due schede unite erano due persone, la separazione e' una
    decisione nuova che nomina la vecchia, e la storia resta leggibile per
    intero.
    """

    azione: str                     # unione | separazione | correzione | conferma
    entita: tuple[int, ...]
    motivo: str
    confidenza: float
    evidenze: tuple[str, ...] = ()
    contraddizioni: tuple[str, ...] = ()
    atti: tuple[int, ...] = ()
    decisore: str = "algoritmo"     # algoritmo | claude | gemini | persona
    modello: str = ""
    versione_prompt: str = ""
    versione_algoritmo: str = ""
    quando: str = ""
    disfa: int | None = None
    id: int | None = None


# ---------------------------------------------------------------------------
# Le verifiche sull'immagine
# ---------------------------------------------------------------------------

@dataclass
class Verifica:
    """Una domanda mirata all'immagine originale.

    Non 'ritrascrivi questa pagina' — quello e' gia' stato fatto e
    costerebbe la quota di un giorno per rifare cio' che c'e' gia' — ma
    'il cognome del padre in questo atto e' Lella o Lelli?'. Una domanda
    chiusa su un ritaglio piccolo: e' quello che il modello sa fare
    meglio, ed e' quello che costa meno.
    """

    atto: int
    immagine: str
    campo: str
    # Il campo dei dati su cui la risposta agisce — 'eta', 'cognome',
    # 'professione' — distinto da 'campo', che e' come lo si dice al
    # modello. Senza, una risposta non saprebbe dove tornare.
    campo_dati: str = ""
    domanda: str = ""
    alternative: tuple[str, ...] = ()
    menzione: int | None = None
    bersaglio: str = ""             # la parola attorno a cui ritagliare
    priorita: float = 0.0
    motivo: str = ""
    # Riempiti dopo la chiamata.
    risposta: str = ""
    confidenza: float = 0.0
    stato: str = "in attesa"        # in attesa | risposta | fallita | saltata
    modello: str = ""
    quando: str = ""
    chiave_cache: str = ""


# ---------------------------------------------------------------------------
# Lo schema
# ---------------------------------------------------------------------------
#
# Le prime quattro tabelle hanno gli stessi nomi e le stesse colonne di
# quelle della fase 6 vecchia, piu' qualche colonna in fondo. Non e'
# pigrizia: l'applicazione dell'albero e il controllo di qualita' leggono
# quelle, e un impianto nuovo che obbliga a riscrivere tutto cio' che gli
# sta attorno non si puo' confrontare con quello che sostituisce. Il
# confronto fra i due e' l'unica prova che il nuovo sia meglio, e va reso
# facile.

SCHEMA_SQL = """
-- 'main.' come nell'altro motore, e per la stessa ragione: un nome non
-- qualificato che in questo database non esiste ancora viene cercato nei
-- database attaccati, e li' butterebbe il lavoro di qualcun altro. Qui
-- oggi non c'e' niente di attaccato, ma la regola vale per costruzione:
-- un DROP nudo in uno schema di questo progetto e' un errore in attesa.
DROP TABLE IF EXISTS main.individui;
DROP TABLE IF EXISTS main.menzioni;
DROP TABLE IF EXISTS main.legami;
DROP TABLE IF EXISTS main.unioni;
DROP TABLE IF EXISTS main.individui_fts;
DROP TABLE IF EXISTS main.fatti;
DROP TABLE IF EXISTS main.anomalie;
-- 'scartati' non e' di questa fase: la produce 'coerenza', che appartiene
-- all'altro motore. Sta qui perche' i due hanno condiviso il database
-- prima che ognuno avesse il suo, e cio' che ne resta punta a individui
-- rinumerati da allora: righe che nessuna interrogazione puo' leggere
-- giuste, e che fanno credere a chi guarda che questa fase cancelli le
-- conclusioni assurde. Non le cancella: le lascia in coda con la domanda
-- gia' pronta per l'immagine.
DROP TABLE IF EXISTS main.scartati;

CREATE TABLE individui (
    id              INTEGER PRIMARY KEY,
    -- L'identificatore che sopravvive alle ricostruzioni: la menzione
    -- piu' antica del gruppo. Rinumerare gli individui a ogni esecuzione
    -- rende impossibile qualunque annotazione umana e qualunque audit
    -- trail, perche' il '#2396' di ieri oggi e' un'altra persona.
    chiave          TEXT UNIQUE,
    nome            TEXT,
    cognome         TEXT,
    sesso           TEXT,
    anno_nascita    INTEGER,
    nascita_origine TEXT,
    anno_morte      INTEGER,
    morta_entro     INTEGER,
    anno_primo      INTEGER,
    anno_ultimo     INTEGER,
    professioni     TEXT,
    residenze       TEXT,
    contrade        TEXT,
    varianti_nome   TEXT,
    varianti_cognome TEXT,
    menzioni        INTEGER NOT NULL DEFAULT 0,
    menzioni_incerte INTEGER NOT NULL DEFAULT 0,
    fondata_su      TEXT,
    -- Le colonne nuove: quanto il sistema crede a questa scheda, in che
    -- stato e' l'identita', e su quali prove si regge.
    confidenza      REAL NOT NULL DEFAULT 1.0,
    stato           TEXT NOT NULL DEFAULT 'confermato',
    prove           TEXT
);

CREATE TABLE menzioni (
    persona     INTEGER PRIMARY KEY REFERENCES persone(id),
    individuo   INTEGER NOT NULL REFERENCES individui(id),
    certa       INTEGER NOT NULL DEFAULT 1,
    confidenza  REAL NOT NULL DEFAULT 1.0,
    stato       TEXT NOT NULL DEFAULT 'confermato',
    -- Perche' questa riga sta in questa scheda. E' la colonna che
    -- permette di rispondere alla domanda che un archivio ricostruito
    -- deve sempre poter reggere: 'e chi lo dice?'
    prove       TEXT
);

CREATE TABLE legami (
    figlio      INTEGER NOT NULL REFERENCES individui(id),
    genitore    INTEGER NOT NULL REFERENCES individui(id),
    tipo        TEXT NOT NULL,
    atto        INTEGER REFERENCES atti(id),
    confidenza  REAL NOT NULL DEFAULT 1.0,
    stato       TEXT NOT NULL DEFAULT 'confermato',
    PRIMARY KEY (figlio, genitore, tipo)
);

CREATE TABLE unioni (
    id          INTEGER PRIMARY KEY,
    marito      INTEGER REFERENCES individui(id),
    moglie      INTEGER REFERENCES individui(id),
    anno        INTEGER,
    atto        INTEGER REFERENCES atti(id),
    origine     TEXT NOT NULL,
    confidenza  REAL NOT NULL DEFAULT 1.0,
    stato       TEXT NOT NULL DEFAULT 'confermato'
);

CREATE TABLE fatti (
    id          INTEGER PRIMARY KEY,
    individuo   INTEGER NOT NULL REFERENCES individui(id),
    tipo        TEXT NOT NULL,
    grezzo      TEXT,
    normalizzato TEXT,
    interpretato TEXT,
    anno        INTEGER,
    atto        INTEGER REFERENCES atti(id),
    menzione    INTEGER REFERENCES persone(id),
    confidenza  REAL NOT NULL DEFAULT 1.0,
    stato       TEXT NOT NULL DEFAULT 'confermato'
);

CREATE TABLE anomalie (
    id          INTEGER PRIMARY KEY,
    tipo        TEXT NOT NULL,
    individui   TEXT NOT NULL,
    atti        TEXT,
    campo       TEXT,
    descrizione TEXT NOT NULL,
    spiegazioni TEXT,
    prove       TEXT,
    confidenza  REAL NOT NULL DEFAULT 0.5,
    impatto     INTEGER NOT NULL DEFAULT 1,
    gravita     TEXT NOT NULL DEFAULT 'media',
    priorita    REAL NOT NULL DEFAULT 0.0,
    stato       TEXT NOT NULL DEFAULT 'aperta'
);

-- Le decisioni NON si cancellano a ogni ricostruzione: sono la memoria
-- del progetto, comprese quelle prese da una persona. Le tabelle
-- ricostruite ripartono da zero; questa si accumula.
CREATE TABLE IF NOT EXISTS decisioni (
    id          INTEGER PRIMARY KEY,
    quando      TEXT NOT NULL,
    azione      TEXT NOT NULL,
    entita      TEXT NOT NULL,
    motivo      TEXT,
    confidenza  REAL,
    evidenze    TEXT,
    contraddizioni TEXT,
    atti        TEXT,
    decisore    TEXT NOT NULL DEFAULT 'algoritmo',
    modello     TEXT,
    versione_prompt TEXT,
    versione_algoritmo TEXT,
    -- La decisione che questa supera. Nessuna riga viene mai tolta:
    -- tornare indietro e' una decisione nuova, non una cancellatura.
    disfa       INTEGER REFERENCES decisioni(id)
);

-- Anche le verifiche si accumulano: sono la cache che impedisce di
-- ripagare due volte la stessa domanda.
CREATE TABLE IF NOT EXISTS verifiche (
    chiave      TEXT PRIMARY KEY,
    atto        INTEGER,
    immagine    TEXT,
    campo       TEXT,
    domanda     TEXT NOT NULL,
    alternative TEXT,
    menzione    INTEGER,
    risposta    TEXT,
    confidenza  REAL,
    stato       TEXT NOT NULL DEFAULT 'in attesa',
    modello     TEXT,
    versione_prompt TEXT,
    quando      TEXT,
    priorita    REAL NOT NULL DEFAULT 0.0,
    motivo      TEXT
);

CREATE INDEX idx_fatti_individuo ON fatti(individuo, tipo, anno);
CREATE INDEX idx_anomalie_priorita ON anomalie(priorita DESC);
CREATE INDEX idx_menzioni_individuo ON menzioni(individuo);
CREATE INDEX idx_legami_genitore ON legami(genitore);
CREATE INDEX idx_legami_figlio   ON legami(figlio);
CREATE INDEX idx_unioni_marito   ON unioni(marito);
CREATE INDEX idx_unioni_moglie   ON unioni(moglie);
CREATE INDEX idx_ind_cognome     ON individui(cognome);
CREATE INDEX idx_ind_nome        ON individui(nome);
CREATE INDEX IF NOT EXISTS idx_decisioni_azione ON decisioni(azione);
CREATE INDEX IF NOT EXISTS idx_verifiche_stato ON verifiche(stato, priorita DESC);

CREATE VIRTUAL TABLE individui_fts USING fts5(
    nome, cognome, varianti_nome, varianti_cognome,
    content='individui', content_rowid='id'
);
"""


def elenco(valori: Any) -> str | None:
    """Le forme viste, in ordine, separate da barre verticali."""
    if not valori:
        return None
    if hasattr(valori, "most_common"):
        return " | ".join(f for f, _ in valori.most_common() if f)
    return " | ".join(sorted(str(v) for v in valori if v))
