"""Le forme attestate del paese, che la statistica non puo' ricavare.

La fase 5 scopre le letture sbagliate sfruttando la ridondanza dei
registri, ma puo' solo scegliere fra le forme che qualcuno ha letto. Una
grafia letta male **sempre allo stesso modo** produce un consenso
concorde e sbagliato: piu' dati non lo curano, lo consolidano. E una
forma corretta che non compare in nessuna trascrizione e' irraggiungibile
per definizione.

Questo modulo e' il punto in cui entra l'unica cosa che risolve quei
casi: il sapere di chi conosce il paese. Serve a due usi, e il primo vale
piu' del secondo — prevenire l'errore in lettura costa meno che
ripararlo dopo, e soprattutto ripara anche i casi che nessuna statistica
segnalerebbe.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# Le particelle che legano un casato composto: 'di Marco', 'Della Penna',
# 'De Luca'. Servono a non lasciare che una voce del glossario si mangi un
# pezzo di un casato piu' lungo (vedi ``_dentro_un_casato_composto``).
PARTICELLE_CASATO = {"di", "de", "della", "dello", "dei", "degli", "da", "d"}


def _senza_accenti(testo: str) -> str:
    scomposto = unicodedata.normalize("NFD", testo)
    return "".join(c for c in scomposto if unicodedata.category(c) != "Mn")


def _chiave(testo: str) -> str:
    """Forma di confronto: senza accenti, minuscola, spazi compattati."""
    return re.sub(r"\s+", " ", _senza_accenti(testo).lower()).strip()


@dataclass
class Correzione:
    """Una sostituzione applicata, con il perche'."""

    letto: str
    corretto: str
    campo: str


@dataclass
class Glossario:
    toponimi: dict[str, str] = field(default_factory=dict)
    cognomi: dict[str, str] = field(default_factory=dict)
    # I mestieri servono a una cosa che la statistica non sa fare:
    # riconoscere due **parole diverse** per la stessa occupazione.
    # 'Bovajo' e 'bovaro' non si somigliano abbastanza da sembrare due
    # grafie della stessa parola, e infatti non lo sono: sono due modi
    # di dirla, e solo chi conosce il paese puo' dichiararlo.
    mestieri: dict[str, str] = field(default_factory=dict)
    confermati: set[str] = field(default_factory=set)

    @classmethod
    def carica(cls, percorso: Path | str | None) -> "Glossario":
        """Legge il glossario; un file assente e' un glossario vuoto.

        Un progetto nuovo non ha ancora nessuna conoscenza locale da
        dichiarare, e non deve per questo rifiutarsi di partire.
        """
        if percorso is None:
            return cls()
        percorso = Path(percorso)
        if not percorso.exists():
            logger.info("Nessun glossario in %s: procedo senza.", percorso)
            return cls()

        dati = yaml.safe_load(percorso.read_text(encoding="utf-8")) or {}
        return cls(
            toponimi=cls._rovescia(dati.get("toponimi") or {}),
            cognomi=cls._rovescia(dati.get("cognomi") or {}),
            mestieri=cls._rovescia(dati.get("mestieri") or {}),
            confermati={_chiave(v) for v in (dati.get("confermati") or [])},
        )

    @staticmethod
    def _rovescia(voci: dict[str, list[str]]) -> dict[str, str]:
        """Da {corretta: [errate]} a {chiave errata: corretta}.

        Il file e' scritto per chi lo compila — una riga per forma vera,
        con sotto le sue storpiature. La ricerca invece parte sempre da
        cio' che si e' letto, quindi la mappa va rovesciata una volta
        sola, al caricamento.
        """
        mappa: dict[str, str] = {}
        for corretta, errate in voci.items():
            for errata in errate or []:
                mappa[_chiave(errata)] = corretta
            # Anche la forma corretta va riconosciuta, cosi' un valore
            # gia' giusto non viene trattato come sconosciuto.
            mappa.setdefault(_chiave(corretta), corretta)
        return mappa

    # --- uso 1: prevenire l'errore in lettura ------------------------------

    def forme_note(self) -> tuple[list[str], list[str]]:
        """Le forme corrette da mostrare al modello: toponimi e cognomi."""
        return (
            sorted(set(self.toponimi.values())),
            sorted(set(self.cognomi.values())),
        )

    # --- uso 2: correggere cio' che e' gia' stato letto --------------------

    @staticmethod
    def _dentro_un_casato_composto(valore: str, inizio: int, fine: int) -> bool:
        """La forma trovata e' un pezzo di un casato composto piu' lungo?

        Il glossario sostituisce **dentro** il valore, e questo su un
        indirizzo e' giusto. Su un casato no: la voce «Marco» (variante di
        «Mosca») riscriveva «di Marco», che e' una famiglia vera del paese,
        e la moglie di Vincenzo Marianacci finiva nell'albero come «Dorotea
        di Mosca». Lo stesso faceva «Motta» dentro «Di Motta» (diciannove
        righe) e «Della» dentro «Della Penna».

        Due segni bastano a riconoscere il caso, e nessuno dei due tocca il
        casato scritto da solo:

        - la forma viene **dopo una particella** ('di Marco', 'Di Motta') e
          non se la porta dentro;
        - la forma **e' una particella** e il valore continua ('Della
          Penna', 'Di Luca').
        """
        prima = valore[:inizio].split()
        trovato = valore[inizio:fine].split()
        dopo = valore[fine:].split()
        if not trovato:
            return False
        if prima and _chiave(prima[-1]).rstrip("'") in PARTICELLE_CASATO:
            if _chiave(trovato[0]).rstrip("'") not in PARTICELLE_CASATO:
                return True
        if _chiave(trovato[0]).rstrip("'") in PARTICELLE_CASATO and len(trovato) == 1 and dopo:
            return True
        return False

    def correggi_campo(self, valore: str | None, campo: str) -> tuple[str | None, list[Correzione]]:
        """Sostituisce dentro il valore le letture errate note.

        La sostituzione avviene **dentro** il testo, non sull'intero
        campo: la residenza di un atto e' una frase intera ("in questo
        Comune di Torrebruna, strada a piedi della Lama di Nuorro"), e il
        toponimo da correggere ne e' solo un pezzo.

        Le forme piu' lunghe si provano per prime: 'strada Trafficinella'
        prima di 'Trafficinella', altrimenti la seconda mangerebbe la
        prima lasciando 'strada strada Trascinella'.

        Il confronto ignora maiuscole e spaziatura, ma **non** tocca gli
        accenti del valore restituito: normalizzarli per confrontare e poi
        rendere il testo cosi' normalizzato storpierebbe ogni 'citta'' e
        'parrocchia' della frase per correggere un toponimo.
        """
        if not valore:
            return valore, []

        mappa = self.toponimi if campo == "luogo" else self.cognomi
        corretto, applicate = valore, []

        for errata in sorted(mappa, key=len, reverse=True):
            giusta = mappa[errata]
            if giusta == errata:
                continue  # identica: niente da fare
            # Il confronto NON puo' avvenire sulle chiavi normalizzate:
            # 'Porta murella' e 'Porta Murella' hanno la stessa chiave, e
            # scartarle lascerebbe per sempre la minuscola dov'era.
            # re.escape non escapa gli spazi dal Python 3.7, quindi la
            # sostituzione va fatta sul letterale, non sulla sua fuga.
            schema = re.compile(
                r"\b" + r"\s+".join(re.escape(p) for p in errata.split()) + r"\b",
                re.IGNORECASE,
            )
            if campo == "cognome":
                sopravvive = [m for m in schema.finditer(corretto)
                              if not self._dentro_un_casato_composto(corretto, m.start(), m.end())]
                if not sopravvive:
                    continue
                nuovo, quanti = corretto, 0
                for m in reversed(sopravvive):
                    nuovo = nuovo[:m.start()] + giusta + nuovo[m.end():]
                    quanti += 1
            else:
                nuovo, quanti = schema.subn(giusta, corretto)
            # 'quanti' conta i riscontri, non i cambiamenti: la regex e'
            # insensibile alle maiuscole, quindi riconosce anche la forma
            # gia' corretta. Segnalare quella come correzione gonfierebbe
            # il rendiconto di sostituzioni che non hanno cambiato nulla.
            if quanti and nuovo != corretto:
                applicate.append(Correzione(letto=errata, corretto=giusta, campo=campo))
                corretto = nuovo

        return corretto, applicate

    def e_confermato(self, valore: str | None) -> bool:
        """Una forma rara gia' verificata sull'originale non si risegnala."""
        return bool(valore) and _chiave(valore) in self.confermati


# Quanti atti devono nominare tutt'e due le forme perche' non siano piu'
# la stessa parola letta in due modi. Uno puo' essere una pagina
# trascritta male a meta'; tre sono un fatto.
ATTI_INSIEME_PER_DUE_FAMIGLIE = 3


def _quante_volte(testi: list[str], forma: str) -> tuple[int, set[int]]:
    """Le occorrenze di una forma, e la posizione degli atti che la portano.

    Le posizioni, non i testi: due atti possono avere lo stesso testo, e
    confrontarli per contenuto o per identita' li conterebbe una volta
    sola.
    """
    schema = re.compile(r"(?<![A-Za-z])" + re.escape(forma) + r"(?![a-z])")
    trovati = [(i, len(schema.findall(t))) for i, t in enumerate(testi)]
    return (sum(n for _, n in trovati), {i for i, n in trovati if n})


def fusioni_sospette(testi, cognomi: dict[str, list[str]]) -> list[dict]:
    """Le voci del glossario che fondono due famiglie invece di correggere.

    Il glossario e' l'unico posto del progetto dove entra un giudizio
    umano, ed e' percio' l'unico che puo' sbagliare **in silenzio**:
    dichiarare che 'Lozzi' e' una cattiva lettura di 'Torzi' non lascia
    nessuna traccia di errore, riduce le varianti, migliora tutte le
    statistiche — e cancella una famiglia.

    Due prove, e non servono le immagini:

    * **la frequenza**. Una grafia sbagliata piu' diffusa di quella
      giusta non e' una grafia sbagliata. 'Lozzi' compariva 594 volte
      contro le 351 di 'Torzi'.
    * **la coesistenza**. Se le due forme stanno nello stesso atto sono
      due persone, non due letture della stessa: nel n. 376 del 1814,
      «Crescenzo Torzi dichiarante Ferdinando Lozzi Testimone».

    Prende i testi integrali degli atti e le voci ``cognomi`` del file,
    nella forma ``{canonica: [varianti]}``. Rende un elenco ordinato per
    gravita': prima quelle che coesistono di piu'.
    """
    testi = [t for t in testi if t]
    fuori: list[dict] = []
    for canonica, varianti in (cognomi or {}).items():
        volte_canonica, dove_canonica = _quante_volte(testi, canonica)
        for variante in varianti or []:
            volte, dove = _quante_volte(testi, variante)
            if not volte:
                continue        # mai letta: non fonde niente
            insieme = len(dove & dove_canonica)
            if insieme < ATTI_INSIEME_PER_DUE_FAMIGLIE and volte <= volte_canonica:
                continue
            fuori.append({
                "canonica": canonica,
                "variante": variante,
                "volte_variante": volte,
                "volte_canonica": volte_canonica,
                "atti_insieme": insieme,
                "perche": (
                    "compaiono insieme nello stesso atto"
                    if insieme >= ATTI_INSIEME_PER_DUE_FAMIGLIE
                    else "la variante e' piu' frequente della forma canonica"
                ),
            })
    fuori.sort(key=lambda v: (-v["atti_insieme"], -v["volte_variante"]))
    return fuori


# ---------------------------------------------------------------------------
# Le prove che non stanno sull'immagine
# ---------------------------------------------------------------------------
#
# Una forma rara si decide guardando la pagina, ma la pagina non e' l'unica
# prova e spesso non e' la piu' economica. Due prove stanno gia' nei dati,
# e insieme hanno deciso settanta coppie del glossario del 2026 che le
# immagini lasciavano aperte: la stessa persona scritta altrove con la
# forma giusta, e il padre che nello stesso atto porta il casato del figlio.

# Quanti anni possono separare due nascite dedotte dall'eta' perche' siano
# la stessa: i registri arrotondano le eta', e uno o due anni di scarto sono
# la norma.
ANNI_FRA_DUE_NASCITE = 2

# I ruoli di chi e' il soggetto dell'atto, e porta percio' il casato del padre.
SOGGETTI_DELL_ATTO = frozenset({"neonato", "neonata", "defunto", "defunta", "sposo", "sposa"})


def _nascita_dall_eta(riga: dict) -> int | None:
    from history_maker import nomi

    eta = nomi.analizza_eta(riga.get("eta")) if riga.get("eta") else None
    if eta is None or not eta.anni or getattr(eta, "minima", False) or eta.anni < 12:
        return None
    return int(riga["anno"]) - int(eta.anni)


def stessa_persona_altrove(
    righe_letta: list[dict], righe_giusta: list[dict], anni: int = ANNI_FRA_DUE_NASCITE,
) -> list[tuple[dict, dict]]:
    """Le righe della forma letta che sono una persona scritta altrove con la forma giusta.

    «Francesco Detta», trentasei anni nel 1835, e' nato nel 1799; nei
    registri c'e' un Francesco Petta nato nel 1800. «Costanzo Zacilli» e'
    nato nel 1829 come Costanzo Pacilli. La prova non e' nella lettura ma
    nella persona: lo stesso nome di battesimo e lo stesso anno di nascita,
    in un paese di duemila anime, sono lo stesso uomo, e il cognome raro e'
    il suo cognome letto male.

    Serve l'eta' da tutte e due le parti: senza, lo stesso nome non prova
    niente, perche' di Giuseppe e di Maria ce ne sono decine per casato.
    Le righe sono dizionari con ``nome``, ``anno`` ed ``eta``; rende le
    coppie (riga della forma letta, riga della forma giusta).
    """
    from history_maker import paleografia

    per_nome: dict[str, list[tuple[int, dict]]] = {}
    for riga in righe_giusta:
        nascita = _nascita_dall_eta(riga)
        if nascita is not None and riga.get("nome"):
            per_nome.setdefault(paleografia.normalizza(riga["nome"]), []).append((nascita, riga))
    trovate = []
    for riga in righe_letta:
        nascita = _nascita_dall_eta(riga)
        if nascita is None or not riga.get("nome"):
            continue
        for sua, altra in per_nome.get(paleografia.normalizza(riga["nome"]), ()):
            if abs(sua - nascita) <= anni:
                trovate.append((riga, altra))
                break
    return trovate


def famiglia_nello_stesso_atto(
    righe_atto: list[dict], letta: str, giusta: str,
) -> tuple[dict, dict] | None:
    """Il padre e il figlio dello stesso atto, con il casato letto in due modi.

    Nella nascita del 1878 il padre e' letto «Cannunzio» e il neonato
    «Pannunzio»: il figlio legittimo porta il casato del padre, e quindi
    le due letture sono una parola sola. La madre non conta: porta il
    cognome da nubile, che con quello del figlio non c'entra.

    Rende la coppia (padre, figlio) che lo prova, o None.
    """
    from history_maker import paleografia

    cercate = {paleografia.normalizza(letta), paleografia.normalizza(giusta)}

    def forma(riga: dict) -> str:
        return paleografia.normalizza(riga.get("cognome") or "")

    padri = [r for r in righe_atto if (r.get("ruolo") or "") == "padre"]
    figli = [r for r in righe_atto if (r.get("ruolo") or "") in SOGGETTI_DELL_ATTO]
    for padre in padri:
        for figlio in figli:
            if {forma(padre), forma(figlio)} == cercate:
                return padre, figlio
    return None

