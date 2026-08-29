"""La fase 3 nel suo insieme, contro un finto 'claude' programmabile.

Verifica le tre cose che rendono utilizzabile questo backend: le pagine
vanno a gruppi per non sprecare quota, la risposta viene riallineata alle
pagine giuste, e l'esaurimento della quota interrompe il lavoro senza
perderlo.
"""

import json

import pytest
from PIL import Image

from history_maker import claudecode, transcribe
from history_maker.catalogo import Catalogo, Registro
from history_maker.config import Config


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        comune="Torrebruna",
        termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"],
        escludi_contesto=["Guardiabruna"],
        anno_min=1809,
        anno_max=1900,
        tipologie=[],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866", tipologia="Nati", anno=1866, archive_id="19944535",
    )


@pytest.fixture
def pagine(config, registro):
    """Sei pagine scaricate e pronte per la trascrizione."""
    Catalogo(comune="Torrebruna", registri=[registro]).salva(config.catalogo)
    cartella = config.immagini / registro.slug
    cartella.mkdir(parents=True, exist_ok=True)
    for i in range(1, 7):
        Image.new("RGB", (400, 500), "white").save(cartella / f"{i:04d}.jpg", "JPEG")
    return transcribe.pagine_da_trascrivere(config)


def _pagina_json(nome, numero):
    return {
        "file": nome, "tipo_pagina": "atti", "anno_indicato": "1866",
        "comune_indicato": "Torrebruna", "osservazioni": None,
        "atti": [{
            "numero_atto": str(numero), "tipo": "nascita", "data_atto": "1866-03-27",
            "data_evento": None, "ora_evento": None, "luogo": "Torrebruna",
            "persone": [{"ruolo": "neonato", "nome": "Maria", "cognome": "Di Nardo",
                         "eta": None, "professione": None, "residenza": None,
                         "stato_vitale": None, "note": None}],
            "testo_integrale": "L'anno milleottocentosessantasei...",
            "parti_illeggibili": [], "affidabilita": "alta",
        }],
    }


def test_le_pagine_vanno_a_gruppi(config, pagine, finto_claude):
    """Sei pagine a quattro per chiamata sono due invocazioni, non sei.

    E' il punto di tutto il backend: il sovraccarico di Claude Code si
    paga per invocazione.
    """
    finto_claude.programma(
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(1, 5)])),
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(5, 7)])),
    )
    esito = transcribe.esegui(config, pagine)

    assert esito.chiamate == 2
    assert esito.trascritte == 6 and esito.fallite == 0
    assert finto_claude.chiamate == 2


def test_ogni_pagina_finisce_nel_proprio_file(config, pagine, registro, finto_claude):
    finto_claude.programma(
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(1, 5)])),
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(5, 7)])),
    )
    transcribe.esegui(config, pagine)

    cartella = config.trascrizioni / registro.slug
    assert sorted(p.name for p in cartella.glob("*.json")) == [f"{i:04d}.json" for i in range(1, 7)]

    dati = json.loads((cartella / "0003.json").read_text(encoding="utf-8"))
    assert dati["atti"][0]["numero_atto"] == "3"
    # La provenienza viaggia con la trascrizione: senza, il dato non e' citabile.
    assert dati["_origine"]["ark_url"] == registro.ark_url
    assert dati["_origine"]["anno"] == 1866
    # 'file' serviva solo ad allineare la risposta, non finisce nell'archivio.
    assert "file" not in dati


def test_risposta_in_ordine_sparso_riallineata(config, pagine, registro, finto_claude):
    """Se il modello inverte l'ordine, il nome del file rimette a posto."""
    invertite = [_pagina_json(f"{i:04d}.jpg", i) for i in (3, 1, 4, 2)]
    finto_claude.programma(
        finto_claude.risposta(json.dumps(invertite)),
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in (5, 6)])),
    )
    transcribe.esegui(config, pagine)

    cartella = config.trascrizioni / registro.slug
    for i in range(1, 5):
        dati = json.loads((cartella / f"{i:04d}.json").read_text(encoding="utf-8"))
        assert dati["atti"][0]["numero_atto"] == str(i)


def test_risposta_senza_nomi_allineata_per_posizione(config, pagine, registro, finto_claude):
    senza_nome = [{"tipo_pagina": "atti", "atti": [{"numero_atto": str(i)}]} for i in range(1, 5)]
    finto_claude.programma(
        finto_claude.risposta(json.dumps(senza_nome)),
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in (5, 6)])),
    )
    transcribe.esegui(config, pagine)

    dati = json.loads(
        (config.trascrizioni / registro.slug / "0002.json").read_text(encoding="utf-8")
    )
    assert dati["atti"][0]["numero_atto"] == "2"


def test_gruppo_illeggibile_ritentato_una_pagina_per_volta(config, pagine, finto_claude):
    """Una risposta inutilizzabile non deve perdere l'intero gruppo."""
    buone = [finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i)])) for i in range(1, 5)]
    finto_claude.programma(
        finto_claude.risposta("scusa, non riesco a leggere"),  # gruppo 1: illeggibile
        *buone,                                                 # poi una per volta
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in (5, 6)])),
    )
    esito = transcribe.esegui(config, pagine[:4])

    assert esito.trascritte == 4
    # 1 tentativo di gruppo + 4 singoli.
    assert esito.chiamate == 5


def test_pagina_vuota_e_un_risultato_valido(config, pagine, registro, finto_claude):
    """Una copertina senza atti non e' un fallimento."""
    vuote = [{"file": f"{i:04d}.jpg", "tipo_pagina": "copertina", "atti": []} for i in range(1, 5)]
    finto_claude.programma(finto_claude.risposta(json.dumps(vuote)))
    esito = transcribe.esegui(config, pagine[:4])

    assert esito.trascritte == 4 and esito.fallite == 0
    dati = json.loads(
        (config.trascrizioni / registro.slug / "0001.json").read_text(encoding="utf-8")
    )
    assert dati["tipo_pagina"] == "copertina" and dati["atti"] == []


def test_quota_esaurita_ferma_senza_perdere_il_fatto(config, pagine, registro, finto_claude):
    finto_claude.programma(
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(1, 5)])),
        finto_claude.risposta("Claude usage limit reached. Resets at 3pm", is_error=True, subtype="error"),
    )
    esito = transcribe.esegui(config, pagine, attendi_quota=False)

    assert esito.quota_esaurita
    assert esito.trascritte == 4        # il primo gruppo e' salvo
    cartella = config.trascrizioni / registro.slug
    assert len(list(cartella.glob("*.json"))) == 4


def test_rilanciare_riprende_dalle_pagine_mancanti(config, pagine, registro, finto_claude):
    finto_claude.programma(
        finto_claude.risposta(json.dumps([_pagina_json(f"{i:04d}.jpg", i) for i in range(1, 5)])),
        finto_claude.risposta("Claude usage limit reached", is_error=True, subtype="error"),
    )
    transcribe.esegui(config, pagine)

    # Un nuovo giro deve vedere solo le due pagine rimaste.
    rimaste = transcribe.pagine_da_trascrivere(config)
    assert [p.percorso.name for p in rimaste] == ["0005.jpg", "0006.jpg"]


def test_stima_senza_pagine():
    assert "Nessuna pagina" in transcribe.stima(None, [])
