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
    trovate.extend(nomi_del_sesso_sbagliato(esito))
    trovate.extend(figli_prima_del_matrimonio(esito))
    trovate.extend(fratelli_omonimi(esito))
    trovate.extend(padre_di_un_altro_casato(esito))
    return trovate


# Quanti figli servono perche' il cognome che non torna sia un problema
# del padre e non del figlio. Due: con un figlio solo la lettura sbagliata
# puo' stare da una parte qualsiasi, e ``cognomi_sospetti`` la guarda gia'
# dal lato del figlio. Con due figli che concordano fra loro e discordano
# dal padre, la parte sbagliata e' il padre.
FIGLI_CHE_FANNO_TESTO = 2


def padre_di_un_altro_casato(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Un genitore il cui cognome non e' quello di nessuno dei suoi figli.

    Un padre e i suoi figli legittimi portano lo stesso cognome. Se non
    lo portano, e i figli sono piu' d'uno e fra loro concordano, allora
    la scheda del padre non e' di un uomo solo: e' il posto dove due
    uomini con lo stesso nome di battesimo si sono sovrapposti, e i figli
    dell'uno sono finiti appesi all'altro. E' il caso peggiore da
    lasciare in piedi, perche' non fa perdere una persona ma **sposta un
    ramo intero** sotto la famiglia sbagliata.

    Il caso che l'ha voluta: Fileno De Lucia risultava figlio di «Luigi
    di Fazio». Li' l'uomo era uno solo — 'di Fazio' e 'De Lucia' sono la
    stessa mano letta in due modi, e a scegliere il cognome sbagliato era
    stata la scheda, non il grafo; a raddrizzarlo basta
    ``esecuzione.cognome_di_famiglia``. Ma proprio per questo il
    controllo si fa **dopo** quella scelta: quando neppure una delle
    letture del padre torna nei figli, non c'e' piu' una lettura da
    preferire, e quello che resta e' un dubbio sull'identita'.

    Non guarda il cognome della madre: nei registri porta quello da
    nubile, che con quello dei figli non c'entra per costruzione.

    Oggi non trova niente, ed e' il risultato giusto: la fase 4 da' al
    figlio il cognome del padre quando l'atto lo scrive, quindi dentro
    l'atto i due lo condividono sempre, e perche' questo controllo parli
    bisogna che una fusione abbia messo insieme due uomini che l'archivio
    non ha mai visto insieme. E' una rete, non una diagnosi: sta qui per
    il giorno in cui una regola nuova la strappera'.
    """
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        if scheda.sesso != "M" or not scheda.chiavi_cognome:
            continue
        figli = [
            esito.schede[f] for f in sorted(scheda.figli)
            if f in esito.schede and esito.schede[f].chiavi_cognome
        ]
        if len(figli) < FIGLI_CHE_FANNO_TESTO:
            continue
        if any(scheda.chiavi_cognome & figlio.chiavi_cognome for figlio in figli):
            continue
        loro = set.intersection(*(figlio.chiavi_cognome for figlio in figli))
        if not loro:
            continue        # i figli non concordano neanche fra loro
        trovate.append(mod.Anomalia(
            tipo="SURNAME_ANOMALY",
            individui=(chiave, *(f.chiave for f in figli[:3])),
            campo="cognome",
            descrizione=(
                f"{scheda.etichetta()} e' dato per padre di {len(figli)} figli "
                f"che si chiamano tutti '{sorted(loro)[0]}', e nessuna delle sue "
                f"letture ({', '.join(sorted(scheda.chiavi_cognome))}) e' quella"
            ),
            atti=tuple(sorted(scheda.atti))[:4],
            spiegazioni=(
                "questa scheda tiene insieme due uomini con lo stesso nome",
                "il cognome del padre e' una lettura rovinata di quello dei figli",
                "sono figli naturali riconosciuti solo dalla madre",
            ),
            confidenza=0.7,
            impatto=scheda.quante + sum(f.quante for f in figli),
            gravita="alta",
        ))
    return trovate


def fratelli_omonimi(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Due figli della stessa coppia con lo stesso nome, e nessuna morte in mezzo.

    Riusare il nome di un figlio morto e' l'uso del paese, e l'archivio
    ne e' pieno: Secondina Moretta nasce nel 1822, muore a quattro anni
    nel 1826, e nel 1828 nasce la seconda Secondina. In quei casi c'e' un
    atto di morte fra le due nascite, e le due bambine sono due — il veto
    sui due atti di nascita ha ragione a tenerle separate.

    Quando l'atto di morte **non c'e'**, la lettura ovvia si rovescia:
    nessuno chiama Luigi due figli vivi. O il primo e' morto e il
    registro l'ha perso, o una delle due nascite non e' sua — e in tutti
    e due i casi il veto sta separando un uomo dal suo atto di nascita.

    Il caso: Luigi Moretta, nato nel 1819, e «Luigi Nicola Maria»
    Moretta, nato nel 1821, tutti e due da Carmine Moretta e Maria Lella.
    Le due schede hanno **la stessa vita adulta** — le eta' dichiarate
    dal 1852 al 1879 danno tutte il 1819-1820 — e un solo atto di morte,
    quello del 1880.
    """
    per_coppia: dict[tuple, list] = defaultdict(list)
    for chiave, scheda in esito.schede.items():
        if not scheda.nascite_certe or not scheda.padri or not scheda.madri:
            continue
        for padre in scheda.padri:
            for madre in scheda.madri:
                per_coppia[(padre, madre)].append(chiave)

    trovate: list[mod.Anomalia] = []
    for (padre, madre), figli in sorted(per_coppia.items()):
        if len(figli) < 2:
            continue
        for indice, uno in enumerate(sorted(figli)):
            primo = esito.schede[uno]
            for altro in sorted(figli)[indice + 1:]:
                secondo = esito.schede[altro]
                if not evidenza._due_schede_con_lo_stesso_nome(primo, secondo):
                    continue
                prima, dopo = sorted((
                    (min(primo.nascite_certe), primo),
                    (min(secondo.nascite_certe), secondo),
                ), key=lambda x: x[0])
                if prima[1].morte is not None and prima[1].morte <= dopo[0]:
                    continue        # il nome e' stato riusato: sono due
                trovate.append(mod.Anomalia(
                    tipo="DUPLICATE_PERSON",
                    individui=(uno, altro),
                    descrizione=(
                        f"{primo.etichetta()} e {secondo.etichetta()} sono figli "
                        f"della stessa coppia e portano lo stesso nome, ma fra le "
                        f"due nascite non c'e' nessun atto di morte: nessuno "
                        f"chiama cosi' due figli vivi"
                    ),
                    spiegazioni=(
                        "il primo e' morto e il registro l'ha perso",
                        "una delle due nascite non e' sua",
                    ),
                    confidenza=0.6,
                    impatto=primo.quante + secondo.quante,
                    gravita="alta",
                ))
    return trovate


# Quanti anni si concede a un figlio di precedere il matrimonio dei
# genitori. Uno: i figli nati prima delle nozze esistono e vengono
# legittimati dall'atto stesso, ma nascono nei mesi che le precedono,
# non dieci anni prima.
ANNI_PRIMA_DEL_MATRIMONIO = 2


def figli_prima_del_matrimonio(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Un figlio che nasce anni prima che i suoi genitori si sposino.

    Quasi sempre non e' un figlio prematrimoniale: e' **un'eta' letta
    male**, e l'atto di matrimonio dei genitori e' la prova piu' solida
    che l'archivio possieda per smentirla — porta una data scritta, non
    un'eta' ricordata a voce.

    Il caso che l'ha voluta: Luigi Moretta, morto nel 1880, trascritto
    «di anni settantuno». Da li' risultava nato nel 1809, mentre suo
    padre Carmine Moretta e sua madre Maria Lella si sposano nel
    **febbraio 1813**. Sulla pagina c'e' scritto «sessantuno»: e' il
    Luigi nato nel 1819, e senza questa lettura restava un terzo fratello
    omonimo che non e' mai esistito.
    """
    nozze: dict = {}
    for chiave, scheda in esito.schede.items():
        for menzione in scheda.menzioni:
            if menzione.tipo_atto != "matrimonio" or menzione.ruolo not in (
                "sposo", "sposa"
            ):
                continue
            if menzione.anno:
                nozze[chiave] = min(nozze.get(chiave, menzione.anno), menzione.anno)

    trovate: list[mod.Anomalia] = []
    for chiave, scheda in sorted(esito.schede.items()):
        anno = scheda.anno_nascita
        if anno is None:
            continue
        for genitore in sorted(scheda.padri | scheda.madri):
            matrimonio = nozze.get(genitore)
            if matrimonio is None or anno >= matrimonio - ANNI_PRIMA_DEL_MATRIMONIO:
                continue
            suo = esito.schede.get(genitore)
            trovate.append(mod.Anomalia(
                tipo="AGE_ANOMALY",
                individui=(chiave, genitore),
                campo="eta",
                descrizione=(
                    f"{scheda.etichetta()} risulta nato nel {anno}, ma "
                    f"{suo.etichetta() if suo else genitore} si sposa nel "
                    f"{matrimonio}: l'eta' dichiarata non regge contro una data "
                    f"scritta"
                ),
                spiegazioni=(
                    "l'eta' e' letta male sull'atto",
                    "e' un figlio di un matrimonio precedente",
                ),
                confidenza=0.7, impatto=scheda.quante, gravita="media",
            ))
    return trovate


def nomi_del_sesso_sbagliato(esito: risoluzione.Esito) -> list[mod.Anomalia]:
    """Un padre con un nome da donna, o una madre con un nome da uomo.

    Il ruolo dice il sesso senza margine — un padre e' un uomo — quindi
    quando il nome letto e' inequivocabilmente dell'altro sesso a
    sbagliare e' la **lettura**, e sbaglia in un punto che si vede: il
    nome. E' la classe di errore piu' facile da mandare all'immagine,
    perche' la domanda e' chiusa.

    Nell'albero di Filippo Lella ce n'era una: nell'atto 5000 del 1874 il
    padre del neonato Nicola Maria **Di Nardo** era trascritto
    «Domenicantonia D'Illice» — femminile, e con un cognome che in tutto
    l'archivio compare due volte. Era Domenicantonio Di Nardo,
    quarantatre anni, marito di Maria Domenica Pelliccia.
    """
    genere = getattr(esito.corpus, "genere", None)
    if genere is None:
        return []
    trovate: list[mod.Anomalia] = []
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        for menzione in scheda.menzioni:
            atteso = _sesso_dal_ruolo(menzione.ruolo)
            if atteso is None or not menzione.nome:
                continue
            dal_nome = genere.di(menzione.nome)
            if dal_nome is None or dal_nome == atteso:
                continue
            trovate.append(mod.Anomalia(
                tipo="NAME_ANOMALY",
                individui=(chiave,),
                campo="nome",
                descrizione=(
                    f"nell'atto {menzione.atto} e' {menzione.ruolo} — quindi "
                    f"{'un uomo' if atteso == 'M' else 'una donna'} — ma porta "
                    f"il nome «{menzione.nome}», che in paese e' "
                    f"{'maschile' if dal_nome == 'M' else 'femminile'}"
                ),
                atti=(menzione.atto,),
                spiegazioni=(
                    "il nome e' letto male sull'atto",
                    "il ruolo e' letto male sull'atto",
                ),
                confidenza=0.7, impatto=scheda.quante, gravita="media",
            ))
    return trovate


def _sesso_dal_ruolo(ruolo: str | None) -> str | None:
    from history_maker import nomi as _nomi

    chiave = (ruolo or "").strip().casefold()
    if chiave in _nomi.RUOLI_MASCHILI:
        return "M"
    if chiave in _nomi.RUOLI_FEMMINILI:
        return "F"
    return None


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
