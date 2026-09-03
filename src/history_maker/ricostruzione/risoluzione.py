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
        unita.vietati = prima.vietati | seconda.vietati
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

    # Le separazioni si raccolgono per scheda prima di applicarle: una
    # decisione che stacca tre menzioni dalla stessa persona e' **una**
    # separazione in tre pezzi, non tre separazioni da una menzione
    # ciascuna. Applicandole a una a una si otterrebbero tre schede
    # solitarie invece di un gruppo.
    da_staccare: dict = defaultdict(set)
    for prima_chiave, seconda_chiave in imposizioni.get("separare", ()):
        chiave = esito.di_menzione.get(prima_chiave)
        if chiave is None or esito.di_menzione.get(seconda_chiave) != chiave:
            continue        # gia' separate: non c'e' niente da fare
        da_staccare[chiave].add(seconda_chiave)

    for chiave in sorted(da_staccare):
        scheda = esito.schede.get(chiave)
        if scheda is None:
            continue
        pezzi = _dividi_su(scheda, chiavi, da_staccare[chiave])
        if len(pezzi) < 2:
            continue
        del esito.schede[chiave]
        for pezzo in pezzi:
            esito.schede[pezzo.chiave] = pezzo
            for menzione in pezzo.menzioni:
                esito.di_menzione[menzione.id] = pezzo.chiave
        conteggi["separazioni imposte"] += 1
        conteggi["parenti divisi di conseguenza"] += propaga_separazione(
            esito, pezzi, chiavi, esito.modello
        )

    for prima_chiave, seconda_chiave in imposizioni.get("unire", ()):
        una = esito.schede.get(esito.di_menzione.get(prima_chiave))
        altra = esito.schede.get(esito.di_menzione.get(seconda_chiave))
        if una is None or altra is None or una is altra:
            continue
        impossibile = evidenza.veti(una, altra)
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
    return gruppi


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
    """
    verso_pezzo: dict[int, Scheda] = {}
    for pezzo in pezzi:
        for menzione in pezzo.menzioni:
            verso_pezzo[menzione.id] = pezzo

    # Chi punta a una menzione dei pezzi, e chi ne e' puntato.
    vicini: dict[int, dict[int, Scheda]] = defaultdict(dict)
    for menzione in esito.corpus.menzioni:
        for riferimento in (menzione.padre, menzione.madre, menzione.coniuge):
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


def _dividi_su(scheda: Scheda, chiavi, escluse: set) -> list[Scheda]:
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
    prima.vietati = set(scheda.vietati) | seconda.ids
    seconda.vietati = set(scheda.vietati) | prima.ids
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

    aggiorna_grafo(esito)
    esito.statistiche["schede finali"] = len(esito.schede)
    return esito
