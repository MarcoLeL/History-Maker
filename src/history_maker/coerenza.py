"""Fase 6c: l'albero non puo' uscire da qui contenendo cose impossibili.

Le fasi precedenti fanno del loro meglio e poi scrivono. Questa guarda
cio' che hanno scritto e toglie quello che non puo' stare in piedi: un
bambino con due padri, una donna che partorisce due volte a tre mesi, un
uomo che sposa sua sorella. Non sono dubbi da segnalare a chi consulta —
sono affermazioni false, e un archivio non deve contenerne.

Il criterio non e' "nel dubbio togli". E' **tieni la prova migliore e
togli quella peggiore**, dove la qualita' della prova si misura con una
scala che i registri stessi suggeriscono:

* l'atto di nascita di quel bambino e' la prova migliore che esista sui
  suoi genitori: e' scritto il giorno del parto, dal padre che va a
  dichiararlo;
* l'atto di matrimonio o di morte **di quella stessa persona** viene
  dopo: nomina i genitori a distanza di anni o decenni, spesso a memoria
  di terzi, ed e' la fonte di quasi tutte le letture rovinate;
* qualunque altro atto viene per ultimo.

A parita', vince il genitore piu' attestato nel resto dell'archivio.

Niente sparisce in silenzio. Ogni legame tolto finisce in ``scartati``
con il motivo e l'atto che lo affermava: la menzione resta al suo posto e
la scheda della persona continua a mostrare la pagina che diceva quella
cosa. Si toglie la **conclusione**, non la fonte — che e' esattamente la
differenza fra correggere un archivio e censurarlo.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import date

from history_maker import qualita

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
DROP TABLE IF EXISTS scartati;

-- Cio' che la ricostruzione aveva concluso e che si e' dovuto togliere
-- perche' rendeva l'albero impossibile. E' la memoria delle correzioni:
-- senza, un archivio 'pulito' non si distingue da un archivio a cui non
-- e' mai stato chiesto niente.
CREATE TABLE scartati (
    id          INTEGER PRIMARY KEY,
    genere      TEXT NOT NULL,      -- 'legame' o 'unione'
    uno         INTEGER,            -- il figlio, o il marito
    altro       INTEGER,            -- il genitore, o la moglie
    tipo        TEXT,
    atto        INTEGER,
    motivo      TEXT NOT NULL
);

CREATE INDEX idx_scartati_uno   ON scartati(uno);
CREATE INDEX idx_scartati_altro ON scartati(altro);
"""


class Archivio:
    """Le poche cose che i controlli chiedono di continuo, lette una volta."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self.conn = conn
        self.individui = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, nome, cognome, sesso, anno_nascita, nascita_origine, "
                "anno_morte, morta_entro, menzioni FROM individui"
            )
        }
        self.atti = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, tipo, anno, data_evento, data_atto FROM atti"
            )
        }
        # In quale atto ciascuno e' il soggetto: e' cio' che distingue
        # "l'atto di nascita di questo bambino" da "un atto qualunque che
        # lo nomina", e su quella distinzione si regge tutta la scala
        # delle prove.
        self.soggetto_in: dict[int, set[int]] = defaultdict(set)
        for r in conn.execute(
            """
            SELECT m.individuo, p.atto, a.tipo
              FROM menzioni m
              JOIN persone p ON p.id = m.persona
              JOIN atti a ON a.id = p.atto
             WHERE p.ruolo IN ('neonato','neonata','defunto','defunta','sposo','sposa')
            """
        ):
            self.soggetto_in[r["individuo"]].add(r["atto"])

    def nome(self, individuo: int) -> str:
        riga = self.individui.get(individuo, {})
        pezzi = " ".join(
            p for p in (riga.get("nome"), riga.get("cognome")) if p
        )
        return f"{pezzi or '?'} [#{individuo}]"

    def forza(self, figlio: int, atto: int | None) -> tuple:
        """Quanto vale, come prova, un legame affermato da questo atto."""
        if atto is None:
            return (0, 0)
        tipo = self.atti.get(atto, {}).get("tipo")
        if atto in self.soggetto_in.get(figlio, ()) and tipo == "nascita":
            valore = 3
        elif atto in self.soggetto_in.get(figlio, ()):
            valore = 2
        else:
            valore = 1
        return (valore, 0)

    def quando(self, atto: int | None) -> date | None:
        riga = self.atti.get(atto or -1)
        if riga is None:
            return None
        for campo in ("data_evento", "data_atto"):
            if riga.get(campo):
                try:
                    return date.fromisoformat(str(riga[campo])[:10])
                except ValueError:
                    continue
        return date(riga["anno"], 7, 1) if riga.get("anno") else None


def _scarta_legami(
    conn: sqlite3.Connection, scarti: list[tuple[int, int, str, int | None, str]]
) -> None:
    for figlio, genitore, tipo, atto, motivo in scarti:
        conn.execute(
            "DELETE FROM legami WHERE figlio = ? AND genitore = ? AND tipo = ?",
            (figlio, genitore, tipo),
        )
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('legame',?,?,?,?,?)",
            (figlio, genitore, tipo, atto, motivo),
        )


# ---------------------------------------------------------------------------
# Le regole
# ---------------------------------------------------------------------------

def un_genitore_solo(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Nessuno ha due padri. Si tiene quello che l'atto migliore dichiara.

    Arrivare qui vuol dire che :mod:`famiglie` non e' riuscito a
    riconoscere i due come la stessa persona — di solito perche' hanno
    davvero due nomi diversi, cioe' perche' due pagine si contraddicono.
    Fra due pagine che si contraddicono si sceglie, e si dice quale.
    """
    per_figlio: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for riga in conn.execute("SELECT figlio, genitore, tipo, atto FROM legami"):
        per_figlio[(riga["figlio"], riga["tipo"])].append(riga)

    scarti = []
    for (figlio, tipo), righe in per_figlio.items():
        if len(righe) < 2:
            continue
        ordinate = sorted(
            righe,
            key=lambda r: (
                archivio.forza(figlio, r["atto"]),
                archivio.individui.get(r["genitore"], {}).get("menzioni", 0),
                -r["genitore"],
            ),
            reverse=True,
        )
        tenuto = ordinate[0]
        for riga in ordinate[1:]:
            scarti.append((
                figlio, riga["genitore"], tipo, riga["atto"],
                f"{archivio.nome(figlio)} aveva {len(righe)} {tipo}i; tenuto "
                f"{archivio.nome(tenuto['genitore'])}, che lo dichiara l'atto "
                f"piu' vicino alla nascita",
            ))
    _scarta_legami(conn, scarti)
    return len(scarti)


def parti_possibili(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Due parti della stessa donna a meno di nove mesi, e non gemelli.

    Uno dei due non e' suo. Si toglie quello affermato dall'atto piu'
    debole; se pari, quello piu' lontano dagli altri parti, perche' e'
    quello che sta peggio nella serie.
    """
    per_madre: dict[int, list[tuple[date, sqlite3.Row]]] = defaultdict(list)
    for riga in conn.execute(
        "SELECT l.figlio, l.genitore, l.tipo, l.atto FROM legami l "
        "JOIN atti a ON a.id = l.atto WHERE a.tipo = 'nascita' AND l.tipo = 'madre'"
    ):
        quando = archivio.quando(riga["atto"])
        if quando is not None:
            per_madre[riga["genitore"]].append((quando, riga))

    scarti = []
    for madre, elenco in per_madre.items():
        if archivio.individui.get(madre, {}).get("sesso") != "F":
            continue
        elenco.sort(key=lambda v: v[0])
        tolti: set[int] = set()
        for (prima, una), (dopo, altra) in zip(elenco, elenco[1:]):
            if una["figlio"] in tolti:
                continue
            distanza = (dopo - prima).days
            if not 0 < distanza < qualita.GIORNI_FRA_DUE_PARTI:
                continue
            debole = min(
                (una, altra),
                key=lambda r: (
                    archivio.forza(r["figlio"], r["atto"]),
                    archivio.individui.get(r["figlio"], {}).get("menzioni", 0),
                ),
            )
            tolti.add(debole["figlio"])
            scarti.append((
                debole["figlio"], madre, "madre", debole["atto"],
                f"{archivio.nome(madre)} avrebbe partorito a {distanza} giorni "
                f"di distanza: una delle due maternita' non e' sua",
            ))
    _scarta_legami(conn, scarti)
    return len(scarti)


def eta_dei_genitori(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Nessuno fa figli a otto anni, ne' a novanta, ne' da morto."""
    scarti = []
    for riga in conn.execute(
        "SELECT l.figlio, l.genitore, l.tipo, l.atto, a.anno AS anno_parto "
        "FROM legami l LEFT JOIN atti a ON a.id = l.atto"
    ):
        genitore = archivio.individui.get(riga["genitore"], {})
        figlio = archivio.individui.get(riga["figlio"], {})
        nascita_genitore = genitore.get("anno_nascita")
        quando = riga["anno_parto"] if (
            archivio.atti.get(riga["atto"] or -1, {}).get("tipo") == "nascita"
        ) else figlio.get("anno_nascita")
        if nascita_genitore is None or quando is None:
            continue

        eta = quando - nascita_genitore
        limite = (
            qualita.ETA_MASSIMA_MADRE if genitore.get("sesso") == "F"
            else qualita.ETA_MASSIMA_PADRE
        )
        if eta < qualita.ETA_MINIMA_GENITORE:
            scarti.append((
                riga["figlio"], riga["genitore"], riga["tipo"], riga["atto"],
                f"{archivio.nome(riga['genitore'])} avrebbe avuto un figlio a "
                f"{eta} anni",
            ))
            continue
        if eta > limite:
            scarti.append((
                riga["figlio"], riga["genitore"], riga["tipo"], riga["atto"],
                f"{archivio.nome(riga['genitore'])} avrebbe avuto un figlio a "
                f"{eta} anni",
            ))
            continue

        morte = genitore.get("anno_morte")
        margine = qualita.POSTUMO["madre" if genitore.get("sesso") == "F" else "padre"]
        if morte is not None and quando > morte + margine:
            scarti.append((
                riga["figlio"], riga["genitore"], riga["tipo"], riga["atto"],
                f"{archivio.nome(riga['genitore'])} e' morto nel {morte} e il "
                f"figlio nasce nel {quando}",
            ))
    _scarta_legami(conn, scarti)
    return len(scarti)


def arco_dei_parti(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Una donna madre per piu' di trentacinque anni sono due donne.

    Non potendo dividerle qui, si toglie il parto piu' lontano dal grosso
    della serie, e si continua finche' l'arco non rientra.
    """
    scarti = []
    while True:
        per_madre: dict[int, list[tuple[int, sqlite3.Row]]] = defaultdict(list)
        for riga in conn.execute(
            "SELECT l.figlio, l.genitore, l.tipo, l.atto, a.anno FROM legami l "
            "JOIN atti a ON a.id = l.atto "
            "WHERE a.tipo = 'nascita' AND l.tipo = 'madre'"
        ):
            per_madre[riga["genitore"]].append((riga["anno"], riga))

        fatto = []
        for madre, elenco in per_madre.items():
            if archivio.individui.get(madre, {}).get("sesso") != "F":
                continue
            anni = sorted(a for a, _ in elenco)
            if len(anni) < 2 or anni[-1] - anni[0] <= qualita.ARCO_MASSIMO_DEI_PARTI:
                continue
            mediana = anni[len(anni) // 2]
            lontano = max(elenco, key=lambda v: abs(v[0] - mediana))
            fatto.append((
                lontano[1]["figlio"], madre, "madre", lontano[1]["atto"],
                f"{archivio.nome(madre)} risultava madre dal {anni[0]} al "
                f"{anni[-1]}, oltre una vita fertile",
            ))
        if not fatto:
            break
        _scarta_legami(conn, fatto)
        scarti.extend(fatto)
    return len(scarti)


def coniugi_non_parenti(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Nessuno sposa sua sorella o sua figlia.

    Fra cugini si', ed e' normale in un paese di montagna: quello non si
    tocca. Qui si tolgono solo le unioni fra chi risulta anche genitore e
    figlio, o fratello.
    """
    tolte = 0
    for riga in conn.execute(
        """
        SELECT u.id, u.marito, u.moglie, u.atto FROM unioni u
         WHERE u.marito IS NOT NULL AND u.moglie IS NOT NULL
           AND (EXISTS (SELECT 1 FROM legami l
                         WHERE (l.figlio = u.marito AND l.genitore = u.moglie)
                            OR (l.figlio = u.moglie AND l.genitore = u.marito))
             OR EXISTS (SELECT 1 FROM legami a
                          JOIN legami b ON b.genitore = a.genitore
                                       AND b.figlio <> a.figlio
                        WHERE a.figlio = u.marito AND b.figlio = u.moglie))
        """
    ).fetchall():
        conn.execute("DELETE FROM unioni WHERE id = ?", (riga["id"],))
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('unione',?,?,NULL,?,?)",
            (riga["marito"], riga["moglie"], riga["atto"],
             f"{archivio.nome(riga['marito'])} e {archivio.nome(riga['moglie'])} "
             f"risultano anche parenti stretti: l'unione non puo' stare"),
        )
        tolte += 1
    return tolte


def sesso_dai_ruoli(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Due coniugi dello stesso sesso: il sesso e' sbagliato, non la coppia.

    Il sesso non e' scritto sugli atti: si deduce dal nome, e sui nomi
    che finiscono in -a portati da uomini — 'Giovambattista', 'Amadio' —
    la deduzione sbaglia. Il **ruolo** invece e' scritto: chi l'atto
    chiama 'padre' o 'sposo' e' un uomo, chi chiama 'madre' o 'sposa' e'
    una donna, e non c'e' statistica sui nomi che possa contraddirlo.

    Quindi qui non si butta l'unione — sarebbe buttare una famiglia per
    un aggettivo — si corregge il sesso e la si tiene.
    """
    ruoli = {
        "padre": "M", "sposo": "M", "marito": "M",
        "madre": "F", "sposa": "F", "moglie": "F",
    }
    corretti = 0
    for riga in conn.execute(
        """
        SELECT u.marito, u.moglie FROM unioni u
          JOIN individui m ON m.id = u.marito
          JOIN individui f ON f.id = u.moglie
         WHERE m.sesso IS NOT NULL AND m.sesso = f.sesso
        """
    ).fetchall():
        for individuo, atteso in ((riga["marito"], "M"), (riga["moglie"], "F")):
            conteggio: Counter[str] = Counter()
            for r in conn.execute(
                "SELECT p.ruolo FROM menzioni m JOIN persone p ON p.id = m.persona "
                "WHERE m.individuo = ?", (individuo,)
            ):
                sesso = ruoli.get((r["ruolo"] or "").lower())
                if sesso:
                    conteggio[sesso] += 1
            if not conteggio:
                continue
            dichiarato = conteggio.most_common(1)[0][0]
            if archivio.individui.get(individuo, {}).get("sesso") != dichiarato:
                conn.execute(
                    "UPDATE individui SET sesso = ? WHERE id = ?",
                    (dichiarato, individuo),
                )
                archivio.individui[individuo]["sesso"] = dichiarato
                corretti += 1

    # Quello che resta e' un'altra faccenda. Quando anche i ruoli dicono
    # lo stesso sesso — due 'padre' nello stesso atto di nascita —
    # sbagliato non e' il sesso: e' la coppia. Succede dove la divisione
    # della pagina ha unito due atti, e li' l'unico rimedio e' togliere
    # l'unione: due uomini nominati padre nella stessa riga non sono
    # marito e moglie di nessuno.
    for riga in conn.execute(
        """
        SELECT u.id, u.marito, u.moglie, u.atto, m.sesso FROM unioni u
          JOIN individui m ON m.id = u.marito
          JOIN individui f ON f.id = u.moglie
         WHERE m.sesso IS NOT NULL AND m.sesso = f.sesso
        """
    ).fetchall():
        conn.execute("DELETE FROM unioni WHERE id = ?", (riga["id"],))
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('unione',?,?,NULL,?,?)",
            (riga["marito"], riga["moglie"], riga["atto"],
             f"{archivio.nome(riga['marito'])} e {archivio.nome(riga['moglie'])} "
             f"risultano coniugi ma i loro ruoli negli atti dicono lo stesso "
             f"sesso ({riga['sesso']}): non sono una coppia"),
        )
        corretti += 1
    return corretti


def unioni_possibili(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Nessuno si sposa a sei anni o a centosei, e nessuna coppia due volte.

    L'atto di matrimonio e' la fonte piu' ricca dell'archivio — due sposi
    e quattro genitori — quindi attribuirlo alla persona sbagliata sposta
    sei parentele in un colpo, ed e' il motivo per cui questo controllo
    vale piu' del numero di casi che conta.
    """
    tolte = 0
    for riga in conn.execute(
        """
        SELECT u.id, u.marito, u.moglie, u.atto, u.anno,
               i.id AS chi, u.anno - i.anno_nascita AS eta
          FROM unioni u
          JOIN individui i ON i.id IN (u.marito, u.moglie)
         WHERE u.origine = 'matrimonio' AND u.anno IS NOT NULL
           AND i.anno_nascita IS NOT NULL
           AND (u.anno - i.anno_nascita > ? OR u.anno - i.anno_nascita < ?)
        """,
        (qualita.ETA_MASSIMA_SPOSI, qualita.ETA_MINIMA_SPOSI),
    ).fetchall():
        if conn.execute(
            "SELECT COUNT(*) FROM unioni WHERE id = ?", (riga["id"],)
        ).fetchone()[0] == 0:
            continue
        conn.execute("DELETE FROM unioni WHERE id = ?", (riga["id"],))
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('unione',?,?,NULL,?,?)",
            (riga["marito"], riga["moglie"], riga["atto"],
             f"{archivio.nome(riga['chi'])} si sarebbe sposato nel {riga['anno']} "
             f"a {riga['eta']} anni"),
        )
        tolte += 1

    # La stessa coppia scritta due volte, una per verso.
    for riga in conn.execute(
        "SELECT b.id, b.marito, b.moglie FROM unioni a "
        "JOIN unioni b ON a.marito = b.moglie AND a.moglie = b.marito "
        "WHERE a.id < b.id"
    ).fetchall():
        conn.execute("DELETE FROM unioni WHERE id = ?", (riga["id"],))
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('unione',?,?,NULL,NULL,?)",
            (riga["marito"], riga["moglie"],
             "la stessa coppia era memorizzata due volte, a ruoli invertiti"),
        )
        tolte += 1
    return tolte


def date_impossibili(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Chi muore prima di nascere, o vive centoventi anni.

    Qui non c'e' un legame da togliere: c'e' un anno sbagliato. Si butta
    quello **stimato** e si tiene quello che viene da un atto; se sono
    documentati tutti e due, si tiene la nascita, perche' l'atto di
    nascita e' della persona e quello di morte puo' essere di un'altra.
    """
    corretti = 0
    for riga in conn.execute(
        "SELECT id, anno_nascita, nascita_origine, anno_morte FROM individui "
        "WHERE anno_nascita IS NOT NULL AND anno_morte IS NOT NULL "
        "AND (anno_morte < anno_nascita OR anno_morte - anno_nascita > ?)",
        (qualita.VITA_MASSIMA,),
    ).fetchall():
        if riga["nascita_origine"] != "certa":
            conn.execute(
                "UPDATE individui SET anno_nascita = NULL, nascita_origine = NULL "
                "WHERE id = ?", (riga["id"],)
            )
        else:
            conn.execute(
                "UPDATE individui SET anno_morte = NULL WHERE id = ?", (riga["id"],)
            )
        corretti += 1

    # 'morta_entro' e' una deduzione — un atto che la nomina come 'fu' —
    # e non puo' contraddire un atto di morte vero.
    corretti += conn.execute(
        "UPDATE individui SET morta_entro = NULL "
        "WHERE anno_morte IS NOT NULL AND morta_entro IS NOT NULL "
        "AND morta_entro < anno_morte"
    ).rowcount
    corretti += conn.execute(
        "UPDATE individui SET morta_entro = NULL "
        "WHERE anno_nascita IS NOT NULL AND morta_entro IS NOT NULL "
        "AND morta_entro < anno_nascita"
    ).rowcount
    return corretti


def nessun_ciclo(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Nessuno e' antenato di se stesso.

    Si rompe il ciclo dal legame piu' debole che lo compone: e' l'unico
    punto in cui questa fase deve scegliere senza avere una regola del
    mondo che la guidi, e allora sceglie la prova peggiore.
    """
    tolti = 0
    while True:
        genitori: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for riga in conn.execute("SELECT figlio, genitore, tipo, atto FROM legami"):
            genitori[riga["figlio"]].append(riga)

        ciclo = _trova_ciclo(genitori)
        if not ciclo:
            return tolti
        debole = min(
            ciclo, key=lambda r: (
                archivio.forza(r["figlio"], r["atto"]),
                archivio.individui.get(r["genitore"], {}).get("menzioni", 0),
            )
        )
        _scarta_legami(conn, [(
            debole["figlio"], debole["genitore"], debole["tipo"], debole["atto"],
            f"{archivio.nome(debole['figlio'])} risultava antenato di se stesso: "
            f"rotto il legame meno documentato del giro",
        )])
        tolti += 1


def _trova_ciclo(genitori: dict[int, list[sqlite3.Row]]) -> list[sqlite3.Row]:
    stato: dict[int, int] = {}
    for partenza in list(genitori):
        if stato.get(partenza):
            continue
        pila = [(partenza, iter(genitori.get(partenza, ())))]
        percorso: list[sqlite3.Row] = []
        in_corso = {partenza}
        while pila:
            nodo, prossimi = pila[-1]
            avanzato = False
            for legame in prossimi:
                su = legame["genitore"]
                if su in in_corso:
                    inizio = next(
                        (i for i, l in enumerate(percorso) if l["figlio"] == su), 0
                    )
                    return percorso[inizio:] + [legame]
                if stato.get(su):
                    continue
                pila.append((su, iter(genitori.get(su, ()))))
                in_corso.add(su)
                percorso.append(legame)
                avanzato = True
                break
            if not avanzato:
                pila.pop()
                in_corso.discard(nodo)
                if percorso:
                    percorso.pop()
                stato[nodo] = 1
    return []


def nessun_morto_redivivo(conn: sqlite3.Connection, archivio: Archivio) -> int:
    """Chi ha un atto di morte non fa da testimone otto anni dopo.

    E' l'unico caso in cui non basta togliere una conclusione: la
    conclusione sbagliata e' **l'identita' stessa**, due persone finite
    in una scheda sola. Domenico Pepe muore a un anno nel 1809 e si sposa
    a trentuno nel 1840: le eta' dichiarate tornano — sarebbe nato nel
    1808 e nel 1809 — ed e' proprio per questo che il riconoscimento li
    ha uniti. Ma un atto di morte e' un documento, e dopo quello non si
    testimonia piu'.

    Qui la scheda si **divide**: le menzioni successive alla morte se ne
    vanno in una persona nuova, con i legami che ne discendono. E' la sola
    operazione di tutto il progetto che disfa invece di unire, e vale la
    pena dire perche' esista solo qui: dividere e' pericoloso — spezza
    famiglie vere — e si puo' fare senza rischio soltanto quando a
    chiederlo e' un fatto scritto su un registro, non una somiglianza.
    """
    segnaposti = ",".join("?" * len(qualita.RUOLI_DA_VIVI))
    da_dividere: dict[int, set[int]] = defaultdict(set)
    for riga in conn.execute(
        f"""
        SELECT m.individuo, m.persona, p.atto
          FROM menzioni m
          JOIN persone p ON p.id = m.persona
          JOIN atti a ON a.id = p.atto
          JOIN individui i ON i.id = m.individuo
         WHERE i.anno_morte IS NOT NULL AND a.anno > i.anno_morte
           AND p.ruolo IN ({segnaposti})
           AND COALESCE(p.stato_vitale, '') NOT LIKE '%fu%'
           AND COALESCE(p.stato_vitale, '') NOT LIKE '%defunt%'
        """,
        qualita.RUOLI_DA_VIVI,
    ):
        da_dividere[riga["individuo"]].add(riga["persona"])

    if not da_dividere:
        return 0

    prossimo = (conn.execute("SELECT MAX(id) FROM individui").fetchone()[0] or 0) + 1
    divisi = 0
    for vecchio, persone in da_dividere.items():
        # Tutte le menzioni degli **stessi atti**, non solo quelle che
        # hanno fatto scattare il controllo: se un uomo e' sposo nel 1840,
        # anche il resto di quell'atto parla di lui e non del bambino.
        segna = ",".join("?" * len(persone))
        atti = [
            r["atto"] for r in conn.execute(
                f"SELECT DISTINCT atto FROM persone WHERE id IN ({segna})",
                list(persone),
            )
        ]
        segna_atti = ",".join("?" * len(atti))
        spostate = [
            r["persona"] for r in conn.execute(
                f"""SELECT m.persona FROM menzioni m JOIN persone p ON p.id = m.persona
                     WHERE m.individuo = ? AND p.atto IN ({segna_atti})""",
                (vecchio, *atti),
            )
        ]
        if not spostate or len(spostate) >= archivio.individui[vecchio]["menzioni"]:
            continue

        anagrafe = conn.execute(
            f"""SELECT p.nome, p.cognome, p.eta, a.anno FROM menzioni m
                 JOIN persone p ON p.id = m.persona JOIN atti a ON a.id = p.atto
                WHERE m.persona IN ({",".join("?" * len(spostate))})
                ORDER BY a.anno LIMIT 1""",
            spostate,
        ).fetchone()
        conn.execute(
            "INSERT INTO individui (id, nome, cognome, sesso, anno_primo, anno_ultimo, "
            "menzioni, fondata_su) VALUES (?,?,?,?,?,?,?,?)",
            (
                prossimo,
                anagrafe["nome"] if anagrafe else None,
                anagrafe["cognome"] if anagrafe else None,
                archivio.individui[vecchio].get("sesso"),
                anagrafe["anno"] if anagrafe else None,
                anagrafe["anno"] if anagrafe else None,
                len(spostate),
                "divisa da un omonimo gia' morto",
            ),
        )
        conn.execute(
            f"UPDATE menzioni SET individuo = ? WHERE persona IN "
            f"({','.join('?' * len(spostate))})",
            (prossimo, *spostate),
        )
        for tabella, colonne in (
            ("legami", ("figlio", "genitore")),
            ("unioni", ("marito", "moglie")),
        ):
            for colonna in colonne:
                conn.execute(
                    f"UPDATE {tabella} SET {colonna} = ? "
                    f"WHERE {colonna} = ? AND atto IN ({segna_atti})",
                    (prossimo, vecchio, *atti),
                )
        conn.execute(
            "UPDATE individui SET menzioni = menzioni - ? WHERE id = ?",
            (len(spostate), vecchio),
        )
        conn.execute(
            "INSERT INTO scartati (genere, uno, altro, tipo, atto, motivo) "
            "VALUES ('identita',?,?,NULL,NULL,?)",
            (vecchio, prossimo,
             f"{archivio.nome(vecchio)} ha un atto di morte e compariva vivo dopo: "
             f"le menzioni successive sono di un omonimo, ora scheda #{prossimo}"),
        )
        prossimo += 1
        divisi += 1
    return divisi


REGOLE = (
    ("omonimi di un morto, divisi", nessun_morto_redivivo),
    ("un genitore solo per tipo", un_genitore_solo),
    ("eta' e morte dei genitori", eta_dei_genitori),
    ("parti a meno di nove mesi", parti_possibili),
    ("arco dei parti", arco_dei_parti),
    ("coniugi parenti stretti", coniugi_non_parenti),
    ("sesso dedotto dai ruoli", sesso_dai_ruoli),
    ("unioni impossibili", unioni_possibili),
    ("cicli nell'albero", nessun_ciclo),
    ("date impossibili", date_impossibili),
)


def rendi_coerente(conn: sqlite3.Connection) -> Counter[str]:
    """Toglie dall'albero tutto cio' che non puo' essere vero.

    L'ordine conta: prima si riduce ogni figlio a un padre e una madre,
    perche' molte altre impossibilita' sono conseguenze di quella; poi si
    guardano i parti, che dipendono da quali maternita' sono rimaste.
    """
    conn.executescript(SCHEMA_SQL)
    conto: Counter[str] = Counter()
    archivio = Archivio(conn)
    for nome_regola, regola in REGOLE:
        quanti = regola(conn, archivio)
        if quanti:
            conto[nome_regola] = quanti
            logger.info("  %s: %d corretti", nome_regola, quanti)
    return conto
