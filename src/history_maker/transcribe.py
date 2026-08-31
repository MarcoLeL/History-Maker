"""Fase 3: trascrizione delle immagini.

Chi legge le pagine e' un backend intercambiabile — Gemini sul piano
gratuito, o Claude Code sull'abbonamento — e questo modulo non lo sa:
prepara le immagini, compone l'istruzione, allinea la risposta alle
pagine e salva. Vedi :mod:`history_maker.backend`.

Quattro conseguenze governano il modulo, e valgono per tutti i motori:

**Le pagine vanno a gruppi.** Con Claude Code il sovraccarico (~50.000
token di prompt di sistema e strumenti) e' per invocazione, non per
immagine: raggruppare e' l'unico modo di non sprecare la quota. Con
Gemini quel sovraccarico non c'e', ma il piano gratuito conta le
*richieste al giorno*, e allora raggruppare e' l'unico modo di
trascrivere piu' pagine. La leva e' la stessa per ragioni opposte. Il
tetto di quanto si puo' raggruppare non e' pero' la quota giornaliera:
e' il limite di **token al minuto**, che con le facciate divise si
raggiunge prima di quanto sembri.

**La quota si esaurisce.** Quando succede il lavoro non fallisce: si
ferma pulito, oppure aspetta e riprende se glielo si chiede. Ogni pagina
finita e' salvata subito, quindi rilanciare riprende sempre da dove si
era arrivati.

**La risposta va validata comunque.** Gemini accetta un
``responseSchema`` e restituisce JSON conforme; Claude Code no. In
entrambi i casi ogni pagina passa da ``schema.valida_pagina``: un
vincolo di forma non e' un vincolo di senso, e un gruppo che non si
lascia interpretare viene ritentato una pagina per volta.

**La risoluzione e' il tetto della qualita'.** Una scansione e' una
doppia pagina orizzontale con cornice nera: mandarla intera fa arrivare
ogni facciata al modello con 784 pixel di larghezza. Il ritaglio e la
divisione in due facciate, in :func:`prepara_immagini`, vengono prima
della riduzione e valgono piu' di qualunque scelta di modello.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from history_maker import backend as motori
from history_maker import glossario, ritaglio, schema
from history_maker.backend import Backend, LimiteUsoRaggiunto, Richiesta
from history_maker.catalogo import Catalogo, Registro, selezione
from history_maker.config import Config
from history_maker.prompt import (
    COME_LEGGERE,
    ISTRUZIONE_GRUPPO,
    NOTA_FACCIATE,
    descrivi_forme_note,
    descrivi_pagina,
    descrivi_schema,
    sistema,
)
from history_maker.schema import PaginaNonValida, valida_pagina

logger = logging.getLogger(__name__)

ESTENSIONI_IMMAGINE = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Quanto aspettare quando la quota e' esaurita e si e' scelto di attendere.
# I limiti dell'abbonamento si rinnovano su finestre di alcune ore, quindi
# ricontrollare ogni quarto d'ora e' abbastanza spesso da non perdere
# tempo e abbastanza raro da non tempestare la CLI.
ATTESA_QUOTA_S = 900

# Sotto questa soglia un'attesa suggerita dal servizio e' un limite di
# ritmo, non una quota finita: si aspetta e si prosegue senza chiedere
# permesso. Sopra, e' il muro della quota e la decisione torna a chi ha
# lanciato il comando.
ATTESA_BREVE_MAX_S = 300

# Quanti 429 di fila su uno stesso gruppo prima di dichiararlo un muro,
# qualunque cosa dica il messaggio. E' la rete di sicurezza che non
# dipende dal saper leggere l'errore: senza, il lavoro ha girato otto
# minuti contro una quota giornaliera esaurita credendo di aspettare
# qualche secondo di congestione.
MAX_RITENTATIVI_DI_RITMO = 4


def _e_solo_ritmo(exc: LimiteUsoRaggiunto, tentativi: int) -> bool:
    """Se vale la pena aspettare e riprovare, o e' il muro del giorno.

    Tre segnali, in ordine di affidabilita': cosa dice l'API della quota
    violata, quanto suggerisce di aspettare, e quante volte ha gia' detto
    di no. L'ultimo e' l'unico che non si puo' sbagliare a leggere.
    """
    if getattr(exc, "giornaliera", False):
        return False
    if tentativi > MAX_RITENTATIVI_DI_RITMO:
        return False
    return exc.attesa_s is not None and exc.attesa_s <= ATTESA_BREVE_MAX_S


@dataclass
class Pagina:
    """Una pagina da trascrivere, con il contesto del registro che la contiene."""

    percorso: Path
    registro: Registro

    @property
    def id_richiesta(self) -> str:
        return f"{self.registro.slug}--{self.percorso.stem}"

    @property
    def destinazione_relativa(self) -> Path:
        return Path(self.registro.slug) / f"{self.percorso.stem}.json"


@dataclass
class Esito:
    trascritte: int = 0
    fallite: int = 0
    chiamate: int = 0
    token_contesto: int = 0
    token_output: int = 0
    quota_esaurita: bool = False


def pagine_da_trascrivere(
    config: Config,
    solo_mancanti: bool = True,
    dal: int | None = None,
    al: int | None = None,
) -> list[Pagina]:
    """Le pagine scaricate che rientrano nella raccolta, in ordine di anno.

    **L'ordine conta perche' il lavoro si interrompe.** La quota
    giornaliera finisce a meta' corsa, ed e' normale che finisca: quello
    che cambia e' cosa resta in mano nel frattempo. Il catalogo elenca i
    registri come il portale li ha restituiti — prima tutti i Nati di
    cinquant'anni, poi tutti i Morti degli stessi — e seguirlo lascia un
    mosaico, con buchi sparsi in ogni decennio. Ordinare per anno lascia
    invece un blocco compatto: con 1809-1860 completi si ricostruiscono
    famiglie intere, con meta' 1840 e meta' 1850 non si chiude nulla.

    E' lo stesso ordine che usa gia' la fase di scaricamento.
    """
    catalogo = Catalogo.carica(config.catalogo)
    registri = sorted(
        selezione(catalogo, config), key=lambda r: (r.anno or 0, r.tipologia or "")
    )
    pagine: list[Pagina] = []
    for registro in registri:
        # Intersezione, non confronto secco: un registro 1813-1814
        # risponde a --anno 1814 anche se il suo anno di apertura e' 1813.
        if dal is not None and (registro.anno_fine or registro.anno or 0) < dal:
            continue
        if al is not None and (registro.anno or 0) > al:
            continue
        cartella = config.immagini / registro.slug
        if not cartella.is_dir():
            continue
        for immagine in sorted(cartella.iterdir()):
            if immagine.suffix.lower() not in ESTENSIONI_IMMAGINE:
                continue
            pagina = Pagina(percorso=immagine, registro=registro)
            if solo_mancanti and (config.trascrizioni / pagina.destinazione_relativa).exists():
                continue
            pagine.append(pagina)
    return pagine


def prepara_immagine(pagina: Pagina, config: Config) -> Path:
    """La prima delle copie ridotte di una pagina.

    Comoda quando si sa che ce n'e' una sola — con ``dividi_facciate``
    spento — e per i test. Il lavoro vero usa :func:`prepara_immagini`,
    perche' una scansione divisa in due facciate sono due immagini e
    prenderne una sola vuol dire perdere gli atti dell'altra.
    """
    return prepara_immagini(pagina, config)[0]


# Quanto le due meta' si sovrappongono, in frazione della larghezza. Una
# parola a cavallo della piega centrale, tagliata in due, non la legge
# nessuno: un dito di sovrapposizione la lascia intera almeno da un lato.
SOVRAPPOSIZIONE_FACCIATE = 0.03


def prepara_immagini(pagina: Pagina, config: Config) -> list[Path]:
    """Le copie ridotte da mandare al modello: una, o le due facciate.

    **La risoluzione e' il tetto della qualita', e qui si decide.** Una
    scansione di questi registri e' una doppia pagina in orizzontale,
    circa 3700x2300: ridotta intera a 1568 di lato lungo, ogni facciata
    arriva al modello con **784 pixel di larghezza**. E' la causa prima
    delle letture sbagliate — ``Femminilli`` letto ``Tommolilli``, ``Rua
    di Nuorro`` letta ``Via di Ricovro`` — tutte perfettamente leggibili
    sull'originale.

    Ritagliare non basta: toglie la cornice nera, ma la scansione resta
    orizzontale e il lato lungo continua a decidere il fattore di
    riduzione, quindi la facciata resta a 784 px. E ritagliare *una sola*
    meta' non si puo': su questi registri una doppia pagina porta spesso
    un atto per lato, e :func:`ritaglio.lato_scritto` restituisce ``None``
    apposta, perche' buttare via un atto e' un errore che non si vede.

    Dividere le due facciate risolve entrambe le cose insieme: ogni meta'
    diventa un'immagine verticale, il suo lato lungo e' l'altezza, e alla
    stessa riduzione la larghezza utile passa da 784 a circa 1100 pixel.
    Nessun atto va perso perche' vengono mandate tutte e due. Il prezzo e'
    il doppio delle immagini per pagina — che sul piano gratuito si paga
    in pagine per chiamata, non in denaro.
    """
    trascrizione = config.trascrizione
    cartella = config.ridotte / pagina.registro.slug
    radice = pagina.percorso.stem
    nomi = (
        [f"{radice}-sinistra.jpg", f"{radice}-destra.jpg"]
        if trascrizione.dividi_facciate
        else [f"{radice}.jpg"]
    )
    destinazioni = [cartella / nome for nome in nomi]
    if all(d.exists() for d in destinazioni):
        return destinazioni
    cartella.mkdir(parents=True, exist_ok=True)

    area = ritaglio.area_da_leggere(pagina.percorso) if trascrizione.ritaglia else None
    with Image.open(pagina.percorso) as immagine:
        intera = immagine.convert("RGB")
        if area:
            intera = intera.crop(area)
        for destinazione, ritagliata in zip(destinazioni, _facciate(intera, trascrizione)):
            _riduci_e_salva(
                ritagliata, trascrizione.lato_lungo_px, trascrizione.qualita_jpeg, destinazione
            )
    return destinazioni


def _facciate(immagine: Image.Image, trascrizione) -> list[Image.Image]:
    """L'immagine intera, o le sue due meta' con un po' di sovrapposizione."""
    if not trascrizione.dividi_facciate:
        return [immagine]
    larghezza, altezza = immagine.size
    meta = larghezza // 2
    margine = round(larghezza * SOVRAPPOSIZIONE_FACCIATE)
    return [
        immagine.crop((0, 0, min(larghezza, meta + margine), altezza)),
        immagine.crop((max(0, meta - margine), 0, larghezza, altezza)),
    ]


def _riduci_e_salva(immagine: Image.Image, lato: int, qualita: int, destinazione: Path) -> None:
    if max(immagine.size) > lato:
        fattore = lato / max(immagine.size)
        immagine = immagine.resize(
            (round(immagine.width * fattore), round(immagine.height * fattore)), Image.LANCZOS
        )
    immagine.save(destinazione, "JPEG", quality=qualita, optimize=True)


@lru_cache(maxsize=4)
def _forme_note_da(percorso: Path) -> str:
    """Il blocco di forme attestate, letto una volta sola.

    La trascrizione di un secolo sono migliaia di invocazioni: rileggere
    il glossario da disco a ogni gruppo di quattro pagine sarebbe uno
    spreco silenzioso.
    """
    toponimi, cognomi = glossario.Glossario.carica(percorso).forme_note()
    return descrivi_forme_note(toponimi, cognomi)


def _forme_note(config: Config) -> str:
    return _forme_note_da(config.glossario)


def costruisci_prompt(
    pagine: list[Pagina],
    immagini: list[list[Path]],
    forme_note: str = "",
    motore: Backend | None = None,
    testo_integrale: bool = True,
) -> str:
    """Istruzione per un gruppo di pagine.

    ``immagini`` porta, per ogni pagina, le immagini che la ritraggono:
    una sola, o le due meta' del foglio aperto.

    Il motore decide come vanno nominate — per percorso su disco a chi le
    apre da solo, per posizione a chi le riceve allegate. Senza
    ``motore`` vale la forma storica, quella di Claude Code.
    """
    modo = motore.modo_immagini if motore else "disco"
    riferimento = (
        motore.riferimento_immagine if motore else (lambda percorso, indice: str(percorso))
    )

    voci: list[str] = []
    posizione = 0
    for pagina, gruppo in zip(pagine, immagini):
        riferimenti = []
        for percorso in gruppo:
            riferimenti.append(riferimento(percorso, posizione))
            posizione += 1
        voci.append(
            descrivi_pagina(
                riferimenti,
                pagina.registro.contesto,
                pagina.registro.anno,
                pagina.registro.tipologia,
                nome=pagina.percorso.name,
            )
        )

    divise = any(len(gruppo) > 1 for gruppo in immagini)
    return ISTRUZIONE_GRUPPO.format(
        quante=len(pagine),
        come_leggere=COME_LEGGERE[modo],
        elenco="\n".join(voci),
        nota_facciate=NOTA_FACCIATE if divise else "",
        forme_note=forme_note,
        schema=descrivi_schema(testo_integrale),
    )


def _salva(config: Config, pagina: Pagina, contenuto: dict) -> Path:
    destinazione = config.trascrizioni / pagina.destinazione_relativa
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    contenuto = dict(contenuto)
    contenuto.pop("file", None)  # serviva solo ad allineare la risposta alle pagine
    contenuto["_origine"] = {
        "immagine": str(pagina.percorso.relative_to(config.immagini)),
        "registro": pagina.registro.slug,
        "ark_url": pagina.registro.ark_url,
        "anno": pagina.registro.anno,
        "tipologia": pagina.registro.tipologia,
        "contesto": pagina.registro.contesto,
    }
    destinazione.write_text(
        json.dumps(contenuto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destinazione


def _nomi_accettati(pagina: Pagina, gruppo: list[Path]) -> list[str]:
    """Come il modello puo' legittimamente aver chiamato questa pagina.

    In ordine di specificita': prima i nomi **qualificati dal registro**,
    che sono gli unici davvero univoci, poi i nomi nudi, poi i gambi senza
    estensione.

    L'ordine e' la parte che conta. Le pagine si chiamano
    ``0001-pag-1.jpg`` dentro ogni registro, e una chiamata che attraversa
    piu' registri vede lo stesso nome ripetuto: cercare prima il nome nudo
    farebbe prendere alla prima pagina l'oggetto di un'altra, e a quel
    punto ogni trascrizione finisce nel file sbagliato — un guasto molto
    peggiore di una pagina persa.
    """
    slug = pagina.registro.slug
    nudi = [pagina.percorso.name, *(p.name for p in gruppo)]
    qualificati = [f"{slug}/{n}" for n in nudi]
    return [*qualificati, *nudi, *(Path(c).stem for c in nudi)]


def _allinea(risposta, pagine: list[Pagina], immagini: list[list[Path]]) -> list[dict | None]:
    """Associa gli oggetti della risposta alle pagine richieste.

    Due passate, in quest'ordine perche' i criteri non sono equivalenti:

    1. **Per nome**, quando il nome identifica una pagina sola. E' il
       criterio robusto: regge anche se il modello cambia l'ordine.
    2. **Per posizione**, per tutto il resto. I modelli rispondono
       nell'ordine in cui hanno ricevuto le immagini, e quando i nomi non
       distinguono e' l'unica informazione rimasta.

    Il caso che ha imposto la seconda passata: le pagine si chiamano
    ``0001-pag-1.jpg`` dentro OGNI registro. Un gruppo da dodici pagine
    attraversa dodici registri ``Diversi`` da una-due pagine, lo stesso
    nome compare cinque volte, e senza ripiego posizionale quattro pagine
    su cinque risultavano fallite pur avendo la loro risposta li' dentro.
    """
    if isinstance(risposta, dict):
        risposta = [risposta]
    if not isinstance(risposta, list):
        return [None] * len(pagine)

    voci = [v for v in risposta if isinstance(v, dict)]
    per_nome: dict[str, dict] = {}
    for voce in voci:
        nome = voce.get("file")
        if isinstance(nome, str) and nome.strip():
            per_nome.setdefault(nome.strip(), voce)

    # I nomi nudi che in QUESTA chiamata indicano piu' di una pagina non
    # possono servire a distinguerle: su quelli si salta alla posizione.
    # Meglio una pagina ritentata che una trascrizione nel file di
    # un'altra — un guasto che non si vedrebbe mai.
    conteggio: dict[str, int] = {}
    for pagina in pagine:
        conteggio[pagina.percorso.name] = conteggio.get(pagina.percorso.name, 0) + 1
    ambigui = {n for n, quanti in conteggio.items() if quanti > 1}

    allineate: list[dict | None] = [None] * len(pagine)
    for indice, (pagina, gruppo) in enumerate(zip(pagine, immagini)):
        for nome in _nomi_accettati(pagina, gruppo):
            if nome in ambigui or Path(nome).name in ambigui:
                continue
            # Confronto esatto sul nome intero e sull'ultimo segmento —
            # il modello puo' riportare il percorso completo — e poi il
            # nome cercato DENTRO la stringa, che recupera l'etichetta per
            # esteso ("immagine 3 (1818-diversi-17809656/0001-pag-1.jpg)")
            # senza indebolire il confronto: un nome di file non compare
            # li' per caso.
            chiave = next(
                (k for k in per_nome if k == nome or Path(k).name == nome or nome in k),
                None,
            )
            if chiave is not None:
                allineate[indice] = per_nome.pop(chiave)
                break

    gia_usate = {id(v) for v in allineate if v is not None}
    avanzi = iter(v for v in voci if id(v) not in gia_usate)
    for indice in range(len(pagine)):
        if allineate[indice] is None:
            allineate[indice] = next(avanzi, None)
    return allineate


def trascrivi_gruppo(config: Config, pagine: list[Pagina], motore: Backend | None = None) -> Esito:
    """Trascrive un gruppo di pagine con una sola chiamata al modello.

    Se il gruppo non produce risultati utilizzabili e conteneva piu' di
    una pagina, riprova una pagina per volta: cosi' una pagina illeggibile
    non porta con se' le altre.
    """
    motore = motore or motori.crea(config)
    integrale = config.trascrizione.testo_integrale
    immagini = [prepara_immagini(p, config) for p in pagine]
    # L'ordine in cui vengono allegate deve essere lo stesso in cui
    # l'istruzione le nomina: e' su quello che si regge l'allineamento
    # quando il modello non ripete il nome del file.
    allegate = [percorso for gruppo in immagini for percorso in gruppo]

    esito = Esito(chiamate=1)
    risultato = motore.esegui(
        Richiesta(
            sistema=sistema(integrale),
            istruzione=costruisci_prompt(pagine, immagini, _forme_note(config), motore, integrale),
            immagini=allegate,
            schema=schema.risposta_gruppo(integrale),
            modello=config.trascrizione.modello,
            timeout_s=config.trascrizione.timeout_s,
        )
    )
    esito.token_contesto = risultato.token_contesto
    esito.token_output = risultato.token_output

    if not risultato.ok:
        logger.warning("Gruppo non riuscito (%s)", risultato.errore)
        return _ritenta_singole(config, pagine, esito, motore)

    try:
        dati = motori.estrai_json(risultato.testo)
    except ValueError as exc:
        logger.warning("Risposta non interpretabile: %s", exc)
        return _ritenta_singole(config, pagine, esito, motore)

    for pagina, voce in zip(pagine, _allinea(dati, pagine, immagini)):
        if voce is None:
            logger.warning("%s: nessun oggetto corrispondente nella risposta", pagina.id_richiesta)
            esito.fallite += 1
            continue
        try:
            _salva(config, pagina, valida_pagina(voce))
            esito.trascritte += 1
        except PaginaNonValida as exc:
            logger.warning("%s: %s", pagina.id_richiesta, exc)
            esito.fallite += 1
    return esito


def _ritenta_singole(
    config: Config, pagine: list[Pagina], esito: Esito, motore: Backend | None = None
) -> Esito:
    """Ripiego: una pagina per chiamata, per isolare quella problematica."""
    if len(pagine) == 1:
        esito.fallite += 1
        return esito
    logger.info("Riprovo le %d pagine una alla volta", len(pagine))
    for pagina in pagine:
        singolo = trascrivi_gruppo(config, [pagina], motore)
        esito.trascritte += singolo.trascritte
        esito.fallite += singolo.fallite
        esito.chiamate += singolo.chiamate
        esito.token_contesto += singolo.token_contesto
        esito.token_output += singolo.token_output
    return esito


def esegui(config: Config, pagine: list[Pagina], attendi_quota: bool = False) -> Esito:
    """Trascrive tutte le pagine, a gruppi, gestendo l'esaurimento della quota."""
    motore = motori.crea(config)
    motore.verifica()
    per_chiamata = max(1, config.trascrizione.pagine_per_chiamata)
    gruppi = [pagine[i : i + per_chiamata] for i in range(0, len(pagine), per_chiamata)]

    lavoratori = max(1, config.trascrizione.chiamate_parallele)
    if lavoratori > 1 and len(gruppi) > 1:
        return _esegui_in_parallelo(config, gruppi, motore, attendi_quota, lavoratori)
    return _esegui_in_sequenza(config, gruppi, motore, attendi_quota)


def _esegui_in_sequenza(
    config: Config, gruppi: list[list[Pagina]], motore: Backend, attendi_quota: bool
) -> Esito:
    totale = Esito()

    for indice, gruppo in enumerate(gruppi, 1):
        tentativi = 0
        while True:
            try:
                esito = trascrivi_gruppo(config, gruppo, motore)
                break
            except LimiteUsoRaggiunto as exc:
                tentativi += 1
                # Due cose diverse arrivano dalla stessa eccezione, e
                # confonderle costa caro in entrambi i versi.
                #
                # Un limite di RITMO — troppe richieste in un minuto — dura
                # qualche decina di secondi, e il servizio dice lui quanto:
                # fermare per questo un lavoro di migliaia di pagine
                # sarebbe assurdo, quindi si aspetta e si prosegue anche
                # senza --attendi.
                #
                # La QUOTA finita e' un'altra cosa: si rinnova a ore, e
                # decidere se restare qui ad aspettare tocca a chi ha
                # lanciato il comando.
                breve = _e_solo_ritmo(exc, tentativi)
                if not breve and not attendi_quota:
                    logger.warning("Quota di %s esaurita: %s", motore.nome, exc)
                    totale.quota_esaurita = True
                    return totale
                attesa = exc.attesa_s or ATTESA_QUOTA_S
                logger.info(
                    "%s, riprovo fra %s. Il lavoro fatto e' gia' salvato.",
                    "Limite di ritmo" if breve else "Quota esaurita",
                    _durata(attesa),
                )
                time.sleep(attesa)

        totale.trascritte += esito.trascritte
        totale.fallite += esito.fallite
        totale.chiamate += esito.chiamate
        totale.token_contesto += esito.token_contesto
        totale.token_output += esito.token_output
        logger.info(
            "[%d/%d gruppi] %d pagine trascritte, %d fallite",
            indice, len(gruppi), totale.trascritte, totale.fallite,
        )
    return totale


def _esegui_in_parallelo(
    config: Config,
    gruppi: list[list[Pagina]],
    motore: Backend,
    attendi_quota: bool,
    lavoratori: int,
) -> Esito:
    """Piu' chiamate aperte insieme, con le partenze sempre scandite dal ritmo.

    Non e' un modo di forzare i limiti: le richieste partono comunque una
    alla volta, distanziate da :meth:`gemini.Ritmo.attendi_turno`. Quello
    che il parallelismo riempie e' **l'attesa**, che e' quasi tutto il
    tempo: una chiamata da sei pagine impiega 88 secondi a generare, e per
    ottantasette di quelli il processo non fa nulla. Con un lavoratore
    solo si usa 0,7 del ritmo consentito.

    Sulla quota esaurita il comportamento e' quello sequenziale, applicato
    alla squadra: si smette di consegnare lavoro nuovo, i gruppi gia'
    partiti finiscono, e cio' che era su disco resta su disco.
    """
    import concurrent.futures
    import threading

    totale = Esito()
    conteggio = threading.Lock()
    basta = threading.Event()
    fatti = 0

    def lavora(gruppo: list[Pagina]) -> Esito | None:
        tentativi = 0
        while not basta.is_set():
            try:
                return trascrivi_gruppo(config, gruppo, motore)
            except LimiteUsoRaggiunto as exc:
                tentativi += 1
                breve = _e_solo_ritmo(exc, tentativi)
                if not breve and not attendi_quota:
                    logger.warning("Quota di %s esaurita: %s", motore.nome, exc)
                    basta.set()
                    return None
                attesa = exc.attesa_s or ATTESA_QUOTA_S
                logger.info(
                    "%s, riprovo fra %s. Il lavoro fatto e' gia' salvato.",
                    "Limite di ritmo" if breve else "Quota esaurita",
                    _durata(attesa),
                )
                # Un'attesa interrompibile: se un altro lavoratore nel
                # frattempo trova la quota finita, non restiamo fermi un
                # quarto d'ora per poi scoprirlo.
                basta.wait(attesa)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=lavoratori) as squadra:
        futuri = [squadra.submit(lavora, gruppo) for gruppo in gruppi]
        for futuro in concurrent.futures.as_completed(futuri):
            esito = futuro.result()
            with conteggio:
                fatti += 1
                if esito is None:
                    totale.quota_esaurita = True
                    continue
                totale.trascritte += esito.trascritte
                totale.fallite += esito.fallite
                totale.chiamate += esito.chiamate
                totale.token_contesto += esito.token_contesto
                totale.token_output += esito.token_output
                logger.info(
                    "[%d/%d gruppi] %d pagine trascritte, %d fallite",
                    fatti, len(gruppi), totale.trascritte, totale.fallite,
                )
    return totale


# --- quanto ci vuole davvero ------------------------------------------------
#
# Le prime versioni di questa stima dividevano le chiamate per il limite
# di richieste al minuto e chiudevano li'. Su 86 pagine prometteva due
# minuti; ce ne sono voluti diciannove. Il limite del piano ne consente
# dieci al minuto, ma **ne partono 0,7**: quello che decide non e' la
# quota, e' quanto ci mette il modello a scrivere la risposta.
#
# Da qui tre vincoli, e il tempo e' il maggiore dei tre. Dire quale dei
# tre morde vale piu' del numero stesso, perche' e' l'unica informazione
# che dice su quale manopola ha senso mettere le mani.

# Misurato su 15 chiamate da 6 pagine con gemini-3.6-flash e le facciate
# divise: mediana 88 s a chiamata (min 34, max 123), cioe' ~15 s a
# pagina. Il tempo segue i token prodotti, non le immagini lette.
SECONDI_PER_PAGINA = 15.0

# Senza la trascrizione diplomatica l'uscita cala di circa un terzo —
# misurato sulle prime 86 pagine — e con essa il tempo di generazione.
SCONTO_SENZA_TESTO_INTEGRALE = 0.66

# Misurati sulla stessa corsa: 16.246 token in ingresso e 12.890 in
# uscita per sei pagine, comprensivi del prompt e delle due immagini
# delle facciate.
TOKEN_INGRESSO_PER_PAGINA = 2_700
TOKEN_USCITA_PER_PAGINA = 2_150


def _tempo_gemini(config: Config, chiamate: int, pagine: int) -> tuple[float, str]:
    """Secondi previsti, e quale dei tre limiti li determina."""
    trascrizione = config.trascrizione
    sconto = 1.0 if trascrizione.testo_integrale else SCONTO_SENZA_TESTO_INTEGRALE

    # Il parallelismo divide SOLO la generazione: i limiti al minuto e
    # ai token valgono per l'intera squadra, non per lavoratore.
    lavoratori = max(1, trascrizione.chiamate_parallele)
    generazione = pagine * SECONDI_PER_PAGINA * sconto / lavoratori
    etichetta = "la generazione del modello"
    if lavoratori > 1:
        etichetta += f" ({lavoratori} chiamate in parallelo)"
    candidati = [(generazione, etichetta)]
    if trascrizione.richieste_al_minuto:
        candidati.append(
            (
                chiamate / trascrizione.richieste_al_minuto * 60,
                f"il limite di {trascrizione.richieste_al_minuto} richieste al minuto",
            )
        )
    if trascrizione.token_al_minuto:
        token = pagine * (TOKEN_INGRESSO_PER_PAGINA + TOKEN_USCITA_PER_PAGINA * sconto)
        candidati.append(
            (
                token / trascrizione.token_al_minuto * 60,
                f"il limite di {trascrizione.token_al_minuto:,} token al minuto".replace(",", "."),
            )
        )
    return max(candidati)


def _quante(numero: int, singolare: str, plurale: str) -> str:
    return f"{numero} {singolare if numero == 1 else plurale}"


def _durata(secondi: float) -> str:
    if secondi < 90:
        return f"{round(secondi)} secondi"
    if secondi < 5400:
        return f"{round(secondi / 60)} minuti"
    return f"{secondi / 3600:.1f} ore"


def stima(config: Config, pagine: list[Pagina]) -> str:
    """Cosa aspettarsi in termini di chiamate e di tempo.

    Nessuno dei due motori si misura in euro — l'abbonamento e' gia'
    pagato, il piano gratuito di Gemini non si paga affatto — ma si
    misurano in modo diverso, e la differenza e' il punto: Claude Code
    consuma quota a token, Gemini consuma **richieste al giorno**. Da qui
    due riepiloghi distinti.
    """
    if not pagine:
        return "Nessuna pagina da trascrivere."

    trascrizione = config.trascrizione
    per_chiamata = max(1, trascrizione.pagine_per_chiamata)
    chiamate = -(-len(pagine) // per_chiamata)  # divisione per eccesso
    testa = (
        f"{_quante(len(pagine), 'pagina', 'pagine')} da trascrivere con "
        f"{trascrizione.modello} ({trascrizione.backend})\n"
        f"  {_quante(chiamate, 'chiamata', 'chiamate')} "
        f"da {_quante(per_chiamata, 'pagina', 'pagine')}\n"
    )
    if not trascrizione.testo_integrale:
        testa += "  senza testo_integrale: circa un terzo di token prodotti in meno\n"

    if trascrizione.backend == "gemini":
        return testa + _stima_gemini(config, chiamate, len(pagine))
    return testa + _stima_claude_code(len(pagine), chiamate)


def _stima_gemini(config: Config, chiamate: int, pagine: int) -> str:
    al_giorno = config.trascrizione.richieste_al_giorno
    al_minuto = config.trascrizione.richieste_al_minuto
    righe = []

    secondi, vincolo = _tempo_gemini(config, chiamate, pagine)
    righe.append(f"  ~{_durata(secondi)} di esecuzione — il vincolo e' {vincolo}")
    if al_giorno:
        giorni = -(-chiamate // al_giorno)
        righe.append(
            f"  {_quante(giorni, 'giorno', 'giorni')} di quota gratuita "
            f"({al_giorno} chiamate al giorno)"
        )
        # Il contatore di oggi e' su disco, non nel processo: dirlo qui
        # evita di lanciare il lavoro convinti di avere una giornata
        # intera davanti quando ne resta un quarto.
        restano = motori.crea(config).ritmo.restano_oggi()
        if restano is not None:
            righe.append(
                f"  oggi restano {restano} chiamate, cioe' {restano * config.trascrizione.pagine_per_chiamata} pagine"
            )
    righe.append("")
    righe.append("  Il piano gratuito ha tre limiti, non uno: richieste al")
    righe.append("  minuto, TOKEN al minuto, richieste al giorno. Alzare")
    righe.append("  pagine_per_chiamata fa piu' pagine al giorno, ma il tetto")
    righe.append("  e' il token al minuto: oltre, arrivano i 429.")
    righe.append("  I tuoi limiti veri stanno su aistudio.google.com/rate-limit")
    righe.append("  Usa --attendi per lasciar riprendere il lavoro al rinnovo;")
    righe.append("  ogni pagina finita e' gia' salvata.")
    return "\n".join(righe)


def _stima_claude_code(quante: int, chiamate: int) -> str:
    # Misurato: ~50.000 token di impalcatura per invocazione, piu' circa
    # 2.100 per immagine a 1568 px di lato lungo.
    contesto = chiamate * 50_000 + quante * 2_100
    return (
        f"  ~{contesto / 1e6:.1f}M token di contesto complessivi\n"
        f"  di cui {chiamate * 50_000 / contesto:.0%} e' impalcatura della CLI,\n"
        f"  pagata a ogni invocazione e non per pagina\n"
        f"\n"
        f"  La quota dell'abbonamento si rinnova a finestre: e' normale che\n"
        f"  un lavoro di questa mole si fermi e riprenda piu' volte. Usa\n"
        f"  --attendi per lasciarlo andare da solo; ogni pagina finita e'\n"
        f"  salvata subito, quindi rilanciare non rifa' mai il lavoro fatto."
    )
