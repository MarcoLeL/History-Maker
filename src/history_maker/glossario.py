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
