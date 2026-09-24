"""La risoluzione dell'identita' e la riconciliazione del grafo.

La domanda a cui questo modulo risponde **non** e' "qual e' la persona
piu' simile a questa menzione?". E':

    quale interpretazione produce la ricostruzione complessivamente
    piu' coerente con tutti i documenti disponibili?

La differenza si vede in tre punti, e sono i tre in cui il sistema
precedente si fermava.

**Si torna indietro.** Una decisione presa al secondo atto puo' rivelarsi
sbagliata al duemillesimo. Qui le schede si fondono e si **separano**, per
quanti giri servono, finche' niente cambia piu'. Una fusione che rende una
scheda incoerente viene disfatta dalla separazione, e resta scritto
perche'.

**Il contesto pesa piu' del nome.** Il punteggio non e' una somma di pesi
scelti a mano ma un rapporto di verosimiglianza misurato sul paese
(:mod:`evidenza`). Due 'Pelliccia' non provano niente, due 'Genualdi'
provano molto, e nessuno ha dovuto deciderlo.

**L'ambiguita' e' un risultato.** Quando due candidati incompatibili sono
ugualmente buoni, il sistema non sceglie: dichiara l'omonimia e lascia le
schede separate con l'anomalia scritta accanto. Un archivio che sceglie a
caso quando non sa e' peggio di uno che ammette di non sapere, perche' non
si vede che ha scelto a caso.

L'ordine dei passaggi
---------------------

1. **Primo giro, incrementale.** Ogni menzione cerca fra le schede gia'
   formate quella che le somiglia di piu'. Serve a passare da cinquantamila
   righe a qualche decina di migliaia di schede a un costo lineare: senza,
   il confronto a coppie sul corpus intero non finirebbe.
2. **Riconciliazione, a coppie.** Ora che le schede esistono si guardano
   fra loro, e questa volta l'evidenza comprende i **legami come
   identita'** — 'sua moglie e' quella scheda' — che al primo giro non
   esistevano ancora. E' qui che si rimettono insieme le famiglie che il
   primo giro aveva diviso.
3. **Separazione.** Ogni scheda viene riletta per intero: se contiene una
   contraddizione fisica, viene divisa nei suoi pezzi coerenti.
4. Si ripete finche' un giro non cambia piu' niente.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from history_maker import paleografia
from history_maker.ricostruzione import candidati, evidenza, modello as mod
from history_maker.ricostruzione.evidenza import Modello
from history_maker.ricostruzione.scheda import Scheda

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Le soglie
# ---------------------------------------------------------------------------
#
# Le soglie sono sul **logit**, cioe' sull'evidenza gia' letta insieme
# all'a priori del corpus (vedi :meth:`evidenza.Modello.dal_corpus`). E'
# la scelta che rende le soglie trasportabili: le stesse prove valgono di
# piu' in un comune piccolo, dove gli omonimi sono pochi, e meno in uno
# grande, e su soglie assolute bisognerebbe ritararle a mano per ogni
# archivio.
#
# Zero vuol dire "tanto probabile quanto improbabile". -0,90 e' il punto
# in cui si unisce, che corrisponde all'11% di probabilita': sembra
# pochissimo e non lo e', perche' il costo dei due errori non e'
# simmetrico. Una fusione sbagliata fabbrica una persona che non e' mai
# esistita, e **si vede**: produce cose impossibili che il controllo di
# qualita' conta una per una. Una frammentazione non produce niente di
# impossibile — produce un albero con meno parentele di quante
# l'archivio ne contenga, e chi guarda non ha modo di accorgersene.
#
# I numeri, misurati sull'archivio intero (13.600 persone contro le
# 16.700 della fase 6 precedente):
#
#   soglia   cose impossibili   frammentazioni   bambini ritrovati da adulti
#   -0,30              165            220                  1.316
#   -0,60              167            194                  1.339
#   -0,90              215            229                  1.504
#   -1,30              273            253                  1.536
#
# L'ultima colonna e' quella che conta: quanti bambini nati qui
# l'archivio ritrova poi da adulti. E' la frammentazione che nessun
# controllo vede, ed e' la ragione per cui la soglia sta a -0,90 e non
# piu' in alto.
#
# Il conto che rende leggibile la soglia: due menzioni con lo stesso nome
# e lo stesso cognome, e nient'altro, valgono +2,3 di evidenza contro un
# a priori di -4,1, cioe' -1,8 di logit. **Sotto la soglia, apposta.** In
# un paese dove il primogenito porta il nome del nonno, 'Domenico
# Pelliccia' sono cinque uomini diversi, e per unirne due serve qualcosa
# che non sia il nome: un genitore, un coniuge, un figlio.
#
# Le soglie sono due e la seconda e' la piu' interessante: sopra
# SOGLIA_UNIONE si unisce, fra le due si **segnala** senza unire. La
# fascia in mezzo non e' uno scarto — e' la coda che va a Claude e alla
# verifica sull'immagine, cioe' esattamente il lavoro che vale la pena
# far fare a un modello costoso.
SOGLIA_UNIONE = -0.90
SOGLIA_SEGNALAZIONE = -1.90

# Quando il candidato e' uno solo, lo spazio delle ipotesi e' piu'
# piccolo e la stessa evidenza vale di piu'. Non e' una scorciatoia: e'
# la stessa aritmetica con un a priori diverso. Un testimone 'Egidio
# Colaneri, 33 anni' nel 1834 e uno 'Egidio Colaneri, 45 anni' nel 1846
# sono lo stesso uomo se in tutto il secolo non ce n'e' un altro con
# un'eta' compatibile, e tenerli separati fabbrica due mezzi uomini al
# posto di uno intero.
SOGLIA_CANDIDATO_UNICO = -2.00

# Quanto il migliore deve staccare un candidato **incompatibile con lui**
# perche' la scelta non sia un tiro di dadi. E' la regola che produce gli
# omonimi invece di nasconderli.
MARGINE_OMONIMIA = 0.6

# I giri di riconciliazione. Il ciclo si ferma da solo quando nessuna
# regola trova piu' niente; il tetto e' una rete perche' un errore futuro
# non mandi la costruzione in eterno senza dirlo.
GIRI_MASSIMI = 12


@dataclass
class Esito:
    """Cio' che la ricostruzione produce, prima di essere scritta."""

    schede: dict
    di_menzione: dict
    modello: Modello
    corpus: object
    decisioni: list = field(default_factory=list)
    anomalie: list = field(default_factory=list)
    statistiche: Counter = field(default_factory=Counter)

    def scheda_di(self, menzione: int) -> Scheda | None:
        chiave = self.di_menzione.get(menzione)
        return self.schede.get(chiave) if chiave is not None else None


def _adesso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Il primo giro
# ---------------------------------------------------------------------------

def _forza(menzione) -> int:
    """In che ordine conviene guardare le menzioni.

    Prima quelle che portano piu' informazione — chi ha padre **e** madre
    scritti — perche' formano nuclei ben identificati a cui le menzioni
    piu' povere possono poi agganciarsi. All'incontrario, un testimone
    senza eta' guardato per primo aprirebbe una scheda vaga, e quella
    scheda vaga si prenderebbe poi tutti gli omonimi del secolo.
    """
    if menzione.padre is not None and menzione.madre is not None:
        return 0
    if menzione.padre is not None or menzione.madre is not None:
        return 1
    if menzione.coniuge is not None:
        return 2
    if menzione.eta is not None:
        return 3
    return 4


def _scegli(
    candidate: list[Scheda], nuova: Scheda, modello: Modello
) -> tuple[Scheda | None, mod.Evidenza | None, list[tuple[Scheda, mod.Evidenza]]]:
    """La scheda a cui attaccare, l'evidenza che lo sostiene, e le rivali.

    Rende ``(scelta, evidenza, ammesse)``. ``scelta`` e' ``None`` sia
    quando non c'e' nessun candidato accettabile, sia quando ce ne sono
    due ugualmente buoni e incompatibili fra loro: i due casi si
    distinguono guardando ``ammesse``, ed e' quella distinzione a
    produrre le anomalie di omonimia.
    """
    ammesse: list[tuple[Scheda, mod.Evidenza]] = []
    for scheda in candidate:
        prove = evidenza.confronta(scheda, nuova, modello)
        if prove.impossibile is None and prove.logit >= SOGLIA_SEGNALAZIONE:
            ammesse.append((scheda, prove))

    if not ammesse:
        return None, None, [], "nessun candidato"

    ammesse.sort(key=lambda coppia: (-coppia[1].logit, coppia[0].chiave))
    migliore, prove = ammesse[0]
    soglia = SOGLIA_CANDIDATO_UNICO if len(ammesse) == 1 else SOGLIA_UNIONE
    if prove.logit < soglia:
        # Non e' ambiguita': e' evidenza che non basta. La distinzione
        # conta, perche' i due casi vogliono rimedi diversi — questo
        # aspetta la riconciliazione, l'altro aspetta un occhio umano.
        return None, None, ammesse, "sotto soglia"

    # L'ambiguita' si misura solo contro chi e' **incompatibile** con il
    # migliore. Due schede che potrebbero essere la stessa persona non
    # sono due possibilita' fra cui scegliere: sono due pezzi dello
    # stesso uomo, e li unira' la riconciliazione.
    for rivale, sue_prove in ammesse[1:]:
        if sue_prove.logit < prove.logit - MARGINE_OMONIMIA:
            break
        if evidenza.veti(migliore, rivale) is not None:
            return None, None, ammesse, "ambiguo"
    return migliore, prove, ammesse, ""


def primo_giro(corpus, modello: Modello) -> Esito:
    """Da cinquantamila menzioni a qualche decina di migliaia di schede."""
    chiavi = corpus.chiavi()
    schede: dict[int, Scheda] = {}
    di_menzione: dict[int, int] = {}
    anomalie: list[mod.Anomalia] = []

    indice: dict[tuple[str, str], list[int]] = defaultdict(list)
    dentro: dict[tuple[str, str], set[int]] = defaultdict(set)

    ordinate = sorted(corpus.menzioni, key=lambda m: (_forza(m), m.anno, m.id))
    for menzione in ordinate:
        nuova = Scheda.dalla_menzione(menzione, chiavi)

        viste: set[int] = set()
        candidate: list[Scheda] = []
        for cognome in _forme_cognome(nuova, modello):
            for parte in _forme_nome(nuova, modello):
                for chiave in indice.get((cognome, parte), ()):
                    if chiave not in viste:
                        viste.add(chiave)
                        candidate.append(schede[chiave])

        scelta, prove, ammesse, motivo = _scegli(candidate, nuova, modello)

        if scelta is None:
            # Solo l'ambiguita' vera diventa un'anomalia. Le menzioni che
            # restano sole per evidenza insufficiente non si segnalano
            # qui: le riprende la riconciliazione, e cio' che avanza lo
            # raccoglie 'anomalie.duplicati_probabili' a grafo finito.
            if motivo == "ambiguo":
                anomalie.append(_anomalia_omonimia(ammesse, nuova))
            schede[nuova.chiave] = nuova
            di_menzione[menzione.id] = nuova.chiave
            bersaglio = nuova
        else:
            scelta.aggiungi(menzione, chiavi)
            di_menzione[menzione.id] = scelta.chiave
            if prove.logit < SOGLIA_UNIONE:
                scelta.incerte.add(menzione.id)
            scelta.prove.append(prove.racconta(3))
            bersaglio = scelta

        for cognome in sorted(bersaglio.chiavi_cognome) or [""]:
            for parte in sorted(bersaglio.parti_nome) or [""]:
                scaffale = (cognome, parte)
                if bersaglio.chiave not in dentro[scaffale]:
                    dentro[scaffale].add(bersaglio.chiave)
                    indice[scaffale].append(bersaglio.chiave)

    return Esito(
        schede=schede, di_menzione=di_menzione, modello=modello, corpus=corpus,
        anomalie=anomalie,
    )


def _forme_cognome(scheda: Scheda, modello: Modello) -> list[str]:
    """Gli scaffali di cognome in cui cercare i candidati di questa scheda.

    Lo scaffale vuoto vale **solo** per chi il cognome non ce l'ha. Non e'
    un dettaglio: metterci dentro anche chi ce l'ha significherebbe un
    unico scaffale che contiene tutti, e ogni menzione verrebbe
    confrontata con l'archivio intero. Misurato: 546 candidati per
    menzione invece di una decina.
    """
    # Lo scaffale vuoto si guarda sempre, anche avendo un cognome: e' li'
    # che stanno le righe a cui il cognome non e' stato letto — i neonati
    # soprattutto, che il formulario da' per impliciti nel nome del padre
    # — e sono esattamente quelle che hanno piu' bisogno di ritrovare la
    # loro famiglia. Contiene poche schede, quindi costa poco.
    forme: set[str] = {""}
    for cognome in scheda.chiavi_cognome | scheda.alternative_cognome:
        forme.update(modello.vicini_cognome.get(cognome, (cognome,)))
    return sorted(forme)


def _forme_nome(scheda: Scheda, modello: Modello) -> list[str]:
    forme: set[str] = set()
    for parte in scheda.parti_nome or {""}:
        forme.update(modello.vicini_nome.get(parte, (parte,)))
    return sorted(forme)


def _anomalia_omonimia(ammesse, nuova: Scheda) -> mod.Anomalia:
    prime = ammesse[:3]
    return mod.Anomalia(
        tipo="POSSIBLE_HOMONYM",
        individui=tuple(sorted(s.chiave for s, _ in prime) + [nuova.chiave]),
        descrizione=(
            f"{nuova.etichetta()} potrebbe essere una di "
            + " oppure ".join(s.etichetta() for s, _ in prime)
            + ": nessuna evidenza distingue le une dalle altre"
        ),
        atti=tuple(sorted({m.atto for m in nuova.menzioni})),
        spiegazioni=(
            "sono omonimi diversi",
            "una delle schede e' gia' la persona giusta",
            "l'eta' o il mestiere di una delle due e' letto male",
        ),
        evidenza=prime[0][1],
        confidenza=prime[0][1].confidenza,
        impatto=sum(s.quante for s, _ in prime),
        gravita="alta",
    )


# ---------------------------------------------------------------------------
# Il grafo: i legami come identita'
# ---------------------------------------------------------------------------

def aggiorna_grafo(esito: Esito) -> None:
    """Riscrive i legami di ogni scheda come schede, non come nomi.

    E' il passaggio che cambia tutto rispetto al primo giro. Fino a qui
    due schede si confrontano attraverso il nome del parente ricopiato
    dall'atto — 'il padre di questa e' Filippo Lella, il padre di quella
    e' Filippo Lella' — e il nome ricopiato porta addosso l'errore di chi
    ha letto la pagina. Da qui in poi si confrontano attraverso il
    parente stesso: 'il padre e' **quella** scheda'.
    """
    for scheda in esito.schede.values():
        scheda.padri = set()
        scheda.madri = set()
        scheda.coniugi = set()
        scheda.figli = set()

    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        for menzione in scheda.menzioni:
            for riferimento, dove, genitore in (
                (menzione.padre, scheda.padri, True),
                (menzione.madre, scheda.madri, True),
                (menzione.coniuge, scheda.coniugi, False),
            ):
                if riferimento is None:
                    continue
                altra = esito.di_menzione.get(riferimento)
                if altra is None or altra == chiave:
                    continue
                dove.add(altra)
                if genitore:
                    esito.schede[altra].figli.add(chiave)


# ---------------------------------------------------------------------------
# La riconciliazione
# ---------------------------------------------------------------------------

def _radice(padri: dict, chiave: int) -> int:
    """Dove e' finita una scheda dopo le fusioni di questo giro."""
    while padri.get(chiave, chiave) != chiave:
        chiave = padri[chiave]
    return chiave


def riconcilia(esito: Esito, sporche: set | None = None) -> tuple[int, set]:
    """Un giro di confronti a coppie. Rende le unioni fatte e chi ha toccato."""
    aggiorna_grafo(esito)
    coppie, affollati = candidati.tutte(
        esito.schede, esito.di_menzione,
        esito.modello.vicini_cognome, esito.modello.vicini_nome,
    )
    if affollati:
        esito.statistiche["scaffali affollati"] = len(affollati)

    if sporche is not None:
        coppie = [c for c in coppie if c[0] in sporche or c[1] in sporche]

    # Il punteggio si calcola una volta per coppia, prima di toccare
    # niente: cosi' l'ordine delle fusioni non dipende da quali fusioni
    # sono gia' state fatte, e due esecuzioni identiche danno lo stesso
    # albero.
    valutate: list[tuple[float, int, int]] = []
    per_nodo: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for una, altra in coppie:
        prima, seconda = esito.schede.get(una), esito.schede.get(altra)
        if prima is None or seconda is None:
            continue
        prove = evidenza.confronta(prima, seconda, esito.modello)
        if prove.impossibile is not None:
            continue
        if prove.logit >= SOGLIA_SEGNALAZIONE:
            per_nodo[una].append((prove.logit, altra))
            per_nodo[altra].append((prove.logit, una))
        if prove.logit >= SOGLIA_UNIONE:
            valutate.append((prove.logit, una, altra))

    valutate.sort(key=lambda riga: (-riga[0], riga[1], riga[2]))

    chiavi = esito.corpus.chiavi()
    padri: dict[int, int] = {}
    fatte = 0
    toccate: set = set()
    for punteggio, una, altra in valutate:
        prima_chiave = _radice(padri, una)
        seconda_chiave = _radice(padri, altra)
        if prima_chiave == seconda_chiave:
            continue
        prima = esito.schede.get(prima_chiave)
        seconda = esito.schede.get(seconda_chiave)
        if prima is None or seconda is None:
            continue

        # Le fusioni gia' fatte hanno cambiato le due schede: il punteggio
        # va rifatto, e puo' essere sia salito sia sceso.
        prove = evidenza.confronta(prima, seconda, esito.modello)
        if prove.impossibile is not None or prove.logit < SOGLIA_UNIONE:
            continue
        if _ambigua(esito, prima, seconda, prove.logit, per_nodo, padri):
            continue

        unita = Scheda.unione(prima, seconda, chiavi)
        unita.prove.append(prove.racconta(4))
        # La separazione e' la rete: se la fusione rende la scheda
        # incoerente, non si fa. E' un controllo in piu' rispetto ai veti
        # a coppie, perche' una scheda puo' diventare impossibile per la
        # somma di due meta' che prese a due a due non lo erano.
        if evidenza.incoerenze(unita):
            continue

        esito.schede[unita.chiave] = unita
        perdente = seconda_chiave if unita.chiave == prima_chiave else prima_chiave
        del esito.schede[perdente]
        padri[perdente] = unita.chiave
        padri[prima_chiave] = unita.chiave
        padri[seconda_chiave] = unita.chiave
        padri[unita.chiave] = unita.chiave
        for menzione in unita.menzioni:
            esito.di_menzione[menzione.id] = unita.chiave

        esito.decisioni.append(mod.Decisione(
            azione="unione",
            entita=(prima_chiave, seconda_chiave),
            motivo=f"{prima.etichetta()} + {seconda.etichetta()}",
            confidenza=prove.confidenza,
            evidenze=tuple(f"{i.dettaglio or i.tipo} ({i.peso:+.2f})" for i in prove.pro()),
            contraddizioni=tuple(
                f"{i.dettaglio or i.tipo} ({i.peso:+.2f})" for i in prove.contro()
            ),
            atti=tuple(sorted(unita.atti))[:12],
            quando=_adesso(),
        ))
        fatte += 1
        toccate.add(unita.chiave)
        toccate.update(prima.legami() | seconda.legami())
    return fatte, toccate


def _ambigua(
    esito: Esito, prima: Scheda, seconda: Scheda, punteggio: float,
    per_nodo: dict, padri: dict,
) -> bool:
    """Se esiste un candidato ugualmente buono e incompatibile con questo.

    E' la regola che separa gli omonimi invece di cucirli. Non basta che
    ci siano altri candidati: devono essere **incompatibili** con quello
    scelto, perche' due candidati che potrebbero essere fra loro la stessa
    persona non sono un'ambiguita' — sono due pezzi che verranno uniti a
    loro volta.
    """
    for scheda, altra_chiave in ((prima, seconda.chiave), (seconda, prima.chiave)):
        for rivale_punteggio, rivale in per_nodo.get(scheda.chiave, ()):
            if rivale_punteggio < punteggio - MARGINE_OMONIMIA:
                continue
            radice = _radice(padri, rivale)
            if radice in (scheda.chiave, altra_chiave):
                continue
            candidato = esito.schede.get(radice)
            altra = esito.schede.get(_radice(padri, altra_chiave))
            if candidato is None or altra is None:
                continue
            if evidenza.veti(candidato, altra) is not None:
                esito.statistiche["fusioni sospese per omonimia"] += 1
                esito.anomalie.append(mod.Anomalia(
                    tipo="POSSIBLE_HOMONYM",
                    individui=(scheda.chiave, altra.chiave, candidato.chiave),
                    descrizione=(
                        f"{scheda.etichetta()} somiglia allo stesso modo a "
                        f"{altra.etichetta()} e a {candidato.etichetta()}, che "
                        f"pero' non possono essere la stessa persona"
                    ),
                    spiegazioni=(
                        "sono due omonimi e la menzione appartiene a uno dei due",
                        "una delle date e' letta male",
                    ),
                    confidenza=0.5,
                    impatto=scheda.quante + altra.quante + candidato.quante,
                    gravita="alta",
                ))
                return True
    return False


# ---------------------------------------------------------------------------
# La separazione
# ---------------------------------------------------------------------------

def separa(esito: Esito) -> tuple[int, set]:
    """Divide le schede che contengono una contraddizione fisica.

    E' la meta' che al sistema precedente mancava del tutto. Una fusione
    sbagliata, li', restava per sempre: non c'era nessun passaggio che
    rileggesse una scheda intera e si accorgesse che quella donna
    partoriva per sessantun anni. Qui la scheda incoerente si smonta e si
    rimonta nei suoi pezzi coerenti, e resta scritto che e' successo.
    """
    chiavi = esito.corpus.chiavi()
    separate = 0
    toccate: set = set()
    for chiave in sorted(esito.schede):
        scheda = esito.schede.get(chiave)
        if scheda is None or scheda.quante < 2:
            continue
        guai = evidenza.incoerenze(scheda)
        if not guai:
            continue

        pezzi = _dividi(scheda, chiavi, esito.modello)
        if len(pezzi) < 2:
            continue

        del esito.schede[chiave]
        toccate.update(scheda.legami())
        for pezzo in pezzi:
            esito.schede[pezzo.chiave] = pezzo
            toccate.add(pezzo.chiave)
            for menzione in pezzo.menzioni:
                esito.di_menzione[menzione.id] = pezzo.chiave
        separate += 1
        esito.decisioni.append(mod.Decisione(
            azione="separazione",
            entita=tuple(p.chiave for p in pezzi),
            motivo=f"{scheda.etichetta()} conteneva: " + "; ".join(guai),
            confidenza=0.9,
            contraddizioni=tuple(guai),
            atti=tuple(sorted(scheda.atti))[:12],
            quando=_adesso(),
        ))
        esito.anomalie.append(mod.Anomalia(
            tipo="IDENTITY_ANOMALY",
            individui=tuple(p.chiave for p in pezzi),
            descrizione=(
                f"la scheda {scheda.etichetta()} conteneva cose incompatibili "
                f"({'; '.join(guai)}) ed e' stata divisa in {len(pezzi)}"
            ),
            spiegazioni=guai,
            confidenza=0.75,
            impatto=scheda.quante,
            gravita="alta",
        ))
    return separate, toccate


def coniugi_contemporanei(esito: Esito, scheda: Scheda) -> list[set] | None:
    """Le menzioni della scheda divise per coniuge, se i coniugi si accavallano.

    Rende i gruppi — uno per coniuge — oppure ``None`` se non c'e' niente
    da dire. E' l'unico modo che l'archivio ha di accorgersi di una
    fusione **al contrario**: non una persona spezzata in due, ma due
    persone schiacciate in una.

    Il caso che l'ha voluta: «Angela Lella», nata nel 1857, che nel
    ricostruito aveva **sei mariti** — Placido Pelliccia, Amedeo
    Femminilli, Amadio Rice, Amadio Meo, Nazario Pelliccia, Arcadio
    Femminilli — e figli da quattro di loro negli stessi anni. Nel paese
    ci sono quattro o cinque Angela Lella coetanee, e il cognome piu'
    comune del vicinato le teneva insieme.

    Due condizioni, tutte e due necessarie, perche' e' una regola che
    **spezza** e spezzare a vuoto e' il danno peggiore:

    1. i periodi si sovrappongono davvero. Una vedova che si risposa ha
       due mariti in due tratti di vita diversi, e quella e' una persona
       sola: si guarda l'intervallo di anni delle menzioni di ciascun
       coniuge, e si chiede che due si accavallino;
    2. i due coniugi non si somigliano. Se sono «Amedeo Femminilli» e
       «Arcadio Femminilli» il problema sta di la' — e' **lui** a essere
       spezzato in due — e tagliare anche lei raddoppierebbe il guaio.

    Una terza prudenza e' stata provata e **tolta**, e vale la pena
    lasciarne scritto il motivo: chiedere che ogni coniugio portasse
    almeno due menzioni — per non spezzare su un legame nato da un atto
    solo — faceva scendere le divisioni da 353 a 106 e peggiorava tutte e
    quattro le misure di qualita' insieme (frammentazioni da 412 a 448,
    accorpamenti da 109 a 175). I coniugi da una menzione sola sono
    fragili, ma nella somma dicono la verita' piu' spesso di quanto
    sbaglino.
    """
    gruppi: dict[int, set] = defaultdict(set)
    anni: dict[int, list[int]] = defaultdict(list)
    for menzione in scheda.menzioni:
        if menzione.coniuge is None:
            continue
        altro = esito.di_menzione.get(menzione.coniuge)
        if altro is None or altro == scheda.chiave:
            continue
        gruppi[altro].add(menzione.id)
        if menzione.anno:
            anni[altro].append(menzione.anno)
    if len(gruppi) < 2:
        return None

    def periodo(chiave: int) -> tuple[int, int] | None:
        suoi = anni.get(chiave) or []
        return (min(suoi), max(suoi)) if suoi else None

    accavallati: set[int] = set()
    chiavi_coniugi = sorted(gruppi)
    for indice, uno in enumerate(chiavi_coniugi):
        primo = periodo(uno)
        if primo is None:
            continue
        for altro in chiavi_coniugi[indice + 1:]:
            secondo = periodo(altro)
            if secondo is None:
                continue
            if primo[0] > secondo[1] or secondo[0] > primo[1]:
                continue        # due stagioni della vita, non due persone
            if _forse_lo_stesso(esito, uno, altro):
                continue
            accavallati.update((uno, altro))
    if len(accavallati) < 2:
        return None
    return [gruppi[chiave] for chiave in sorted(accavallati)]


def _forse_lo_stesso(esito: Esito, uno: int, altro: int) -> bool:
    """Due schede di coniuge che potrebbero essere lo stesso uomo spezzato."""
    prima, seconda = esito.schede.get(uno), esito.schede.get(altro)
    if prima is None or seconda is None:
        return True
    return evidenza.confronta(prima, seconda, esito.modello, coniugi=False).logit > 0


def separa_per_coniugi(esito: Esito) -> tuple[int, set]:
    """Spezza chi ha piu' coniugi negli stessi anni, seguendo gli atti.

    La divisione non la decide un punteggio: la decidono i documenti. Le
    menzioni si raggruppano per il coniuge che l'atto nomina accanto, e i
    gruppi diventano i semi di :func:`dividi_in`, che sistema le menzioni
    rimaste con lo stesso confronto che regge tutto il resto.
    """
    chiavi = esito.corpus.chiavi()
    separate, toccate = 0, set()
    for chiave in sorted(esito.schede):
        scheda = esito.schede.get(chiave)
        if scheda is None or scheda.quante < 3:
            continue
        semi = coniugi_contemporanei(esito, scheda)
        if semi is None:
            continue
        pezzi = dividi_in(scheda, chiavi, esito.modello, semi)
        if len(pezzi) < 2:
            continue
        del esito.schede[chiave]
        toccate.update(scheda.legami())
        for pezzo in pezzi:
            esito.schede[pezzo.chiave] = pezzo
            toccate.add(pezzo.chiave)
            for menzione in pezzo.menzioni:
                esito.di_menzione[menzione.id] = pezzo.chiave
        separate += 1
        esito.anomalie.append(mod.Anomalia(
            tipo="IDENTITY_ANOMALY",
            individui=tuple(p.chiave for p in pezzi),
            descrizione=(
                f"{scheda.etichetta()} risultava sposata a {len(semi)} persone "
                f"diverse negli stessi anni: e' stata divisa in {len(pezzi)}"
            ),
            spiegazioni=(
                "sono omonimi coetanei tenuti insieme da un cognome comune",
                "uno dei coniugi e' la stessa persona letta in due modi",
            ),
            confidenza=0.75, impatto=scheda.quante, gravita="alta",
        ))
    return separate, toccate


# Quante menzioni fanno di una scheda un **frammento**: una riga o due,
# cioe' quel che resta quando una lettura storta stacca una persona dal
# resto della sua vita. La regola sotto unisce solo i frammenti, perche'
# due schede piene con lo stesso nome possono benissimo essere due mogli
# vere — di due Maria un uomo ne sposava anche due.
MENZIONI_DI_UN_FRAMMENTO = 2

# Quanto deve essere piu' rara una forma di cognome perche' possa essere
# una lettura sbagliata dell'altra. Un decimo: 'Montanaro' (quindici
# volte) contro 'Moretta' (piu' di mille) si'; 'Mosca' (duecentosettantasei)
# contro 'Lella' (milletrecento) no — quelle sono due famiglie.
QUOTA_DI_UNA_LETTURA = 0.10


def _casati_conciliabili(esito: "Esito", una: Scheda, altra: Scheda) -> bool:
    """Lo stesso casato, oppure uno dei due puo' essere una lettura dell'altro.

    Una lettura sbagliata e' per forza rara: e' un incidente della penna o
    dell'occhio, e non si ripete centinaia di volte. Due casati frequenti
    che non si somigliano sono due famiglie, qualunque cosa dica il resto.
    Il silenzio non contraddice: senza cognome, o senza frequenza, si
    lascia decidere al resto della regola.
    """
    if _cognomi_compatibili(una, altra):
        return True
    frequenze = esito.corpus.frequenze_cognome

    def quante(scheda: Scheda) -> int:
        return max((frequenze.get(c, 0) for c in scheda.chiavi_cognome), default=0)

    mie, sue = quante(una), quante(altra)
    if not mie or not sue:
        return True
    if min(mie, sue) / max(mie, sue) > QUOTA_DI_UNA_LETTURA:
        return False
    # Una forma di poche righe puo' essere lo sbaglio di qualunque parola.
    # Una famiglia no: «Mosca», padre di Saba, e' meno di un decimo di
    # «Marianacci», ma ha 281 righe, e allora dev'essere almeno una parola
    # che la penna possa confondere con l'altra. Nessuna fa di Marianacci
    # Mosca.
    if min(mie, sue) < evidenza.RIGHE_DI_UNA_FAMIGLIA:
        return True
    return any(
        paleografia.somiglianza(mio, suo) >= evidenza.SOMIGLIANZA_DI_UNA_LETTURA
        for mio in una.chiavi_cognome for suo in altra.chiavi_cognome
    )


# Quanto devono somigliarsi i cognomi di due coniugi contemporanei perche'
# siano la stessa famiglia letta in due modi: Petolini e Petolina si',
# Petolini e Marianacci no.
SOMIGLIANZA_DELLA_STESSA_FAMIGLIA = 0.80


def _stessa_famiglia(una: Scheda, altra: Scheda) -> bool:
    """Le due schede portano lo stesso casato, o due letture della stessa parola."""
    if not una.chiavi_cognome or not altra.chiavi_cognome:
        return False
    if una.chiavi_cognome & altra.chiavi_cognome:
        return True
    return any(
        paleografia.somiglianza(mio, suo) >= SOMIGLIANZA_DELLA_STESSA_FAMIGLIA
        or _coda_di(mio, suo)
        for mio in una.chiavi_cognome for suo in altra.chiavi_cognome
    )


# La trascrizione perde l'inizio di un cognome: «Lella» per Colella (letto
# sulla pagina tre volte, tre mogli dello stesso marito), «Nardo» per Di
# Nardo, «Mosca» per «fu Mosca». La somiglianza non lo vede — Lella e Colella
# fanno 0,71 — perche' misura la parola intera. Quello che resta pero' e' la
# coda intera della parola: almeno quattro lettere e almeno meta' della
# parola, perche' 'tore' non e' Salvatore.
LETTERE_DI_UNA_CODA = 4


def _coda_di(una: str, altra: str) -> bool:
    """Una chiave di cognome e' la coda dell'altra: l'inizio perso nella lettura."""
    corta, lunga = sorted((una, altra), key=len)
    return (
        corta != lunga
        and len(corta) >= LETTERE_DI_UNA_CODA
        and 2 * len(corta) >= len(lunga)
        and lunga.endswith(corta)
    )


def _negli_stessi_anni(primo: list[int], secondo: list[int]) -> bool:
    """I figli e le nozze di due coppie si mescolano negli anni.

    Una vedova che si risposa ha due mariti in due tratti di vita: i figli
    del secondo nascono quando quelli del primo sono finiti. Se invece si
    accavallano, i due mariti erano vivi e sposati insieme.
    """
    return bool(primo and secondo) and min(primo) <= max(secondo) and min(secondo) <= max(primo)


# Quanti anni possono separare i figli di due coniugi dello stesso casato
# perche' non ci sia il tempo di una vedovanza e di nuove nozze. Fra un
# figlio e l'altro, a Torrebruna, passano due o tre anni: sei sono il
# doppio, e un lutto con un matrimonio nuovo non ci stanno.
ANNI_FRA_DUE_FIGLI = 6


def _di_seguito(primo: list[int], secondo: list[int]) -> bool:
    """I figli delle due coppie si danno il cambio senza un vuoto di anni."""
    if not primo or not secondo:
        return False
    return (min(secondo) - max(primo) <= ANNI_FRA_DUE_FIGLI
            and min(primo) - max(secondo) <= ANNI_FRA_DUE_FIGLI)


def _vivo_nell_atto(menzione) -> bool:
    """La riga non e' quella di un morto ne' di un «fu»."""
    return not (menzione.stato_vitale or "").startswith("defunt")


def _anno_da_coniugi(menzione, corpus) -> bool:
    """L'anno dell'atto e' un anno in cui i due coniugi sono vivi e insieme.

    Le nascite e le nozze lo sono sempre. Gli atti di morte no: un «fu
    Tizio» nell'atto di morte di un figlio vecchio allungherebbe la
    coppia di cinquant'anni, e la vedova risposata con la sorella della
    prima moglie sembrerebbe avere due mogli insieme. Valgono pero'
    quando l'atto nomina **tutt'e due** i coniugi da vivi: il padre che
    denuncia la morte del proprio bambino e' un uomo vivo, sposato in
    quell'anno con la madre che l'atto scrive accanto a lui. Era il caso
    di «Basilio Pelliccia», che nella morte del 1859 denuncia il figlio
    avuto da Maria Nicola Desiderio ed e' Savino Pelliccia letto male:
    la sua riga, scartata perche' l'atto era di morte, lo lasciava
    fuori da ogni confronto.
    """
    if menzione.tipo_atto in ("nascita", "matrimonio"):
        return True
    if menzione.tipo_atto != "morte":
        return False
    coniuge = corpus.per_id.get(menzione.coniuge)
    return coniuge is not None and _vivo_nell_atto(menzione) and _vivo_nell_atto(coniuge)


def _vedovanza_impossibile(primo: Scheda, suoi: list[int], secondi: list[int]) -> bool:
    """Il primo coniuge e' ancora vivo quando comincia il secondo coniugio.

    Ci si risposa da vedovi: perche' le seconde nozze stiano in piedi, il
    primo coniuge dev'essere morto prima. Quando invece il suo atto di
    morte porta una data **posteriore** all'anno in cui il secondo
    coniuge comincia, la vedovanza non c'e' mai stata, e il «secondo» e'
    il primo col nome letto in un altro modo.

    Il caso: Maria Nicola Desiderio del fu Bellisario sposa nel 1856
    Savino Antonio Pelliccia del fu Samuele, e ne ha figli fino al 1860;
    nella nascita del 1870 il marito e' trascritto «Donato Pelliccia del
    fu Samuele d'anni trentasei». Savino muore nel 1889 — dieci anni
    dopo quel 1870 — e nel 1876 si risposa: non era morto, e Donato non
    era un secondo marito. Sulla pagina, infatti, c'e' scritto Savino.

    Serve una morte scritta: l'ultima menzione non basta, perche' un
    «fu Tizio» nell'atto di un figlio la porterebbe avanti di anni.
    """
    if not suoi or not secondi or min(suoi) >= min(secondi):
        return False
    morte = primo.morte
    return morte is not None and morte > min(secondi)


def _coniugi_contemporanei_da_unire(esito: Esito, scheda: Scheda):
    """Due coniugi della scheda con figli negli stessi anni e lo stesso casato."""
    anni: dict[int, list[int]] = defaultdict(list)
    for menzione in scheda.menzioni:
        if menzione.coniuge is None or not menzione.anno:
            continue
        # Solo gli anni in cui la coppia e' **viva e insieme**: vedi
        # :func:`_anno_da_coniugi`.
        if not _anno_da_coniugi(menzione, esito.corpus):
            continue
        altro = esito.di_menzione.get(menzione.coniuge)
        if altro is not None and altro != scheda.chiave:
            anni[altro].append(menzione.anno)
    coniugi = sorted(anni)
    for indice, uno in enumerate(coniugi):
        for altro in coniugi[indice + 1:]:
            una, altra = esito.schede.get(uno), esito.schede.get(altro)
            if una is None or altra is None or not _stessa_famiglia(una, altra):
                continue
            if not _negli_stessi_anni(anni[uno], anni[altro]):
                # Due stagioni della vita — vedovanza e nuove nozze — ma solo
                # se c'e' il tempo per viverle. Un coniuge-frammento che da'
                # un figlio due anni dopo l'altro, dello stesso casato, non
                # e' un secondo marito: e' il primo, col nome letto male.
                frammento = min(una.quante, altra.quante) <= MENZIONI_DI_UN_FRAMMENTO
                if not ((frammento and _di_seguito(anni[uno], anni[altro]))
                        or _vedovanza_impossibile(una, anni[uno], anni[altro])
                        or _vedovanza_impossibile(altra, anni[altro], anni[uno])):
                    continue
            if evidenza.veti(una, altra, divisioni_del_calcolo=False) is not None:
                continue
            return una, altra
    return None


# Provato e scartato: chiedere alle fusioni di fine giro che la scheda
# unita non porti piu' contraddizioni di quante ne portassero le due
# separate (`len(evidenza.incoerenze(unita)) <= len(incoerenze(una)) +
# len(incoerenze(altra))`), e in una seconda versione anche che nessuna
# eta' dichiarata cadesse sotto l'eta' minima di un ruolo gia' ricoperto.
#
# Nasceva da un caso vero: Agostino Pelliccia sposa una Maria Marianacci
# di vent'anni nel 1886 e un'altra di vent'anni nel 1900, e il passaggio
# dei coniugi omonimi le univa, facendo sposare la seconda a sei anni.
#
# Misurato tre volte di fila, il conto e' sempre peggiorato: le fusioni
# di fine giro sono scese da sessantatre a ventuno, i coniugi duplicati
# saliti da 19 a 29 e le coppie gemelle da 47 a 51, per una cosa
# impossibile in meno. Quell'unica sposa bambina si vede nel rapporto di
# qualita' e si corregge con una separazione decisa a mano; le quaranta
# famiglie che restavano spezzate non si vedevano piu'. Vale la regola
# di sempre: la frammentazione costa piu' di un'unione sbagliata.


def unisci_coniugi_contemporanei(esito: Esito) -> int:
    """Due coniugi con figli negli stessi anni, della stessa famiglia, sono uno.

    Nell'Ottocento non si divorzia, e ci si risposa solo da vedovi: una
    persona **non puo'** avere due coniugi vivi insieme. Quando i figli
    che ha con l'uno nascono in mezzo a quelli che ha con l'altro, una
    delle due schede e' sbagliata. Se i due coniugi sono di due famiglie,
    e' la persona in mezzo a essere due omonimi schiacciati in uno, e la
    divide :func:`separa_per_coniugi`. Se sono **della stessa famiglia**,
    e' il coniuge a essere spezzato in due, e il nome e' stato letto in
    due modi: e allora li si rimette insieme qui, prima che la divisione
    spezzi la persona sbagliata.

    I casi: Giuseppe Pepe aveva per moglie Celidata Petolini — scritta
    anche Calidata, Calideta, Celidonia — e **Candidata** Petolini, con
    figli di tutte e due fra il 1809 e il 1825. Angela Maria Lella aveva
    per marito un Cicchillitti scritto Gesualdo, Gualdo, Segualdo,
    Romualdo e Rinaldo. Nessun confronto fra nomi li poteva riconoscere:
    li riconosce il fatto che non si possono avere due mogli insieme.

    Quando gli anni invece non si accavallano, la vedovanza resta una
    spiegazione possibile — a meno che il primo coniuge non sia
    documentato **vivo** dopo l'inizio del secondo coniugio: allora
    nemmeno quella regge, e la fusione si fa lo stesso. Vedi
    :func:`_vedovanza_impossibile`.

    I veti valgono come sempre: due atti di morte, due atti di nascita,
    una vita che non torna fermano la fusione.
    """
    chiavi = esito.corpus.chiavi()
    unite = 0
    for chiave in sorted(esito.schede):
        scheda = esito.schede.get(chiave)
        if scheda is None:
            continue
        while (coppia := _coniugi_contemporanei_da_unire(esito, scheda)) is not None:
            _fondi(esito, *coppia, chiavi)
            unite += 1
    return unite


def unisci_genitori_dello_stesso_figlio(esito: Esito) -> int:
    """Un figlio ha una madre sola, e un padre solo.

    Se la scheda di un figlio punta a due schede di madre — la madre
    scritta nel suo atto di nascita e quella scritta nel suo atto di
    morte o di matrimonio — le due sono la stessa donna letta in due
    modi. Il caso: Giuseppe Antonio Salvatore, nato nel 1809 da Nicola
    Salvatore e **Orsola** Cicchillitti, sposo nel 1838 figlio di Nicola
    Salvatore e **Clorinda** Cicchillitti. Il nome della madre non si
    riconosce; lo riconosce il figlio, che di madri ne ha una.

    Tre prudenze. L'altro genitore dev'essere uno solo: se il figlio ha
    anche due padri, a essere doppio e' lui — due bambini cuciti in una
    scheda — e fondere le madri raddoppierebbe il guaio. Le due schede
    devono essere della stessa famiglia (:func:`_stessa_famiglia`): due
    madri di due casati possono essere la stessa donna letta malissimo, ma
    anche il segno che il figlio e' sbagliato, e nel dubbio si lascia. E i
    veti valgono come sempre.
    """
    chiavi = esito.corpus.chiavi()
    unite = 0
    for _ in range(2):
        aggiorna_grafo(esito)
        toccate: set[int] = set()
        fatte = 0
        for chiave in sorted(esito.schede):
            figlio = esito.schede.get(chiave)
            if figlio is None:
                continue
            for genitori, altri in ((figlio.madri, figlio.padri), (figlio.padri, figlio.madri)):
                if len(genitori) < 2 or len(altri) > 1:
                    continue
                schede = [esito.schede.get(c) for c in sorted(genitori)]
                schede = [s for s in schede if s is not None and s.chiave not in toccate]
                coppia = next((
                    (una, altra)
                    for indice, una in enumerate(schede) for altra in schede[indice + 1:]
                    if _stessa_famiglia(una, altra)
                    and evidenza.veti(una, altra, divisioni_del_calcolo=False) is None
                ), None)
                if coppia is None:
                    continue
                unita = _fondi(esito, *coppia, chiavi)
                toccate.update((coppia[0].chiave, coppia[1].chiave, unita.chiave))
                fatte += 1
        unite += fatte
        if not fatte:
            break
    return unite


def unisci_coniugi_omonimi(esito: Esito) -> int:
    """Due coniugi della stessa persona, con lo stesso nome, sono uno solo.

    E' il rovescio esatto di :func:`separa_per_coniugi`. Li' due coniugi
    **diversi** negli stessi anni dicevano che la scheda in mezzo teneva
    insieme due persone; qui due coniugi con **lo stesso nome** dicono il
    contrario: che a essere spezzato in due e' il coniuge.

    Due casi veri, tutti e due dall'albero di Filippo Lella:

    * Angela Moretta risultava sposata a **tre Egidio Pelliccia** — uno
      con venti menzioni e due con una ciascuna, staccate dal resto della
      sua vita da una lettura storta;
    * Domenico Di Nardo aveva per moglie **Antonia Mouton** e **Antonia
      Montanaro**, madri dei suoi due figli morti nel 1828 e nel 1829.
      Sono la stessa donna: «Mouton» e «Montanaro» sono due modi di
      sbagliare lo stesso cognome, che compare quindici volte e due.

    Il cognome entra nella condizione solo per quello che puo' dire. Nel
    secondo caso e' proprio il cognome a essere letto in due modi, ed e'
    il **nome** a reggere il riconoscimento — ma una lettura sbagliata e'
    per forza rara, e due casati frequenti sono due famiglie. «Giuseppe
    Mosca», padre di Saba nella morte del 1887, era finito dentro Giuseppe
    **Lella**: la moglie di tutti e due era la stessa scheda sbagliata, e
    il cognome non contava (vedi ``_casati_conciliabili``). A tenere la
    regola prudente
    ci pensano due cose: si unisce solo quando almeno una delle due
    schede e' un frammento (:data:`MENZIONI_DI_UN_FRAMMENTO`), e i veti
    valgono comunque — due atti di morte, due atti di nascita, una vita
    che finisce prima di cominciare fermano la fusione come sempre.
    """
    chiavi = esito.corpus.chiavi()
    unite = 0
    for chiave in sorted(esito.schede):
        scheda = esito.schede.get(chiave)
        if scheda is None:
            continue
        gruppi: dict[int, set] = defaultdict(set)
        for menzione in scheda.menzioni:
            if menzione.coniuge is None:
                continue
            altro = esito.di_menzione.get(menzione.coniuge)
            if altro is not None and altro != chiave:
                gruppi[altro].add(menzione.id)
        if len(gruppi) < 2:
            continue
        candidati = sorted(gruppi)
        for indice, uno in enumerate(candidati):
            for altro in candidati[indice + 1:]:
                prima = esito.schede.get(uno)
                seconda = esito.schede.get(altro)
                if prima is None or seconda is None or prima is seconda:
                    continue
                # Due vite intere si fondono solo se il nome e' uguale e il
                # casato e' lo stesso: risposarsi e' raro, risposarsi con
                # una donna dello stesso nome e della stessa famiglia lo e'
                # molto di piu'. Celidata e Candidata Petolini, Clementina
                # Iorio nata nel 1799 e nel 1802: una moglie letta due volte.
                if (min(prima.quante, seconda.quante) > MENZIONI_DI_UN_FRAMMENTO
                        and not _stessa_famiglia(prima, seconda)):
                    continue
                if not evidenza._due_schede_con_lo_stesso_nome(prima, seconda):
                    continue
                if not _casati_conciliabili(esito, prima, seconda):
                    continue
                if evidenza.veti(
                    prima, seconda, divisioni_del_calcolo=False
                ) is not None:
                    continue
                _fondi(esito, prima, seconda, chiavi)
                unite += 1
    return unite


def _fondi(esito: Esito, prima: Scheda, seconda: Scheda, chiavi) -> Scheda:
    """Mette due schede in una e aggiorna l'esito. Non fa nessun controllo."""
    unita = Scheda.unione(prima, seconda, chiavi)
    esito.schede[unita.chiave] = unita
    for perdente in (prima.chiave, seconda.chiave):
        if perdente != unita.chiave:
            esito.schede.pop(perdente, None)
    for menzione in unita.menzioni:
        esito.di_menzione[menzione.id] = unita.chiave
    return unita


def _cognomi_compatibili(una: Scheda, altra: Scheda) -> bool:
    """Il silenzio non contraddice: un cognome che manca non e' un cognome diverso."""
    if not una.chiavi_cognome or not altra.chiavi_cognome:
        return True
    return bool(una.chiavi_cognome & altra.chiavi_cognome)


def _puo_essere_lo_stesso(esito: Esito, una: Scheda, altra: Scheda, chiavi) -> bool:
    """Le due schede reggono la fusione: stesso nome, stesso casato, nessun veto."""
    if una.chiave == altra.chiave:
        return True
    if not evidenza._due_schede_con_lo_stesso_nome(una, altra):
        return False
    if not _cognomi_compatibili(una, altra):
        return False
    if evidenza.veti(una, altra, divisioni_del_calcolo=False) is not None:
        return False
    return not evidenza.incoerenze(Scheda.unione(una, altra, chiavi))


def coppie_dai_figli(esito: Esito) -> dict:
    """Le coppie di genitori, con i figli che le documentano."""
    coppie: dict[tuple[int, int], set] = defaultdict(set)
    for chiave in sorted(esito.schede):
        scheda = esito.schede[chiave]
        for padre in sorted(scheda.padri):
            for madre in sorted(scheda.madri):
                coppie[(padre, madre)].add(chiave)
    return coppie


def _bucket(scheda: Scheda) -> tuple[str, str]:
    """Una chiave grossolana per non confrontare tutte le coppie a due a due."""
    nome = min(scheda.chiavi_nome) if scheda.chiavi_nome else ""
    cognome = min(scheda.chiavi_cognome) if scheda.chiavi_cognome else ""
    return nome[:3], cognome[:3]


def unisci_coppie_gemelle(esito: Esito) -> int:
    """Due nuclei con gli stessi due genitori sono un nucleo solo.

    E' la frammentazione che fa piu' danno, e il controllo di qualita' la
    conta gia' da tempo — «coppie gemelle» e' la voce piu' alta di tutte:
    non spezza una persona ma una **famiglia**, e i fratelli finiscono in
    due nuclei che da quel momento non si riconoscono piu' come tali.

    Fin qui la si contava e basta. Contarla non bastava: il confronto a
    coppie guarda due schede alla volta, e due «Luigi Moretta» che si
    somigliano poco abbastanza da non unirsi restano due anche quando
    accanto a ciascuno c'e' la **stessa moglie**. La prova non e' in
    nessuna delle due schede: e' nella coppia.

    Il caso: Luigi Moretta, nato nel 1819 da Carmine Moretta e Maria
    Lella, e sua moglie Lucia Sciulli. Nell'atto n. 5 del 1860 la moglie
    era stata letta «Anna» — la pagina scrive Lucia — e da quel momento
    l'uomo che nel 1852 dichiara una morte a trentadue anni e quello che
    nel 1867 presenta Maria Celeste a quarantasette erano due uomini.

    Quattro nomi che tornano insieme, piu' i figli, sono molto piu' di
    quanto chieda una fusione a due; e cio' che si chiede in piu' e' che
    ciascuna delle due fusioni regga per conto suo — stesso nome, casati
    conciliabili, nessun veto, e la scheda che ne esce non incoerente.

    Va **a giri finiti**, come ``unisci_coniugi_omonimi``: prima le
    schede dei genitori non hanno ancora la forma definitiva, e una
    divisione del calcolo non e' un ostacolo (vedi ``evidenza.veti``).

    Misurato sull'archivio intero, ottantadue fusioni: le frammentazioni
    scendono da 304 a 287, gli accorpamenti salgono da 93 a 102, gli
    impossibili restano 64. Si tiene perche' e' il verso giusto — una
    famiglia divisa costa piu' di un uomo con una moglie di troppo, e
    una moglie di troppo si vede, mentre due fratelli che non si
    riconoscono no — ma il saldo e' stretto, e va rimisurato ogni volta
    che si tocca.
    """
    chiavi = esito.corpus.chiavi()
    per_bucket: dict[tuple, list] = defaultdict(list)
    for padre, madre in sorted(coppie_dai_figli(esito)):
        uno, altra = esito.schede.get(padre), esito.schede.get(madre)
        if uno is None or altra is None:
            continue
        per_bucket[(_bucket(uno), _bucket(altra))].append((padre, madre))

    spostate: dict[int, int] = {}
    unite = 0
    for vicine in per_bucket.values():
        for indice, una in enumerate(vicine):
            for altra in vicine[indice + 1:]:
                padri = (_radice(spostate, una[0]), _radice(spostate, altra[0]))
                madri = (_radice(spostate, una[1]), _radice(spostate, altra[1]))
                if padri[0] == padri[1] and madri[0] == madri[1]:
                    continue        # e' gia' un nucleo solo
                schede = [esito.schede.get(c) for c in (*padri, *madri)]
                if any(s is None for s in schede):
                    continue
                if not _puo_essere_lo_stesso(esito, schede[0], schede[1], chiavi):
                    continue
                if not _puo_essere_lo_stesso(esito, schede[2], schede[3], chiavi):
                    continue
                for prima, seconda in ((schede[0], schede[1]), (schede[2], schede[3])):
                    if prima.chiave == seconda.chiave:
                        continue
                    fusa = _fondi(esito, prima, seconda, chiavi)
                    for vecchia in (prima.chiave, seconda.chiave):
                        spostate[vecchia] = fusa.chiave
                    spostate[fusa.chiave] = fusa.chiave
                    unite += 1
    return unite


def unisci_righe_doppie(esito: Esito) -> int:
    """Rimette nella scheda dell'originale le righe lette due volte.

    Le marca ``menzioni.righe_lette_due_volte``; qui si uniscono le
    schede, con i veti di sempre — un atto di nascita in piu', una morte
    prima del matrimonio — e senza unire niente che diventi incoerente.
    Le divisioni del calcolo non contano: una riga ripetuta e' la stessa
    riga, e nessuna ipotesi fatta prima vale contro la struttura dell'atto.
    """
    chiavi = esito.corpus.chiavi()
    unite = 0
    for menzione in esito.corpus.menzioni:
        if menzione.doppia_di is None:
            continue
        mia = esito.di_menzione.get(menzione.id)
        sua = esito.di_menzione.get(menzione.doppia_di)
        if mia is None or sua is None or mia == sua:
            continue
        una, altra = esito.schede[mia], esito.schede[sua]
        # Il patronimico qui non e' una prova contro: la riga doppia e' il
        # dichiarante stesso, e il suo patronimico e' il nome del nonno letto
        # su quella pagina. Nella nascita del 1858 n. 36 il nonno e' scritto
        # «Giudeto», in tutto il resto dell'archivio «Diodato»: le due
        # letture si somigliano per 0,57, sotto la soglia che separa due
        # padri, e il veto teneva fuori per sempre una riga che la regola
        # 'padre_che_e_il_nonno' aveva gia' riconosciuto come Vincenzo
        # Pannunzio - lasciandolo con due mogli e la moglie con due mariti.
        if evidenza.veti(una, altra, divisioni_del_calcolo=False, patronimici=False) is not None:
            continue
        if evidenza.incoerenze(Scheda.unione(una, altra, chiavi)):
            continue
        _fondi(esito, una, altra, chiavi)
        unite += 1
    return unite


def sposta_al_coniuge_omonimo(esito: Esito) -> int:
    """Quando le schede non si possono unire, si sposta la menzione.

    ``unisci_coniugi_omonimi`` rimette insieme due coniugi omonimi della
    stessa persona quando uno dei due e' un frammento. Quando nessuno dei
    due lo e' non c'e' niente da unire — possono essere, e spesso sono,
    due uomini veri — ma resta da guardare la **riga** che lega il
    secondo alla moglie comune.

    Il caso: Maria Michela Di Nardo, sposata con Giuseppe Colella nel
    1878, risultava moglie anche di un secondo Giuseppe Colella. Sono
    davvero due uomini: il secondo e' nato a Celenza ed e' morto nel 1895
    «maritato con Donna Michela Di Verde», il primo dichiara ancora nel
    1896 e nel 1898, e il veto ha ragione a tenerli separati. Ma una riga
    del secondo non era sua: il dichiarante della nascita del 1880, padre
    di un figlio di Maria Michela, con ventiquattro anni come il marito.
    Era il marito, e la riga era finita nella scheda sbagliata.

    Si sposta solo la riga che lega l'altro alla moglie comune — una
    scheda intera sarebbe una fusione, e quella la decide chi unisce — e
    solo se il marito principale la accoglie senza veti e senza diventare
    incoerente. Il principale e' quello che la moglie nomina piu' volte.
    """
    chiavi = esito.corpus.chiavi()
    spostate = 0
    for chiave in sorted(esito.schede):
        scheda = esito.schede.get(chiave)
        if scheda is None:
            continue
        gruppi: dict[int, set] = defaultdict(set)
        for menzione in scheda.menzioni:
            if menzione.coniuge is None:
                continue
            altro = esito.di_menzione.get(menzione.coniuge)
            if altro is not None and altro != chiave:
                gruppi[altro].add(menzione.coniuge)
        if len(gruppi) < 2:
            continue
        ordine = sorted(gruppi, key=lambda k: (
            -len(gruppi[k]), -esito.schede[k].quante, k
        ))
        principale_chiave = ordine[0]
        for altro_chiave in ordine[1:]:
            principale = esito.schede.get(principale_chiave)
            altro = esito.schede.get(altro_chiave)
            if principale is None or altro is None or principale is altro:
                continue
            if not evidenza._due_schede_con_lo_stesso_nome(principale, altro):
                continue
            if not _cognomi_compatibili(principale, altro):
                continue
            da_spostare = [m for m in altro.menzioni if m.id in gruppi[altro_chiave]]
            if not da_spostare or len(da_spostare) == len(altro.menzioni):
                continue
            pezzo = Scheda.dalla_menzione(da_spostare[0], chiavi)
            for menzione in da_spostare[1:]:
                pezzo = Scheda.unione(pezzo, Scheda.dalla_menzione(menzione, chiavi), chiavi)
            if evidenza.veti(principale, pezzo, divisioni_del_calcolo=False) is not None:
                continue
            accresciuta = Scheda.unione(principale, pezzo, chiavi)
            if evidenza.incoerenze(accresciuta):
                continue
            restano = [m for m in altro.menzioni if m.id not in pezzo.ids]
            ridotta = Scheda(chiave=min(m.id for m in restano), menzioni=restano)
            ridotta.ricalcola(chiavi)
            ridotta.vietati = set(altro.vietati) - ridotta.ids
            ridotta.eredita_divieti(altro)

            esito.schede.pop(principale.chiave, None)
            esito.schede.pop(altro.chiave, None)
            esito.schede[accresciuta.chiave] = accresciuta
            esito.schede[ridotta.chiave] = ridotta
            for menzione in accresciuta.menzioni:
                esito.di_menzione[menzione.id] = accresciuta.chiave
            for menzione in ridotta.menzioni:
                esito.di_menzione[menzione.id] = ridotta.chiave
            principale_chiave = accresciuta.chiave
            spostate += len(da_spostare)
    return spostate


def _dividi(scheda: Scheda, chiavi, modello: Modello) -> list[Scheda]:
    """Smonta una scheda incoerente nei suoi gruppi compatibili.

    Le menzioni si riprendono in ordine di forza — prima quelle che
    portano una data certa, che sono quelle che definiscono i gruppi — e
    ognuna va nel gruppo che la accoglie meglio fra quelli che non la
    contraddicono. Una menzione che nessun gruppo puo' accogliere ne apre
    uno nuovo, e nessuna riga va persa.
    """
    gruppi: list[Scheda] = []
    for menzione in sorted(scheda.menzioni, key=lambda m: (_forza(m), m.anno, m.id)):
        una = Scheda.dalla_menzione(menzione, chiavi)
        migliore, punteggio = None, None
        for gruppo in gruppi:
            if evidenza.veti(gruppo, una) is not None:
                continue
            prove = evidenza.confronta(gruppo, una, modello)
            if prove.impossibile is not None:
                continue
            unita = Scheda.unione(gruppo, una, chiavi)
            if evidenza.incoerenze(unita):
                continue
            if punteggio is None or prove.logit > punteggio:
                migliore, punteggio = gruppo, prove.logit
        if migliore is None:
            gruppi.append(una)
        else:
            migliore.aggiungi(menzione, chiavi)
    return gruppi


# ---------------------------------------------------------------------------
# L'orchestrazione
# ---------------------------------------------------------------------------

def applica_decisioni(esito: Esito, imposizioni) -> Counter:
    """Rimette in gioco le decisioni gia' prese da Claude o da una persona.

    E' cio' che chiude il cerchio. Senza, il giudizio di chi ha guardato
    la carta vale una volta sola: la ricostruzione successiva rifarebbe
    gli stessi conti e tornerebbe alla stessa conclusione, e l'archivio
    non imparerebbe niente.

    Le imposizioni scavalcano il punteggio ma **non i veti**: se unire due
    schede produrrebbe una donna che partorisce dopo il proprio funerale,
    la fusione non si fa e resta scritto perche'. Non e' sfiducia verso
    chi ha deciso — una persona puo' avere ragione sul caso e sbagliare
    sulla scheda — ed e' l'unico modo perche' l'archivio non contenga
    affermazioni false.
    """
    chiavi = esito.corpus.chiavi()
    conteggi: Counter = Counter()

    # Una decisione alla volta, nell'ordine in cui sono state prese. Le
    # menzioni staccate dalla **stessa** decisione fanno un pezzo solo —
    # chi ha deciso ha detto che vanno insieme — ma due decisioni diverse
    # sulla stessa scheda sono due tagli distinti, e vanno dati uno dopo
    # l'altro. Raccoglierle tutte in un insieme unico costava caro:
    # staccare da Rebecca Lella la sua morte (che va a Maria) e una
    # menzione incerta (che va ad Angela Maria) le metteva nello stesso
    # pezzo, e la prima unione se le portava via tutt'e due.
    # L'ordine e' quello delle schede, non quello delle decisioni: una
    # separazione si propaga ai parenti, e la propagazione dipende da
    # cosa e' gia' stato tagliato. Ordinare per scheda tiene il risultato
    # stabile fra un'esecuzione e l'altra anche quando si aggiunge una
    # decisione in fondo al registro — misurato sull'archivio: con
    # l'ordine delle decisioni le frammentazioni salivano di diciassette.
    gruppi = sorted(
        (
            (esito.di_menzione.get(resta), resta, staccate)
            for resta, staccate in imposizioni.get("separare", ())
        ),
        key=lambda gruppo: (gruppo[0] is None, gruppo[0] or 0),
    )
    for _, resta, staccate in gruppi:
        chiave = esito.di_menzione.get(resta)
        if chiave is None:
            continue
        insieme = {
            menzione for menzione in staccate
            if esito.di_menzione.get(menzione) == chiave
        }
        if not insieme:
            # Gia' separate: non c'e' niente da tagliare, ma la decisione
            # va ricordata (vedi 'segna_separate').
            segna_separate(esito, resta, staccate)
            continue
        scheda = esito.schede.get(chiave)
        if scheda is None:
            continue
        pezzi = _dividi_su(scheda, chiavi, insieme, resta)
        if len(pezzi) < 2:
            continue
        del esito.schede[chiave]
        for pezzo in pezzi:
            esito.schede[pezzo.chiave] = pezzo
            for menzione in pezzo.menzioni:
                esito.di_menzione[menzione.id] = pezzo.chiave
        # Dopo il taglio, non prima: il pezzo staccato eredita i segni
        # della scheda da cui viene, e un segno messo prima contro le
        # staccate rimaste fuori finiva addosso a lui. Le due Amalia
        # Marianacci, staccate insieme da Sofia Amalia, non potevano piu'
        # tornare insieme per colpa della decisione che le diceva insieme.
        segna_separate(esito, resta, staccate)
        conteggi["separazioni imposte"] += 1
        conteggi["parenti divisi di conseguenza"] += propaga_separazione(
            esito, pezzi, chiavi, esito.modello
        )

    for prima_chiave, seconda_chiave in imposizioni.get("unire", ()):
        una = esito.schede.get(esito.di_menzione.get(prima_chiave))
        altra = esito.schede.get(esito.di_menzione.get(seconda_chiave))
        if una is None or altra is None or una is altra:
            continue
        # Una divisione del calcolo non ferma una decisione: il calcolo ci
        # puo' tornare sopra da solo, e chi ha guardato la carta ne sa di
        # piu'. Ludovico Pelliccia, padre di trentatre anni nel 1842, restava
        # lontano dal marito di Maria Di Nardo per una divisione fatta in un
        # giro precedente. Le separazioni decise sulla pagina e le cose
        # impossibili restano veti.
        impossibile = evidenza.veti(una, altra, divisioni_del_calcolo=False)
        if impossibile is not None:
            conteggi["unioni imposte rifiutate"] += 1
            esito.anomalie.append(mod.Anomalia(
                tipo="IDENTITY_ANOMALY",
                individui=(una.chiave, altra.chiave),
                descrizione=(
                    f"una decisione presa prima univa {una.etichetta()} e "
                    f"{altra.etichetta()}, ma oggi non si puo': {impossibile}"
                ),
                spiegazioni=(
                    "la decisione era sbagliata",
                    "le schede sono cambiate e ora contengono altro",
                ),
                confidenza=0.5, impatto=una.quante + altra.quante, gravita="alta",
            ))
            continue
        unita = Scheda.unione(una, altra, chiavi)
        esito.schede[unita.chiave] = unita
        perdente = altra.chiave if unita.chiave == una.chiave else una.chiave
        esito.schede.pop(perdente, None)
        for menzione in unita.menzioni:
            esito.di_menzione[menzione.id] = unita.chiave
        conteggi["unioni imposte"] += 1

    esito.statistiche.update(conteggi)
    return conteggi


def dividi_in(scheda: Scheda, chiavi, modello: Modello, semi: list) -> list[Scheda]:
    """Divide una scheda a partire da due gruppi di menzioni indicati.

    I ``semi`` sono l'ipotesi di chi ha letto il caso — un modello o una
    persona — e quasi mai coprono tutte le menzioni: il fascicolo ne
    mostra due dozzine, e una scheda sbagliata ne ha spesso cento. Le
    altre non si buttano e non restano dove capita: si assegnano al seme
    che le accoglie meglio, con lo stesso confronto che regge tutto il
    resto della fase.

    E' la divisione del lavoro giusta fra le due parti: **il giudizio a
    chi legge, la contabilita' al calcolo**. Senza questo, una
    separazione decisa su ventiquattro righe ne lasciava settanta dalla
    parte sbagliata, e la scheda restava lunga una vita e mezza.
    """
    per_id = {m.id: m for m in scheda.menzioni}
    gruppi: list[Scheda] = []
    for seme in semi:
        menzioni = [per_id[m] for m in sorted(seme) if m in per_id]
        if not menzioni:
            continue
        pezzo = Scheda(chiave=min(m.id for m in menzioni), menzioni=menzioni)
        pezzo.ricalcola(chiavi)
        gruppi.append(pezzo)
    if len(gruppi) < 2:
        return [scheda]

    assegnate = set().union(*(g.ids for g in gruppi))
    restanti = [m for m in scheda.menzioni if m.id not in assegnate]
    for menzione in sorted(restanti, key=lambda m: (_forza(m), m.anno, m.id)):
        una = Scheda.dalla_menzione(menzione, chiavi)
        migliore, punteggio = None, None
        for gruppo in gruppi:
            if evidenza.veti(gruppo, una) is not None:
                continue
            prove = evidenza.confronta(gruppo, una, modello)
            if prove.impossibile is None and (
                punteggio is None or prove.logit > punteggio
            ):
                migliore, punteggio = gruppo, prove.logit
        (migliore or gruppi[0]).aggiungi(menzione, chiavi)

    # Ogni pezzo si ricorda di dover stare lontano dagli altri: senza,
    # la riconciliazione del giro dopo li rimetterebbe insieme.
    tutti = set().union(*(g.ids for g in gruppi))
    for gruppo in gruppi:
        gruppo.vietati = set(scheda.vietati) | (tutti - gruppo.ids)
        # Ereditato, non aggiunto: questa e' una divisione del calcolo,
        # e il calcolo puo' tornarci sopra (vedi 'evidenza.veti').
        gruppo.eredita_divieti(scheda)
    return gruppi


def segna_separate(esito: Esito, resta: int, staccate) -> None:
    """Una separazione decisa sulla pagina vale anche se le righe sono gia' in due schede.

    Il caso: Giambattista Lella e Francesco Colella, mariti di due Maria
    Pelliccia. Quando le decisioni si applicano, le loro righe stanno gia'
    in due schede, e la separazione non aveva niente da tagliare — e
    niente da ricordare. Poi i passaggi di fine giro vedono una Maria
    Pelliccia con due mariti negli stessi anni e di casati che si
    somigliano (Lella, Colella), e li rimettono insieme: la decisione era
    persa, e l'albero dava alla moglie di Giambattista un figlio a
    cinquantaquattro anni.

    Il segno va sulla sola scheda della riga che resta, con gli
    identificatori delle righe staccate: ``evidenza.veti`` guarda tutte e
    due le direzioni, e basta. Segnare anche le schede delle staccate
    marchiava righe che la decisione non nomina: la scheda di Pietro
    Lorenzo Marianacci conteneva per errore anche il marito di Clementina
    Franchella, e quando la si e' divisa lui si e' portato dietro il
    divieto contro il proprio sposalizio del 1813. :meth:`Scheda.unione`
    porta il segno con se' in ogni fusione successiva. Modifica sul posto.
    """
    scheda = esito.schede.get(esito.di_menzione.get(resta))
    if scheda is not None:
        scheda.vieta(resta, set(staccate) - scheda.ids)


def propaga_separazione(esito: Esito, pezzi: list, chiavi, modello: Modello) -> int:
    """Dopo aver diviso una persona, divide chi le stava intorno.

    E' il passo che mancava, e si vede nei numeri: separare un uomo in
    due lascia sua moglie legata a tutti e due i pezzi, e da quel momento
    lei risulta avere due mariti con lo stesso nome. Il controllo di
    qualita' lo conta come frammentazione, e ha ragione — la famiglia e'
    divisa a meta'.

    Il criterio non e' un punteggio ma **l'atto**: se le menzioni di una
    moglie si dividono nettamente fra quelle che stanno negli atti del
    primo marito e quelle che stanno negli atti del secondo, allora sono
    due donne, e la divisione la dicono i documenti. Se invece si
    mescolano, non si tocca niente: vorrebbe dire che il taglio a monte
    era sbagliato, e sbagliarne due non fa un giusto.

    Si propaga **solo al coniuge**, non ai genitori. Due mariti con lo
    stesso nome sono due uomini; due volte lo stesso padre no — un padre
    ha figli da piu' persone per definizione, e i suoi figli si dividono
    fra loro senza che lui si divida. Finche' la propagazione risaliva
    anche ai genitori, separare Domenico Lella dall'omonimo Domenico di
    Vito tagliava in due **Filippo Lella**, che di quel taglio non
    c'entrava niente: e i due mezzi Filippo, marcati come da tenere
    separati, non si rimettevano piu' insieme.
    """
    verso_pezzo: dict[int, Scheda] = {}
    for pezzo in pezzi:
        for menzione in pezzo.menzioni:
            verso_pezzo[menzione.id] = pezzo

    # Chi punta a una menzione dei pezzi, e chi ne e' puntato.
    vicini: dict[int, dict[int, Scheda]] = defaultdict(dict)
    for menzione in esito.corpus.menzioni:
        for riferimento in (menzione.coniuge,):
            if riferimento is None:
                continue
            if riferimento in verso_pezzo:
                vicini[menzione.id][id(verso_pezzo[riferimento])] = verso_pezzo[riferimento]
            if menzione.id in verso_pezzo and riferimento not in verso_pezzo:
                vicini[riferimento][id(verso_pezzo[menzione.id])] = verso_pezzo[menzione.id]

    divise = 0
    da_guardare = {
        esito.di_menzione[m] for m in vicini if m in esito.di_menzione
    }
    for chiave in sorted(da_guardare):
        scheda = esito.schede.get(chiave)
        if scheda is None or scheda.quante < 2:
            continue
        gruppi: dict[int, set] = defaultdict(set)
        indecise = 0
        for menzione in scheda.menzioni:
            verso = vicini.get(menzione.id)
            if not verso:
                indecise += 1
            elif len(verso) == 1:
                gruppi[next(iter(verso))].add(menzione.id)
            else:
                indecise += 1       # legata a tutti e due: non dice niente
        if len(gruppi) < 2:
            continue
        semi = [gruppi[k] for k in sorted(gruppi)]
        nuovi = dividi_in(scheda, chiavi, modello, semi)
        if len(nuovi) < 2:
            continue
        del esito.schede[chiave]
        for nuovo in nuovi:
            esito.schede[nuovo.chiave] = nuovo
            for menzione in nuovo.menzioni:
                esito.di_menzione[menzione.id] = nuovo.chiave
        divise += 1
        esito.decisioni.append(mod.Decisione(
            azione="separazione",
            entita=tuple(
                [min(nuovi[0].ids)] + sorted(set().union(*(n.ids for n in nuovi[1:])))
            ),
            motivo=(
                f"{scheda.etichetta()} era legata a tutti i pezzi di una scheda "
                f"appena divisa, e i suoi atti si dividono allo stesso modo"
            ),
            confidenza=0.8,
            evidenze=("la divisione la dicono gli atti, non il punteggio",),
            quando=_adesso(),
        ))
    return divise


def _dividi_su(
    scheda: Scheda, chiavi, escluse: set, riga_tenuta: int | None = None
) -> list[Scheda]:
    """Stacca da una scheda le menzioni indicate, tutte insieme.

    E' la separazione minima che rispetta la decisione senza disfare il
    lavoro attorno: le menzioni nominate formano una scheda nuova, le
    altre restano dove sono.

    I due pezzi si ricordano di doversi stare lontani. Senza, la
    riconciliazione del giro successivo li rimetterebbe insieme — hanno
    lo stesso nome, e sono stati uniti proprio perche' il punteggio
    diceva di si' — e la decisione varrebbe una volta sola.
    """
    resta = [m for m in scheda.menzioni if m.id not in escluse]
    esce = [m for m in scheda.menzioni if m.id in escluse]
    if not resta or not esce:
        return [scheda]
    prima = Scheda(chiave=min(m.id for m in resta), menzioni=resta)
    prima.ricalcola(chiavi)
    seconda = Scheda(chiave=min(m.id for m in esce), menzioni=esce)
    seconda.ricalcola(chiavi)
    # Il pezzo staccato si ricorda della sola riga che la decisione tiene:
    # le altre del pezzo che resta ci stavano per il calcolo, non per la
    # decisione. Segnarle tutte teneva lontano per sempre il padre del 1830
    # di Nicoletta Troilo da suo marito del 1832, finito per errore nella
    # scheda della riga che restava. Senza ``riga_tenuta`` si segna tutto,
    # come prima.
    tenuta = {riga_tenuta} if riga_tenuta is not None else prima.ids
    prima.vietati = set(scheda.vietati) | seconda.ids
    seconda.vietati = set(scheda.vietati) | tenuta
    # Questa divisione viene da una decisione presa sulla pagina — e'
    # l'unico posto in cui '_dividi_su' e' chiamato — e va marcata come
    # tale: nessun passo successivo puo' tornarci sopra.
    # Ogni pezzo si porta i divieti delle sue righe, non quelli dell'altro.
    prima.eredita_divieti(scheda)
    seconda.eredita_divieti(scheda)
    if riga_tenuta is not None:
        prima.vieta(riga_tenuta, seconda.ids)
        for riga in seconda.ids:
            seconda.vieta(riga, {riga_tenuta})
    else:
        prima._separati_in_blocco |= seconda.ids
        seconda._separati_in_blocco |= prima.ids
    return [prima, seconda]


def ricostruisci(
    corpus, giri: int = GIRI_MASSIMI, deposito=None, modello: Modello | None = None,
    imposizioni: dict | None = None,
) -> Esito:
    """Il ciclo intero: primo giro, riconciliazione, separazione, ancora.

    ``modello`` si passa gia' fatto quando si provano piu' configurazioni
    di seguito sullo stesso corpus: dipende solo dalle frequenze, non
    dalle soglie, e ricalcolarlo a ogni prova e' il grosso del tempo.
    """
    if modello is None:
        logger.info("modello delle frequenze su %d menzioni...", corpus.totale)
        modello = Modello.dal_corpus(corpus, deposito)

    logger.info("primo giro sulle menzioni...")
    esito = primo_giro(corpus, modello)
    # Da qui in avanti il modello sa dove stanno le schede: serve a
    # confrontare due coniugi come persone invece che come stringhe
    # (vedi 'evidenza.prova_dai_coniugi'). La mappa e' la stessa che
    # la riconciliazione modifica in corsa, quindi resta aggiornata da
    # sola a ogni fusione.
    modello.schede = esito.schede
    logger.info("  %d schede", len(esito.schede))
    esito.statistiche["schede dopo il primo giro"] = len(esito.schede)

    sporche: set | None = None
    for giro in range(giri):
        unite, toccate_unione = riconcilia(esito, sporche)
        divise, toccate_divisione = separa(esito)
        logger.info("  giro %d: %d unioni, %d separazioni, %d schede",
                    giro + 1, unite, divise, len(esito.schede))
        esito.statistiche["unioni"] += unite
        esito.statistiche["separazioni"] += divise
        if not unite and not divise:
            break
        # Dal secondo giro in poi si riguardano solo le schede toccate e
        # quelle a cui sono legate: e' cio' che rende praticabile un ciclo
        # di dodici giri su cinquantamila menzioni. Il vicinato ci deve
        # stare, perche' una fusione cambia l'evidenza anche per chi non
        # e' stato toccato — se due madri diventano una, i due mariti
        # guadagnano di colpo una moglie in comune.
        sporche = (toccate_unione | toccate_divisione) & set(esito.schede)
    else:
        logger.warning("la riconciliazione non si e' fermata in %d giri", giri)

    # Chi ha piu' coniugi negli stessi anni. Va dopo la riconciliazione,
    # non dentro: serve che le schede dei coniugi siano gia' quelle
    # definitive, altrimenti si conterebbero come due mariti i due
    # tronconi dello stesso uomo.
    # Prima di dividere chi ha due coniugi insieme, si guarda se i due
    # coniugi non siano la stessa persona letta in due modi.
    contemporanei = unisci_coniugi_contemporanei(esito)
    if contemporanei:
        logger.info("  %d coniugi contemporanei della stessa famiglia rimessi insieme",
                    contemporanei)
        esito.statistiche["coniugi contemporanei uniti"] = contemporanei
    per_coniugi, _toccate = separa_per_coniugi(esito)
    if per_coniugi:
        logger.info("  %d schede divise per coniugi contemporanei", per_coniugi)
        esito.statistiche["separazioni per coniugi"] = per_coniugi
        riconcilia(esito)

    # E il rovescio: due coniugi con lo stesso nome sono un coniuge solo
    # spezzato in due. Va dopo la divisione, perche' e' li' che le schede
    # dei coniugi prendono la forma definitiva.
    doppie = unisci_righe_doppie(esito)
    if doppie:
        logger.info("  %d righe lette due volte rimesse insieme", doppie)
        esito.statistiche["righe lette due volte"] = doppie

    omonimi = unisci_coniugi_omonimi(esito)
    if omonimi:
        logger.info("  %d coniugi omonimi rimessi insieme", omonimi)
        esito.statistiche["coniugi omonimi uniti"] = omonimi

    # Dove le schede non si possono unire, si sposta la riga sola.
    spostate = sposta_al_coniuge_omonimo(esito)
    if spostate:
        logger.info("  %d menzioni spostate al coniuge omonimo", spostate)
        esito.statistiche["menzioni spostate al coniuge omonimo"] = spostate

    # E il grado sopra: due nuclei con gli stessi due genitori. Va dopo
    # i coniugi omonimi, che gli tolgono di mezzo i doppioni piu'
    # facili, e lavora sul grafo aggiornato.
    aggiorna_grafo(esito)
    gemelle = unisci_coppie_gemelle(esito)
    if gemelle:
        logger.info("  %d schede unite da coppie gemelle", gemelle)
        esito.statistiche["schede unite da coppie gemelle"] = gemelle

    # E il grado sotto: un figlio con due madri, o due padri, della stessa
    # famiglia. Va dopo le coppie gemelle, che rimettono insieme i figli.
    genitori = unisci_genitori_dello_stesso_figlio(esito)
    if genitori:
        logger.info("  %d genitori doppi dello stesso figlio rimessi insieme", genitori)
        esito.statistiche["genitori dello stesso figlio uniti"] = genitori

    # Le decisioni gia' prese entrano per ultime, e scavalcano il
    # punteggio: chi ha guardato la carta ne sa piu' del calcolo.
    if imposizioni:
        conteggi = applica_decisioni(esito, imposizioni)
        if conteggi:
            logger.info("  decisioni gia' prese: %s", dict(conteggi))
        # Dopo un'imposizione il grafo e' cambiato, e cambiare il grafo
        # puo' rendere possibile un'unione che prima non lo era.
        riconcilia(esito)
        separa(esito)

    # E ancora i coniugi omonimi, alla fine. Le decisioni imposte e l'ultima
    # riconciliazione rifanno schede, e un coniuge spezzato dopo il primo
    # passaggio restava spezzato: ventiquattro coppie nell'archivio, come le
    # due Giovanna Pelliccia mogli di Michele Moretta.
    # Lo stesso vale per i coniugi contemporanei della stessa famiglia:
    # Grazia e Giacinta Marianacci, mogli di Giuseppe Cicchillitti con figli
    # nel 1836 e nel 1843, erano due schede solo dopo il primo passaggio.
    contemporanei = unisci_coniugi_contemporanei(esito)
    if contemporanei:
        logger.info("  %d coniugi contemporanei rimessi insieme alla fine", contemporanei)
        esito.statistiche["coniugi contemporanei uniti alla fine"] = contemporanei
    ancora = unisci_coniugi_omonimi(esito)
    if ancora:
        logger.info("  %d coniugi omonimi rimessi insieme alla fine", ancora)
        esito.statistiche["coniugi omonimi uniti alla fine"] = ancora

    aggiorna_grafo(esito)
    esito.statistiche["schede finali"] = len(esito.schede)
    return esito
