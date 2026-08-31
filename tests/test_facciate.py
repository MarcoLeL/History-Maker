"""La divisione della scansione nelle sue due facciate.

E' l'intervento che alza di piu' l'accuratezza, e ha un modo preciso di
rompersi in silenzio: se il modello capisce che le due meta' sono due
pagine, restituisce due oggetti dove ne serviva uno, e da quel momento
ogni trascrizione finisce nel file della pagina successiva. Tutto quello
che c'e' qui sotto difende quel confine.
"""

import json

import pytest
from PIL import Image

from history_maker import transcribe
from history_maker.catalogo import Catalogo, Registro
from history_maker.config import Config, Trascrizione


def _config(tmp_path, **trascrizione) -> Config:
    impostazioni = {"backend": "claude-code", "modello": "claude-opus-5", "lato_lungo_px": 800}
    impostazioni.update(trascrizione)
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
        glossario=tmp_path / "glossario-inesistente.yaml",
        trascrizione=Trascrizione(**impostazioni),
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866", tipologia="Nati", anno=1866, archive_id="19944535",
    )


def _scansione(config, registro, nome="0001.jpg", dimensioni=(3600, 2300)) -> transcribe.Pagina:
    """Una doppia pagina delle proporzioni vere di questi registri."""
    cartella = config.immagini / registro.slug
    cartella.mkdir(parents=True, exist_ok=True)
    percorso = cartella / nome
    Image.new("RGB", dimensioni, "white").save(percorso, "JPEG")
    return transcribe.Pagina(percorso, registro)


# --- le immagini -----------------------------------------------------------


def test_una_scansione_diventa_due_facciate(tmp_path, registro):
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    percorsi = transcribe.prepara_immagini(_scansione(config, registro), config)

    assert [p.name for p in percorsi] == ["0001-sinistra.jpg", "0001-destra.jpg"]
    assert all(p.exists() for p in percorsi)


def test_dividere_alza_la_risoluzione_utile(tmp_path, registro):
    """Il punto di tutta la faccenda, in due numeri.

    A parita' di lato lungo, la doppia pagina spende i suoi pixel in
    larghezza e ogni facciata ne riceve la meta'. Divisa, ogni facciata e'
    verticale: il lato lungo diventa l'altezza e la larghezza utile
    cresce di circa la meta'.
    """
    intera = _config(tmp_path / "a", dividi_facciate=False, ritaglia=False)
    divisa = _config(tmp_path / "b", dividi_facciate=True, ritaglia=False)

    with Image.open(transcribe.prepara_immagini(_scansione(intera, registro), intera)[0]) as im:
        utile_prima = im.width // 2  # meta' della doppia pagina
    with Image.open(transcribe.prepara_immagini(_scansione(divisa, registro), divisa)[0]) as im:
        utile_dopo = im.width

    assert utile_dopo > utile_prima * 1.3


def test_le_due_meta_si_sovrappongono(tmp_path, registro):
    """Una parola a cavallo della piega non deve restare tagliata in due."""
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False, lato_lungo_px=10_000)
    sinistra, destra = transcribe.prepara_immagini(
        _scansione(config, registro, dimensioni=(1000, 800)), config
    )
    with Image.open(sinistra) as a, Image.open(destra) as b:
        assert a.width + b.width > 1000


def test_le_copie_gia_fatte_non_si_rifanno(tmp_path, registro):
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    pagina = _scansione(config, registro)
    prime = transcribe.prepara_immagini(pagina, config)
    firme = [p.stat().st_mtime_ns for p in prime]
    assert [p.stat().st_mtime_ns for p in transcribe.prepara_immagini(pagina, config)] == firme


def test_senza_divisione_resta_una_sola_immagine(tmp_path, registro):
    config = _config(tmp_path, dividi_facciate=False, ritaglia=False)
    percorsi = transcribe.prepara_immagini(_scansione(config, registro), config)
    assert [p.name for p in percorsi] == ["0001.jpg"]


# --- il prompt -------------------------------------------------------------


def test_il_prompt_dice_che_le_due_meta_sono_una_pagina_sola(tmp_path, registro):
    """Senza questa frase il modello restituisce due oggetti e disallinea tutto."""
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    pagina = _scansione(config, registro)
    immagini = [transcribe.prepara_immagini(pagina, config)]

    prompt = transcribe.costruisci_prompt([pagina], immagini)

    assert "UNA SOLA pagina" in prompt
    assert "UN SOLO oggetto per scansione" in prompt
    assert "0001.jpg" in prompt  # il nome della scansione, non quello delle meta'


def test_senza_divisione_la_nota_non_compare(tmp_path, registro):
    """Non va detto quando non serve: e' istruzione che confonde e basta."""
    config = _config(tmp_path, dividi_facciate=False, ritaglia=False)
    pagina = _scansione(config, registro)
    immagini = [transcribe.prepara_immagini(pagina, config)]

    assert "UNA SOLA pagina" not in transcribe.costruisci_prompt([pagina], immagini)


# --- l'allineamento --------------------------------------------------------


def test_la_risposta_si_allinea_sul_nome_della_scansione(tmp_path, registro):
    """Il modello nomina la scansione; le immagini su disco hanno altri nomi."""
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    pagina = _scansione(config, registro)
    immagini = [transcribe.prepara_immagini(pagina, config)]

    allineate = transcribe._allinea(
        [{"file": "0001.jpg", "tipo_pagina": "atti"}], [pagina], immagini
    )
    assert allineate[0]["tipo_pagina"] == "atti"


def test_si_allinea_anche_sul_nome_di_una_meta(tmp_path, registro):
    """Se il modello nomina la meta' invece della scansione, va bene lo stesso."""
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    pagina = _scansione(config, registro)
    immagini = [transcribe.prepara_immagini(pagina, config)]

    allineate = transcribe._allinea(
        [{"file": "0001-destra.jpg", "tipo_pagina": "indice"}], [pagina], immagini
    )
    assert allineate[0]["tipo_pagina"] == "indice"


def test_ogni_pagina_prende_la_sua_e_non_quella_della_vicina(tmp_path, registro):
    """Il guasto peggiore di tutti: nomi simili, risposta fuori ordine."""
    config = _config(tmp_path, dividi_facciate=True, ritaglia=False)
    pagine = [_scansione(config, registro, f"{i:04d}.jpg") for i in (1, 2, 3)]
    immagini = [transcribe.prepara_immagini(p, config) for p in pagine]

    allineate = transcribe._allinea(
        [
            {"file": "immagine 5 (0003.jpg)", "anno_indicato": "terza"},
            {"file": "0001.jpg", "anno_indicato": "prima"},
            {"file": "immagine 3 (0002-sinistra.jpg)", "anno_indicato": "seconda"},
        ],
        pagine,
        immagini,
    )
    assert [v["anno_indicato"] for v in allineate] == ["prima", "seconda", "terza"]


def test_un_solo_json_per_scansione_anche_se_le_immagini_erano_due(tmp_path, registro, monkeypatch):
    """La prova che chiude il cerchio: due immagini dentro, un file fuori."""
    from test_gemini import FintaSessione, _risposta_ok  # noqa: PLC0415

    monkeypatch.setenv("GEMINI_API_KEY", "chiave-di-prova")

    config = _config(tmp_path, backend="gemini", modello="gemini-2.5-flash", dividi_facciate=True,
                     ritaglia=False)
    Catalogo(comune="Torrebruna", registri=[registro]).salva(config.catalogo)
    _scansione(config, registro)
    pagine = transcribe.pagine_da_trascrivere(config)

    from history_maker.gemini import BackendGemini  # noqa: PLC0415

    sessione = FintaSessione(
        _risposta_ok([{"file": "0001.jpg", "tipo_pagina": "atti", "atti": []}])
    )
    motore = BackendGemini(config, sessione)
    esito = transcribe.trascrivi_gruppo(config, pagine, motore)

    assert (esito.trascritte, esito.fallite) == (1, 0)
    prodotti = sorted(p.name for p in (config.trascrizioni / registro.slug).iterdir())
    assert prodotti == ["0001.json"]
    # Ma le immagini allegate erano davvero due.
    parti = sessione.richieste[0]["corpo"]["contents"][0]["parts"]
    assert sum(1 for p in parti if "inlineData" in p) == 2
    salvato = json.loads((config.trascrizioni / registro.slug / "0001.json").read_text("utf-8"))
    assert salvato["_origine"]["immagine"].endswith("0001.jpg")
