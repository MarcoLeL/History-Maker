"""Interfaccia a riga di comando: ``python -m history_maker <comando>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from history_maker.config import Config


def _configura_log(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="history-maker",
        description="Dal Portale Antenati alla storia di un paese: scoperta, "
        "scaricamento, trascrizione e riordino degli atti di stato civile.",
    )
    parser.add_argument("-c", "--config", type=Path, default=None, help="file YAML di configurazione")
    parser.add_argument("-v", "--verboso", action="store_true", help="log di dettaglio")
    sotto = parser.add_subparsers(dest="comando", required=True)

    p = sotto.add_parser("discover", help="fase 1: trova i registri del comune sul portale")
    p.add_argument("--headless", action="store_true",
                   help="browser senza finestra (piu' esposto alle challenge del WAF)")
    p.add_argument("--tutti-gli-anni-insieme", action="store_true",
                   help="una sola ricerca invece di una per anno")
    p.add_argument("--debug-html", type=Path, default=None,
                   help="salva l'HTML delle pagine di ricerca in questa cartella")

    p = sotto.add_parser("catalog", help="mostra cosa contiene il catalogo")
    p.add_argument("--scartati", action="store_true", help="elenca anche i registri esclusi")

    p = sotto.add_parser("download", help="fase 2: scarica le immagini dei registri selezionati")
    p.add_argument("--limite", type=int, default=None, help="scarica solo i primi N registri")
    p.add_argument("--lato-max", type=int, default=0,
                   help="lato lungo massimo in pixel (0 = piena risoluzione)")
    p.add_argument("--elenca", action="store_true", help="mostra cosa scaricherebbe e si ferma")

    p = sotto.add_parser("transcribe", help="fase 3: fa trascrivere le immagini a Claude")
    p.add_argument("--batch", action="store_true",
                   help="usa la Batch API: meta' del costo, risultati entro 24 ore")
    p.add_argument("--raccogli", action="store_true", help="scarica i risultati dei lotti gia' inviati")
    p.add_argument("--limite", type=int, default=None, help="trascrive solo le prime N pagine")
    p.add_argument("--stima", action="store_true", help="stima il costo e si ferma")
    p.add_argument("--rifai", action="store_true", help="ritrascrive anche le pagine gia' fatte")

    sotto.add_parser("dataset", help="fase 4: costruisce database, CSV e sintesi")
    sotto.add_parser("stato", help="a che punto e' la pipeline")
    return parser


# Comandi che non possono fare nulla senza il catalogo prodotto da 'discover'.
RICHIEDONO_CATALOGO = {"catalog", "download", "transcribe"}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configura_log(args.verboso)
    config = Config.carica(args.config)

    if args.comando in RICHIEDONO_CATALOGO and not config.catalogo.exists():
        print(
            f"Nessun catalogo in {config.catalogo}.\n"
            f"Comincia dalla scoperta dei registri:\n"
            f"  python -m history_maker discover",
            file=sys.stderr,
        )
        return 2

    if args.comando == "discover":
        from history_maker import discover

        catalogo = discover.esegui(
            config,
            headless=args.headless,
            solo_anni=not args.tutti_gli_anni_insieme,
            debug_dir=args.debug_html,
        )
        print(discover.riepilogo(catalogo, config))
        return 0

    if args.comando == "catalog":
        from history_maker import discover
        from history_maker.catalogo import Catalogo, pertinente

        catalogo = Catalogo.carica(config.catalogo)
        print(discover.riepilogo(catalogo, config))
        if args.scartati:
            print("\nRegistri scartati:")
            for registro in catalogo.registri:
                ok, motivo = pertinente(registro, config)
                if not ok:
                    print(f"  {registro.ark_url}\n    {registro.contesto or '?'} — {motivo}")
        return 0

    if args.comando == "download":
        from history_maker import download

        esito = download.esegui(
            config, limite_registri=args.limite, lato_max=args.lato_max, solo_stima=args.elenca
        )
        if not args.elenca:
            print(
                f"Scaricate {esito.scaricate}, gia' presenti {esito.saltate}, "
                f"fallite {esito.fallite} ({esito.byte / 1e9:.2f} GB)"
            )
        return 0

    if args.comando == "transcribe":
        from history_maker import transcribe

        if args.raccogli:
            print(f"Salvate {transcribe.raccogli_lotti(config)} trascrizioni.")
            return 0

        pagine = transcribe.pagine_da_trascrivere(config, solo_mancanti=not args.rifai)
        if args.limite:
            pagine = pagine[: args.limite]
        print(transcribe.stima_costo(config, pagine, lotto=args.batch))
        if args.stima:
            return 0
        if not pagine:
            return 0
        if args.batch:
            identificativi = transcribe.invia_lotti(config, pagine)
            print(
                f"Inviati {len(identificativi)} lotti. Quando saranno pronti (di norma "
                f"entro un'ora):\n  python -m history_maker transcribe --raccogli"
            )
        else:
            print(f"Trascritte {transcribe.trascrivi_sincrono(config, pagine)}/{len(pagine)} pagine.")
        return 0

    if args.comando == "dataset":
        from history_maker import dataset

        percorso = dataset.costruisci(config)
        print(f"Database: {percorso}")
        print(f"Sintesi:  {config.dataset / 'sintesi.md'}")
        return 0

    if args.comando == "stato":
        _stato(config)
        return 0

    return 1


def _stato(config: Config) -> None:
    """Riassume a che punto sono le quattro fasi."""
    from history_maker.catalogo import Catalogo, pertinente

    print(f"Comune: {config.comune} ({config.anno_min}-{config.anno_max})\n")

    if not config.catalogo.exists():
        print("1. scoperta     non ancora fatta  ->  python -m history_maker discover")
        return
    catalogo = Catalogo.carica(config.catalogo)
    selezionati = [r for r in catalogo.registri if pertinente(r, config)[0]]
    attese = sum(r.n_immagini or 0 for r in selezionati)
    print(f"1. scoperta     {len(catalogo.registri)} registri, {len(selezionati)} selezionati")

    from history_maker.transcribe import ESTENSIONI_IMMAGINE

    scaricate = sum(
        1
        for r in selezionati
        for f in (config.immagini / r.slug).glob("*")
        if f.suffix.lower() in ESTENSIONI_IMMAGINE
    )
    print(f"2. immagini     {scaricate}/{attese or '?'} scaricate")

    trascritte = (
        sum(1 for p in config.trascrizioni.rglob("*.json") if not p.name.startswith("_"))
        if config.trascrizioni.exists()
        else 0
    )
    print(f"3. trascrizioni {trascritte}/{scaricate or '?'} pagine")

    db = config.dataset / "torrebruna.sqlite"
    print(f"4. dataset      {'costruito' if db.exists() else 'non ancora costruito'}")


if __name__ == "__main__":
    sys.exit(main())
