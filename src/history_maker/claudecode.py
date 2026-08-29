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
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Frasi con cui la CLI segnala che la quota dell'abbonamento e' esaurita.
# Sono euristiche: la formulazione puo' cambiare, quindi il codice non ci
# fa affidamento per la correttezza, solo per distinguere "riprova piu'
# tardi" da "questa pagina non si riesce a leggere".
SEGNALI_LIMITE = (
    "usage limit",
    "rate limit",
    "limite di utilizzo",
    "limit reached",
    "resets at",
    "try again later",
    "riprova piu tardi",
)


class LimiteUsoRaggiunto(RuntimeError):
    """La quota dell'abbonamento e' esaurita: si riprende piu' tardi."""


class ClaudeCodeNonTrovato(RuntimeError):
    """L'eseguibile ``claude`` non e' nel PATH."""


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
    prompt: str,
    sistema: str,
    cartelle: list[Path],
    modello: str,
) -> list[str]:
    """Argomenti della chiamata a ``claude``.

    Le opzioni non sono decorative:

    ``--print``          modalita' non interattiva, stampa e termina.
    ``--output-format``  ``json``, per leggere anche l'esito e i consumi.
    ``--system-prompt``  sostituisce il prompt di sistema di Claude Code
                         con quello paleografico, che e' piu' corto e
                         piu' pertinente.
    ``--allowedTools``   solo ``Read``: al lavoro serve leggere immagini,
                         nient'altro.
    ``--restricted``     toglie gli strumenti che eseguono comandi.
    ``--permission-mode dontAsk``  senza, la CLI si fermerebbe ad
                         aspettare un consenso che in un ciclo di
                         migliaia di pagine nessuno darebbe.
    ``--add-dir``        autorizza la lettura della cartella immagini.
    """
    comando = [
        verifica_installazione(),
        "--print",
        prompt,
        "--system-prompt",
        sistema,
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
    comando = costruisci_comando(prompt, sistema, cartelle, modello)
    try:
        completato = subprocess.run(
            comando, capture_output=True, text=True, timeout=timeout, check=False
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
        esito.errore = risposta.get("subtype") or "esito non riuscito"
        if _sembra_limite(esito.testo):
            raise LimiteUsoRaggiunto(esito.testo)
    return esito


def _sembra_limite(testo: str) -> bool:
    minuscolo = testo.lower()
    return any(segnale in minuscolo for segnale in SEGNALI_LIMITE)


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
