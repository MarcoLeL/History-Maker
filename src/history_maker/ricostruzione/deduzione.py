"""I genitori che nessun atto scrive, dedotti dal nome dei nipoti.

Il problema e' di archivio, non di algoritmo: **i registri hanno buchi**.
A Torrebruna mancano i nati dal 1861 al 1865 e i matrimoni del 1888, e
mancheranno sempre. Chi e' nato in quegli anni compare per la prima volta
da adulto, quando porta il proprio figlio in municipio, e nessun atto dira'
mai di chi e' figlio lui. Con il solo criterio «unisci cio' che gli atti
dichiarano», quelle persone restano teste di ramo staccate dall'albero, e
il ramo sotto di loro — sette figli, in un caso — resta appeso al nulla.

La prova che resta quando l'atto manca
--------------------------------------

Nel Mezzogiorno preunitario il nome di battesimo non si sceglie: si
eredita, e in un ordine fisso.

    primo maschio    -> il padre del padre
    prima femmina    -> la madre del padre
    secondo maschio  -> il padre della madre
    seconda femmina  -> la madre della madre

E' una regola sociale, non una legge: si rompe quando un nonno e' vivo e
un altro appena morto, quando un bambino muore e il nome viene riusato
per il successivo, quando la famiglia e' forestiera. Quindi **non prova
niente da sola** — ma un uomo il cui primogenito porta il nome di Tizio
*e* la cui primogenita porta il nome della moglie di Tizio, in un paese
di duemila anime, non e' una coincidenza: sono due estrazioni
indipendenti dallo stesso vocabolario di nomi.

Il caso che ha voluto questo modulo
-----------------------------------

Domenicantonio Lella, nato attorno al 1862, sposato con Clementina Petta,
sette figli fra il 1885 e il 1900. Nessun atto lo dice figlio di
qualcuno: il suo atto di nascita cadrebbe negli anni perduti e il suo
matrimonio cadrebbe nel 1888. Ma:

* il suo primo maschio, nel 1885, si chiama **Nicola Maria**;
* la sua prima femmina, nel 1887, si chiama **Teresa**;
* Nicola Lella (1821-1900) ha per moglie **Teresa Desiderio**;
* Nicola Lella, settantenne, e' il dichiarante nel 1899 alla morte di
  tre di quei bambini;
* Nicola Lella ha un figlio maschio documentato, Domenico, nato e morto
  nel 1855 — e riusare il nome del nonno paterno sul figlio successivo e'
  esattamente cio' che si faceva.

Nessuna di queste cose e' un atto che dica «Domenicantonio e' figlio di
Nicola». Tutte insieme sono piu' di quanto serva.

Cosa questo modulo non fa
-------------------------

Non scrive mai un legame come gli altri. I legami dedotti hanno
``stato = 'dedotto'`` e una confidenza sotto l'unita', l'applicazione li
disegna tratteggiati, e ogni riga lascia nel registro una decisione con
le prove che l'hanno retta. Un albero in cui le deduzioni si confondono
con gli atti non e' un albero genealogico, e' un romanzo.

E non sceglie fra due candidati: se il paese ha due uomini che
soddisfano le stesse prove, il legame non si scrive e resta
un'**anomalia** con i nomi di tutti e due. Il dubbio dichiarato vale piu'
di una risposta a caso.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3

from history_maker import paleografia

logger = logging.getLogger(__name__)

# Quanti anni possono passare fra un padre e un figlio. Il minimo e' la
# maggiore eta' canonica dei registri, il massimo e' largo apposta: in
# questi paesi gli uomini si risposano e fanno figli fino a tardi.
ETA_MINIMA_DEL_PADRE = 18
ETA_MASSIMA_DEL_PADRE = 60

# E quanti fra una madre e un figlio: la nonna che il nome della nipote
# indica deve aver potuto partorirlo. Una seconda moglie sposata tardi
# porta lo stesso nome della prima piu' spesso di quanto si creda.
ETA_MINIMA_DELLA_MADRE = 14
ETA_MASSIMA_DELLA_MADRE = 50

# La confidenza dei legami dedotti, per combinazione di prove. Sono
# numeri scelti, non misurati, e vanno letti come tre gradini: «quasi
# certo», «probabile», «non abbastanza».
CONFIDENZA = {
    ("nonno", "nonna", "presente"): 0.85,
    ("nonno", "nonna"): 0.75,
    ("nonno", "presente"): 0.65,
}

# Sotto questa soglia non si scrive niente: il solo nome del nonno e'
# l'indizio che apre il caso, non quello che lo chiude.
SOGLIA = 0.65


@dataclasses.dataclass
class Proposta:
    """Un padre dedotto, con le prove che lo reggono."""

    figlio: int
    padre: int
    madre: int | None
    confidenza: float
    prove: tuple[str, ...]
    alternative: tuple[int, ...] = ()

    @property
    def sicura(self) -> bool:
        return not self.alternative and self.confidenza >= SOGLIA


def _canonico(cognome: str | None) -> str:
    return paleografia.forma_canonica(cognome or "")


def _stesso_nome(uno: str | None, altro: str | None) -> bool:
    """Due nomi di battesimo sono lo stesso nome?

    Si confrontano i **pezzi**: «Nicola Maria» porta il nome di
    «Nicola», e «Domenico Antonio» quello di «Domenico». E' cosi' che
    funziona l'uso — al nonno si aggiunge un secondo nome, non lo si
    sostituisce.
    """
    def pezzi(nome: str | None) -> set[str]:
        return {
            paleografia.normalizza(parte)
            for parte in (nome or "").split() if len(parte) > 2
        }

    miei, suoi = pezzi(uno), pezzi(altro)
    if not miei or not suoi:
        return False
    return any(
        paleografia.somiglianza(mio, suo) >= 0.85 for mio in miei for suo in suoi
    )


def _archivio(conn: sqlite3.Connection) -> dict:
    """Tutto quello che serve, letto una volta sola."""
    conn.row_factory = sqlite3.Row
    persone = {
        riga["id"]: dict(riga)
        for riga in conn.execute(
            "SELECT id, nome, cognome, sesso, anno_nascita, anno_morte, "
            "       anno_primo, anno_ultimo FROM individui"
        )
    }
    figli: dict[int, list[int]] = {}
    genitori: dict[int, list[int]] = {}
    for riga in conn.execute("SELECT figlio, genitore, tipo FROM legami"):
        figli.setdefault(riga["genitore"], []).append(riga["figlio"])
        genitori.setdefault(riga["figlio"], []).append(riga["genitore"])
    coniugi: dict[int, list[int]] = {}
    for riga in conn.execute("SELECT marito, moglie FROM unioni"):
        if riga["marito"] and riga["moglie"]:
            coniugi.setdefault(riga["marito"], []).append(riga["moglie"])
            coniugi.setdefault(riga["moglie"], []).append(riga["marito"])
    atti: dict[int, set[int]] = {}
    for riga in conn.execute(
        "SELECT m.individuo, p.atto FROM menzioni m JOIN persone p ON p.id = m.persona"
    ):
        atti.setdefault(riga["individuo"], set()).add(riga["atto"])
    per_cognome: dict[str, list[int]] = {}
    for identificatore, persona in persone.items():
        per_cognome.setdefault(_canonico(persona["cognome"]), []).append(identificatore)
    return {
        "persone": persone, "figli": figli, "genitori": genitori,
        "coniugi": coniugi, "atti": atti, "per_cognome": per_cognome,
    }


def _primo(archivio: dict, chi: int, sesso: str) -> dict | None:
    """Il primo figlio di quel sesso, in ordine di nascita."""
    nati = [
        archivio["persone"][f] for f in archivio["figli"].get(chi, ())
        if archivio["persone"].get(f)
        and archivio["persone"][f]["sesso"] == sesso
        and archivio["persone"][f]["anno_nascita"] is not None
    ]
    return min(nati, key=lambda p: p["anno_nascita"]) if nati else None


def proposte(conn: sqlite3.Connection) -> list[Proposta]:
    """I padri che si possono dedurre, con le loro prove.

    Non scrive niente: rende le proposte. Chi le applica e'
    :func:`applica`, e la separazione fra le due cose serve a poterle
    guardare — ``python -m history_maker deduci --elenca`` — prima di
    metterle nell'albero.
    """
    archivio = _archivio(conn)
    persone = archivio["persone"]
    trovate: list[Proposta] = []

    orfani = [
        identificatore for identificatore, persona in persone.items()
        if persona["sesso"] == "M"
        and persona["cognome"]
        and persona["anno_nascita"] is not None
        and identificatore in archivio["figli"]
        and identificatore not in archivio["genitori"]
    ]

    for chi in sorted(orfani):
        persona = persone[chi]
        primogenito = _primo(archivio, chi, "M")
        if primogenito is None:
            continue
        primogenita = _primo(archivio, chi, "F")
        nascita = persona["anno_nascita"]
        miei_atti = archivio["atti"].get(chi, set())
        for figlio in archivio["figli"].get(chi, ()):
            miei_atti |= archivio["atti"].get(figlio, set())

        candidati: list[Proposta] = []
        for altro in archivio["per_cognome"].get(_canonico(persona["cognome"]), ()):
            if altro == chi:
                continue
            suo = persone[altro]
            if suo["sesso"] != "M" or suo["anno_nascita"] is None:
                continue
            eta = nascita - suo["anno_nascita"]
            if not ETA_MINIMA_DEL_PADRE <= eta <= ETA_MASSIMA_DEL_PADRE:
                continue
            if suo["anno_morte"] is not None and suo["anno_morte"] < nascita:
                continue
            if not _stesso_nome(primogenito["nome"], suo["nome"]):
                continue
            # Un figlio non e' il padre di suo padre: senza questo, due
            # omonimi di generazioni vicine si nominano a vicenda.
            if chi in archivio["figli"].get(altro, ()) or altro in archivio["figli"].get(chi, ()):
                continue
            # Ne' il suocero: Giuseppe Ottaviano (1829) riceveva per padre
            # Paolo, che e' il padre scritto di sua moglie Annunziata, e i
            # due sposi diventavano fratelli.
            if any(altro in archivio["genitori"].get(coniuge, ())
                   for coniuge in archivio["coniugi"].get(chi, ())):
                continue

            prove = ["il primogenito porta il suo nome"]
            marchi = ["nonno"]
            madre = None
            for moglie in archivio["coniugi"].get(altro, ()):
                sua = persone.get(moglie)
                # Maria Felicia di Rado, nata nel 1807, sposa nel 1832 il
                # vedovo Pietro Antonio Cicchillitti: non e' la madre di
                # Giuseppe, nato nel 1807 come lei.
                if sua and sua["anno_nascita"] is not None and not (
                    ETA_MINIMA_DELLA_MADRE <= nascita - sua["anno_nascita"] <= ETA_MASSIMA_DELLA_MADRE
                ):
                    continue
                if sua and primogenita and _stesso_nome(primogenita["nome"], sua["nome"]):
                    prove.append(f"la primogenita porta il nome di sua moglie "
                                 f"{sua['nome']} {sua['cognome'] or ''}".strip())
                    marchi.append("nonna")
                    madre = moglie
                    break
            suoi_atti = archivio["atti"].get(altro, set())
            for moglie in archivio["coniugi"].get(altro, ()):
                suoi_atti = suoi_atti | archivio["atti"].get(moglie, set())
            if miei_atti & suoi_atti:
                prove.append("compare negli atti della famiglia")
                marchi.append("presente")

            confidenza = CONFIDENZA.get(tuple(marchi), 0.0)
            if confidenza < SOGLIA:
                continue
            candidati.append(Proposta(
                figlio=chi, padre=altro, madre=madre,
                confidenza=confidenza, prove=tuple(prove),
            ))

        if not candidati:
            continue
        migliore = max(candidati, key=lambda p: p.confidenza)
        pari = [p for p in candidati if p.confidenza == migliore.confidenza]
        if len(pari) > 1:
            migliore = dataclasses.replace(
                migliore, alternative=tuple(sorted(p.padre for p in pari)),
            )
        trovate.append(migliore)
    return trovate


def rapporto(conn: sqlite3.Connection, elenco: list[Proposta]) -> str:
    """Le proposte in Markdown, per guardarle prima di applicarle."""
    conn.row_factory = sqlite3.Row
    def nome(identificatore: int) -> str:
        riga = conn.execute(
            "SELECT nome, cognome, anno_nascita, anno_morte FROM individui WHERE id = ?",
            (identificatore,),
        ).fetchone()
        if riga is None:
            return f"#{identificatore}"
        vissuto = f"{riga['anno_nascita'] or '?'}-{riga['anno_morte'] or ''}"
        return f"{riga['nome']} {riga['cognome'] or ''} ({vissuto}) #{identificatore}"

    righe = ["# I genitori dedotti", ""]
    sicure = [p for p in elenco if p.sicura]
    dubbie = [p for p in elenco if not p.sicura]
    righe.append(f"{len(sicure)} proposte, {len(dubbie)} casi con piu' candidati.")
    righe.append("")
    for proposta in sorted(sicure, key=lambda p: -p.confidenza):
        righe.append(f"## {nome(proposta.figlio)}")
        righe.append(f"padre: **{nome(proposta.padre)}** — confidenza "
                     f"{proposta.confidenza:.2f}")
        if proposta.madre:
            righe.append(f"madre: {nome(proposta.madre)}")
        for prova in proposta.prove:
            righe.append(f"- {prova}")
        righe.append("")
    if dubbie:
        righe += ["## Piu' di un candidato, nessuno scritto", ""]
        for proposta in dubbie:
            alternative = ", ".join(nome(x) for x in proposta.alternative)
            righe.append(f"- {nome(proposta.figlio)}: {alternative}")
    return "\n".join(righe)


def applica(conn: sqlite3.Connection, elenco: list[Proposta]) -> int:
    """Scrive i legami dedotti e li annota nel registro.

    I legami dedotti si riscrivono da capo a ogni ricostruzione, come
    tutti gli altri: la tabella ``legami`` e' un esito, non un archivio.
    Cio' che resta e' la riga nel registro delle decisioni.
    """
    from history_maker.ricostruzione import registro

    scritti = 0
    for proposta in elenco:
        if not proposta.sicura:
            continue
        coppie = [(proposta.padre, "padre")]
        if proposta.madre is not None:
            coppie.append((proposta.madre, "madre"))
        for genitore, tipo in coppie:
            gia = conn.execute(
                "SELECT 1 FROM legami WHERE figlio = ? AND tipo = ?",
                (proposta.figlio, tipo),
            ).fetchone()
            if gia:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO legami (figlio, genitore, tipo, atto, "
                "confidenza, stato) VALUES (?,?,?,NULL,?,'dedotto')",
                (proposta.figlio, genitore, tipo,
                 proposta.confidenza - (0.05 if tipo == "madre" else 0.0)),
            )
            scritti += 1
        registro.annota(
            conn, "deduzione", (proposta.figlio, proposta.padre),
            "nessun atto dice di chi e' figlio: il legame viene dall'uso "
            "del paese, che da' al primo maschio il nome del nonno paterno "
            "e alla prima femmina quello della nonna",
            confidenza=proposta.confidenza, decisore="algoritmo",
            evidenze=proposta.prove,
        )
    for dubbio in anomalie(elenco):
        conn.execute(
            "INSERT INTO anomalie (tipo, individui, descrizione, spiegazioni, "
            "confidenza, impatto, gravita, priorita, stato) "
            "VALUES (?,?,?,?,?,1,'media',?, 'aperta')",
            (dubbio["tipo"], dubbio["individui"], dubbio["descrizione"],
             "uno dei due e' il padre | nessuno dei due lo e'",
             dubbio["confidenza"], dubbio["confidenza"]),
        )
    return scritti


def anomalie(elenco: list[Proposta], numeri: dict | None = None) -> list[dict]:
    """I casi con piu' di un candidato, da mettere nella coda dei dubbi."""
    fuori = []
    for proposta in elenco:
        if proposta.sicura:
            continue
        fuori.append({
            "tipo": "PARENT_GUESS_AMBIGUOUS",
            "individui": json.dumps([proposta.figlio, *proposta.alternative]),
            "descrizione": (
                "il primogenito porta un nome che in paese hanno piu' uomini "
                "dello stesso cognome: il padre non si puo' dedurre senza "
                "sceglierlo a caso"
            ),
            "confidenza": proposta.confidenza,
        })
    return fuori
