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


# ---------------------------------------------------------------------------
# Il contesto di un documento
# ---------------------------------------------------------------------------
#
# Il fascicolo qui sopra ha per unita' **il caso da decidere**: prende
# un'anomalia e ne fa un dossier. Serve all'arbitro, e per quello va bene.
#
# Ma per tornare all'immagine con qualcosa in mano l'unita' e' un'altra:
# **l'atto**. La domanda non e' piu' "questi due sono la stessa persona?"
# ma "cosa dice davvero questa pagina, e dove va d'accordo con quello che
# sappiamo?" — e per farla bisogna mettere davanti al modello chi c'e'
# dentro l'atto, la famiglia che gli si e' ricostruita attorno, e cio' che
# di quella famiglia e' gia' sospetto.
#
# Due regole, e sono le stesse del fascicolo perche' e' lo stesso
# mestiere.
#
# **Il contesto e' un'ipotesi.** Va detto con quella parola. Un modello a
# cui si da' il grafo come dato di fatto rilegge l'immagine finche' non
# torna, e produce una conferma che non vale niente.
#
# **Le cose gia' sospette si dichiarano.** Un contesto che presenta la
# ricostruzione come pulita ottiene conferme; uno che dice "su questa
# persona pendono due dubbi, eccoli" ottiene una lettura.

# Quanti parenti per grado. Il tetto non e' per la finestra del modello —
# ci starebbero — ma per il ragionamento: dieci fratelli elencati sono un
# contesto, quaranta sono rumore in cui la persona giusta si perde.
PARENTI_PER_GRADO = 12

# Quante persone simili proporre. Sono i candidati che il calcolo ha
# gia' considerato e non ha saputo escludere: sopra la mezza dozzina
# smettono di essere candidati e diventano un elenco del paese.
SIMILI_MASSIMI = 6

# I codici del vocabolario degli errori, dai tipi di anomalia che il
# sistema produce. La lista e' aperta per costruzione — ne' 'tipo' ne'
# 'campo' sono vincolati nello schema — e questa mappa serve solo a dire
# la stessa cosa con le parole di chi legge.
CODICE_ERRORE = {
    "DUPLICATE_PERSON": "duplicate_person",
    "POSSIBLE_HOMONYM": "homonym",
    "IDENTITY_ANOMALY": "identity_conflict",
    "NAME_ANOMALY": "name_variant",
    "SURNAME_ANOMALY": "surname_variant",
    "AGE_ANOMALY": "age_inconsistency",
    "DATE_ANOMALY": "chronology_conflict",
    "RELATIONSHIP_ANOMALY": "wrong_parent",
    "MARITAL_ANOMALY": "possible_second_marriage",
    "PROFESSION_ANOMALY": "wrong_profession",
    "ADDRESS_ANOMALY": "wrong_domicile",
    "LOCATION_ANOMALY": "wrong_locality",
    "TRANSCRIPTION_ANOMALY": "transcription_error",
}


def per_documento(conn, atto: int, simili: int = SIMILI_MASSIMI) -> dict:
    """Tutto e solo cio' che serve per tornare su questa pagina.

    Legge dal database invece che da un esito in memoria, e non e' un
    dettaglio: significa che si puo' costruire il contesto di un atto
    senza rifare la ricostruzione: due minuti e mezzo per una domanda su
    una pagina sarebbero il motivo per cui la domanda non si fa.
    """
    riga = conn.execute(
        "SELECT a.*, r.tipologia AS registro_tipologia, r.anno AS registro_anno "
        "FROM atti a LEFT JOIN registri r ON r.slug = a.registro WHERE a.id = ?",
        (atto,),
    ).fetchone()
    if riga is None:
        raise ValueError(f"l'atto {atto} non esiste")

    menzioni = list(conn.execute(
        "SELECT p.*, m.individuo, m.certa, m.confidenza AS confidenza_menzione "
        "FROM persone p LEFT JOIN menzioni m ON m.persona = p.id "
        "WHERE p.atto = ? ORDER BY p.id", (atto,)
    ))
    individui = [m["individuo"] for m in menzioni if m["individuo"] is not None]

    errori = _errori_sospetti(conn, individui, atto)
    return {
        "atto": _atto(riga),
        "trascrizione_precedente": {
            "testo_integrale": riga["testo_integrale"],
            "affidabilita": riga["affidabilita"],
            "incertezze": riga["incertezze"],
            "menzioni": [_menzione(m) for m in menzioni],
        },
        "contesto_genealogico": {
            "_nota": (
                "IPOTESI, non verita'. E' quello che il resto dell'archivio "
                "fa pensare, ricavato da altri atti. Se l'immagine lo "
                "contraddice, ha ragione l'immagine: dillo invece di "
                "aggiustare la lettura perche' torni."
            ),
            "persone": [
                _persona(conn, individuo, ruolo)
                for individuo, ruolo in _ruoli(menzioni)
            ],
            "persone_simili": _simili(conn, individui, simili),
        },
        "possible_errors": errori,
        "domande_aperte": _domande_aperte(errori),
    }


def _atto(riga) -> dict:
    return {
        "id": riga["id"],
        "tipo": riga["tipo"],
        "numero": riga["numero_atto"],
        "anno": riga["anno"],
        "data": riga["data_atto"],
        "data_evento": riga["data_evento"],
        "luogo": riga["luogo"],
        "registro": " ".join(
            str(p) for p in (riga["registro_tipologia"], riga["registro_anno"]) if p
        ) or riga["registro"],
        "immagine": riga["immagine"],
    }


def _ruoli(menzioni) -> list[tuple[int, str]]:
    """Gli individui dell'atto, ciascuno una volta sola, con il loro ruolo.

    Una persona puo' comparire due volte nello stesso atto — il padre che
    e' anche il dichiarante — e va nominata una volta con tutt'e due i
    ruoli, altrimenti il contesto la presenta come due persone e
    suggerisce da solo la risposta sbagliata.
    """
    visti: dict[int, list[str]] = {}
    for menzione in menzioni:
        if menzione["individuo"] is None:
            continue
        ruoli = visti.setdefault(menzione["individuo"], [])
        if menzione["ruolo"] and menzione["ruolo"] not in ruoli:
            ruoli.append(menzione["ruolo"])
    return [(individuo, " e ".join(ruoli)) for individuo, ruoli in visti.items()]


def _menzione(riga) -> dict:
    """Una riga come la trascrizione l'ha resa, con il letto accanto al pulito."""
    voce = {
        "menzione": riga["id"],
        "ruolo": riga["ruolo"],
        "nome": riga["nome"],
        "cognome": riga["cognome"],
        "eta": riga["eta"],
        "professione": riga["professione"],
        "residenza": riga["residenza"],
        "stato_vitale": riga["stato_vitale"],
        "individuo": riga["individuo"],
    }
    # Il letto si mostra solo quando differisce: ripeterlo sempre
    # raddoppia il fascicolo e non aggiunge niente.
    for pulito, letto in (("nome", "nome_letto"), ("cognome", "cognome_letto")):
        if riga[letto] and riga[letto] != riga[pulito]:
            voce[f"{pulito}_come_letto"] = riga[letto]
    if riga["cognome_origine"] and riga["cognome_origine"] != "atto":
        # Un cognome ricavato dal padre non e' scritto sulla pagina, e
        # chiedere all'immagine di confermarlo e' una domanda senza
        # risposta.
        voce["cognome_non_scritto_ma_dedotto_da"] = riga["cognome_origine"]
    if riga["incerto"]:
        voce["lettura_incerta"] = True
    if riga["note"]:
        voce["note"] = riga["note"]
    return voce


def _persona(conn, individuo: int, ruolo: str) -> dict:
    """Chi il sistema crede che sia questa riga, e su cosa lo crede."""
    riga = conn.execute(
        "SELECT * FROM individui WHERE id = ?", (individuo,)
    ).fetchone()
    if riga is None:
        return {"individuo": individuo, "ruolo_nell_atto": ruolo,
                "_nota": "nessuna scheda: questa riga non e' stata assegnata"}

    genitori = _genitori(conn, individuo)
    return {
        "individuo": individuo,
        "ruolo_nell_atto": ruolo,
        "nome": riga["nome"],
        "cognome": riga["cognome"],
        "sesso": riga["sesso"],
        "confidenza_della_scheda": round(riga["confidenza"] or 0.0, 2),
        "stato": riga["stato"],
        "quante_menzioni": riga["menzioni"],
        "su_cosa_si_regge": riga["fondata_su"],
        "nascita": {
            "anno": riga["anno_nascita"],
            "fonte": (
                "atto di nascita" if riga["nascita_origine"] == "certa"
                else "eta' dichiarate negli atti"
            ),
        },
        "morte": {"anno": riga["anno_morte"], "gia_morta_entro": riga["morta_entro"]},
        "letture_del_nome": _varianti(riga["varianti_nome"]),
        "letture_del_cognome": _varianti(riga["varianti_cognome"]),
        "mestieri": _serie_dai_fatti(conn, individuo, "professione"),
        "contrade": _serie_dai_fatti(conn, individuo, "contrada"),
        "genitori": genitori,
        "fratelli": _fratelli(conn, individuo, genitori),
        "coniugi": _coniugi(conn, individuo),
        "figli": _figli(conn, individuo),
        "documenti_collegati": _documenti(conn, individuo),
    }


def _varianti(campo: str | None) -> list[str]:
    """Le grafie lette, che il database tiene separate da barre verticali."""
    if not campo:
        return []
    return [pezzo.strip() for pezzo in campo.split("|") if pezzo.strip()][:6]


def _serie_dai_fatti(conn, individuo: int, tipo: str) -> list[str]:
    """Un attributo nel tempo, dai fatti datati.

    'bovaro nel 1850, contadino nel 1875' e' una biografia; 'bovaro |
    contadino' e' una contraddizione apparente. E' la stessa distinzione
    di ``_serie``, presa dal database invece che dalle menzioni in
    memoria — perche' e' nei fatti che l'anno di ciascun valore vive.
    """
    visti: dict[tuple, None] = {}
    for riga in conn.execute(
        "SELECT anno, COALESCE(interpretato, grezzo) AS valore FROM fatti "
        "WHERE individuo = ? AND tipo = ? AND anno IS NOT NULL "
        "ORDER BY anno", (individuo, tipo)
    ):
        if riga["valore"]:
            visti.setdefault((riga["anno"], riga["valore"]), None)
    return [f"{anno}: {valore}" for anno, valore in visti]


def _etichetta(conn, individuo: int) -> str:
    riga = conn.execute(
        "SELECT nome, cognome, anno_nascita FROM individui WHERE id = ?", (individuo,)
    ).fetchone()
    if riga is None:
        return f"[{individuo}]"
    nome = " ".join(p for p in (riga["nome"], riga["cognome"]) if p) or "?"
    anno = f" n.{riga['anno_nascita']}" if riga["anno_nascita"] else ""
    return f"{nome}{anno} [{individuo}]"


def _genitori(conn, individuo: int) -> dict:
    fuori: dict[str, list] = {}
    for riga in conn.execute(
        "SELECT genitore, tipo, atto, confidenza FROM legami WHERE figlio = ?",
        (individuo,),
    ):
        fuori.setdefault(riga["tipo"], []).append({
            "individuo": riga["genitore"],
            "etichetta": _etichetta(conn, riga["genitore"]),
            "dichiarato_nell_atto": riga["atto"],
        })
    return fuori


def _fratelli(conn, individuo: int, genitori: dict) -> list[dict]:
    """Gli altri figli degli stessi genitori.

    Sono la prova migliore che il contesto possa portare su un caso
    difficile: due schede con gli stessi fratelli sono quasi sempre la
    stessa persona, e due schede con fratelli diversi quasi mai.
    """
    chiavi = [
        voce["individuo"] for voci in genitori.values() for voce in voci
    ]
    if not chiavi:
        return []
    segnaposto = ",".join("?" * len(chiavi))
    righe = conn.execute(
        f"SELECT DISTINCT figlio FROM legami WHERE genitore IN ({segnaposto}) "
        f"AND figlio <> ?", (*chiavi, individuo)
    ).fetchall()
    return [
        {"individuo": r["figlio"], "etichetta": _etichetta(conn, r["figlio"])}
        for r in righe[:PARENTI_PER_GRADO]
    ]


def _coniugi(conn, individuo: int) -> list[dict]:
    """TUTTI i coniugi conosciuti, non il primo.

    Il tetto non c'e' di proposito. Una persona puo' averne avuti due, e
    tagliare la lista a uno vorrebbe dire nascondere al modello proprio
    il fatto su cui gli si sta chiedendo di ragionare; e quando ne
    risultano sei, quello e' il dato — la scheda ha inghiottito piu'
    persone — e va visto.
    """
    fuori = []
    for riga in conn.execute(
        "SELECT id, marito, moglie, anno, atto, origine, confidenza FROM unioni "
        "WHERE marito = ? OR moglie = ? ORDER BY anno", (individuo, individuo)
    ):
        altro = riga["moglie"] if riga["marito"] == individuo else riga["marito"]
        if altro is None:
            continue
        fuori.append({
            "individuo": altro,
            "etichetta": _etichetta(conn, altro),
            "anno": riga["anno"],
            "come_si_sa": (
                "atto di matrimonio" if riga["origine"] == "matrimonio"
                else "compaiono insieme come genitori"
            ),
            "atto": riga["atto"],
        })
    return fuori


def _figli(conn, individuo: int) -> list[dict]:
    righe = conn.execute(
        "SELECT l.figlio, l.tipo, i.anno_nascita FROM legami l "
        "LEFT JOIN individui i ON i.id = l.figlio "
        "WHERE l.genitore = ? ORDER BY i.anno_nascita", (individuo,)
    ).fetchall()
    return [
        {"individuo": r["figlio"], "etichetta": _etichetta(conn, r["figlio"]),
         "come": r["tipo"]}
        for r in righe[:PARENTI_PER_GRADO]
    ]


def _documenti(conn, individuo: int) -> list[str]:
    """Gli altri atti in cui questa persona compare, in ordine.

    Servono a rispondere alla domanda che decide i casi difficili: questa
    vita, messa in fila, sta in piedi?
    """
    righe = conn.execute(
        "SELECT a.id, a.anno, a.tipo, p.ruolo, p.eta FROM menzioni m "
        "JOIN persone p ON p.id = m.persona JOIN atti a ON a.id = p.atto "
        "WHERE m.individuo = ? ORDER BY a.anno, a.id", (individuo,)
    ).fetchall()
    righe = [
        " | ".join(str(p) for p in (
            r["anno"], r["tipo"], r["ruolo"],
            f"eta' {r['eta']}" if r["eta"] else "", f"atto {r['id']}",
        ) if p)
        for r in righe
    ]
    if len(righe) <= RIGHE_MASSIME:
        return righe
    meta = RIGHE_MASSIME // 2
    return righe[:meta] + [f"… e altre {len(righe) - RIGHE_MASSIME}"] + righe[-meta:]


# Quanti dubbi mettere nel contesto. Il tetto non e' per la finestra: e'
# perche' un elenco lungo di sospetti fa lo stesso danno di un elenco
# vuoto. Chi legge smette di distinguere quello che conta.
ERRORI_MASSIMI = 8

# Sotto questa confidenza un duplicato non e' un sospetto: e' il calcolo
# che dice di no. Presentare un 2% come "possibile errore" e' un modo di
# suggerire una risposta sbagliata.
CONFIDENZA_MINIMA = 0.15

# I dubbi che una pagina puo' sciogliere, con il nome del campo da
# guardare. E' la distinzione che ordina questa sezione, e non e' la
# stessa cosa di "riguarda questo atto".
#
# Un dubbio sull'**identita'** — 'questi due sono la stessa persona?' —
# non si risolve guardando l'immagine: la pagina dice cosa c'e' scritto,
# non chi era. Metterlo in cima a un contesto che serve a rileggere
# significa chiedere al modello di indovinare invece che di leggere.
#
# Un dubbio sulla **lettura** invece la pagina lo scioglie, e lo
# scioglie anche quando il calcolo gli da' poca confidenza — anzi
# soprattutto allora.
DUBBI_CHE_LA_PAGINA_SCIOGLIE = {
    "surname_variant": "il cognome",
    "name_variant": "il nome",
    "age_inconsistency": "l'eta' dichiarata",
    "chronology_conflict": "la data",
    "wrong_profession": "la professione",
    "wrong_domicile": "il domicilio",
    "wrong_locality": "il luogo",
    "transcription_error": "la lettura",
}


def _errori_sospetti(conn, individui: list[int], atto: int) -> list[dict]:
    """Cio' che sulla ricostruzione di queste persone e' gia' in dubbio.

    E' la sezione che rende il contesto onesto. Un fascicolo che presenta
    la ricostruzione come pulita ottiene conferme; questo dice "su questa
    persona pendono due dubbi, eccoli, e uno riguarda proprio il cognome
    che ti sto chiedendo di leggere". Le anomalie ci sono gia' tutte in
    tabella, con la loro confidenza e il loro impatto, e fino a qui il
    fascicolo ne portava **una**: quella che lo aveva generato.

    Due scelte fanno la differenza fra un elenco utile e un elenco.

    **Vengono prima i dubbi che nominano questo atto.** Ordinare per sola
    priorita' mette in testa il sindaco: firma milleduecento atti, quindi
    ogni dubbio sulla sua identita' sposta duecento persone e vince su
    tutto — mentre di questa pagina non e' il soggetto. Un dubbio che cita
    l'atto e' un dubbio che la pagina puo' sciogliere, ed e' quello che si
    e' venuti a chiedere.

    **Ci sono anche le correzioni gia' applicate.** Un cognome che
    l'algoritmo ha raddrizzato sul padre non e' una questione chiusa: e'
    esattamente l'inferenza che vale la pena mettere davanti
    all'originale. Escluderle perche' lo stato non e' 'aperta' toglieva
    dal contesto il caso migliore che ci fosse.
    """
    if not individui:
        return []
    dentro, fuori, visti = set(individui), [], set()
    for riga in conn.execute("SELECT * FROM anomalie ORDER BY priorita DESC"):
        try:
            coinvolti = json.loads(riga["individui"])
            atti = json.loads(riga["atti"] or "[]")
        except (TypeError, ValueError):
            continue
        if not set(coinvolti) & dentro or riga["id"] in visti:
            continue
        codice = CODICE_ERRORE.get(riga["tipo"], riga["tipo"].lower())
        su_questa_pagina = atto in atti
        leggibile = codice in DUBBI_CHE_LA_PAGINA_SCIOGLIE and su_questa_pagina
        # Il filtro sulla confidenza vale per i dubbi che la pagina non
        # scioglie. Su quelli che scioglie, la poca confidenza del calcolo
        # e' proprio la ragione per andare a guardare.
        if not leggibile and (riga["confidenza"] or 0.0) < CONFIDENZA_MINIMA:
            continue
        visti.add(riga["id"])
        voce = {
            "codice": codice,
            "tipo": riga["tipo"],
            "campo": riga["campo"],
            "individui": coinvolti,
            "descrizione": riga["descrizione"],
            "spiegazioni_possibili": [
                s for s in (riga["spiegazioni"] or "").split(" | ") if s
            ],
            "prove": riga["prove"],
            "confidenza": round(riga["confidenza"] or 0.0, 2),
            "quante_persone_sposta": riga["impatto"],
            "gravita": riga["gravita"],
            "riguarda_questo_atto": su_questa_pagina,
            "la_pagina_puo_rispondere": leggibile,
        }
        if riga["stato"] != "aperta":
            voce["gia_corretto_dall_algoritmo"] = (
                "il valore mostrato nel contesto e' gia' quello corretto; "
                "la lettura originale e' fra le varianti"
            )
        fuori.append(voce)
    # Prima cio' che la pagina puo' sciogliere, poi il resto per quanto ci
    # si crede. Ordinare per sola priorita' metteva in cima il sindaco:
    # firma milleduecento atti, quindi ogni dubbio sulla sua identita'
    # sposta duecento persone e vince su tutto — mentre di questa pagina
    # non e' il soggetto.
    fuori.sort(key=lambda e: (not e["la_pagina_puo_rispondere"], -e["confidenza"]))
    return fuori[:ERRORI_MASSIMI]


def _simili(conn, individui: list[int], quanti: int) -> list[dict]:
    """Le schede che potrebbero essere le stesse, e che il calcolo non ha escluso.

    Non si cercano per somiglianza di stringa: si prendono da dove sono
    gia' — le anomalie di duplicato e di omonimia, che hanno gia' pesato
    le prove a favore e contro. Cercarle di nuovo per nome darebbe i
    Pelliccia, che in questo paese sono cinquemila menzioni e non sono un
    indizio.
    """
    if not individui:
        return []
    dentro = set(individui)
    fuori: dict[int, dict] = {}
    for riga in conn.execute(
        "SELECT * FROM anomalie WHERE tipo IN ('DUPLICATE_PERSON', 'POSSIBLE_HOMONYM') "
        "AND stato = 'aperta' ORDER BY priorita DESC"
    ):
        try:
            coinvolti = json.loads(riga["individui"])
        except (TypeError, ValueError):
            continue
        if not set(coinvolti) & dentro:
            continue
        for altro in coinvolti:
            if altro in dentro or altro in fuori:
                continue
            fuori[altro] = {
                "individuo": altro,
                "etichetta": _etichetta(conn, altro),
                "perche": riga["descrizione"],
                "quanto_ci_crede_il_calcolo": round(riga["confidenza"] or 0.0, 2),
                "prove": riga["prove"],
            }
            if len(fuori) >= quanti:
                return list(fuori.values())
    return list(fuori.values())


def _domande_aperte(errori: list[dict]) -> list[str]:
    """Le domande a cui la pagina potrebbe rispondere, in ordine di peso.

    Solo quelle **decidibili guardando l'immagine**: se un dubbio riguarda
    l'identita' — 'questi due sono la stessa persona?' — la pagina non lo
    scioglie, e metterlo qui vorrebbe dire chiedere al modello di
    indovinare invece che di leggere.
    """
    domande = []
    for errore in errori:
        if not errore.get("la_pagina_puo_rispondere"):
            continue
        campo = DUBBI_CHE_LA_PAGINA_SCIOGLIE[errore["codice"]]
        domande.append(
            f"Cosa c'e' scritto sulla pagina per {campo}? "
            f"Il dubbio e': {errore['descrizione']}"
        )
    return domande[:6]
