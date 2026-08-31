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
    def anno_fine(self) -> int | None:
        """Anno di chiusura, per i registri che ne coprono piu' d'uno.

        Derivata dal titolo invece che salvata: un catalogo scritto prima
        che questa distinzione esistesse resta corretto senza migrazioni,
        e non c'e' modo che il campo diverghi dal titolo da cui viene.
        """
        return iiif.estrai_anno_fine(self.titolo or "")

    @property
    def anni_coperti(self) -> range:
        """Gli anni che questo registro contiene, estremi compresi.

        Quasi sempre uno solo. I registri biennali — a Torrebruna il
        ``1813-1814`` — ne coprono due, e trattarli come se ne coprissero
        uno fa comparire lacune inesistenti nella serie.
        """
        if self.anno is None:
            return range(0)
        return range(self.anno, (self.anno_fine or self.anno) + 1)

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
    # Un registro biennale entra se ANCHE SOLO UNO dei suoi anni rientra:
    # il '1813-1814' vale per chi chiede il 1814 quanto per chi chiede il
    # 1813, e scartarlo perche' "comincia" prima perderebbe l'anno buono.
    if not any(config.anno_min <= a <= config.anno_max for a in registro.anni_coperti):
        return False, f"anni {registro.titolo} fuori dall'intervallo richiesto"

    if config.tipologie:
        attese = {iiif.normalizza(t) for t in config.tipologie}
        if iiif.normalizza(registro.tipologia or "") not in attese:
            return False, f"tipologia '{registro.tipologia}' non richiesta"

    # L'esclusione ha la precedenza sull'inclusione, come per il contesto:
    # una tipologia elencata qui resta fuori anche se 'tipologie' e' vuota
    # e quindi tutto il resto entra.
    tipologia = iiif.normalizza(registro.tipologia or "")
    for esclusa in config.escludi_tipologie:
        if iiif.normalizza(esclusa) in tipologia:
            return False, f"tipologia '{registro.tipologia}' esclusa ('{esclusa}')"

    return True, "ok"


# --- la selezione, che non deve aprire buchi nella serie --------------------


def tipo_base(registro: Registro) -> str:
    """La tipologia senza le sue specificazioni.

    ``Matrimoni, pubblicazioni`` e ``Matrimoni, processetti`` sono
    entrambi ``matrimoni``: e' il livello a cui ha senso chiedersi se di
    quell'anno resti qualcosa.
    """
    return iiif.normalizza((registro.tipologia or "").split(",")[0])


def _escluso_solo_per_tipologia(registro: Registro, config) -> bool:
    """Vero se l'unica ragione dell'esclusione e' la tipologia.

    Un registro di Guardiabruna, o di un anno fuori intervallo, non si
    recupera mai: quello e' un altro comune o un'altra raccolta. Una
    tipologia esclusa invece e' una scelta di merito — "non voglio le
    pubblicazioni perche' duplicano gli atti" — e una scelta di merito
    smette di valere quando l'atto che duplicherebbe non esiste.
    """
    from dataclasses import replace

    return pertinente(registro, replace(config, escludi_tipologie=[]))[0]


def selezione(catalogo: "Catalogo", config) -> list[Registro]:
    """I registri da lavorare, con le esclusioni ma senza i buchi che aprono.

    **Il caso che ha imposto questa funzione.** Escludere ``Matrimoni,
    pubblicazioni`` e' giusto: la pubblicazione e' la promessa di
    matrimonio e nomina gli stessi sposi e gli stessi genitori dell'atto,
    che nello stesso anno c'e' gia'. Ma a Torrebruna, per il 1870, 1871,
    1873, 1874, 1875 e 1888, il registro dei matrimoni **non esiste**: e'
    andato perduto, o non e' mai stato versato. Li' la pubblicazione non
    duplica niente — e' l'unica traccia rimasta di chi si e' sposato.

    Quindi un'esclusione per tipologia vale finche' resta qualcos'altro di
    quell'anno e di quel tipo. Quando non resta nulla, il registro escluso
    rientra: meglio un doppione che non c'e' che sei anni di matrimoni
    cancellati da una riga di configurazione.
    """
    tenuti = [r for r in catalogo.registri if pertinente(r, config)[0]]
    if not getattr(config, "recupera_serie_interrotta", True):
        return tenuti

    coperti = {(a, tipo_base(r)) for r in tenuti for a in r.anni_coperti}
    for registro in catalogo.registri:
        if pertinente(registro, config)[0]:
            continue
        if not _escluso_solo_per_tipologia(registro, config):
            continue
        # Si recupera solo se NESSUNO dei suoi anni e' gia' coperto: un
        # registro biennale che colma anche un solo anno vale la pena.
        if all((a, tipo_base(registro)) in coperti for a in registro.anni_coperti):
            continue
        registro.note.append(
            f"recuperato: per il {registro.anno} non esiste nessun altro "
            f"registro di tipo '{tipo_base(registro)}'"
        )
        tenuti.append(registro)
    return tenuti


def recuperati(catalogo: "Catalogo", config) -> list[Registro]:
    """I soli registri rientrati perche' erano l'ultima fonte del loro anno."""
    return [r for r in selezione(catalogo, config) if not pertinente(r, config)[0]]
