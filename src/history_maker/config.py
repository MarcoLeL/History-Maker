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
    modello: str = "claude-opus-5"
    lato_lungo_px: int = 1568
    # Il sovraccarico di Claude Code e' per invocazione, non per immagine:
    # raggruppare le pagine e' il modo per non sprecare la quota.
    pagine_per_chiamata: int = 4
    timeout_s: int = 900


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
            catalogo=_path("catalogo", "data/catalogo.json"),
            immagini=_path("immagini", "data/immagini"),
            ridotte=_path("ridotte", "data/immagini_ridotte"),
            trascrizioni=_path("trascrizioni", "data/trascrizioni"),
            dataset=_path("dataset", "data/dataset"),
            glossario=_path("glossario", "config/glossario-torrebruna.yaml"),
            rete=Rete(**(dati.get("rete") or {})),
            trascrizione=Trascrizione(**(dati.get("trascrizione") or {})),
        )
