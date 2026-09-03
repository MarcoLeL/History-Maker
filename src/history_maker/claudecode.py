"""Esecuzione di Claude Code in modalita' non interattiva.

Questa e' l'alternativa all'API a consumo: ``claude -p`` usa
l'abbonamento Claude Pro invece del credito prepagato. In cambio ci sono
due differenze che condizionano tutto il modulo.

**Il sovraccarico e' per invocazione.** Ogni chiamata porta con se' il
prompt di sistema e le definizioni degli strumenti di Claude Code: circa
50.000 token, contro i ~2.500 di una singola immagine. Trascrivere una
pagina per chiamata sprecherebbe il 95% della quota in impalcatura, per
cui le pagine vengono raggruppate: a quattro per chiamata il costo
scende intorno ai 20.000 token a pagina.

**La risposta non e' vincolata a uno schema.** L'API offre
``output_config.format``, la CLI no: il testo torna spesso dentro un
blocco markdown e va ripulito e validato.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from history_maker.backend import (  # noqa: F401  (fanno parte dell'API storica di questo modulo)
    BackendNonDisponibile,
    LimiteUsoRaggiunto,
    Richiesta,
    Risposta,
    estrai_json,
)

logger = logging.getLogger(__name__)

# Frasi con cui la CLI segnala che la quota dell'abbonamento e' esaurita.
# Sono euristiche: la formulazione puo' cambiare, quindi il codice non ci
# fa affidamento per la correttezza, solo per distinguere "riprova piu'
# tardi" da "questa pagina non si riesce a leggere".
SEGNALI_LIMITE = (
    "usage limit",
    "rate limit",
    "session limit",
    "limite di utilizzo",
    "limit reached",
    "try again later",
    "riprova piu tardi",
)

# L'ora del rinnovo compare in forme diverse — "resets at 3pm" ma anche
# "resets 5:10pm (Europe/Rome)" — e cercare la sola parola "resets"
# rischierebbe di scambiare per quota esaurita una trascrizione che la
# contiene. Pretendere una cifra subito dopo tiene insieme le due cose.
ORARIO_DI_RIPRESA = re.compile(r"resets\s+(?:at\s+)?\d", re.IGNORECASE)


class ClaudeCodeNonTrovato(BackendNonDisponibile):
    """L'eseguibile ``claude`` non e' nel PATH."""


# Se una di queste e' impostata, Claude Code la preferisce all'abbonamento
# e le chiamate vengono fatturate a consumo sull'API. Poiche' questa
# pipeline e' pensata per girare sull'abbonamento, vengono tolte
# dall'ambiente del processo figlio: e' l'unico modo in cui il lavoro
# potrebbe costare denaro, e va escluso per costruzione, non per fiducia.
CREDENZIALI_A_PAGAMENTO = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def ambiente_solo_abbonamento() -> dict[str, str]:
    """Copia dell'ambiente senza le credenziali a consumo."""
    ambiente = dict(os.environ)
    rimosse = [nome for nome in CREDENZIALI_A_PAGAMENTO if ambiente.pop(nome, None)]
    if rimosse:
        logger.warning(
            "%s risulta impostata: la tolgo dall'ambiente di 'claude' perche' "
            "farebbe fatturare le trascrizioni sull'API a consumo invece di "
            "usare l'abbonamento.",
            " e ".join(rimosse),
        )
    return ambiente


@dataclass
class Esito:
    ok: bool
    testo: str = ""
    token_contesto: int = 0
    token_output: int = 0
    costo_listino: float = 0.0
    errore: str = ""
    grezzo: dict = field(default_factory=dict)


def verifica_installazione() -> str:
    """Percorso dell'eseguibile ``claude``, o errore con istruzioni."""
    percorso = shutil.which("claude")
    if not percorso:
        raise ClaudeCodeNonTrovato(
            "L'eseguibile 'claude' non e' nel PATH.\n"
            "Installalo con:  npm install -g @anthropic-ai/claude-code\n"
            "poi autenticati con l'abbonamento lanciando 'claude' una volta."
        )
    return percorso


def costruisci_comando(
    sistema_file: Path,
    cartelle: list[Path],
    modello: str,
) -> list[str]:
    """Argomenti della chiamata a ``claude``.

    Le opzioni non sono decorative:

    ``--print``          modalita' non interattiva, stampa e termina.
    ``--output-format``  ``json``, per leggere anche l'esito e i consumi.
    ``--system-prompt-file``  sostituisce il prompt di sistema di Claude
                         Code con quello paleografico, che e' piu' corto e
                         piu' pertinente.
    ``--allowedTools``   solo ``Read``: al lavoro serve leggere immagini,
                         nient'altro.
    ``--restricted``     toglie gli strumenti che eseguono comandi.
    ``--permission-mode dontAsk``  senza, la CLI si fermerebbe ad
                         aspettare un consenso che in un ciclo di
                         migliaia di pagine nessuno darebbe.
    ``--add-dir``        autorizza la lettura della cartella immagini.

    **I due testi lunghi non passano dalla riga di comando.** Il prompt
    arriva sullo standard input e il prompt di sistema da un file. Non e'
    un'eleganza: su Windows ``claude`` e' un file ``.cmd``, quindi la
    chiamata passa da ``cmd.exe``, che interpreta a modo suo i caratteri
    ``> | & ^ %`` e si ferma al primo ritorno a capo. L'elenco delle
    pagine e' multiriga e il contesto archivistico contiene ``>``
    (``Archivio di Stato di Chieti > Stato civile napoleonico >
    Torrebruna``): passato come argomento arrivava troncato alla prima
    riga, e il modello rispondeva — a ragione — di non sapere quali
    pagine leggere.
    """
    comando = [
        verifica_installazione(),
        "--print",
        "--system-prompt-file",
        str(sistema_file),
        "--output-format",
        "json",
        "--allowedTools",
        "Read",
        "--restricted",
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--model",
        modello,
    ]
    for cartella in cartelle:
        comando += ["--add-dir", str(cartella)]
    return comando


def esegui(
    prompt: str,
    sistema: str,
    cartelle: list[Path],
    modello: str,
    timeout: int = 900,
) -> Esito:
    """Lancia Claude Code e restituisce l'esito.

    Solleva :class:`LimiteUsoRaggiunto` quando la quota e' finita, cosi'
    che il chiamante possa aspettare invece di scambiare l'esaurimento
    della quota per un errore di lettura.
    """
    with tempfile.TemporaryDirectory(prefix="history-maker-") as temporanea:
        sistema_file = Path(temporanea) / "sistema.txt"
        sistema_file.write_text(sistema, encoding="utf-8")
        return _esegui_con(
            costruisci_comando(sistema_file, cartelle, modello), prompt, timeout
        )


def _esegui_con(comando: list[str], prompt: str, timeout: int) -> Esito:
    try:
        completato = subprocess.run(
            comando,
            input=prompt,
            capture_output=True,
            text=True,
            # Claude Code scrive in UTF-8, ma senza dirlo Python decodifica
            # con la codifica preferita del sistema: su Windows e' una
            # codepage a un byte, che storpia ogni carattere accentato. Su
            # atti di stato civile italiani sarebbe corruzione silenziosa
            # del risultato, non un dettaglio di visualizzazione.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=ambiente_solo_abbonamento(),
        )
    except subprocess.TimeoutExpired:
        return Esito(ok=False, errore=f"nessuna risposta entro {timeout}s")

    uscita = (completato.stdout or "").strip()
    errori = (completato.stderr or "").strip()

    if _sembra_limite(uscita + " " + errori):
        raise LimiteUsoRaggiunto(errori or uscita or "quota esaurita")

    if not uscita:
        return Esito(ok=False, errore=errori or f"uscita vuota (codice {completato.returncode})")

    try:
        risposta = json.loads(uscita)
    except json.JSONDecodeError:
        return Esito(ok=False, errore=f"uscita non in JSON: {uscita[:200]}")

    consumi = risposta.get("usage") or {}
    esito = Esito(
        ok=not risposta.get("is_error") and risposta.get("subtype") == "success",
        testo=risposta.get("result") or "",
        token_contesto=(
            consumi.get("input_tokens", 0)
            + consumi.get("cache_creation_input_tokens", 0)
            + consumi.get("cache_read_input_tokens", 0)
        ),
        token_output=consumi.get("output_tokens", 0),
        costo_listino=risposta.get("total_cost_usd") or 0.0,
        grezzo=risposta,
    )
    if not esito.ok:
        # 'subtype' non e' un messaggio d'errore: e' il tipo di
        # conclusione della sessione, e su un turno fallito puo' restare
        # 'success' — il caso vero e' un modello inesistente ('claude
        # --model gemini-3.5-flash-lite' risponde cosi', con
        # is_error=true e subtype='success' insieme). Il messaggio utile
        # sta in 'result': e' li' che l'eseguibile spiega cosa e'
        # andato storto. 'subtype' resta l'ultima spiaggia, per non
        # restituire mai una stringa vuota.
        esito.errore = (
            risposta.get("result") or risposta.get("subtype") or "esito non riuscito"
        )
        if _sembra_limite(esito.testo) or _sembra_limite(esito.errore):
            raise LimiteUsoRaggiunto(esito.errore)
    return esito


def _sembra_limite(testo: str) -> bool:
    minuscolo = testo.lower()
    if any(segnale in minuscolo for segnale in SEGNALI_LIMITE):
        return True
    return bool(ORARIO_DI_RIPRESA.search(testo))


# --- il backend ------------------------------------------------------------


class BackendClaudeCode:
    """Trascrive invocando ``claude -p`` con l'abbonamento.

    Resta la strada senza chiave e senza spesa, ed e' quella con cui il
    progetto e' nato. Il suo limite non e' la qualita' ma il ritmo: le
    ~50.000 token di impalcatura per invocazione, sommate alla quota a
    finestre dell'abbonamento, tengono la trascrizione di un secolo su una
    scala di mesi.
    """

    nome = "claude-code"
    modo_immagini = "disco"

    def __init__(self, config):
        self.config = config

    def verifica(self) -> None:
        verifica_installazione()

    def riferimento_immagine(self, percorso: Path, indice: int) -> str:
        """Il percorso su disco: e' con ``Read`` che la CLI apre le immagini."""
        return str(percorso)

    def esegui(self, richiesta: Richiesta) -> Risposta:
        cartelle = sorted({p.parent for p in richiesta.immagini} | {self.config.ridotte})
        esito = esegui(
            prompt=richiesta.istruzione,
            sistema=richiesta.sistema,
            cartelle=cartelle,
            modello=richiesta.modello or self.config.trascrizione.modello,
            timeout=richiesta.timeout_s,
        )
        return Risposta(
            ok=esito.ok,
            testo=esito.testo,
            token_contesto=esito.token_contesto,
            token_output=esito.token_output,
            errore=esito.errore,
        )
