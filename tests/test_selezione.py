"""Il filtro della raccolta: 1809-1900, Torrebruna si', Guardiabruna no.

E' la regola che il progetto deve azzeccare: Guardiabruna e' oggi una
frazione di Torrebruna, ma fino al 1928 era comune autonomo con registri
propri, e il portale la restituisce nella stessa ricerca.
"""

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
