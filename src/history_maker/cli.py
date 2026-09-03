"""Interfaccia a riga di comando: ``python -m history_maker <comando>``."""

from __future__ import annotations

import argparse
import dataclasses
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

    def _anni(parser_, aiuto: str) -> None:
        """Opzioni per restringere l'intervallo senza toccare la configurazione."""
        parser_.add_argument("--anno", type=int, default=None, help=f"solo quest'anno ({aiuto})")
        parser_.add_argument("--dal", type=int, default=None, help="anno iniziale")
        parser_.add_argument("--al", type=int, default=None, help="anno finale")

    p = sotto.add_parser("discover", help="fase 1: trova i registri del comune sul portale")
    _anni(p, "utile per una prova: 92 anni sono 92 ricerche")
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
    _anni(p, "scarica solo i registri di quest'anno")

    p = sotto.add_parser("transcribe", help="fase 3: fa trascrivere le immagini a un modello")
    p.add_argument("--backend", default=None, choices=["gemini", "claude-code"],
                   help="motore di trascrizione (default: dal file di configurazione)")
    p.add_argument("--modello", default=None,
                   help="modello del backend scelto, es. gemini-3.6-flash o claude-opus-5")
    p.add_argument("--senza-testo-integrale", action="store_true",
                   help="non chiedere la trascrizione diplomatica: un terzo di token in meno")
    p.add_argument("--limite", type=int, default=None, help="trascrive solo le prime N pagine")
    p.add_argument("--stima", action="store_true", help="mostra chiamate, contesto e tempo, poi si ferma")
    p.add_argument("--rifai", action="store_true", help="ritrascrive anche le pagine gia' fatte")
    p.add_argument("--attendi", action="store_true",
                   help="quando la quota si esaurisce, aspetta il rinnovo invece di fermarsi")
    p.add_argument("--pagine-per-chiamata", type=int, default=None,
                   help="pagine per invocazione (default: dal file di configurazione)")
    p.add_argument("--parallele", type=int, default=None,
                   help="chiamate aperte insieme; le partenze restano scandite dal ritmo")
    _anni(p, "trascrive solo le pagine di quest'anno")

    sotto.add_parser(
        "modelli",
        help="quali modelli accetta la tua chiave (non consuma quota di generazione)",
    )

    p = sotto.add_parser(
        "confronta",
        help="mette due letture delle stesse pagine a confronto (non consuma quota)",
    )
    p.add_argument("prima", type=Path, help="cartella di trascrizioni")
    p.add_argument("seconda", type=Path, help="l'altra cartella di trascrizioni")
    p.add_argument("--divergenze", type=int, default=40,
                   help="quante divergenze elencare (default 40)")
    p.add_argument("--rapporto", type=Path, default=None,
                   help="scrive il rapporto su file invece che a schermo")

    sotto.add_parser("dataset", help="fase 4: costruisce database, CSV e sintesi")

    sotto.add_parser(
        "genealogia",
        help="fase 6: riconosce le persone e ricostruisce i legami familiari",
    )

    p = sotto.add_parser(
        "ricostruisci",
        help="fase 6b: la ricostruzione probabilistica, con le sue anomalie",
    )
    p.add_argument("--giri", type=int, default=None,
                   help="quanti giri di riconciliazione al massimo")
    p.add_argument("--senza-cache", action="store_true",
                   help="rifa' i vicinati invece di riusarli (piu' lento)")

    p = sotto.add_parser(
        "dubbi",
        help="la coda dei casi da guardare, in ordine di priorita'",
    )
    p.add_argument("--quanti", type=int, default=30, help="quanti casi mostrare")
    p.add_argument("--tipo", default=None, help="solo le anomalie di questo tipo")

    p = sotto.add_parser(
        "verifica",
        help="fase 6d: chiede all'immagine originale le parole decisive",
    )
    p.add_argument("--quante", type=int, default=30,
                   help="quante domande fare al massimo")
    p.add_argument("--elenca", action="store_true",
                   help="mostra le domande che farebbe e si ferma")
    p.add_argument("--backend", default=None, choices=["gemini", "claude-code"],
                   help="motore per questa esecuzione (default: dal file di "
                        "configurazione)")
    p.add_argument("--modello", default=None,
                   help="modello del backend scelto, per questa esecuzione")

    p = sotto.add_parser(
        "rileggi",
        help="fase 6f: rilegge la pagina intera con il contesto genealogico accanto",
    )
    p.add_argument("--quante", type=int, default=20,
                   help="quante pagine rileggere al massimo")
    p.add_argument("--atto", type=int, default=None,
                   help="una pagina precisa, invece di quelle in coda")
    p.add_argument("--elenca", action="store_true",
                   help="mostra i contesti che manderebbe e si ferma "
                        "(non consuma quota)")
    p.add_argument("--contesto", action="store_true",
                   help="con --elenca, stampa il contesto per intero invece "
                        "del riassunto")
    p.add_argument("--backend", default=None, choices=["gemini", "claude-code"],
                   help="motore per questa esecuzione (default: dal file di "
                        "configurazione). La rilettura legge un'immagine: "
                        "resta Gemini, di norma.")
    p.add_argument("--modello", default=None,
                   help="modello del backend scelto, per questa esecuzione")

    p = sotto.add_parser(
        "arbitra",
        help="fase 6e: sottopone a un modello i casi che il calcolo non decide",
    )
    p.add_argument("--quanti", type=int, default=20, help="quanti casi sottoporre")
    p.add_argument("--tipo", default=None,
                   help="solo i casi di questo tipo, es. DUPLICATE_PERSON")
    p.add_argument("--elenca", action="store_true",
                   help="mostra i fascicoli che manderebbe e si ferma")
    p.add_argument("--backend", default=None, choices=["gemini", "claude-code"],
                   help="motore per questa esecuzione (default: dal file di "
                        "configurazione). L'arbitrato e' ragionamento su "
                        "testo, non lettura di un'immagine: e' il posto "
                        "giusto per 'claude-code', anche quando le "
                        "trascrizioni usano Gemini.")
    p.add_argument("--modello", default=None,
                   help="modello del backend scelto, per questa esecuzione")
    p.add_argument("--dividi", action="store_true",
                   help="applica anche le divisioni chieste dal modello "
                        "(misurate come dannose: vedi docs/ricostruzione.md)")

    p = sotto.add_parser(
        "decidi",
        help="registra una decisione presa da te, che vale piu' del calcolo",
    )
    p.add_argument("azione", choices=["unione", "separazione", "disfa"],
                   help="unire due schede, separarle, o disfare una decisione")
    p.add_argument("chiavi", nargs="+",
                   help="le due chiavi delle schede (il numero dopo la P), "
                        "oppure il numero della decisione da disfare")
    p.add_argument("--perche", required=True, help="il motivo, che resta scritto")

    p = sotto.add_parser("albero", help="apre l'applicazione per navigare l'albero")
    p.add_argument("--porta", type=int, default=8000, help="porta su cui servire")
    p.add_argument("--senza-browser", action="store_true",
                   help="non aprire il browser da solo")
    sotto.add_parser(
        "revisione",
        help="fase 5: segnala le letture probabilmente sbagliate (non consuma quota)",
    )
    p = sotto.add_parser(
        "qualita",
        help="fase 7: conta cio' che nell'albero non puo' essere vero",
    )
    p.add_argument("--esempi", type=int, default=10,
                   help="quanti casi mostrare per ogni controllo nel report")
    p.add_argument("--motore", default="v2", choices=["v1", "v2"],
                   help="quale ricostruzione misurare: v1 = 'genealogia', "
                        "v2 = 'ricostruisci' (default)")

    sotto.add_parser(
        "confronta-ricostruzioni",
        help="mette la vecchia fase 6 e la nuova fianco a fianco (non consuma quota)",
    )

    p = sotto.add_parser(
        "pipeline",
        help="fase 6g: ricostruisci, rileggi e arbitra in ciclo, finche' "
             "un giro non produce piu' niente",
    )
    p.add_argument("--giri", type=int, default=None,
                   help="quanti giri al massimo (default 20)")
    p.add_argument("--pagine-per-giro", type=int, default=None,
                   help="quante pagine rileggere per giro (default 40)")
    p.add_argument("--casi-per-giro", type=int, default=None,
                   help="quanti casi sottoporre all'arbitro per giro (default 15)")
    p.add_argument("--tipo-arbitro", default=None,
                   help="solo i casi di questo tipo per l'arbitro, "
                        "es. DUPLICATE_PERSON")
    p.add_argument("--dividi", action="store_true",
                   help="applica anche le divisioni chieste dall'arbitro "
                        "(misurate come dannose in aggregato: vedi "
                        "docs/ricostruzione.md)")
    p.add_argument("--backend-arbitro", default=None,
                   choices=["gemini", "claude-code"],
                   help="motore per l'arbitrato (default: dal file di "
                        "configurazione). L'arbitrato ragiona su testo: "
                        "'claude-code' e' il posto per cui e' pensato, "
                        "anche quando le trascrizioni usano Gemini.")
    p.add_argument("--modello-arbitro", default=None,
                   help="modello del backend scelto per l'arbitrato")

    p = sotto.add_parser(
        "archivia-decisioni",
        help="sposta le decisioni dell'algoritmo delle esecuzioni piu' vecchie",
    )
    p.add_argument("--tieni", type=int, default=None,
                   help="quante esecuzioni tenere nella tabella calda (default 2)")
    p.add_argument("--a-secco", action="store_true",
                   help="conta quanto sposterebbe, senza spostare niente")

    sotto.add_parser("stato", help="a che punto e' la pipeline")
    return parser


# Comandi che non possono fare nulla senza il catalogo prodotto da 'discover'.
RICHIEDONO_CATALOGO = {"catalog", "download", "transcribe"}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configura_log(args.verboso)
    config = Config.carica(args.config)

    # --anno 1809 e' la scorciatoia per --dal 1809 --al 1809.
    dal = getattr(args, "dal", None)
    al = getattr(args, "al", None)
    if getattr(args, "anno", None) is not None:
        dal = al = args.anno

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
            dal=dal,
            al=al,
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
            config, limite_registri=args.limite, lato_max=args.lato_max,
            solo_stima=args.elenca, dal=dal, al=al,
        )
        if not args.elenca:
            print(
                f"Scaricate {esito.scaricate}, gia' presenti {esito.saltate}, "
                f"fallite {esito.fallite} ({esito.byte / 1e9:.2f} GB)"
            )
        return 0

    if args.comando == "transcribe":
        from history_maker import backend as motori
        from history_maker import transcribe

        # Le opzioni sovrascrivono il file di configurazione solo per
        # questa esecuzione: e' cosi' che si confrontano due motori sulle
        # stesse pagine senza toccare il YAML fra una prova e l'altra.
        modifiche = {}
        if args.pagine_per_chiamata:
            modifiche["pagine_per_chiamata"] = args.pagine_per_chiamata
        if args.backend:
            modifiche["backend"] = args.backend
        if args.modello:
            modifiche["modello"] = args.modello
        if args.senza_testo_integrale:
            modifiche["testo_integrale"] = False
        if args.parallele:
            modifiche["chiamate_parallele"] = args.parallele
        if modifiche:
            config = dataclasses.replace(
                config, trascrizione=dataclasses.replace(config.trascrizione, **modifiche)
            )

        pagine = transcribe.pagine_da_trascrivere(
            config, solo_mancanti=not args.rifai, dal=dal, al=al
        )
        if args.limite:
            pagine = pagine[: args.limite]
        print(transcribe.stima(config, pagine))
        if args.stima or not pagine:
            return 0

        try:
            esito = transcribe.esegui(config, pagine, attendi_quota=args.attendi)
        except motori.BackendNonDisponibile as exc:
            print(exc, file=sys.stderr)
            return 2

        print(
            f"\nTrascritte {esito.trascritte}/{len(pagine)} pagine "
            f"({esito.fallite} fallite) in {esito.chiamate} chiamate."
        )
        # I token consumati non sono un dettaglio contabile: il limite di
        # token al minuto e' cio' che mette il tetto a 'pagine_per_chiamata',
        # e senza vedere il consumo reale quel numero si sceglie a caso.
        if esito.token_contesto or esito.token_output:
            def _mille(n: float) -> str:
                """Separatore delle migliaia all'italiana: 16.246, non 16,246."""
                return f"{n:,.0f}".replace(",", ".")

            per_pagina = (esito.token_contesto + esito.token_output) / max(1, esito.trascritte)
            print(
                f"Token: {_mille(esito.token_contesto)} in ingresso, "
                f"{_mille(esito.token_output)} in uscita — "
                f"~{_mille(per_pagina)} a pagina, "
                f"~{_mille(esito.token_contesto / max(1, esito.chiamate))} in ingresso "
                f"per chiamata."
            )
        if esito.quota_esaurita:
            print(
                "\nLa quota si e' esaurita. Il lavoro fatto e' salvato:\n"
                "  rilancia lo stesso comando piu' tardi per riprendere,\n"
                "  oppure aggiungi --attendi per lasciarlo proseguire da solo."
            )
        return 0

    if args.comando == "modelli":
        from history_maker import backend as motori

        motore = motori.crea(config)
        if not hasattr(motore, "modelli_disponibili"):
            print(
                f"Il backend '{motore.nome}' non espone un elenco di modelli.",
                file=sys.stderr,
            )
            return 2
        try:
            disponibili = motore.modelli_disponibili()
        except motori.BackendNonDisponibile as exc:
            print(exc, file=sys.stderr)
            return 2

        attuale = config.trascrizione.modello
        print(f"Modelli utilizzabili con la tua chiave ({len(disponibili)}):\n")
        for nome in disponibili:
            print(f"  {'->' if nome == attuale else '  '} {nome}")
        if attuale not in disponibili:
            print(
                f"\nAttenzione: '{attuale}', il modello in configurazione, non e' "
                f"nell'elenco.\nCorreggi 'trascrizione.modello' in config/torrebruna.yaml."
            )
        return 0

    if args.comando == "confronta":
        from history_maker import confronto

        esito = confronto.confronta(args.prima, args.seconda)
        testo = confronto.rapporto(esito, quante_divergenze=args.divergenze)
        if args.rapporto:
            args.rapporto.parent.mkdir(parents=True, exist_ok=True)
            args.rapporto.write_text(testo, encoding="utf-8")
            print(f"Rapporto: {args.rapporto}")
        else:
            print(testo)
        return 0

    if args.comando == "dataset":
        from history_maker import dataset

        percorso = dataset.costruisci(config)
        print(f"Database: {percorso}")
        print(f"Sintesi:  {config.dataset / 'sintesi.md'}")
        return 0

    if args.comando == "genealogia":
        from history_maker import genealogia

        percorso = genealogia.costruisci(config)
        print(f"Database: {percorso}")
        print(f"Sintesi:  {config.dataset / 'genealogia.md'}")
        print(f"Da rivedere: {config.dataset / 'glossario-nomi-proposto.yaml'}")
        return 0

    if args.comando == "ricostruisci":
        from history_maker.ricostruzione import esecuzione, risoluzione

        percorso = esecuzione.costruisci(
            config,
            giri=args.giri or risoluzione.GIRI_MASSIMI,
            senza_cache=args.senza_cache,
        )
        print(f"Database:  {percorso}")
        print(f"Sintesi:   {config.dataset / 'ricostruzione.md'}")
        print(f"Dubbi:     {config.dataset / 'anomalie.md'}")
        print(
            "\nLe anomalie non sono scarti: sono la coda di lavoro. "
            "'python -m history_maker dubbi' la mostra in ordine."
        )
        return 0

    if args.comando == "dubbi":
        import sqlite3

        percorso = config.dataset / "torrebruna.sqlite"
        if not percorso.exists():
            print(f"Manca {percorso}. Prima: python -m history_maker ricostruisci",
                  file=sys.stderr)
            return 2
        conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            righe = list(conn.execute(
                "SELECT tipo, descrizione, spiegazioni, confidenza, impatto, priorita "
                "FROM anomalie WHERE stato = 'aperta' "
                + ("AND tipo = ? " if args.tipo else "")
                + "ORDER BY priorita DESC LIMIT ?",
                ((args.tipo, args.quanti) if args.tipo else (args.quanti,)),
            ))
        except sqlite3.OperationalError:
            print(
                "Nessuna tabella 'anomalie': l'albero e' stato costruito con la\n"
                "fase 6 vecchia. Rilancia con 'python -m history_maker ricostruisci'.",
                file=sys.stderr,
            )
            return 2
        for riga in righe:
            print(f"[{riga['priorita']:.2f}] {riga['tipo']}")
            print(f"  {riga['descrizione']}")
            if riga["spiegazioni"]:
                print(f"  possibili spiegazioni: {riga['spiegazioni']}")
            print(f"  confidenza {riga['confidenza']:.0%}, impatto {riga['impatto']}")
        totale = conn.execute(
            "SELECT COUNT(*) FROM anomalie WHERE stato = 'aperta'"
        ).fetchone()[0]
        print(f"\n{len(righe)} casi mostrati su {totale} aperti.")
        return 0

    if args.comando == "decidi":
        import sqlite3

        from history_maker.ricostruzione import registro

        percorso = config.dataset / "torrebruna.sqlite"
        if not percorso.exists():
            print(f"Manca {percorso}. Prima: python -m history_maker ricostruisci",
                  file=sys.stderr)
            return 2
        conn = sqlite3.connect(percorso)
        try:
            if args.azione == "disfa":
                numero = registro.disfa(conn, int(args.chiavi[0]), args.perche)
                conn.commit()
                print(
                    f"Decisione {args.chiavi[0]} superata dalla {numero}. "
                    f"La vecchia resta nel registro: un archivio in cui si\n"
                    f"puo' riscrivere il passato non e' un archivio."
                )
                return 0
            if len(args.chiavi) < 2:
                print("Servono due chiavi.", file=sys.stderr)
                return 2
            numero = registro.annota(
                conn, args.azione, [int(c) for c in args.chiavi[:2]],
                args.perche, confidenza=1.0, decisore="persona",
            )
            conn.commit()
        except (ValueError, sqlite3.OperationalError) as errore:
            print(f"Non riesco a registrare la decisione: {errore}", file=sys.stderr)
            return 2
        finally:
            conn.close()
        print(
            f"Decisione {numero} registrata.\n"
            f"Vale dalla prossima ricostruzione: "
            f"python -m history_maker ricostruisci"
        )
        return 0

    if args.comando == "rileggi":
        import json
        import sqlite3

        from history_maker.ricostruzione import rilettura

        if args.backend or args.modello:
            modifiche = {}
            if args.backend:
                modifiche["backend"] = args.backend
            if args.modello:
                modifiche["modello"] = args.modello
            config = dataclasses.replace(
                config, trascrizione=dataclasses.replace(config.trascrizione, **modifiche)
            )

        percorso = config.dataset / "torrebruna.sqlite"
        if not percorso.exists():
            print(f"Manca {percorso}. Prima: python -m history_maker ricostruisci",
                  file=sys.stderr)
            return 2

        # A differenza di 'verifica' e 'arbitra', qui il grafo non si
        # ricostruisce in memoria: il contesto di un atto si legge dalle
        # tabelle. Due minuti e mezzo di attesa per una domanda su una
        # pagina sarebbero il motivo per cui la domanda non si fa.
        conn = sqlite3.connect(percorso)
        conn.row_factory = sqlite3.Row
        atti = [args.atto] if args.atto else rilettura.casi(conn, args.quante)
        dossier = rilettura.prepara(conn, atti, config.immagini)

        if args.elenca or not dossier:
            for dato in dossier:
                if args.contesto:
                    print(json.dumps(
                        {k: v for k, v in dato.items() if not k.startswith("_")},
                        ensure_ascii=False, indent=1,
                    ))
                    continue
                atto = dato["atto"]
                print(f"atto {atto['id']}: {atto['tipo']} n. {atto['numero']} "
                      f"del {atto['anno']} — {atto['immagine']}")
                for domanda in dato["domande_aperte"]:
                    print(f"    ? {domanda}")
                for errore in dato["possible_errors"][:3]:
                    print(f"    · {errore['codice']} ({errore['confidenza']:.2f}) "
                          f"{errore['descrizione'][:90]}")
            print(f"\n{len(dossier)} pagine da rileggere. "
                  f"Senza --elenca consumano quota.")
            conn.close()
            return 0

        esiti = rilettura.esegui(config, conn, dossier)
        conn.commit()
        conn.close()
        print(
            f"Pagine rilette: {esiti['fatte']}, riusate dalla cache: "
            f"{esiti['riusate']}, fallite: {esiti['fallite']}.\n"
            f"Correzioni registrate: {esiti['correzioni']}, "
            f"conflitti col contesto: {esiti['conflitti_col_contesto']}."
        )
        if esiti["correzioni"]:
            print("Valgono dalla prossima ricostruzione: "
                  "python -m history_maker ricostruisci")
        if esiti["quota_esaurita"]:
            print("\nLa quota si e' esaurita. Quello che e' fatto e' salvato:\n"
                  "  rilancia lo stesso comando piu' tardi per riprendere.")
        return 0

    if args.comando in ("verifica", "arbitra"):
        import json
        import sqlite3

        from history_maker.ricostruzione import (
            anomalie as coda_anomalie, arbitro, cache, contesto, esecuzione,
            lettura, risoluzione, verifica as verifica_immagini,
        )

        if args.backend or args.modello:
            modifiche = {}
            if args.backend:
                modifiche["backend"] = args.backend
            if args.modello:
                modifiche["modello"] = args.modello
            config = dataclasses.replace(
                config, trascrizione=dataclasses.replace(config.trascrizione, **modifiche)
            )

        percorso = config.dataset / "torrebruna.sqlite"
        if not percorso.exists():
            print(f"Manca {percorso}. Prima: python -m history_maker ricostruisci",
                  file=sys.stderr)
            return 2

        # La ricostruzione si rifa' in memoria: e' l'unico modo di avere il
        # grafo su cui ragionare, e costa qualche minuto contro una quota
        # che non si ricompra. Le risposte gia' date restano in cache.
        conn = sqlite3.connect(percorso)
        deposito = cache.Deposito(config.dataset)
        corpus = lettura.carica(conn, deposito)
        esito = risoluzione.ricostruisci(corpus, deposito=deposito)
        esito.anomalie.extend(coda_anomalie.tutte(esito))

        if args.comando == "verifica":
            domande = verifica_immagini.domande_da(
                esito, esito.anomalie, quante=args.quante
            )
            if args.elenca or not domande:
                for domanda in domande:
                    print(f"[{domanda.priorita:.2f}] atto {domanda.atto} — "
                          f"{domanda.domanda}")
                    print(f"    {domanda.motivo}")
                print(f"\n{len(domande)} domande.")
                return 0
            esiti = verifica_immagini.esegui(config, conn, domande)
            conn.commit()
            print(
                f"Domande fatte: {esiti['fatte']}, riusate dalla cache: "
                f"{esiti['riusate']}, fallite: {esiti['fallite']}."
            )
            if esiti["quota_esaurita"]:
                print(
                    "\nLa quota si e' esaurita. Le risposte gia' avute sono salvate:\n"
                    "  rilancia lo stesso comando piu' tardi per riprendere."
                )
            return 0

        esclusi_gia_arbitrati = arbitro.gia_arbitrati(conn)
        casi = arbitro.casi(esito, args.quanti, args.tipo, esclusi_gia_arbitrati)
        if args.elenca or not casi:
            for anomalia in casi:
                fascicolo = contesto.fascicolo(anomalia, esito)
                print(json.dumps(fascicolo, ensure_ascii=False, indent=1))
            print(f"\n{len(casi)} casi.")
            return 0
        gia_prese = len(esito.decisioni)
        conteggi = arbitro.arbitra(
            config, esito, quanti=args.quanti, deposito=deposito, tipo=args.tipo,
            dividi=args.dividi, conn=conn,
        )
        # Le risposte si salvano **tutte**, anche quelle che non cambiano
        # niente: e' il solo modo di poter dire, fra un mese, quante volte
        # quel modello ha detto 'non deciso' e su che tipo di casi.
        from history_maker.ricostruzione import registro

        registro.salva(conn, esito.decisioni[gia_prese:])
        conn.commit()
        print(
            f"Casi sottoposti: {conteggi['casi']}, risposte: {conteggi['risposte']}, "
            f"applicate: {conteggi['applicate']}, riusate: {conteggi['riusate']}."
        )
        if conteggi["verifiche_chieste"]:
            print(
                f"{conteggi['verifiche_chieste']} casi chiedono di guardare "
                f"l'immagine: python -m history_maker verifica"
            )
        print(
            "\nLe decisioni prese sono nella tabella 'decisioni', con il modello\n"
            "che le ha prese. Per vederle applicate all'albero rilancia\n"
            "'python -m history_maker ricostruisci'."
        )
        return 0

    if args.comando == "albero":
        from history_maker import app

        return app.avvia(config, porta=args.porta, apri=not args.senza_browser)

    if args.comando == "qualita":
        from history_maker import affiancate, qualita

        try:
            conn = affiancate.apri(config, args.motore, sola_lettura=True)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 2
        if not affiancate.ricostruita(conn):
            quale = "genealogia" if args.motore == "v1" else "ricostruisci"
            print(f"Nessuna ricostruzione '{args.motore}' da misurare. "
                  f"Prima: python -m history_maker {quale}", file=sys.stderr)
            return 2
        # Il report della V1 sta accanto al suo database, non sopra quello
        # della V2: misurare l'altro motore non deve sovrascrivere il
        # rapporto di quello che si sta usando.
        destinazione = config.dataset / (
            "qualita.md" if args.motore == "v2" else f"qualita-{args.motore}.md"
        )
        report, esiti = qualita.scrivi_report(
            conn, destinazione, esempi=args.esempi
        )
        totali = qualita.conteggi(esiti)
        for esito in sorted(esiti, key=lambda e: -e.quanti):
            if esito.quanti:
                print(f"{esito.quanti:>6}  {esito.controllo.categoria:<15} "
                      f"{esito.controllo.nome}")
        print(
            f"\n{totali.get('impossibile', 0)} cose impossibili, "
            f"{totali.get('frammentazione', 0)} frammentazioni, "
            f"{totali.get('accorpamento', 0)} accorpamenti, "
            f"{totali.get('sospetto', 0)} letture da ricontrollare."
            f"\nReport: {report}"
        )
        return 0

    if args.comando == "confronta-ricostruzioni":
        from history_maker import affiancate

        try:
            rapporto = affiancate.confronta(config)
        except (FileNotFoundError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 2
        destinazione = config.dataset / "confronto-ricostruzioni.md"
        destinazione.write_text(rapporto, encoding="utf-8")
        print(rapporto)
        print(f"Rapporto: {destinazione}")
        return 0

    if args.comando == "archivia-decisioni":
        import sqlite3

        from history_maker.ricostruzione import registro

        percorso = config.dataset / "torrebruna.sqlite"
        if not percorso.exists():
            print(f"Manca {percorso}. Prima: python -m history_maker ricostruisci",
                  file=sys.stderr)
            return 2
        conn = sqlite3.connect(percorso)
        kwargs = {"a_secco": args.a_secco}
        if args.tieni is not None:
            kwargs["tieni_ultime"] = args.tieni
        esiti = registro.archivia(conn, **kwargs)
        if not args.a_secco:
            conn.commit()
        conn.close()
        print(
            f"{esiti['esecuzioni_trovate']} esecuzioni distinte nella tabella. "
        )
        if args.a_secco:
            print(f"Sposterebbe {esiti.get('archiviabili', 0)} decisioni "
                  f"algoritmiche in decisioni_archivio (nessuna toccata).")
        else:
            print(f"{esiti['archiviate']} decisioni algoritmiche spostate "
                  f"in decisioni_archivio.")
        return 0

    if args.comando == "pipeline":
        from history_maker.ricostruzione import pipeline

        config_arbitro = config
        if args.backend_arbitro or args.modello_arbitro:
            modifiche = {}
            if args.backend_arbitro:
                modifiche["backend"] = args.backend_arbitro
            if args.modello_arbitro:
                modifiche["modello"] = args.modello_arbitro
            config_arbitro = dataclasses.replace(
                config, trascrizione=dataclasses.replace(config.trascrizione, **modifiche)
            )

        kwargs = {}
        if args.giri is not None:
            kwargs["giri_massimi"] = args.giri
        if args.pagine_per_giro is not None:
            kwargs["pagine_per_giro"] = args.pagine_per_giro
        if args.casi_per_giro is not None:
            kwargs["casi_per_giro"] = args.casi_per_giro

        def stampa_giro(giro):
            print(
                f"giro {giro.numero}: {giro.correzioni_lettura} correzioni "
                f"({giro.conflitti_col_contesto} conflitti col contesto), "
                f"{giro.decisioni_arbitro} decisioni dell'arbitro "
                f"({giro.divisioni_proposte} divisioni proposte) — "
                f"{giro.schede_dopo} schede, {giro.impossibili} impossibili, "
                f"{giro.frammentazioni} frammentazioni, "
                f"{giro.accorpamenti} accorpamenti"
            )
            if giro.pagine_fallite:
                print(f"    {giro.pagine_fallite} pagine fallite nella lettura")
            if giro.fermo_per_quota:
                print("    quota esaurita in questo giro")

        try:
            storia = pipeline.esegui(
                config, tipo_arbitro=args.tipo_arbitro, dividi_arbitro=args.dividi,
                config_arbitro=config_arbitro, su_giro=stampa_giro, **kwargs
            )
        except FileNotFoundError as errore:
            print(errore, file=sys.stderr)
            return 2

        destinazione = config.dataset / "pipeline.md"
        destinazione.write_text(pipeline.rapporto(storia), encoding="utf-8")
        print(f"\nRapporto: {destinazione}")
        if storia:
            motivo = pipeline.convergenza(storia)
            if motivo:
                print(motivo)
            elif storia[-1].fermo_per_quota:
                print(
                    "Fermato per quota esaurita, non per convergenza: "
                    "rilanciare lo stesso comando piu' tardi riprende da qui."
                )
        return 0

    if args.comando == "revisione":
        from history_maker import revisione

        try:
            segnalazioni, incerti = revisione.analizza(config)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 2
        percorso = revisione.scrivi_report(config)
        print(
            f"{len(segnalazioni)} segnalazioni e {len(incerti)} atti incerti.\n"
            f"Report: {percorso}\n"
            f"\nSono segnalazioni da vagliare, non correzioni: un cognome raro\n"
            f"puo' essere un forestiero vero, ed e' un dato che vale la pena tenere."
        )
        return 0

    if args.comando == "stato":
        _stato(config)
        return 0

    return 1


def _stato(config: Config) -> None:
    """Riassume a che punto sono le quattro fasi."""
    from history_maker.catalogo import Catalogo, selezione

    print(f"Comune: {config.comune} ({config.anno_min}-{config.anno_max})\n")

    if not config.catalogo.exists():
        print("1. scoperta     non ancora fatta  ->  python -m history_maker discover")
        return
    catalogo = Catalogo.carica(config.catalogo)
    selezionati = selezione(catalogo, config)
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

    report = config.dataset / "revisione.md"
    print(f"5. revisione    {'fatta' if report.exists() else 'non ancora fatta'}")

    if db.exists():
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            persone = conn.execute("SELECT COUNT(*) FROM individui").fetchone()[0]
            dubbi = conn.execute(
                "SELECT COUNT(*) FROM anomalie WHERE stato = 'aperta'"
            ).fetchone()[0]
            print(f"6. albero       {persone} persone, {dubbi} dubbi aperti")
        except sqlite3.OperationalError:
            print("6. albero       non ancora ricostruito"
                  "  ->  python -m history_maker ricostruisci")
        finally:
            conn.close()
    else:
        print("6. albero       non ancora ricostruito")


if __name__ == "__main__":
    sys.exit(main())
