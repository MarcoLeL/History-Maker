"""Fase 5: dove le trascrizioni probabilmente sbagliano.

Non corregge niente. Segnala, spiega perche', e rimanda all'immagine
originale: in una ricerca storica la decisione su un nome dubbio spetta a
chi guarda la carta, non a una statistica.

Il principio e' che un secolo di atti di un solo comune e' una fonte
molto ridondante, e la ridondanza si puo' sfruttare in tre modi, tutti a
costo zero perche' non richiedono nessuna chiamata al modello:

1. **Frequenze.** I cognomi di un paese sono pochi e ricorrenti. Una
   forma che compare una volta sola ed e' a un passo da una che ricorre
   trecento volte merita un'occhiata.

2. **Gli indici.** I registri hanno pagine di indice che elencano i
   cognomi degli atti di quell'anno: sono una seconda lettura
   indipendente degli stessi nomi. Le discordanze fra indice e atti sono
   il segnale piu' affidabile che ci sia, e non costano nulla.

3. **Le ricorrenze delle persone.** Un padre compare nelle nascite di
   tutti i figli, chi nasce si sposa e muore. Grafie discordanti della
   stessa famiglia si riconciliano fra loro.

Una precisazione che governa tutto il modulo: un cognome raro puo' essere
una lettura errata, ma puo' anche essere un forestiero vero — una sposa
di un paese vicino, un soldato, un prete. Il secondo caso e' un dato
storico prezioso, non rumore, e per questo i risultati sono segnalazioni
da vagliare, mai correzioni applicate.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from history_maker import paleografia
from history_maker.config import Config

logger = logging.getLogger(__name__)

# Una forma vista al massimo questo numero di volte e' "rara".
SOGLIA_RARO = 2
# Una forma vista almeno questo numero di volte fa da riferimento.
SOGLIA_FREQUENTE = 10
# Quanto due forme devono somigliarsi perche' valga la pena segnalarle.
SOGLIA_SOMIGLIANZA = 0.80


@dataclass
class Segnalazione:
    tipo: str
    letto: str
    proposto: str | None
    motivo: str
    occorrenze_lette: int
    occorrenze_proposte: int
    esempi: list[str] = field(default_factory=list)

    def __lt__(self, altra: "Segnalazione") -> bool:
        # Prima le discordanze con l'indice, poi le piu' sbilanciate.
        ordine = {"indice": 0, "frequenza": 1, "variante": 2}
        return (ordine.get(self.tipo, 9), -self.occorrenze_proposte) < (
            ordine.get(altra.tipo, 9), -altra.occorrenze_proposte
        )


def _connessione(config: Config) -> sqlite3.Connection:
    percorso = config.dataset / "torrebruna.sqlite"
    if not percorso.exists():
        raise FileNotFoundError(
            f"Nessun database in {percorso}. Costruiscilo prima:\n"
            f"  python -m history_maker dataset"
        )
    conn = sqlite3.connect(percorso)
    conn.row_factory = sqlite3.Row
    return conn


def frequenze_cognomi(conn: sqlite3.Connection) -> Counter[str]:
    """Quante volte ricorre ciascun cognome negli atti."""
    return Counter(
        riga["cognome"]
        for riga in conn.execute(
            "SELECT cognome FROM persone WHERE cognome IS NOT NULL AND cognome <> ''"
        )
    )


def _riferimenti(conn: sqlite3.Connection, cognome: str, limite: int = 3) -> list[str]:
    """Qualche atto in cui il cognome compare, per poter andare a vedere."""
    righe = conn.execute(
        "SELECT a.anno, a.numero_atto, a.immagine FROM persone p "
        "JOIN atti a ON a.id = p.atto WHERE p.cognome = ? LIMIT ?",
        (cognome, limite),
    ).fetchall()
    return [
        f"{r['anno'] or '?'} atto {r['numero_atto'] or '?'} ({r['immagine'] or '?'})"
        for r in righe
    ]


def cognomi_sospetti(conn: sqlite3.Connection) -> list[Segnalazione]:
    """Forme rare a un passo da forme frequenti.

    Il confronto avviene solo fra raro e frequente: due forme entrambe
    rare non si correggono a vicenda, e due frequenti sono probabilmente
    due famiglie diverse davvero.
    """
    frequenze = frequenze_cognomi(conn)
    rari = [c for c, n in frequenze.items() if n <= SOGLIA_RARO]
    frequenti = [c for c, n in frequenze.items() if n >= SOGLIA_FREQUENTE]

    segnalazioni: list[Segnalazione] = []
    for raro in sorted(rari):
        migliore, punteggio = None, 0.0
        for frequente in frequenti:
            if paleografia.forma_canonica(raro) == paleografia.forma_canonica(frequente):
                migliore, punteggio = frequente, 1.0
                break
            attuale = paleografia.somiglianza(raro, frequente)
            if attuale > punteggio:
                migliore, punteggio = frequente, attuale
        if migliore and punteggio >= SOGLIA_SOMIGLIANZA:
            segnalazioni.append(
                Segnalazione(
                    tipo="frequenza",
                    letto=raro,
                    proposto=migliore,
                    motivo=(
                        f"compare {frequenze[raro]} volta/e ed e' molto vicino a "
                        f"'{migliore}', che ne conta {frequenze[migliore]} "
                        f"(somiglianza {punteggio:.2f})"
                    ),
                    occorrenze_lette=frequenze[raro],
                    occorrenze_proposte=frequenze[migliore],
                    esempi=_riferimenti(conn, raro),
                )
            )
    return segnalazioni


def discordanze_con_indici(conn: sqlite3.Connection) -> list[Segnalazione]:
    """Cognomi che l'indice e gli atti dello stesso registro scrivono diversamente.

    E' il controllo piu' solido del modulo: due letture indipendenti della
    stessa informazione, fatte su pagine diverse dello stesso registro.
    """
    per_registro_atti: dict[str, set[str]] = defaultdict(set)
    for riga in conn.execute(
        "SELECT a.registro, p.cognome FROM persone p JOIN atti a ON a.id = p.atto "
        "WHERE p.cognome IS NOT NULL AND p.cognome <> ''"
    ):
        per_registro_atti[riga["registro"]].add(riga["cognome"])

    per_registro_indice: dict[str, set[str]] = defaultdict(set)
    for riga in conn.execute(
        "SELECT registro, cognome FROM voci_indice "
        "WHERE cognome IS NOT NULL AND cognome <> ''"
    ):
        per_registro_indice[riga["registro"]].add(riga["cognome"])

    segnalazioni: list[Segnalazione] = []
    for registro, dall_indice in sorted(per_registro_indice.items()):
        dagli_atti = per_registro_atti.get(registro, set())
        for cognome in sorted(dall_indice - dagli_atti):
            migliore, punteggio = None, 0.0
            for negli_atti in dagli_atti:
                attuale = paleografia.somiglianza(cognome, negli_atti)
                if attuale > punteggio:
                    migliore, punteggio = negli_atti, attuale
            if migliore and punteggio >= SOGLIA_SOMIGLIANZA:
                segnalazioni.append(
                    Segnalazione(
                        tipo="indice",
                        letto=migliore,
                        proposto=cognome,
                        motivo=(
                            f"negli atti del registro {registro} si legge '{migliore}', "
                            f"ma l'indice dello stesso registro scrive '{cognome}' "
                            f"(somiglianza {punteggio:.2f})"
                        ),
                        occorrenze_lette=0,
                        occorrenze_proposte=0,
                        esempi=[f"registro {registro}"],
                    )
                )
    return segnalazioni


def varianti_della_stessa_forma(conn: sqlite3.Connection) -> list[Segnalazione]:
    """Grafie diverse che si riducono alla stessa forma canonica.

    ``Di Nardo`` / ``Dinardo`` / ``De Nardo`` sono quasi certamente la
    stessa famiglia: qui non c'e' un errore di lettura da correggere, ma
    una normalizzazione da decidere prima di contare le persone.
    """
    frequenze = frequenze_cognomi(conn)
    gruppi: dict[str, list[str]] = defaultdict(list)
    for cognome in frequenze:
        gruppi[paleografia.forma_canonica(cognome)].append(cognome)

    segnalazioni: list[Segnalazione] = []
    for forme in gruppi.values():
        if len(forme) < 2:
            continue
        forme.sort(key=lambda c: -frequenze[c])
        principale = forme[0]
        for variante in forme[1:]:
            segnalazioni.append(
                Segnalazione(
                    tipo="variante",
                    letto=variante,
                    proposto=principale,
                    motivo=(
                        f"stessa forma canonica di '{principale}': probabilmente "
                        f"la stessa famiglia scritta in due modi"
                    ),
                    occorrenze_lette=frequenze[variante],
                    occorrenze_proposte=frequenze[principale],
                    esempi=_riferimenti(conn, variante),
                )
            )
    return segnalazioni


def atti_da_rileggere(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Atti che il modello stesso ha dichiarato incerti."""
    return conn.execute(
        "SELECT anno, numero_atto, immagine, affidabilita, incertezze FROM atti "
        "WHERE affidabilita = 'bassa' OR (incertezze IS NOT NULL AND incertezze <> '') "
        "ORDER BY anno, numero_atto"
    ).fetchall()


def analizza(config: Config) -> tuple[list[Segnalazione], list[sqlite3.Row]]:
    conn = _connessione(config)
    try:
        varianti = varianti_della_stessa_forma(conn)
        # Una forma gia' riconosciuta come variante grafica non va
        # ripetuta fra i sospetti di frequenza: e' la stessa osservazione
        # detta due volte, e un report ridondante si smette di leggere.
        gia_spiegate = {v.letto for v in varianti}
        sospetti = [s for s in cognomi_sospetti(conn) if s.letto not in gia_spiegate]

        segnalazioni = discordanze_con_indici(conn) + sospetti + varianti
        segnalazioni.sort()
        return segnalazioni, atti_da_rileggere(conn)
    finally:
        conn.close()


def scrivi_report(config: Config) -> Path:
    """Scrive ``revisione.md`` e restituisce il percorso."""
    segnalazioni, incerti = analizza(config)
    config.dataset.mkdir(parents=True, exist_ok=True)
    percorso = config.dataset / "revisione.md"

    per_tipo: dict[str, list[Segnalazione]] = defaultdict(list)
    for segnalazione in segnalazioni:
        per_tipo[segnalazione.tipo].append(segnalazione)

    righe = [
        f"# Revisione delle trascrizioni — {config.comune}",
        "",
        "Segnalazioni, non correzioni. Ogni voce va verificata",
        "sull'immagine originale, che resta in `data/immagini/`.",
        "",
        "**Un cognome raro non e' per forza un errore.** Puo' essere una",
        "sposa di un altro paese, un soldato, un prete di passaggio: sono",
        "proprio i casi che raccontano i rapporti fra Torrebruna e i comuni",
        "vicini. Confermare una forma rara e' un risultato quanto correggerla.",
        "",
        f"- Discordanze indice/atti: **{len(per_tipo['indice'])}**",
        f"- Cognomi rari vicini a frequenti: **{len(per_tipo['frequenza'])}**",
        f"- Varianti della stessa forma: **{len(per_tipo['variante'])}**",
        f"- Atti dichiarati incerti dal modello: **{len(incerti)}**",
        "",
    ]

    titoli = {
        "indice": (
            "## Discordanze fra indice e atti",
            "L'indice del registro e gli atti dello stesso registro scrivono lo\n"
            "stesso cognome in due modi. Sono due letture indipendenti: e' il\n"
            "segnale piu' affidabile che questa fase produca.",
        ),
        "frequenza": (
            "## Cognomi rari vicini a cognomi frequenti",
            "Forme che compaiono una o due volte e somigliano molto a forme\n"
            "che ricorrono in tutto il secolo. Da guardare una per una.",
        ),
        "variante": (
            "## Varianti della stessa forma",
            "Grafie diverse dello stesso cognome. Qui di solito non c'e' un\n"
            "errore di lettura, ma una scelta di normalizzazione da fare prima\n"
            "di contare le famiglie.",
        ),
    }

    for tipo in ("indice", "frequenza", "variante"):
        elenco = per_tipo.get(tipo)
        if not elenco:
            continue
        titolo, spiegazione = titoli[tipo]
        righe += ["", titolo, "", spiegazione, "", "| letto | forse | perche' | dove |", "|---|---|---|---|"]
        for s in elenco:
            dove = "; ".join(s.esempi) or "—"
            righe.append(f"| `{s.letto}` | `{s.proposto}` | {s.motivo} | {dove} |")

    if incerti:
        righe += [
            "",
            "## Atti che il modello ha dichiarato incerti",
            "",
            "Segnalati in fase di trascrizione, non da questa analisi.",
            "",
            "| anno | atto | affidabilita | punti dubbi | immagine |",
            "|---|---|---|---|---|",
        ]
        for r in incerti:
            righe.append(
                f"| {r['anno'] or '?'} | {r['numero_atto'] or '?'} | "
                f"{r['affidabilita'] or '?'} | {r['incertezze'] or '—'} | "
                f"`{r['immagine'] or '?'}` |"
            )

    if not segnalazioni and not incerti:
        righe += ["", "Nessuna segnalazione: nulla da rivedere con questi criteri."]

    percorso.write_text("\n".join(righe) + "\n", encoding="utf-8")
    return percorso
