"""Claude sui casi che il calcolo non decide, e solo su quelli.

Un modello di ragionamento e' la risorsa piu' cara della catena, e usarlo
su tutto sarebbe insieme impossibile e inutile: cinquantamila menzioni non
entrano in nessuna finestra, e novantanove casi su cento il calcolo li
risolve da solo meglio e gratis.

Il criterio e' quindi uno solo: **la fascia grigia**. I casi in cui
l'evidenza sta fra la soglia della segnalazione e quella dell'unione sono
esattamente quelli in cui il calcolo ha fatto tutto quello che poteva e si
e' fermato a meta'. Sono anche quelli in cui una risposta cambia qualcosa:
sopra e sotto la fascia la risposta e' gia' data.

L'ordine e' quello della coda delle anomalie — dubbio per impatto — cosi'
che le prime domande siano quelle che spostano piu' albero.

Cosa succede a una risposta
---------------------------

Non diventa un fatto. Diventa una **decisione con un autore**: nel
registro resta scritto che a unire quelle due schede e' stato un modello,
quale, con quale prompt e con quanta confidenza. Se domani si scoprira'
che quel modello sbagliava un certo tipo di casi, si potra' vedere quali e
disfarli — cosa che con una fusione anonima sarebbe impossibile.

Una risposta sotto la soglia di fiducia non fa niente: resta
nell'archivio come opinione, e il caso resta aperto. Un modello che dice
'non deciso' e' un modello che sta funzionando.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from history_maker.backend import LimiteUsoRaggiunto, Richiesta
from history_maker.ricostruzione import cache, contesto, evidenza, modello as mod
from history_maker.ricostruzione.scheda import Scheda

logger = logging.getLogger(__name__)

# Quanto deve essere sicuro il modello perche' la sua risposta cambi
# l'albero. Sotto, la risposta resta registrata ma non muove niente: e'
# un'opinione in piu' nel fascicolo, non una conclusione.
FIDUCIA_MINIMA = 0.8

# Quanti casi per chiamata. Piu' di tre e il fascicolo diventa lungo e le
# risposte si accorciano; uno solo spreca il prompt di sistema, che con
# Claude Code e' la voce di costo piu' grossa.
CASI_PER_CHIAMATA = 3

# Se le divisioni chieste dal modello si applicano davvero.
#
# **Spente di default, e il perche' e' misurato.** Sui casi di 'arco di
# vita impossibile' il modello risponde bene: «108 anni non sono una vita
# sola», e indica quali menzioni vanno con quali. Applicando quelle
# ventiquattro divisioni, le vite impossibili scendono da 73 a 53 — e le
# frammentazioni salgono da 180 a 217.
#
# Il conto vero l'ha fatto un controllo apposta: delle 59 coppie gemelle
# che ne uscivano, **49 avevano i figli intrecciati**. Non erano omonimi
# separati bene: erano famiglie spezzate. Dividere un uomo lascia sua
# moglie a meta' fra i pezzi, e propagarle la divisione peggiora le cose
# invece di rimediarle.
#
# La capacita' resta — il fascicolo numera le menzioni, la risposta si sa
# applicare, i pezzi si ricordano di stare lontani — e si accende con
# 'arbitra --dividi' da chi vuole vedere l'effetto sul proprio archivio.
# Quello che non si fa e' applicarla di default sulla base di una misura
# che dice il contrario.
DIVIDE_DI_DEFAULT = False

# I tipi di caso che ha senso mandare. Un'anomalia di data non si decide
# ragionando: si decide guardando la pagina, ed e' un altro modulo.
DA_ARBITRARE = (
    "DUPLICATE_PERSON", "POSSIBLE_HOMONYM", "MARITAL_ANOMALY",
    # Le schede che coprono un arco di vita che nessuno copre: sono
    # fusioni sbagliate che non producono niente di localmente
    # impossibile, e per dividerle serve qualcuno che le legga.
    "IDENTITY_ANOMALY",
)


def casi(esito, quanti: int, tipo: str | None = None) -> list[mod.Anomalia]:
    """I casi da sottoporre, in ordine di quanto conviene chiederli.

    ``tipo`` restringe a una categoria sola. Serve a misurare: le
    anomalie di doppio coniuge hanno impatto altissimo e occupano la
    testa della coda, e senza poter chiedere **solo** i duplicati non si
    riesce a sapere quanto le risposte di un modello cambino la
    frammentazione.
    """
    from history_maker.ricostruzione import anomalie as coda_anomalie

    ammessi = (tipo,) if tipo else DA_ARBITRARE
    return [
        anomalia for anomalia in coda_anomalie.coda(esito)
        if anomalia.tipo in ammessi
    ][:quanti]


def arbitra(
    config, esito, quanti: int = 30, deposito=None, applica: bool = True,
    tipo: str | None = None, dividi: bool = DIVIDE_DI_DEFAULT,
) -> dict:
    """Sottopone i casi ambigui e, se la risposta e' netta, la applica."""
    from history_maker import backend as motori

    da_fare = casi(esito, quanti, tipo)
    if not da_fare:
        return {"casi": 0}

    motore = motori.crea(config)
    motore.verifica()
    modello_scelto = config.trascrizione.modello
    conteggi = {"casi": len(da_fare), "risposte": 0, "applicate": 0,
                "riusate": 0, "quota_esaurita": False, "verifiche_chieste": 0,
                "divisioni_proposte": 0}

    for gruppo in _a_gruppi(da_fare, CASI_PER_CHIAMATA):
        fascicoli = [contesto.fascicolo(anomalia, esito) for anomalia in gruppo]
        # La chiave comprende il fascicolo intero: se le schede sono
        # cambiate, la domanda non e' piu' la stessa e la risposta di
        # ieri non vale. Se sono uguali, la quota si risparmia.
        chiave = cache.impronta(fascicoli, modello_scelto, contesto.VERSIONE_PROMPT)
        chieste = {"fatto": False}

        def chiedi_ora():
            chieste["fatto"] = True
            return _chiedi(motore, fascicoli, config, conteggi)

        if deposito is not None:
            risposte = deposito.ottieni("arbitro", chiave, chiedi_ora)
            if not chieste["fatto"]:
                conteggi["riusate"] += len(gruppo)
        else:
            risposte = chiedi_ora()
        if risposte is None:
            break

        for anomalia, risposta in zip(gruppo, risposte):
            conteggi["risposte"] += 1
            if _applica(esito, anomalia, risposta, modello_scelto, dividi, conteggi) and applica:
                conteggi["applicate"] += 1
            if (risposta.get("verifica_sull_immagine") or {}).get("serve"):
                conteggi["verifiche_chieste"] += 1
    return conteggi


def _chiedi(motore, fascicoli: list[dict], config, conteggi) -> list[dict] | None:
    richiesta = Richiesta(
        sistema=contesto.SISTEMA,
        istruzione=contesto.istruzione(fascicoli),
        modello=config.trascrizione.modello,
        timeout_s=config.trascrizione.timeout_s,
    )
    try:
        risposta = motore.esegui(richiesta)
    except LimiteUsoRaggiunto as limite:
        logger.info("quota esaurita: %s", limite)
        conteggi["quota_esaurita"] = True
        return None
    if not risposta.ok:
        logger.warning("l'arbitro non ha risposto: %s", risposta.errore)
        return [{} for _ in fascicoli]
    return _interpreta(risposta.testo, len(fascicoli))


def _interpreta(testo: str, quanti: int) -> list[dict]:
    from history_maker.backend import estrai_json

    try:
        dati = estrai_json(testo)      # rende gia' la struttura, non il testo
    except (ValueError, TypeError):
        return [{} for _ in range(quanti)]
    if isinstance(dati, dict):
        dati = [dati]
    if not isinstance(dati, list):
        return [{} for _ in range(quanti)]
    # Una risposta piu' corta della domanda non si allinea a caso: le
    # mancanti restano vuote, cioe' 'non deciso'.
    dati = [d if isinstance(d, dict) else {} for d in dati]
    return (dati + [{} for _ in range(quanti)])[:quanti]


def _applica(
    esito, anomalia: mod.Anomalia, risposta: dict, modello_scelto: str,
    dividi_pure: bool = DIVIDE_DI_DEFAULT, conteggi: dict | None = None,
) -> bool:
    """Traduce una risposta in una decisione, e la registra sempre.

    Registrare anche le risposte che non cambiano niente non e' zelo: e'
    il solo modo di poter dire, fra un mese, quante volte quel modello ha
    detto 'non deciso' e su che tipo di casi.
    """
    decisione = (risposta.get("decisione") or "non deciso").strip().casefold()
    confidenza = float(risposta.get("confidenza") or 0.0)
    prove = tuple(risposta.get("prove_usate") or ())
    ragionamento = (risposta.get("ragionamento") or "").strip()

    esito.decisioni.append(mod.Decisione(
        azione=_azione(esito, anomalia, decisione, confidenza),
        entita=anomalia.individui,
        motivo=ragionamento or anomalia.descrizione,
        confidenza=confidenza,
        evidenze=prove,
        contraddizioni=(
            (risposta.get("alternativa"),) if risposta.get("alternativa") else ()
        ),
        atti=anomalia.atti,
        decisore="claude",
        modello=modello_scelto,
        versione_prompt=contesto.VERSIONE_PROMPT,
        quando=_adesso(),
    ))

    if confidenza < FIDUCIA_MINIMA:
        return False
    gruppi = risposta.get("gruppi")
    if gruppi and decisione.startswith("persone diverse"):
        if conteggi is not None:
            conteggi["divisioni_proposte"] = conteggi.get("divisioni_proposte", 0) + 1
        if not dividi_pure:
            return False
        return dividi(esito, anomalia, gruppi, ragionamento, confidenza)
    if not decisione.startswith("stessa"):
        return False
    return unisci(esito, anomalia.individui[:2], ragionamento, confidenza)


def dividi(esito, anomalia: mod.Anomalia, gruppi, motivo: str, confidenza: float) -> bool:
    """Divide una scheda nei gruppi di menzioni che il modello ha indicato.

    Il primo gruppo resta dov'e', gli altri se ne vanno. E' l'altra meta'
    del lavoro dell'arbitro: fino a qui sapeva dire 'sono la stessa
    persona' e non 'questa scheda ne contiene due', e i casi in cui una
    scheda copre un arco di vita che nessuno copre restavano li'.

    I pezzi si ricordano di doversi stare lontani, altrimenti la
    riconciliazione del giro dopo li rimetterebbe insieme.
    """
    if not anomalia.individui:
        return False
    scheda = esito.schede.get(anomalia.individui[0])
    if scheda is None or scheda.quante < 2:
        return False

    validi = [
        {m for m in (_numero(voce) for voce in gruppo) if m in scheda.ids}
        for gruppo in gruppi if isinstance(gruppo, (list, tuple))
    ]
    validi = [g for g in validi if g]
    if len(validi) < 2:
        return False
    # I semi non coprono tutte le menzioni — il fascicolo ne mostra due
    # dozzine e una scheda sbagliata ne ha spesso cento — e le altre le
    # assegna il calcolo, che e' la parte che sa contare.
    validi[1] = validi[1] - validi[0]
    if not validi[1]:
        return False

    from history_maker.ricostruzione.risoluzione import (
        dividi_in, propaga_separazione,
    )

    pezzi = dividi_in(scheda, esito.corpus.chiavi(), esito.modello, validi[:2])
    if len(pezzi) < 2:
        return False
    if _non_sono_due_vite(pezzi):
        logger.info("divisione rifiutata su %s: i due pezzi si sovrappongono",
                    scheda.etichetta())
        return False
    escluse = pezzi[1].ids
    del esito.schede[scheda.chiave]
    for pezzo in pezzi:
        esito.schede[pezzo.chiave] = pezzo
        for menzione in pezzo.menzioni:
            esito.di_menzione[menzione.id] = pezzo.chiave
    # Dividere un uomo lascia sua moglie legata a tutti e due i pezzi:
    # se i suoi atti si dividono allo stesso modo, si divide anche lei.
    propaga_separazione(esito, pezzi, esito.corpus.chiavi(), esito.modello)

    # Una decisione per menzione staccata: e' la forma che 'registro'
    # sa rileggere, e che la ricostruzione successiva riapplica.
    esito.decisioni.append(mod.Decisione(
        azione="separazione",
        entita=(min(pezzi[0].ids), *sorted(escluse)),
        motivo=motivo or f"{scheda.etichetta()} contiene due persone",
        confidenza=confidenza,
        evidenze=tuple(f"menzione {m} a parte" for m in sorted(escluse))[:8],
        atti=anomalia.atti,
        decisore="claude",
        versione_prompt=contesto.VERSIONE_PROMPT,
        quando=_adesso(),
    ))
    return True


def _azione(esito, anomalia: mod.Anomalia, decisione: str, confidenza: float) -> str:
    """Che cosa fa, davvero, questa risposta.

    Non si traduce 'persone diverse' in una separazione a occhi chiusi, e
    la ragione l'ha mostrata la prima esecuzione dal vivo. Su un caso di
    doppio coniuge le entita' del fascicolo non sono due schede che
    potrebbero coincidere: sono **una persona e i suoi due coniugi**.
    'Sono persone diverse' li' vuol dire 'le due mogli sono due donne', e
    prenderlo per un ordine di separare avrebbe staccato il marito dalla
    prima moglie — cioe' l'opposto di quello che il modello ha detto.

    Quindi: una separazione si registra solo quando le due entita' sono
    davvero **nella stessa scheda** e quindi c'e' qualcosa da separare.
    Negli altri casi la risposta conferma lo stato attuale, ed e'
    comunque un risultato: quel dubbio non va piu' riaperto.
    """
    if confidenza < FIDUCIA_MINIMA:
        return "conferma"
    if decisione.startswith("stessa"):
        return "unione"
    if not decisione.startswith("persone diverse"):
        return "conferma"
    chiavi = anomalia.individui[:2]
    if len(chiavi) < 2:
        return "conferma"
    insieme = (
        chiavi[0] in esito.schede and chiavi[1] in esito.schede
        and esito.schede[chiavi[0]] is esito.schede[chiavi[1]]
    )
    return "separazione" if insieme else "conferma"


def unisci(esito, chiavi, motivo: str, confidenza: float) -> bool:
    """Fonde due schede se niente di fisicamente impossibile lo vieta.

    Il veto resta anche qui, e non e' sfiducia verso il modello: una
    risposta puo' essere ragionevole e sbagliata, e un archivio che
    accetta di scrivere che una donna ha partorito dopo il proprio
    funerale non e' piu' un archivio.
    """
    prima = esito.schede.get(chiavi[0])
    seconda = esito.schede.get(chiavi[1]) if len(chiavi) > 1 else None
    if prima is None or seconda is None or prima is seconda:
        return False
    if evidenza.veti(prima, seconda) is not None:
        logger.info("fusione rifiutata: %s", evidenza.veti(prima, seconda))
        return False

    unita = Scheda.unione(prima, seconda, esito.corpus.chiavi())
    if evidenza.incoerenze(unita):
        return False
    esito.schede[unita.chiave] = unita
    perdente = seconda.chiave if unita.chiave == prima.chiave else prima.chiave
    esito.schede.pop(perdente, None)
    for menzione in unita.menzioni:
        esito.di_menzione[menzione.id] = unita.chiave
    return True


# Di quanti anni possono sovrapporsi i due pezzi di una scheda divisa
# perche' la divisione sia ancora "due vite" e non "una vita tagliata a
# meta'". Qualche anno ci sta — le eta' sono dichiarate a voce e i
# passaggi di consegne esistono — ma non un ventennio.
SOVRAPPOSIZIONE_MASSIMA = 8


def _non_sono_due_vite(pezzi: list) -> bool:
    """Se la divisione taglia una vita invece di separarne due.

    E' la guardia che manca al giudizio del modello, ed e' costata
    caro scoprirla. Sui casi di 'arco di vita impossibile' il modello
    risponde bene — «108 anni non sono una vita sola», e ha ragione — ma
    le menzioni che assegna ai due gruppi si sovrappongono nel tempo, e
    il risultato non sono due uomini in sequenza: e' lo stesso uomo
    tagliato in due, con la moglie che finisce a meta' fra i pezzi.

    Misurato applicando le divisioni senza questa guardia: le vite
    impossibili scendevano da 73 a 53, e delle 59 coppie gemelle che ne
    uscivano **49 avevano i figli intrecciati** — cioe' erano famiglie
    spezzate, non omonimi separati.

    L'anomalia dice 'un arco che una vita sola non copre': la divisione
    che la risolve deve quindi separare due **periodi**. Se i due pezzi
    convivono per vent'anni, non e' quella.
    """
    intervalli = []
    for pezzo in pezzi:
        anni = [m.anno for m in pezzo.menzioni if m.anno]
        if not anni:
            return True
        intervalli.append((min(anni), max(anni)))
    intervalli.sort()
    for (_, fine), (inizio, _) in zip(intervalli, intervalli[1:]):
        if fine - inizio > SOVRAPPOSIZIONE_MASSIMA:
            return True
    return False


def _numero(voce) -> int | None:
    """L'identificatore di menzione dentro quello che il modello risponde.

    Il fascicolo scrive le righe come ``[2528]`` e il modello, con ogni
    diritto, risponde ``"[2528]"``. Pretendere un intero nudo faceva
    cadere l'intera chiamata con un ValueError, e buttava via anche le
    risposte buone che le stavano accanto.
    """
    if isinstance(voce, int):
        return voce
    cifre = "".join(c for c in str(voce) if c.isdigit())
    return int(cifre) if cifre else None


def _a_gruppi(elementi: list, quanti: int):
    for inizio in range(0, len(elementi), quanti):
        yield elementi[inizio: inizio + quanti]


def _adesso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
