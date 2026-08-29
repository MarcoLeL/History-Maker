"""Il finto ``claude`` usato dai test, scritto una volta sola.

Sostituisce l'eseguibile vero nel PATH per collaudare la fase 3 senza
consumare quota. La logica sta qui, in Python; cambia solo il lanciatore
che il sistema operativo sa eseguire — uno script ``sh`` su Unix, un
``.cmd`` su Windows, perche' li' ``shutil.which`` cerca solo i nomi con
un'estensione elencata in PATHEXT e un file chiamato ``claude`` senza
estensione non verrebbe mai trovato.

A ogni invocazione: registra la chiamata, annota se ha ricevuto una
chiave API (le trascrizioni non devono mai essere fatturate a consumo) e
stampa la risposta prevista dal copione.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

REGISTRO = "chiamate.log"
COPIONE = "copione.json"
SPIA_CHIAVE = "chiave_vista.txt"


def risposta(testo: str, is_error: bool = False, subtype: str = "success") -> dict:
    """Una risposta nella forma di ``claude --output-format json``."""
    return {
        "is_error": is_error,
        "subtype": subtype,
        "result": testo,
        "total_cost_usd": 0.04,
        "usage": {
            "input_tokens": 4,
            "cache_creation_input_tokens": 7000,
            "cache_read_input_tokens": 43000,
            "output_tokens": 150,
        },
    }


def _esegui(cartella: Path) -> None:
    """Corpo del finto eseguibile, invocato dal lanciatore."""
    registro = cartella / REGISTRO
    with registro.open("a", encoding="utf-8") as handle:
        handle.write("chiamata\n")
    # Una riga per invocazione: il prompt contiene ritorni a capo e
    # registrarlo per intero falserebbe il conteggio.
    quante = sum(1 for _ in registro.read_text(encoding="utf-8").splitlines())

    (cartella / SPIA_CHIAVE).write_text(
        os.environ.get("ANTHROPIC_API_KEY", "ASSENTE"), encoding="utf-8"
    )

    copione = json.loads((cartella / COPIONE).read_text(encoding="utf-8"))
    print(json.dumps(copione[min(quante - 1, len(copione) - 1)]))


class FintoClaude:
    """Governa il finto eseguibile: lo programma e ne osserva l'uso."""

    def __init__(self, cartella: Path):
        self.cartella = cartella

    def programma(self, *risposte: dict) -> None:
        (self.cartella / COPIONE).write_text(json.dumps(list(risposte)), encoding="utf-8")

    @property
    def chiamate(self) -> int:
        registro = self.cartella / REGISTRO
        if not registro.exists():
            return 0
        return len([r for r in registro.read_text(encoding="utf-8").splitlines() if r.strip()])

    @property
    def chiave_api_vista(self) -> str:
        spia = self.cartella / SPIA_CHIAVE
        return spia.read_text(encoding="utf-8").strip() if spia.exists() else ""

    risposta = staticmethod(risposta)


def installa(cartella: Path, monkeypatch, windows: bool | None = None) -> FintoClaude:
    """Mette un finto ``claude`` in testa al PATH e lo restituisce.

    ``windows`` esiste solo per poter collaudare da Unix il lanciatore
    che verra' scritto su Windows.
    """
    windows = os.name == "nt" if windows is None else windows
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / COPIONE).write_text(json.dumps([risposta("[]")]), encoding="utf-8")

    corpo = f'import sys; sys.path.insert(0, r"{Path(__file__).parent}"); ' \
            f'from finto_claude import _esegui; from pathlib import Path; ' \
            f'_esegui(Path(r"{cartella}"))'

    if windows:
        lanciatore = cartella / "claude.cmd"
        lanciatore.write_text(f'@echo off\r\n"{sys.executable}" -c "{corpo}"\r\n', encoding="utf-8")
    else:
        lanciatore = cartella / "claude"
        lanciatore.write_text(f'#!/bin/sh\nexec "{sys.executable}" -c \'{corpo}\'\n', encoding="utf-8")
        lanciatore.chmod(lanciatore.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{cartella}{os.pathsep}{os.environ['PATH']}")
    return FintoClaude(cartella)
