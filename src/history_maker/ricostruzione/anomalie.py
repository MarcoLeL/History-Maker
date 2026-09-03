"""Cio' che nel grafo non torna, in ordine di quanto conviene guardarlo.

Un archivio ricostruito da una macchina sembra sempre finito. Questo
modulo e' il posto dove smette di sembrarlo: raccoglie tutto quello che
non quadra e lo mette in fila, non per gravita' assoluta ma per **quanto
vale guardarlo**.

L'ordine e' un prodotto di tre cose, e vale la pena spiegarlo perche' non
e' quello che verrebbe in mente:

* **il dubbio**, che e' massimo a meta' strada e nullo agli estremi. Un
  caso al 95% non ha bisogno di nessuno; uno al 50% si;
* **l'impatto**, cioe' quante persone la decisione sposterebbe. Unire due
  schede con quaranta discendenti ciascuna cambia ottanta schede; unire
  due testimoni ne cambia due;
* **la gravita' della categoria**: una data impossibile e' un errore
  certo, una professione discordante e' una curiosita'.

Il risultato e' che il caso incerto che muove un ramo intero passa davanti
al caso quasi certo che non muove niente. E' anche l'ordine con cui
conviene spendere la quota di Gemini e il ragionamento di Claude: sono
risorse finite, e questa e' la lista che dice dove valgono di piu'.

Le categorie sono quelle del modello (``TIPI_ANOMALIA``). Tre meritano una
parola:

**DUPLICATE_PERSON** e' la fascia grigia: due schede che si somigliano
abbastanza da far dubitare ma non abbastanza da unirle. Non e' uno scarto
— e' esattamente il lavoro che vale la pena far fare a un modello
costoso, perche' e' dove il calcolo si ferma e il giudizio comincia.

**POSSIBLE_HOMONYM** e' il contrario: due candidati ugualmente buoni e
incompatibili fra loro. Qui il sistema **sa** di non poter decidere, e
dirlo e' un risultato, non un fallimento.

**TRANSCRIPTION_ANOMALY** e' il caso in cui il sospetto non e'
sull'identita' ma sulla lettura: la fonte ha un dato che il resto
dell'archivio contraddice, e la risposta non sta nel grafo ma
sull'immagine.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from history_maker.ricostruzione import candidati, evidenza, modello as mod, risoluzione

logger = logging.getLogger(__name__)

# Oltre questo scarto fra l'eta' dichiarata e l'atto di nascita, o l'eta'
# e' letta male o non e' la stessa persona.
SCARTO_ETA_SOSPETTO = 10

# Sotto quante attestazioni un cognome e' abbastanza raro da meritare un
# dubbio quando il padre ne porta uno diverso e frequente.
COGNOME_RARO = 3
DOMINANZA_PATERNA = 8

# Due parti a meno di questi giorni non sono due parti.
MESI_FRA_DUE_PARTI = 9


def tutte(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Tutte le anomalie che si vedono a grafo finito."""
    trovate: list[mod.Anomalia] = []
    trovate.extend(duplicati_probabili(esito))
    trovate.extend(eta_incoerenti(esito))
    trovate.extend(cognomi_sospetti(esito))
    trovate.extend(parti_impossibili(esito))
    trovate.extend(coniugi_contemporanei(esito))
    trovate.extend(letture_decisive_incerte(esito))
    trovate.extend(vite_troppo_lunghe(esito))
    return trovate


# Quanti anni puo' durare la comparsa di una persona nei registri. Non e'
# la vita massima: e' l'arco fra la prima e l'ultima volta in cui compare
# **da viva**, che comincia da adulta e finisce con la morte.
ARCO_MASSIMO = 85


def vite_troppo_lunghe(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Chi compare nei registri per piu' anni di quanti se ne viva.

    E' la fusione sbagliata che non produce nessuna impossibilita'
    puntuale: ogni singolo atto e' plausibile, e solo l'arco intero non
    sta in piedi. Manasse Franchella, agrimensore, risultava presente dal
    1809 al 1894 con una nascita stimata nel 1784 — centodieci anni —
    perche' un atto lo dava genitore dello stesso figlio di Mariano
    Franchella, e quel figlio era a sua volta due bambini in una scheda.

    Non e' un veto e non si separa: il rimedio giusto e' quasi sempre
    sciogliere **un'altra** fusione, e per capire quale serve guardare.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        anni = scheda.anni_presente or scheda.anni
        if len(anni) < 2:
            continue
        arco = max(anni) - min(anni)
        nascita = scheda.anno_nascita
        eta_finale = (max(anni) - nascita) if nascita is not None else None
        if arco <= ARCO_MASSIMO and (eta_finale is None or eta_finale <= 100):
            continue
        trovate.append(mod.Anomalia(
            tipo="IDENTITY_ANOMALY",
            individui=(chiave,),
            campo="eta",
            descrizione=(
                f"{scheda.etichetta()} compare dal {min(anni)} al {max(anni)}"
                + (f", cioe' fino a {eta_finale} anni" if eta_finale else "")
                + ": e' un arco che una vita sola non copre"
            ),
            atti=tuple(sorted(scheda.atti))[:6],
            spiegazioni=(
                "la scheda ha raccolto due persone dello stesso nome",
                "una fusione a monte — un figlio, un coniuge — ha tirato dentro "
                "un omonimo",
                "l'eta' che fissa la nascita e' letta male",
            ),
            confidenza=0.85,
            impatto=scheda.quante,
            gravita="alta",
        ))
    return trovate


# ---------------------------------------------------------------------------
# La fascia grigia
# ---------------------------------------------------------------------------

def duplicati_probabili(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Le coppie che il calcolo non ha unito ma non ha nemmeno scartato.

    Sono la coda che vale la pena mandare a un modello: il calcolo ha
    fatto quello che poteva e si e' fermato a meta'. Ogni riga qui e' una
    persona che potrebbe essere spezzata in due.
    """
    coppie, _ = candidati.tutte(
        esito.schede, esito.di_menzione,
        esito.modello.vicini_cognome, esito.modello.vicini_nome,
    )
    trovate: list[mod.Anomalia] = []
    for una, altra in coppie:
        prima, seconda = esito.schede.get(una), esito.schede.get(altra)
        if prima is None or seconda is None:
            continue
        prove = evidenza.confronta(prima, seconda, esito.modello)
        if prove.impossibile is not None:
            continue
        if not (
            risoluzione.SOGLIA_SEGNALAZIONE <= prove.logit < risoluzione.SOGLIA_UNIONE
        ):
            continue
        trovate.append(mod.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(una, altra),
            descrizione=(
                f"{prima.etichetta()} e {seconda.etichetta()} potrebbero essere "
                f"la stessa persona ({prove.confidenza:.0%})"
            ),
            atti=tuple(sorted(prima.atti | seconda.atti))[:8],
            spiegazioni=(
                "sono la stessa persona e una lettura e' rovinata",
                "sono due omonimi",
            ),
            evidenza=prove,
            confidenza=prove.confidenza,
            impatto=prima.quante + seconda.quante,
            gravita="media" if prima.quante + seconda.quante < 6 else "alta",
        ))
    return trovate


# ---------------------------------------------------------------------------
# Le date
# ---------------------------------------------------------------------------

def eta_incoerenti(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Un'eta' dichiarata che l'atto di nascita smentisce.

    O l'eta' e' letta male, o quella riga non e' di questa persona. Sono
    due spiegazioni molto diverse e il grafo da solo non le distingue:
    e' il caso tipico da mandare all'immagine.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        if not scheda.nascite_certe:
            continue
        vera = min(scheda.nascite_certe)
        for menzione in scheda.menzioni:
            stimata = menzione.anno_nascita
            if stimata is None:
                continue
            scarto = abs(stimata - vera)
            if scarto <= SCARTO_ETA_SOSPETTO:
                continue
            trovate.append(mod.Anomalia(
                tipo="AGE_ANOMALY",
                individui=(chiave,),
                campo="eta",
                descrizione=(
                    f"{scheda.etichetta()} dichiara {menzione.eta_letta} nel "
                    f"{menzione.anno}, che corrisponderebbe al {stimata}, ma il "
                    f"suo atto di nascita e' del {vera}"
                ),
                atti=(menzione.atto,),
                # Le due eta' in gioco: quella che l'atto dichiara e
                # quella che l'atto di nascita implica. Con tutte e due
                # in mano la domanda all'immagine diventa chiusa.
                alternative=(
                    str(menzione.eta_letta or ""),
                    f"{menzione.anno - vera}",
                ),
                spiegazioni=(
                    "l'eta' e' letta male sull'atto",
                    "questa riga e' di un'altra persona",
                ),
                confidenza=0.6,
                impatto=scheda.quante,
                gravita="media",
            ))
    return trovate


def parti_impossibili(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Due figli della stessa madre a meno di nove mesi di distanza.

    Non e' un dubbio: o uno dei due atti e' attribuito alla madre
    sbagliata, o sono due parti dello stesso giorno letti come due date.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        if scheda.sesso != "F" or len(scheda.parti) < 2:
            continue
        # Il controllo e' sulle date, non sugli anni: due nascite in anni
        # diversi possono distare due mesi, e due nascite nello stesso
        # anno possono essere gemelli, che sono normali.
        date = sorted(
            (m.data, m.atto) for m in scheda.menzioni
            if m.tipo_atto == "nascita" and m.ruolo == "madre" and m.data
        )
        for (una, atto_uno), (altra, atto_due) in zip(date, date[1:]):
            mesi = _mesi_fra(una, altra)
            if mesi is None or mesi >= MESI_FRA_DUE_PARTI or mesi == 0:
                continue
            trovate.append(mod.Anomalia(
                tipo="DATE_ANOMALY",
                individui=(chiave,),
                campo="nascita",
                descrizione=(
                    f"{scheda.etichetta()} risulta partorire il {una} e il "
                    f"{altra}: {mesi} mesi di distanza"
                ),
                atti=(atto_uno, atto_due),
                spiegazioni=(
                    "uno dei due atti e' attribuito alla madre sbagliata",
                    "una delle due date e' letta male",
                ),
                confidenza=0.85,
                impatto=scheda.quante,
                gravita="alta",
            ))
    return trovate


def _mesi_fra(una: str, altra: str) -> int | None:
    """I mesi fra due date in forma ISO, quando si lasciano leggere."""
    try:
        anno_uno, mese_uno = int(una[:4]), int(una[5:7])
        anno_due, mese_due = int(altra[:4]), int(altra[5:7])
    except (ValueError, IndexError):
        return None
    if not (1 <= mese_uno <= 12 and 1 <= mese_due <= 12):
        return None
    return abs((anno_due - anno_uno) * 12 + (mese_due - mese_uno))


def coniugi_contemporanei(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Due coniugi negli stessi anni, senza che il primo risulti morto.

    Non e' automaticamente un errore — ci si risposa, e le vedove di
    questi registri si risposano spesso — ma due matrimoni **che si
    sovrappongono** vogliono una spiegazione: o una vedovanza che
    l'archivio non ha, o due schede da separare, o una da unire.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        if len(scheda.coniugi) < 2:
            continue
        finestre = []
        for coniuge in sorted(scheda.coniugi):
            altra = esito.schede.get(coniuge)
            if altra is None:
                continue
            anni = [
                m.anno for m in scheda.menzioni
                if m.coniuge is not None
                and esito.di_menzione.get(m.coniuge) == coniuge and m.anno
            ]
            if anni:
                finestre.append((min(anni), max(anni), altra.chiave))
        finestre.sort()
        for (inizio_uno, fine_uno, prima), (inizio_due, fine_due, seconda) in zip(
            finestre, finestre[1:]
        ):
            una, altra = esito.schede[prima], esito.schede[seconda]
            if inizio_due > fine_uno:
                continue    # in fila: una vedovanza e un secondo matrimonio
            if una.morte is not None and una.morte <= inizio_due:
                continue
            trovate.append(mod.Anomalia(
                tipo="MARITAL_ANOMALY",
                individui=(chiave, una.chiave, altra.chiave),
                descrizione=(
                    f"{scheda.etichetta()} ha due coniugi negli stessi anni: "
                    f"{una.etichetta()} ({inizio_uno}-{fine_uno}) e "
                    f"{altra.etichetta()} ({inizio_due}-{fine_due})"
                ),
                spiegazioni=(
                    "i due coniugi sono la stessa persona letta in due modi",
                    "sono due omonimi e le schede vanno separate",
                    "manca l'atto di morte del primo coniuge",
                ),
                confidenza=0.6,
                impatto=scheda.quante + una.quante + altra.quante,
                gravita="alta",
            ))
    return trovate


# ---------------------------------------------------------------------------
# I nomi
# ---------------------------------------------------------------------------

def cognomi_sospetti(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Un figlio con un cognome raro e un padre con uno frequente.

    Un figlio legittimo porta il cognome del padre: non e' una
    probabilita', e' come funziona l'atto di stato civile. Quando le due
    letture divergono, quindi, **una delle due e' sbagliata** — e questo
    e' un caso in cui si sa anche quale sia la domanda da fare
    all'immagine, che e' cio' che lo rende economico da risolvere.

    Cio' che questo controllo NON fa e' correggere. Un cognome raro puo'
    essere un figlio illegittimo riconosciuto, o una madre nubile: sono
    esattamente i casi che raccontano qualcosa, e appiattirli sul
    cognome del padre distruggerebbe il dato piu' interessante.
    """
    frequenze = esito.corpus.frequenze_cognome
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        if not scheda.chiavi_cognome or not scheda.padri:
            continue
        mio = min(scheda.chiavi_cognome, key=lambda c: (-frequenze.get(c, 0), c))
        quante_mie = frequenze.get(mio, 0)
        if quante_mie > COGNOME_RARO:
            continue
        for padre in sorted(scheda.padri):
            altro = esito.schede.get(padre)
            if altro is None or not altro.chiavi_cognome:
                continue
            suo = min(altro.chiavi_cognome, key=lambda c: (-frequenze.get(c, 0), c))
            if suo == mio:
                continue
            quante_sue = frequenze.get(suo, 0)
            if quante_sue < quante_mie * DOMINANZA_PATERNA:
                continue
            trovate.append(mod.Anomalia(
                tipo="SURNAME_ANOMALY",
                individui=(chiave, padre),
                campo="cognome",
                descrizione=(
                    f"{scheda.etichetta()} porta '{mio}' ({quante_mie} volte in "
                    f"tutto il secolo) ma suo padre porta '{suo}' ({quante_sue})"
                ),
                atti=tuple(sorted(scheda.atti))[:4],
                spiegazioni=(
                    f"il cognome del figlio e' una lettura rovinata di '{suo}'",
                    "e' un figlio naturale, e il cognome diverso e' un dato vero",
                    "la paternita' e' attribuita alla persona sbagliata",
                ),
                confidenza=0.65,
                impatto=scheda.quante,
                gravita="media",
            ))
    return trovate


def letture_decisive_incerte(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Chi si regge su una lettura che la trascrizione stessa dava per dubbia.

    Il modello che ha letto le pagine, quando esitava, lo ha scritto.
    Quando quella riga e' anche l'unica che tiene insieme una scheda, il
    dubbio della trascrizione diventa un dubbio sull'identita', e la
    risposta non e' nel grafo: e' sull'immagine.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        incerte = [m for m in scheda.menzioni if m.incerto]
        if not incerte:
            continue
        # Conta solo se quella riga porta qualcosa che il resto della
        # scheda non ha: se il nome e' letto dubbio ma altre otto righe lo
        # confermano, non c'e' niente da verificare.
        if scheda.quante > 2 and len(incerte) < scheda.quante / 2:
            continue
        menzione = incerte[0]
        trovate.append(mod.Anomalia(
            tipo="TRANSCRIPTION_ANOMALY",
            individui=(chiave,),
            campo="nome",
            descrizione=(
                f"{scheda.etichetta()} si regge su una lettura che la "
                f"trascrizione dava per dubbia (atto {menzione.atto}, "
                f"'{menzione.nome_letto} {menzione.cognome_letto}')"
            ),
            atti=(menzione.atto,),
            spiegazioni=("la lettura va verificata sull'immagine",),
            confidenza=0.5,
            impatto=scheda.quante,
            gravita="bassa" if scheda.quante == 1 else "media",
        ))
    return trovate


# ---------------------------------------------------------------------------
# La coda
# ---------------------------------------------------------------------------

# Quanti casi della stessa persona possono stare in testa alla coda. Il
# resto non sparisce: scivola in fondo, e torna quando i primi sono
# finiti.
#
# Serve perche' l'impatto entra nella priorita' e le schede grosse ne
# hanno molto: un individuo da 1.153 menzioni occupava da solo le prime
# trenta posizioni, e una coda cosi' fa spendere tutta la quota di una
# giornata su una persona sola. Tre e' il numero che lascia vedere un
# caso da tre lati — chi e', chi sono i suoi due coniugi — senza che ne
# monopolizzi il turno.
CASI_PER_PERSONA = 3


def coda(esito: risoluzione.Esito, quante: int | None = None) -> list[mod.Anomalia]:
    """Le anomalie in ordine di quanto conviene guardarle.

    L'ordine e' la priorita', con un correttivo: nessuna persona occupa
    la testa della coda piu' di ``CASI_PER_PERSONA`` volte. Quello che
    avanza va in fondo invece di sparire, perche' un caso rimandato resta
    un caso.
    """
    ordinate = sorted(
        esito.anomalie,
        key=lambda a: (-a.priorita, a.tipo, a.individui),
    )
    quanti_per_persona: Counter = Counter()
    testa, rimandate = [], []
    for anomalia in ordinate:
        chiave = anomalia.individui[0] if anomalia.individui else None
        if chiave is not None and quanti_per_persona[chiave] >= CASI_PER_PERSONA:
            rimandate.append(anomalia)
            continue
        if chiave is not None:
            quanti_per_persona[chiave] += 1
        testa.append(anomalia)
    ordinate = testa + rimandate
    return ordinate[:quante] if quante else ordinate


def rapporto(esito: risoluzione.Esito, numeri: dict | None = None, esempi: int = 25) -> str:
    """Il rapporto sulle anomalie, per categoria e in ordine di priorita'."""
    per_tipo: dict[str, list] = defaultdict(list)
    for anomalia in coda(esito):
        per_tipo[anomalia.tipo].append(anomalia)

    conteggi = Counter({tipo: len(righe) for tipo, righe in per_tipo.items()})
    righe = [
        "# I dubbi della ricostruzione",
        "",
        "Ogni riga di questo elenco e' un posto in cui il calcolo si e'",
        "fermato: non un errore da correggere in silenzio, ma una domanda a",
        "cui serve o un ragionamento o un'occhiata alla carta.",
        "",
        "L'ordine non e' per gravita' ma per **quanto conviene guardarlo**:",
        "il dubbio moltiplicato per quante persone la risposta sposterebbe.",
        "",
        "| tipo | quante |",
        "|---|---:|",
    ]
    for tipo, quante in conteggi.most_common():
        righe.append(f"| {tipo} | {quante} |")

    for tipo, _ in conteggi.most_common():
        righe += ["", f"## {tipo} — {conteggi[tipo]}", ""]
        for anomalia in per_tipo[tipo][:esempi]:
            righe.append(f"- {anomalia.descrizione}")
            if anomalia.spiegazioni:
                righe.append(
                    f"  - possibili spiegazioni: {'; '.join(anomalia.spiegazioni)}"
                )
            if anomalia.evidenza is not None:
                righe.append(f"  - prove: {anomalia.evidenza.racconta(4)}")
            righe.append(
                f"  - priorita' {anomalia.priorita:.2f} "
                f"(confidenza {anomalia.confidenza:.0%}, impatto {anomalia.impatto})"
            )
        if conteggi[tipo] > esempi:
            righe.append(f"- …e altri {conteggi[tipo] - esempi}")
    righe.append("")
    return "\n".join(righe)
