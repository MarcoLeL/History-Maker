"""Fase 3: trascrizione delle immagini con Claude Code.

Usa l'abbonamento Claude Pro tramite ``claude -p``, non il credito API.
Tre conseguenze governano il modulo:

**Le pagine vanno a gruppi.** Il sovraccarico di Claude Code (prompt di
sistema e definizioni degli strumenti, ~50.000 token) e' per invocazione,
non per immagine. A quattro pagine per chiamata il costo scende a circa
20.000 token a pagina invece di 50.000.

**La quota si esaurisce.** Quando succede il lavoro non fallisce: si
ferma pulito, oppure aspetta e riprende se glielo si chiede. Ogni pagina
finita e' salvata subito, quindi rilanciare riprende sempre da dove si
era arrivati.

**La risposta va validata.** Senza ``output_config.format`` la
conformita' allo schema non e' garantita, per cui ogni pagina passa da
``schema.valida_pagina`` e un gruppo che non si lascia interpretare viene
ritentato una pagina per volta.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from history_maker import claudecode, glossario
from history_maker.catalogo import Catalogo, Registro, pertinente
from history_maker.config import Config
from history_maker.prompt import (
    ISTRUZIONE_GRUPPO,
    SCHEMA_A_PAROLE,
    SISTEMA,
    descrivi_forme_note,
    descrivi_pagina,
)
from history_maker.schema import PaginaNonValida, valida_pagina

logger = logging.getLogger(__name__)

ESTENSIONI_IMMAGINE = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Quanto aspettare quando la quota e' esaurita e si e' scelto di attendere.
# I limiti dell'abbonamento si rinnovano su finestre di alcune ore, quindi
# ricontrollare ogni quarto d'ora e' abbastanza spesso da non perdere
# tempo e abbastanza raro da non tempestare la CLI.
ATTESA_QUOTA_S = 900


@dataclass
class Pagina:
    """Una pagina da trascrivere, con il contesto del registro che la contiene."""

    percorso: Path
    registro: Registro

    @property
    def id_richiesta(self) -> str:
        return f"{self.registro.slug}--{self.percorso.stem}"

    @property
    def destinazione_relativa(self) -> Path:
        return Path(self.registro.slug) / f"{self.percorso.stem}.json"


@dataclass
class Esito:
    trascritte: int = 0
    fallite: int = 0
    chiamate: int = 0
    token_contesto: int = 0
    quota_esaurita: bool = False


def pagine_da_trascrivere(
    config: Config,
    solo_mancanti: bool = True,
    dal: int | None = None,
    al: int | None = None,
) -> list[Pagina]:
    """Le pagine scaricate che rientrano nella raccolta."""
    catalogo = Catalogo.carica(config.catalogo)
    pagine: list[Pagina] = []
    for registro in catalogo.registri:
        if not pertinente(registro, config)[0]:
            continue
        if dal is not None and (registro.anno or 0) < dal:
            continue
        if al is not None and (registro.anno or 0) > al:
            continue
        cartella = config.immagini / registro.slug
        if not cartella.is_dir():
            continue
        for immagine in sorted(cartella.iterdir()):
            if immagine.suffix.lower() not in ESTENSIONI_IMMAGINE:
                continue
            pagina = Pagina(percorso=immagine, registro=registro)
            if solo_mancanti and (config.trascrizioni / pagina.destinazione_relativa).exists():
                continue
            pagine.append(pagina)
    return pagine


def prepara_immagine(pagina: Pagina, config: Config) -> Path:
    """Scrive una copia ridotta della pagina e ne restituisce il percorso.

    Claude Code legge il file dal disco, quindi il modo di controllare
    quanto contesto consuma un'immagine e' ridimensionarla prima. Le copie
    stanno in una cartella a parte: gli originali a piena risoluzione
    restano intatti, ed e' su quelli che si rilegge un atto dubbio.
    """
    lato = config.trascrizione.lato_lungo_px
    destinazione = config.ridotte / pagina.registro.slug / f"{pagina.percorso.stem}.jpg"
    if destinazione.exists():
        return destinazione
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(pagina.percorso) as immagine:
        immagine = immagine.convert("RGB")
        if max(immagine.size) > lato:
            fattore = lato / max(immagine.size)
            immagine = immagine.resize(
                (round(immagine.width * fattore), round(immagine.height * fattore)),
                Image.LANCZOS,
            )
        immagine.save(destinazione, "JPEG", quality=90, optimize=True)
    return destinazione


@lru_cache(maxsize=4)
def _forme_note_da(percorso: Path) -> str:
    """Il blocco di forme attestate, letto una volta sola.

    La trascrizione di un secolo sono migliaia di invocazioni: rileggere
    il glossario da disco a ogni gruppo di quattro pagine sarebbe uno
    spreco silenzioso.
    """
    toponimi, cognomi = glossario.Glossario.carica(percorso).forme_note()
    return descrivi_forme_note(toponimi, cognomi)


def _forme_note(config: Config) -> str:
    return _forme_note_da(config.glossario)


def costruisci_prompt(
    pagine: list[Pagina],
    percorsi: list[Path],
    forme_note: str = "",
) -> str:
    """Istruzione per un gruppo di pagine."""
    elenco = "\n".join(
        descrivi_pagina(
            str(percorso), pagina.registro.contesto, pagina.registro.anno, pagina.registro.tipologia
        )
        for pagina, percorso in zip(pagine, percorsi)
    )
    return ISTRUZIONE_GRUPPO.format(
        quante=len(pagine), elenco=elenco, forme_note=forme_note, schema=SCHEMA_A_PAROLE
    )


def _salva(config: Config, pagina: Pagina, contenuto: dict) -> Path:
    destinazione = config.trascrizioni / pagina.destinazione_relativa
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    contenuto = dict(contenuto)
    contenuto.pop("file", None)  # serviva solo ad allineare la risposta alle pagine
    contenuto["_origine"] = {
        "immagine": str(pagina.percorso.relative_to(config.immagini)),
        "registro": pagina.registro.slug,
        "ark_url": pagina.registro.ark_url,
        "anno": pagina.registro.anno,
        "tipologia": pagina.registro.tipologia,
        "contesto": pagina.registro.contesto,
    }
    destinazione.write_text(
        json.dumps(contenuto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destinazione


def _allinea(risposta, pagine: list[Pagina], percorsi: list[Path]) -> list[dict | None]:
    """Associa gli oggetti della risposta alle pagine richieste.

    Prima per nome file, che e' il criterio robusto se il modello cambia
    l'ordine; poi per posizione, per le risposte che il nome non ce
    l'hanno.
    """
    if isinstance(risposta, dict):
        risposta = [risposta]
    if not isinstance(risposta, list):
        return [None] * len(pagine)

    per_nome: dict[str, dict] = {}
    senza_nome: list[dict] = []
    for voce in risposta:
        if not isinstance(voce, dict):
            continue
        nome = voce.get("file")
        if isinstance(nome, str) and nome.strip():
            per_nome[Path(nome.strip()).name] = voce
        else:
            senza_nome.append(voce)

    allineate: list[dict | None] = []
    riserva = iter(senza_nome)
    for percorso in percorsi:
        voce = per_nome.pop(percorso.name, None)
        if voce is None:
            voce = next(riserva, None)
        allineate.append(voce)
    return allineate


def trascrivi_gruppo(config: Config, pagine: list[Pagina]) -> Esito:
    """Trascrive un gruppo di pagine con una sola invocazione di Claude Code.

    Se il gruppo non produce risultati utilizzabili e conteneva piu' di
    una pagina, riprova una pagina per volta: cosi' una pagina illeggibile
    non porta con se' le altre tre.
    """
    percorsi = [prepara_immagine(p, config) for p in pagine]
    cartelle = sorted({p.parent for p in percorsi} | {config.ridotte})

    esito = Esito(chiamate=1)
    risultato = claudecode.esegui(
        prompt=costruisci_prompt(pagine, percorsi, _forme_note(config)),
        sistema=SISTEMA,
        cartelle=cartelle,
        modello=config.trascrizione.modello,
        timeout=config.trascrizione.timeout_s,
    )
    esito.token_contesto = risultato.token_contesto

    if not risultato.ok:
        logger.warning("Gruppo non riuscito (%s)", risultato.errore)
        return _ritenta_singole(config, pagine, esito)

    try:
        dati = claudecode.estrai_json(risultato.testo)
    except ValueError as exc:
        logger.warning("Risposta non interpretabile: %s", exc)
        return _ritenta_singole(config, pagine, esito)

    for pagina, voce in zip(pagine, _allinea(dati, pagine, percorsi)):
        if voce is None:
            logger.warning("%s: nessun oggetto corrispondente nella risposta", pagina.id_richiesta)
            esito.fallite += 1
            continue
        try:
            _salva(config, pagina, valida_pagina(voce))
            esito.trascritte += 1
        except PaginaNonValida as exc:
            logger.warning("%s: %s", pagina.id_richiesta, exc)
            esito.fallite += 1
    return esito


def _ritenta_singole(config: Config, pagine: list[Pagina], esito: Esito) -> Esito:
    """Ripiego: una pagina per chiamata, per isolare quella problematica."""
    if len(pagine) == 1:
        esito.fallite += 1
        return esito
    logger.info("Riprovo le %d pagine una alla volta", len(pagine))
    for pagina in pagine:
        singolo = trascrivi_gruppo(config, [pagina])
        esito.trascritte += singolo.trascritte
        esito.fallite += singolo.fallite
        esito.chiamate += singolo.chiamate
        esito.token_contesto += singolo.token_contesto
    return esito


def esegui(config: Config, pagine: list[Pagina], attendi_quota: bool = False) -> Esito:
    """Trascrive tutte le pagine, a gruppi, gestendo l'esaurimento della quota."""
    claudecode.verifica_installazione()
    per_chiamata = max(1, config.trascrizione.pagine_per_chiamata)
    gruppi = [pagine[i : i + per_chiamata] for i in range(0, len(pagine), per_chiamata)]
    totale = Esito()

    for indice, gruppo in enumerate(gruppi, 1):
        while True:
            try:
                esito = trascrivi_gruppo(config, gruppo)
                break
            except claudecode.LimiteUsoRaggiunto as exc:
                if not attendi_quota:
                    logger.warning("Quota dell'abbonamento esaurita: %s", exc)
                    totale.quota_esaurita = True
                    return totale
                logger.info(
                    "Quota esaurita, riprovo fra %d minuti. Il lavoro fatto e' gia' salvato.",
                    ATTESA_QUOTA_S // 60,
                )
                time.sleep(ATTESA_QUOTA_S)

        totale.trascritte += esito.trascritte
        totale.fallite += esito.fallite
        totale.chiamate += esito.chiamate
        totale.token_contesto += esito.token_contesto
        logger.info(
            "[%d/%d gruppi] %d pagine trascritte, %d fallite",
            indice, len(gruppi), totale.trascritte, totale.fallite,
        )
    return totale


def stima(config: Config, pagine: list[Pagina]) -> str:
    """Cosa aspettarsi in termini di chiamate, contesto e tempo.

    Non ci sono euro da stimare: l'abbonamento e' gia' pagato. Il vincolo
    e' la quota, che si misura in token, e il tempo.
    """
    if not pagine:
        return "Nessuna pagina da trascrivere."

    per_chiamata = max(1, config.trascrizione.pagine_per_chiamata)
    chiamate = -(-len(pagine) // per_chiamata)  # divisione per eccesso
    # Misurato: ~50.000 token di impalcatura per invocazione, piu' circa
    # 2.500 per immagine a 1568 px di lato lungo.
    contesto = chiamate * 50_000 + len(pagine) * 2_500
    minuti = chiamate * 0.5  # ~30 s a chiamata per un gruppo di quattro

    return (
        f"{len(pagine)} pagine da trascrivere con {config.trascrizione.modello}\n"
        f"  {chiamate} invocazioni di Claude Code ({per_chiamata} pagine ciascuna)\n"
        f"  ~{contesto / 1e6:.1f}M token di contesto complessivi\n"
        f"  ~{minuti / 60:.1f} ore di esecuzione, al netto delle pause per la quota\n"
        f"\n"
        f"  La quota dell'abbonamento si rinnova a finestre: e' normale che\n"
        f"  un lavoro di questa mole si fermi e riprenda piu' volte. Usa\n"
        f"  --attendi per lasciarlo andare da solo; ogni pagina finita e'\n"
        f"  salvata subito, quindi rilanciare non rifa' mai il lavoro fatto."
    )
