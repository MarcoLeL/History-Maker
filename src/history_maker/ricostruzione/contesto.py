"""Il fascicolo compatto: cosa mandare a un modello, e cosa no.

Un caso ambiguo si risolve guardando **il contesto**, non il database. E'
una distinzione che costa: mandare a un modello tutto quello che si sa
sarebbe insieme impossibile — cinquantamila menzioni non entrano in
nessuna finestra — e inutile, perche' la risposta a "questi due sono la
stessa persona?" sta in una ventina di righe, non in cinquantamila.

Un fascicolo contiene, e nient'altro:

* le schede in questione, con nomi, date, mestieri e contrade **datati**;
* la loro cronologia, atto per atto, in ordine;
* i parenti gia' riconosciuti, che sono la prova migliore;
* le prove a favore e contro, con il loro peso;
* le spiegazioni possibili, elencate senza sceglierne una;
* cio' che l'atto dice testualmente, quando serve.

Due cose che il fascicolo **non** fa, e sono le piu' importanti.

Non suggerisce la risposta. Le spiegazioni possibili si elencano tutte,
compresa 'sono due persone diverse', perche' un fascicolo che presenta
solo l'ipotesi della fusione ottiene fusioni.

Non nasconde le contraddizioni. Un modello a cui si danno solo le prove a
favore risponde di si', e la sua risposta non vale niente.
"""

from __future__ import annotations

import json
from collections import Counter

from history_maker.ricostruzione import modello as mod
from history_maker.ricostruzione.scheda import Scheda

VERSIONE_PROMPT = "1.0.0"

# Quante righe di cronologia mettere per scheda. Il tetto serve alle
# schede grosse — il sindaco ne ha 1.371 — dove le prime e le ultime
# dicono tutto e le altre sono ripetizione.
RIGHE_MASSIME = 24


def fascicolo(anomalia: mod.Anomalia, esito) -> dict:
    """Tutto e solo cio' che serve a decidere questo caso."""
    schede = [
        esito.schede[chiave] for chiave in anomalia.individui
        if chiave in esito.schede
    ]
    return {
        "caso": anomalia.tipo,
        "domanda": _domanda(anomalia, schede),
        "descrizione": anomalia.descrizione,
        "schede": [
            _scheda(scheda, esito, _da_dividere(anomalia, schede))
            for scheda in schede
        ],
        "prove_a_favore": (
            [f"{i.dettaglio or i.tipo} ({i.peso:+.2f})" for i in anomalia.evidenza.pro()]
            if anomalia.evidenza else []
        ),
        "prove_contrarie": (
            [f"{i.dettaglio or i.tipo} ({i.peso:+.2f})" for i in anomalia.evidenza.contro()]
            if anomalia.evidenza else []
        ),
        "spiegazioni_possibili": list(anomalia.spiegazioni) or [
            "sono la stessa persona", "sono due persone diverse",
        ],
        "confidenza_del_calcolo": round(anomalia.confidenza, 3),
        "quante_persone_sposta": anomalia.impatto,
    }


def _domanda(anomalia: mod.Anomalia, schede: list[Scheda]) -> str:
    """La domanda in italiano, formulata in modo che 'non so' sia una risposta."""
    nomi = " e ".join(scheda.etichetta() for scheda in schede[:3])
    if _da_dividere(anomalia, schede):
        return (
            f"La scheda di {schede[0].etichetta()} e' una persona sola o due? "
            f"Se sono due, dimmi quali menzioni vanno con quali usando i "
            f"numeri fra parentesi quadre della cronologia."
        )
    if anomalia.tipo in ("DUPLICATE_PERSON", "POSSIBLE_HOMONYM"):
        return (
            f"{nomi} sono la stessa persona o persone diverse? "
            f"Se le prove non bastano, dillo."
        )
    if anomalia.tipo in ("SURNAME_ANOMALY", "NAME_ANOMALY"):
        return (
            f"Il {anomalia.campo or 'nome'} di {nomi} e' una lettura sbagliata, "
            f"oppure e' un dato vero da conservare?"
        )
    if anomalia.tipo == "AGE_ANOMALY":
        return f"L'eta' dichiarata di {nomi} e' compatibile con il resto?"
    if anomalia.tipo == "MARITAL_ANOMALY":
        return f"Come si spiegano i due coniugi contemporanei di {nomi}?"
    return f"Come si spiega questo caso su {nomi}?"


def _da_dividere(anomalia: mod.Anomalia, schede: list) -> bool:
    """Se il caso chiede di dividere una scheda invece di confrontarne due.

    E' l'altra meta' del lavoro dell'arbitro, e per un po' e' mancata:
    sapeva rispondere 'sono la stessa persona' e non 'questa scheda ne
    contiene due'. I casi che lo chiedono sono quelli su **una** scheda
    sola: un arco di vita che nessuno copre, due coniugi negli stessi
    anni.
    """
    return (
        anomalia.tipo in ("IDENTITY_ANOMALY", "MARITAL_ANOMALY")
        and len(schede) >= 1
        and schede[0].quante > 2
    )


def _scheda(scheda: Scheda, esito, numerata: bool = False) -> dict:
    """Una scheda ridotta a cio' che serve per decidere."""
    return {
        "id": scheda.chiave,
        "nome": scheda.nome_migliore(),
        "cognome": scheda.cognome_migliore(),
        "sesso": scheda.sesso,
        "nascita": (
            {"anno": scheda.nascita_certa, "fonte": "atto di nascita"}
            if scheda.nascite_certe
            else {"anno": scheda.anno_nascita, "fonte": "eta' dichiarate"}
        ),
        "morte": scheda.morte,
        "gia_morta_entro": scheda.morta_entro,
        "letture_del_nome": _forme(scheda.nomi),
        "letture_del_cognome": _forme(scheda.cognomi),
        "mestieri": _serie(scheda, "professione"),
        "contrade": _serie(scheda, "contrada"),
        "parenti": _parenti(scheda, esito),
        "cronologia": _cronologia(scheda, numerata),
        "quante_menzioni": scheda.quante,
        "su_cosa_si_regge": scheda.fondamento(),
    }


def _forme(contatore: Counter) -> list[str]:
    return [f"{forma} ({quante})" for forma, quante in contatore.most_common(6)]


def _serie(scheda: Scheda, campo: str) -> list[str]:
    """Un attributo nel tempo, che e' l'unico modo di leggerlo.

    'bovaro nel 1850, contadino nel 1875' e' una biografia; 'bovaro |
    contadino' e' una contraddizione apparente, ed e' la differenza fra
    far ragionare un modello e confonderlo.
    """
    visti: dict[tuple, None] = {}
    for menzione in sorted(scheda.menzioni, key=lambda m: (m.anno, m.id)):
        valore = menzione.professione if campo == "professione" else (
            menzione.via or menzione.residenza
        )
        if valore:
            visti.setdefault((menzione.anno, valore), None)
    return [f"{anno}: {valore}" for anno, valore in visti]


def _parenti(scheda: Scheda, esito) -> dict:
    def etichette(chiavi):
        return [
            esito.schede[c].etichetta() for c in sorted(chiavi) if c in esito.schede
        ][:8]

    return {
        "padre": etichette(scheda.padri),
        "madre": etichette(scheda.madri),
        "coniuge": etichette(scheda.coniugi),
        "figli": etichette(scheda.figli),
        "padre_scritto": sorted(p.replace("|", " ") for p in scheda.padri_nome)[:4],
        "madre_scritta": sorted(m.replace("|", " ") for m in scheda.madri_nome)[:4],
        "coniuge_scritto": sorted(c.replace("|", " ") for c in scheda.coniugi_nome)[:4],
    }


def _cronologia(scheda: Scheda, numerata: bool = False) -> list[str]:
    """Gli atti in cui compare, in ordine, uno per riga.

    ``numerata`` mette davanti a ogni riga l'identificatore della
    menzione. Serve quando la domanda e' 'questa scheda e' una persona o
    due?': senza un modo di **nominare** le righe, la risposta non si
    puo' applicare, e infatti l'arbitro sapeva unire ma non separare.
    """
    righe = []
    for menzione in sorted(scheda.menzioni, key=lambda m: (m.anno, m.id)):
        pezzi = [
            f"[{menzione.id}]" if numerata else "",
            str(menzione.anno), menzione.tipo_atto, menzione.ruolo,
            f"{menzione.nome_letto or '?'} {menzione.cognome_letto or ''}".strip(),
        ]
        if menzione.eta_letta:
            pezzi.append(f"eta' {menzione.eta_letta}")
        if menzione.professione:
            pezzi.append(menzione.professione)
        if menzione.via or menzione.residenza:
            pezzi.append(menzione.via or menzione.residenza)
        pezzi.append(f"atto {menzione.atto}")
        righe.append(" | ".join(p for p in pezzi if p))
    if len(righe) <= RIGHE_MASSIME:
        return righe
    meta = RIGHE_MASSIME // 2
    return righe[:meta] + [f"… e altre {len(righe) - RIGHE_MASSIME} menzioni"] + righe[-meta:]


# ---------------------------------------------------------------------------
# Il prompt
# ---------------------------------------------------------------------------

SISTEMA = """Sei un genealogista esperto di registri di stato civile italiani
dell'Ottocento, e conosci i modi in cui questi documenti sbagliano: eta'
dichiarate a voce e arrotondate, cognomi resi in grafie diverse dallo
stesso scrivano, secondi nomi che compaiono e scompaiono, il primogenito
che porta il nome del nonno e quindi paesi pieni di omonimi.

Ti viene sottoposto un caso che un calcolo probabilistico non ha saputo
decidere. Il tuo compito NON e' trovare una risposta a tutti i costi: e'
dire quale interpretazione rende l'insieme dei documenti piu' coerente, e
dichiarare quando le prove non bastano.

Regole che non puoi violare:

1. Non inventare nulla. Nessun nome, data, parentela o mestiere che non
   sia nel fascicolo.
2. Le relazioni familiari valgono piu' dei nomi. Stesso coniuge, stessi
   figli, stessi genitori sono prove forti; nome e cognome uguali, in un
   paese di poche famiglie, non provano quasi niente.
3. Un'eta' che non torna di pochi anni non e' una prova contraria: e'
   normale. Un atto di nascita e' invece una data scritta.
4. Un cognome diverso puo' essere un errore di lettura, ma puo' anche
   essere una persona diversa: guarda cosa dice il resto.
5. Se la risposta dipende da come e' scritta una parola sulla pagina,
   dillo e indica quale parola: c'e' modo di andare a guardarla."""

RISPOSTA = """Rispondi con un solo oggetto JSON, senza altro testo:

{
  "decisione": "stessa persona" | "persone diverse" | "non deciso",
  "_nota": "usa 'gruppi' solo quando ti si chiede di dividere una scheda",
  "confidenza": <numero fra 0 e 1>,
  "ragionamento": "<le due o tre frasi che contano>",
  "prove_usate": ["<le prove del fascicolo su cui ti sei basato>"],
  "alternativa": "<l'interpretazione che scarti, e perche'>",
  "gruppi": [[<numeri delle menzioni di una persona>], [<quelle dell'altra>]],
  "verifica_sull_immagine": {
    "serve": true | false,
    "atto": <numero dell'atto da guardare, se serve>,
    "domanda": "<la domanda precisa da fare sull'immagine>",
    "parola": "<la parola attorno a cui ritagliare>"
  }
}"""


def istruzione(fascicoli: list[dict]) -> str:
    """Il testo da mandare al modello, con uno o piu' casi."""
    pezzi = [
        f"Ecco {len(fascicoli)} caso da decidere."
        if len(fascicoli) == 1
        else f"Ecco {len(fascicoli)} casi da decidere, uno per oggetto.",
        "",
    ]
    for numero, dato in enumerate(fascicoli, start=1):
        pezzi.append(f"--- CASO {numero} ---")
        pezzi.append(json.dumps(dato, ensure_ascii=False, indent=1))
        pezzi.append("")
    if len(fascicoli) > 1:
        pezzi.append(
            "Rispondi con un array JSON di oggetti, uno per caso, nello "
            "stesso ordine."
        )
    pezzi.append(RISPOSTA)
    return "\n".join(pezzi)
