"""Tornare sull'immagine, una parola per volta.

La trascrizione e' gia' stata fatta e non si rifa': un secolo di registri
sono migliaia di pagine, e ritrascriverle costerebbe giorni di quota per
riottenere quello che c'e' gia'. Ma la trascrizione **non e' la verita'**,
e in un pugno di casi la differenza fra due ricostruzioni sta in come e'
scritta una parola sulla carta.

Quei casi si riconoscono: sono quelli in cui il grafo ha due
interpretazioni ugualmente coerenti e la sola cosa che le separa e' una
lettura. Li' vale la pena tornare all'originale — e solo li'.

La domanda giusta
-----------------

Non 'ritrascrivi questa pagina' ma **'il cognome del padre in questo atto
e' Lella o Lelli?'**. Una domanda chiusa, con le alternative in chiaro, su
un ritaglio piccolo. Tre vantaggi, tutti e tre grossi:

* il modello risponde meglio, perche' scegliere fra due forme e' piu'
  facile che leggere da zero;
* il ritaglio viene dall'immagine a **piena risoluzione**, non dalla copia
  ridotta che aveva letto la prima volta: e' la stessa pagina con tre volte
  i pixel, ed e' spesso li' che sta la differenza;
* costa una frazione dei token di una pagina intera.

Dove ritagliare lo dice la trascrizione stessa: il testo integrale
dell'atto contiene la parola, e la sua posizione nel testo e'
approssimativamente la sua posizione nella pagina. Quando la parola non si
trova si ritaglia l'intero specchio di scrittura, che costa di piu' ma e'
sempre meglio della pagina intera.

La quota
--------

Cinquecento richieste al giorno non sono infinite, e ogni domanda gia'
fatta e' una quota gia' spesa: la cache e' indicizzata su tutto cio' che
influenza la risposta — atto, immagine, domanda, alternative, modello,
versione del prompt — e una domanda ripetuta non parte nemmeno. Il ritmo,
il conteggio giornaliero e l'attesa sono quelli gia' scritti per la fase 3
(:class:`history_maker.gemini.Ritmo`): non c'e' motivo di averne due.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from history_maker.backend import LimiteUsoRaggiunto, Richiesta
from history_maker import paleografia
from history_maker.ricostruzione import cache, modello as mod
from history_maker.ricostruzione.modello import Verifica

logger = logging.getLogger(__name__)

VERSIONE_PROMPT = "1.0.0"

# Quante richieste al massimo per una sola esecuzione. Non e' la quota —
# quella la tiene il backend — e' una difesa contro l'errore di battitura
# che manda in coda diecimila domande.
TETTO_PER_ESECUZIONE = 200


SISTEMA = """Sei un paleografo che legge registri di stato civile italiani
dell'Ottocento. Ti viene mostrato il RITAGLIO di una pagina originale, a
piena risoluzione, e ti viene fatta una domanda precisa su una parola.

Rispondi solo su quello che vedi. Se la parola non e' nel ritaglio, o non
si legge, dillo: 'non leggibile' e' una risposta utile, una risposta
inventata no. Se la grafia ammette due letture, dille tutte e due."""


SCHEMA_RISPOSTA = {
    "type": "object",
    "properties": {
        "lettura": {"type": "string"},
        "fra_le_alternative": {"type": "string"},
        "confidenza": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["lettura", "confidenza"],
}


def istruzione(verifica: Verifica) -> str:
    """La domanda, con le alternative in chiaro e il permesso di dire di no."""
    righe = [
        f"Nell'atto n. {verifica.atto} di questo registro, guarda "
        f"{verifica.campo}.",
        "",
        f"Domanda: {verifica.domanda}",
    ]
    if verifica.alternative:
        righe += [
            "",
            "Le letture che altri documenti dello stesso archivio suggeriscono:",
        ]
        righe += [f"  - {forma}" for forma in verifica.alternative]
        righe += [
            "",
            "Puo' anche non essere nessuna di queste: in quel caso scrivi cosa "
            "c'e' scritto davvero.",
        ]
    if verifica.bersaglio:
        righe += ["", f"La parola dovrebbe trovarsi vicino a: «{verifica.bersaglio}»."]
    righe += [
        "",
        "Rispondi con un solo oggetto JSON:",
        json.dumps(
            {
                "lettura": "<cosa c'e' scritto>",
                "fra_le_alternative": "<quale delle alternative, o 'nessuna'>",
                "confidenza": "<numero fra 0 e 1>",
                "note": "<cosa rende la lettura difficile, se lo e'>",
            },
            ensure_ascii=False, indent=1,
        ),
    ]
    return "\n".join(righe)


# ---------------------------------------------------------------------------
# Da un'anomalia a una domanda
# ---------------------------------------------------------------------------

# Le anomalie che una domanda all'immagine puo' davvero risolvere. Le
# altre — un'omonimia, un duplicato che dipende da tutta la rete
# familiare — non si risolvono guardando una parola, e mandarcele
# spenderebbe quota senza cambiare niente.
RISOLVIBILI_A_VISTA = {
    "SURNAME_ANOMALY": ("cognome", "il cognome"),
    "NAME_ANOMALY": ("nome", "il nome di battesimo"),
    "AGE_ANOMALY": ("eta", "l'eta' dichiarata"),
    "TRANSCRIPTION_ANOMALY": ("cognome", "il nome e il cognome"),
    "PROFESSION_ANOMALY": ("professione", "la professione"),
    "ADDRESS_ANOMALY": ("via", "la contrada"),
}

# Quanto deve essere sicuro il modello perche' la sua lettura corregga il
# dato. Sotto, la risposta resta nell'archivio come opinione: e' il
# principio di tutta questa fase, che una risposta non diventa mai un
# fatto per il solo fatto di essere arrivata.
FIDUCIA_PER_CORREGGERE = 0.8


def domande_da(esito, anomalie: list[mod.Anomalia], quante: int = 50) -> list[Verifica]:
    """Trasforma le anomalie che si possono guardare in domande mirate."""
    domande: list[Verifica] = []
    # Una persona, un campo, una domanda. Lo stesso dubbio si presenta
    # spesso su quattro atti diversi — la stessa eta' sbagliata ripetuta
    # in quattro registri — e farne quattro domande spenderebbe quattro
    # volte la quota per la stessa risposta.
    gia_chiesto: set[tuple] = set()
    for anomalia in sorted(anomalie, key=lambda a: -a.priorita):
        coppia = RISOLVIBILI_A_VISTA.get(anomalia.tipo)
        if coppia is None or not anomalia.atti:
            continue
        campo_dati, campo = coppia
        scheda = esito.schede.get(anomalia.individui[0])
        if scheda is None:
            continue
        if (scheda.chiave, campo) in gia_chiesto:
            continue
        gia_chiesto.add((scheda.chiave, campo))
        atto = anomalia.atti[0]
        dati = esito.corpus.atti.get(atto) or {}
        immagine = dati.get("immagine")
        if not immagine:
            continue
        alternative = _alternative(anomalia, esito, scheda)
        bersaglio = _bersaglio(scheda, atto, campo)
        verifica = Verifica(
            atto=atto,
            immagine=immagine,
            campo=campo,
            campo_dati=campo_dati,
            domanda=_domanda(anomalia, campo, alternative),
            alternative=alternative,
            menzione=_menzione(scheda, atto),
            bersaglio=bersaglio,
            priorita=anomalia.priorita,
            motivo=anomalia.descrizione,
        )
        verifica.chiave_cache = chiave(verifica, "")
        domande.append(verifica)
        if len(domande) >= quante:
            break
    return domande


def _domanda(anomalia: mod.Anomalia, campo: str, alternative: tuple) -> str:
    """La domanda, formulata perche' si possa rispondere guardando.

    Chiusa quando le alternative sono note — scegliere fra due forme e'
    piu' facile che leggere da zero — e aperta quando non lo sono.
    L'eta' ha una formulazione sua: li' le due candidate non sono due
    grafie ma due numeri, e il modo di chiederlo e' dire quale sarebbe
    l'altro e perche'.
    """
    if anomalia.tipo == "AGE_ANOMALY" and len(alternative) >= 2:
        return (
            f"Che eta' e' scritta qui? La trascrizione dice «{alternative[0]}»; "
            f"il suo atto di nascita ne farebbe circa {alternative[1]}."
        )
    if len(alternative) >= 2:
        return f"{campo} scritto qui e' «{alternative[0]}» o «{alternative[1]}»?"
    return f"Come e' scritto {campo} in questo atto?"


def _alternative(anomalia: mod.Anomalia, esito, scheda) -> tuple[str, ...]:
    """Le forme fra cui il grafo esita, quella letta per prima."""
    if anomalia.alternative:
        return tuple(dict.fromkeys(f for f in anomalia.alternative if f))
    forme: list[str] = []
    if anomalia.campo == "cognome":
        forme.extend(f for f, _ in scheda.cognomi.most_common(2))
        for altra in anomalia.individui[1:]:
            vicina = esito.schede.get(altra)
            if vicina is not None:
                forme.extend(f for f, _ in vicina.cognomi.most_common(1))
    elif anomalia.campo in ("nome", None, ""):
        forme.extend(f for f, _ in scheda.nomi.most_common(2))
    visti: dict[str, None] = {}
    for forma in forme:
        if forma:
            visti.setdefault(forma, None)
    return tuple(visti)


def _bersaglio(scheda, atto: int, campo: str = "") -> str:
    """La parola attorno a cui ritagliare, dentro il testo dell'atto.

    E' il campo su cui si dubita, non un campo qualsiasi: per una
    professione la banda va cercata attorno alla professione, che negli
    atti sta a qualche riga di distanza dal nome.
    """
    for menzione in scheda.menzioni:
        if menzione.atto != atto:
            continue
        if campo.endswith("professione"):
            return menzione.professione or menzione.cognome_letto or ""
        if campo.endswith("contrada"):
            return menzione.via or menzione.residenza or ""
        return menzione.cognome_letto or menzione.nome_letto or ""
    return ""


def _menzione(scheda, atto: int) -> int | None:
    for menzione in scheda.menzioni:
        if menzione.atto == atto:
            return menzione.id
    return None


# ---------------------------------------------------------------------------
# L'esecuzione
# ---------------------------------------------------------------------------

def chiave(verifica: Verifica, modello: str) -> str:
    """L'impronta di tutto cio' che influenza la risposta.

    Se cambia il modello o la versione del prompt la chiave cambia, e la
    domanda si rifa': una risposta data da un altro modello e' un'altra
    risposta, e spacciarla per questa sarebbe peggio che non averla.
    """
    return cache.impronta(
        verifica.atto, verifica.immagine, verifica.campo, verifica.domanda,
        list(verifica.alternative), modello, VERSIONE_PROMPT,
    )


def gia_risposte(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Le domande gia' fatte, per non ripagarle."""
    conn.row_factory = sqlite3.Row
    try:
        return {
            riga["chiave"]: riga
            for riga in conn.execute("SELECT * FROM verifiche")
        }
    except sqlite3.OperationalError:
        return {}


def esegui(
    config, conn: sqlite3.Connection, verifiche: list[Verifica],
    tetto: int = TETTO_PER_ESECUZIONE,
) -> dict:
    """Fa le domande che restano da fare, e si ferma quando la quota finisce.

    Non solleva mai per esaurimento di quota: il lavoro fatto e' gia'
    salvato e rilanciare riprende da li'. E' la stessa scelta della fase
    3, e per la stessa ragione — su migliaia di domande fermarsi e'
    normale, non e' un errore.
    """
    from history_maker import backend as motori
    from history_maker import ritaglio

    motore = motori.crea(config)
    motore.verifica()
    modello_scelto = config.trascrizione.modello
    fatte = gia_risposte(conn)
    esiti = {"riusate": 0, "fatte": 0, "fallite": 0, "quota_esaurita": False}
    cartella = Path(config.dataset) / "ritagli"

    for verifica in verifiche[:tetto]:
        verifica.chiave_cache = chiave(verifica, modello_scelto)
        precedente = fatte.get(verifica.chiave_cache)
        if precedente is not None and precedente["stato"] == "risposta":
            verifica.risposta = precedente["risposta"]
            verifica.confidenza = precedente["confidenza"] or 0.0
            verifica.stato = "risposta"
            esiti["riusate"] += 1
            # Anche una risposta ripescata dalla cache deve poter
            # correggere: la quota e' gia' stata spesa, e se la
            # correzione non fosse ancora stata registrata — perche' la
            # domanda e' di ieri — perderla sarebbe uno spreco doppio.
            if _correggi(conn, verifica, modello_scelto):
                esiti["correzioni"] = esiti.get("correzioni", 0) + 1
                conn.commit()
            continue

        percorso = Path(config.immagini) / verifica.immagine
        if not percorso.exists():
            logger.warning("manca l'immagine %s", percorso)
            verifica.stato = "saltata"
            _salva(conn, verifica, modello_scelto)
            continue

        try:
            banda = ritaglio.banda_per(
                percorso, _testo_atto(conn, verifica.atto), verifica.bersaglio
            )
            ritagliata = ritaglio.ritaglia(
                percorso, banda, cartella / f"{verifica.chiave_cache}.jpg"
            )
        except Exception as errore:            # un'immagine rovinata non ferma tutto
            logger.warning("ritaglio fallito su %s: %s", percorso, errore)
            verifica.stato = "fallita"
            _salva(conn, verifica, modello_scelto)
            esiti["fallite"] += 1
            continue

        richiesta = Richiesta(
            sistema=SISTEMA,
            istruzione=istruzione(verifica),
            immagini=[ritagliata],
            schema=SCHEMA_RISPOSTA,
            modello=modello_scelto,
            timeout_s=config.trascrizione.timeout_s,
        )
        try:
            risposta = motore.esegui(richiesta)
        except LimiteUsoRaggiunto as limite:
            logger.info("quota esaurita: %s", limite)
            esiti["quota_esaurita"] = True
            break

        if not risposta.ok:
            verifica.stato = "fallita"
            esiti["fallite"] += 1
        else:
            dati = _interpreta(risposta.testo)
            verifica.risposta = dati.get("lettura", "")
            verifica.confidenza = float(dati.get("confidenza") or 0.0)
            verifica.stato = "risposta"
            esiti["fatte"] += 1
        verifica.modello = modello_scelto
        verifica.quando = _adesso()
        _salva(conn, verifica, modello_scelto)
        if _correggi(conn, verifica, modello_scelto):
            esiti["correzioni"] = esiti.get("correzioni", 0) + 1
        conn.commit()
    return esiti


def _correggi(conn: sqlite3.Connection, verifica: Verifica, modello_scelto: str) -> bool:
    """Trasforma una lettura sicura e diversa in una correzione registrata.

    E' il passo che chiude il ciclo. Senza, una risposta dall'immagine
    resta una riga in una tabella e non cambia niente: la ricostruzione
    successiva rileggerebbe la stessa eta' sbagliata e rifarebbe lo stesso
    errore.

    La correzione **non riscrive la trascrizione**: e' una decisione con
    un autore, e la lettura originale resta dov'e'. La ricostruzione la
    applica come interpretazione (vedi ``registro.correzioni``), e se
    domani si scoprira' che quel modello sbagliava un certo tipo di
    parole si potra' vedere quali e disfarle.
    """
    if verifica.stato != "risposta" or verifica.menzione is None:
        return False
    # Una correzione gia' registrata non si registra due volte: le
    # decisioni si accumulano, e ripeterle a ogni esecuzione riempirebbe
    # il registro di copie della stessa cosa.
    gia = conn.execute(
        "SELECT 1 FROM decisioni WHERE azione = 'correzione' AND entita = ? "
        "AND evidenze LIKE ?",
        (json.dumps([verifica.menzione]), f"{verifica.campo_dati}=%"),
    ).fetchone()
    if gia is not None:
        return False
    letta = (verifica.alternative[0] if verifica.alternative else "").strip()
    risposta = (verifica.risposta or "").strip()
    if not risposta or verifica.confidenza < FIDUCIA_PER_CORREGGERE:
        return False
    if paleografia.normalizza(risposta) == paleografia.normalizza(letta):
        return False       # conferma la lettura: e' un risultato, non una correzione

    from history_maker.ricostruzione import registro

    registro.annota(
        conn, "correzione", (verifica.menzione,),
        f"{verifica.campo_dati}: letto «{letta}», sull'immagine «{risposta}»",
        confidenza=verifica.confidenza,
        decisore="gemini",
        modello=modello_scelto,
        versione_prompt=VERSIONE_PROMPT,
        atti=(verifica.atto,),
        evidenze=(f"{verifica.campo_dati}={risposta}",),
        contraddizioni=(f"la trascrizione diceva {letta}",) if letta else (),
    )
    return True


def _testo_atto(conn: sqlite3.Connection, atto: int) -> str | None:
    riga = conn.execute(
        "SELECT testo_integrale FROM atti WHERE id = ?", (atto,)
    ).fetchone()
    return riga[0] if riga else None


def _interpreta(testo: str) -> dict:
    """La risposta del modello, gia' sciolta dal blocco markdown.

    ``estrai_json`` **rende gia' la struttura**, non il testo: passarla a
    ``json.loads`` solleva un TypeError, e il ramo di ripiego trasformava
    ogni risposta buona in una vuota. Costava una richiesta di quota per
    volta e non lasciava traccia, perche' una risposta vuota e' esattamente
    cio' che si vede quando il modello non sa leggere.
    """
    from history_maker.backend import estrai_json

    try:
        dati = estrai_json(testo)
    except (ValueError, TypeError):
        return {"lettura": "", "confidenza": 0.0}
    return dati if isinstance(dati, dict) else {"lettura": "", "confidenza": 0.0}


def _salva(conn: sqlite3.Connection, verifica: Verifica, modello_scelto: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO verifiche (chiave, atto, immagine, campo, domanda, "
        "alternative, menzione, risposta, confidenza, stato, modello, "
        "versione_prompt, quando, priorita, motivo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            verifica.chiave_cache, verifica.atto, verifica.immagine, verifica.campo,
            verifica.domanda, " | ".join(verifica.alternative), verifica.menzione,
            verifica.risposta, verifica.confidenza, verifica.stato, modello_scelto,
            VERSIONE_PROMPT, verifica.quando or _adesso(), verifica.priorita,
            verifica.motivo,
        ),
    )


def _adesso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
