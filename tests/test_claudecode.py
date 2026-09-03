"""Il backend Claude Code: composizione del comando, lettura della risposta.

Il ciclo completo e' collaudato contro un finto eseguibile ``claude`` che
imita la forma dell'output di ``--output-format json``: cosi' i test non
consumano quota dell'abbonamento e girano ovunque.
"""

import json
import os

import pytest
from conftest import FINTI, installa_finto_claude

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

def test_comando_contiene_le_opzioni_necessarie(tmp_path, finto_claude):
    sistema = tmp_path / "sistema.txt"
    comando = claudecode.costruisci_comando(
        sistema, [tmp_path / "a", tmp_path / "b"], "claude-opus-5"
    )
    assert "--print" in comando
    assert comando[comando.index("--system-prompt-file") + 1] == str(sistema)
    assert comando[comando.index("--output-format") + 1] == "json"
    assert comando[comando.index("--model") + 1] == "claude-opus-5"
    # Al lavoro serve solo leggere immagini.
    assert comando[comando.index("--allowedTools") + 1] == "Read"
    assert "--restricted" in comando
    # Senza questo la CLI si fermerebbe ad aspettare un consenso.
    assert comando[comando.index("--permission-mode") + 1] == "dontAsk"
    # Ogni cartella di immagini va autorizzata.
    assert comando.count("--add-dir") == 2


def test_i_testi_lunghi_non_passano_dalla_riga_di_comando(tmp_path, finto_claude):
    """Ne' il prompt ne' il prompt di sistema stanno negli argomenti.

    Su Windows 'claude' e' un .cmd, quindi la chiamata passa da cmd.exe,
    che si ferma al primo ritorno a capo e interpreta '>' come una
    redirezione. L'elenco delle pagine e' multiriga e il contesto
    archivistico contiene '>': passarli come argomenti li mutila.
    """
    comando = claudecode.costruisci_comando(
        tmp_path / "sistema.txt", [tmp_path], "claude-opus-5"
    )
    riga = " ".join(comando)
    assert "\n" not in riga
    assert ">" not in riga
    assert "|" not in riga


# --- riconoscimento dell'esaurimento quota ---------------------------------

@pytest.mark.parametrize(
    "messaggio",
    [
        "Claude usage limit reached. Resets at 3pm",
        "You have hit the rate limit",
        "Limite di utilizzo raggiunto",
        # Il messaggio che ha originato questo test: la prima esecuzione
        # vera si e' fermata cosi' e nessun segnale lo intercettava, per
        # cui l'esaurimento della quota e' stato scambiato per una pagina
        # illeggibile e ritentato dieci volte — bruciando altra quota
        # invece di fermarsi e dire quando riprovare.
        "You've hit your session limit · resets 5:10pm (Europe/Rome)",
        "Session limit reached · resets 9am",
    ],
)
def test_messaggi_di_quota_riconosciuti(messaggio):
    assert claudecode._sembra_limite(messaggio)


@pytest.mark.parametrize(
    "messaggio",
    [
        "File not found: /x/0001.jpg",
        # "resets" da sola non basta: puo' comparire in un testo trascritto.
        "l'atto dice che il termine si resets ogni anno",
    ],
)
def test_errore_normale_non_scambiato_per_quota(messaggio):
    assert not claudecode._sembra_limite(messaggio)


# --- ciclo completo contro un finto eseguibile -----------------------------

@pytest.fixture
def finto_claude(tmp_path, monkeypatch):
    """Installa un finto 'claude' nel PATH; restituisce come programmarlo."""
    uscite = tmp_path / "risposta.json"

    installa_finto_claude(
        tmp_path, monkeypatch, FINTI / "eco_risposta.py", FINTO_RISPOSTA=str(uscite)
    )

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
    """Un errore normale torna come esito negativo, non come eccezione.

    Il messaggio viene da 'result', non da 'subtype': 'subtype' e' il
    tipo di conclusione della sessione, non una spiegazione, e su un
    turno fallito puo' restare 'success' — vedi il test sotto.
    """
    finto_claude("qualcosa e' andato storto", is_error=True, subtype="error_during_execution")
    esito = claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)
    assert not esito.ok and esito.errore == "qualcosa e' andato storto"


def test_il_messaggio_non_viene_da_subtype_quando_dice_successo(finto_claude, tmp_path):
    """Il caso vero che ha reso questa distinzione necessaria.

    Un modello inesistente ('claude --model gemini-3.5-flash-lite') fa
    fallire il turno con is_error=true, ma la sessione si conclude
    comunque con subtype='success': prendere 'subtype' come messaggio
    d'errore avrebbe detto 'success' su un fallimento vero.
    """
    finto_claude(
        "There's an issue with the selected model (gemini-3.5-flash-lite). "
        "It may not exist or you may not have access to it.",
        is_error=True, subtype="success",
    )
    esito = claudecode.esegui("p", "s", [tmp_path], "gemini-3.5-flash-lite", timeout=30)
    assert not esito.ok
    assert esito.errore != "success"
    assert "gemini-3.5-flash-lite" in esito.errore


def test_quota_esaurita_solleva(finto_claude, tmp_path):
    """L'esaurimento della quota va distinto da un errore di lettura."""
    finto_claude("Claude usage limit reached. Resets at 3pm", is_error=True, subtype="error")
    with pytest.raises(LimiteUsoRaggiunto):
        claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)


def test_gli_accenti_sopravvivono_al_processo_figlio(tmp_path, monkeypatch):
    """La risposta e' UTF-8 anche dove il sistema preferisce altro.

    Senza dirlo esplicitamente, Python decodifica l'output del figlio con
    la codifica preferita del sistema: su Windows una codepage a un byte,
    che trasforma 'addì' in 'addÃ¬'. Su atti italiani sarebbe corruzione
    silenziosa del risultato del lavoro.
    """
    installa_finto_claude(tmp_path, monkeypatch, FINTI / "risposta_accentata.py")

    esito = claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)

    assert esito.ok
    assert "addì ventisette" in esito.testo
    assert "città di Torrebruna" in esito.testo
    assert "trentadué" in esito.testo


def test_il_prompt_arriva_intatto_al_processo_figlio(tmp_path, monkeypatch):
    """Il caso che ha fermato la prima esecuzione vera.

    Il prompt e' multiriga e il contesto archivistico contiene '>' e '|'.
    Passato come argomento su Windows arrivava troncato alla prima riga, e
    il modello rispondeva di non sapere quali pagine leggere — senza che
    nulla, nel codice che compone il prompt, fosse sbagliato.
    """
    installa_finto_claude(tmp_path, monkeypatch, FINTI / "eco_prompt.py")

    prompt = (
        "Trascrivi queste 2 pagine.\n"
        "- C:\\dati\\0001.jpg\n"
        "    contesto: Archivio di Stato di Chieti > Stato civile "
        "napoleonico > Torrebruna | anno: 1809\n"
        "- C:\\dati\\0002.jpg & altro 100% (parentesi)\n"
    )
    sistema = "Sei un paleografo.\nRispondi in JSON: {\"a\": 1} & basta.\n"

    esito = claudecode.esegui(prompt, sistema, [tmp_path], "claude-opus-5", timeout=60)

    ricevuto = json.loads(esito.testo)
    assert ricevuto["prompt"] == prompt
    assert ricevuto["sistema"] == sistema


def test_claude_assente_da_messaggio_utile(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(claudecode.ClaudeCodeNonTrovato, match="npm install"):
        claudecode.verifica_installazione()


# --- garanzia che il lavoro non venga fatturato a consumo ------------------

def test_chiavi_api_tolte_dall_ambiente(monkeypatch):
    """Con una chiave API impostata, Claude Code fatturerebbe a consumo.

    La pipeline e' pensata per l'abbonamento: le credenziali a pagamento
    vanno escluse per costruzione, non lasciate alla disciplina di chi la
    lancia.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qualcosa")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "un-token")
    monkeypatch.setenv("PERCORSO_INNOCUO", "resta")

    ambiente = claudecode.ambiente_solo_abbonamento()

    assert "ANTHROPIC_API_KEY" not in ambiente
    assert "ANTHROPIC_AUTH_TOKEN" not in ambiente
    assert ambiente["PERCORSO_INNOCUO"] == "resta"


def test_ambiente_intatto_senza_chiavi(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert claudecode.ambiente_solo_abbonamento() == dict(os.environ)


def test_chiave_api_non_raggiunge_il_processo_figlio(tmp_path, monkeypatch):
    """Verifica end-to-end: il finto 'claude' non vede la chiave."""
    spia = tmp_path / "visto.txt"
    installa_finto_claude(
        tmp_path, monkeypatch, FINTI / "spia_ambiente.py", FINTO_SPIA=str(spia)
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-non-deve-passare")

    claudecode.esegui("p", "s", [tmp_path], "claude-opus-5", timeout=30)
    assert spia.read_text().strip() == "ASSENTE"
