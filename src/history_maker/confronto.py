"""Mettere due letture delle stesse pagine una accanto all'altra.

Serve a rispondere alla sola domanda che conta quando si cambia motore:
**questo legge bene come l'altro?** La risposta non puo' venire da una
dichiarazione di chi scrive il codice, e non puo' nemmeno venire dal
confronto con la verita', che nessuno ha: viene dalle pagine che sono
gia' state trascritte una volta.

Da qui la scelta di fondo di questo modulo: **non dice chi ha ragione.**
Quando due letture divergono, una delle due sbaglia — o tutte e due — e
per saperlo bisogna guardare la carta. Quello che il confronto puo' dire,
e che vale il tempo di leggerlo, e' *quanto* e *dove* divergono, e di che
natura sia la divergenza:

- **stessa forma**: le due letture coincidono. Non e' una prova che siano
  giuste, ma due letture indipendenti che sbagliano allo stesso modo sono
  rare, ed e' il segnale piu' forte che si possa avere senza l'originale.
- **vicine**: divergono di uno scambio plausibile per una mano
  dell'Ottocento — la ``f`` che e' una ``ſ`` lunga, ``c`` per ``e``. La
  distanza usata e' quella pesata di :mod:`history_maker.paleografia`,
  non una distanza di edit generica.
- **diverse**: due letture che non si somigliano. E' qui che si guarda.
- **mancanti**: una delle due non ha letto niente. Un buco dichiarato vale
  piu' di un dato inventato, ma se sono molti da una parte sola, quella
  parte sta leggendo peggio.

Il modulo confronta due cartelle di trascrizioni con la stessa struttura,
quindi funziona per due motori diversi ma anche per lo stesso motore a
due risoluzioni, o con e senza glossario. E' il modo di trasformare
"secondo me si legge meglio" in un numero.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from history_maker import paleografia
from history_maker.normalizza import separa_incertezza

# Sotto questa somiglianza due forme sono letture diverse, non varianti
# della stessa. E' la stessa soglia con cui la fase 5 raggruppa le
# varianti di un cognome: usarne una diversa qui vorrebbe dire misurare
# una cosa e correggerne un'altra.
SOGLIA_VICINE = 0.82

# I campi su cui si misura l'accordo. Sono quelli che reggono un albero
# genealogico: chi, di chi, quando. La trascrizione diplomatica non c'e'
# apposta — due lettori bravi la scrivono diversa e sarebbe rumore.
CAMPI_ATTO = ("numero_atto", "tipo", "data_atto")
CAMPI_PERSONA = ("ruolo", "nome", "cognome")


@dataclass
class Divergenza:
    pagina: str
    campo: str
    prima: str | None
    seconda: str | None
    genere: str  # "vicine" | "diverse" | "mancante-a" | "mancante-b"


@dataclass
class Confronto:
    prima: Path
    seconda: Path
    pagine_comuni: int = 0
    solo_prima: list[str] = field(default_factory=list)
    solo_seconda: list[str] = field(default_factory=list)
    accordi: Counter = field(default_factory=Counter)
    divergenze: list[Divergenza] = field(default_factory=list)
    atti_prima: int = 0
    atti_seconda: int = 0
    persone_prima: int = 0
    persone_seconda: int = 0

    @property
    def confronti(self) -> int:
        return sum(self.accordi.values())

    def quota(self, genere: str) -> float:
        return self.accordi[genere] / self.confronti if self.confronti else 0.0


def _carica(cartella: Path) -> dict[str, dict]:
    """Le trascrizioni di una cartella, indicizzate per registro/pagina."""
    pagine: dict[str, dict] = {}
    if not cartella.is_dir():
        return pagine
    for percorso in sorted(cartella.rglob("*.json")):
        if percorso.name.startswith("_"):
            continue
        try:
            pagine[f"{percorso.parent.name}/{percorso.stem}"] = json.loads(
                percorso.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
    return pagine


def _testo(valore) -> str | None:
    """Il valore di un campo, senza il segno di incertezza.

    ``Pizzi`` e ``Pizzi [?]`` sono la stessa lettura, dichiarata con
    fiducia diversa: contarle come divergenti gonfierebbe il disaccordo
    con decine di casi in cui i due motori hanno letto identico e uno solo
    ha avuto il pudore di dirlo.
    """
    if valore is None:
        return None
    testo, _incerto = separa_incertezza(str(valore))
    return testo


def _genere(a: str | None, b: str | None) -> str:
    """Che tipo di rapporto c'e' fra due letture dello stesso campo."""
    if a is None and b is None:
        return "entrambe-vuote"
    if a is None:
        return "mancante-a"
    if b is None:
        return "mancante-b"
    if paleografia.normalizza(a) == paleografia.normalizza(b):
        return "uguali"
    return "vicine" if paleografia.somiglianza(a, b) >= SOGLIA_VICINE else "diverse"


def _appaia_atti(atti_a: list[dict], atti_b: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Accosta gli atti delle due letture.

    Per numero d'atto quando c'e', che e' l'unico identificatore stabile
    su una pagina; per posizione per il resto. Un atto letto da una parte
    sola resta spaiato: **e' un dato, non un fastidio** — se un motore
    vede sistematicamente meno atti dell'altro, e' la cosa piu'
    importante che questo confronto possa dire.
    """
    per_numero_b = {
        n: atto for atto in atti_b if (n := _testo(atto.get("numero_atto"))) is not None
    }
    usati: set[int] = set()
    coppie: list[tuple[dict | None, dict | None]] = []

    for atto in atti_a:
        numero = _testo(atto.get("numero_atto"))
        gemello = per_numero_b.get(numero) if numero else None
        if gemello is not None:
            usati.add(id(gemello))
            coppie.append((atto, gemello))
        else:
            coppie.append((atto, None))

    # Ripiego per posizione: gli atti senza numero, appaiati agli spaiati
    # nell'ordine in cui stanno sulla pagina.
    liberi_b = [a for a in atti_b if id(a) not in usati]
    spaiati = [i for i, (a, b) in enumerate(coppie) if b is None]
    for indice, gemello in zip(spaiati, liberi_b):
        coppie[indice] = (coppie[indice][0], gemello)
        usati.add(id(gemello))

    coppie += [(None, a) for a in atti_b if id(a) not in usati]
    return coppie


def _chiave_persona(persona: dict) -> str:
    return paleografia.normalizza(_testo(persona.get("ruolo")) or "")


def _appaia_persone(a: list[dict], b: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Accosta le persone di due letture dello stesso atto, per ruolo e ordine."""
    per_ruolo: dict[str, list[dict]] = {}
    for persona in b:
        per_ruolo.setdefault(_chiave_persona(persona), []).append(persona)

    coppie: list[tuple[dict | None, dict | None]] = []
    for persona in a:
        candidati = per_ruolo.get(_chiave_persona(persona))
        coppie.append((persona, candidati.pop(0) if candidati else None))
    coppie += [(None, p) for resto in per_ruolo.values() for p in resto]
    return coppie


def _confronta_campi(
    esito: Confronto, pagina: str, campi, a: dict | None, b: dict | None, prefisso: str = ""
) -> None:
    for campo in campi:
        prima = _testo(a.get(campo)) if a else None
        seconda = _testo(b.get(campo)) if b else None
        genere = _genere(prima, seconda)
        if genere == "entrambe-vuote":
            continue
        esito.accordi[genere] += 1
        if genere != "uguali":
            esito.divergenze.append(
                Divergenza(pagina, f"{prefisso}{campo}", prima, seconda, genere)
            )


def confronta(prima: Path, seconda: Path) -> Confronto:
    """Confronta due cartelle di trascrizioni delle stesse pagine."""
    a, b = _carica(prima), _carica(seconda)
    esito = Confronto(prima=prima, seconda=seconda)
    esito.solo_prima = sorted(set(a) - set(b))
    esito.solo_seconda = sorted(set(b) - set(a))

    for chiave in sorted(set(a) & set(b)):
        esito.pagine_comuni += 1
        pagina_a, pagina_b = a[chiave], b[chiave]
        _confronta_campi(esito, chiave, ("tipo_pagina",), pagina_a, pagina_b)

        atti_a = pagina_a.get("atti") or []
        atti_b = pagina_b.get("atti") or []
        esito.atti_prima += len(atti_a)
        esito.atti_seconda += len(atti_b)

        for atto_a, atto_b in _appaia_atti(atti_a, atti_b):
            _confronta_campi(esito, chiave, CAMPI_ATTO, atto_a, atto_b, "atto.")
            persone_a = (atto_a or {}).get("persone") or []
            persone_b = (atto_b or {}).get("persone") or []
            esito.persone_prima += len(persone_a)
            esito.persone_seconda += len(persone_b)
            for persona_a, persona_b in _appaia_persone(persone_a, persone_b):
                _confronta_campi(esito, chiave, CAMPI_PERSONA, persona_a, persona_b, "persona.")

    return esito


# --- il rapporto -----------------------------------------------------------

ETICHETTE = {
    "uguali": "stessa forma",
    "vicine": "vicine (scambio plausibile per la mano)",
    "diverse": "letture diverse",
    "mancante-a": "lette solo dalla seconda",
    "mancante-b": "lette solo dalla prima",
}


def rapporto(esito: Confronto, quante_divergenze: int = 40) -> str:
    """Il confronto in forma leggibile.

    Chiude con le divergenze vere, quelle da guardare sull'originale, e
    non con una percentuale sola: una percentuale dice se preoccuparsi,
    un elenco dice dove.
    """
    if not esito.pagine_comuni:
        return (
            f"Nessuna pagina in comune fra {esito.prima} e {esito.seconda}.\n"
            f"Servono due cartelle di trascrizioni delle STESSE pagine: "
            f"trascrivi le stesse immagini con l'altro motore e riprova."
        )

    righe = [
        "# Confronto fra due letture",
        "",
        f"- prima:   {esito.prima}",
        f"- seconda: {esito.seconda}",
        f"- pagine confrontate: {esito.pagine_comuni}",
    ]
    if esito.solo_prima:
        righe.append(f"- solo nella prima: {len(esito.solo_prima)} pagine")
    if esito.solo_seconda:
        righe.append(f"- solo nella seconda: {len(esito.solo_seconda)} pagine")

    righe += [
        "",
        f"Atti trovati: {esito.atti_prima} nella prima, {esito.atti_seconda} nella seconda.",
        f"Persone: {esito.persone_prima} contro {esito.persone_seconda}.",
        "",
        f"## Accordo su {esito.confronti} campi",
        "",
    ]
    for genere in ("uguali", "vicine", "diverse", "mancante-a", "mancante-b"):
        quanti = esito.accordi[genere]
        if quanti:
            righe.append(f"- {ETICHETTE[genere]}: {quanti} ({esito.quota(genere):.1%})")

    concordi = esito.quota("uguali") + esito.quota("vicine")
    righe += [
        "",
        f"**Accordo sostanziale: {concordi:.1%}** (stessa forma o scambio plausibile).",
        "",
        "Non e' una misura di correttezza: due letture concordi possono",
        "sbagliare insieme, ed e' proprio il caso che il glossario esiste per",
        "risolvere. E' una misura di *quanto le due letture si somigliano*, e",
        "il posto dove guardare sono le righe qui sotto.",
    ]

    vere = [d for d in esito.divergenze if d.genere == "diverse"]
    if vere:
        righe += ["", f"## Le {min(len(vere), quante_divergenze)} divergenze da guardare", ""]
        righe.append("| pagina | campo | prima | seconda |")
        righe.append("|---|---|---|---|")
        for d in vere[:quante_divergenze]:
            righe.append(f"| {d.pagina} | {d.campo} | {d.prima or '—'} | {d.seconda or '—'} |")
        if len(vere) > quante_divergenze:
            righe.append("")
            righe.append(f"…e altre {len(vere) - quante_divergenze}.")

    return "\n".join(righe) + "\n"
