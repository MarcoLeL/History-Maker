"""L'applicazione per navigare l'albero genealogico.

Un server della libreria standard e una pagina che disegna l'albero da
se'. Nessuna dipendenza in piu' rispetto a quelle che il progetto ha
gia': parte in un istante, non chiede di installare niente, e resta
utilizzabile fra dieci anni — cosa che per un archivio storico non e' un
dettaglio.

Perche' non Streamlit, che sarebbe la scelta ovvia. Streamlit riesegue
l'intero script a ogni interazione: su un albero di qualche centinaio di
nodi ogni clic sarebbe una ricostruzione completa, e soprattutto non c'e'
modo di disegnare un grafo navigabile — con i coniugi accostati, i figli
appesi alla coppia e l'espansione su un nodo qualsiasi — dentro i suoi
componenti. Qui la pagina tiene il grafo in memoria e chiede al server
solo i dati che le mancano, quindi spostarsi da una persona all'altra
costa una richiesta di pochi kilobyte.

Il server e' in **sola lettura** e ascolta solo su ``localhost``: e' uno
strumento da tavolo, non un sito pubblicato.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

from history_maker import albero
from history_maker.config import Config

logger = logging.getLogger(__name__)

STATICI = Path(__file__).parent / "statico"


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Gestore(SimpleHTTPRequestHandler):
    """Le poche rotte che servono. Tutto il resto e' un file statico."""

    def __init__(self, *args, conn, immagini, ridotte, **kwargs):
        self.conn = conn
        self.immagini = immagini
        self.ridotte = ridotte
        super().__init__(*args, directory=str(STATICI), **kwargs)

    def log_message(self, formato, *argomenti):  # noqa: N802 (nome della libreria)
        logger.debug(formato, *argomenti)

    def do_GET(self):  # noqa: N802 (nome della libreria)
        indirizzo = urlparse(self.path)
        percorso = unquote(indirizzo.path)
        parametri = parse_qs(indirizzo.query)

        try:
            if percorso.startswith("/api/"):
                return self._api(percorso[5:], parametri)
            if percorso.startswith("/immagine/"):
                return self._immagine(percorso[len("/immagine/"):])
        except Exception:
            logger.exception("errore servendo %s", percorso)
            return self._json({"errore": "richiesta non riuscita"}, stato=500)

        if percorso == "/":
            self.path = "/index.html"
        return super().do_GET()

    # -- le rotte ---------------------------------------------------------

    def _api(self, rotta: str, parametri: dict) -> None:
        if rotta == "statistiche":
            return self._json(albero.statistiche(self.conn))

        if rotta == "cerca":
            testo = (parametri.get("q") or [""])[0]
            return self._json(albero.cerca(self.conn, testo))

        if rotta.startswith("persona/"):
            scheda = albero.scheda(self.conn, int(rotta.split("/")[1]))
            if scheda is None:
                return self._json({"errore": "persona non trovata"}, stato=404)
            return self._json(scheda)

        if rotta.startswith("albero/"):
            individuo = int(rotta.split("/")[1])
            su = int((parametri.get("su") or ["3"])[0])
            giu = int((parametri.get("giu") or ["3"])[0])
            return self._json(albero.albero(self.conn, individuo, su, giu))

        return self._json({"errore": "rotta sconosciuta"}, stato=404)

    def _immagine(self, relativo: str) -> None:
        """La pagina di registro da cui viene un atto.

        Si preferisce la copia ridotta: e' quella che ha letto il modello,
        pesa una frazione dell'originale e per guardare una grafia basta
        e avanza. L'originale a piena risoluzione resta il ripiego.
        """
        relativo = relativo.lstrip("/")
        for radice in (self.ridotte, self.immagini):
            if radice is None:
                continue
            candidato = (radice / relativo).resolve()
            # Nessun percorso puo' uscire dalle cartelle delle immagini.
            if not str(candidato).startswith(str(radice.resolve())):
                return self._json({"errore": "percorso non ammesso"}, stato=403)
            if candidato.is_file():
                dati = candidato.read_bytes()
                tipo = mimetypes.guess_type(candidato.name)[0] or "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(dati)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(dati)
                return
        return self._json({"errore": "immagine non trovata"}, stato=404)

    def _json(self, dati, stato: int = 200) -> None:
        corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
        self.send_response(stato)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def avvia(config: Config, porta: int = 8000, apri: bool = True) -> int:
    percorso = config.dataset / "torrebruna.sqlite"
    if not percorso.exists():
        print(f"Manca {percorso}. Prima: python -m history_maker dataset")
        return 1

    conn = albero.apri(percorso)
    try:
        conn.execute("SELECT 1 FROM individui LIMIT 1")
    except Exception:
        print(
            "L'albero non e' ancora stato costruito.\n"
            "Prima: python -m history_maker genealogia"
        )
        return 1

    numeri = albero.statistiche(conn)
    gestore = partial(
        Gestore,
        conn=conn,
        immagini=Path(config.immagini) if config.immagini else None,
        ridotte=Path(config.ridotte) if config.ridotte else None,
    )

    server = Server(("127.0.0.1", porta), gestore)
    indirizzo = f"http://127.0.0.1:{porta}/"
    print(f"L'albero di {config.comune}: {numeri['individui_albero']} persone, "
          f"{numeri['legami']} legami, {numeri['unioni']} coppie "
          f"({numeri['anno_min']}-{numeri['anno_max']})")
    print(f"Aperto su {indirizzo}   (Ctrl+C per chiudere)")

    if apri:
        threading.Timer(0.5, lambda: webbrowser.open(indirizzo)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChiuso.")
    finally:
        server.server_close()
        conn.close()
    return 0
