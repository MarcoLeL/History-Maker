"""Tornare sulla pagina intera, sapendo cosa aspettarsi.

Fra la trascrizione e la verifica c'era un buco, e questo modulo lo
riempie.

Da una parte ``transcribe`` legge la pagina **senza sapere niente**: e'
giusto che sia cosi', perche' una lettura influenzata da quello che ci si
aspetta di trovare non e' una prova indipendente, e senza prove
indipendenti l'archivio non ha da cosa ricavare la verita'.

Dall'altra ``verifica`` fa **una domanda chiusa su una parola**: «il
cognome del padre e' Lella o Lelli?», su un ritaglio piccolo. Costa poco
e risponde bene, ma puo' solo sciogliere dubbi che qualcuno ha gia'
formulato.

In mezzo manca la cosa che un genealogista fa davvero: **riguardare la
pagina intera avendo in mente la famiglia**. E' cosi' che si scopre un
errore che nessuna anomalia ha segnalato — perche' l'occhio che sa che
il padre e' un Lella si accorge che quella parola non e' un cognome, e
l'occhio che non lo sa la trascrive com'e'.

Il compito, e cosa non e'
-------------------------

Il compito **non** e' «correggi la trascrizione perche' torni con
l'albero». Sarebbe il modo piu' efficace di fabbricare conferme: un
modello a cui si da' il grafo come dato di fatto rilegge finche' non
torna, e la sua risposta non vale niente.

Il compito e' **confrontare tre cose e dire come stanno fra loro**:
quello che si vede sulla pagina, quello che la trascrizione dice, e
quello che il resto dell'archivio fa aspettare. I verdetti sono sette e
comprendono ``PROVE_INSUFFICIENTI``, perche' non decidere e' un
risultato.

Il verdetto che vale di piu' e' ``CONFLITTO_CON_IL_CONTESTO`` con alta
confidenza: vuol dire che l'immagine ha ragione e il grafo aveva torto.
E' il caso di Marzio d'Andria Motta, ed e' esattamente cio' che si e'
venuti a cercare — non un fallimento.

Cosa ne viene
-------------

Le risposte non diventano fatti: diventano **decisioni con un autore**,
come quelle di ``verifica``, e la ricostruzione successiva le applica.
La trascrizione non si tocca mai: il grezzo resta dov'e'.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from history_maker import paleografia
from history_maker.backend import LimiteUsoRaggiunto, Richiesta, estrai_json
from history_maker.ricostruzione import cache, contesto

logger = logging.getLogger(__name__)

# 1.1.0: aggiunto il vocabolario del paese e le regole 6-7 (plausibilita'
# del nome/cognome/mestiere/contrada, ragionamento in contesto familiare
# ottocentesco). Trovato dopo un caso vero — "Teodoro", scritto due volte
# nello stesso atto e attestato 31 volte nell'archivio, letto come
# "Cynodoro" (zero attestazioni) a confidenza 0,95.
# 1.2.0: la regola 6 non chiede piu' di confrontare con la lista delle
# forme locali, ma con la conoscenza generale dei nomi italiani — "questo
# nome esiste in Italia?", non solo "esiste in questo archivio?". La
# versione 1.1 non aveva bastato sullo stesso caso ("Cynodoro" di nuovo, a
# confidenza 0,90); la formulazione assoluta provata a mano ("non puoi
# MAI...") ha funzionato ma vietava anche un nome vero mai visto prima in
# paese, che e' esattamente cio' che non deve succedere.
VERSIONE_PROMPT = "1.2.0"

# Una difesa contro l'errore di battitura che manda in coda tremila
# pagine. Non e' la quota — quella la tiene il backend — ed e' un tetto
# largo apposta: deve stare sopra a qualunque '--quante' sensato, perche'
# 'casi()' seleziona esattamente le pagine richieste e un tetto piu'
# stretto qui le troncherebbe in silenzio senza che 'esegui' lo dica.
TETTO_PER_ESECUZIONE = 500

# Sotto questa confidenza una lettura diversa non diventa una correzione.
# E' la stessa soglia di 'verifica', e per la stessa ragione: una
# correzione sbagliata si propaga ai parenti, una correzione mancata no.
FIDUCIA_PER_CORREGGERE = 0.80

# I verdetti ammessi. 'PROVE_INSUFFICIENTI' e 'NON_LEGGIBILE' sono
# risposte, non fallimenti: un sistema che risponde sempre costringe chi
# consulta a fidarsi o a rifare tutto.
VERDETTI = (
    "CONFERMATO",                    # pagina e trascrizione d'accordo
    "COMPATIBILE",                   # differenze che non cambiano niente
    "CONFLITTO_CON_LA_TRASCRIZIONE", # la pagina dice altro: si corregge
    "CONFLITTO_CON_IL_CONTESTO",     # la pagina ha ragione, il grafo no
    "AMBIGUO",                       # la grafia ammette due letture
    "NON_LEGGIBILE",
    "PROVE_INSUFFICIENTI",
)

SCHEMA_TABELLA = """
CREATE TABLE IF NOT EXISTS riletture (
    chiave      TEXT PRIMARY KEY,
    atto        INTEGER,
    immagine    TEXT,
    -- Il verdetto complessivo sulla pagina, e i campi uno per uno in
    -- JSON: la struttura per campo e' la cosa che rende una rilettura
    -- diversa da una ritrascrizione.
    esito       TEXT,
    campi       TEXT,
    quante_correzioni INTEGER NOT NULL DEFAULT 0,
    modello     TEXT,
    versione_prompt TEXT,
    versione_contesto TEXT,
    quando      TEXT,
    stato       TEXT NOT NULL DEFAULT 'in attesa'
);
CREATE INDEX IF NOT EXISTS idx_riletture_atto ON riletture(atto);
"""


SISTEMA = """Sei un genealogista esperto di registri di stato civile italiani
dell'Ottocento, e sai come questi documenti sbagliano: eta' dichiarate a
voce e arrotondate, cognomi resi in grafie diverse dalla stessa mano,
formule fisse in cui una parola presa per un'altra cambia una parentela.

Ti vengono dati tre cose sulla stessa pagina:

1. L'IMMAGINE ORIGINALE dell'atto.
2. La TRASCRIZIONE che ne era stata fatta, senza nessun contesto.
3. Il CONTESTO GENEALOGICO che il resto dell'archivio fa aspettare.

Il tuo compito NON e' correggere la trascrizione perche' torni con il
contesto. E' dire, campo per campo, **come stanno fra loro** quelle tre
cose.

Regole che non puoi violare:

1. Il contesto e' un'IPOTESI, ricavata da altri documenti. Non e' la
   verita'. Se l'immagine lo contraddice, ha ragione l'immagine: dillo,
   con il verdetto CONFLITTO_CON_IL_CONTESTO. E' un risultato utile, non
   un problema.
2. Non inventare. Se una parola non si legge, il verdetto e'
   NON_LEGGIBILE; se non basta per decidere, e' PROVE_INSUFFICIENTI.
   Sono risposte buone.
3. Rispondi solo sui campi su cui hai qualcosa da dire. Un campo che
   l'immagine conferma e su cui nessuno aveva dubbi non serve elencarlo.
4. Un'eta' che non torna di pochi anni non e' un errore: e' come si
   dichiarava l'eta'. Un atto di nascita e' invece una data scritta.
5. Le formule del formulario ingannano. "marito di Angela Rossi" dice il
   nome della MOGLIE, non il cognome del marito; "fu Giuseppe" dice che
   il padre e' morto, e non fa parte del cognome di nessuno. Se la
   trascrizione ha inglobato una di queste, dillo.
6. Un nome o un cognome deve esistere davvero nella tradizione italiana
   dell'Ottocento. Usa quello che sai sui nomi italiani, non solo quello
   che vedi in questa pagina: "Cynodoro" non e' un nome italiano — nessun
   calendario, nessuna tradizione regionale lo porta — mentre "Teodoro"
   lo e'. Se la tua lettura non e' un nome o un cognome italiano
   riconoscibile, e la trascrizione precedente riporta invece una forma
   che lo e', il sospetto cade sulla lettura nuova, non su quella vecchia:
   abbassa la confidenza o rispondi AMBIGUO, invece di scegliere una
   parola che sembra plausibile ma non esiste. In fondo a questo
   messaggio trovi anche il VOCABOLARIO DEL PAESE — le forme piu'
   attestate in questo archivio — come ulteriore riscontro, non come
   unico giudice: un nome puo' essere vero ed essere il primo della sua
   famiglia a comparire qui.
7. Ragiona come un genealogista che conosce il paese ragionerebbe: chi
   e' comparente in questo atto, di che famiglia e' probabilmente, che
   eta' avrebbe senso per il suo ruolo. Un'identita' che non ha senso in
   una famiglia dell'Ottocento — un testimone di otto anni, un padre piu'
   giovane del figlio — e' un segnale, non un dettaglio da ignorare."""


def _risposta_attesa() -> str:
    return json.dumps(
        {
            "esito": "|".join(("CONFERMATO", "CONFLITTO_CON_LA_TRASCRIZIONE",
                               "CONFLITTO_CON_IL_CONTESTO", "AMBIGUO",
                               "NON_LEGGIBILE", "PROVE_INSUFFICIENTI")),
            "campi": [
                {
                    "menzione": "<il numero della menzione a cui il campo appartiene>",
                    "campo": "cognome | nome | eta | professione | residenza | data",
                    "lettura_dall_immagine": "<cosa vedi scritto>",
                    "verdetto": "<uno dei verdetti>",
                    "confidenza": "<numero fra 0 e 1>",
                    "spiegazione": "<una frase: cosa te lo fa dire>",
                }
            ],
            "note": "<cosa la pagina non permette di decidere, se c'e'>",
        },
        ensure_ascii=False, indent=1,
    )


# Quante forme per categoria mette il vocabolario del paese. Non e' una
# lista esaustiva — sarebbe l'intero glossario — e' il senso del luogo
# che chi e' cresciuto li' avrebbe gia' in testa: i nomi, i cognomi, le
# contrade e i mestieri che chiunque, in paese, riconoscerebbe a colpo
# d'occhio. Cinquanta nomi e cinquanta cognomi coprono la stragrande
# maggioranza delle persone reali; venti contrade e dieci mestieri sono
# gia' oltre quanto un paese di poche migliaia di anime ne conosca.
VOCABOLARIO_TOP_NOMI = 50
VOCABOLARIO_TOP_COGNOMI = 50
VOCABOLARIO_TOP_VIE = 20
VOCABOLARIO_TOP_MESTIERI = 10


def vocabolario_del_paese(conn: sqlite3.Connection) -> dict:
    """Le forme piu' attestate del paese: nomi, cognomi, contrade, mestieri.

    Non e' materiale per il calcolo — quello vive nei pesi misurati sul
    corpus, altrove — e' il termine di paragone che manca a un modello
    che legge una pagina alla volta: senza, "Cynodoro" e "Teodoro" sono
    ugualmente plausibili in astratto, perche' nessuno dei due significa
    niente per chi non sa che il primo non e' mai esistito in
    novantadue anni di registri e il secondo compare trentuno volte.

    Costruito sugli **individui** (le persone riconosciute), non sulle
    menzioni grezze: contare le menzioni darebbe piu' peso a chi compare
    piu' spesso — il sindaco, la levatrice — che a quante persone
    *diverse* portano davvero quel nome, che e' la domanda giusta.
    """
    def top(query: str, quanti: int) -> list[str]:
        return [
            riga[0] for riga in conn.execute(query).fetchall()[:quanti]
            if riga[0]
        ]

    return {
        "nomi": top(
            "SELECT nome, COUNT(*) c FROM individui WHERE nome IS NOT NULL "
            "AND nome <> '' GROUP BY nome ORDER BY c DESC",
            VOCABOLARIO_TOP_NOMI,
        ),
        "cognomi": top(
            "SELECT cognome, COUNT(*) c FROM individui WHERE cognome IS NOT NULL "
            "AND cognome <> '' GROUP BY cognome ORDER BY c DESC",
            VOCABOLARIO_TOP_COGNOMI,
        ),
        "contrade": top(
            "SELECT COALESCE(interpretato, grezzo), COUNT(*) c FROM fatti "
            "WHERE tipo = 'contrada' AND COALESCE(interpretato, grezzo) IS NOT NULL "
            "GROUP BY 1 ORDER BY c DESC",
            VOCABOLARIO_TOP_VIE,
        ),
        "mestieri": top(
            "SELECT COALESCE(interpretato, grezzo), COUNT(*) c FROM fatti "
            "WHERE tipo = 'professione' AND COALESCE(interpretato, grezzo) IS NOT NULL "
            "GROUP BY 1 ORDER BY c DESC",
            VOCABOLARIO_TOP_MESTIERI,
        ),
    }


def istruzione(dato: dict, vocabolario: dict | None = None) -> str:
    """Il testo che accompagna l'immagine.

    L'ordine conta: prima cosa si vede, poi cosa era stato letto, poi
    cosa ci si aspetta, e per ultimo il vocabolario del paese — il
    termine di paragone piu' ampio, che serve a giudicare tutto il
    resto e per questo va letto per ultimo, non per primo.
    """
    atto = dato["atto"]
    pezzi = [
        f"ATTO n. {atto['numero']} di {atto['tipo']}, anno {atto['anno']}, "
        f"registro «{atto['registro']}».",
        "",
        "--- LA TRASCRIZIONE PRECEDENTE (letta senza contesto) ---",
        json.dumps(dato["trascrizione_precedente"], ensure_ascii=False, indent=1),
        "",
        "--- IL CONTESTO GENEALOGICO (un'IPOTESI, ricavata da altri atti) ---",
        json.dumps(dato["contesto_genealogico"], ensure_ascii=False, indent=1),
    ]
    if dato["possible_errors"]:
        pezzi += [
            "",
            "--- COSA E' GIA' IN DUBBIO ---",
            "Sono sospetti che il calcolo ha gia' segnalato. Non sono",
            "verita': alcuni saranno smentiti dalla pagina, ed e' utile",
            "saperlo.",
            json.dumps(dato["possible_errors"], ensure_ascii=False, indent=1),
        ]
    if dato["domande_aperte"]:
        pezzi += ["", "--- LE DOMANDE A CUI QUESTA PAGINA PUO' RISPONDERE ---"]
        pezzi += [f"  {n}. {d}" for n, d in enumerate(dato["domande_aperte"], 1)]
    if vocabolario:
        pezzi += [
            "",
            "--- IL VOCABOLARIO DEL PAESE (le forme piu' attestate; vedi regola 6) ---",
            json.dumps(vocabolario, ensure_ascii=False, indent=1),
        ]
    pezzi += [
        "",
        "Guarda l'immagine e rispondi con un solo oggetto JSON:",
        _risposta_attesa(),
    ]
    return "\n".join(pezzi)


def chiave(dato: dict, modello: str, vocabolario: dict | None = None) -> str:
    """L'impronta di tutto cio' che cambia la risposta.

    Compresa **l'immagine**, per contenuto e non per nome: le pagine si
    possono riscaricare a una risoluzione diversa, e una risposta data
    su trecento pixel non vale per quella stessa pagina a mille.

    Compreso il **vocabolario**: cresce con l'archivio, e una risposta
    data quando "Teodoro" era attestato dieci volte non e' la stessa
    domanda di quando ne e' attestato cento.
    """
    return cache.impronta(
        dato["atto"]["id"], dato["atto"].get("impronta_immagine"),
        dato["trascrizione_precedente"], dato["contesto_genealogico"],
        dato["possible_errors"], vocabolario, modello, VERSIONE_PROMPT,
    )


def _adesso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Quali pagine rileggere
# ---------------------------------------------------------------------------
#
# Non tutte, e non a caso. Un secolo di registri sono settemila atti, e
# rileggerli tutti costerebbe giorni di quota per riottenere in gran
# parte cio' che c'e' gia'. Le pagine che vale la pena riguardare sono
# quelle in cui **il grafo ha gia' un dubbio che la carta puo'
# sciogliere**: e' la stessa logica della coda delle anomalie, applicata
# all'atto invece che alla persona.
#
# I tipi di anomalia che nominano un campo leggibile stanno in
# 'contesto.DUBBI_CHE_LA_PAGINA_SCIOGLIE'. Un dubbio sull'identita' non
# entra: la pagina dice cosa c'e' scritto, non chi era.

TIPI_LEGGIBILI = tuple(
    tipo for tipo, codice in contesto.CODICE_ERRORE.items()
    if codice in contesto.DUBBI_CHE_LA_PAGINA_SCIOGLIE
)


# Quante pagine per persona. Senza questo tetto la coda si satura su una
# vita sola: una donna con quattro parti troppo ravvicinati ha otto atti
# di nascita in cui il dubbio compare, e con venti pagine di budget se ne
# prende sei. E' la stessa disciplina che 'anomalie.coda' applica alla
# coda dei dubbi, per la stessa ragione — una giornata di quota spesa su
# una persona sola non e' un campione.
PAGINE_PER_PERSONA = 2


def casi(conn: sqlite3.Connection, quanti: int) -> list[int]:
    """Gli atti da rileggere, in ordine di quanto la risposta sposterebbe.

    Rende identificatori di atto, non di anomalia: due dubbi sulla stessa
    pagina si sciolgono con una chiamata sola, e trattarli separatamente
    vorrebbe dire pagarla due volte.

    Esclude gli atti gia' risposti. Senza, lanciare lo stesso comando su
    un archivio con piu' pagine leggibili di quante ``TETTO_PER_ESECUZIONE``
    ne esegua in un colpo resterebbe bloccato per sempre sulle stesse
    prime pagine — ormai tutte in cache — senza mai avanzare verso le
    successive. E' un difetto trovato eseguendo davvero il comando su un
    lotto piu' grande del tetto, non leggendo il codice.
    """
    from collections import Counter

    gia_risposte = _gia_risposte(conn)
    segnaposto = ",".join("?" * len(TIPI_LEGGIBILI))
    scelti: dict[int, float] = {}
    per_persona: Counter = Counter()
    for riga in conn.execute(
        f"SELECT individui, atti, priorita FROM anomalie "
        f"WHERE tipo IN ({segnaposto}) ORDER BY priorita DESC", TIPI_LEGGIBILI
    ):
        try:
            atti = json.loads(riga["atti"] or "[]")
            coinvolti = json.loads(riga["individui"] or "[]")
        except (TypeError, ValueError):
            continue
        chi = coinvolti[0] if coinvolti else None
        for atto in atti:
            if chi is not None and per_persona[chi] >= PAGINE_PER_PERSONA:
                break
            if atto in gia_risposte:
                continue
            if atto in scelti:
                # Una pagina con tre dubbi vale piu' di una con uno solo,
                # ma non tre volte: il costo della chiamata e' lo stesso.
                continue
            scelti[atto] = riga["priorita"] or 0.0
            if chi is not None:
                per_persona[chi] += 1
        if len(scelti) >= quanti:
            break
    return [atto for atto, _ in sorted(scelti.items(), key=lambda v: -v[1])][:quanti]


def _gia_risposte(conn: sqlite3.Connection) -> set[int]:
    try:
        return {
            riga["atto"] for riga in conn.execute(
                "SELECT DISTINCT atto FROM riletture WHERE stato = 'risposta'"
            )
        }
    except sqlite3.OperationalError:
        return set()        # la tabella non esiste ancora: nessuna pagina fatta


def tutti_gli_atti(
    conn: sqlite3.Connection, quanti: int, da_anno: int | None = None,
) -> list[int]:
    """Ogni atto non ancora riletto, in ordine cronologico.

    E' l'altra strada rispetto a :func:`casi`. Quella guarda solo le
    pagine su cui **un'anomalia ha gia' segnalato** un dubbio preciso — un
    cognome che non torna col padre, un'eta' incoerente con un atto di
    nascita — e per costruzione non vede niente sulle pagine che sono
    internamente coerenti ma sbagliate lo stesso: il caso di Marzio
    d'Andria Motta, o di Pietrangelo Morella (che quasi certamente e' un
    Moretta, come tutta la sua famiglia, ma nessuna anomalia lo dice
    perche' l'atto e il padre dichiarato concordano sulla stessa grafia).

    Questa strada rilegge **ogni pagina**, non solo quelle segnalate, con
    lo stesso contesto genealogico completo di :func:`casi`. Costa di
    piu' — un secolo di registri sono settemila atti — ma e' l'unico modo
    di scoprire un errore silenzioso invece di aspettare che diventi
    un'anomalia rumorosa.

    Perche' ha senso ora e non da subito: e' la parte lenta e a basso
    ragionamento (confrontare una pagina con la sua ipotesi genealogica),
    ed e' esattamente il mestiere di Gemini — veloce, quota ampia, nessun
    limite a finestre. L'arbitrato fra identita' resta un mestiere per un
    modello che ragiona, e quello si esaurisce presto: non ha senso
    tenerlo fermo ad aspettare, quando la lettura pura puo' avanzare da
    sola nel frattempo.

    ``da_anno`` restringe a un secolo — o a un pezzo — per volta: utile
    per lavorare l'archivio a tappe verificabili invece che come un'unica
    massa indistinta.
    """
    gia_risposte = _gia_risposte(conn)
    query = (
        "SELECT DISTINCT a.id FROM atti a JOIN persone p ON p.atto = a.id "
        "WHERE 1=1"
    )
    parametri: list = []
    if da_anno is not None:
        query += " AND a.anno >= ?"
        parametri.append(da_anno)
    query += " ORDER BY a.anno, a.id"

    scelti = []
    for riga in conn.execute(query, parametri):
        if riga[0] in gia_risposte:
            continue
        scelti.append(riga[0])
        if len(scelti) >= quanti:
            break
    return scelti


def prepara(conn: sqlite3.Connection, atti: list[int], immagini: Path) -> list[dict]:
    """Costruisce il contesto di ogni atto, e ci mette l'impronta dell'immagine.

    L'impronta e' del **contenuto**, non del percorso. Indicizzare la
    cache sul nome del file vuol dire che riscaricare le pagine a piena
    risoluzione non invalida le risposte date su quelle ridotte — e
    quelle risposte sono proprio quelle che avrebbe senso rifare.
    """
    fuori = []
    for atto in atti:
        try:
            dato = contesto.per_documento(conn, atto)
        except ValueError:
            logger.warning("l'atto %d non esiste piu'", atto)
            continue
        nome = dato["atto"]["immagine"]
        percorso = immagini / (nome or "")
        if not nome or not percorso.exists():
            logger.warning("manca l'immagine dell'atto %d", atto)
            continue
        dato["atto"]["impronta_immagine"] = _impronta_file(percorso)
        dato["_percorso_immagine"] = str(percorso)
        fuori.append(dato)
    return fuori


def _impronta_file(percorso: Path) -> str:
    import hashlib

    digestione = hashlib.sha256()
    with percorso.open("rb") as file:
        for blocco in iter(lambda: file.read(1 << 20), b""):
            digestione.update(blocco)
    return digestione.hexdigest()[:24]


# ---------------------------------------------------------------------------
# L'esecuzione
# ---------------------------------------------------------------------------

def esegui(
    config, conn: sqlite3.Connection, dossier: list[dict],
    tetto: int = TETTO_PER_ESECUZIONE,
) -> dict:
    """Rilegge le pagine, e si ferma quando la quota finisce.

    Non solleva mai per esaurimento di quota: quello che e' fatto e' gia'
    salvato e rilanciare riprende da li'.
    """
    from history_maker import backend as motori

    conn.executescript(SCHEMA_TABELLA)
    motore = motori.crea(config)
    motore.verifica()
    scelto = config.trascrizione.modello
    fatte = gia_fatte(conn)
    # Una volta sola per l'intera esecuzione, non per pagina: e' lo stesso
    # vocabolario per tutte, e ricalcolarlo a ogni atto sarebbe tempo speso
    # a riscoprire ogni volta che 'Torzi' e' un cognome del paese.
    vocabolario = vocabolario_del_paese(conn)
    esiti = {"riusate": 0, "fatte": 0, "fallite": 0, "correzioni": 0,
             "conflitti_col_contesto": 0, "quota_esaurita": False}

    for dato in dossier[:tetto]:
        impronta = chiave(dato, scelto, vocabolario)
        precedente = fatte.get(impronta)
        if precedente is not None and precedente["stato"] == "risposta":
            esiti["riusate"] += 1
            continue

        richiesta = Richiesta(
            sistema=SISTEMA,
            istruzione=istruzione(dato, vocabolario),
            immagini=[Path(dato["_percorso_immagine"])],
            modello=scelto,
            timeout_s=config.trascrizione.timeout_s,
        )
        try:
            risposta = motore.esegui(richiesta)
        except LimiteUsoRaggiunto as limite:
            logger.info("quota esaurita: %s", limite)
            esiti["quota_esaurita"] = True
            break

        if not risposta.ok:
            _salva(conn, dato, impronta, scelto, None, "fallita")
            esiti["fallite"] += 1
            conn.commit()
            continue

        letto = _interpreta(risposta.testo)
        corrette = _correggi(conn, dato, letto, scelto)
        esiti["fatte"] += 1
        esiti["correzioni"] += corrette
        esiti["conflitti_col_contesto"] += sum(
            1 for campo in letto.get("campi", [])
            if campo.get("verdetto") == "CONFLITTO_CON_IL_CONTESTO"
        )
        _salva(conn, dato, impronta, scelto, letto, "risposta", corrette)
        conn.commit()
    return esiti


def _interpreta(testo: str) -> dict:
    """La risposta come struttura, o una vuota se non lo e'.

    ``estrai_json`` rende gia' la struttura, non il testo: ripassarla a
    ``json.loads`` solleverebbe un ``TypeError``, e il ramo di ripiego
    trasformerebbe ogni risposta buona in una vuota. E' un errore che
    questo progetto ha gia' pagato una volta, e non si vede: una risposta
    vuota e' esattamente cio' che si osserva quando il modello non
    riesce a leggere.
    """
    try:
        dati = estrai_json(testo)
    except (ValueError, TypeError) as errore:
        logger.warning("risposta non interpretabile: %s", errore)
        return {}
    if isinstance(dati, list):
        dati = dati[0] if dati else {}
    return dati if isinstance(dati, dict) else {}


def gia_fatte(conn: sqlite3.Connection) -> dict:
    try:
        return {
            riga["chiave"]: riga for riga in conn.execute("SELECT * FROM riletture")
        }
    except sqlite3.OperationalError:
        return {}


def _salva(
    conn: sqlite3.Connection, dato: dict, impronta: str, modello: str,
    letto: dict | None, stato: str, correzioni: int = 0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO riletture (chiave, atto, immagine, esito, campi, "
        "quante_correzioni, modello, versione_prompt, versione_contesto, quando, "
        "stato) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            impronta, dato["atto"]["id"], dato["atto"]["immagine"],
            (letto or {}).get("esito"),
            json.dumps((letto or {}).get("campi", []), ensure_ascii=False),
            correzioni, modello, VERSIONE_PROMPT, contesto.VERSIONE_PROMPT,
            _adesso(), stato,
        ),
    )


# I campi su cui ha senso chiedere "questa forma esiste nel paese?". Non
# tutti: una data o un'eta' non hanno un vocabolario da rispettare.
CAMPI_CON_VOCABOLARIO = frozenset({"nome", "cognome", "professione"})

# Sotto quante attestazioni una forma non fa testo abbastanza da bocciare
# l'alternativa. Sopra questa soglia la forma vecchia e' evidentemente
# reale, e una nuova lettura con zero attestazioni altrove ha l'onere
# della prova — che una sola pagina, letta da un modello che sappiamo
# poter scavalcare persino un ancoraggio messo davanti ai suoi occhi, non
# basta a fornire.
MINIMO_ATTESTAZIONI_PER_BOCCIARE = 3


def _attestazioni(conn: sqlite3.Connection, campo: str, valore: str) -> int:
    """Quante persone diverse portano gia' questa forma nell'archivio.

    Sugli **individui** (persone riconosciute), non sulle menzioni
    grezze: la domanda e' 'questo nome esiste nel paese', non 'quante
    volte compare la firma di chi lo porta piu' spesso'.
    """
    if not valore:
        return 0
    colonna = {"nome": "nome", "cognome": "cognome"}.get(campo)
    if colonna:
        return conn.execute(
            f"SELECT COUNT(*) FROM individui WHERE {colonna} = ? COLLATE NOCASE",
            (valore,),
        ).fetchone()[0]
    # Il canonico se c'e', il grezzo solo quando non c'e' — la stessa
    # regola di 'vocabolario_del_paese'. Contare il grezzo anche quando
    # esiste gia' un interpretato conterebbe una grafia storpiata gia'
    # ricondotta come se fosse una forma a se': 'Agrimenfore' non deve
    # sembrare attestata quando e' solo la lettura di 'Agrimensore'.
    return conn.execute(
        "SELECT COUNT(*) FROM fatti WHERE tipo = ? "
        "AND COALESCE(interpretato, grezzo) = ? COLLATE NOCASE",
        (campo, valore),
    ).fetchone()[0]


def _plausibile(conn: sqlite3.Connection, campo: str, nuova: str, vecchia: str) -> bool:
    """Se vale la pena fidarsi di una lettura mai vista, contro una gia'
    nota al paese.

    Il caso che l'ha resa necessaria: "Teodoro", scritto due volte
    nello stesso atto e attestato 31 volte nell'archivio, letto
    "Cynodoro" — zero attestazioni, in nessun documento, mai — a
    confidenza 0,95 prima e 0,90 dopo aver aggiunto la regola sulla
    plausibilita' al prompt. Il testo non e' bastato: la lettura
    visiva del modello ha scavalcato un ancoraggio che aveva davanti
    agli occhi. Questo controllo non chiede al modello di essere piu'
    prudente: lo e' al posto suo, dopo che ha gia' risposto.

    Non e' un veto assoluto — un nome nuovo puo' benissimo essere vero,
    un forestiero, una grafia che nessuno aveva ancora letto — ma
    quando la forma vecchia e' gia' ben attestata, l'onere della prova
    sta sulla nuova, e una sola lettura non lo regge.
    """
    if campo not in CAMPI_CON_VOCABOLARIO:
        return True
    if _attestazioni(conn, campo, nuova) > 0:
        return True
    return _attestazioni(conn, campo, vecchia) < MINIMO_ATTESTAZIONI_PER_BOCCIARE


def _correggi(conn: sqlite3.Connection, dato: dict, letto: dict, modello: str) -> int:
    """Trasforma le letture sicure e diverse in correzioni registrate.

    E' il passo che chiude il ciclo: senza, una rilettura resta una riga
    in una tabella e la ricostruzione successiva rifarebbe lo stesso
    errore.

    Tre condizioni, e la terza e' quella nuova. La lettura deve essere
    **sicura**, deve essere **diversa da quella trascritta** — una
    conferma non e' una correzione, ed e' un risultato quanto una
    correzione, significa che li' a non tornare e' l'identita', non la
    lettura — e deve essere **plausibile**: vedi :func:`_plausibile`.
    """
    from history_maker.ricostruzione import registro

    per_menzione = {
        m["menzione"]: m for m in dato["trascrizione_precedente"]["menzioni"]
    }
    corrette = 0
    for campo in letto.get("campi", []):
        if campo.get("verdetto") not in (
            "CONFLITTO_CON_LA_TRASCRIZIONE", "CONFLITTO_CON_IL_CONTESTO"
        ):
            continue
        try:
            confidenza = float(campo.get("confidenza") or 0.0)
            menzione = int(campo.get("menzione"))
        except (TypeError, ValueError):
            continue
        if confidenza < FIDUCIA_PER_CORREGGERE:
            continue
        riga = per_menzione.get(menzione)
        nuova = (campo.get("lettura_dall_immagine") or "").strip()
        quale = campo.get("campo") or ""
        if riga is None or not nuova or quale not in riga:
            continue
        vecchia = (riga.get(quale) or "").strip()
        if paleografia.normalizza(nuova) == paleografia.normalizza(vecchia):
            continue        # conferma la lettura: non e' una correzione
        if not _plausibile(conn, quale, nuova, vecchia):
            logger.info(
                "correzione scartata: %r -> %r su %s non ha nessuna "
                "attestazione contro una forma gia' nota", vecchia, nuova, quale,
            )
            continue
        gia = conn.execute(
            "SELECT 1 FROM decisioni WHERE azione = 'correzione' AND entita = ? "
            "AND evidenze LIKE ?", (json.dumps([menzione]), f"{quale}=%")
        ).fetchone()
        if gia is not None:
            continue
        registro.annota(
            conn, "correzione", (menzione,),
            f"{quale}: letto «{vecchia}», sull'immagine «{nuova}»; "
            f"{campo.get('spiegazione', '')}".strip(),
            confidenza=confidenza,
            decisore="gemini",
            modello=modello,
            versione_prompt=VERSIONE_PROMPT,
            evidenze=(f"{quale}={nuova}",),
        )
        corrette += 1
    return corrette
