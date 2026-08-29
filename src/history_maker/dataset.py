"""Fase 4: dalle trascrizioni a un archivio consultabile.

Produce tre cose dalla stessa fonte:

* ``torrebruna.sqlite``  tre tabelle (registri, atti, persone) piu' un
  indice full-text su cui si possono fare domande come "tutti i Di Nardo
  fra il 1840 e il 1860".
* ``atti.csv`` / ``persone.csv``  per chi preferisce un foglio di calcolo.
* ``sintesi.md``  un quadro d'insieme: quanti atti per anno, i cognomi
  piu' frequenti, i mestieri, le lacune della serie.

Non interpreta i dati: li mette in forma. L'interpretazione storica resta
un lavoro da fare sulle fonti cosi' ordinate.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from history_maker import glossario, normalizza
from history_maker.config import Config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS registri (
    slug        TEXT PRIMARY KEY,
    anno        INTEGER,
    tipologia   TEXT,
    contesto    TEXT,
    ark_url     TEXT
);

CREATE TABLE IF NOT EXISTS atti (
    id              INTEGER PRIMARY KEY,
    registro        TEXT REFERENCES registri(slug),
    immagine        TEXT,
    numero_atto     TEXT,
    tipo            TEXT,
    anno            INTEGER,
    data_atto       TEXT,
    data_evento     TEXT,
    ora_evento      TEXT,
    luogo           TEXT,
    luogo_letto     TEXT,
    testo_integrale TEXT,
    affidabilita    TEXT,
    incertezze      TEXT
);

-- 'nome' e 'cognome' sono le forme su cui si conta; 'nome_letto' e
-- 'cognome_letto' conservano cio' che c'era scritto sulla pagina, con il
-- marcatore di dubbio compreso. Tenere le due cose separate e' quello che
-- permette di normalizzare senza distruggere: la lettura originale resta
-- sempre ispezionabile, e ogni normalizzazione e' reversibile.
CREATE TABLE IF NOT EXISTS persone (
    id            INTEGER PRIMARY KEY,
    atto          INTEGER REFERENCES atti(id),
    ruolo         TEXT,
    nome          TEXT,
    nome_letto    TEXT,
    cognome       TEXT,
    cognome_letto TEXT,
    incerto       INTEGER NOT NULL DEFAULT 0,
    eta           TEXT,
    professione   TEXT,
    residenza     TEXT,
    residenza_letta TEXT,
    stato_vitale  TEXT,
    note          TEXT
);

-- Le voci di indice sono una seconda lettura degli stessi cognomi degli
-- atti: stanno in una tabella a parte perche' la fase di revisione le
-- confronta con quelle lette negli atti dello stesso registro.
CREATE TABLE IF NOT EXISTS voci_indice (
    id          INTEGER PRIMARY KEY,
    registro    TEXT REFERENCES registri(slug),
    immagine    TEXT,
    numero_atto TEXT,
    nome        TEXT,
    cognome     TEXT,
    cognome_letto TEXT
);

CREATE INDEX IF NOT EXISTS idx_indice_registro ON voci_indice(registro);
CREATE INDEX IF NOT EXISTS idx_atti_anno    ON atti(anno);
CREATE INDEX IF NOT EXISTS idx_atti_tipo    ON atti(tipo);
CREATE INDEX IF NOT EXISTS idx_pers_cognome ON persone(cognome);
CREATE INDEX IF NOT EXISTS idx_pers_atto    ON persone(atto);

CREATE VIRTUAL TABLE IF NOT EXISTS atti_fts USING fts5(
    testo_integrale, numero_atto, luogo, content='atti', content_rowid='id'
);
"""


def leggi_trascrizioni(cartella: Path) -> Iterator[dict[str, Any]]:
    """Scorre i JSON prodotti dalla fase di trascrizione."""
    for percorso in sorted(cartella.rglob("*.json")):
        if percorso.name.startswith("_"):
            continue
        try:
            yield json.loads(percorso.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("%s: JSON illeggibile (%s)", percorso, exc)


def _anno_da_data(data: str | None, ripiego: int | None) -> int | None:
    if data:
        match = re.search(r"\b(1[78]\d{2}|19\d{2})\b", data)
        if match:
            return int(match.group(1))
    return ripiego


def _unifica_varianti(conn: sqlite3.Connection) -> list[normalizza.Proposta]:
    """Riconduce le grafie della stessa famiglia a una forma sola.

    Gira **a caricamento finito**, e non potrebbe essere altrimenti: una
    variante si giudica per quanto ricorre nell'intero corpus, non dentro
    la pagina o l'anno in cui capita. Una forma vista due volte in una
    pagina e quindici altrove e' un'altra cosa da una vista due volte e
    basta.

    Tocca solo la colonna ``cognome``: ``cognome_letto`` conserva sempre
    cio' che c'era scritto sulla carta, quindi ogni unificazione resta
    ispezionabile e reversibile. Le varianti su cui il calcolo non se la
    sente non vengono toccate: tornano indietro come proposte per il
    glossario, dove decide chi conosce il paese.
    """
    frequenze = Counter(
        {
            cognome: quanti
            for cognome, quanti in conn.execute(
                "SELECT cognome, COUNT(*) FROM persone "
                "WHERE cognome IS NOT NULL AND cognome <> '' GROUP BY cognome"
            )
        }
    )
    correzioni, proposte = normalizza.raggruppa_varianti(frequenze)

    for variante, canonica in correzioni.items():
        conn.execute(
            "UPDATE persone SET cognome = ? WHERE cognome = ?", (canonica, variante)
        )
        # Le voci d'indice vanno unificate con la stessa mappa: sono la
        # stessa famiglia letta da un'altra pagina, e normalizzarne una
        # sola farebbe divergere proprio il confronto che la fase 5 usa
        # come segnale piu' affidabile.
        conn.execute(
            "UPDATE voci_indice SET cognome = ? WHERE cognome = ?", (canonica, variante)
        )

    if correzioni:
        logger.info("Varianti unificate da sole: %d", len(correzioni))
    if proposte:
        logger.info("Varianti da decidere nel glossario: %d", len(proposte))
    return proposte


def costruisci(config: Config) -> Path:
    """Costruisce il database SQLite e i CSV; restituisce il percorso del database."""
    config.dataset.mkdir(parents=True, exist_ok=True)
    percorso_db = config.dataset / "torrebruna.sqlite"
    if percorso_db.exists():
        percorso_db.unlink()  # ricostruzione integrale: la fonte sono i JSON

    conn = sqlite3.connect(percorso_db)
    conn.executescript(SCHEMA_SQL)

    # Le forme attestate del paese entrano qui: sono l'unica cosa che
    # raddrizza le letture sbagliate in modo concorde, che nessuna
    # statistica puo' scoprire perche' non c'e' nessun disaccordo da
    # rilevare.
    glossario_locale = glossario.Glossario.carica(config.glossario)
    corrette: Counter[str] = Counter()

    def _con_glossario(valore: str | None, campo: str) -> str | None:
        pulito, _ = normalizza.separa_incertezza(valore)
        corretto, applicate = glossario_locale.correggi_campo(pulito, campo)
        for c in applicate:
            corrette[f"{c.letto} -> {c.corretto}"] += 1
        return corretto

    n_atti = n_persone = n_pagine = n_voci = 0
    for pagina in leggi_trascrizioni(config.trascrizioni):
        origine = pagina.get("_origine", {})
        n_pagine += 1
        conn.execute(
            "INSERT OR IGNORE INTO registri VALUES (?,?,?,?,?)",
            (
                origine.get("registro"),
                origine.get("anno"),
                origine.get("tipologia"),
                origine.get("contesto"),
                origine.get("ark_url"),
            ),
        )
        for voce in pagina.get("voci_indice") or []:
            conn.execute(
                "INSERT INTO voci_indice (registro, immagine, numero_atto, nome, "
                "cognome, cognome_letto) VALUES (?,?,?,?,?,?)",
                (
                    origine.get("registro"),
                    origine.get("immagine"),
                    voce.get("numero_atto"),
                    # Le voci d'indice servono a un solo scopo: essere
                    # confrontate con i cognomi letti negli atti. Un
                    # marcatore di dubbio attaccato al valore farebbe
                    # fallire il confronto proprio dove funziona meglio.
                    normalizza.separa_incertezza(voce.get("nome"))[0],
                    _con_glossario(voce.get("cognome"), "cognome"),
                    voce.get("cognome"),
                ),
            )
            n_voci += 1

        for atto in pagina.get("atti") or []:
            cursore = conn.execute(
                """INSERT INTO atti (registro, immagine, numero_atto, tipo, anno,
                                     data_atto, data_evento, ora_evento, luogo,
                                     luogo_letto, testo_integrale, affidabilita,
                                     incertezze)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    origine.get("registro"),
                    origine.get("immagine"),
                    atto.get("numero_atto"),
                    atto.get("tipo"),
                    _anno_da_data(atto.get("data_atto"), origine.get("anno")),
                    atto.get("data_atto"),
                    atto.get("data_evento"),
                    atto.get("ora_evento"),
                    _con_glossario(atto.get("luogo"), "luogo"),
                    atto.get("luogo"),
                    atto.get("testo_integrale"),
                    atto.get("affidabilita"),
                    "; ".join(atto.get("parti_illeggibili") or []) or None,
                ),
            )
            id_atto = cursore.lastrowid
            n_atti += 1
            for persona in atto.get("persone") or []:
                nome, nome_incerto = normalizza.separa_incertezza(persona.get("nome"))
                _, cognome_incerto = normalizza.separa_incertezza(persona.get("cognome"))
                cognome = _con_glossario(persona.get("cognome"), "cognome")
                conn.execute(
                    """INSERT INTO persone (atto, ruolo, nome, nome_letto,
                                            cognome, cognome_letto, incerto, eta,
                                            professione, residenza, residenza_letta,
                                            stato_vitale, note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        id_atto,
                        persona.get("ruolo"),
                        nome,
                        persona.get("nome"),
                        cognome,
                        persona.get("cognome"),
                        int(nome_incerto or cognome_incerto),
                        persona.get("eta"),
                        persona.get("professione"),
                        # La residenza e' una frase intera che contiene il
                        # toponimo: "in questo Comune di Torrebruna, strada
                        # a piedi della Lama di Nuorro". Il glossario
                        # corregge dentro la frase, non l'intero campo.
                        _con_glossario(persona.get("residenza"), "luogo"),
                        persona.get("residenza"),
                        persona.get("stato_vitale"),
                        persona.get("note"),
                    ),
                )
                n_persone += 1

    proposte = _unifica_varianti(conn)

    conn.execute(
        "INSERT INTO atti_fts(rowid, testo_integrale, numero_atto, luogo) "
        "SELECT id, testo_integrale, numero_atto, luogo FROM atti"
    )
    conn.commit()
    logger.info(
        "Database: %d pagine, %d atti, %d persone, %d voci di indice",
        n_pagine, n_atti, n_persone, n_voci,
    )

    if corrette:
        logger.info(
            "Glossario applicato: %s",
            ", ".join(f"{k} ({n})" for k, n in sorted(corrette.items())),
        )

    _esporta_csv(conn, config.dataset)
    (config.dataset / "sintesi.md").write_text(sintesi(conn, config), encoding="utf-8")
    _scrivi_proposte(config, proposte)
    conn.close()
    return percorso_db


def _scrivi_proposte(config: Config, proposte: list[normalizza.Proposta]) -> Path:
    """Scrive le varianti da decidere gia' in forma di glossario.

    Il file e' pronto da incollare in ``glossario-*.yaml``: chi decide non
    deve ricopiare niente, solo cancellare le righe che non gli tornano.
    Le forme sono ordinate per quanto ricorrono, perche' una vista sei
    volte merita attenzione e una vista una volta sola quasi mai.
    """
    percorso = config.dataset / "glossario-proposto.yaml"
    if not proposte:
        percorso.write_text(
            "# Nessuna variante da decidere: il calcolo se l'e' cavata da solo.\n",
            encoding="utf-8",
        )
        return percorso

    righe = [
        "# Varianti su cui il calcolo non se la sente di decidere.",
        "#",
        "# Sono forme troppo simili per essere famiglie estranee e troppo alla",
        "# pari per stabilire quale sia la lettura buona. Qui serve chi conosce",
        "# il paese: un cognome raro puo' essere una lettura sbagliata, ma anche",
        "# un forestiero vero — una sposa di un comune vicino, un soldato, un",
        "# prete — e quello e' un dato storico, non rumore.",
        "#",
        "# Per accettare una proposta, sposta la riga sotto 'cognomi:' nel",
        "# glossario. Per rifiutarla, cancellala: se la forma e' giusta cosi',",
        "# elencala sotto 'confermati:' e non verra' piu' riproposta.",
        "",
        "cognomi:",
    ]
    for p in proposte:
        righe.append(f"  # {p.motivo}")
        righe.append(f"  {p.proposto}:")
        righe.append(f"    - {p.letto}")
    righe.append("")

    percorso.write_text("\n".join(righe), encoding="utf-8")
    logger.info("Proposte per il glossario: %s", percorso)
    return percorso


def _esporta_csv(conn: sqlite3.Connection, cartella: Path) -> None:
    for tabella in ("atti", "persone", "voci_indice"):
        cursore = conn.execute(f"SELECT * FROM {tabella}")
        intestazioni = [d[0] for d in cursore.description]
        with (cartella / f"{tabella}.csv").open("w", newline="", encoding="utf-8") as handle:
            scrittore = csv.writer(handle)
            scrittore.writerow(intestazioni)
            scrittore.writerows(cursore)


def sintesi(conn: sqlite3.Connection, config: Config) -> str:
    """Quadro d'insieme in Markdown: il primo sguardo sui dati raccolti."""

    def query(sql: str, *parametri) -> list[tuple]:
        return conn.execute(sql, parametri).fetchall()

    (totale_atti,) = query("SELECT COUNT(*) FROM atti")[0]
    (totale_persone,) = query("SELECT COUNT(*) FROM persone")[0]

    righe = [
        f"# {config.comune}, {config.anno_min}-{config.anno_max}",
        "",
        "Sintesi generata automaticamente dalle trascrizioni degli atti di",
        "stato civile conservati nell'Archivio di Stato di Chieti e pubblicati",
        "sul Portale Antenati.",
        "",
        f"- Atti trascritti: **{totale_atti}**",
        f"- Persone nominate: **{totale_persone}**",
        "",
        "## Atti per tipo",
        "",
        "| tipo | atti |",
        "|---|---|",
    ]
    for tipo, quanti in query(
        "SELECT COALESCE(tipo,'?'), COUNT(*) FROM atti GROUP BY 1 ORDER BY 2 DESC"
    ):
        righe.append(f"| {tipo} | {quanti} |")

    righe += ["", "## Copertura per decennio", "", "| decennio | atti |", "|---|---|"]
    for decennio, quanti in query(
        "SELECT (anno/10)*10, COUNT(*) FROM atti WHERE anno IS NOT NULL "
        "GROUP BY 1 ORDER BY 1"
    ):
        righe.append(f"| {decennio}-{decennio + 9} | {quanti} |")

    anni_presenti = {a for (a,) in query("SELECT DISTINCT anno FROM atti WHERE anno IS NOT NULL")}
    lacune = [a for a in range(config.anno_min, config.anno_max + 1) if a not in anni_presenti]
    if lacune:
        righe += [
            "",
            "## Anni senza atti trascritti",
            "",
            "Sono le lacune della serie: registri mai versati all'archivio,",
            "perduti, non ancora digitalizzati, oppure semplicemente non",
            "compresi in questa raccolta.",
            "",
            _compatta_intervalli(lacune),
        ]

    righe += ["", "## Cognomi piu' frequenti", "", "| cognome | ricorrenze |", "|---|---|"]
    for cognome, quanti in query(
        "SELECT cognome, COUNT(*) FROM persone WHERE cognome IS NOT NULL AND cognome <> '' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
    ):
        righe.append(f"| {cognome} | {quanti} |")

    righe += ["", "## Mestieri dichiarati", "", "| professione | ricorrenze |", "|---|---|"]
    for professione, quanti in query(
        "SELECT LOWER(professione), COUNT(*) FROM persone "
        "WHERE professione IS NOT NULL AND professione <> '' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
    ):
        righe.append(f"| {professione} | {quanti} |")

    affidabilita = Counter(
        dict(query("SELECT COALESCE(affidabilita,'?'), COUNT(*) FROM atti GROUP BY 1"))
    )
    righe += [
        "",
        "## Qualita' della trascrizione",
        "",
        "Dichiarata dal modello atto per atto. Gli atti a affidabilita' bassa",
        "vanno riletti sull'immagine originale prima di usarli.",
        "",
        "| affidabilita | atti |",
        "|---|---|",
    ]
    for livello, quanti in affidabilita.most_common():
        righe.append(f"| {livello} | {quanti} |")

    return "\n".join(righe) + "\n"


def _compatta_intervalli(anni: list[int]) -> str:
    """1810, 1811, 1812, 1820 -> '1810-1812, 1820'."""
    if not anni:
        return "nessuno"
    gruppi: list[str] = []
    inizio = precedente = anni[0]
    for anno in anni[1:]:
        if anno == precedente + 1:
            precedente = anno
            continue
        gruppi.append(str(inizio) if inizio == precedente else f"{inizio}-{precedente}")
        inizio = precedente = anno
    gruppi.append(str(inizio) if inizio == precedente else f"{inizio}-{precedente}")
    return ", ".join(gruppi)
