"""Chi trascrive davvero: il motore, dietro un'unica interfaccia.

La fase 3 e' nata sopra Claude Code, e per un po' Claude Code *era* la
fase 3. Sono due cose diverse, e tenerle unite costava caro: il
sovraccarico della CLI — circa 50.000 token di prompt di sistema e
definizioni di strumenti a **ogni invocazione**, contro i ~2.100 di
un'immagine — significa che a dieci pagine per chiamata i sette decimi
della quota se ne vanno in impalcatura, prima ancora di guardare una
pagina.

Questo modulo separa *cosa* si chiede a un modello da *chi* glielo
chiede. Un backend riceve una :class:`Richiesta` — il prompt di sistema,
l'istruzione, le immagini nell'ordine in cui l'istruzione le nomina — e
restituisce una :class:`Risposta`. Il resto della pipeline non sa quale
motore stia girando.

I motori differiscono su tre punti, e sono esattamente i tre che
l'interfaccia lascia decidere a loro:

**Come arrivano le immagini.** Claude Code le legge dal disco con lo
strumento ``Read``, quindi il prompt deve nominarle per percorso e la
cartella va autorizzata. Gemini le riceve inline nella richiesta, quindi
il percorso non gli dice niente e va etichettata la posizione. Da qui
:meth:`Backend.riferimento_immagine`.

**Se lo schema si puo' imporre.** L'API di Gemini accetta un
``responseSchema`` e la risposta e' conforme per costruzione; la CLI di
Claude Code non offre l'equivalente e la forma va chiesta a parole, poi
verificata. Il validatore in :mod:`history_maker.schema` resta comunque
in mezzo: e' cintura oltre alle bretelle, e non costa nulla.

**Come finisce la quota.** L'abbonamento la esaurisce a finestre di ore;
il piano gratuito di Gemini la conta al minuto e al giorno. In entrambi i
casi il chiamante vuole distinguere "riprova piu' tardi" da "questa
pagina non si legge", e per questo c'e' un'unica eccezione condivisa.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from history_maker.config import Config


class LimiteUsoRaggiunto(RuntimeError):
    """La quota e' esaurita: non e' un errore di lettura, si riprende dopo.

    ``attesa_s`` e' il tempo che il servizio stesso suggerisce di
    aspettare, quando lo dice. Gemini lo mette nel corpo dell'errore
    (``RetryInfo.retryDelay``); Claude Code no, e resta ``None``.
    """

    def __init__(
        self, messaggio: str, attesa_s: float | None = None, giornaliera: bool = False
    ):
        super().__init__(messaggio)
        self.attesa_s = attesa_s
        # Se e' il muro del giorno o solo un limite di ritmo. Non si
        # deduce dal ritardo suggerito: l'API ne allega uno breve a
        # entrambi, e confonderli manda il lavoro in un ciclo di
        # ritentativi contro una quota che non si rinnovera' per ore.
        self.giornaliera = giornaliera


class BackendNonDisponibile(RuntimeError):
    """Il motore non e' utilizzabile, con le istruzioni per rimediare."""


@dataclass
class Richiesta:
    """Una chiamata al modello: cosa deve fare, e su quali immagini."""

    sistema: str
    istruzione: str
    # Nell'ordine in cui l'istruzione le elenca. L'allineamento della
    # risposta alle pagine si regge su quest'ordine quando il modello non
    # ripete il nome del file.
    immagini: list[Path] = field(default_factory=list)
    # JSON Schema della risposta attesa. I backend che sanno imporlo lo
    # usano; gli altri lo ignorano, e la conformita' resta affidata al
    # validatore.
    schema: dict | None = None
    modello: str = ""
    timeout_s: int = 900


@dataclass
class Risposta:
    ok: bool
    testo: str = ""
    token_contesto: int = 0
    token_output: int = 0
    errore: str = ""


@runtime_checkable
class Backend(Protocol):
    """Un motore di trascrizione."""

    nome: str
    # "disco" se il modello apre le immagini da solo, "allegate" se le
    # riceve dentro la richiesta. Cambia una frase del prompt: chiedere a
    # Gemini di usare lo strumento Read e' una chiamata buttata.
    modo_immagini: str

    def verifica(self) -> None:
        """Solleva :class:`BackendNonDisponibile` se non si puo' usare.

        Il messaggio deve dire come rimediare: e' la prima cosa che legge
        chi installa il progetto su una macchina nuova.
        """

    def riferimento_immagine(self, percorso: Path, indice: int) -> str:
        """Come l'istruzione deve nominare questa immagine.

        Il percorso per chi legge dal disco, l'etichetta di posizione per
        chi le riceve inline.
        """

    def esegui(self, richiesta: Richiesta) -> Risposta:
        """Effettua la chiamata. Solleva :class:`LimiteUsoRaggiunto` a quota finita."""


# I nomi accettati in ``trascrizione.backend``, con il modulo che li
# realizza. Aggiungerne uno e' aggiungere una riga qui e un modulo.
BACKEND_DISPONIBILI = ("gemini", "claude-code")


def crea(config: Config) -> Backend:
    """Il backend indicato dalla configurazione."""
    nome = (config.trascrizione.backend or "").strip().lower()

    if nome == "gemini":
        from history_maker.gemini import BackendGemini

        return BackendGemini(config)

    if nome in ("claude-code", "claude_code", "claudecode", "claude"):
        from history_maker.claudecode import BackendClaudeCode

        return BackendClaudeCode(config)

    raise BackendNonDisponibile(
        f"Backend di trascrizione sconosciuto: {config.trascrizione.backend!r}.\n"
        f"Valori ammessi in config -> trascrizione.backend: "
        f"{', '.join(BACKEND_DISPONIBILI)}."
    )


# --- interpretare la risposta ----------------------------------------------
#
# Serve a tutti i motori, non solo a quelli senza vincolo di schema: anche
# dove la risposta e' JSON per costruzione, un modello che ci ripensa e
# incornicia tutto in un blocco markdown costa una chiamata sprecata, e
# ripulirla e' gratis.

# Il modello incornicia quasi sempre il JSON in un blocco markdown, anche
# quando gli si chiede di non farlo.
_BLOCCO = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def estrai_json(testo: str):
    """Ricava la struttura JSON dalla risposta testuale.

    Tollera il blocco markdown e l'eventuale frase di accompagnamento,
    cercando il primo array o oggetto bilanciato.
    """
    if not testo or not testo.strip():
        raise ValueError("risposta vuota")

    blocco = _BLOCCO.search(testo)
    candidato = blocco.group(1) if blocco else testo.strip()

    try:
        return json.loads(candidato)
    except json.JSONDecodeError:
        pass

    ritagliato = _primo_valore_bilanciato(candidato)
    if ritagliato is None:
        raise ValueError(f"nessun JSON riconoscibile in: {testo[:200]}")
    return json.loads(ritagliato)


def _primo_valore_bilanciato(testo: str) -> str | None:
    """Primo array o oggetto JSON completo contenuto nel testo."""
    aperture = {"[": "]", "{": "}"}
    for inizio, carattere in enumerate(testo):
        if carattere not in aperture:
            continue
        chiusura = aperture[carattere]
        profondita = 0
        in_stringa = False
        preceduto_da_backslash = False
        for fine in range(inizio, len(testo)):
            corrente = testo[fine]
            if in_stringa:
                if preceduto_da_backslash:
                    preceduto_da_backslash = False
                elif corrente == "\\":
                    preceduto_da_backslash = True
                elif corrente == '"':
                    in_stringa = False
                continue
            if corrente == '"':
                in_stringa = True
            elif corrente == carattere:
                profondita += 1
            elif corrente == chiusura:
                profondita -= 1
                if profondita == 0:
                    return testo[inizio : fine + 1]
    return None


