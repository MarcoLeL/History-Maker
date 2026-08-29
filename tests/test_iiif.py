"""Il formato del Portale Antenati, collaudato su manifest campione."""

import pytest

from history_maker import iiif
from history_maker.errors import ManifestError


def test_manifest_url_dalla_pagina(html_galleria):
    url = iiif.estrai_manifest_url(html_galleria, "https://esempio")
    assert url == "https://dam-antenati.cultura.gov.it/antenati/containers/aBcDeFg/manifest"


def test_pagina_senza_manifest_fallisce():
    with pytest.raises(ManifestError):
        iiif.estrai_manifest_url("<html><body>niente</body></html>", "https://esempio")


def test_metadati(manifest_torrebruna):
    assert iiif.metadato(manifest_torrebruna, iiif.META_TITOLO) == "1866"
    assert iiif.metadato(manifest_torrebruna, iiif.META_TIPOLOGIA) == "Nati"
    assert "Torrebruna" in iiif.metadato(manifest_torrebruna, iiif.META_CONTESTO)


def test_metadato_multilingua():
    manifest = {"metadata": [{"label": "Titolo", "value": [{"@language": "it", "@value": "1871"}]}]}
    assert iiif.metadato(manifest, "Titolo") == "1871"


def test_metadato_assente_usa_il_default(manifest_torrebruna):
    assert iiif.metadato(manifest_torrebruna, "Inesistente", default="") == ""
    with pytest.raises(ManifestError):
        iiif.metadato(manifest_torrebruna, "Inesistente")


def test_canvas_e_immagini(manifest_torrebruna):
    pagine = iiif.canvases(manifest_torrebruna)
    assert len(pagine) == 2
    assert iiif.url_immagine(pagine[0]).endswith("/aa1/full/full/0/default.jpg")


def test_manifest_senza_canvas_fallisce():
    with pytest.raises(ManifestError):
        iiif.canvases({"sequences": [{"canvases": []}]})
    with pytest.raises(ManifestError):
        iiif.canvases({})


def test_riscrittura_dimensione():
    url = "https://iiif-antenati.cultura.gov.it/iiif/2/aa1/full/full/0/default.jpg"
    # /full/full/ e /full/max/ oggi danno 403: serve pct:100 per l'originale.
    assert iiif.con_dimensione(url) == (
        "https://iiif-antenati.cultura.gov.it/iiif/2/aa1/full/pct:100/0/default.jpg"
    )
    assert "/full/!2000,2000/0/" in iiif.con_dimensione(url, 2000)


@pytest.mark.parametrize(
    "url, ark",
    [
        ("https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x", "an_ua19944535"),
        ("https://antenati.cultura.gov.it/chi-siamo/", None),
    ],
)
def test_ark_id(url, ark):
    assert iiif.estrai_ark_id(url) == ark


def test_archive_id():
    url = "https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x"
    assert iiif.estrai_archive_id(url) == "19944535"
    with pytest.raises(ManifestError):
        iiif.estrai_archive_id("https://antenati.cultura.gov.it/")


@pytest.mark.parametrize(
    "titolo, anno",
    [("1866", 1866), ("1866-1870", 1866), ("Nati 1809", 1809), ("senza data", None)],
)
def test_anno_dal_titolo(titolo, anno):
    assert iiif.estrai_anno(titolo) == anno


def test_normalizzazione_accenti():
    assert iiif.normalizza("TORREBRUNÀ ") == "torrebruna"


def test_riconoscimento_url_manifest():
    assert iiif.is_manifest_url("https://dam-antenati.cultura.gov.it/x/manifest")
    assert not iiif.is_manifest_url("https://antenati.cultura.gov.it/ark:/12657/an_ua1/abc")
