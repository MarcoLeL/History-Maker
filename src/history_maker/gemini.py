"""Trascrizione con Gemini, sul piano gratuito di Google AI Studio.

E' l'alternativa a Claude Code per il vincolo che rendeva impraticabile
un secolo di registri: non il denaro, il **tempo**. Con l'abbonamento Pro
la quota si rinnova a finestre di ore e regge circa un anno di atti per
finestra; novant'anni diventano mesi di attesa.

Tre differenze rispetto alla CLI, tutte a favore:

**Nessun sovraccarico per chiamata.** Claude Code porta ~50.000 token di
prompt di sistema e definizioni di strumenti a ogni invocazione: a dieci
pagine per chiamata sono 5.000 token a pagina di pura impalcatura, contro
i ~2.100 dell'immagine vera. Qui la richiesta contiene solo cio' che
serve. Sul piano gratuito questo non si traduce in risparmio ma in
**pagine per giorno**, che e' la valuta che conta.

**Lo schema si puo' imporre.** ``responseSchema`` vincola la risposta a
:data:`history_maker.schema.PAGINA`: niente blocco markdown da ripulire,
niente chiavi inventate. Il validatore resta comunque in mezzo, perche'
un vincolo di forma non e' un vincolo di senso.

**Le immagini viaggiano inline.** Non c'e' uno strumento ``Read`` e il
percorso su disco non significa niente per il modello: ogni immagine e'
preceduta da un'etichetta che ne ripete il nome del file, cosi'
l'allineamento della risposta alle pagine regge come prima.

Il prezzo da pagare e' il ritmo: il piano gratuito conta le richieste al
minuto e al giorno, e superarlo non e' un errore da ritentare subito ma
un turno da aspettare. Se ne occupa :class:`Ritmo`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from history_maker.backend import (
    BackendNonDisponibile,
    LimiteUsoRaggiunto,
    Richiesta,
    Risposta,
)
from history_maker.config import Config

logger = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
ENDPOINT = _BASE + "/{modello}:generateContent"
ENDPOINT_MODELLI = _BASE + "?pageSize=200"

# La chiave si legge dall'ambiente, come CHROME_BINARY e CHROMEDRIVER nel
# resto del progetto. Il primo nome e' quello di Google AI Studio, il
# secondo quello storico: accettarli entrambi evita l'errore piu' comune
# al primo avvio.
VARIABILI_CHIAVE = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# Stati con cui l'API segnala che la quota e' finita, non che la richiesta
# fosse sbagliata.
STATI_DI_QUOTA = {"RESOURCE_EXHAUSTED"}

# Tetto alla richiesta con le immagini allegate inline. Il limite dell'API
# e' 20 MB; si sta sotto con un margine, perche' sforarlo a meta' di un
# lavoro di giorni e' un errore di rete che non dice la sua causa.
LIMITE_RICHIESTA_BYTE = 18_000_000

# Quanto aspettare quando il modello risponde "high demand". E' una
# congestione dalla parte di Google, tipicamente breve: mezzo minuto e si
# ritenta lo stesso gruppo. Il tetto ai ritentativi in transcribe.py
# impedisce che diventi un'attesa senza fine.
ATTESA_SERVIZIO_OCCUPATO_S = 30.0

# Misurati su 20 chiamate reali con le facciate divise a 1568 px: 16.246
# token in ingresso e 12.890 in uscita per sei pagine. Servono a stimare
# il ritmo prima di partire — l'unico modo di rispettare un limite di
# token al minuto senza prima sforarlo.
TOKEN_INGRESSO_PER_PAGINA = 2_700
TOKEN_USCITA_PER_PAGINA = 2_150

# Mediana misurata: 88 s per una chiamata da 6 pagine, cioe' ~15 s a
# pagina. E' il numero che rende il parallelismo interessante: da solo un
# lavoratore fa 0,7 chiamate al minuto contro le 15 consentite.
SECONDI_PER_PAGINA = 15.0


# --- il ritmo del piano gratuito -------------------------------------------


class Ritmo:
    """Tiene le richieste dentro i limiti al minuto e al giorno.

    Il limite al minuto e' un'attesa: si dorme e si prosegue. Quello al
    giorno e' un muro, e va trattato come l'esaurimento della quota
    dell'abbonamento — il lavoro si ferma pulito e riprende domani, con
    tutte le pagine gia' fatte salvate.

    Il conteggio giornaliero sta su disco perche' **il processo muore e la
    quota no**: rilanciare il comando tre volte nello stesso pomeriggio
    non deve far ripartire il contatore da zero e prendersi una raffica di
    429.

    Il giorno e' quello di Los Angeles, perche' li' avviene il rinnovo, e
    lo si calcola a UTC-8 fisso invece di seguire l'ora legale. E' uno
    scarto di un'ora per meta' dell'anno, sempre nel verso prudente: si
    riparte un'ora dopo il rinnovo vero, mai un'ora prima.
    """

    FUSO_RINNOVO = timezone(timedelta(hours=-8))

    def __init__(
        self,
        al_minuto: int,
        al_giorno: int,
        registro: Path,
        token_al_minuto: int = 0,
        token_per_chiamata: int = 0,
    ):
        self.al_minuto = max(0, al_minuto)
        self.al_giorno = max(0, al_giorno)
        self.registro = registro
        self.token_al_minuto = max(0, token_al_minuto)
        self.token_per_chiamata = max(0, token_per_chiamata)
        self._ultima: float | None = None
        # Cio' che il ritmo impara strada facendo: quanto costa davvero
        # una chiamata, invece di quanto il codice credeva costasse.
        self._token_medi = 0.0
        self._chiamate_osservate = 0
        # Con piu' lavoratori in parallelo il conteggio giornaliero e' un
        # leggi-modifica-scrivi su un file condiviso, e senza serratura
        # due richieste simultanee ne segnerebbero una sola. La stessa
        # serratura fa da distributore dei turni: chi la prende aspetta il
        # suo momento e la rilascia, cosi' il ritmo dell'intera squadra
        # resta quello consentito a uno solo.
        self._serratura = threading.Lock()

    @property
    def intervallo_s(self) -> float:
        """Il tempo minimo fra due richieste, per tutti i lavoratori insieme.

        Sono due limiti, e vale il piu' stringente. Quello sulle richieste
        e' ovvio; quello sui **token** al minuto morde prima non appena si
        raggruppano molte pagine per chiamata, perche' ogni pagina sono due
        immagini da ~1.550 token. Stimarlo qui, invece di scoprirlo dai
        429, e' cio' che permette di aprire piu' richieste insieme senza
        prendersi una raffica di rifiuti.
        """
        intervalli = [0.0]
        # Un margine del 10%: i limiti sono valutati su una finestra
        # scorrevole e stare esattamente sul filo si prende dei 429 che
        # costano piu' dell'attesa risparmiata.
        if self.al_minuto:
            intervalli.append(60.0 / self.al_minuto * 1.1)
        token = self.token_osservati or self.token_per_chiamata
        if self.token_al_minuto and token:
            intervalli.append(60.0 * token / self.token_al_minuto * 1.1)
        return max(intervalli)

    def osserva(self, token: int) -> None:
        """Impara quanto costa davvero una chiamata, dalla risposta stessa.

        **Una costante scritta a mano invecchia col modello.** La stima
        iniziale — ~2.150 token in uscita per pagina — era misurata sul
        Flash con la trascrizione diplomatica; il Flash Lite ne produce
        372, sei volte meno. Il ritmo calcolato su quel numero teneva il
        lavoro a 3,9 chiamate al minuto dove ne consentiva 6,4: **meta'
        della velocita', per una stima sbagliata in eccesso.**

        Ogni risposta porta con se' i propri consumi, quindi non c'e'
        motivo di indovinare. La media si aggiorna man mano e il ritmo la
        segue, qualunque modello si stia usando e con o senza
        ``testo_integrale``.
        """
        if token <= 0:
            return
        with self._serratura:
            self._chiamate_osservate += 1
            # Media incrementale: non serve tenere la storia.
            self._token_medi += (token - self._token_medi) / self._chiamate_osservate

    @property
    def token_osservati(self) -> int:
        """La media misurata, quando ce n'e' abbastanza per fidarsene.

        Tre chiamate: poche per una statistica, abbastanza per accorgersi
        che l'ordine di grandezza della stima e' sbagliato — che e'
        l'unica cosa che conta qui.
        """
        return round(self._token_medi) if self._chiamate_osservate >= 3 else 0

    def _oggi(self) -> str:
        return datetime.now(self.FUSO_RINNOVO).strftime("%Y-%m-%d")

    def _conteggio(self) -> tuple[str, int]:
        oggi = self._oggi()
        try:
            dati = json.loads(self.registro.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return oggi, 0
        if dati.get("giorno") != oggi:
            return oggi, 0
        return oggi, int(dati.get("richieste", 0))

    def fatte_oggi(self) -> int:
        return self._conteggio()[1]

    def restano_oggi(self) -> int | None:
        """Quante richieste restano, o ``None`` se non c'e' un tetto."""
        if not self.al_giorno:
            return None
        return max(0, self.al_giorno - self.fatte_oggi())

    def attendi_turno(self) -> None:
        """Prende il turno: blocca finche' si puo' chiamare.

        Con piu' lavoratori la serratura si tiene per tutta l'attesa. E'
        deliberato: serializzare le PARTENZE e' esattamente il modo di
        distribuire un limite al minuto fra piu' richieste che poi
        proseguono in parallelo. Tenerla e' economico, perche' l'attesa
        vale secondi e la chiamata che segue ne vale decine.
        """
        with self._serratura:
            if self.restano_oggi() == 0:
                raise LimiteUsoRaggiunto(
                    f"esaurite le {self.al_giorno} richieste giornaliere del piano gratuito; "
                    f"si rinnovano a mezzanotte del fuso del Pacifico"
                )
            if self._ultima is not None:
                pausa = self.intervallo_s - (time.monotonic() - self._ultima)
                if pausa > 0:
                    time.sleep(pausa)
            # Il turno si marca all'uscita, non al ritorno della chiamata:
            # e' l'istante della partenza che il limite conta.
            self._ultima = time.monotonic()

    def segna(self, conta: bool = True) -> None:
        """Registra una richiesta effettuata.

        ``conta=False`` per una richiesta che e' partita ma e' stata
        respinta: una rifiutata per ritmo (429), per modello inesistente
        (404) o per schema sbagliato (400) non consuma quota giornaliera,
        e Google non la conta. Contarla farebbe fermare il lavoro con
        richieste ancora disponibili — ed e' successo: il contatore locale
        segnava 23 dove AI Studio ne segnava 17.
        """
        if not conta or not self.al_giorno:
            return
        with self._serratura:
            giorno, fatte = self._conteggio()
            self.registro.parent.mkdir(parents=True, exist_ok=True)
            self.registro.write_text(
                json.dumps({"giorno": giorno, "richieste": fatte + 1}), encoding="utf-8"
            )


# --- lo schema, nel dialetto di Gemini -------------------------------------

# ``responseSchema`` non e' JSON Schema: e' un sottoinsieme di OpenAPI 3.0.
# Le differenze che contano per lo schema di questo progetto sono tre, e
# tutte e tre farebbero fallire la richiesta con un 400 se passassero
# intatte:
#
#   - i tipi unione ``["string", "null"]`` non esistono; il nullo e' un
#     attributo a parte, ``nullable``;
#   - ``additionalProperties`` non e' previsto;
#   - un ``enum`` non puo' contenere ``None``, che va tolto e reso come
#     ``nullable``.
#
# In cambio ``propertyOrdering`` esiste e vale la pena metterlo: fissa
# l'ordine in cui il modello compila i campi, e compilare "cognome" dopo
# "nome" e' l'ordine in cui la frase sta sulla carta.

_TIPI = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def per_gemini(schema: dict) -> dict:
    """Traduce un JSON Schema del progetto nel dialetto di ``responseSchema``."""
    tipi = schema.get("type")
    tipi = [tipi] if isinstance(tipi, str) else list(tipi or [])
    nullo = "null" in tipi
    concreti = [t for t in tipi if t != "null"]

    convertito: dict = {}
    if concreti:
        convertito["type"] = _TIPI.get(concreti[0], concreti[0].upper())
    if nullo:
        convertito["nullable"] = True
    if descrizione := schema.get("description"):
        convertito["description"] = descrizione

    if valori := schema.get("enum"):
        ammessi = [v for v in valori if v is not None]
        if ammessi:
            convertito["enum"] = ammessi
        if len(ammessi) != len(valori):
            convertito["nullable"] = True

    if "items" in schema:
        convertito["items"] = per_gemini(schema["items"])

    if proprieta := schema.get("properties"):
        convertito["properties"] = {n: per_gemini(s) for n, s in proprieta.items()}
        # L'ordine delle chiavi nel sorgente e' l'ordine in cui i dati
        # stanno sull'atto; conservarlo non e' estetica.
        convertito["propertyOrdering"] = list(proprieta)
        if richiesti := schema.get("required"):
            convertito["required"] = list(richiesti)

    return convertito


# --- il backend ------------------------------------------------------------


class BackendGemini:
    """Trascrive chiamando l'API di Google AI Studio."""

    nome = "gemini"
    modo_immagini = "allegate"

    def __init__(self, config: Config, sessione: requests.Session | None = None):
        self.config = config
        # Una sessione per lavoratore. ``requests.Session`` non e' sicura
        # fra thread — il pool di connessioni non lo e' — e con piu'
        # chiamate in parallelo il guasto sarebbe intermittente e
        # difficile da riprodurre. Una sessione iniettata (i test) resta
        # quella per tutti: li' di thread ce n'e' uno solo.
        self._sessione_iniettata = sessione
        self._locale = threading.local()
        pagine = max(1, config.trascrizione.pagine_per_chiamata)
        self.ritmo = Ritmo(
            al_minuto=config.trascrizione.richieste_al_minuto,
            al_giorno=config.trascrizione.richieste_al_giorno,
            token_al_minuto=config.trascrizione.token_al_minuto,
            token_per_chiamata=pagine
            * (TOKEN_INGRESSO_PER_PAGINA + TOKEN_USCITA_PER_PAGINA),
            # Un contatore PER MODELLO, perche' per modello sono i limiti:
            # gemini-3.6-flash da' 20 richieste al giorno, un flash-lite ne
            # da' 500, e sono due secchielli separati. Con un contatore
            # solo, provare un modello diverso per mezz'ora falserebbe la
            # contabilita' di quello con cui stai facendo il lavoro vero.
            registro=config.trascrizioni.parent
            / f".quota-gemini-{config.trascrizione.modello}.json",
        )

    @property
    def sessione(self) -> requests.Session:
        """La sessione HTTP di questo lavoratore."""
        if self._sessione_iniettata is not None:
            return self._sessione_iniettata
        propria = getattr(self._locale, "sessione", None)
        if propria is None:
            propria = requests.Session()
            self._locale.sessione = propria
        return propria

    # -- interfaccia Backend --

    def verifica(self) -> None:
        if not self._chiave():
            raise BackendNonDisponibile(
                "Nessuna chiave per Gemini nell'ambiente.\n"
                "Prendine una (gratuita) su https://aistudio.google.com/apikey\n"
                "poi impostala prima di lanciare il comando:\n"
                "  PowerShell:  $env:GEMINI_API_KEY = 'la-tua-chiave'\n"
                "  bash:        export GEMINI_API_KEY='la-tua-chiave'\n"
                f"Sono accettate anche in {' o '.join(VARIABILI_CHIAVE)}."
            )

    def riferimento_immagine(self, percorso: Path, indice: int) -> str:
        """Le immagini arrivano inline: conta la posizione, non il percorso.

        Il nome resta pero' nell'etichetta, perche' la risposta lo ripete
        nel campo ``file`` ed e' cosi' che ogni oggetto torna alla sua
        pagina anche se il modello cambia l'ordine.

        **Il nome comprende il registro, e non e' un dettaglio.** Le pagine
        si chiamano ``0001-pag-1.jpg`` dentro OGNI registro: sono uniche
        solo li' dentro. Una chiamata da dodici pagine attraversa dodici
        registri ``Diversi`` da una-due pagine ciascuno, e allora lo stesso
        nome compare cinque volte — il primo oggetto se lo prende, gli
        altri quattro non trovano piu' nulla e le loro pagine risultano
        fallite. Misurato: 37 pagine perse su 60.
        """
        return f"immagine {indice + 1} ({percorso.parent.name}/{percorso.name})"

    def esegui(self, richiesta: Richiesta) -> Risposta:
        self.verifica()
        self.ritmo.attendi_turno()

        corpo = self._corpo(richiesta)
        self._verifica_dimensione(corpo, len(richiesta.immagini))
        url = ENDPOINT.format(modello=richiesta.modello or self.config.trascrizione.modello)

        try:
            # La chiave va nell'intestazione, non nella query: un URL
            # finisce nei log di chiunque stia in mezzo.
            risposta = self.sessione.post(
                url,
                json=corpo,
                headers={"x-goog-api-key": self._chiave(), "Content-Type": "application/json"},
                timeout=richiesta.timeout_s,
            )
        except requests.Timeout:
            return Risposta(ok=False, errore=f"nessuna risposta entro {richiesta.timeout_s}s")
        except requests.RequestException as exc:
            return Risposta(ok=False, errore=f"errore di rete: {exc}")

        # Conta solo cio' che Google conta: una richiesta respinta — per
        # ritmo (429), per modello inesistente (404), per schema sbagliato
        # (400) — non consuma quota giornaliera. Contarla farebbe fermare
        # il lavoro con richieste ancora disponibili, ed e' successo: il
        # contatore locale segnava 23 dove AI Studio ne segnava 17.
        # Si conta OGNI richiesta che arriva all'API, riuscita o no: e'
        # cosi' che la conta Google. Per un po' qui si contavano solo le
        # 2xx, sulla base di un confronto che sembrava provarlo — il
        # contatore locale diceva 23 dove AI Studio diceva 17. Il
        # confronto era falsato: quelle sette fallite erano contro un
        # ALTRO modello, quindi un altro secchiello, e non provavano
        # niente. Il risultato e' stato un contatore che sottostimava, e
        # un lavoro andato a sbattere contro un muro che credeva lontano.
        self.ritmo.segna()
        esito = self._interpreta(risposta)
        self.ritmo.osserva(esito.token_contesto + esito.token_output)
        return esito

    # -- dettagli --

    @staticmethod
    def _chiave() -> str:
        for nome in VARIABILI_CHIAVE:
            if valore := os.environ.get(nome, "").strip():
                return valore
        return ""

    def _corpo(self, richiesta: Richiesta) -> dict:
        """La richiesta HTTP: istruzione, immagini etichettate, schema.

        L'ordine delle parti non e' indifferente. L'istruzione viene
        **prima** delle immagini, perche' il modello sappia cosa cercare
        mentre le guarda invece di ripercorrerle dopo; ogni immagine e'
        preceduta dalla sua etichetta, cosi' che il nome del file resti
        attaccato alla pagina giusta anche quando ne arrivano dieci.
        """
        parti: list[dict] = [{"text": richiesta.istruzione}]
        for indice, percorso in enumerate(richiesta.immagini):
            parti.append({"text": f"\n--- {self.riferimento_immagine(percorso, indice)} ---"})
            parti.append(
                {
                    "inlineData": {
                        "mimeType": MIME.get(percorso.suffix.lower(), "image/jpeg"),
                        "data": base64.b64encode(percorso.read_bytes()).decode("ascii"),
                    }
                }
            )

        generazione: dict = {
            # Una trascrizione non e' un compito creativo: la stessa
            # pagina deve dare la stessa lettura.
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "maxOutputTokens": self.config.trascrizione.token_massimi,
        }
        if richiesta.schema:
            generazione["responseSchema"] = per_gemini(richiesta.schema)
        # I token di ragionamento si scalano da maxOutputTokens: lasciarlo
        # deciso dal modello va bene finche' c'e' margine, ma poterlo
        # limitare e' cio' che evita di scoprire il tetto a JSON troncato.
        if (budget := self.config.trascrizione.token_ragionamento) >= 0:
            generazione["thinkingConfig"] = {"thinkingBudget": budget}

        return {
            "systemInstruction": {"parts": [{"text": richiesta.sistema}]},
            "contents": [{"role": "user", "parts": parti}],
            "generationConfig": generazione,
        }

    def modelli_disponibili(self) -> list[str]:
        """I modelli che questa chiave puo' usare per generare contenuto.

        Esiste per una ragione precisa, ed e' successa: un modello puo'
        smettere di essere servito alle chiavi nuove da un giorno
        all'altro, e la riga in configurazione che ieri funzionava oggi
        restituisce un 404 che non dice quale sia l'alternativa. Questa
        chiamata non consuma quota di generazione.
        """
        self.verifica()
        risposta = self.sessione.get(
            ENDPOINT_MODELLI,
            headers={"x-goog-api-key": self._chiave()},
            timeout=60,
        )
        risposta.raise_for_status()
        return sorted(
            m["name"].removeprefix("models/")
            for m in (risposta.json().get("models") or [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        )

    def _verifica_dimensione(self, corpo: dict, quante_immagini: int) -> None:
        """Ferma una richiesta troppo grande prima di spedirla.

        Le immagini viaggiano dentro la richiesta, in base64, che le
        gonfia di un terzo: con le facciate divise sono due immagini da
        ~500 KB per pagina, e il tetto di 20 MB si raggiunge prima di
        quanto sembri. Scoprirlo da un errore di rete a meta' di un lavoro
        di giorni sarebbe una diagnosi difficile per una causa banale.
        """
        byte = len(json.dumps(corpo).encode("utf-8"))
        if byte <= LIMITE_RICHIESTA_BYTE:
            return
        raise BackendNonDisponibile(
            f"La richiesta pesa {byte / 1e6:.1f} MB con {quante_immagini} immagini, "
            f"oltre il tetto di {LIMITE_RICHIESTA_BYTE / 1e6:.0f} MB per le immagini "
            f"allegate.\n"
            f"In config/torrebruna.yaml, una di queste tre:\n"
            f"  - abbassa 'pagine_per_chiamata' (ora {self.config.trascrizione.pagine_per_chiamata}) — "
            f"e' la leva giusta, costa solo qualche richiesta in piu';\n"
            f"  - abbassa 'qualita_jpeg' (ora {self.config.trascrizione.qualita_jpeg}), "
            f"non sotto 80;\n"
            f"  - abbassa 'lato_lungo_px' (ora {self.config.trascrizione.lato_lungo_px}), "
            f"ma e' quella che costa in accuratezza."
        )

    def _interpreta(self, http: requests.Response) -> Risposta:
        try:
            dati = http.json()
        except ValueError:
            dati = {}

        if http.status_code != 200:
            errore = dati.get("error") or {}
            messaggio = errore.get("message") or http.text[:300] or f"HTTP {http.status_code}"
            if http.status_code == 429 or errore.get("status") in STATI_DI_QUOTA:
                # Il muro giornaliero e quello al minuto arrivano ENTRAMBI
                # come 429 con un retryDelay breve — 10, 39, 54 secondi —
                # quindi il ritardo suggerito non li distingue. A dirlo e'
                # la quota violata, che l'API nomina nei dettagli.
                raise LimiteUsoRaggiunto(
                    messaggio,
                    attesa_s=_attesa_suggerita(errore),
                    giornaliera=_e_quota_giornaliera(errore),
                )
            if http.status_code in (401, 403):
                raise BackendNonDisponibile(
                    f"Chiave rifiutata da Gemini: {messaggio}\n"
                    f"Controlla la variabile {VARIABILI_CHIAVE[0]}."
                )
            if 400 <= http.status_code < 500:
                # Ogni 4xx che non sia un 429 dice che la richiesta e'
                # sbagliata, non che la pagina sia illeggibile: uno schema
                # che l'API non accetta (400), un modello che per questa
                # chiave non esiste (404). Fallira' identica a ogni
                # tentativo, quindi ritentarla una pagina per volta
                # brucerebbe sei richieste per riscoprire sei volte lo
                # stesso errore — cosa che e' successa davvero il giorno in
                # cui 'gemini-2.5-flash' ha smesso di essere servito alle
                # chiavi nuove.
                raise BackendNonDisponibile(
                    f"Gemini ha rifiutato la richiesta (HTTP {http.status_code}): {messaggio}\n"
                    f"Non e' un problema della pagina — la stessa richiesta fallirebbe "
                    f"uguale — quindi mi fermo invece di consumare quota a ritentarla.\n"
                    f"Il sospetto piu' probabile e' 'modello' in config/torrebruna.yaml "
                    f"(ora: {self.config.trascrizione.modello}). Per vedere quali modelli "
                    f"accetta la tua chiave:\n"
                    f"  python -m history_maker modelli"
                )
            # Un servizio occupato non e' una pagina illeggibile.
            #
            # Il 503 — "This model is currently experiencing high demand" —
            # dice che il modello e' sovraccarico, non che ci sia qualcosa
            # di sbagliato nella richiesta. Trattarlo come un fallimento
            # del gruppo faceva ripiegare a UNA PAGINA PER CHIAMATA: dodici
            # richieste al posto di una, per un guasto che non riguarda
            # nessuna pagina in particolare. E' successo oggi, quattro
            # volte in tre minuti, e ha bruciato piu' di cento chiamate.
            #
            # La risposta giusta e' aspettare e ritentare lo STESSO gruppo,
            # che e' esattamente cio' che il chiamante fa con un limite di
            # ritmo. Il tetto ai ritentativi impedisce il ciclo infinito.
            if http.status_code >= 500:
                raise LimiteUsoRaggiunto(
                    f"servizio occupato: {messaggio}",
                    attesa_s=ATTESA_SERVIZIO_OCCUPATO_S,
                )
            return Risposta(ok=False, errore=messaggio)

        consumi = dati.get("usageMetadata") or {}
        candidati = dati.get("candidates") or []
        if not candidati:
            # Nessun candidato significa quasi sempre che il prompt e'
            # stato bloccato in ingresso: il motivo sta altrove nel corpo.
            motivo = (dati.get("promptFeedback") or {}).get("blockReason", "nessun candidato")
            return Risposta(ok=False, errore=f"risposta vuota ({motivo})")

        candidato = candidati[0]
        testo = "".join(
            parte.get("text", "")
            for parte in ((candidato.get("content") or {}).get("parts") or [])
        )
        motivo = candidato.get("finishReason")

        risposta = Risposta(
            ok=bool(testo.strip()),
            testo=testo,
            token_contesto=int(consumi.get("promptTokenCount", 0)),
            token_output=int(consumi.get("candidatesTokenCount", 0)),
        )
        if motivo == "MAX_TOKENS":
            # Un JSON troncato non si ripara: meglio dirlo e lasciare che
            # il gruppo venga ritentato una pagina per volta, dove ci sta.
            risposta.ok = False
            risposta.errore = (
                f"risposta troncata a {self.config.trascrizione.token_massimi} token "
                f"(alza trascrizione.token_massimi o abbassa pagine_per_chiamata)"
            )
        elif not risposta.ok:
            risposta.errore = f"nessun testo nella risposta (finishReason={motivo})"
        return risposta


# Come l'API nomina le quote giornaliere nei dettagli dell'errore. Il
# 429 al minuto e quello al giorno sono indistinguibili dal retryDelay —
# li abbiamo visti entrambi a 10-54 secondi — ma la violazione porta il
# nome della quota, e quella lo dice.
SEGNALI_QUOTA_GIORNALIERA = ("perday", "per_day", "per-day", "requestsperday", "free_tier_requests")


def _e_quota_giornaliera(errore: dict) -> bool:
    """Se questo 429 e' il muro del giorno, non un limite di ritmo.

    Confonderli e' costato una corsa intera: il codice ha ritentato per
    otto minuti contro una quota esaurita, credendo di aspettare qualche
    secondo di congestione.
    """
    testo = ""
    for dettaglio in errore.get("details") or []:
        for violazione in dettaglio.get("violations") or []:
            testo += " ".join(str(v) for v in violazione.values()).lower()
        testo += str(dettaglio.get("quotaId") or "").lower()
    testo += str(errore.get("message") or "").lower()
    compatto = testo.replace(" ", "")
    return any(s.replace(" ", "") in compatto for s in SEGNALI_QUOTA_GIORNALIERA)


def _attesa_suggerita(errore: dict) -> float | None:
    """Il ``retryDelay`` che l'API allega ai 429, in secondi."""
    for dettaglio in errore.get("details") or []:
        ritardo = dettaglio.get("retryDelay")
        if isinstance(ritardo, str) and ritardo.endswith("s"):
            try:
                return float(ritardo[:-1])
            except ValueError:
                continue
    return None
