"""Piu' chiamate aperte insieme, senza forzare i limiti del piano.

Il parallelismo qui non serve a superare una quota: serve a riempire
l'ATTESA. Una chiamata da sei pagine impiega 88 secondi a generare, e per
ottantasette il processo non fa nulla — con un lavoratore solo si usa 0,7
del ritmo consentito.

Sono i test piu' importanti del progetto in proporzione alle righe che
coprono, perche' i guasti da concorrenza non falliscono in modo pulito:
capitano una volta su cento, sporcano un contatore, e si scoprono tre
giorni dopo quando la quota finisce a meta' pomeriggio senza motivo.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from history_maker import gemini, transcribe
from history_maker.backend import LimiteUsoRaggiunto, Risposta
from history_maker.catalogo import Catalogo, Registro
from history_maker.config import Config, Trascrizione


def _config(tmp_path, **trascrizione) -> Config:
    impostazioni = {
        "backend": "gemini",
        "modello": "finto",
        "lato_lungo_px": 200,
        "dividi_facciate": False,
        "ritaglia": False,
        "pagine_per_chiamata": 1,
    }
    impostazioni.update(trascrizione)
    return Config(
        comune="Torrebruna",
        termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"],
        escludi_contesto=[],
        anno_min=1809,
        anno_max=1900,
        tipologie=[],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
        glossario=tmp_path / "manca.yaml",
        trascrizione=Trascrizione(**impostazioni),
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866", tipologia="Nati", anno=1866, archive_id="19944535",
    )


def _pagine(config, registro, quante: int):
    Catalogo(comune="Torrebruna", registri=[registro]).salva(config.catalogo)
    cartella = config.immagini / registro.slug
    cartella.mkdir(parents=True, exist_ok=True)
    for i in range(1, quante + 1):
        Image.new("RGB", (120, 160), "white").save(cartella / f"{i:04d}.jpg", "JPEG")
    return transcribe.pagine_da_trascrivere(config)


class MotoreLento:
    """Un backend finto che impiega tempo e misura la concorrenza vera."""

    nome = "finto"
    modo_immagini = "allegate"

    def __init__(self, durata: float = 0.05, fallisci_dopo: int | None = None):
        self.durata = durata
        self.fallisci_dopo = fallisci_dopo
        self.chiamate = 0
        self.insieme = 0
        self.massimo_insieme = 0
        self._serratura = threading.Lock()

    def verifica(self) -> None:
        return None

    def riferimento_immagine(self, percorso, indice):
        return f"immagine {indice + 1} ({percorso.name})"

    def esegui(self, richiesta):
        with self._serratura:
            self.chiamate += 1
            mia = self.chiamate
            self.insieme += 1
            self.massimo_insieme = max(self.massimo_insieme, self.insieme)
        try:
            time.sleep(self.durata)
            if self.fallisci_dopo is not None and mia > self.fallisci_dopo:
                raise LimiteUsoRaggiunto("quota finita")
            nomi = [Path(p).name for p in richiesta.immagini]
            return Risposta(
                ok=True,
                testo=json.dumps(
                    [{"file": n, "tipo_pagina": "atti", "atti": []} for n in nomi]
                ),
                token_contesto=100,
                token_output=50,
            )
        finally:
            with self._serratura:
                self.insieme -= 1


# --- che il lavoro esca giusto ---------------------------------------------


def test_in_parallelo_esce_lo_stesso_risultato_che_in_sequenza(tmp_path, registro):
    """Il parallelismo deve cambiare quando le pagine sono pronte, non quali."""
    risultati = {}
    for nome, parallele in [("sequenza", 1), ("parallelo", 4)]:
        config = _config(tmp_path / nome, chiamate_parallele=parallele)
        pagine = _pagine(config, registro, 8)
        gruppi = [[p] for p in pagine]
        if parallele > 1:
            esito = transcribe._esegui_in_parallelo(
                config, gruppi, MotoreLento(), False, parallele
            )
        else:
            esito = transcribe._esegui_in_sequenza(config, gruppi, MotoreLento(), False)
        risultati[nome] = (
            esito.trascritte,
            sorted(p.name for p in (config.trascrizioni / registro.slug).iterdir()),
        )

    assert risultati["sequenza"] == risultati["parallelo"]
    assert risultati["parallelo"][0] == 8


def test_le_chiamate_girano_davvero_insieme(tmp_path, registro):
    """Senza questa prova, un parallelismo rotto passerebbe per lento."""
    config = _config(tmp_path, chiamate_parallele=4)
    pagine = _pagine(config, registro, 8)
    motore = MotoreLento(durata=0.1)

    transcribe._esegui_in_parallelo(config, [[p] for p in pagine], motore, False, 4)

    assert motore.massimo_insieme > 1, "nessuna sovrapposizione: gira ancora in sequenza"
    assert motore.massimo_insieme <= 4


def test_i_conteggi_non_si_perdono_per_strada(tmp_path, registro):
    """Otto gruppi da una pagina fanno otto chiamate e otto pagine, sempre."""
    config = _config(tmp_path, chiamate_parallele=4)
    pagine = _pagine(config, registro, 8)
    motore = MotoreLento()

    esito = transcribe._esegui_in_parallelo(config, [[p] for p in pagine], motore, False, 4)

    assert esito.trascritte == 8
    assert esito.chiamate == 8
    assert esito.token_contesto == 800  # 100 per chiamata, nessuno perso
    assert esito.token_output == 400


def test_la_quota_finita_ferma_la_squadra_senza_perdere_il_fatto(tmp_path, registro):
    """Chi e' gia' partito finisce; il lavoro nuovo non parte piu'."""
    config = _config(tmp_path, chiamate_parallele=3)
    pagine = _pagine(config, registro, 12)
    motore = MotoreLento(fallisci_dopo=4)

    esito = transcribe._esegui_in_parallelo(config, [[p] for p in pagine], motore, False, 3)

    assert esito.quota_esaurita
    assert 0 < esito.trascritte < 12
    # E cio' che risulta trascritto e' su disco, non solo contato.
    su_disco = list((config.trascrizioni / registro.slug).iterdir())
    assert len(su_disco) == esito.trascritte


# --- il ritmo, che resta quello consentito a uno solo -----------------------


def test_il_ritmo_e_condiviso_fra_i_lavoratori(tmp_path):
    """Il limite al minuto vale per la squadra, non per lavoratore.

    Senza serratura sul turno quattro lavoratori partirebbero insieme e
    farebbero quattro volte il ritmo consentito — che e' esattamente il
    modo di prendersi una raffica di 429 credendo di essere in regola.
    """
    ritmo = gemini.Ritmo(al_minuto=600, al_giorno=0, registro=tmp_path / "q.json")
    partenze = []

    def parti():
        ritmo.attendi_turno()
        partenze.append(time.monotonic())

    inizio = time.monotonic()
    fili = [threading.Thread(target=parti) for _ in range(4)]
    for filo in fili:
        filo.start()
    for filo in fili:
        filo.join()

    assert len(partenze) == 4
    # 600 al minuto = 0,1 s fra una partenza e l'altra, piu' il margine.
    assert max(partenze) - inizio >= 3 * ritmo.intervallo_s * 0.9


def test_il_contatore_giornaliero_non_perde_colpi(tmp_path):
    """Leggi-modifica-scrivi su file: senza serratura se ne perdono."""
    registro = tmp_path / "q.json"
    ritmo = gemini.Ritmo(al_minuto=0, al_giorno=1000, registro=registro)

    fili = [threading.Thread(target=ritmo.segna) for _ in range(30)]
    for filo in fili:
        filo.start()
    for filo in fili:
        filo.join()

    assert ritmo.fatte_oggi() == 30


def test_il_tetto_di_token_al_minuto_rallenta_le_partenze(tmp_path):
    """Con molte pagine per chiamata e' il TPM a scandire, non l'RPM."""
    stretto = gemini.Ritmo(
        al_minuto=1000,
        al_giorno=0,
        registro=tmp_path / "q.json",
        token_al_minuto=100_000,
        token_per_chiamata=50_000,
    )
    largo = gemini.Ritmo(al_minuto=1000, al_giorno=0, registro=tmp_path / "q2.json")

    assert stretto.intervallo_s > largo.intervallo_s
    # Meta' del budget di token per chiamata = due chiamate al minuto.
    assert stretto.intervallo_s == pytest.approx(60 / 2 * 1.1, rel=0.01)


# --- nomi che si ripetono fra registri diversi -----------------------------


def test_pagine_omonime_di_registri_diversi_non_si_confondono(tmp_path):
    """Il guasto che ha fatto fallire 37 pagine su 60.

    Le pagine si chiamano '0001-pag-1.jpg' dentro OGNI registro: sono
    uniche solo li'. Una chiamata da dodici pagine attraversa dodici
    registri 'Diversi' da una-due pagine, e lo stesso nome compare cinque
    volte. Cercando per nome nudo, la prima pagina si prende l'oggetto e
    le altre quattro non trovano piu' nulla.
    """
    registri = [
        Registro(ark_url=f"https://x/{a}", contesto="Chieti/Torrebruna",
                 titolo=str(a), tipologia="Diversi", anno=a, archive_id=str(a))
        for a in (1818, 1820, 1822)
    ]
    pagine, immagini = [], []
    for r in registri:
        cartella = tmp_path / r.slug
        cartella.mkdir(parents=True, exist_ok=True)
        percorso = cartella / "0001-pag-1.jpg"
        percorso.write_bytes(b"x")
        pagine.append(transcribe.Pagina(percorso, r))
        immagini.append([percorso])

    # Il modello risponde con i nomi qualificati dal registro.
    risposta = [
        {"file": f"{r.slug}/0001-pag-1.jpg", "anno_indicato": str(r.anno)} for r in registri
    ]
    allineate = transcribe._allinea(risposta, pagine, immagini)
    assert [v["anno_indicato"] for v in allineate] == ["1818", "1820", "1822"]


def test_con_nomi_nudi_ambigui_si_ripiega_sull_ordine(tmp_path):
    """Se il modello ignora la qualificazione, resta l'ordine.

    E' l'unica informazione rimasta, ed e' buona: i modelli rispondono
    nell'ordine in cui hanno ricevuto le immagini. Meglio dell'alternativa
    precedente, che era perdere le pagine.
    """
    registri = [
        Registro(ark_url=f"https://x/{a}", contesto="Chieti/Torrebruna",
                 titolo=str(a), tipologia="Diversi", anno=a, archive_id=str(a))
        for a in (1818, 1820, 1822)
    ]
    pagine, immagini = [], []
    for r in registri:
        cartella = tmp_path / r.slug
        cartella.mkdir(parents=True, exist_ok=True)
        percorso = cartella / "0001-pag-1.jpg"
        percorso.write_bytes(b"x")
        pagine.append(transcribe.Pagina(percorso, r))
        immagini.append([percorso])

    risposta = [{"file": "0001-pag-1.jpg", "anno_indicato": str(r.anno)} for r in registri]
    allineate = transcribe._allinea(risposta, pagine, immagini)

    assert [v["anno_indicato"] for v in allineate] == ["1818", "1820", "1822"]
    assert all(v is not None for v in allineate), "nessuna pagina deve restare senza risposta"


def test_i_nomi_restano_il_criterio_forte_quando_distinguono(tmp_path):
    """Con nomi univoci, una risposta fuori ordine si rimette a posto."""
    r = Registro(ark_url="https://x/1", contesto="Chieti/Torrebruna",
                 titolo="1866", tipologia="Nati", anno=1866, archive_id="1")
    cartella = tmp_path / r.slug
    cartella.mkdir(parents=True, exist_ok=True)
    pagine, immagini = [], []
    for n in ("0001-pag-1.jpg", "0002-pag-2.jpg", "0003-pag-3.jpg"):
        p = cartella / n
        p.write_bytes(b"x")
        pagine.append(transcribe.Pagina(p, r))
        immagini.append([p])

    risposta = [
        {"file": f"{r.slug}/0003-pag-3.jpg", "anno_indicato": "terza"},
        {"file": f"{r.slug}/0001-pag-1.jpg", "anno_indicato": "prima"},
        {"file": f"{r.slug}/0002-pag-2.jpg", "anno_indicato": "seconda"},
    ]
    allineate = transcribe._allinea(risposta, pagine, immagini)
    assert [v["anno_indicato"] for v in allineate] == ["prima", "seconda", "terza"]


# --- il ritmo impara quanto costa davvero una chiamata ---------------------


def test_il_ritmo_si_adatta_ai_token_veri(tmp_path):
    """Una costante scritta a mano invecchia col modello.

    La stima iniziale — ~2.150 token in uscita per pagina — era misurata
    sul Flash con la trascrizione diplomatica; il Flash Lite ne produce
    372. Il ritmo calcolato su quel numero teneva il lavoro a 3,9 chiamate
    al minuto dove ne consentiva 6,4: meta' della velocita', per una stima
    sbagliata in eccesso.
    """
    ritmo = gemini.Ritmo(
        al_minuto=15, al_giorno=0, registro=tmp_path / "q.json",
        token_al_minuto=250_000, token_per_chiamata=58_200,   # la stima sbagliata
    )
    prima = ritmo.intervallo_s

    for _ in range(5):
        ritmo.osserva(35_288)      # quello che costa davvero

    assert ritmo.token_osservati == 35_288
    assert ritmo.intervallo_s < prima, "il ritmo non ha imparato"
    # E resta comunque sopra il minimo imposto dalle richieste al minuto.
    assert ritmo.intervallo_s >= 60 / 15


def test_servono_alcune_chiamate_prima_di_fidarsi(tmp_path):
    """Una sola risposta anomala non deve spostare il ritmo."""
    ritmo = gemini.Ritmo(al_minuto=15, al_giorno=0, registro=tmp_path / "q.json",
                         token_al_minuto=250_000, token_per_chiamata=58_200)
    ritmo.osserva(1_000)
    assert ritmo.token_osservati == 0, "si fida di una misura sola"
    assert ritmo.intervallo_s == pytest.approx(60 * 58_200 / 250_000 * 1.1)


def test_una_misura_vuota_non_sporca_la_media(tmp_path):
    ritmo = gemini.Ritmo(al_minuto=0, al_giorno=0, registro=tmp_path / "q.json")
    for _ in range(5):
        ritmo.osserva(0)
    assert ritmo.token_osservati == 0


def test_se_i_token_crescono_il_ritmo_rallenta(tmp_path):
    """Vale in entrambe le direzioni: e' una misura, non uno sconto."""
    ritmo = gemini.Ritmo(al_minuto=1000, al_giorno=0, registro=tmp_path / "q.json",
                         token_al_minuto=250_000, token_per_chiamata=1_000)
    lento = gemini.Ritmo(al_minuto=1000, al_giorno=0, registro=tmp_path / "q2.json",
                         token_al_minuto=250_000, token_per_chiamata=1_000)
    for _ in range(5):
        lento.osserva(200_000)
    assert lento.intervallo_s > ritmo.intervallo_s
