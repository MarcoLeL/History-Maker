"""Il backend Claude Code: composizione del comando, lettura della risposta.

Il ciclo completo e' collaudato contro un finto eseguibile ``claude`` che
imita la forma dell'output di ``--output-format json``: cosi' i test non
consumano quota dell'abbonamento e girano ovunque.
"""

import json
import os
import stat

import pytest

from history_maker import claudecode
from history_maker.claudecode import LimiteUsoRaggiunto, estrai_json


# --- lettura della risposta ------------------------------------------------

def test_json_dentro_blocco_markdown():
    """Il modello incornicia quasi sempre il JSON, anche se gli si dice di no."""
    assert estrai_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert estrai_json("```\n{\"a\": 1}\n```") == {"a": 1}


def test_json_nudo():
    assert estrai_json('[{"a": 1}]') == [{"a": 1}]


def test_json_con_frase_di_accompagnamento():
    testo = 'Ecco le trascrizioni:\n[{"a": 1}]\nSpero siano corrette.'
    assert estrai_json(testo) == [{"a": 1}]


def test_parentesi_dentro_le_stringhe_non_confondono():
    testo = 'Risultato: [{"nota": "illeggibile [?] e ] chiusa"}]'
    assert estrai_json(testo) == [{"nota": "illeggibile [?] e ] chiusa"}]


def test_json_annidato():
    assert estrai_json('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}


@pytest.mark.parametrize("testo", ["", "   ", "nessun json qui"])
def test_risposta_senza_json_solleva(testo):
    with pytest.raises(ValueError):
        estrai_json(testo)


# --- composizione del comando ---------------------------------------------

def test_comando_contiene_le_opzioni_necessarie(tmp_path):
    comando = claudecode.costruisci_comando(
        "prompt", "sistema", [tmp_path / "a", tmp_path / "b"], "claude-opus-5"
    )
    assert "--print" in comando and "prompt" in comando
    assert comando[comando.index("--system-prompt") + 1] == "sistema"
    assert comando[comando.index("--output-format") + 1] == "json"
    assert comando[comando.index("--model") + 1] == "claude-opus-5"
    # Al lavoro serve solo leggere immagini.
    assert comando[comando.index("--allowedTools") + 1] == "Read"
    assert "--restricted" in comando
    # Senza questo la CLI si fermerebbe ad aspettare un consenso.
    assert comando[comando.index("--permission-mode") + 1] == "dontAsk"
    # Ogni cartella di immagini va autorizzata.
    assert comando.count("--add-dir") == 2


# --- riconoscimento dell'esaurimento quota ---------------------------------

@pytest.mark.parametrize(
    "messaggio",
    [
        "Claude usage limit reached. Resets at 3pm",
        "You have hit the rate limit",
        "Limite di utilizzo raggiunto",
    ],
)
def test_messaggi_di_quota_riconosciuti(messaggio):
    assert claudecode._sembra_limite(messaggio)


def test_errore_normale_non_scambiato_per_quota():
    assert not claudecode._sembra_limite("File not found: /x/0001.jpg")


# --- ciclo completo contro un finto eseguibile -----------------------------

@pytest.fixture
def finto_claude(tmp_path, monkeypatch):
    """Installa un finto 'claude' nel PATH; restituisce come programmarlo."""
    uscite = tmp_path / "risposta.json"

    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\ncat " + str(uscite) + "\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    def programma(risultato: str, is_error: bool = False, subtype: str = "success"):
        uscite.write_text(
            json.dumps(
                {
                    "is_error": is_error,
                    "subtype": subtype,
                    "result": risultato,
                    "total_cost_usd": 0.04,
                    "usage": {
                        "input_tokens": 4,
                        "cache_creation_input_tokens": 7000,
                        "cache_read_input_tokens": 43000,
                        "output_tokens": 150,
                    },
                }
            ),
            encoding="utf-8",
        )

    return programma


def test_esecuzione_riuscita(finto_claude, tmp_path):
    finto_claude('```json\n[{"file": "0001.jpg", "atti": []}]\n```')
    esito = claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)

    assert esito.ok
    assert estrai_json(esito.testo) == [{"file": "0001.jpg", "atti": []}]
    # Il contesto e' la somma delle tre voci: e' la quota che si consuma.
    assert esito.token_contesto == 50_004
    assert esito.token_output == 150


def test_esecuzione_fallita_non_solleva(finto_claude, tmp_path):
    """Un errore normale torna come esito negativo, non come eccezione."""
    finto_claude("qualcosa e' andato storto", is_error=True, subtype="error_during_execution")
    esito = claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)
    assert not esito.ok and esito.errore == "error_during_execution"


def test_quota_esaurita_solleva(finto_claude, tmp_path):
    """L'esaurimento della quota va distinto da un errore di lettura."""
    finto_claude("Claude usage limit reached. Resets at 3pm", is_error=True, subtype="error")
    with pytest.raises(LimiteUsoRaggiunto):
        claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)


def test_claude_assente_da_messaggio_utile(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(claudecode.ClaudeCodeNonTrovato, match="npm install"):
        claudecode.verifica_installazione()
