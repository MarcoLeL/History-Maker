"""Le fasi 2, 3 e 4 collaudate senza toccare la rete ne' l'API."""

import json

import pytest
from PIL import Image

from history_maker import dataset, discover, download, transcribe
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
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866",
        tipologia="Nati",
        anno=1866,
        archive_id="19944535",
    )


# --- fase 1: costruzione delle interrogazioni ------------------------------

def test_url_di_ricerca_per_anno():
    assert discover.url_ricerca("Torrebruna", 1866) == (
        "https://antenati.cultura.gov.it/search-registry/?localita=Torrebruna&anno=1866"
    )


def test_url_di_ricerca_senza_anno():
    assert discover.url_ricerca("Torrebruna").endswith("?localita=Torrebruna")


# --- fase 2: nomi dei file ------------------------------------------------

@pytest.mark.parametrize(
    "canvas, indice, atteso",
    [
        ({"label": "12"}, 3, "0012"),          # l'etichetta numerica del portale vince
        ({"label": "Copertina"}, 1, "0001-copertina"),
        ({}, 7, "0007"),                        # senza etichetta si usa la posizione
    ],
)
def test_nome_pagina_ordinabile(canvas, indice, atteso):
    assert download.nome_pagina(canvas, indice) == atteso


def test_nomi_pagina_in_ordine_alfabetico():
    nomi = [download.nome_pagina({"label": str(n)}, n) for n in (2, 10, 100)]
    assert nomi == sorted(nomi)


# --- fase 3: costruzione della richiesta a Claude --------------------------

def _immagine_finta(percorso, dimensione=(3000, 4000)):
    percorso.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", dimensione, "white").save(percorso, "JPEG")
    return percorso


def test_immagine_ridimensionata_al_lato_richiesto(tmp_path):
    percorso = _immagine_finta(tmp_path / "0001.jpg")
    import base64
    import io

    media_type, dati = transcribe.prepara_immagine(percorso, 1568)
    assert media_type == "image/jpeg"
    with Image.open(io.BytesIO(base64.standard_b64decode(dati))) as ridotta:
        assert max(ridotta.size) == 1568


def test_immagine_piccola_non_viene_ingrandita(tmp_path):
    percorso = _immagine_finta(tmp_path / "0002.jpg", (800, 600))
    import base64
    import io

    _, dati = transcribe.prepara_immagine(percorso, 1568)
    with Image.open(io.BytesIO(base64.standard_b64decode(dati))) as immagine:
        assert immagine.size == (800, 600)


def test_richiesta_contiene_immagine_contesto_e_schema(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0001.jpg"), registro)
    richiesta = transcribe.costruisci_richiesta(pagina, config)

    assert richiesta["model"] == config.trascrizione.modello
    # Il prompt di sistema va in cache: e' identico per migliaia di pagine.
    assert richiesta["system"][0]["cache_control"] == {"type": "ephemeral"}
    contenuto = richiesta["messages"][0]["content"]
    assert contenuto[0]["type"] == "image"
    assert "1866" in contenuto[1]["text"] and "Nati" in contenuto[1]["text"]
    # Lo schema vincola la risposta: niente JSON da riparare a mano.
    assert richiesta["output_config"]["format"]["type"] == "json_schema"


def test_id_richiesta_stabile(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0042.jpg"), registro)
    assert pagina.id_richiesta == "1866-nati-19944535--0042"


def test_pagine_da_trascrivere_salta_quelle_gia_fatte(config, registro):
    catalogo = Catalogo(comune="Torrebruna", registri=[registro])
    catalogo.salva(config.catalogo)
    cartella = config.immagini / registro.slug
    for nome in ("0001.jpg", "0002.jpg"):
        _immagine_finta(cartella / nome, (400, 400))
    (cartella / "manifest.json").write_text("{}", encoding="utf-8")  # non e' una pagina

    assert len(transcribe.pagine_da_trascrivere(config)) == 2

    fatta = config.trascrizioni / registro.slug / "0001.json"
    fatta.parent.mkdir(parents=True, exist_ok=True)
    fatta.write_text("{}", encoding="utf-8")
    assert [p.percorso.name for p in transcribe.pagine_da_trascrivere(config)] == ["0002.jpg"]
    assert len(transcribe.pagine_da_trascrivere(config, solo_mancanti=False)) == 2


def test_registri_non_pertinenti_non_vengono_trascritti(config, registro):
    fuori = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua777/abc",
        contesto="Chieti/Guardiabruna", titolo="1866", tipologia="Nati", anno=1866,
        archive_id="777",
    )
    Catalogo(comune="Torrebruna", registri=[registro, fuori]).salva(config.catalogo)
    for reg in (registro, fuori):
        _immagine_finta(config.immagini / reg.slug / "0001.jpg", (400, 400))

    slug_trovati = {p.registro.slug for p in transcribe.pagine_da_trascrivere(config)}
    assert slug_trovati == {registro.slug}


# --- fase 4: aggregazione -------------------------------------------------

def _trascrizione(config, registro, nome, atti):
    percorso = config.trascrizioni / registro.slug / nome
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        json.dumps(
            {
                "tipo_pagina": "atti",
                "anno_indicato": str(registro.anno),
                "comune_indicato": "Torrebruna",
                "atti": atti,
                "osservazioni": None,
                "_origine": {
                    "immagine": f"{registro.slug}/{nome.replace('.json', '.jpg')}",
                    "registro": registro.slug,
                    "ark_url": registro.ark_url,
                    "anno": registro.anno,
                    "tipologia": registro.tipologia,
                    "contesto": registro.contesto,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


ATTO = {
    "numero_atto": "17",
    "tipo": "nascita",
    "data_atto": "1866-03-27",
    "data_evento": "1866-03-26",
    "ora_evento": "ore ventidue",
    "luogo": "Torrebruna",
    "persone": [
        {"ruolo": "neonato", "nome": "Maria", "cognome": "Di Nardo", "eta": None,
         "professione": None, "residenza": "Torrebruna", "stato_vitale": None, "note": None},
        {"ruolo": "padre", "nome": "Giuseppe", "cognome": "Di Nardo", "eta": "trentadue",
         "professione": "contadino", "residenza": "Torrebruna", "stato_vitale": "vivente", "note": None},
    ],
    "testo_integrale": "L'anno milleottocentosessantasei, addi' ventisette di marzo...",
    "parti_illeggibili": ["il cognome della levatrice"],
    "affidabilita": "alta",
}


def test_dataset_da_trascrizioni(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    _trascrizione(config, registro, "0002.json", [])  # pagina bianca

    percorso_db = dataset.costruisci(config)
    import sqlite3

    conn = sqlite3.connect(percorso_db)
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 2
    assert conn.execute("SELECT anno FROM atti").fetchone()[0] == 1866
    assert conn.execute(
        "SELECT incertezze FROM atti"
    ).fetchone()[0] == "il cognome della levatrice"

    # La ricerca full-text e' il modo in cui si interroga davvero l'archivio.
    trovati = conn.execute(
        "SELECT numero_atto FROM atti_fts WHERE atti_fts MATCH 'milleottocentosessantasei'"
    ).fetchall()
    assert trovati == [("17",)]
    conn.close()

    assert (config.dataset / "atti.csv").exists()
    assert (config.dataset / "persone.csv").exists()
    sintesi = (config.dataset / "sintesi.md").read_text(encoding="utf-8")
    assert "Di Nardo" in sintesi and "contadino" in sintesi


def test_sintesi_elenca_gli_anni_senza_atti(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    dataset.costruisci(config)
    sintesi = (config.dataset / "sintesi.md").read_text(encoding="utf-8")
    assert "Anni senza atti trascritti" in sintesi
    assert "1809-1865" in sintesi and "1867-1900" in sintesi


def test_ricostruzione_non_duplica(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    dataset.costruisci(config)
    percorso_db = dataset.costruisci(config)
    import sqlite3

    conn = sqlite3.connect(percorso_db)
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize(
    "anni, atteso",
    [
        ([1810, 1811, 1812, 1820], "1810-1812, 1820"),
        ([1850], "1850"),
        ([], "nessuno"),
    ],
)
def test_intervalli_compattati(anni, atteso):
    assert dataset._compatta_intervalli(anni) == atteso
