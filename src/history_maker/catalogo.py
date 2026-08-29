"""Il catalogo: l'elenco dei registri trovati e il loro stato.

E' l'artefatto che collega le fasi. ``discover`` lo crea, ``download`` lo
legge e lo aggiorna, ``transcribe`` lo legge. E' un JSON leggibile a mano
di proposito: se la scoperta automatica sbaglia qualcosa, si corregge con
un editor di testo invece di ridiscutere il codice.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from slugify import slugify

from history_maker import iiif


@dataclass
class Registro:
    """Un'unita' archivistica: un registro di un anno e di una tipologia."""

    ark_url: str
    ark_id: str | None = None
    archive_id: str | None = None
    manifest_url: str | None = None

    # Metadati letti dal manifest (autorevoli, a differenza dell'HTML).
    contesto: str | None = None
    titolo: str | None = None
    tipologia: str | None = None
    anno: int | None = None

    n_immagini: int | None = None
    cartella: str | None = None
    # Testo grezzo raccolto dalla pagina dei risultati: serve solo a
    # rendere il catalogo leggibile prima che i manifest siano risolti.
    etichetta_ricerca: str | None = None
    note: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """Nome di cartella stabile e ordinabile per anno."""
        anno = f"{self.anno:04d}" if self.anno else "0000"
        pezzi = [anno, self.tipologia or "sconosciuta", self.archive_id or self.ark_id or "x"]
        return slugify("-".join(str(p) for p in pezzi))

    def applica_manifest(self, manifest: dict[str, Any]) -> None:
        """Riempie i metadati leggendoli dal manifest IIIF."""
        self.contesto = iiif.metadato(manifest, iiif.META_CONTESTO, default="")
        self.titolo = iiif.metadato(manifest, iiif.META_TITOLO, default="")
        self.tipologia = iiif.metadato(manifest, iiif.META_TIPOLOGIA, default="")
        self.anno = iiif.estrai_anno(self.titolo or "")
        self.n_immagini = len(iiif.canvases(manifest))


@dataclass
class Catalogo:
    comune: str
    registri: list[Registro] = field(default_factory=list)

    def salva(self, percorso: Path) -> None:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        dati = {"comune": self.comune, "registri": [asdict(r) for r in self.registri]}
        percorso.write_text(
            json.dumps(dati, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def carica(cls, percorso: Path) -> "Catalogo":
        dati = json.loads(Path(percorso).read_text(encoding="utf-8"))
        return cls(
            comune=dati.get("comune", ""),
            registri=[Registro(**r) for r in dati.get("registri", [])],
        )

    def unisci(self, nuovi: Iterable[Registro]) -> int:
        """Aggiunge i registri non ancora presenti; restituisce quanti ne ha aggiunti.

        L'identita' e' l'URL ark, cosi' rilanciare ``discover`` e'
        idempotente e non perde le correzioni fatte a mano.
        """
        noti = {r.ark_url for r in self.registri}
        aggiunti = 0
        for registro in nuovi:
            if registro.ark_url not in noti:
                self.registri.append(registro)
                noti.add(registro.ark_url)
                aggiunti += 1
        return aggiunti


def pertinente(registro: Registro, config) -> tuple[bool, str]:
    """Dice se il registro rientra nella raccolta, e perche' no in caso contrario.

    Il confronto avviene sul "Contesto archivistico" del manifest, che ha
    forma ``Stato civile italiano/Chieti/Torrebruna/...``. E' la sola
    fonte che distingua Torrebruna da Guardiabruna in modo affidabile:
    la pagina dei risultati mostra entrambe perche' Guardiabruna e' oggi
    una frazione di Torrebruna, ma fino al 1928 era comune autonomo e i
    suoi registri sono un fondo separato.
    """
    contesto = iiif.normalizza(registro.contesto or "")
    if not contesto:
        return False, "contesto archivistico assente (manifest non risolto)"

    for escluso in config.escludi_contesto:
        if iiif.normalizza(escluso) in contesto:
            return False, f"escluso: il contesto contiene '{escluso}'"

    if config.includi_contesto and not any(
        iiif.normalizza(incluso) in contesto for incluso in config.includi_contesto
    ):
        return False, "il contesto non nomina il comune richiesto"

    if registro.anno is None:
        return False, f"anno non ricavabile dal titolo '{registro.titolo}'"
    if not (config.anno_min <= registro.anno <= config.anno_max):
        return False, f"anno {registro.anno} fuori dall'intervallo richiesto"

    if config.tipologie:
        attese = {iiif.normalizza(t) for t in config.tipologie}
        if iiif.normalizza(registro.tipologia or "") not in attese:
            return False, f"tipologia '{registro.tipologia}' non richiesta"

    return True, "ok"
