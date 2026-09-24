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
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from history_maker import paleografia, ricuci
from history_maker.backend import LimiteUsoRaggiunto, Richiesta, estrai_json
from history_maker.ricostruzione import cache, contesto
from history_maker.ricostruzione import registro as _registro

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


def _blocco_atto(dato: dict, intestazione: str = "") -> list[str]:
    """Un atto e cio' che di lui si sa: le righe, senza il contorno.

    Sta a parte perche' una facciata ne contiene piu' d'uno, e il
    contorno — il vocabolario del paese, lo schema della risposta — si
    manda una volta sola per tutta la pagina, non una per atto.
    """
    atto = dato["atto"]
    pezzi = [
        (intestazione + f"ATTO n. {atto['numero']} di {atto['tipo']}, "
         f"anno {atto['anno']}, registro «{atto['registro']}»."),
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
    return pezzi


def istruzione(dato: dict, vocabolario: dict | None = None) -> str:
    """Il testo che accompagna l'immagine.

    L'ordine conta: prima cosa si vede, poi cosa era stato letto, poi
    cosa ci si aspetta, e per ultimo il vocabolario del paese — il
    termine di paragone piu' ampio, che serve a giudicare tutto il
    resto e per questo va letto per ultimo, non per primo.
    """
    pezzi = _blocco_atto(dato)
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


def _risposta_attesa_facciata(dati: list[dict]) -> str:
    return json.dumps(
        {
            "atti": [
                {
                    "atto": dato["atto"]["id"],
                    "esito": "<uno dei verdetti, per QUESTO atto>",
                    "campi": ["<come sopra, solo le menzioni di questo atto>"],
                }
                for dato in dati
            ],
            "note": "<cosa la pagina non permette di decidere, se c'e'>",
        },
        ensure_ascii=False, indent=1,
    )


def istruzione_facciata(facciata: dict, vocabolario: dict | None = None) -> str:
    """Il testo per una facciata intera: gli atti che ci stanno, uno dietro l'altro.

    Con un atto solo rende **esattamente** il testo di :func:`istruzione`.
    Non e' un dettaglio di eleganza: la chiave della cache e' un'impronta
    di cio' che si manda, e un testo diverso butterebbe via le risposte
    gia' pagate su tutte le facciate da un atto solo.

    Con piu' atti, il vocabolario del paese e lo schema della risposta si
    mandano **una volta sola** in fondo — e' il pezzo identico a ogni
    chiamata, e ripeterlo per atto sarebbe l'unico sperpero che
    l'accorpamento non toglie da se'.
    """
    dati = facciata["atti"]
    if len(dati) == 1 and not facciata.get("seguito"):
        return istruzione(dati[0], vocabolario)

    if len(dati) == 1:
        pezzi = [
            "Ti vengono date DUE immagini, e sono due scansioni consecutive",
            "dello stesso registro.",
            "",
            "In questo registro ogni atto comincia sulla pagina destra di",
            "un'apertura e FINISCE IN CIMA ALLA SCANSIONE SUCCESSIVA. Quindi:",
            "",
            "  IMMAGINE 1 — l'atto di cui devi rispondere comincia qui, sulla",
            "    pagina destra. In CIMA A SINISTRA c'e' invece la coda",
            "    dell'atto PRECEDENTE: non e' roba tua. Il nome che leggi",
            "    li' e' di un'altra persona, e attribuirlo a questo atto e'",
            "    l'errore preciso che questa seconda immagine serve a evitare.",
            "",
            "  IMMAGINE 2 — la scansione dopo. In CIMA A SINISTRA finisce",
            "    l'atto tuo: e' li' che si trova, di norma, il nome che il",
            "    dichiarante da' al neonato. Tutto il resto di questa",
            "    immagine e' gia' l'atto SEGUENTE: ignoralo.",
            "",
            "Prima di correggere un nome, chiediti da quale dei tre atti",
            "visibili viene la parola che stai leggendo. Se non riesci a",
            "dirlo, la risposta e' PROVE_INSUFFICIENTI.",
            "",
        ]
        pezzi += _blocco_atto(dati[0])
        if vocabolario:
            pezzi += [
                "",
                "--- IL VOCABOLARIO DEL PAESE (le forme piu' attestate; vedi regola 6) ---",
                json.dumps(vocabolario, ensure_ascii=False, indent=1),
            ]
        pezzi += [
            "",
            "Guarda le immagini e rispondi con un solo oggetto JSON:",
            _risposta_attesa(),
        ]
        return "\n".join(pezzi)

    pezzi = [
        f"Questa immagine e' UNA FACCIATA di registro, e ci stanno sopra "
        f"{len(dati)} atti.",
        "",
        "Rispondi su ciascuno separatamente. L'errore da non fare e'",
        "attribuire a un atto cio' che si legge nell'altro: ogni campo va",
        "riferito al numero di menzione che gli appartiene, e quei numeri",
        "sono elencati atto per atto qui sotto.",
    ]
    for numero, dato in enumerate(dati, 1):
        pezzi += [
            "",
            "=" * 68,
            f"ATTO {numero} DI {len(dati)} SU QUESTA FACCIATA "
            f"(identificativo {dato['atto']['id']})",
            "=" * 68,
        ]
        pezzi += _blocco_atto(dato)
    if vocabolario:
        pezzi += [
            "",
            "=" * 68,
            "--- IL VOCABOLARIO DEL PAESE (le forme piu' attestate; vedi regola 6) ---",
            "Vale per tutti gli atti di questa facciata.",
            json.dumps(vocabolario, ensure_ascii=False, indent=1),
        ]
    pezzi += [
        "",
        "Guarda l'immagine e rispondi con un solo oggetto JSON:",
        _risposta_attesa_facciata(dati),
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


def chiave_facciata(
    facciata: dict, modello: str, vocabolario: dict | None = None,
) -> str:
    """L'impronta di una domanda su una facciata intera.

    Con un atto solo e' **la stessa** di :func:`chiave`: le risposte gia'
    in archivio restano valide, e l'accorpamento non costa una quota di
    riscaldamento.
    """
    dati = facciata["atti"]
    if len(dati) == 1 and not facciata.get("seguito"):
        return chiave(dati[0], modello, vocabolario)
    return cache.impronta(
        facciata["immagine"], facciata["impronta_immagine"],
        facciata.get("seguito"), facciata.get("impronta_seguito"),
        [
            (d["atto"]["id"], d["trascrizione_precedente"],
             d["contesto_genealogico"], d["possible_errors"])
            for d in dati
        ],
        vocabolario, modello, VERSIONE_PROMPT,
    )


def _chiave_riga(impronta: str, dato: dict, quanti: int) -> str:
    """La chiave della riga di un singolo atto dentro una facciata.

    ``riletture.chiave`` e' una chiave primaria, quindi tre atti della
    stessa facciata non possono condividerla: l'ultimo scritto
    cancellerebbe i primi due, e la pagina risulterebbe letta per un atto
    solo. Con un atto solo resta l'impronta nuda — di nuovo, per non
    invalidare quello che c'e' gia'.
    """
    if quanti == 1:
        return impronta
    return cache.impronta(impronta, dato["atto"]["id"])


def _per_atto(letto: dict, dati: list[dict]) -> dict[int, dict]:
    """Rismista la risposta di una facciata sugli atti che ci stanno.

    L'ancora e' la **menzione**, non l'identificativo dell'atto: le
    menzioni sono uniche su tutto l'archivio e il modello le ha davanti
    scritte una per una, mentre l'identificativo dell'atto glielo si
    chiede di riecheggiare — e riecheggiare e' proprio cio' che un
    modello sbaglia. L'identificativo serve solo per l'esito
    complessivo, che non ha menzioni a cui appendersi.

    Regge anche una risposta piatta — senza la lista ``atti`` — perche'
    un modello a cui si chiede una forma non sempre la da', e una
    risposta buona nella forma sbagliata non va buttata.
    """
    atto_di_menzione = {
        m["menzione"]: dato["atto"]["id"]
        for dato in dati
        for m in dato["trascrizione_precedente"]["menzioni"]
    }
    primo = dati[0]["atto"]["id"]
    fuori: dict[int, dict] = {
        dato["atto"]["id"]: {"esito": None, "campi": []} for dato in dati
    }

    blocchi = letto.get("atti")
    if not isinstance(blocchi, list) or not blocchi:
        # Risposta piatta: l'esito e' uno e vale per tutta la facciata.
        for id_atto in fuori:
            fuori[id_atto]["esito"] = letto.get("esito")
        blocchi = [{"atto": None, "campi": letto.get("campi") or []}]

    for blocco in blocchi:
        if not isinstance(blocco, dict):
            continue
        try:
            dichiarato = int(blocco.get("atto"))
        except (TypeError, ValueError):
            dichiarato = None
        if dichiarato in fuori and blocco.get("esito"):
            fuori[dichiarato]["esito"] = blocco["esito"]
        for campo in blocco.get("campi") or []:
            if not isinstance(campo, dict):
                continue
            try:
                menzione = int(campo.get("menzione"))
            except (TypeError, ValueError):
                menzione = None
            bersaglio = atto_di_menzione.get(menzione)
            if bersaglio is None:
                # Menzione che non appartiene a nessuno degli atti: non si
                # butta, si appende dove il modello diceva (o al primo), che
                # e' dove '_correggi' la scartera' lasciandone traccia.
                logger.info(
                    "campo con menzione %r non attribuibile a nessun atto "
                    "della facciata", campo.get("menzione"),
                )
                bersaglio = dichiarato if dichiarato in fuori else primo
            fuori[bersaglio]["campi"].append(campo)
    return fuori


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


# ---------------------------------------------------------------------------
# La facciata, non l'atto
# ---------------------------------------------------------------------------
#
# Su una facciata di registro stanno **piu' atti**: due di norma, tre
# spesso, fino a nove. Chiedere un atto per volta vuol dire caricare la
# stessa immagine fino a nove volte, e l'immagine e' quasi tutto il costo
# di una chiamata — mentre il muro del piano gratuito e' un numero di
# *richieste* al giorno, non di token.
#
# Sull'archivio: 7.400 atti stanno su 4.044 facciate. Chiedere per
# facciata e' il 44% di chiamate in meno, a parita' di pagine lette.
#
# E non e' solo piu' economico, e' piu' corretto. Oggi al modello si
# manda una facciata con sopra tre atti e gli si chiede di uno solo:
# leggere il campo dell'atto sbagliato e' un errore che nessuno vede,
# perche' la risposta e' comunque ben formata. Far coincidere la domanda
# con l'immagine toglie l'occasione invece di sperare che non capiti.

# Quanti atti chiedere alla coda per ogni facciata voluta. La media
# misurata e' 1,83 atti per facciata: con tre si arriva quasi sempre al
# numero chiesto, e quando non ci si arriva si fanno **meno** chiamate
# del previsto, mai di piu' — cioe' si sbaglia dalla parte della quota.
ATTI_PER_FACCIATA_ATTESI = 3


def facciate(
    conn: sqlite3.Connection, atti: list[int], completa: bool = True,
) -> list[list[int]]:
    """Raggruppa gli atti scelti per la facciata su cui stanno.

    Tiene l'ordine in cui la coda li ha scelti: la prima facciata e'
    quella dell'atto piu' urgente.

    Con ``completa``, ogni facciata si porta dietro **anche gli altri
    atti che stanno sulla stessa immagine** e non sono ancora stati
    riletti. Sono gratis: l'immagine parte comunque, e il contesto di un
    atto in piu' costa qualche migliaio di caratteri contro i quattro
    quintili di costo che sta nei pixel. E' anche il modo di non
    lasciare indietro l'atto n. 4 solo perche' l'anomalia era sul n. 3.
    """
    gia_risposte = _gia_risposte(conn) if completa else set()
    ordine: list[str] = []
    per_immagine: dict[str, list[int]] = {}
    for atto in atti:
        riga = conn.execute(
            "SELECT immagine FROM atti WHERE id = ?", (atto,)
        ).fetchone()
        nome = riga[0] if riga is not None else None
        if not nome:
            logger.warning("l'atto %d non ha un'immagine", atto)
            continue
        if nome not in per_immagine:
            per_immagine[nome] = []
            ordine.append(nome)
        if atto not in per_immagine[nome]:
            per_immagine[nome].append(atto)

    if completa:
        for nome in ordine:
            for riga in conn.execute(
                "SELECT DISTINCT a.id FROM atti a JOIN persone p ON p.atto = a.id "
                "WHERE a.immagine = ? ORDER BY a.id", (nome,)
            ):
                if riga[0] not in gia_risposte and riga[0] not in per_immagine[nome]:
                    per_immagine[nome].append(riga[0])
    return [sorted(per_immagine[nome]) for nome in ordine]


def da_rileggere(
    conn: sqlite3.Connection, quante: int, tutte: bool = False,
    da_anno: int | None = None,
) -> list[list[int]]:
    """Le prossime ``quante`` **facciate** da rileggere, non i prossimi atti.

    E' l'unita' giusta perche' e' l'unita' della spesa: una facciata e'
    una chiamata, e la quota si conta in chiamate. Il ``--quante`` della
    riga di comando ha sempre detto «quante pagine», e da qui in avanti
    e' vero alla lettera.
    """
    grezzi = (
        tutti_gli_atti(conn, quante * ATTI_PER_FACCIATA_ATTESI, da_anno=da_anno)
        if tutte else casi(conn, quante * ATTI_PER_FACCIATA_ATTESI)
    )
    return facciate(conn, grezzi)[:quante]


def prepara(
    conn: sqlite3.Connection, gruppi: list[list[int]], immagini: Path,
) -> list[dict]:
    """Costruisce il dossier di ogni facciata: l'immagine, e gli atti che ci stanno.

    L'impronta dell'immagine e' del **contenuto**, non del percorso.
    Indicizzare la cache sul nome del file vuol dire che riscaricare le
    pagine a piena risoluzione non invalida le risposte date su quelle
    ridotte — e quelle risposte sono proprio quelle che avrebbe senso
    rifare.

    Un atto che non esiste piu', o la cui immagine manca, esce dalla sua
    facciata senza far cadere le altre: una facciata rimasta senza atti
    non viene resa.
    """
    fuori = []
    for gruppo in gruppi:
        dati, percorso, impronta = [], None, None
        for atto in gruppo:
            try:
                dato = contesto.per_documento(conn, atto)
            except ValueError:
                logger.warning("l'atto %d non esiste piu'", atto)
                continue
            nome = dato["atto"]["immagine"]
            suo = immagini / (nome or "")
            if not nome or not suo.exists():
                logger.warning("manca l'immagine dell'atto %d", atto)
                continue
            if impronta is None:
                percorso, impronta = suo, _impronta_file(suo)
            # L'impronta sta anche dentro l'atto, dove 'chiave' la cerca:
            # una facciata di un atto solo deve dare la stessa impronta di
            # prima, o le 449 risposte gia' in cache si buttano via.
            dato["atto"]["impronta_immagine"] = impronta
            dato["_percorso_immagine"] = str(percorso)
            dati.append(dato)
        if not dati:
            continue
        facciata = {
            "immagine": dati[0]["atto"]["immagine"],
            "impronta_immagine": impronta,
            "_percorso_immagine": str(percorso),
            "atti": dati,
        }
        _attacca_il_seguito(conn, facciata, immagini)
        fuori.append(facciata)
    return fuori


# ---------------------------------------------------------------------------
# L'atto che sborda sulla scansione dopo
# ---------------------------------------------------------------------------
#
# In 139 registri su 320 — il 23% degli atti — ogni atto comincia sulla
# pagina destra dell'apertura e **finisce in cima alla scansione
# successiva**. La scansione che il database associa a un atto contiene
# quindi due cose sbagliate: in cima la **coda dell'atto precedente**, e
# da nessuna parte la **fine del proprio**.
#
# Chiedere a un modello di rileggere quell'immagine e' chiedergli di
# giudicare un atto monco avendo sotto gli occhi il finale di un altro. E
# lui non risponde 'prove insufficienti': legge cio' che vede e lo
# attribuisce a chi gli e' stato nominato. Misurato su 58 atti:
#
#     facciata da 1 atto (sborda)      14 atti   14 correzioni
#     facciata da 2+ atti (atto intero) 44 atti    0 correzioni
#
# Tutta la produzione da una parte sola — e il caso che l'ha fatto
# scoprire e' un neonato maschio, Nicolangelo Detta, ribattezzato "Maria
# Lucia" con confidenza 0,90: il nome della bambina dell'atto precedente,
# scritto in cima alla sua pagina.
#
# 'transcribe' non ne soffre perche' legge dodici pagine per chiamata e
# ricuce l'atto attraverso lo stacco. Qui si fa la stessa cosa in
# piccolo: quando la facciata regge un atto solo, si manda anche la
# scansione dopo, dove quell'atto finisce.


def _scansione_seguente(
    conn: sqlite3.Connection, atto: int, immagine: str,
) -> str | None:
    """La scansione dopo questa, nello stesso registro.

    La si cerca fra gli atti, non fra i file: l'ordine degli atti e'
    l'ordine del registro, e un file in piu' nella cartella — una
    copertina, un indice — non deve spostare il seguito di un atto.
    """
    registro = immagine.split("/")[0] + "/"
    riga = conn.execute(
        "SELECT immagine FROM atti WHERE id > ? AND immagine <> ? "
        "AND immagine LIKE ? ORDER BY id LIMIT 1",
        (atto, immagine, registro + "%"),
    ).fetchone()
    return riga[0] if riga is not None else None


def _attacca_il_seguito(
    conn: sqlite3.Connection, facciata: dict, immagini: Path,
) -> None:
    """Aggiunge alla facciata la scansione su cui l'atto va a finire.

    Solo quando la facciata ne regge **uno**: se su una pagina ci stanno
    due o piu' atti vuol dire che sono corti e finiscono dove
    cominciano, e la seconda immagine sarebbe un costo senza una ragione.
    """
    dati = facciata["atti"]
    if len(dati) != 1:
        return
    nome = _scansione_seguente(
        conn, dati[0]["atto"]["id"], facciata["immagine"])
    if not nome:
        return              # l'ultimo atto del registro: il seguito non c'e'
    percorso = immagini / nome
    if not percorso.exists():
        logger.warning("manca la scansione che segue %s", facciata["immagine"])
        return
    facciata["seguito"] = nome
    facciata["impronta_seguito"] = _impronta_file(percorso)
    facciata["_percorso_seguito"] = str(percorso)


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
    """Rilegge le facciate, e si ferma quando la quota finisce.

    Una chiamata per **facciata**, non per atto: e' la stessa immagine, e
    l'immagine e' quasi tutto il costo. ``tetto`` conta le chiamate,
    perche' e' quello che la quota conta.

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
    esiti = {"riusate": 0, "fatte": 0, "atti": 0, "fallite": 0, "correzioni": 0,
             "conflitti_col_contesto": 0, "attese": 0, "quota_esaurita": False}

    for facciata in dossier[:tetto]:
        dati = facciata["atti"]
        impronta = chiave_facciata(facciata, scelto, vocabolario)
        chiavi = {
            dato["atto"]["id"]: _chiave_riga(impronta, dato, len(dati))
            for dato in dati
        }
        # Riusabile solo se **ogni** atto della facciata ha gia' risposta:
        # una pagina risposta a meta' e' una pagina da rifare.
        gia = [fatte.get(c) for c in chiavi.values()]
        if all(r is not None and r["stato"] == "risposta" for r in gia):
            esiti["riusate"] += 1
            continue

        pagine = [Path(facciata["_percorso_immagine"])]
        if facciata.get("_percorso_seguito"):
            # L'ordine conta: la prima e' la pagina dell'atto, la seconda
            # quella dove va a finire, e l'istruzione le nomina cosi'.
            pagine.append(Path(facciata["_percorso_seguito"]))
        richiesta = Richiesta(
            sistema=SISTEMA,
            istruzione=istruzione_facciata(facciata, vocabolario),
            immagini=pagine,
            modello=scelto,
            timeout_s=config.trascrizione.timeout_s,
        )
        risposta = _chiedi(motore, richiesta, esiti)
        if risposta is None:
            break               # il muro del giorno: si riprende domani

        if not risposta.ok:
            for dato in dati:
                _salva(conn, dato, chiavi[dato["atto"]["id"]], scelto, None, "fallita")
            esiti["fallite"] += 1
            conn.commit()
            continue

        smistata = _per_atto(_interpreta(risposta.testo), dati)
        esiti["fatte"] += 1
        for dato in dati:
            suo = smistata[dato["atto"]["id"]]
            corrette = _correggi(conn, dato, suo, scelto)
            esiti["atti"] += 1
            esiti["correzioni"] += corrette
            esiti["conflitti_col_contesto"] += sum(
                1 for campo in suo.get("campi", [])
                if _verdetto_canonico(campo.get("verdetto"))
                == "CONFLITTO_CON_IL_CONTESTO"
            )
            _salva(conn, dato, chiavi[dato["atto"]["id"]], scelto, suo,
                   "risposta", corrette)
        conn.commit()
    return esiti


# Quanto aspettare quando il servizio non dice quanto. E' la stessa
# mezza attesa che 'gemini' suggerisce per un 503: una congestione
# dalla parte di Google, tipicamente breve.
ATTESA_PREDEFINITA_S = 30.0


def _chiedi(motore, richiesta, esiti: dict):
    """Fa la domanda, e aspetta quando c'e' solo da aspettare.

    Dalla stessa eccezione arrivano due cose molto diverse, e questo
    modulo le confondeva tutt'e due nella peggiore: **il muro del
    giorno** — cinquecento richieste finite, si torna domani — e **una
    congestione di trenta secondi**, il 503 "this model is currently
    experiencing high demand".

    Trattare la seconda come la prima ferma un lavoro di quattrocento
    chiamate per un intoppo che passa da solo, e per giunta stampa che
    la quota e' esaurita quando ne restano quattrocento. E' successo:
    ventinove facciate su duecentottanta, e il comando ha annunciato la
    fine della giornata con novantatre richieste su cinquecento.

    Il giudizio non si riscrive qui: e' quello di
    ``transcribe._e_solo_ritmo``, che pesa tre segnali in ordine di
    affidabilita' — cosa dice l'API della quota violata, quanto
    suggerisce di aspettare, e quante volte ha gia' detto di no. Averne
    due copie vuol dire vederle divergere.

    Rende ``None`` solo quando c'e' davvero da fermarsi.
    """
    from history_maker.transcribe import _e_solo_ritmo

    tentativi = 0
    while True:
        try:
            return motore.esegui(richiesta)
        except LimiteUsoRaggiunto as limite:
            tentativi += 1
            if not _e_solo_ritmo(limite, tentativi):
                logger.warning("ci si ferma qui: %s", limite)
                esiti["quota_esaurita"] = True
                return None
            attesa = limite.attesa_s or ATTESA_PREDEFINITA_S
            logger.info(
                "%s: aspetto %.0f s e ritento la stessa facciata", limite, attesa,
            )
            esiti["attese"] += 1
            time.sleep(attesa)


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

    Ma di ogni individuo si guardano **tutte le letture** — la forma
    canonica e quelle scartate, che il database tiene in
    ``varianti_nome`` e ``varianti_cognome`` — non la sola canonica.
    Guardare solo quella vuol dire non vedere proprio le grafie rare,
    che sono le uniche su cui la domanda si pone davvero: il
    consolidamento le ha gia' ricondotte alla forma dominante, e da
    fuori sembrano non essere mai esistite.

    Il caso che l'ha resa necessaria e' il rovescio esatto di
    "Cynodoro": "Metro Marianacci", scritto cosi' due volte nell'atto
    del 1809 e letto cosi' in altri due atti — 1811 e 1812 — ma
    consolidato sotto "Mitrodoro". Contando la sola forma canonica
    risultava mai visto in novantadue anni di registri, e il veto ha
    buttato una lettura giusta: il calzolaio del 1809 e' rimasto una
    scheda a se', separata dalla propria — stessa moglie, stesso anno
    di nascita, stesso mestiere.
    """
    if not valore:
        return 0
    colonne = {
        "nome": ("nome", "varianti_nome"),
        "cognome": ("cognome", "varianti_cognome"),
    }.get(campo)
    if colonne:
        canonica, letture = colonne
        # Le varianti stanno in una stringa sola, separate da ' | '.
        # Avvolgerla nello stesso separatore fa combaciare anche la
        # prima e l'ultima senza un caso a parte per ciascuna.
        return conn.execute(
            f"SELECT COUNT(*) FROM individui WHERE {canonica} = ? COLLATE NOCASE "
            f"OR ' | ' || COALESCE({letture}, '') || ' | ' LIKE ?",
            (valore, f"% | {valore} | %"),
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


# Le parole con cui un modello dice «qui non c'e' scritto niente». Sono
# risposte oneste alla domanda sbagliata: la rilettura chiede *cosa dice
# la pagina*, e riceve *che la pagina non dice*. Registrarle come letture
# mette la parola 'nulla' nel mestiere di una contadina, e la frase
# «non presente (e' il neonato)» nella sua eta'.
_UN_ASSENZA = re.compile(
    r"^\s*(?:nulla|niente|nessun[oa]?|vuot[oa]|assente|mancante|illeggibile|"
    r"in\s?bianco|n\.?d\.?|[-–—]+|\?+)\s*$|"
    r"^\s*non\s+(?:presente|indicat[oa]|specificat[oa]|leggibile|riportat[oa]|"
    r"c['’]?e)\b",
    re.I,
)


def _e_una_lettura(valore: str) -> bool:
    """Il valore proposto e' qualcosa che sta scritto sulla pagina?

    Vale per ogni campo, e si divide il lavoro con :data:`_NON_E_UN_NOME`:
    quella guarda le parentesi e i dubbi, questa le assenze. Serve
    perche' l'eta' e la via non hanno vocabolario e passavano senza
    nessun controllo. Le due prove stanno tutt'e due nell'archivio:
    ``professione = 'nulla'`` per una donna che l'atto dice campagnuola,
    e ``eta = "non presente (e' il neonato)"``.
    """
    valore = (valore or "").strip()
    return bool(valore) and not _UN_ASSENZA.match(valore)


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


# Il modello non si attiene ai sette verdetti, e non e' un vezzo: dice
# ERRORE_NELLA_TRASCRIZIONE, CORRETTO_DA_IMMAGINE, CONFORME. Sono
# sinonimi trasparenti per un lettore e stringhe sconosciute per un
# confronto di uguaglianza — e '_correggi' confrontava con due stringhe
# esatte, quindi **buttava via trentasei letture** in cui il modello
# diceva chiaramente che la pagina smentisce la trascrizione. E' il
# difetto opposto a quelli che questo modulo si e' fatto: non una
# correzione di troppo, una correzione mancata.
#
# La cura non e' allargare il confronto a qualunque cosa somigli a un
# conflitto. E' una tabella esplicita, che si legge e si discute.
_SINONIMI_VERDETTO = {
    # la pagina smentisce cio' che era stato trascritto
    "ERRORE_TRASCRIZIONE": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "ERRORE_DI_TRASCRIZIONE": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "ERRORE_NELLA_TRASCRIZIONE": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "CORRETTO": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "CORRETTO_DA_IMMAGINE": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "CORRETTO_DALL_IMMAGINE": "CONFLITTO_CON_LA_TRASCRIZIONE",
    "CORRETTO_DAL_GENEALOGISTA": "CONFLITTO_CON_LA_TRASCRIZIONE",
    # la pagina conferma
    "CONFORME": "CONFERMATO",
    "CONFERMATO_DA_IMMAGINE": "CONFERMATO",
    "CONFERMATO_DALL_IMMAGINE": "CONFERMATO",
    "CONFERMATO_CON_NOTE": "CONFERMATO",
    "CONFERMATO_CON_VARIANTE_GRAFICA": "CONFERMATO",
    "CONFERMATO_CON_DIVERGENZA": "AMBIGUO",
    "NON_PRESENTE_SULLA_PAGINA": "PROVE_INSUFFICIENTI",
}

# 'CORRETTO_DAL_CONTESTO' non sta nella tabella, e resta inerte apposta:
# dice che il modello ha cambiato la lettura per farla tornare con il
# grafo, cioe' esattamente cio' che il primo paragrafo di questo modulo
# vieta. Una lettura ottenuta cosi' non e' una prova, e non deve
# diventare una correzione per la porta di servizio dei sinonimi.


def _verdetto_canonico(grezzo) -> str:
    """Il verdetto ricondotto a uno dei sette, quando si puo'.

    Il grezzo resta com'e' nella riga di ``riletture``: l'archivio deve
    poter mostrare cosa il modello ha *detto*, non solo come lo abbiamo
    inteso.
    """
    if not isinstance(grezzo, str):
        return ""
    pulito = grezzo.strip().upper().replace("'", "_").replace(" ", "_")
    if pulito in VERDETTI:
        return pulito
    if pulito in _SINONIMI_VERDETTO:
        return _SINONIMI_VERDETTO[pulito]
    # I refusi del modello sul nome del verdetto stesso, che esistono:
    # 'CONFLITTO_CON_LA_TRASCRITTORE' e' costato una correzione buona
    # ('bavaro' -> 'bovaro', confidenza 0,95). Il prefisso e' abbastanza
    # lungo da non poter significare altro.
    if pulito.startswith("CONFLITTO_CON_LA_TRASC"):
        return "CONFLITTO_CON_LA_TRASCRIZIONE"
    if pulito.startswith("CONFLITTO_CON_IL_CONT"):
        return "CONFLITTO_CON_IL_CONTESTO"
    return pulito


def _senza_il_cognome(
    riga: dict, campo: str, nuova: str,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Toglie dal nome il cognome che il modello ci ha incollato dentro.

    Un modello che rilegge una firma legge cio' che c'e' scritto —
    «Giuseppe Pizzi Sindaco» — e lo mette tutto nel campo che gli e'
    stato chiesto. Il valore che ne esce e' **giusto come lettura e
    sbagliato come dato**: ``nome`` = «Giuseppe Pizzi» accanto a
    ``cognome`` = «Pizzi» fa una persona che si chiama «Giuseppe Pizzi
    Pizzi», e per il calcolo fa di peggio — la forma canonica
    'giusepepizi' non e' mai stata vista, quindi il nome piu' comune
    del paese diventa un indizio raro e pesa il doppio nel confronto
    fra due schede.

    La lettura non si butta, si sbuccia: scartarla vorrebbe dire
    perdere il nome del sindaco — che qui vale, perche' lo stesso uomo
    sta in archivio come «P. Pizzi», «G. Pizzi» e «Giuseppe Pizzi», tre
    schede per una firma abbreviata in tre modi.

    Solo in coda, e solo sul nome: gli atti di stato civile scrivono
    «nome cognome», mai il contrario, e togliere un pezzo dal cognome
    rovinerebbe le forme composte — «Di Nardo», «D'Ettorre» — che sono
    cognomi interi e non un nome piu' un cognome.
    """
    if campo != "nome":
        return nuova
    cognome = (riga.get("cognome") or "").strip()
    if cognome:
        suo = paleografia.normalizza(cognome)
        if paleografia.normalizza(nuova) == suo:
            return ""       # tutto cognome: non resta nessun nome
        parti = nuova.split()
        for taglio in range(1, len(parti)):
            if paleografia.normalizza(" ".join(parti[taglio:])) == suo:
                return " ".join(parti[:taglio])
    if conn is not None:
        return _senza_un_cognome_del_paese(conn, nuova)
    return nuova


# Quanto piu' spesso una forma deve comparire come cognome che come nome
# perche' toglierla dal nome sia sicuro. Dieci volte separa nettamente i
# due casi che contano: 'Marianacci' e' 622 volte un cognome e una volta
# un nome, 'Pizzi' 93 contro zero — mentre 'Salvatore', che e' un cognome
# del paese **ed e' anche un nome di battesimo vero**, sta a 245 contro
# 92 e non si tocca. Sui 822 nomi composti dell'archivio la soglia ne
# sbuccia 14, e sono tutti e quattordici nome-piu'-cognome incollati.
VOLTE_PER_DIRLO_UN_COGNOME = 10

# Cio' che non resta mai da solo come nome: sbucciare non deve ridurre
# 'Di Laudo' a 'Di'.
_PARTICELLE = frozenset({"di", "de", "d'", "da", "la", "lo", "del", "della"})


def _e_una_particella(parola: str) -> bool:
    """La parola apre un casato invece di essere un nome.

    Non basta l'insieme sopra: nei registri la particella sta attaccata,
    e «d'Armi» e' un token solo. Cercare la parola intera lasciava
    passare proprio i casati che si volevano riconoscere.
    """
    ripulita = (parola or "").casefold()
    return ripulita in _PARTICELLE or ripulita[:2] in ("d'", "l'", "d’", "l’")


def _senza_un_cognome_del_paese(conn: sqlite3.Connection, nuova: str) -> str:
    """Toglie dal nome un cognome del paese che non e' il suo.

    La guardia sopra confronta con il cognome **gia' trascritto** per
    quella persona, e non basta: il modello che rilegge la firma di un
    sindaco puo' correggere insieme nome e cognome, e allora nel nome
    finisce un cognome che nella trascrizione non c'era. E' il caso di
    «Giovanni Maria Nanni» riletto «Giovanni Marianacci»: nessuna delle
    due parole e' 'Nanni', e il confronto non scatta.

    Qui la domanda e' un'altra e la fa all'archivio: **questa parola, in
    questo paese, e' un cognome o un nome?** Non e' una regola sui nomi
    italiani in generale — 'Salvatore' e' tutt'e due — e' una misura su
    chi e' davvero vissuto qui.

    Si guarda in coda e in testa, perche' i registri cambiano ordine a
    meta' archivio: fino al 1865 «Maria Salvatore», dal 1866 «Salvatore
    Maria». Il cognome incollato nel nome arriva da tutt'e due le parti.
    """
    parti = nuova.split()
    if len(parti) < 2:
        return nuova

    def piu_cognome_che_nome(parola: str) -> bool:
        come_cognome = _attestazioni(conn, "cognome", parola)
        come_nome = _attestazioni(conn, "nome", parola)
        return come_cognome >= VOLTE_PER_DIRLO_UN_COGNOME * max(1, come_nome)

    resto = " ".join(parti[:-1])
    # Non basta che il resto non SIA una particella: non deve nemmeno
    # finirci. «Giuseppe di Tommaso» sbucciato di 'Tommaso' lasciava
    # «Giuseppe di», che non e' un nome ma un troncone — visto succedere
    # in una passata vera, una volta su 451 atti.
    if len(resto) >= 3 and not _e_una_particella(parti[-2]):
        if piu_cognome_che_nome(parti[-1]):
            return resto

    # E lo stesso davanti. Dal 1866 i registri scrivono prima il casato:
    # «da Salvatore Maria, di anni ventinove, contadina, sua moglie
    # legittima» e' la signora Maria Salvatore, e la trascrizione che
    # divide nome e cognome ha ragione. La rilettura pero' vede la
    # pagina, legge le due parole nell'ordine in cui stanno, e propone
    # «Salvatore Maria» come nome — sapendolo: la sua spiegazione dice
    # «per inversione tipica dei formulari», e lo fa lo stesso.
    davanti = " ".join(parti[1:])
    if (len(davanti) >= 3 and not _e_una_particella(parti[0])
            and not _e_una_particella(parti[1])):
        # La particella davanti a cio' che resta e' la stessa trappola
        # che si evita in coda, al contrario: «Franchetta d'Armi»
        # sbucciato di 'Franchetta' lasciava «d'Armi», che e' il casato,
        # non il nome — e la persona restava senza nome di battesimo.
        if piu_cognome_che_nome(parti[0]):
            return davanti
    return nuova


# Quante letture devono venire dall'atto accanto perche' l'intero
# gruppo si consideri copiato invece che letto. Due bastano, per la
# stessa ragione per cui bastano due inversioni in 'nomi': una
# coincidenza capita, due sono un'abitudine — qui l'abitudine di
# guardare la meta' di pagina sbagliata.
LETTURE_DALL_ATTO_ACCANTO = 2


def _valori_degli_atti_vicini(conn: sqlite3.Connection, atto: int) -> dict:
    """Cosa c'e' scritto negli atti confinanti, campo per campo.

    Serve a riconoscere la lettura presa dalla pagina sbagliata. Un atto
    non occupa una scansione intera: la sua coda sta in cima alla
    successiva, e la coda di quello prima sta sulla sua. Il modello ha
    dunque **sempre** sotto gli occhi pezzi di due atti che non sono il
    suo, e quando la pagina che gli tocca e' avara — un cognome sbiadito,
    una riga cancellata — prende quello che vede.

    Il caso che l'ha resa necessaria: l'atto di matrimonio n. 8 del 1813,
    dove la rilettura ha proposto di rinominare **tutti e quattro** i
    testimoni, e i quattro nomi nuovi erano, nell'ordine, i testimoni
    dell'atto n. 7 stampati sulla meta' sinistra della stessa scansione.
    """
    riga = conn.execute(
        "SELECT registro FROM atti WHERE id = ?", (atto,)
    ).fetchone()
    if riga is None:
        return {}
    vicini = [
        r[0] for r in conn.execute(
            "SELECT id FROM (SELECT id FROM atti WHERE registro = ? AND id < ? "
            "ORDER BY id DESC LIMIT 1) UNION SELECT id FROM (SELECT id FROM atti "
            "WHERE registro = ? AND id > ? ORDER BY id LIMIT 1)",
            (riga[0], atto, riga[0], atto),
        )
    ]
    if not vicini:
        return {}
    segnaposto = ",".join("?" * len(vicini))
    fuori: dict = {}
    for persona in conn.execute(
        f"SELECT nome, cognome, professione, via FROM persone "
        f"WHERE atto IN ({segnaposto})", vicini
    ):
        for campo in ("nome", "cognome", "professione", "via"):
            valore = persona[campo]
            if valore:
                fuori.setdefault(campo, set()).add(paleografia.normalizza(valore))
    return fuori


def _presenti_in_questo_atto(dato: dict) -> dict:
    """I valori che l'atto porta gia': non sono 'presi dal vicino'."""
    fuori: dict = {}
    for m in dato["trascrizione_precedente"]["menzioni"]:
        for campo in ("nome", "cognome", "professione"):
            valore = m.get(campo)
            if valore:
                fuori.setdefault(campo, set()).add(paleografia.normalizza(valore))
    return fuori


def _copiate_dall_atto_accanto(
    conn: sqlite3.Connection, dato: dict, candidate: list[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Le letture che vengono dalla pagina di un altro atto.

    Rende l'insieme da rifiutare, vuoto se il sospetto non regge. Una
    sola coincidenza non basta: in un paese di poche migliaia di anime
    gli stessi nomi tornano di continuo, e bocciare su una sola
    somiglianza vorrebbe dire perdere correzioni buone. Due nello stesso
    atto invece non sono un caso.
    """
    if len(candidate) < LETTURE_DALL_ATTO_ACCANTO:
        return set()
    vicini = _valori_degli_atti_vicini(conn, dato["atto"]["id"])
    if not vicini:
        return set()
    qui = _presenti_in_questo_atto(dato)
    sospette = {
        (campo, nuova) for campo, nuova in candidate
        if paleografia.normalizza(nuova) in vicini.get(campo, ())
        and paleografia.normalizza(nuova) not in qui.get(campo, ())
    }
    return sospette if len(sospette) >= LETTURE_DALL_ATTO_ACCANTO else set()



# Le parole con cui una spiegazione ammette di aver guardato altrove.
# Non sono sinonimi: 'precedente' e 'seguente' dicono *un altro atto*,
# e basta una di queste perche' la lettura non riguardi piu' questo.
_ALTROVE = re.compile(
    r"att[oi]\s+(?:n\.?\s*\d+\s+)?(?:precedent|success|seguent|"
    r"anterior|prima|dopo)|"
    r"(?:fine|coda|inizio|principio|testa)\s+dell['’]att[oa]\s+"
    r"(?:precedent|success|seguent)|"
    r"pagina\s+(?:precedent|success|seguent)",
    re.I,
)

# 'atto n. 7', 'atto numero sette', 'l'atto 12': il numero citato.
_ATTO_CITATO = re.compile(
    r"att[oi]\s+(?:n(?:umero)?\.?\s*)?([0-9]+|[a-zàèéìòù]+)",
    re.I,
)


def _spiegazione_guarda_altrove(spiegazione: str, numero_atto) -> bool:
    """La spiegazione confessa di aver letto fuori dai bordi dell'atto?

    E' il complemento di :func:`_copiate_dall_atto_accanto`, e prende i
    casi che quella non vede. Li' si riconosce la copia dal *risultato* —
    il valore nuovo e' identico a un valore del vicino — e per non
    bocciare i molti omonimi del paese ci vogliono due indizi. Qui si
    riconosce dal *movente*, che il modello scrive da se':

        «L'immagine 1 (in cima a sinistra, fine dell'atto precedente)
        mostra chiaramente il nome 'Nicolangela'»

    e quella era davvero la neonata dell'atto prima. Una confessione non
    ha bisogno di conferma: ne basta una.

    Attenzione a non bocciare il caso opposto, che e' sano. Un atto non
    finisce con la scansione, e alla rilettura si allega apertamente la
    scansione seguente; dire «l'immagine 2, che contiene la parte finale
    dell'atto n. 1» quando *questo* e' l'atto n. 1 non e' guardare
    altrove, e' leggere l'atto fino in fondo. Percio' un numero d'atto
    citato manda in fumo la correzione **solo se e' un altro numero**.
    """
    if not spiegazione:
        return False
    if _ALTROVE.search(spiegazione):
        return True
    mio = ricuci.numero(numero_atto)
    if mio is None:
        return False
    for trovato in _ATTO_CITATO.finditer(spiegazione):
        altro = ricuci.numero(trovato.group(1))
        if altro is not None and altro != mio:
            return True
    return False


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
    candidate: list[tuple] = []
    for campo in letto.get("campi", []):
        if _verdetto_canonico(campo.get("verdetto")) not in (
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
        if not _e_una_lettura(nuova):
            logger.info(
                "correzione scartata: %r su %s non e' una lettura, e' "
                "un'assenza o un commento", nuova, quale,
            )
            continue
        sbucciata = _senza_il_cognome(riga, quale, nuova, conn)
        if not sbucciata:
            logger.info(
                "correzione scartata: %r sul nome e' tutto cognome", nuova,
            )
            continue
        if sbucciata != nuova:
            logger.info(
                "dal nome «%s» tolgo il cognome che ci era finito dentro: «%s»",
                nuova, sbucciata,
            )
            nuova = sbucciata
        vecchia = (riga.get(quale) or "").strip()
        if paleografia.normalizza(nuova) == paleografia.normalizza(vecchia):
            continue        # conferma la lettura: non e' una correzione
        if not _plausibile(conn, quale, nuova, vecchia):
            logger.info(
                "correzione scartata: %r -> %r su %s non ha nessuna "
                "attestazione contro una forma gia' nota", vecchia, nuova, quale,
            )
            continue
        spiegazione = campo.get("spiegazione", "")
        if _spiegazione_guarda_altrove(
            spiegazione, dato["atto"].get("numero")
        ):
            logger.info(
                "correzione scartata: %r -> %r su %s si regge su un punto "
                "fuori da questo atto — «%s»", vecchia, nuova, quale, spiegazione,
            )
            continue
        gia = conn.execute(
            "SELECT 1 FROM decisioni WHERE azione = 'correzione' AND entita = ? "
            "AND evidenze LIKE ?", (json.dumps([menzione]), f"{quale}=%")
        ).fetchone()
        if gia is not None:
            continue
        candidate.append((menzione, quale, vecchia, nuova, confidenza,
                          spiegazione))

    # Il giudizio d'insieme, che sulla singola riga non si puo' dare: se
    # due o piu' letture di questo atto vengono dalla pagina di un altro,
    # non sono letture, e' la meta' di pagina sbagliata.
    copiate = _copiate_dall_atto_accanto(
        conn, dato, [(q, n) for _, q, _, n, _, _ in candidate])
    for menzione, quale, vecchia, nuova, confidenza, spiegazione in candidate:
        if (quale, nuova) in copiate:
            logger.info(
                "correzione scartata: %r -> %r su %s e' un valore dell'atto "
                "accanto, e non e' la sola", vecchia, nuova, quale,
            )
            continue
        registro.annota(
            conn, "correzione", (menzione,),
            f"{quale}: letto «{vecchia}», sull'immagine «{nuova}»; "
            f"{spiegazione}".strip(),
            confidenza=confidenza,
            decisore="gemini",
            modello=modello,
            versione_prompt=VERSIONE_PROMPT,
            evidenze=(f"{quale}={nuova}",),
        )
        corrette += 1
    return corrette


# ---------------------------------------------------------------------------
# Tornare su cio' che si e' deciso prima di sapere
# ---------------------------------------------------------------------------
#
# Le prime 437 pagine sono state rilette con il prompt 1.0.0: senza il
# vocabolario del paese, senza le regole 6-7, e soprattutto **prima che
# esistesse il veto di plausibilita'**. Le loro correzioni sono in vigore
# come tutte le altre, e la misura dice cosa vale quel batch: il 65% delle
# correzioni su nome e cognome sostituisce una parola con un'altra che non
# le somiglia — non sono riletture, sono sostituzioni.
#
# Il rimedio non e' una soglia inventata adesso. E' **ripassare quelle
# decisioni al controllo che non hanno avuto**: la stessa 'plausibile' che
# oggi governa ogni correzione nuova. Cosi' l'archivio torna coerente con
# la sua regola, invece di avere due epoche con due metri diversi.

# Una forma di nome o cognome sono una o due parole, tre al massimo per i
# composti del paese: 'Maria Nicola', 'Giuseppe Antonio', 'Di Nardo'.
# Oltre, e' una frase — e una frase nel campo del nome e' un modello che
# ha risposto a una domanda diversa da quella fatta.
PAROLE_MASSIME_IN_UN_NOME = 3

# Cosa non compare mai dentro un nome, e sempre dentro una risposta che
# ragiona invece di leggere: parentesi, virgolette, punti interrogativi,
# e le parole con cui un modello elenca le sue incertezze.
_NON_E_UN_NOME = re.compile(
    r"[()\[\]?\"«»]|\boppure\b|\bnel testo\b|\bignot[oa]\b|\bnessun", re.I
)


# Gli onorifici non fanno parte di un nome ne' di un cognome: sono un
# titolo che l'atto premette. 'Zeno' corretto in «Donna Federica Zara»
# stava in un campo COGNOME, ed e' tre parole — dentro il tetto dei
# composti — ma nessun casato di questo paese comincia per 'Donna'.
_ONORIFICI = re.compile(r"^(don|donna|signor[ae]?|sig\.?|mastro|maestro)\b", re.I)


def _forma_da_nome(valore: str) -> bool:
    """Se questa stringa puo' essere un nome o un cognome, come forma.

    Non dice se e' *giusta* — dice se e' della categoria giusta. E'
    un controllo di struttura, non di merito, e per questo si puo'
    applicare a decisioni prese anni fa senza riguardare le immagini.
    """
    if not valore or _NON_E_UN_NOME.search(valore):
        return False
    if _ONORIFICI.match(valore.strip()):
        return False
    return 1 <= len(valore.split()) <= PAROLE_MASSIME_IN_UN_NOME


# I campi su cui una correzione si puo' ripassare. Oltre a quelli con
# vocabolario ci sono l'eta' e la via: non hanno forme da confrontare,
# ma possono benissimo ricevere un'assenza travestita da lettura.
CAMPI_CORREGGIBILI = CAMPI_CON_VOCABOLARIO | frozenset({"eta", "via"})

# I decisori che 'ripassa_al_veto' non tocca, gia' pronti per un IN di
# SQL. L'algoritmo e' escluso perche' le sue correzioni le rifa' da solo;
# gli altri perche' hanno guardato la pagina, e una statistica non
# ribalta chi ha guardato (vedi ``registro.DECISORI_CHE_HANNO_GUARDATO``).
_DECISORI_INTOCCABILI = ", ".join(
    "'" + chi + "'"
    for chi in ("algoritmo",) + tuple(_registro.DECISORI_CHE_HANNO_GUARDATO)
)


def _perche_non_regge(conn, persona, campo, nuova, vecchia):
    """Il motivo per cui una correzione gia' presa oggi non passerebbe.

    Rende ``(motivo, sostituto)``, e ``(None, None)`` se regge ancora.
    Il sostituto c'e' solo quando la lettura era giusta e mal
    impacchettata — il cognome finito dentro il nome si sbuccia, non si
    butta.
    """
    sbucciata = _senza_il_cognome(dict(persona), campo, nuova, conn)
    if sbucciata and sbucciata != nuova:
        return "cognome nel nome", sbucciata
    if campo in ("nome", "cognome") and not _forma_da_nome(nuova):
        return "non e' una forma di nome", None
    if _NON_E_UN_NOME.search(nuova):
        # 'contadina' -> «coi [coabitante]»: una parentesi quadra e' il
        # modello che ragiona, non la pagina che parla.
        return "non e' una lettura, e' un'annotazione", None
    if not _plausibile(conn, campo, nuova, vecchia):
        return "non plausibile", None
    return None, None


def ripassa_al_veto(
    conn: sqlite3.Connection, versione: str, applica: bool = False,
) -> list[dict]:
    """Ripassa le correzioni di una versione di prompt ai controlli di oggi.

    Rende l'elenco di cio' che non regge, con il motivo. Con ``applica``
    scrive nel registro le decisioni che le superano — **non le cancella**:
    ogni riga vecchia resta dov'e', marcata da quella che la disfa, e chi
    domani chiedera' «perche' l'archivio ha cambiato idea?» trovera' la
    risposta scritta.

    Tre motivi, in ordine di quanto sono sicuri:

    * ``cognome nel nome`` — il cognome incollato dentro il nome. Non si
      annulla: si sbuccia, perche' la lettura era giusta e solo
      impacchettata male.
    * ``non e' una forma di nome`` — una frase, o un elenco di dubbi, nel
      campo di un nome.
    * ``non plausibile`` — il veto vero: una forma mai vista in paese
      contro una gia' ben attestata.

    Non tocca le decisioni di chi ha aperto la pagina e guardato — una
    persona, o il modello che ha chiesto la scansione invece di fidarsi
    della trascrizione (:data:`registro.DECISORI_CHE_HANNO_GUARDATO`).
    Il veto sulla plausibilita' e' statistico, e una statistica non deve
    poter ribaltare chi ha guardato: 'barilaio', letto a piena
    risoluzione su un atto del 1887, non ha nessun'altra attestazione in
    archivio — ed e' quello che c'e' scritto.
    """
    from history_maker.ricostruzione import registro

    superate = {
        riga[0] for riga in conn.execute(
            "SELECT disfa FROM decisioni WHERE disfa IS NOT NULL"
        )
    }
    verdetti = []
    for riga in conn.execute(
        "SELECT d.id, d.entita, d.evidenze, d.modello, d.motivo, a.numero_atto "
        "FROM decisioni d LEFT JOIN persone p ON p.id = CAST("
        "json_extract(d.entita, '$[0]') AS INTEGER) "
        "LEFT JOIN atti a ON a.id = p.atto WHERE d.azione = 'correzione' "
        f"AND d.decisore NOT IN ({_DECISORI_INTOCCABILI}) "
        "AND d.versione_prompt = ? "
        "ORDER BY d.id", (versione,)
    ):
        if riga["id"] in superate:
            continue
        try:
            menzione = json.loads(riga["entita"])[0]
        except (TypeError, ValueError, IndexError):
            continue
        for prova in (riga["evidenze"] or "").split(" | "):
            campo, _, nuova = prova.partition("=")
            campo, nuova = campo.strip(), nuova.strip()
            if campo not in CAMPI_CORREGGIBILI or not nuova:
                continue
            persona = conn.execute(
                "SELECT nome, cognome, professione, eta, via FROM persone "
                "WHERE id = ?", (menzione,),
            ).fetchone()
            if persona is None:
                continue
            vecchia = (persona[campo] or "").strip()

            # I due controlli che non hanno bisogno di vocabolario, e che
            # quindi valgono anche sull'eta' e sulla via.
            if not _e_una_lettura(nuova):
                motivo, sostituto = "non e' una lettura, e' un'assenza", None
            elif _spiegazione_guarda_altrove(riga["motivo"], riga["numero_atto"]):
                # Se la prova viene da un altro atto non c'e' niente da
                # sbucciare ne' da soppesare: la lettura non riguarda
                # questa pagina, e cade prima di ogni altro giudizio.
                motivo, sostituto = "letta fuori da questo atto", None
            elif campo not in CAMPI_CON_VOCABOLARIO:
                continue        # l'eta' e le date non hanno un vocabolario
            else:
                motivo, sostituto = _perche_non_regge(conn, persona, campo,
                                                      nuova, vecchia)
                if motivo is None:
                    continue
            verdetti.append({
                "decisione": riga["id"], "menzione": menzione, "campo": campo,
                "vecchia": vecchia, "nuova": nuova, "motivo": motivo,
                "sostituto": sostituto,
            })
            if not applica:
                continue
            spiegazione = (
                f"{campo}: la correzione «{nuova}» e' stata presa con il prompt "
                f"{versione}, prima che esistesse il controllo che oggi la "
                f"boccia ({motivo})."
            )
            registro.annota(
                conn, "correzione", (menzione,),
                spiegazione + (
                    f" Al suo posto: «{sostituto}»." if sostituto
                    else f" Torna a valere la trascrizione: «{vecchia}»."
                ),
                confidenza=1.0, decisore="persona",
                modello=riga["modello"], versione_prompt=VERSIONE_PROMPT,
                evidenze=(f"{campo}={sostituto}",) if sostituto else (),
                disfa=riga["id"],
            )
    return verdetti


def da_rifare(
    conn: sqlite3.Connection, versione: str, applica: bool = False,
) -> list[int]:
    """Rimette in coda le pagine lette con una vecchia versione di prompt.

    Rende gli atti coinvolti. Con ``applica`` marca le loro riletture come
    ``superata``: non le cancella — la risposta vecchia resta leggibile,
    ed e' l'unica prova di come rispondeva quel prompt — ma non conta piu'
    ne' come risposta data (:func:`_gia_risposte`) ne' come risposta
    riusabile (:func:`esegui`), quindi quelle pagine tornano in coda e si
    rifanno con il prompt di adesso.
    """
    try:
        atti = [
            riga[0] for riga in conn.execute(
                "SELECT DISTINCT atto FROM riletture WHERE versione_prompt = ? "
                "AND stato = 'risposta' ORDER BY atto", (versione,)
            )
        ]
    except sqlite3.OperationalError:
        return []
    if applica and atti:
        conn.execute(
            "UPDATE riletture SET stato = 'superata' WHERE versione_prompt = ? "
            "AND stato = 'risposta'", (versione,)
        )
    return atti
