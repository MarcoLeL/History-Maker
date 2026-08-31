"""Trovare sulla pagina il punto di una parola dubbia, e ritagliarlo.

Il motivo per cui questo modulo esiste sta in due numeri. Le pagine
originali sono circa 3900x2500 pixel; le copie che il modello legge sono
ridotte a 1568 di lato lungo, cioe' il **40% della risoluzione lineare**.
Quel 60% perduto e' la causa prima delle letture sbagliate: ``Femminilli``
letto ``Tommolilli``, ``Rua di Nuorro`` letto ``Via di Ricovro``, sono
tutti perfettamente leggibili sull'originale e ambigui sulla riduzione.

Un ritaglio di due righe preso dall'**originale** costa circa 164 token
contro i ~2.100 di una pagina intera ridotta: tredici volte meno, con due
volte e mezzo il dettaglio per caratterei. Non e' un compromesso fra costo
e qualita', e' meglio su entrambi i fronti.

**Il problema aperto e' localizzare.** Le trascrizioni non portano
coordinate, quindi la riga si stima: la posizione della parola dentro
``testo_integrale`` viene mappata in proporzione sull'altezza dello
specchio di scrittura, e attorno a quel punto si prende una banda
generosa. E' una stima, non una misura, e il modo giusto di usarla e'
lasciare che chi legge il ritaglio dica "qui non lo vedo" quando la banda
ha sbagliato bersaglio.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Larghezza a cui si riduce la pagina per cercarne lo specchio di
# scrittura. Non serve precisione: si cercano bande di righe, non lettere.
LARGHEZZA_ANALISI = 500

# Quante righe prendere attorno al punto stimato. Misurato sui casi noti
# del 1809, la stima proporzionale sbaglia di una o due righe: con due
# righe di margine il bersaglio finiva sul bordo del ritaglio. Corretto il
# difetto per cui i bordi neri della scansione gonfiavano lo specchio di
# scrittura, la stima centra la riga giusta: tre righe bastano, e lasciano
# comunque spazio all'errore residuo.
RIGHE_DI_MARGINE = 3

# Altezza tipica di una riga manoscritta, in frazione dell'altezza dello
# specchio di scrittura. Ricavata dai registri del 1809: una pagina porta
# fra le venti e le trenta righe.
ALTEZZA_RIGA = 1 / 25


@dataclass(frozen=True)
class Banda:
    """Il rettangolo da ritagliare, in pixel dell'immagine originale."""

    sinistra: int
    alto: int
    destra: int
    basso: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.sinistra, self.alto, self.destra, self.basso)

    @property
    def token_stimati(self) -> int:
        larghezza = self.destra - self.sinistra
        altezza = self.basso - self.alto
        return round(larghezza * altezza / 750)


def _griglia(immagine: Image.Image) -> tuple[list[int], int, int, float, float]:
    """Pixel ridotti in scala di grigi, piu' le soglie ricavate dalla pagina.

    Le soglie si ricavano dalla scansione stessa e non si fissano una
    volta per tutte: questi registri hanno bordi neri, carta ingiallita e
    inchiostro sbiadito in misura diversa da pagina a pagina.
    """
    grigia = immagine.convert("L")
    fattore = LARGHEZZA_ANALISI / grigia.width
    piccola = grigia.resize(
        (LARGHEZZA_ANALISI, max(1, round(grigia.height * fattore))), Image.BILINEAR
    )
    pixel = list(piccola.tobytes())  # getdata() e deprecato da Pillow 14
    ordinati = sorted(pixel)
    carta = ordinati[int(len(ordinati) * 0.75)]
    return pixel, piccola.width, piccola.height, carta, carta * 0.72


def _estensione_carta_verticale(
    pixel: list[int], larghezza: int, altezza: int, carta: float
) -> tuple[int, int]:
    """Da quale a quale riga c'e' il foglio, esclusi i bordi neri."""
    chiare = [
        y
        for y in range(altezza)
        if sum(1 for v in pixel[y * larghezza : (y + 1) * larghezza] if v > carta * 0.85)
        > larghezza * 0.5
    ]
    return (chiare[0], chiare[-1] + 1) if chiare else (0, altezza)


def _profilo_colonne(immagine: Image.Image) -> list[float]:
    """Quanto inchiostro c'e' in ciascuna colonna, da 0 a 1.

    Serve perche' una scansione e' una **doppia pagina**: il testo occupa
    circa un terzo della larghezza, e ritagliare tutto il resto
    triplicherebbe i token per mostrare carta bianca e bordi neri.
    """
    pixel, larghezza, altezza, carta, soglia = _griglia(immagine)
    # Come per le righe, e per lo stesso motivo: i bordi neri in alto e in
    # basso mettono pixel scuri in OGNI colonna, e senza escluderli
    # risulterebbe scritta anche la meta' bianca della scansione.
    y0, y1 = _estensione_carta_verticale(pixel, larghezza, altezza, carta)
    utile = max(1, y1 - y0)

    profilo = []
    for x in range(larghezza):
        colonna = pixel[x::larghezza][y0:y1]
        scuri = sum(1 for v in colonna if v < soglia)
        chiari = sum(1 for v in colonna if v > carta * 0.85)
        # Una colonna di testo e' fatta soprattutto di carta, con sopra
        # un po' d'inchiostro. Il bordo nero della scansione e' invece
        # densissimo e senza carta: senza questa condizione risulterebbe
        # lui la "colonna piu' scritta" della pagina.
        profilo.append(scuri / utile if chiari > utile * 0.55 else 0.0)
    return profilo


def _estensione_carta(
    pixel: list[int], larghezza: int, altezza: int, carta: float
) -> tuple[int, int]:
    """Da quale a quale colonna c'e' il foglio, esclusi i bordi neri."""
    chiare = [
        x
        for x in range(larghezza)
        if sum(1 for v in pixel[x::larghezza] if v > carta * 0.85) > altezza * 0.5
    ]
    return (chiare[0], chiare[-1] + 1) if chiare else (0, larghezza)


def _profilo_righe(immagine: Image.Image) -> list[float]:
    """Quanto inchiostro c'e' in ciascuna riga, **entro il foglio**.

    Il ritaglio delle colonne di bordo non e' un dettaglio. Le scansioni
    hanno una cornice nera su tutti e quattro i lati, e quella cornice
    porta pixel scuri in OGNI riga: contandoli, ogni riga risulta scritta
    e lo specchio di scrittura viene lungo quanto l'intera immagine. E'
    esattamente il difetto che faceva sbagliare la stima verticale di una
    o due righe.
    """
    pixel, larghezza, altezza, carta, soglia = _griglia(immagine)
    x0, x1 = _estensione_carta(pixel, larghezza, altezza, carta)
    utile = max(1, x1 - x0)

    profilo = []
    for y in range(altezza):
        riga = pixel[y * larghezza + x0 : y * larghezza + x1]
        scuri = sum(1 for v in riga if v < soglia)
        chiari = sum(1 for v in riga if v > carta * 0.85)
        # Una riga fuori dal foglio e' scura ma non ha carta: si scarta.
        profilo.append(scuri / utile if chiari > utile * 0.30 else 0.0)
    return profilo


def specchio_di_scrittura(percorso: Path) -> tuple[float, float]:
    """Dove comincia e dove finisce il testo, in frazione dell'altezza.

    Restituisce ``(inizio, fine)`` fra 0 e 1. Serve a non mappare la
    posizione di una parola sull'intera scansione, che comprende bordi
    neri e margini bianchi dove testo non ce n'e'.
    """
    with Image.open(percorso) as immagine:
        profilo = _profilo_righe(immagine)

    if not profilo:
        return 0.0, 1.0
    massimo = max(profilo)
    if massimo <= 0:
        return 0.0, 1.0

    soglia = massimo * 0.18
    con_testo = [i for i, v in enumerate(profilo) if v >= soglia]
    if not con_testo:
        return 0.0, 1.0
    return con_testo[0] / len(profilo), (con_testo[-1] + 1) / len(profilo)


def _senza_accenti(testo: str) -> str:
    scomposto = unicodedata.normalize("NFD", testo)
    return "".join(c for c in scomposto if unicodedata.category(c) != "Mn").lower()


def posizione_nel_testo(testo: str | None, bersaglio: str | None) -> float | None:
    """A che punto del testo compare il bersaglio, da 0 a 1.

    Il confronto ignora accenti e maiuscole; se il bersaglio non compare
    si restituisce ``None``, e chi chiama decidera' che fare — di solito
    ritagliare l'intero specchio di scrittura, che e' comunque piu' nitido
    della pagina ridotta.
    """
    if not testo or not bersaglio:
        return None
    piatto = _senza_accenti(testo)
    dove = piatto.find(_senza_accenti(bersaglio))
    if dove < 0:
        return None
    return dove / max(1, len(piatto))


def banda_per(
    percorso: Path,
    testo: str | None,
    bersaglio: str | None,
    righe_di_margine: int = RIGHE_DI_MARGINE,
    lato: str | None = None,
) -> Banda:
    """Il rettangolo che con ogni probabilita' contiene il bersaglio.

    Quando il bersaglio non si trova nel testo si ritaglia tutto lo
    specchio di scrittura: costa piu' token di una banda ma sempre meno
    di una pagina intera, e resta a piena risoluzione.
    """
    with Image.open(percorso) as immagine:
        larghezza, altezza = immagine.size

    inizio, fine = specchio_di_scrittura(percorso)
    if lato in ("sinistra", "destra"):
        # Quando la trascrizione dice su quale delle due pagine sta
        # l'atto, il ritaglio si dimezza senza rischiare niente. E' il
        # dato che rende questa fase economica oltre che precisa: senza,
        # bisogna tenere tutta la larghezza per non tagliare fuori il
        # bersaglio, e una banda larga costa quanto la pagina intera.
        sinistra, destra = (0.0, 0.53) if lato == "sinistra" else (0.47, 1.0)
    else:
        sinistra, destra = colonna_di_scrittura(percorso)
    x0, x1 = round(larghezza * sinistra), round(larghezza * destra)

    dove = posizione_nel_testo(testo, bersaglio)
    if dove is None:
        return Banda(x0, round(altezza * inizio), x1, round(altezza * fine))

    estensione = fine - inizio
    centro = inizio + dove * estensione
    mezza = ALTEZZA_RIGA * estensione * (righe_di_margine + 0.5)

    alto = max(0, round(altezza * (centro - mezza)))
    basso = min(altezza, round(altezza * (centro + mezza)))
    return Banda(x0, alto, x1, basso)


def ritaglia(percorso: Path, banda: Banda, destinazione: Path) -> Path:
    """Scrive il ritaglio, **senza ingrandirlo**.

    Ingrandire non aggiunge informazione ma moltiplica i token: il
    guadagno di questo modulo sta nel prendere i pixel dall'originale, non
    nello stiracchiarli.
    """
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(percorso) as immagine:
        immagine.crop(banda.box).save(destinazione, "JPEG", quality=92)
    return destinazione


def colonna_di_scrittura(percorso: Path) -> tuple[float, float]:
    """Dove comincia e finisce il testo in orizzontale, in frazione.

    Una scansione dei registri e' una doppia pagina: il verso e' spesso
    vuoto e il recto porta il testo su circa un terzo della larghezza
    totale. Ritagliare anche il resto non aggiunge nulla da leggere e
    triplica il conto dei token.
    """
    with Image.open(percorso) as immagine:
        profilo = _profilo_colonne(immagine)

    if not profilo or max(profilo) <= 0:
        return 0.0, 1.0

    # Le scansioni sono **doppie pagine**, e a volte entrambe sono
    # scritte. Cercare "il blocco di testo" con una soglia sola e' quindi
    # fragile: su una pagina prende la colonna giusta, sulla successiva si
    # aggancia al bordo o alla scrittura che traspare dal foglio di sotto.
    #
    # Scegliere "la meta' con piu' inchiostro" non funziona: su queste
    # pagine l'atto compilato sta spesso a sinistra e a destra c'e' il
    # modulo prestampato ancora vuoto, che e' PIU' denso — la stampa e'
    # piu' nera della scrittura a mano. Provato, e sbagliava meta' dei
    # casi.
    #
    # Si restringe percio' solo quando una meta' e' praticamente vuota,
    # che e' il caso frequente del verso bianco e si riconosce senza
    # ambiguita'. Quando entrambe portano scrittura si tiene la larghezza
    # intera: costa il doppio dei token, ma un ritaglio stretto sul punto
    # sbagliato non costa token — costa la risposta.
    meta = len(profilo) // 2
    a_sinistra, a_destra = sum(profilo[:meta]), sum(profilo[meta:])
    scarsa, ricca = sorted((a_sinistra, a_destra))
    if ricca <= 0 or scarsa > ricca * 0.25:
        return 0.0, 1.0

    base, fine_base = (0, meta) if a_sinistra > a_destra else (meta, len(profilo))
    margine = len(profilo) * 0.03  # le lettere sporgono oltre la colonna densa
    return (
        max(0.0, (base - margine) / len(profilo)),
        min(1.0, (fine_base + margine) / len(profilo)),
    )


# --- scegliere cosa mandare al modello in lettura --------------------------
#
# Le scansioni sono doppie pagine: da un lato l'atto compilato, dall'altro
# il modulo prestampato ancora vuoto. Riducendo l'intera doppia pagina a
# 1568 px si spende meta' del budget per fotografare un foglio senza
# scrittura, e la pagina che conta arriva al modello a circa il 40% della
# sua risoluzione. E' la causa prima delle letture sbagliate.

# Sotto questo rapporto fra l'inchiostro delle due meta', una e'
# palesemente vuota e si puo' scartare senza pensarci.
RAPPORTO_META_VUOTA = 0.10

# Quando entrambe le meta' portano segni, decide il conteggio dei
# mezzitoni; ma solo se il margine e' netto, altrimenti si tiene tutto.
MARGINE_MEZZITONI = 0.10


def _mezzitoni_per_meta(immagine: Image.Image) -> tuple[int, int]:
    """Quanti pixel di tono intermedio ci sono nelle due meta'.

    E' il modo per distinguere la pagina scritta da quella col solo
    modulo, quando entrambe portano stampa. La tipografia e' nera piena;
    la scrittura a mano di questi registri e' a inchiostro bruno,
    piu' chiaro, e cade nella fascia intermedia fra stampa e carta.
    Contare l'inchiostro totale non basta: la stampa e' piu' densa della
    scrittura, e la meta' VUOTA risulterebbe la piu' scritta.
    """
    pixel, larghezza, altezza, carta, _ = _griglia(immagine)
    y0, y1 = _estensione_carta_verticale(pixel, larghezza, altezza, carta)
    basso, alto = carta * 0.45, carta * 0.80

    def conta(x0: int, x1: int) -> int:
        totale = 0
        for y in range(y0, y1):
            totale += sum(1 for v in pixel[y * larghezza + x0 : y * larghezza + x1] if basso < v < alto)
        return totale

    meta = larghezza // 2
    return conta(0, meta), conta(meta, larghezza)


def lato_scritto(percorso: Path) -> str | None:
    """Su quale meta' della scansione sta il testo compilato.

    Restituisce ``"sinistra"``, ``"destra"`` oppure ``None`` quando le due
    meta' non si distinguono abbastanza. ``None`` non e' un fallimento: e'
    la risposta prudente, e chi chiama terra' l'immagine intera. Un
    ritaglio sbagliato costa la pagina, uno mancato costa qualche token.
    """
    with Image.open(percorso) as immagine:
        colonne = _profilo_colonne(immagine)
        meta = len(colonne) // 2
        inchiostro = (sum(colonne[:meta]), sum(colonne[meta:]))
        mezzitoni = _mezzitoni_per_meta(immagine)

    # Servono ENTRAMBE le prove, e larghe. La prima versione si
    # accontentava di una sola e scartava la meta' sbagliata su un quarto
    # delle pagine: su questi registri una doppia pagina porta spesso un
    # atto PER LATO, non un atto e un modulo vuoto, e il ritaglio buttava
    # via un atto intero. Misurato: 124 atti prima, 46 cognomi persi dopo.
    #
    # Il costo dei due errori non e' simmetrico. Non ritagliare costa
    # qualche token; ritagliare di traverso cancella un atto e nessuno se
    # ne accorge, perche' la trascrizione che ne esce e' perfettamente
    # plausibile.
    scarso, ricco = sorted(inchiostro)
    if ricco <= 0 or scarso >= ricco * RAPPORTO_META_VUOTA:
        return None

    scarsi_mt, ricchi_mt = sorted(mezzitoni)
    if ricchi_mt <= 0 or scarsi_mt >= ricchi_mt * RAPPORTO_META_VUOTA:
        return None

    per_inchiostro = "sinistra" if inchiostro[0] > inchiostro[1] else "destra"
    per_mezzitoni = "sinistra" if mezzitoni[0] > mezzitoni[1] else "destra"
    # Se i due indizi non concordano, non si taglia.
    return per_inchiostro if per_inchiostro == per_mezzitoni else None


def area_da_leggere(percorso: Path) -> tuple[int, int, int, int]:
    """Il rettangolo della scansione che vale la pena mandare al modello.

    Toglie la cornice nera, i margini bianchi e — quando si riesce a
    stabilirlo — la meta' non scritta. Quello che resta, ridotto alla
    stessa dimensione di prima, arriva al modello molto piu' grande.
    """
    with Image.open(percorso) as immagine:
        larghezza, altezza = immagine.size
        pixel, la, al, carta, _ = _griglia(immagine)
        x0f, x1f = _estensione_carta(pixel, la, al, carta)
        y0f, y1f = _estensione_carta_verticale(pixel, la, al, carta)

    sinistra = round(larghezza * x0f / la)
    destra = round(larghezza * x1f / la)
    alto = round(altezza * y0f / al)
    basso = round(altezza * y1f / al)

    lato = lato_scritto(percorso)
    if lato:
        meta = larghezza // 2
        margine = round(larghezza * 0.02)  # la piega centrale non taglia lettere
        if lato == "sinistra":
            destra = min(destra, meta + margine)
        else:
            sinistra = max(sinistra, meta - margine)

    return (sinistra, alto, destra, basso)
