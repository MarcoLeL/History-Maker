"""Fase 2 collaudata contro un finto portale HTTP locale.

Non tocca la rete esterna: monta un server che imita il comportamento del
Portale Antenati, compreso il 403 sulla sintassi ``/full/full/0/`` che
rende necessaria la riscrittura a ``pct:100``.
"""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from history_maker import discover, download
from history_maker.catalogo import Catalogo, Registro
from history_maker.config import Config

N_PAGINE = 5


def _manifest(base: str) -> dict:
    return {
        "metadata": [
            {"label": "Contesto archivistico", "value": "Chieti/Stato civile italiano/Torrebruna"},
            {"label": "Titolo", "value": "1866"},
            {"label": "Tipologia", "value": "Nati"},
        ],
        "sequences": [
            {
                "canvases": [
                    {
                        "@id": f"{base}/ark/12657/an_ua19944535/canvas/p{i}",
                        "label": f"{i:04d}",
                        "images": [
                            {
                                "resource": {
                                    "@id": f"{base}/iiif/2/img{i}/full/full/0/default.jpg",
                                    "format": "image/jpeg",
                                }
                            }
                        ],
                    }
                    for i in range(1, N_PAGINE + 1)
                ]
            }
        ],
    }


@pytest.fixture
def portale():
    """Server locale che imita manifest e immagini del portale."""
    stato = {"base": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.endswith("/manifest"):
                corpo = json.dumps(_manifest(stato["base"])).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif "/full/pct:100/0/" in self.path:
                buffer = io.BytesIO()
                Image.new("RGB", (200, 300), "white").save(buffer, "JPEG")
                corpo = buffer.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
            elif "/full/full/0/" in self.path or "/full/max/0/" in self.path:
                # Come il server vero: le sintassi legacy sono rifiutate.
                self.send_response(403)
                self.end_headers()
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    stato["base"] = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield stato["base"]
    server.shutdown()


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
def registro(portale) -> Registro:
    return Registro(
        ark_url=f"{portale}/ark:/12657/an_ua19944535/w9DWR8x",
        archive_id="19944535",
        manifest_url=f"{portale}/containers/aBcDeFg/manifest",
    )


def test_scarica_tutte_le_pagine(config, registro):
    esito = download.scarica_registro(registro, config, config.immagini)
    assert (esito.scaricate, esito.fallite) == (N_PAGINE, 0)

    cartella = config.immagini / registro.slug
    assert sorted(p.name for p in cartella.glob("*.jpg")) == [
        f"{i:04d}.jpg" for i in range(1, N_PAGINE + 1)
    ]
    # Il manifest viene conservato accanto alle immagini come prova d'origine.
    assert (cartella / "manifest.json").exists()
    # Nessun file troncato lasciato indietro.
    assert not list(cartella.glob("*.part"))


def test_rilanciare_il_download_riprende_senza_riscaricare(config, registro):
    download.scarica_registro(registro, config, config.immagini)
    esito = download.scarica_registro(registro, config, config.immagini)
    assert (esito.scaricate, esito.saltate) == (0, N_PAGINE)


def test_metadati_letti_dal_manifest_via_http(config, registro):
    catalogo = Catalogo(comune="Torrebruna", registri=[registro])
    discover.arricchisci_da_manifest(catalogo, config)

    assert registro.anno == 1866
    assert registro.tipologia == "Nati"
    assert registro.n_immagini == N_PAGINE
    assert "Torrebruna" in registro.contesto
    assert "1 registri trovati" in discover.riepilogo(catalogo, config)
    assert "immagini da scaricare: 5" in discover.riepilogo(catalogo, config)
