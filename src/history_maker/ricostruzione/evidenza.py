"""Quanto vale un indizio, misurato sul paese invece che deciso a mano.

Questo modulo risponde a una sola domanda: **quanto e' piu' probabile
vedere questi due gruppi di menzioni se sono la stessa persona che se non
lo sono?** La risposta e' un numero in ban — logaritmi in base dieci del
rapporto fra le due probabilita' — e i numeri in ban si sommano.

Perche' non pesi fissi
----------------------

Il sistema precedente diceva: due genitori uguali valgono 4,0, un coniuge
2,5, il mestiere 0,5; sopra 3,0 si unisce. Sono numeri onesti e sono
sbagliati per costruzione, perche' non sanno **dove** si trovano.

A Torrebruna 'Pelliccia' sono 5.121 menzioni su 49.889. Due Pelliccia non
sono un indizio: sono la normalita' del paese. 'Genualdi' compare sei
volte in un secolo, e due Genualdi sono quasi certamente parenti. Un peso
fisso sul cognome vale uguale nei due casi, e non c'e' modo di aggiustarlo
— alzarlo cuce insieme i Pelliccia, abbassarlo lascia separati i Genualdi.

Il rapporto di verosimiglianza risolve la cosa da solo::

    peso = log10( P(stessa forma | stessa persona) / frequenza della forma )

Per Pelliccia: log10(0,80 / 0,103) = +0,89 — un indizio debole, che e'
esattamente quello che e'. Per Genualdi: log10(0,80 / 0,00012) = +3,8,
tagliato al tetto — un indizio forte, che e' esattamente quello che e'.
La stessa formula, lo stesso codice, nessuna soglia da rinegoziare.

Vale per tutto: nomi, cognomi, mestieri, contrade, e soprattutto per le
**chiavi di parentela**. 'figlio di Filippo Lella e Margherita Rossi' e'
raro nello stesso modo in cui e' raro un cognome, e il peso se lo calcola
da solo.

I quattro veti che restano
--------------------------

Tutto il resto e' graduale, ma quattro cose restano assolute, e sono le
sole che il mondo davvero non consente: due atti di nascita distinti, due
atti di morte distinti, comparire vivi dopo il proprio funerale, partorire
per piu' anni di quanti una donna ne abbia di fertili. A queste si
aggiunge la sola regola logica del mestiere: due righe legate da una
parentela **dentro lo stesso atto** sono due persone, sempre.

Un cognome diverso, un'eta' che non torna, un mestiere cambiato, una
contrada diversa: nessuna di queste e' un veto. Sono indizi contrari, e
un'evidenza piu' forte li puo' superare. E' la differenza che spiega la
meta' delle famiglie che il sistema precedente spezzava in due.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from history_maker import menzioni as lettura_atti, nomi, paleografia
from history_maker.ricostruzione import modello as mod_dati
from history_maker.ricostruzione.modello import Evidenza
from history_maker.ricostruzione.scheda import Scheda, principali

# ---------------------------------------------------------------------------
# Le probabilita' del modello
# ---------------------------------------------------------------------------
#
# Ognuna risponde alla domanda: "sapendo che due menzioni parlano della
# STESSA persona, quanto spesso questo campo coincide?". Sono i parametri
# ``m`` del modello di Fellegi e Sunter, e si stimano dal corpus stesso:
# vedi 'stima_dal_corpus' piu' sotto, che li ricalibra sui casi in cui
# l'identita' e' certa per altra via.

M_COGNOME_UGUALE = 0.80        # stessa forma canonica
M_COGNOME_VARIANTE = 0.13      # forma vicina ma diversa: Lella / Lelli
M_COGNOME_ESTRANEO = 0.03      # cognome del tutto diverso: succede, e va pesato
M_NOME_UGUALE = 0.82
M_NOME_VARIANTE = 0.12
M_NOME_ESTRANEO = 0.02
M_PROFESSIONE = 0.55           # una persona cambia mestiere, ma non sempre
M_CONTRADA = 0.50
M_CHIAVE_GENITORE = 0.60
M_CHIAVE_CONIUGE = 0.65
M_PATRONIMICO = 0.55

# I tetti. Un indizio solo, per raro che sia, non deve poter decidere da
# solo: un cognome visto una volta in un secolo puo' essere un errore di
# lettura tanto quanto una famiglia rara.
TETTO_COGNOME = 3.0
TETTO_NOME = 2.6
TETTO_PARENTELA = 3.2
TETTO_MESTIERE = 0.6
TETTO_CONTRADA = 0.35

# La gerarchia delle evidenze, resa in un numero solo.
#
# Il nome, il cognome, l'eta' e la grafia sono **indizi deboli**: in un
# paese dove il primogenito porta il nome del nonno e i Pelliccia sono
# cinquemila menzioni, la loro somma non deve poter decidere una fusione
# da sola. Sopra questo tetto non contano piu': per unire due schede
# serve almeno un indizio di quelli medi — un mestiere raro, una
# contrada, un ufficio — o meglio ancora uno forte: un genitore, un
# coniuge, un figlio.
#
# Non e' una regola in piu' appiccicata al modello: e' il modo in cui il
# modello ammette di violare l'indipendenza fra i campi. Nome e cognome
# non sono indipendenti dal fatto che due schede siano finite nello
# stesso scaffale, e sommarli per intero conta due volte la stessa cosa.
# Misurato: senza questo tetto la ricostruzione univa 'Angela Lella' con
# 'Angela Lella' sul solo nome piu' il mestiere, e produceva una donna
# con trenta parti in trentanove anni.
#
# Il tetto e' una **frazione della distanza fra l'a priori e la
# certezza**, non un numero fisso: dev'essere legato alla dimensione
# dell'archivio come lo e' l'a priori, altrimenti su un comune piccolo
# non morde e su uno grande stronca anche le fusioni buone. A 0,75 gli
# indizi deboli percorrono tre quarti della strada e non arrivano:
# l'ultimo tratto lo deve fare qualcos'altro.
#
# Il numero non e' libero. Deve valere
#
#     a_priori + QUOTA * |a_priori|  <  SOGLIA_UNIONE
#
# altrimenti nome e cognome, da soli e al massimo, basterebbero a unire,
# e tutto il resto di questo modulo non servirebbe a niente. Con l'a
# priori di Torrebruna (-4,26) e la soglia a -0,90 il margine e' di un
# decimo di ban: stretto, e voluto. C'e' un test che lo verifica
# (``test_il_tetto_agli_indizi_deboli``), perche' e' un legame che si
# rompe in silenzio quando qualcuno ritocca una delle due soglie.
QUOTA_INDIZI_DEBOLI = 0.75

# Sotto questo numero di matrimoni il tasso di seconde nozze non si
# misura: si tiene il ripiego.
MINIMI_PER_MISURARE = 100
# Nemmeno il coniuge discorde puo' valere quanto un veto.
TETTO_SECONDE_NOZZE = -3.0
# Quando i due coniugi hanno lo stesso nome di battesimo e cognomi
# diversi, non sono due nozze: e' una lettura rovinata. Costa poco, come
# costava prima a tutti.
PESO_CONIUGE_COGNOME_DISCORDE = -0.35
# Quanto devono somigliarsi due nomi di battesimo per essere lo stesso.
NOME_CONIUGE_UGUALE = 0.80
# Il tetto di cio' che un coniuge riconosciuto puo' valere. Meno del
# coniuge in comune vero (2,4), perche' li' le due schede sono gia' la
# stessa e qui l'identita' e' dedotta.
PESO_CONIUGE_RICONOSCIUTO = 1.6
# Quanti anni di scarto possono avere due letture della stessa moglie.
SCARTO_NASCITA_CONIUGI = 5

# Cosa sta sotto il tetto e cosa no. **L'eta' sta fuori**, ed e' una
# distinzione che vale quattromila persone: due menzioni con lo stesso
# nome, lo stesso cognome e la stessa eta' dichiarata sono tre indizi
# concordi, non uno, ed e' cosi' che si riconosce un testimone che torna.
# Sotto il tetto ci vanno solo gli indizi che dicono **come si chiama** —
# e in un paese di poche famiglie il nome, da solo, non identifica
# nessuno. Mettendoci dentro anche l'eta', le persone salivano da 13.600
# a 18.200 e i bambini ritrovati da adulti scendevano di un terzo.
INDIZI_DEBOLI = frozenset({"nome", "cognome", "finestra"})

# Le soglie di somiglianza che distinguono 'stessa forma', 'variante' e
# 'forma estranea'. Non sono veti: dicono solo in quale dei tre casi si
# entra, e ogni caso ha il suo peso.
VICINO_COGNOME = 0.78
VICINO_NOME = 0.82

# --- i legami come identita' -----------------------------------------------
#
# Sono l'evidenza migliore che il sistema possa avere, perche' non passano
# per nessun nome ricopiato: 'sua moglie e' **quella** scheda'. I valori
# stanno in una scala sola e la gerarchia e' quella che i registri
# suggeriscono, non una preferenza estetica.
PESO_FIGLIO_IN_COMUNE = 2.8    # due genitori dello stesso bambino
PESO_CONIUGE_IN_COMUNE = 2.4   # due mariti della stessa donna
PESO_GENITORI_IN_COMUNE = 1.6  # stesso padre E stessa madre: fratelli o la stessa
PESO_UN_GENITORE_IN_COMUNE = 0.5
PESO_NUCLEO_IN_COMUNE = 1.2    # in piu', quando coincidono coniuge e figli

# Gli indizi contrari. Nessuno di questi e' un veto, e la ragione e'
# scritta accanto a ciascuno.
PESO_GENITORI_DISCORDI = -1.6  # due famiglie diverse: forte, ma una lettura
                               # rovinata del cognome di una madre lo produce
# Quanto costa un secondo coniuge. NON e' una costante: la misura il
# corpus, perche' era il numero che avevo sbagliato di piu'. Qui c'era
# -0,35 con scritto accanto «ci si risposa, e le vedove si risposano
# spesso», che era una cosa che credevo e non avevo contato. A Torrebruna
# si risposano **cinque persone su 1.172**: -2,37 ban, non -0,35. Un
# fattore cento, e proprio nel punto in cui il calcolo decideva se
# attaccare una moglie in piu' a un uomo che ne aveva gia' una.
PESO_CONIUGE_DISCORDE = -2.4   # ripiego; il valore vero lo da' tasso_seconde_nozze
PESO_MESTIERE_DIVERSO = -0.12  # bovaro nel 1850, contadino nel 1875
PESO_CONTRADA_DIVERSA = -0.08  # ci si trasferisce dentro il paese
PESO_SESSO_DEDOTTO_DIVERSO = -1.5  # dedotto dal nome, non dal ruolo

# --- l'eta' ----------------------------------------------------------------
#
# L'eta' dichiarata e' il dato piu' rumoroso del registro: la si dice a
# voce, la si arrotonda, e lo scrivano la scrive come l'ha sentita. La
# distribuzione dello scarto fra due eta' della stessa persona non e' una
# gaussiana: ha un picco stretto e una coda lunga, ed e' la coda che
# conta, perche' e' li' che il sistema precedente spezzava le persone.
# Misurata sui casi in cui l'identita' e' certa per altra via, lo scarto
# tipico e' di tre anni e la coda arriva oltre i dodici.
SCALA_STRETTA = 2.0
SCALA_LARGA = 8.0
QUOTA_STRETTA = 0.88
QUOTA_LARGA = 0.11
# Quando almeno una delle due eta' e' dichiarata come approssimata
# ('circa quaranta'), le scale si allargano.
SCALA_STRETTA_APPROSSIMATA = 3.0
SCALA_LARGA_APPROSSIMATA = 11.0
# Quanto sono sparsi gli anni di nascita di due omonimi presi a caso:
# l'arco utile di un secolo di registri, cioe' il denominatore.
ARCO_OMONIMI = 50.0

# --- i veti ----------------------------------------------------------------

# Fra due atti di nascita non c'e' tolleranza: sono due bambini.
TOLLERANZA_NASCITA_CERTA = 1

# Fra un atto di nascita e un'eta' dichiarata la tolleranza c'e', ed e'
# larga, perche' l'eta' e' detta a voce e arrotondata. Ma non e'
# infinita: quindici anni non sono un arrotondamento. Il numero e'
# volutamente generoso — il controllo di qualita' segnala gia' a dieci —
# perche' qui un veto sbagliato spezza una persona per sempre.
#
# Senza questo, l'errore che ne veniva era sempre lo stesso e sempre
# grosso: lo sposo di ventinove anni del 1809 finiva nella stessa scheda
# del neonato battezzato con il suo nome nello stesso anno, e da li' in
# poi quella scheda aveva due madri, due padri e un matrimonio a zero
# anni.
TOLLERANZA_NASCITA_DICHIARATA = 15

# L'eta' minima che un ruolo implica sta in :mod:`modello`, insieme al
# resto di cio' che si sa del mondo e non dei registri; la scheda la
# aggrega in 'nascita_al_piu_tardi'.
ETA_MINIMA_RUOLO = mod_dati.ETA_MINIMA_RUOLO
# Per quanti anni si puo' avere figli. Non viene da una statistica sui
# registri ma dalla biologia, e non ha eccezioni da negoziare.
ARCO_FERTILE = {"F": 45, "M": 60}
# Di quanto un figlio puo' nascere dopo la morte del genitore: per la
# madre di niente, per il padre di una gravidanza.
POSTUMO_PADRE = 1


def _laplace(scarto: float, scala: float) -> float:
    return math.exp(-abs(scarto) / scala) / (2.0 * scala)


def peso_eta(scarto: float, approssimata: bool) -> float:
    """Quanto pesa uno scarto di ``scarto`` anni fra due eta' dichiarate.

    >>> round(peso_eta(0, False), 2)
    1.06
    >>> round(peso_eta(6, False), 2)
    -0.14
    >>> peso_eta(12, False) < peso_eta(6, False) < peso_eta(0, False)
    True

    La coda e' lunga apposta: dodici anni di scarto costano circa un ban,
    che un coniuge in comune ripaga tre volte. E' il caso di Michelangelo
    Colella e Maria Marianacci, sedici figli in una scheda e due
    nell'altra, tenute separate dal sistema precedente da nove anni di
    eta' dichiarata.
    """
    stretta = SCALA_STRETTA_APPROSSIMATA if approssimata else SCALA_STRETTA
    larga = SCALA_LARGA_APPROSSIMATA if approssimata else SCALA_LARGA
    densita = (
        QUOTA_STRETTA * _laplace(scarto, stretta)
        + QUOTA_LARGA * _laplace(scarto, larga)
        + (1.0 - QUOTA_STRETTA - QUOTA_LARGA) / 60.0
    )
    return math.log10(densita * ARCO_OMONIMI)


# ---------------------------------------------------------------------------
# Il modello
# ---------------------------------------------------------------------------

def vicinato(forme: set, soglia: float) -> dict:
    """Per ogni forma, quelle che una mano ottocentesca puo' confondere con lei.

    E' il conto piu' caro dell'intera fase — tremila cognomi a due a due
    sono nove milioni di distanze pesate — e ha due difese.

    La prima e' la **lunghezza**: cancellare un carattere costa 1, quindi
    due forme troppo diverse in lunghezza non possono superare la soglia,
    e le forme ordinate per lunghezza permettono di uscire dal ciclo
    invece di saltare una coppia per volta.

    La seconda sono le **lettere in comune**. Ogni carattere che sta in
    una e non nell'altra va cancellato o sostituito, e la sostituzione
    piu' economica di questa tabella costa 0,3: se le lettere estranee
    sono troppe, nessuna combinazione di scambi plausibili puo' arrivare
    alla soglia, e la distanza non serve nemmeno calcolarla. Misurato sui
    2.958 cognomi di Torrebruna il secondo filtro non guadagna niente —
    il taglio sulla lunghezza aveva gia' tolto quasi tutto — e resta
    perche' su forme piu' lunghe di un cognome quel taglio smette di
    bastare. E' comunque **conservativo**: non scarta mai una coppia che
    avrebbe superato la soglia, e il conto e' verificato contro la
    versione senza filtro.

    Il costo vero non si abbatte qui ma nella cache: vedi
    :mod:`history_maker.ricostruzione.cache`.
    """
    ordinate = sorted(forme)
    ordinate.sort(key=len)
    lettere = {forma: set(paleografia.normalizza(forma)) for forma in ordinate}
    quante = {forma: len(paleografia.normalizza(forma)) for forma in ordinate}
    vicini: dict = {forma: {forma} for forma in ordinate}

    for indice, corta in enumerate(ordinate):
        if not corta:
            continue
        sue_lettere = lettere[corta]
        for lunga in ordinate[indice + 1:]:
            massimo = max(quante[corta], quante[lunga])
            if quante[corta] < soglia * quante[lunga]:
                break
            altre = lettere[lunga]
            estranee = max(len(sue_lettere - altre), len(altre - sue_lettere))
            # Diviso due perche' uno scambio fra sequenze ('m' per 'ni')
            # puo' togliere due lettere estranee al prezzo di una. Senza
            # questa cautela il filtro scarterebbe coppie buone, ed e'
            # esattamente il tipo di errore che non lascia traccia.
            if COSTO_MINIMO * estranee / 2.0 > (1.0 - soglia) * massimo:
                continue
            if paleografia.somiglianza(corta, lunga) >= soglia:
                vicini[corta].add(lunga)
                vicini[lunga].add(corta)
    # Ordinate, perche' da qui passa l'ordine in cui i candidati vengono
    # guardati: un insieme lo deciderebbe a caso a ogni esecuzione.
    return {forma: tuple(sorted(valore)) for forma, valore in vicini.items()}


# Il piu' economico degli scambi della tabella paleografica. Serve al
# filtro qui sopra, e va tenuto allineato: se un giorno una confusione
# costasse meno, il filtro comincerebbe a scartare coppie buone.
COSTO_MINIMO = paleografia.COSTO_CONFUSIONE


def _per_famiglia(corpus, campo: str, ripiego: Counter) -> Counter:
    """Le frequenze raccolte per famiglia di grafie, quando c'e' il vocabolario."""
    vocabolario = (getattr(corpus, "vocabolari", None) or {}).get(campo)
    return vocabolario.frequenze_per_famiglia() if vocabolario else ripiego


def prova_dai_coniugi(una: Scheda, altra: Scheda, modello: "Modello") -> float:
    """Quanto provano i coniugi delle due schede, per conto loro.

    E' la domanda che i nomi da soli non sanno fare. Nicolangelo Lella ha
    una moglie sola, e l'archivio la scrive Pizzi, Moretta, Motta,
    Moutta, Desiderio e — una volta, nel 1837 — 'Ermanda Sidri'.
    Confrontare quelle stringhe non porta da nessuna parte: 'Ermanda' e
    'Emmanuela' fanno 0,56, e l'uomo si spezza lungo gli errori di
    lettura del cognome di sua moglie. Confrontare le **schede** invece
    funziona, perche' le due donne hanno la stessa eta' e lo stesso nome
    di marito scritto sulla pagina.

    Il punto delicato e' cosa **non** si concede. La versione precedente
    regalava alle mogli il peso del coniuge in comune, con la
    motivazione «se questi due uomini sono lo stesso, le mogli hanno lo
    stesso marito». E' un'ipotesi legittima da fare, ma poi il risultato
    tornava indietro ad alzare il punteggio dei mariti — cioe' la
    conclusione reggeva la propria premessa. Misurato: gli accorpamenti
    raddoppiavano (142 -> 255) a ogni soglia provata, perche' con 2,4 ban
    regalati bastava pochissimo perche' due donne qualsiasi passassero
    per la stessa, e la catena ripartiva.

    Qui il peso se lo devono **guadagnare**: si somma quello che le due
    schede di coniuge provano da sole, senza nessuna ipotesi sul marito.
    Se e' positivo, i due mariti hanno davvero la stessa moglie e la
    cosa vale a loro favore; se e' negativo, sono due mogli diverse e si
    paga il prezzo delle seconde nozze.

    Non ricorre: al secondo livello i coniugi non si guardano piu'.
    """
    if not modello.schede:
        return 0.0
    mie = [modello.schede[c] for c in una.coniugi if c in modello.schede]
    sue = [modello.schede[c] for c in altra.coniugi if c in modello.schede]
    if not mie or not sue:
        return 0.0
    migliore = 0.0
    for moglie in mie:
        for altra_moglie in sue:
            if moglie.chiave == altra_moglie.chiave:
                return PESO_CONIUGE_RICONOSCIUTO
            if not _coetanee(moglie, altra_moglie):
                continue
            prove = confronta(moglie, altra_moglie, modello, coniugi=False)
            if prove.impossibile is not None:
                continue
            migliore = max(migliore, prove.totale)
    return min(migliore, PESO_CONIUGE_RICONOSCIUTO)


def _coetanee(una: Scheda, altra: Scheda) -> bool:
    """Se due schede di coniuge possono avere la stessa eta'.

    E' il filtro che tiene in piedi tutto il resto. Senza, basta che due
    donne compaiano accanto a un uomo scritto allo stesso modo perche' il
    confronto le prenda per una — e la catena riparte: misurato, Giuseppe
    Lemme si ritrovava dieci mogli.

    Le due schede devono **tutt'e due** dichiarare un'eta'. Un coniuge di
    cui non si sa niente non e' una prova a favore: e' un vuoto, e i
    vuoti qui si pagano, perche' nell'archivio abbondano le madri
    nominate una volta sola e senza eta'.
    """
    mia, sua = una.anno_nascita, altra.anno_nascita
    if mia is None or sua is None:
        return False
    return abs(mia - sua) <= SCARTO_NASCITA_CONIUGI


def _stesso_nome_di_battesimo(une: set, altre: set) -> bool:
    """Se fra i due gruppi di coniugi c'e' lo stesso nome di battesimo.

    Le chiavi sono ``nome|cognome``. Qui si guarda solo la prima meta':
    e' la domanda «e' la stessa moglie scritta male, o e' un'altra
    moglie?», e il nome e' l'unica meta' di cui ci si possa fidare.
    """
    nomi_uno = {chiave.partition("|")[0] for chiave in une}
    nomi_altri = {chiave.partition("|")[0] for chiave in altre}
    for uno in nomi_uno:
        for altro in nomi_altri:
            if uno and altro and nomi.somiglianza_nome(uno, altro) >= NOME_CONIUGE_UGUALE:
                return True
    return False


def tasso_seconde_nozze(corpus) -> tuple[int, int]:
    """Quanti si risposano, contati sugli atti di matrimonio.

    La chiave e' lo sposo **piu' suo padre**: il nome e il cognome da
    soli non bastano, perche' due Domenico Lella che si sposano una volta
    ciascuno sembrerebbero un Domenico Lella che si sposa due volte, e
    misurerei gli omonimi invece delle seconde nozze. Con il padre
    dentro la chiave il conto scende da 1 su 15 a 1 su 234, ed e' quella
    la differenza fra contare i risposati e contare i miei errori.

    Rende ``(risposati, sposi)``. Si misura sugli **atti**, non sulle
    schede: dipendere dall'identita' per pesare l'identita' sarebbe
    circolare, e il numero uscirebbe sbagliato proprio dove il sistema
    sbaglia.
    """
    chiavi = corpus.chiavi()
    anni_per_sposo: dict = {}
    for menzione in corpus.menzioni:
        if menzione.tipo_atto != "matrimonio" or menzione.ruolo not in ("sposo", "sposa"):
            continue
        padri = tuple(sorted(chiavi.padre(menzione)))
        if not padri or not menzione.nome or not menzione.cognome:
            continue
        chiave = (
            paleografia.normalizza(menzione.nome),
            paleografia.normalizza(menzione.cognome),
            menzione.ruolo, padri,
        )
        anni_per_sposo.setdefault(chiave, set()).add(menzione.anno)

    risposati = 0
    for anni in anni_per_sposo.values():
        distinti, ultimo = 0, None
        for anno in sorted(a for a in anni if a is not None):
            # Le pubblicazioni ripetono lo stesso matrimonio a distanza di
            # settimane: due anni consecutivi restano un matrimonio solo.
            if ultimo is None or anno - ultimo > 1:
                distinti += 1
                ultimo = anno
        if distinti > 1:
            risposati += 1
    return risposati, len(anni_per_sposo)


def peso_seconde_nozze(corpus) -> float:
    """Il costo di un coniuge in piu', in ban, misurato sul corpus.

    E' un rapporto di verosimiglianza come tutti gli altri: se le due
    schede sono la stessa persona serve che si sia risposata, e quello
    e' raro; se sono due persone, avere un coniuge per uno e' la norma.
    """
    risposati, sposi = tasso_seconde_nozze(corpus)
    if sposi < MINIMI_PER_MISURARE or risposati < 1:
        return PESO_CONIUGE_DISCORDE
    tasso = risposati / sposi
    # Un tetto: nessun indizio contrario da solo puo' valere piu' di un
    # veto, e un archivio senza nemmeno un risposato non deve produrre
    # un peso infinito.
    return max(TETTO_SECONDE_NOZZE, math.log10(tasso))


def stima_persone(corpus) -> int:
    """Quante persone diverse ci sono, all'ingrosso, in questo archivio.

    Serve all'a priori, e all'a priori basta l'ordine di grandezza. La
    stima sono le combinazioni distinte **nome, cognome, decennio di
    nascita**: sotto conta come una sola due persone che si chiamano
    uguale e sono nate nello stesso decennio, sopra conta come due chi e'
    stato letto in due modi, e i due errori tirano in direzioni opposte.

    Il decennio non e' un di piu'. Senza, la stima sono le sole
    combinazioni nome-cognome, e in un paese dove il primogenito porta il
    nome del nonno quelle contano cinque Domenico Pelliccia come uno.
    Misurate a Torrebruna: 12.800 senza il decennio, 18.062 con, contro
    le 16.700 che la ricostruzione trova davvero.

    L'alternativa sarebbe una costante, e una costante andrebbe rifatta a
    mano per ogni comune: su un archivio di quattrocento menzioni due
    omonimi sono molto piu' probabilmente la stessa persona che su uno di
    cinquantamila, e una soglia che non lo sapesse funzionerebbe in un
    posto solo.
    """
    distinte = {
        (
            m.chiave_nome, m.chiave_cognome,
            ((m.anno_nascita if m.anno_nascita is not None else m.nascita_certa) or 0)
            // 10,
        )
        for m in corpus.menzioni
    }
    return len(distinte) or 1


def _vicini(deposito, nome: str, frequenze, soglia: float) -> dict:
    """Il vicinato, dalla cache se c'e'.

    La chiave comprende le forme **e** la soglia **e** la tabella delle
    confusioni: se cambia una qualunque delle tre il vicinato non e' piu'
    quello, e una cache che non se ne accorgesse sarebbe peggio di
    nessuna cache.
    """
    forme = set(frequenze)
    if deposito is None:
        return vicinato(forme, soglia)
    from history_maker.ricostruzione import cache

    chiave = cache.impronta(
        sorted(forme), soglia,
        paleografia.CONFUSIONI_SEMPLICI, paleografia.CONFUSIONI_MULTIPLE,
        paleografia.CONFUSIONI_MAIUSCOLE, paleografia.COSTO_CONFUSIONE,
    )
    return deposito.ottieni(nome, chiave, lambda: vicinato(forme, soglia))


@dataclass
class Modello:
    """Le frequenze del corpus, tradotte in pesi.

    Si costruisce una volta sull'archivio intero. Tutto quello che sa e'
    quanto e' comune ogni forma in **questo** paese: e' l'unica cosa che
    serve, ed e' l'unica che il sistema precedente non guardava.
    """

    totale: int
    cognomi: Counter
    nomi_interi: Counter
    parti_nome: Counter
    professioni: Counter
    contrade: Counter
    chiavi_genitore: Counter = field(default_factory=Counter)
    chiavi_coniuge: Counter = field(default_factory=Counter)
    # Quanto sono probabili due menzioni della stessa persona prima di
    # guardare qualunque indizio, in ban. Si ricava dal corpus: vedi
    # 'stima_persone'.
    a_priori: float = mod_dati.A_PRIORI
    # Il costo di un coniuge in piu', in ban. Vedi 'tasso_seconde_nozze'.
    seconde_nozze: float = PESO_CONIUGE_DISCORDE

    @property
    def tetto_deboli(self) -> float:
        """Fin dove possono arrivare, da soli, nome, cognome ed eta'."""
        return QUOTA_INDIZI_DEBOLI * abs(self.a_priori)
    # I vicinati: per ogni forma, quelle abbastanza simili da poter essere
    # la stessa scritta da un'altra mano. Servono a due cose — generare i
    # candidati e pesare le varianti — e costano troppo per rifarli.
    vicini_cognome: dict = field(default_factory=dict)
    vicini_nome: dict = field(default_factory=dict)
    # Le schede del giro in corso, per chiave. Servono a una domanda
    # sola — «questi due coniugi sono la stessa persona?» — e cambiano a
    # ogni giro, quindi le aggiorna la riconciliazione. Fuori dalla
    # risoluzione restano vuote e il confronto ripiega sui nomi.
    schede: dict = field(default_factory=dict)

    @classmethod
    def dal_corpus(cls, corpus, deposito=None) -> "Modello":
        """Costruisce il modello, riusando i vicinati gia' calcolati.

        Il ``deposito`` non e' un'ottimizzazione di comodo: senza, ogni
        esecuzione paga sei minuti di distanze pesate prima di guardare
        una menzione, e una fase che costa sei minuti a vuoto e' una fase
        che non si rilancia — che e' il modo migliore per non accorgersi
        mai di un errore.
        """
        chiavi = corpus.chiavi()
        genitore: Counter = Counter()
        coniuge: Counter = Counter()
        for menzione in corpus.menzioni:
            for chiave in chiavi.padre(menzione):
                genitore[chiave] += 1
            for chiave in chiavi.madre(menzione):
                genitore[chiave] += 1
            for chiave in chiavi.coniuge(menzione):
                coniuge[chiave] += 1

        persone = stima_persone(corpus)
        return cls(
            seconde_nozze=peso_seconde_nozze(corpus),
            a_priori=-math.log10(max(2, persone)),
            totale=max(1, corpus.totale),
            cognomi=corpus.frequenze_cognome,
            nomi_interi=corpus.frequenze_nome,
            parti_nome=corpus.frequenze_parte_nome,
            # Le frequenze sono quelle della **famiglia** di grafie, non
            # della singola forma: la rarita' di 'agrimensore' e' quella
            # di tutte le sue letture messe insieme, e contare solo la
            # capofila lo farebbe sembrare piu' raro di quanto e'.
            professioni=_per_famiglia(corpus, "professione", corpus.frequenze_professione),
            contrade=_per_famiglia(corpus, "contrada", corpus.frequenze_contrada),
            chiavi_genitore=genitore,
            chiavi_coniuge=coniuge,
            vicini_cognome=_vicini(
                deposito, "vicini-cognome", corpus.frequenze_cognome, VICINO_COGNOME
            ),
            vicini_nome=_vicini(
                deposito, "vicini-nome", corpus.frequenze_parte_nome, VICINO_NOME
            ),
        )

    # -----------------------------------------------------------------
    def peso_token(
        self, chiave: str, frequenze: Counter, m: float, tetto: float
    ) -> float:
        """Il peso di una forma condivisa, tanto piu' alto quanto e' rara.

        La frequenza si prende sul corpus intero. Un token mai visto vale
        come visto una volta: e' il minimo che si possa dire di qualcosa
        che esiste, ed evita di dividere per zero credendo di aver trovato
        una prova infinita.
        """
        quante = max(1, frequenze.get(chiave, 0))
        return min(tetto, math.log10(m * self.totale / quante))

    def massa_vicina(self, chiave: str, frequenze: Counter, vicini: dict) -> int:
        """Quante menzioni portano una forma confondibile con questa.

        E' il denominatore giusto per pesare una **variante**: 'Lelli'
        accanto a 'Lella' non e' raro quanto 'Lelli' da solo, perche' chi
        cerca una variante ha davanti tutto il vicinato.
        """
        return sum(frequenze.get(v, 0) for v in vicini.get(chiave, (chiave,))) or 1

    # -----------------------------------------------------------------
    def cognome(self, una: Scheda, altra: Scheda) -> tuple[float, str]:
        """Il peso del confronto fra i cognomi delle due schede.

        Si confrontano le forme **principali**, non tutte: una scheda con
        venti menzioni raccoglie qualche grafia isolata, e se ogni grafia
        isolata potesse provare un'identita' quella scheda somiglierebbe a
        chiunque. Le forme minori restano negli scaffali dei candidati —
        chi porta quella grafia viene comunque confrontato — ma non
        bastano da sole a dimostrare niente.
        """
        if not una.chiavi_cognome or not altra.chiavi_cognome:
            return 0.0, ""

        # Le letture alternative che la trascrizione propone da sola
        # contano come forme della scheda: chi ha scritto 'Chiehi [o
        # Chichi]' aveva l'immagine davanti.
        mie = principali(una.conteggi_cognome) | una.alternative_cognome
        sue = principali(altra.conteggi_cognome) | altra.alternative_cognome

        comuni = mie & sue
        if comuni:
            chiave = min(comuni, key=lambda c: (-self.cognomi.get(c, 0), c))
            peso = self.peso_token(chiave, self.cognomi, M_COGNOME_UGUALE, TETTO_COGNOME)
            return peso, f"cognome {chiave}"

        migliore, coppia = 0.0, ("", "")
        for mia in sorted(mie):
            for sua in sorted(sue):
                somiglianza = paleografia.somiglianza(mia, sua)
                if somiglianza > migliore:
                    migliore, coppia = somiglianza, (mia, sua)

        if migliore >= VICINO_COGNOME:
            massa = self.massa_vicina(coppia[0], self.cognomi, self.vicini_cognome)
            peso = min(
                TETTO_COGNOME,
                math.log10(M_COGNOME_VARIANTE * self.totale / massa),
            )
            return peso, f"cognome simile {coppia[0]}/{coppia[1]} ({migliore:.2f})"

        # Cognomi estranei. Non e' un veto — una trascrizione puo'
        # produrre un cognome del tutto diverso, e negli atti succede —
        # ma e' l'indizio contrario piu' pesante che questo modulo abbia
        # fuori dai veti.
        peso = math.log10(max(M_COGNOME_ESTRANEO, 0.001) * (0.4 + migliore))
        return peso, f"cognomi diversi {coppia[0]}/{coppia[1]} ({migliore:.2f})"

    def nome(self, una: Scheda, altra: Scheda) -> tuple[float, str]:
        """Il peso del confronto fra i nomi di battesimo.

        Vale la stessa regola delle forme principali spiegata in
        :meth:`cognome`, e qui e' costata anche di piu': due sorelle,
        'Maria Nicola' e 'Lucia Antonia', sono finite nella stessa scheda
        perche' fra le ventidue letture della prima ce n'era una che
        diceva 'Lucia Antonia'.
        """
        if not una.nomi or not altra.nomi:
            return 0.0, ""

        mie = principali(una.conteggi_nome)
        sue = principali(altra.conteggi_nome)
        comuni = mie & sue
        if comuni:
            chiave = min(comuni, key=lambda c: (-self.nomi_interi.get(c, 0), c))
            return (
                self.peso_token(chiave, self.nomi_interi, M_NOME_UGUALE, TETTO_NOME),
                f"nome {chiave}",
            )

        # Il confronto per pezzi ('Angela' contro 'Angela Maria') si fa
        # sulle forme leggibili, che gli spazi ce li hanno ancora.
        leggibili_mie = {n for n in una.nomi if paleografia.forma_canonica(n) in mie}
        leggibili_sue = {n for n in altra.nomi if paleografia.forma_canonica(n) in sue}
        migliore, coppia = 0.0, ("", "")
        for mio in sorted(leggibili_mie):
            for suo in sorted(leggibili_sue):
                somiglianza = nomi.somiglianza_nome(mio, suo)
                if somiglianza > migliore:
                    migliore, coppia = somiglianza, (mio, suo)
        # Anche qui le letture alternative sono forme buone.
        for mio in sorted(una.alternative_nome | mie):
            for suo in sorted(altra.alternative_nome | sue):
                somiglianza = paleografia.somiglianza(mio, suo)
                if somiglianza > migliore:
                    migliore, coppia = somiglianza, (mio, suo)

        if migliore >= 0.995:
            chiave = paleografia.forma_canonica(coppia[0])
            return (
                self.peso_token(chiave, self.nomi_interi, M_NOME_UGUALE, TETTO_NOME),
                f"nome {coppia[0]}",
            )
        if migliore >= VICINO_NOME:
            chiave = paleografia.forma_canonica(coppia[0])
            massa = self.massa_vicina(chiave, self.parti_nome, self.vicini_nome)
            peso = min(TETTO_NOME, math.log10(M_NOME_VARIANTE * self.totale / massa))
            return peso, f"nome simile {coppia[0]}/{coppia[1]} ({migliore:.2f})"

        peso = math.log10(max(M_NOME_ESTRANEO, 0.001) * (0.4 + migliore))
        return peso, f"nomi diversi {coppia[0]}/{coppia[1]} ({migliore:.2f})"

    # -----------------------------------------------------------------
    def insieme(
        self, mie: set, sue: set, frequenze: Counter, m: float, tetto: float,
        etichetta: str,
    ) -> tuple[float, str]:
        """Il peso di una chiave di parentela in comune, o della sua assenza.

        Rende zero quando una delle due parti non sa niente: **il silenzio
        non e' una prova contraria**. E' la differenza fra 'il padre e' un
        altro' e 'il padre non e' scritto', e confonderle spezza in due
        tutte le persone che compaiono in atti poveri.
        """
        if not mie or not sue:
            return 0.0, ""
        comuni = mie & sue
        if comuni:
            chiave = min(comuni, key=lambda c: (-frequenze.get(c, 0), c))
            return (
                self.peso_token(chiave, frequenze, m, tetto),
                f"{etichetta} {chiave.replace('|', ' ')}",
            )
        # Nessuna chiave uguale: puo' essere una grafia diversa o due
        # famiglie diverse, e la differenza la fa la somiglianza.
        migliore, coppia = 0.0, ("", "")
        for mia in sorted(mie):
            for sua in sorted(sue):
                if lettura_atti.chiavi_vicine(mia, sua):
                    return (
                        self.peso_token(mia, frequenze, m * 0.5, tetto),
                        f"{etichetta} simile {mia.replace('|', ' ')}",
                    )
                somiglianza = _somiglianza_chiave(mia, sua)
                if somiglianza > migliore:
                    migliore, coppia = somiglianza, (mia, sua)
        return -1.0, (
            f"{etichetta} diverso {coppia[0].replace('|', ' ')} / "
            f"{coppia[1].replace('|', ' ')}"
        )


def _somiglianza_chiave(una: str, altra: str) -> float:
    nome_uno, _, cognome_uno = una.partition("|")
    nome_altro, _, cognome_altro = altra.partition("|")
    return min(
        nomi.somiglianza_nome(nome_uno, nome_altro),
        paleografia.somiglianza(cognome_uno, cognome_altro),
    )


# ---------------------------------------------------------------------------
# I veti
# ---------------------------------------------------------------------------

def _morta_prima(morte: int | None, parti, sesso: str | None) -> bool:
    """Se un parto cade dopo la morte del genitore."""
    if morte is None or not parti:
        margine = 0
    else:
        margine = POSTUMO_PADRE if sesso == "M" else 0
    return morte is not None and any(anno > morte + margine for anno in parti)


def _fertilita_plausibile(parti, sesso: str | None) -> bool:
    if len(parti) < 2:
        return True
    return max(parti) - min(parti) <= ARCO_FERTILE.get(sesso or "M", 60)


def veti(una: Scheda, altra: Scheda) -> str | None:
    """Le sole contraddizioni che nessuna evidenza puo' superare.

    Sono poche apposta. Ogni veto in piu' e' una famiglia che nessun
    indizio potra' mai rimettere insieme, e l'esperienza del sistema
    precedente e' che i veti costano piu' di quanto rendano: dei suoi
    otto, cinque producevano frammentazione e nessuno impediva un solo
    errore che il punteggio non avrebbe evitato da solo.
    """
    # -- il sesso, ma solo quando viene dal ruolo -------------------------
    if (
        una.sesso_certo and altra.sesso_certo
        and una.sesso and altra.sesso and una.sesso != altra.sesso
    ):
        return f"sesso opposto dichiarato dal ruolo ({una.sesso} / {altra.sesso})"

    # -- due atti di nascita ----------------------------------------------
    # Il confronto e' su TUTTI gli anni delle due schede messi insieme,
    # non sul primo di ciascuna: se una scheda ne porta gia' due, il
    # problema non e' con l'altra, e' dentro di lei — e va visto.
    nascite = una.nascite_certe + altra.nascite_certe
    if nascite and max(nascite) - min(nascite) > TOLLERANZA_NASCITA_CERTA:
        return f"due atti di nascita distinti ({min(nascite)} e {max(nascite)})"

    # -- due atti di morte -------------------------------------------------
    morti = set(una.morti) | set(altra.morti)
    if len(morti) > 1:
        return f"due atti di morte distinti ({min(morti)} e {max(morti)})"

    # -- vivi dopo il proprio funerale ------------------------------------
    for morta, viva in ((una, altra), (altra, una)):
        if morta.morte is not None and viva.anni_presente:
            if max(viva.anni_presente) > morta.morte:
                return (
                    f"compare viva nel {max(viva.anni_presente)} ma l'atto di morte "
                    f"e' del {morta.morte}"
                )

    # -- prima di nascere, o a un'eta' che il ruolo non consente -----------
    #
    # E' il veto che previene l'errore piu' costoso di tutti: lo sposo di
    # ventinove anni del 1809 attaccato al neonato battezzato con il suo
    # nome nello stesso anno. Prevenirlo vale molto piu' che ripararlo
    # dopo — misurato, spezzare quelle schede a posteriori raddoppiava le
    # frammentazioni, perche' il coniuge si ritrovava due mogli.
    for nata, viva in ((una, altra), (altra, una)):
        if nata.nascita_certa is not None:
            if viva.anni_presente and min(viva.anni_presente) < nata.nascita_certa:
                return (
                    f"compare nel {min(viva.anni_presente)} ma nasce nel "
                    f"{nata.nascita_certa}"
                )
            # L'eta' dichiarata contro la data scritta dell'atto di nascita.
            for stimata, _ in viva.nascite_stimate:
                if abs(stimata - nata.nascita_certa) > TOLLERANZA_NASCITA_DICHIARATA:
                    return (
                        f"l'eta' dichiarata la fa nascere nel {stimata} ma il suo "
                        f"atto di nascita e' del {nata.nascita_certa}"
                    )
        # Il confronto fra i ruoli e l'anno di nascita si fa **solo sulla
        # data scritta**, non sull'eta' dichiarata. Provato anche
        # sull'eta' stimata, con la guardia di 'nascita_solida' e un
        # margine di sei anni: costava quattro volte il tempo — piu'
        # schede, quindi scaffali piu' affollati — e ne guadagnava
        # tredici cose impossibili su duecentotrenta, perdendone
        # diciannove di frammentazione. I casi che restano sono per lo
        # piu' una sola eta' letta male, e a quelli non serve un veto
        # sull'identita': serve un'occhiata alla pagina.
        if nata.nascite_certe:
            incompatibile = _ruoli_incompatibili(viva, nata.nascita_certa)
            if incompatibile:
                return incompatibile

    # -- la biologia -------------------------------------------------------
    parti = una.parti + altra.parti
    sesso = una.sesso or altra.sesso
    if not _fertilita_plausibile(parti, sesso):
        return (
            f"figli dal {min(parti)} al {max(parti)}: piu' anni di quanti se ne "
            f"possa averne"
        )
    morte = una.morte if una.morte is not None else altra.morte
    if _morta_prima(morte, parti, sesso):
        return f"un figlio nasce dopo la morte ({morte})"

    # -- una separazione gia' decisa ---------------------------------------
    # Chi ha guardato la carta ha stabilito che quelle due righe sono di
    # due persone: il calcolo non puo' rimetterle insieme al giro dopo,
    # o la decisione varrebbe una volta sola.
    if una.vietati and una.vietati & altra.ids:
        return "una decisione presa prima le tiene separate"
    if altra.vietati and altra.vietati & una.ids:
        return "una decisione presa prima le tiene separate"

    # -- due righe legate dentro lo stesso atto ---------------------------
    condivisi = una.atti & altra.atti
    if condivisi:
        legato = _legate_nello_stesso_atto(una, altra, condivisi)
        if legato:
            return legato
    return None


def _ruoli_incompatibili(
    scheda: Scheda, nascita: int, margine: int = 0
) -> str | None:
    """Un ruolo ricoperto a un'eta' che quel ruolo non consente.

    Il confronto e' contro due numeri gia' calcolati sulla scheda
    (``nascita_al_piu_tardi`` e ``nascita_al_piu_presto``) invece che
    contro tutte le menzioni: questo controllo gira un milione e mezzo di
    volte per esecuzione, e scorrere le 1.371 menzioni del sindaco a ogni
    giro l'aveva reso il collo di bottiglia dell'intera fase.
    """
    limite = scheda.nascita_al_piu_tardi
    if limite is not None and nascita > limite + margine:
        ruolo, anno = scheda.ruolo_del_limite or ("?", 0)
        return (
            f"nel {anno} e' {ruolo} ma nascerebbe nel {nascita}, cioe' a "
            f"{anno - nascita} anni"
        )
    primo = scheda.nascita_al_piu_presto
    if primo is not None and nascita < primo - margine:
        ruolo, anno = scheda.ruolo_del_limite_alto or ("?", 0)
        return (
            f"nel {anno} e' {ruolo} ma nascerebbe nel {nascita}, cioe' a "
            f"{anno - nascita} anni"
        )
    return None


def incoerenze(scheda: Scheda) -> list[str]:
    """Le contraddizioni **dentro** una scheda, se ce ne sono.

    Serve alla separazione. Una scheda incoerente non e' una persona
    strana: e' una fusione che non andava fatta, e il sistema deve poter
    tornare indietro invece di lasciarla li' con l'aria di essere a
    posto. E' l'altra meta' di :func:`veti`, applicata a una sola scheda.
    """
    guai: list[str] = []
    if scheda.nascite_certe and (
        max(scheda.nascite_certe) - min(scheda.nascite_certe) > TOLLERANZA_NASCITA_CERTA
    ):
        guai.append(
            f"due atti di nascita ({min(scheda.nascite_certe)} e "
            f"{max(scheda.nascite_certe)})"
        )
    if len(set(scheda.morti)) > 1:
        guai.append(f"due atti di morte ({sorted(set(scheda.morti))})")
    if scheda.morti and scheda.anni_presente:
        if max(scheda.anni_presente) > min(scheda.morti):
            guai.append(
                f"compare viva nel {max(scheda.anni_presente)} ma muore nel "
                f"{min(scheda.morti)}"
            )
    if scheda.nascite_certe:
        nascita = min(scheda.nascite_certe)
        if scheda.anni_presente and min(scheda.anni_presente) < nascita:
            guai.append(
                f"compare nel {min(scheda.anni_presente)} ma nasce nel {nascita}"
            )
        incompatibile = _ruoli_incompatibili(scheda, nascita)
        if incompatibile:
            guai.append(incompatibile)
        for stimata, _ in scheda.nascite_stimate:
            if abs(stimata - nascita) > TOLLERANZA_NASCITA_DICHIARATA:
                guai.append(
                    f"l'eta' dichiarata la fa nascere nel {stimata} ma il suo "
                    f"atto di nascita e' del {nascita}"
                )
                break
    if not _fertilita_plausibile(scheda.parti, scheda.sesso):
        guai.append(
            f"figli dal {min(scheda.parti)} al {max(scheda.parti)}"
        )
    if _morta_prima(scheda.morte, scheda.parti, scheda.sesso):
        guai.append(f"un figlio nasce dopo la morte ({scheda.morte})")

    # Due cose che potrebbero stare qui e **non** ci stanno, ed e' una
    # scelta misurata: due parti a meno di nove mesi, e un ruolo
    # ricoperto a un'eta' impossibile secondo la sola eta' dichiarata.
    # Sono errori certi, ma il rimedio non e' spezzare la scheda: uno dei
    # due atti e' attribuito alla persona sbagliata, ed e' quel legame a
    # doversi correggere. Spezzando la scheda le cose impossibili
    # scendevano da 230 a 72 e le frammentazioni salivano da 230 a 579,
    # perche' il coniuge si ritrovava due mogli con lo stesso nome. La
    # prevenzione sta nei veti, che quelle fusioni non le fanno; il
    # residuo resta un'anomalia.
    return guai


# Quanto largo tenere il confronto quando l'anno di nascita non viene da
# un atto ma da un'eta' dichiarata.
MARGINE_ETA_STIMATA = 6


# I ruoli che dentro un atto sono per forza due persone diverse.
COPPIE_ESCLUSIVE = (
    frozenset({"sposo", "sposa"}),
    frozenset({"padre", "madre"}),
    frozenset({"padre dello sposo", "madre dello sposo"}),
    frozenset({"padre della sposa", "madre della sposa"}),
)


def _legate_nello_stesso_atto(una: Scheda, altra: Scheda, atti: set) -> str | None:
    """Due righe dello stesso atto che l'atto stesso dichiara diverse.

    E' l'unico veto puramente logico del modulo, e vale sempre: se
    l'atto dice che questa e' la madre di quella, non possono essere la
    stessa donna, per quanto si somiglino nome, eta' e contrada.
    """
    per_atto_una: dict = {}
    for menzione in una.menzioni:
        per_atto_una.setdefault(menzione.atto, []).append(menzione)
    for menzione in altra.menzioni:
        if menzione.atto not in atti:
            continue
        for mia in per_atto_una.get(menzione.atto, ()):
            if mia.id == menzione.id:
                continue
            for genitore, figlio in ((mia, menzione), (menzione, mia)):
                if figlio.padre == genitore.id or figlio.madre == genitore.id:
                    return (
                        f"nell'atto {mia.atto} l'una e' genitore dell'altra"
                    )
                if figlio.coniuge == genitore.id:
                    return f"nell'atto {mia.atto} sono marito e moglie"
            ruoli = frozenset({mia.ruolo, menzione.ruolo})
            if len(ruoli) == 2 and any(ruoli == coppia for coppia in COPPIE_ESCLUSIVE):
                return (
                    f"nell'atto {mia.atto} sono {mia.ruolo} e {menzione.ruolo}"
                )
    return None


# ---------------------------------------------------------------------------
# Il confronto
# ---------------------------------------------------------------------------

def confronta(
    una: Scheda, altra: Scheda, modello: Modello, coniugi: bool = True
) -> Evidenza:
    """Tutte le prove sulle due schede, con il loro totale in ban.

    ``coniugi=False`` spegne il confronto fra i coniugi come schede. Lo
    usa :func:`prova_dai_coniugi` per non ricorrere all'infinito.
    """
    prove = Evidenza(a_priori=modello.a_priori)

    impossibile = veti(una, altra)
    if impossibile:
        prove.vieta(impossibile)
        return prove

    peso, dettaglio = modello.cognome(una, altra)
    prove.aggiungi("cognome", peso, dettaglio)
    peso, dettaglio = modello.nome(una, altra)
    prove.aggiungi("nome", peso, dettaglio)

    # --- l'eta' ----------------------------------------------------------
    mia, sua = una.anno_nascita, altra.anno_nascita
    if mia is not None and sua is not None:
        scarto = abs(mia - sua)
        # Fra due atti di nascita non si negozia: se sono arrivati qui e'
        # perche' distano meno di un anno, e allora e' una prova forte.
        approssimata = una.approssimata or altra.approssimata
        certa = una.nascita_certa is not None and altra.nascita_certa is not None
        peso = peso_eta(scarto, approssimata and not certa)
        prove.aggiungi("eta", peso, f"nascita {mia} / {sua} ({scarto} anni)")
    else:
        # Le finestre: quando l'eta' manca, resta cio' che dicono i
        # parenti. Vale meno ed e' comunque qualcosa.
        peso, dettaglio = _finestre(una, altra)
        prove.aggiungi("finestra", peso, dettaglio)

    # --- il sesso dedotto -------------------------------------------------
    if (
        una.sesso and altra.sesso and una.sesso != altra.sesso
        and not (una.sesso_certo and altra.sesso_certo)
    ):
        prove.aggiungi(
            "sesso", PESO_SESSO_DEDOTTO_DIVERSO,
            f"sesso dedotto diverso ({una.sesso} / {altra.sesso})",
        )

    # --- i legami come identita' ------------------------------------------
    # Vengono prima di quelli per nome perche' valgono di piu' e perche'
    # dove ci sono rendono superfluo il confronto fra nomi ricopiati.
    figli_comuni = una.figli & altra.figli
    if figli_comuni:
        prove.aggiungi(
            "figlio in comune", PESO_FIGLIO_IN_COMUNE,
            f"genitori dello stesso figlio ({len(figli_comuni)})",
        )
    coniugi_comuni = una.coniugi & altra.coniugi
    if coniugi_comuni:
        prove.aggiungi(
            "coniuge in comune", PESO_CONIUGE_IN_COMUNE, "sposati alla stessa persona",
        )
    padri_comuni = bool(una.padri & altra.padri)
    madri_comuni = bool(una.madri & altra.madri)
    if padri_comuni and madri_comuni:
        prove.aggiungi(
            "genitori in comune", PESO_GENITORI_IN_COMUNE, "stessi genitori",
        )
    elif padri_comuni or madri_comuni:
        prove.aggiungi(
            "un genitore in comune", PESO_UN_GENITORE_IN_COMUNE,
            "stesso padre" if padri_comuni else "stessa madre",
        )
    if coniugi_comuni and figli_comuni:
        prove.aggiungi("nucleo", PESO_NUCLEO_IN_COMUNE, "stesso nucleo familiare")

    # --- i legami come nomi ricopiati -------------------------------------
    if not (padri_comuni and madri_comuni):
        peso_padre, det_padre = modello.insieme(
            una.padri_nome, altra.padri_nome, modello.chiavi_genitore,
            M_CHIAVE_GENITORE, TETTO_PARENTELA, "padre",
        )
        peso_madre, det_madre = modello.insieme(
            una.madri_nome, altra.madri_nome, modello.chiavi_genitore,
            M_CHIAVE_GENITORE, TETTO_PARENTELA, "madre",
        )
        if peso_padre < 0 and peso_madre < 0:
            # Due genitori che si contraddicono tutti e due sono un'altra
            # famiglia. Resta un indizio, non un veto: basta una lettura
            # rovinata del cognome della madre per arrivare qui.
            prove.aggiungi(
                "genitori discordi", PESO_GENITORI_DISCORDI,
                f"{det_padre}; {det_madre}",
            )
        else:
            prove.aggiungi("padre", peso_padre, det_padre)
            prove.aggiungi("madre", peso_madre, det_madre)

    if not coniugi_comuni:
        peso, dettaglio = modello.insieme(
            una.coniugi_nome, altra.coniugi_nome, modello.chiavi_coniuge,
            M_CHIAVE_CONIUGE, TETTO_PARENTELA, "coniuge",
        )
        if peso < 0:
            # Due coniugi che non tornano vogliono dire che, se sono la
            # stessa persona, si e' risposata. Quanto costa lo dice il
            # corpus, non io: vedi 'tasso_seconde_nozze'.
            #
            # Ma prima una domanda che il conto da solo non fa: sono
            # davvero due mogli? Nicolangelo Lella ha la stessa moglie
            # scritta Pizzi, Moretta, Motta, Moutta, Desiderio e Sidri —
            # sei cognomi e un nome solo, Emmanuela. Far pagare le
            # seconde nozze li' vuol dire spezzare un uomo lungo gli
            # errori di lettura del cognome di sua moglie. Dentro una
            # famiglia il nome di battesimo del coniuge e' il campo
            # stabile; il cognome e' quello che la mano rovina.
            if _stesso_nome_di_battesimo(una.coniugi_nome, altra.coniugi_nome):
                peso = PESO_CONIUGE_COGNOME_DISCORDE
                dettaglio = f"{dettaglio}: stesso nome, cognome diverso"
            elif coniugi and (guadagnato := prova_dai_coniugi(una, altra, modello)) > 0:
                # I nomi discordano ma le schede dei due coniugi no: e'
                # una moglie sola, letta male. Vale quanto quelle due
                # schede provano da sole — vedi 'prova_dai_coniugi'.
                peso = guadagnato
                dettaglio = f"{dettaglio}: ma i due coniugi sono la stessa persona"
            else:
                peso = modello.seconde_nozze
                dettaglio = f"{dettaglio}: servirebbero seconde nozze"
        prove.aggiungi("coniuge", peso, dettaglio)

    # --- il patronimico ---------------------------------------------------
    if una.patronimici and altra.patronimici:
        comuni = una.patronimici & altra.patronimici
        if comuni:
            chiave = min(comuni)
            prove.aggiungi(
                "patronimico",
                modello.peso_token(
                    chiave, modello.parti_nome, M_PATRONIMICO, TETTO_PARENTELA
                ),
                f"figlio di {chiave}",
            )
        else:
            # Il patronimico e' scritto proprio per distinguere due
            # omonimi: quando c'e' e discorda, dice qualcosa.
            prove.aggiungi(
                "patronimico", -1.0,
                f"patronimici diversi ({min(una.patronimici)} / {min(altra.patronimici)})",
            )

    # --- il mestiere e la contrada ----------------------------------------
    prove.aggiungi(*_campo(
        una.professioni, altra.professioni, modello, modello.professioni,
        M_PROFESSIONE, TETTO_MESTIERE, PESO_MESTIERE_DIVERSO, "mestiere",
    ))
    prove.aggiungi(*_campo(
        una.contrade, altra.contrade, modello, modello.contrade,
        M_CONTRADA, TETTO_CONTRADA, PESO_CONTRADA_DIVERSA, "contrada",
    ))

    # --- l'ufficio ---------------------------------------------------------
    # Il sindaco firma 1.371 atti e non ha mai i genitori scritti: senza
    # questo resterebbe frantumato in centinaia di omonimi.
    comuni = una.uffici & altra.uffici
    if comuni:
        chiave = min(comuni)
        prove.aggiungi(
            "ufficio",
            modello.peso_token(chiave, modello.professioni, 0.9, 2.0),
            f"stesso ufficio ({chiave})",
        )

    _tetto_agli_indizi_deboli(prove, modello.tetto_deboli)
    return prove


def _tetto_agli_indizi_deboli(prove: Evidenza, tetto: float | None = None) -> None:
    """Impedisce che nome, cognome ed eta' bastino da soli a unire.

    La correzione entra come un indizio a se', con il suo nome, invece di
    sparire dentro il totale: chi legge il perche' di una fusione deve
    poter vedere anche cio' che il sistema ha deciso di **non** contare.
    """
    if tetto is None:
        tetto = QUOTA_INDIZI_DEBOLI * abs(prove.a_priori or mod_dati.A_PRIORI)
    deboli = sum(i.peso for i in prove.indizi if i.tipo in INDIZI_DEBOLI and i.peso > 0)
    if deboli > tetto:
        prove.aggiungi(
            "tetto indizi deboli", tetto - deboli,
            f"nome ed eta' da soli non bastano: {deboli:.2f} ridotti a {tetto:.2f}",
        )


def _campo(
    mie: Counter, sue: Counter, modello: Modello, frequenze: Counter,
    m: float, tetto: float, penalita: float, etichetta: str,
) -> tuple[str, float, str]:
    """Mestiere e contrada: uguali sono un indizio, diversi quasi niente.

    Diversi vale quasi niente **per scelta**: una persona cambia mestiere
    e cambia casa, e il sistema precedente non lo prevedeva. Uguali invece
    vale quanto e' raro: due 'contadino' non dicono niente in un paese di
    contadini, due 'agrimensore' dicono molto.
    """
    if not mie or not sue:
        return etichetta, 0.0, ""
    comuni = set(mie) & set(sue)
    if comuni:
        chiave = min(comuni, key=lambda c: (-frequenze.get(c, 0), c))
        return (
            etichetta,
            modello.peso_token(chiave, frequenze, m, tetto),
            f"{etichetta} {chiave}",
        )
    return etichetta, penalita, f"{etichetta} diverso"


def _finestre(una: Scheda, altra: Scheda) -> tuple[float, str]:
    """Quando l'eta' non c'e', restano gli anni in cui puo' essere nata.

    Due finestre che non si toccano sono un indizio contrario forte ma
    non un veto: la finestra si ricava dall'eta' di un parente, che e'
    dichiarata a voce come tutte le altre.
    """
    if una.finestra is None or altra.finestra is None:
        return 0.0, ""
    basso = max(una.finestra[0], altra.finestra[0])
    alto = min(una.finestra[1], altra.finestra[1])
    if basso <= alto:
        return 0.15, "finestre di nascita compatibili"
    distanza = basso - alto
    return max(-2.0, -0.4 - 0.08 * distanza), (
        f"finestre di nascita lontane {distanza} anni"
    )
