"""Il filtro della raccolta: 1809-1900, Torrebruna si', Guardiabruna no.

E' la regola che il progetto deve azzeccare: Guardiabruna e' oggi una
frazione di Torrebruna, ma fino al 1928 era comune autonomo con registri
propri, e il portale la restituisce nella stessa ricerca.
"""

from dataclasses import replace

import pytest

from history_maker.catalogo import Catalogo, Registro, pertinente
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


def registro(contesto: str, anno: int | None, tipologia: str = "Nati", url: str = "u") -> Registro:
    return Registro(
        ark_url=url,
        contesto=contesto,
        titolo=str(anno) if anno else "",
        tipologia=tipologia,
        anno=anno,
    )


TORREBRUNA = "Archivio di Stato di Chieti/Stato civile italiano/Torrebruna"
GUARDIABRUNA = "Archivio di Stato di Chieti/Stato civile della restaurazione/Guardiabruna"


def test_torrebruna_nel_periodo_e_pertinente(config):
    assert pertinente(registro(TORREBRUNA, 1866), config)[0]


@pytest.mark.parametrize("anno", [1809, 1900])
def test_estremi_del_periodo_inclusi(config, anno):
    assert pertinente(registro(TORREBRUNA, anno), config)[0]


@pytest.mark.parametrize("anno", [1808, 1901, 1930])
def test_fuori_periodo_escluso(config, anno):
    ok, motivo = pertinente(registro(TORREBRUNA, anno), config)
    assert not ok and "fuori dall'intervallo" in motivo


def test_guardiabruna_sempre_esclusa(config):
    """Anche nel periodo giusto e con la tipologia giusta."""
    ok, motivo = pertinente(registro(GUARDIABRUNA, 1866), config)
    assert not ok and "Guardiabruna" in motivo


def test_guardiabruna_esclusa_anche_se_il_contesto_nomina_torrebruna(config):
    """Il portale a volte annida la frazione sotto il comune odierno.

    L'esclusione deve vincere sull'inclusione, altrimenti un contesto come
    'Torrebruna/Guardiabruna' passerebbe il filtro.
    """
    ok, _ = pertinente(registro("Chieti/Torrebruna/Guardiabruna", 1866), config)
    assert not ok


def test_altro_comune_escluso(config):
    ok, motivo = pertinente(registro("Chieti/Castiglione Messer Marino", 1866), config)
    assert not ok and "non nomina il comune" in motivo


def test_maiuscole_e_accenti_non_contano(config):
    assert pertinente(registro("CHIETI/TORREBRUNA", 1866), config)[0]


def test_anno_mancante_escluso(config):
    ok, motivo = pertinente(registro(TORREBRUNA, None), config)
    assert not ok and "anno non ricavabile" in motivo


def test_manifest_non_risolto_escluso(config):
    ok, motivo = pertinente(Registro(ark_url="u"), config)
    assert not ok and "manifest non risolto" in motivo


def test_filtro_per_tipologia(tmp_path, config):
    solo_nati = Config(**{**config.__dict__, "tipologie": ["Nati"]})
    assert pertinente(registro(TORREBRUNA, 1866, "Nati"), solo_nati)[0]
    ok, motivo = pertinente(registro(TORREBRUNA, 1866, "Morti"), solo_nati)
    assert not ok and "non richiesta" in motivo


def test_metadati_dal_manifest(manifest_torrebruna, config):
    reg = Registro(ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x")
    reg.applica_manifest(manifest_torrebruna)
    assert reg.anno == 1866 and reg.tipologia == "Nati" and reg.n_immagini == 2
    assert pertinente(reg, config)[0]


def test_guardiabruna_dal_manifest(manifest_guardiabruna, config):
    reg = Registro(ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua777/abc")
    reg.applica_manifest(manifest_guardiabruna)
    assert not pertinente(reg, config)[0]


def test_catalogo_persistente_e_idempotente(config):
    catalogo = Catalogo(comune="Torrebruna")
    assert catalogo.unisci([registro(TORREBRUNA, 1866, url="a")]) == 1
    # Rilanciare discover non deve duplicare ne' sovrascrivere.
    assert catalogo.unisci([registro(TORREBRUNA, 1866, url="a")]) == 0
    assert catalogo.unisci([registro(TORREBRUNA, 1867, url="b")]) == 1

    catalogo.salva(config.catalogo)
    riletto = Catalogo.carica(config.catalogo)
    assert riletto.comune == "Torrebruna"
    assert {r.ark_url for r in riletto.registri} == {"a", "b"}


def test_slug_ordinabile_per_anno():
    a = Registro(ark_url="u", anno=1866, tipologia="Nati", archive_id="1")
    b = Registro(ark_url="u", anno=1899, tipologia="Nati", archive_id="2")
    assert a.slug < b.slug
    assert a.slug == "1866-nati-1"


# --- esclusione per tipologia ----------------------------------------------

def test_una_tipologia_esclusa_resta_fuori(config):
    """I processetti sono il grosso della serie e la parte piu' faticosa:
    si deve poterli lasciare fuori senza elencare tutto il resto."""
    config = replace(config, escludi_tipologie=["processetti"])
    reg = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua1",
        anno=1809,
        tipologia="Matrimoni, processetti",
        contesto="Archivio di Stato di Chieti/Stato civile italiano/Torrebruna",
    )
    ok, motivo = pertinente(reg, config)
    assert not ok
    assert "processetti" in motivo


def test_l_esclusione_non_tocca_le_altre_tipologie(config):
    config = replace(config, escludi_tipologie=["processetti"])
    reg = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua2",
        anno=1809,
        tipologia="Matrimoni",
        contesto="Archivio di Stato di Chieti/Stato civile italiano/Torrebruna",
    )
    assert pertinente(reg, config)[0]


def test_l_esclusione_vince_sull_inclusione(config):
    """Come per il contesto: chiedere una tipologia ed escluderla insieme
    deve lasciarla fuori, non farla entrare."""
    config = replace(
        config,
        tipologie=["Matrimoni, processetti"],
        escludi_tipologie=["processetti"],
    )
    reg = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua3",
        anno=1809,
        tipologia="Matrimoni, processetti",
        contesto="Archivio di Stato di Chieti/Stato civile italiano/Torrebruna",
    )
    assert not pertinente(reg, config)[0]


# --- le esclusioni valgono anche a valle ------------------------------------


def test_le_pagine_dei_registri_esclusi_non_entrano_nel_database(tmp_path):
    """Un'esclusione in configurazione non si applica da sola a cio' che
    e' gia' su disco.

    Escludere le pubblicazioni ferma la fase 3, ma le pagine trascritte
    prima restano nella cartella: senza filtro rientrerebbero dalla porta
    di servizio, e siccome una pubblicazione nomina gli stessi sposi e gli
    stessi genitori dell'atto di matrimonio vero, le stesse persone
    finirebbero contate due volte.
    """
    import json

    from history_maker.dataset import leggi_trascrizioni

    cartella = tmp_path / "trascrizioni"
    for slug in ("1810-matrimoni-1", "1810-matrimoni-pubblicazioni-2"):
        (cartella / slug).mkdir(parents=True)
        (cartella / slug / "0001.json").write_text(
            json.dumps({"tipo_pagina": "atti", "atti": [], "_origine": {"registro": slug}}),
            encoding="utf-8",
        )

    tutte = list(leggi_trascrizioni(cartella))
    assert len(tutte) == 2

    filtrate = list(leggi_trascrizioni(cartella, ammessi={"1810-matrimoni-1"}))
    assert [p["_origine"]["registro"] for p in filtrate] == ["1810-matrimoni-1"]


def test_un_registro_sconosciuto_al_catalogo_si_tiene(tmp_path):
    """Buttare via in silenzio una trascrizione messa li' a mano sarebbe peggio."""
    import json

    from history_maker.dataset import leggi_trascrizioni

    cartella = tmp_path / "trascrizioni" / "aggiunto-a-mano"
    cartella.mkdir(parents=True)
    (cartella / "0001.json").write_text(
        json.dumps({"tipo_pagina": "atti", "atti": []}), encoding="utf-8"
    )
    assert len(list(leggi_trascrizioni(cartella.parent, ammessi={"altro"}))) == 1


def test_lo_scaricamento_non_tocca_il_catalogo(tmp_path, monkeypatch):
    """Il catalogo ha un solo proprietario: la scoperta.

    Lo scaricamento lo salvava dopo ogni registro, per aggiornare un campo
    che nessuno legge. Tenendolo in memoria per mezz'ora e riscrivendolo
    alla fine, ha cancellato 61 registri che una scoperta lanciata nel
    frattempo aveva appena trovato — senza un errore e senza un avviso.
    """
    from history_maker import download
    from history_maker.catalogo import Catalogo
    from history_maker.config import Config

    config = Config(
        comune="Torrebruna", termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"], escludi_contesto=[],
        anno_min=1809, anno_max=1900, tipologie=[],
        catalogo=tmp_path / "catalogo.json", immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte", trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )
    Catalogo(comune="Torrebruna", registri=[]).salva(config.catalogo)
    prima = config.catalogo.read_text(encoding="utf-8")
    firma = config.catalogo.stat().st_mtime_ns

    download.esegui(config)

    assert config.catalogo.read_text(encoding="utf-8") == prima
    assert config.catalogo.stat().st_mtime_ns == firma
