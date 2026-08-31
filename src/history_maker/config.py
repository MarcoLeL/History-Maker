"""Caricamento della configurazione YAML della raccolta."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RADICE = Path(__file__).resolve().parents[2]
CONFIG_DEFAULT = RADICE / "config" / "torrebruna.yaml"


@dataclass(frozen=True)
class Rete:
    download_paralleli: int = 3
    pausa_tra_richieste_s: float = 0.4
    timeout_s: int = 120
    tentativi: int = 5


@dataclass(frozen=True)
class Trascrizione:
    # Chi legge le pagine: 'gemini' (API di Google AI Studio, piano
    # gratuito) o 'claude-code' (abbonamento Claude Pro). Il modello qui
    # sotto deve appartenere al backend scelto.
    backend: str = "gemini"
    modello: str = "gemini-3.6-flash"
    # Ritagliare l'area scritta prima di ridurre. Si puo' spegnere per
    # confrontare le due letture sulle stesse pagine.
    ritaglia: bool = True
    # Dividere la scansione nelle sue due facciate e mandarle come due
    # immagini. E' l'intervento che alza di piu' l'accuratezza: una doppia
    # pagina ridotta intera fa arrivare ogni facciata a ~780 px di
    # larghezza, divisa a ~1100. Costa il doppio delle immagini, quindi
    # con pagine_per_chiamata alto puo' valere la pena abbassarlo.
    dividi_facciate: bool = True
    lato_lungo_px: int = 1568
    # Qualita' JPEG delle copie. Sotto 80 cominciano gli artefatti sui
    # tratti sottili, che e' esattamente quello che il modello deve
    # leggere; sopra 90 il file cresce senza aggiungere niente. Conta piu'
    # di quanto sembri: le immagini viaggiano dentro la richiesta, e una
    # richiesta troppo grande non parte.
    qualita_jpeg: int = 85
    # Pagine per chiamata. Con Claude Code ammortizza le ~50.000 token di
    # impalcatura per invocazione; con Gemini, dove quel sovraccarico non
    # c'e', decide quante pagine entrano in una delle richieste
    # giornaliere del piano gratuito.
    pagine_per_chiamata: int = 10
    timeout_s: int = 900

    # Quante chiamate tenere aperte insieme. Il vincolo vero della fase 3
    # non e' la quota ma il tempo di generazione — ~15 s a pagina — e un
    # solo lavoratore usa un ventesimo del ritmo consentito. Le richieste
    # partono comunque una alla volta, scandite da :class:`gemini.Ritmo`:
    # il parallelismo riempie l'attesa, non forza il limite.
    chiamate_parallele: int = 1

    # Il ritmo consentito dal piano, da leggere su
    # aistudio.google.com/rate-limit: dipendono dal modello e cambiano
    # nel tempo. I default sono quelli dei Flash (20 richieste al
    # giorno); i Flash Lite ne danno 500. Zero disattiva il controllo.
    richieste_al_minuto: int = 5
    richieste_al_giorno: int = 20
    # Token al minuto. E' il terzo limite del piano, quello che mette il
    # tetto a 'pagine_per_chiamata': con le facciate divise una pagina
    # sono ~2.700 token in ingresso, quindi oltre le otto pagine per
    # chiamata si sfora prima di esaurire la quota giornaliera.
    token_al_minuto: int = 250_000

    # Tetto ai token generati in una risposta. Comprende i token di
    # ragionamento, quindi tenerlo alto non e' spreco: un JSON troncato a
    # meta' e' una chiamata buttata, e sul piano gratuito le chiamate sono
    # la risorsa scarsa.
    token_massimi: int = 65536
    # Ragionamento del modello: -1 lascia decidere lui, 0 lo spegne, un
    # numero positivo lo limita. Sulle grafie difficili aiuta; sulle
    # pagine bianche e' tempo buttato.
    token_ragionamento: int = -1

    # La trascrizione diplomatica integrale dell'atto. E' un terzo dei
    # token prodotti — misurato sulle prime 86 pagine — e serve alla
    # ricerca storica, non alla ricostruzione delle parentele: chi punta
    # solo all'albero genealogico puo' spegnerla e trascrivere una meta'
    # in piu' di pagine a parita' di quota.
    testo_integrale: bool = True


@dataclass(frozen=True)
class Config:
    comune: str
    termine_ricerca: str
    includi_contesto: list[str]
    escludi_contesto: list[str]
    anno_min: int
    anno_max: int
    tipologie: list[str]
    catalogo: Path
    immagini: Path
    ridotte: Path
    trascrizioni: Path
    dataset: Path
    # Tipologie da lasciare fuori anche quando 'tipologie' e' vuota e
    # quindi tutto il resto entra. L'esclusione ha la precedenza.
    escludi_tipologie: list[str] = field(default_factory=list)
    # Un'esclusione per tipologia vale finche' di quell'anno resta
    # qualcos'altro dello stesso tipo. Quando non resta nulla — a
    # Torrebruna succede per i matrimoni del 1870-1875 e del 1888, dove
    # sopravvivono solo le pubblicazioni — il registro escluso rientra:
    # meglio un doppione che non c'e' che sei anni cancellati da una riga
    # di configurazione.
    recupera_serie_interrotta: bool = True
    # Separatore dei CSV. Excel su Windows italiano usa il punto e virgola
    # come separatore di elenco: aprendo un file separato da virgole mette
    # l'intera riga in una colonna sola. Chi legge i CSV con pandas o R
    # passa sep=';' una volta e non ci pensa piu'; chi li apre con un
    # doppio clic, altrimenti, non li vede proprio.
    csv_separatore: str = ";"
    # Le forme attestate del paese. Un progetto nuovo non ne ha ancora,
    # e un file assente vale come glossario vuoto: la conoscenza locale
    # si accumula strada facendo, non e' un prerequisito per partire.
    glossario: Path = field(
        default_factory=lambda: RADICE / "config" / "glossario-torrebruna.yaml"
    )
    rete: Rete = field(default_factory=Rete)
    trascrizione: Trascrizione = field(default_factory=Trascrizione)

    @classmethod
    def carica(cls, percorso: Path | str | None = None) -> "Config":
        percorso = Path(percorso) if percorso else CONFIG_DEFAULT
        dati: dict[str, Any] = yaml.safe_load(percorso.read_text(encoding="utf-8")) or {}
        paths = dati.get("paths", {})

        def _path(chiave: str, default: str) -> Path:
            valore = Path(paths.get(chiave, default))
            return valore if valore.is_absolute() else RADICE / valore

        return cls(
            comune=dati.get("comune", "Torrebruna"),
            termine_ricerca=dati.get("termine_ricerca", dati.get("comune", "Torrebruna")),
            includi_contesto=list(dati.get("includi_contesto") or [dati.get("comune", "Torrebruna")]),
            escludi_contesto=list(dati.get("escludi_contesto") or []),
            anno_min=int(dati.get("anno_min", 1809)),
            anno_max=int(dati.get("anno_max", 1900)),
            tipologie=list(dati.get("tipologie") or []),
            escludi_tipologie=list(dati.get("escludi_tipologie") or []),
            csv_separatore=str(dati.get("csv_separatore", ";")),
            recupera_serie_interrotta=bool(dati.get("recupera_serie_interrotta", True)),
            catalogo=_path("catalogo", "data/catalogo.json"),
            immagini=_path("immagini", "data/immagini"),
            ridotte=_path("ridotte", "data/immagini_ridotte"),
            trascrizioni=_path("trascrizioni", "data/trascrizioni"),
            dataset=_path("dataset", "data/dataset"),
            glossario=_path("glossario", "config/glossario-torrebruna.yaml"),
            rete=Rete(**(dati.get("rete") or {})),
            trascrizione=Trascrizione(**(dati.get("trascrizione") or {})),
        )
