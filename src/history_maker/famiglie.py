"""La ricostituzione delle famiglie: il nucleo prima della persona.

Tutto :mod:`identita` riconosce **persone**, una alla volta, e usa la
parentela come freno: due schede si uniscono se nulla lo vieta, e i
genitori dichiarati che discordano sono uno dei veti. E' un ordine di
operazioni che in un archivio di paese non regge, e si vede nei numeri:
su un secolo di Torrebruna produce quasi settecento persone spezzate in
due e quasi quattrocento cose impossibili, e le seconde discendono in
gran parte dalle prime.

La ragione e' che la domanda e' mal posta. "Questo Domenico Pelliccia e'
quello dell'atto di prima?" non ha risposta: nel secolo ce ne sono
cinque, e per prudenza restano cinque. La domanda a cui i registri
sanno rispondere e' un'altra — **"questa coppia e' quella di prima?"** —
perche' due nomi insieme sono un'impronta incomparabilmente piu' rara di
un nome solo. Domenico Pelliccia sono cinque uomini; Domenico Pelliccia
con Anna Colella e' una famiglia sola.

E' il metodo con cui gli storici della popolazione fanno questo lavoro
da settant'anni — la *ricostituzione delle famiglie* — e questo modulo lo
applica dove serve: non per rifare il riconoscimento da capo, ma per
portargli la prova che gli manca proprio nel punto in cui si ferma.

Le due prove che porta
----------------------

**Un figlio ha una madre sola.** :func:`identita._unifica_genitori` lo
sa gia', ma per unire due madri dello stesso bambino pretende che i
cognomi si somiglino — e quando la seconda lettura e' rovinata sul serio
non si somigliano affatto. Maria Moretta, nata nel 1809, ha per madre
'Tecla Monaco' sul proprio atto di nascita e 'Maruca Decla' sul proprio
atto di matrimonio del 1832: e' la stessa donna, e non c'e' nessuna
somiglianza da misurare. Qui la prova che sostituisce il cognome e' il
**co-genitore**: se anche il padre di quella bambina e' un uomo solo in
tutti e due gli atti, allora le due letture parlano della stessa coppia,
e le due madri sono una. Tre concordanze strutturali — stesso figlio,
stesso ruolo, stesso coniuge — valgono piu' di un cognome illeggibile.

**Due nuclei con la stessa coppia sono un nucleo.** Quando lo
sdoppiamento e' avvenuto senza che nessun figlio stia in tutti e due, il
figlio in comune non c'e' e la prima prova non si applica. Restano i due
nomi della coppia, che presi insieme bastano: due nuclei i cui padri si
somigliano *e* le cui madri si somigliano sono la stessa famiglia, e i
fratelli che stanno di qua e di la' tornano fratelli.

Cosa questo modulo non fa
-------------------------

Non unisce mai contro un impossibile. Ogni fusione passa per le stesse
guardie di :mod:`identita` — sesso, date, arco fertile, nessuno vivo dopo
morto — con una sola eccezione dichiarata: **il veto sui genitori
dichiarati non si applica**. E' deliberato ed e' il cuore della
faccenda. Quel veto esiste per non attaccare un ramo alla famiglia
sbagliata, e nel riconoscimento normale e' giusto; ma qui la prova in
gioco e' piu' forte di lui, e i genitori discordi sono spesso
**l'errore stesso** che si sta correggendo — i genitori nominati in un
atto di matrimonio sono una seconda lettura, peggiore, di gente che
l'archivio conosce gia' meglio da altre pagine.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from history_maker import identita, paleografia, qualita
from history_maker.identita import ChiaviFamiliari, Persona

logger = logging.getLogger(__name__)

# Quanti giri di ricostituzione al massimo. Ogni giro puo' rendere
# possibile il successivo — fondere due madri unisce due nuclei, e due
# nuclei uniti possono rivelare che anche i due padri sono uno — quindi
# si ripete finche' non si assesta.
GIRI_MASSIMI = 20

# Quanto devono somigliarsi i due membri di due coppie perche' le coppie
# siano la stessa.
#
# E' piu' bassa della soglia con cui si dichiarano uguali due persone
# qualunque (0,80 sul cognome), e non e' una concessione: qui la
# condizione va soddisfatta **due volte**, sul padre e sulla madre, e due
# somiglianze indipendenti sono una prova molto piu' forte di una sola.
# Alzarla a 0,80 lascerebbe fuori varianti che nell'archivio sono la
# stessa famiglia scritta da due scrivani — 'Pacilli' e 'Peilli' stanno a
# 0,78, 'Lella' e 'Lelli' a 0,75 — e sono esattamente i casi per cui
# questo modulo esiste.
#
# Il numero e' scelto misurando, non a naso: sotto questa soglia le cose
# impossibili contate dalla fase 7 ricominciano a salire, ed e' quello il
# segnale che si e' passato il segno.
SOGLIA_COPPIA = 0.75


# Quante volte un cognome deve comparire nel secolo per essere una
# famiglia invece che una lettura sbagliata. Sopra questo numero due
# cognomi diversi restano due cognomi, e nessuna mezza prova li unisce.
COGNOME_RARO = 3

# E di quante volte il piu' raro dev'essere piu' raro dell'altro.
#
# Il numero assoluto da solo non basta, e il motivo e' istruttivo: il
# primo consolidamento gira sulle frequenze del corpus che ha davanti, e
# su pochi atti *tutti* i cognomi sono rari. Il rapporto invece regge a
# qualunque scala — 'Lallo' contro 'Lella' e' 2 contro 1.645, ottocento
# volte; 'Colella' contro 'Chielli' e' 2.295 contro 1.271, meno del
# doppio, e sono due famiglie del paese che non vanno unite mai.
DOMINANZA_COGNOME = 20


def ricostituisci(
    persone: list[Persona],
    chiavi: ChiaviFamiliari,
    frequenze: Counter[str] | None = None,
) -> int:
    """Rifa' i nuclei familiari e fonde cio' che risulta essere lo stesso.

    Torna quante fusioni ha fatto. Si chiama dopo il consolidamento di
    :mod:`identita`, quando le persone hanno gia' raccolto tutto quello
    che il riconoscimento normale sapeva dare: e' la fase in cui si
    guarda il risultato come una popolazione di famiglie invece che come
    un elenco di individui.
    """
    fatte = 0
    for giro in range(GIRI_MASSIMI):
        quante = genitori_dello_stesso_figlio(persone, chiavi)
        quante += nuclei_gemelli(persone, chiavi, frequenze)
        quante += coniugi_dello_stesso_coniuge(persone, chiavi)
        if not quante:
            logger.debug("ricostituzione chiusa in %d giri", giro + 1)
            for motivo, volte in SCARTI.most_common():
                logger.debug("  non unite, %s: %d", motivo, volte)
            break
        fatte += quante
    else:
        logger.warning(
            "la ricostituzione non si e' fermata in %d giri", GIRI_MASSIMI
        )
    return fatte


# ---------------------------------------------------------------------------
# Prima prova: un figlio ha una madre sola
# ---------------------------------------------------------------------------

def genitori_dello_stesso_figlio(
    persone: list[Persona], chiavi: ChiaviFamiliari
) -> int:
    """Fonde due schede che fanno da genitore, nello stesso ruolo, allo
    stesso figlio — quando anche l'altro genitore e' uno solo.

    E' :func:`identita._unifica_genitori` con la prova del co-genitore al
    posto della somiglianza fra i cognomi. Il cognome resta la strada
    principale e la percorre gia' l'altro; questa e' la strada per i casi
    in cui il cognome non e' leggibile, che sono esattamente quelli in
    cui l'altro si ferma.
    """
    per_menzione = _per_menzione(persone)
    fatte = 0

    for figlio in persone:
        if not figlio.menzioni:
            continue
        genitori = {
            ruolo: _genitori_dichiarati(figlio, ruolo, per_menzione)
            for ruolo in ("padre", "madre")
        }
        for ruolo, elenco in genitori.items():
            if len(elenco) < 2:
                continue
            # Il co-genitore vale come prova solo se **e' uno solo**: due
            # madri e due padri per lo stesso bambino non dicono che le
            # madri siano una, dicono che c'e' da rifare tutto il nucleo,
            # e di quello si occupa la seconda prova.
            altro = "madre" if ruolo == "padre" else "padre"
            co_genitore = len(genitori[altro]) == 1

            primo = elenco[0]
            for successivo in elenco[1:]:
                if not primo.menzioni or not successivo.menzioni:
                    continue
                if _stesso_genitore(primo, successivo, co_genitore):
                    identita._fondi(primo, successivo, chiavi, incerta=True)
                    fatte += 1
    return fatte


def _genitori_dichiarati(
    figlio: Persona, ruolo: str, per_menzione: dict[int, Persona]
) -> list[Persona]:
    """Le persone distinte che gli atti dichiarano ``ruolo`` di questo figlio."""
    trovati: list[Persona] = []
    for menzione in figlio.menzioni:
        riferimento = getattr(menzione, ruolo)
        if riferimento is None:
            continue
        genitore = per_menzione.get(riferimento)
        if genitore is None or genitore is figlio:
            continue
        if not any(g is genitore for g in trovati):
            trovati.append(genitore)
    return trovati


def _stesso_genitore(
    prima: Persona, seconda: Persona, co_genitore: bool
) -> bool:
    """Se due schede che fanno da genitore allo stesso figlio sono una.

    Senza il co-genitore si pretende il cognome, come fa il
    riconoscimento normale. Con il co-genitore no: la prova sta altrove,
    ed e' piu' solida di una grafia.
    """
    if not _niente_lo_vieta(prima, seconda):
        return False
    if co_genitore:
        return True
    if not prima._cognomi_canonici or not seconda._cognomi_canonici:
        return True
    return any(
        uno == altro or paleografia.somiglianza(uno, altro) >= identita.SOGLIA_GRAFIA
        for uno in prima._cognomi_canonici
        for altro in seconda._cognomi_canonici
    )


# Quanto devono somigliarsi i cognomi di due coniugi della stessa
# persona. E' piu' bassa di tutte le altre soglie del modulo, e puo'
# esserlo perche' qui la prova non e' il cognome: e' che sono sposati
# alla stessa persona, e che i loro anni si accavallano. Serve solo a
# tenere fuori i casi in cui i due cognomi non hanno proprio niente in
# comune, cioe' le seconde nozze vere.
#
# Misurato: 'Montta' e 'Moretta' stanno a 0,67 e sono la stessa famiglia
# — 54 attestazioni contro 1.056 — ma nessuna soglia costruita sulla
# grafia poteva accorgersene senza travolgere anche cognomi estranei.
SOGLIA_CONIUGE = 0.60


def coniugi_dello_stesso_coniuge(
    persone: list[Persona], chiavi: ChiaviFamiliari
) -> int:
    """Due coniugi della stessa persona che portano lo stesso nome.

    Le seconde nozze sono frequentissime — si restava vedovi presto — ma
    non ci si risposava con un'omonima della prima moglie. Quando i nomi
    coincidono, quindi, o e' una donna sola letta due volte, o sono due
    donne diverse e allora i loro anni **non si accavallano**: la prima
    smette di figliare e poi comincia la seconda.

    E' quest'ultima la prova, ed e' migliore di qualunque soglia sulla
    grafia: due mogli i cui parti si alternano sono una donna sola; due
    mogli i cui parti si succedono in blocco sono due. Una scheda senza
    figli non contraddice niente e si lascia assorbire — di solito e'
    proprio un frammento, come il secondo 'Filippo Lella' che compariva
    accanto al primo con la stessa moglie e nient'altro.
    """
    per_menzione = _per_menzione(persone)
    coniugi: dict[int, list[Persona]] = defaultdict(list)
    for persona in persone:
        if not persona.menzioni:
            continue
        for menzione in persona.menzioni:
            if menzione.coniuge is None:
                continue
            altro = per_menzione.get(menzione.coniuge)
            if altro is None or altro is persona:
                continue
            if not any(c is persona for c in coniugi[id(altro)]):
                coniugi[id(altro)].append(persona)

    fatte = 0
    for elenco in coniugi.values():
        if len(elenco) < 2:
            continue
        primo = elenco[0]
        for successivo in elenco[1:]:
            if not primo.menzioni or not successivo.menzioni:
                continue
            if not _stesso_coniuge(primo, successivo):
                continue
            identita._fondi(primo, successivo, chiavi, incerta=True)
            fatte += 1
    return fatte


def _stesso_coniuge(prima: Persona, seconda: Persona) -> bool:
    if not _niente_lo_vieta(prima, seconda):
        return False
    quanto = _si_somigliano(prima, seconda)
    if quanto == MUTO:
        # Un coniuge senza nome non puo' reggere da solo un'unione: qui,
        # a differenza dei nuclei, non c'e' un secondo nome che confermi.
        return False
    if quanto != CONCORDE and not _cognomi_imparentati(prima, seconda):
        return False
    return _anni_intrecciati(prima, seconda)


def _cognomi_imparentati(prima: Persona, seconda: Persona) -> bool:
    """Il nome di battesimo coincide e i cognomi non sono estranei."""
    nomi_vicini = any(
        uno == altro or paleografia.somiglianza(uno, altro) >= SOGLIA_COPPIA
        for uno in prima._nomi_canonici for altro in seconda._nomi_canonici
    )
    if not nomi_vicini:
        return False
    if not prima._cognomi_canonici or not seconda._cognomi_canonici:
        return True
    return any(
        uno == altro or paleografia.somiglianza(uno, altro) >= SOGLIA_CONIUGE
        for uno in prima._cognomi_canonici for altro in seconda._cognomi_canonici
    )


def _anni_intrecciati(prima: Persona, seconda: Persona) -> bool:
    """Se i parti delle due si accavallano, invece di succedersi.

    Due mogli dello stesso uomo che partoriscono ad anni alterni sono una
    donna sola. Due che partoriscono in due blocchi separati sono la
    prima e la seconda moglie, e vanno lasciate stare — e' il caso che
    questa funzione esiste per proteggere.

    Chi non ha parti non contraddice niente: e' un frammento, e si
    lascia assorbire.
    """
    uno, altro = sorted(prima.parti), sorted(seconda.parti)
    if not uno or not altro:
        return True
    return not (uno[-1] < altro[0] or altro[-1] < uno[0])


# ---------------------------------------------------------------------------
# Seconda prova: due nuclei con la stessa coppia
# ---------------------------------------------------------------------------

def nuclei_gemelli(
    persone: list[Persona],
    chiavi: ChiaviFamiliari,
    frequenze: Counter[str] | None = None,
) -> int:
    """Fonde due nuclei familiari che sono lo stesso, con i figli divisi.

    Il confronto e' fra coppie, non fra persone, e questa e' tutta la
    differenza: perche' due nuclei siano dichiarati lo stesso devono
    somigliarsi **il padre con il padre e la madre con la madre**. Due
    condizioni indipendenti, ognuna delle quali da sola sarebbe troppo
    debole per muovere un ramo, sono insieme una prova che il
    riconoscimento per persone non puo' costruire.
    """
    nuclei = _nuclei(persone)
    if len(nuclei) < 2:
        return 0

    # Si confrontano solo i nuclei che condividono l'inizio dei due
    # cognomi. Senza, il confronto sarebbe fra tutte le coppie a due a
    # due — su qualche migliaio di nuclei sono milioni di paragoni di
    # stringhe, ed e' la fase piu' lenta di tutta la fase 6.
    per_bacino: dict[tuple[str, str], list[tuple[Persona, Persona]]] = defaultdict(list)
    for coppia in nuclei:
        per_bacino[_bacino(coppia)].append(coppia)

    fatte = 0
    for vicini in per_bacino.values():
        for indice, una in enumerate(vicini):
            if not _ancora_intera(una):
                continue
            # Prima si guarda **con quante** altre coppie questa
            # potrebbe fondersi, e solo dopo si fonde. Serve al caso del
            # nome mancante: una madre senza nome, moglie di Domenico,
            # va bene per qualunque moglie di Domenico, e se ce n'e' piu'
            # d'una non si sa quale sia. Con un candidato solo non c'e'
            # niente da sbagliare; con due, tacere e' l'unica risposta
            # onesta.
            candidate = [
                altra for altra in vicini[indice + 1:]
                if _ancora_intera(altra)
                and not (una[0] is altra[0] and una[1] is altra[1])
                and _stessa_coppia(una, altra)
            ]
            SCARTI["i due nomi non si somigliano abbastanza"] += (
                len(vicini) - indice - 1 - len(candidate)
            )
            for altra in candidate:
                if not _ancora_intera(altra):
                    continue
                if ha_dei_muti(una, altra):
                    # Mezza prova: si pretende in cambio che il candidato
                    # sia uno solo e che le due coppie stiano negli
                    # stessi anni. La seconda condizione e' quella che
                    # conta: un matrimonio del 1884 e dei figli dal 1885
                    # sono la stessa famiglia; le stesse due coppie a
                    # quarant'anni di distanza sono padre e figlio che
                    # portano gli stessi due nomi, ed e' il modo piu'
                    # facile di sbagliare in questo paese.
                    if len(candidate) > 1:
                        SCARTI["mezza prova e piu' di un candidato"] += 1
                        continue
                    if not _anni_vicini(una, altra):
                        SCARTI["mezza prova e anni lontani"] += 1
                        continue
                    if not _un_cognome_e_raro(una, altra, frequenze):
                        SCARTI["mezza prova e due cognomi attestati"] += 1
                        continue
                # Si fondono i due padri e le due madri insieme o
                # nessuno dei due: mezza fusione lascerebbe un nucleo
                # peggiore di come l'ha trovato — un bambino con un padre
                # solo e due madri.
                if una[0] is not altra[0] and not _niente_lo_vieta(una[0], altra[0]):
                    SCARTI[_perche(una[0], altra[0], "padri")] += 1
                    continue
                if una[1] is not altra[1] and not _niente_lo_vieta(una[1], altra[1]):
                    SCARTI[_perche(una[1], altra[1], "madri")] += 1
                    continue
                if una[0] is not altra[0]:
                    identita._fondi(una[0], altra[0], chiavi, incerta=True)
                    fatte += 1
                if una[1] is not altra[1]:
                    identita._fondi(una[1], altra[1], chiavi, incerta=True)
                    fatte += 1
    return fatte


# Quanti anni possono passare fra due coppie perche' siano ancora la
# stessa famiglia, quando a tenerle insieme e' solo mezza prova. Cinque:
# il tempo fra un matrimonio e i primi figli.
ANNI_DI_DISTANZA = 5


def _anni_vicini(
    una: tuple[Persona, Persona], altra: tuple[Persona, Persona]
) -> bool:
    """Se le due coppie sono attive negli stessi anni."""
    def finestra(coppia: tuple[Persona, Persona]) -> tuple[int, int] | None:
        anni = [
            menzione.anno
            for persona in coppia for menzione in persona.menzioni
        ]
        return (min(anni), max(anni)) if anni else None

    prima, seconda = finestra(una), finestra(altra)
    if prima is None or seconda is None:
        return True
    return not (
        prima[1] + ANNI_DI_DISTANZA < seconda[0]
        or seconda[1] + ANNI_DI_DISTANZA < prima[0]
    )


def _un_cognome_e_raro(
    una: tuple[Persona, Persona],
    altra: tuple[Persona, Persona],
    frequenze: Counter[str] | None,
) -> bool:
    """Se il cognome che discorda e' cosi' raro da essere un errore.

    E' la condizione che rende sicura la mezza prova. 'Lallo' compare due
    volte in settemila atti: non e' una famiglia di Torrebruna, e' una
    lettura sbagliata di 'Lella', che ne ha 1.645. Ma se tutti e due i
    cognomi sono attestati per bene, allora sono due famiglie diverse e
    il solo nome di battesimo in comune non basta piu' — di Domenico, in
    questo paese, ce n'e' uno per casa.
    """
    if frequenze is None:
        return True
    for prima, seconda in ((una[0], altra[0]), (una[1], altra[1])):
        if prima is seconda:
            continue
        if _si_somigliano(prima, seconda) != SOLO_NOME:
            continue
        # Di ciascuno si guarda la forma **piu'** attestata, che e' il suo
        # cognome vero: prendere la meno attestata farebbe passare per
        # raro chiunque abbia una grafia storpiata fra le sue, cioe'
        # proprio le schede meglio documentate.
        rarita = [
            max((frequenze.get(forma, 0) for forma in persona._cognomi), default=0)
            for persona in (prima, seconda)
        ]
        if min(rarita) > COGNOME_RARO:
            return False
        if min(rarita) * DOMINANZA_COGNOME > max(rarita):
            return False
    return True


def _nuclei(persone: list[Persona]) -> list[tuple[Persona, Persona]]:
    """Le coppie dell'archivio: quelle con figli e quelle appena sposate.

    Un nucleo si forma quando un figlio ha **tutti e due** i genitori: e'
    la coppia a essere l'impronta, e mezza coppia non lo e'.

    Ma vale anche la coppia di un atto di matrimonio, che di figli non ne
    ha ancora nessuno. Tenerla fuori costava caro: Domenico Antonio e
    Clementina Petta si sposano nel 1884 e il primo figlio arriva nel
    1885, cosi' la coppia dell'atto di matrimonio e quella degli atti di
    nascita restavano due — e con loro i due sposi, che percio' non
    avevano genitori e non si attaccavano a nessun ramo.
    """
    per_menzione = _per_menzione(persone)
    viste: dict[tuple[int, int], tuple[Persona, Persona]] = {}
    for persona in persone:
        if not persona.menzioni:
            continue
        padri = _genitori_dichiarati(persona, "padre", per_menzione)
        madri = _genitori_dichiarati(persona, "madre", per_menzione)
        if len(padri) == 1 and len(madri) == 1:
            viste.setdefault((id(padri[0]), id(madri[0])), (padri[0], madri[0]))

        # La coppia dichiarata da un atto: marito prima, moglie dopo, per
        # non fabbricare due nuclei con gli stessi due membri invertiti.
        for menzione in persona.menzioni:
            if menzione.coniuge is None:
                continue
            altro = per_menzione.get(menzione.coniuge)
            if altro is None or altro is persona:
                continue
            uomo, donna = (
                (persona, altro) if persona.sesso != "F" else (altro, persona)
            )
            viste.setdefault((id(uomo), id(donna)), (uomo, donna))
    return list(viste.values())


def _ancora_intera(coppia: tuple[Persona, Persona]) -> bool:
    """Una fusione precedente puo' aver svuotato uno dei due."""
    return bool(coppia[0].menzioni) and bool(coppia[1].menzioni)


def _bacino(coppia: tuple[Persona, Persona]) -> tuple[str, str]:
    """In quale cassetto confrontare questa coppia con le altre.

    Si guardano i **nomi di battesimo**, non i cognomi, e la ragione e'
    un errore che questo modulo ha commesso per intero: sui cognomi, chi
    il cognome non ce l'ha finisce in un cassetto suo, dove non lo
    confronta nessuno. E chi non ha il cognome e' esattamente il caso da
    risolvere — 'Domenico x Saba Minchilli', i due genitori nominati di
    sfuggita in un atto solo, che sono la stessa coppia di 'Domenico Di
    Laudo x Saba Minchilli' e restavano un nucleo a parte.

    Il nome di battesimo c'e' quasi sempre, ed e' meno discriminante di
    un cognome; ma qui i cassetti sono fatti da **due** nomi, e due nomi
    insieme bastano a tenerli piccoli.
    """
    return (_iniziale(coppia[0]), _iniziale(coppia[1]))


def _iniziale(persona: Persona) -> str:
    """Le prime lettere del nome di battesimo, per fare i bacini."""
    if not persona._nomi_canonici:
        return ""
    return min(persona._nomi_canonici)[:3]


# Le quattro risposte al confronto fra due membri di due coppie.
#
# ``MUTO`` e' la piu' importante, ed e' la stessa distinzione che
# :mod:`identita` fa sui genitori: **non sapere non e' sapere di no**.
#
# ``SOLO_NOME`` e' la seconda: il nome di battesimo coincide e il cognome
# no. Da sola non vuol dire niente — di Domenico ce n'e' cento — ma
# accanto a un membro che coincide **esatto** e' una prova buona, perche'
# a fare l'impronta e' la coppia. Domenico Antonio, sposo nel 1884, e'
# letto 'Lallo'; il padre dei sette figli di Clementina Petta dal 1885 e'
# letto 'Lella'. I due cognomi stanno a 0,42 e nessuna soglia sulla
# grafia potra' mai unirli: quello che li unisce e' che la moglie e' la
# stessa donna, con lo stesso nome e lo stesso cognome, e che l'uno
# comincia a fare figli l'anno dopo che l'altro si e' sposato.
CONCORDE, SOLO_NOME, MUTO, DISCORDE = 2, 1, 0, -1


def _stessa_coppia(
    una: tuple[Persona, Persona], altra: tuple[Persona, Persona]
) -> bool:
    """Se due nuclei sono la stessa famiglia.

    Serve almeno un membro che concordi davvero. L'altro puo' tacere —
    essere la madre che un atto nomina senza dirne il nome — ma non
    possono tacere tutti e due: due sconosciuti non fanno una coppia.
    """
    if una[0] is altra[0] and una[1] is altra[1]:
        return False
    # Un membro identico e l'altro somigliante basta: e' il caso in cui
    # una meta' della coppia era gia' stata riconosciuta.
    padri = CONCORDE if una[0] is altra[0] else _si_somigliano(una[0], altra[0])
    madri = CONCORDE if una[1] is altra[1] else _si_somigliano(una[1], altra[1])
    if padri == DISCORDE or madri == DISCORDE:
        return False
    # Serve una meta' che coincida davvero. L'altra puo' tacere o portare
    # il solo nome di battesimo, ma non possono farlo tutte e due: due
    # mezze prove non fanno una prova.
    return CONCORDE in (padri, madri)


def ha_dei_muti(una: tuple[Persona, Persona], altra: tuple[Persona, Persona]) -> bool:
    """Se l'accostamento si regge in parte su un nome incompleto.

    Vale sia per il nome che manca sia per il cognome che discorda: in
    tutti e due i casi meta' della prova non c'e', e allora si pretende
    che il candidato sia uno solo.
    """
    padri = CONCORDE if una[0] is altra[0] else _si_somigliano(una[0], altra[0])
    madri = CONCORDE if una[1] is altra[1] else _si_somigliano(una[1], altra[1])
    return MUTO in (padri, madri) or SOLO_NOME in (padri, madri)


def _si_somigliano(prima: Persona, seconda: Persona) -> int:
    """Se due schede portano lo stesso nome, scritto anche diversamente.

    Risponde ``MUTO`` quando una delle due non ha nome affatto. Succede
    di continuo: l'atto di morte di un adulto nomina "sua madre" senza
    dirne il nome, l'atto di nascita di un esposto nomina il padre e
    basta. Trattare quel silenzio come una discordanza vuol dire tenere
    per sempre separata una madre senza nome dalla stessa madre che
    l'atto accanto nomina per esteso — ed e' un caso che nell'archivio
    si vede a occhio, perche' quei due figli risultano fratellastri
    quando sono fratelli.

    Chi chiama deve pero' sapere di reggersi su un silenzio: vedi
    :func:`ha_dei_muti`, che serve a pretendere, in quel caso, che il
    candidato sia uno solo.
    """
    if not prima._nomi_canonici or not seconda._nomi_canonici:
        return MUTO

    for chiave in prima._nomi_canonici:
        for altra in seconda._nomi_canonici:
            if chiave == altra:
                break
            if paleografia.somiglianza(chiave, altra) >= SOGLIA_COPPIA:
                break
        else:
            continue
        break
    else:
        return DISCORDE

    if not prima._cognomi_canonici or not seconda._cognomi_canonici:
        return CONCORDE
    somiglia = any(
        uno == altro or paleografia.somiglianza(uno, altro) >= SOGLIA_COPPIA
        for uno in prima._cognomi_canonici
        for altro in seconda._cognomi_canonici
    )
    return CONCORDE if somiglia else SOLO_NOME


# ---------------------------------------------------------------------------
# Le guardie
# ---------------------------------------------------------------------------

# Perche' le coppie che sembravano la stessa non sono state unite. Non e'
# un dettaglio da sviluppatori: e' la lista di cosa ancora non si sa
# fare, e senza di lei ogni soglia di questo modulo sarebbe stata scelta
# a occhio. Si legge a fine fase 6 con il log di dettaglio.
SCARTI: Counter[str] = Counter()


def _perche(prima: Persona, seconda: Persona, chi: str) -> str:
    """Quale guardia ha fermato la fusione, detto in una riga.

    Ricalca :func:`_niente_lo_vieta` nello stesso ordine: se le due si
    scostano, questo elenco mente e le soglie del modulo si scelgono al
    buio.
    """
    if prima.sesso and seconda.sesso and prima.sesso != seconda.sesso:
        return f"{chi}: sesso diverso"
    if prima.nascita_certa is not None and seconda.nascita_certa is not None:
        return f"{chi}: due atti di nascita diversi"
    if prima.morte is not None and seconda.morte is not None and prima.morte != seconda.morte:
        return f"{chi}: due atti di morte diversi"
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.nascita_certa is None:
            continue
        dichiarati = sorted(
            m.anno_nascita for m in altra.menzioni if m.anno_nascita is not None
        )
        if dichiarati:
            scarto = abs(dichiarati[len(dichiarati) // 2] - una.nascita_certa)
            if scarto > qualita.SCARTO_ETA_MASSIMO:
                return f"{chi}: atto di nascita contro eta' dichiarate ({scarto} anni)"
        if any(m.presente and m.anno < una.nascita_certa for m in altra.menzioni):
            return f"{chi}: compare prima di essere nato"
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.morte is None:
            continue
        if any(m.presente and m.anno > una.morte for m in altra.menzioni):
            return f"{chi}: compare vivo dopo essere morto"
        if identita._morta_prima(una.morte, altra.parti, una.sesso or altra.sesso):
            return f"{chi}: avrebbe partorito dopo morta"
    if not _prole_possibile(prima, seconda):
        return f"{chi}: i parti non stanno in una vita fertile"
    return f"{chi}: nessun veto (fusione fatta o coppia gia' unita)"


def _niente_lo_vieta(prima: Persona, seconda: Persona) -> bool:
    """Solo i fatti documentati possono vietare, qui: non le età dichiarate.

    E' la differenza fra questo modulo e il riconoscimento normale, e
    merita di essere detta per intero perche' e' la scelta su cui tutto
    si regge.

    Il riconoscimento normale, per unire due schede, pretende che le
    **eta' dichiarate** stiano entro sei anni. E' ragionevole quando non
    si ha altro. Ma l'eta' e' il dato piu' fragile che questi registri
    contengano: la dichiara a voce un vicino di casa che e' andato in
    municipio a denunciare una morte, spesso di una persona che non
    sapeva quando fosse nata; il secolo e' pieno di uomini che hanno
    quarant'anni in un atto e cinquantacinque sei anni dopo. Misurato su
    Torrebruna: fra le coppie che portano gli stessi due nomi e che
    l'archivio tiene separate, la ragione numero uno del veto sono
    proprio le eta' lontane — venti, trenta, quarantadue anni.

    Qui l'eta' non puo' vietare, perche' c'e' una prova migliore che le
    si oppone: due nomi che coincidono tutti e due, o uno stesso figlio.
    Restano i veti che vengono da un **documento** invece che da una
    dichiarazione a memoria — due atti di nascita, due atti di morte, il
    sesso — e quelli che vengono dalla biologia, che sono controllati da
    :func:`_prole_possibile`.
    """
    if prima.sesso and seconda.sesso and prima.sesso != seconda.sesso:
        return False

    # Due atti di nascita sono due bambini: e' un fatto scritto, non una
    # memoria, e resta il veto piu' solido che ci sia.
    if prima.nascita_certa is not None and seconda.nascita_certa is not None:
        if abs(prima.nascita_certa - seconda.nascita_certa) > identita.TOLLERANZA_NASCITA_CERTA:
            return False

    if prima.morte is not None and seconda.morte is not None:
        if prima.morte != seconda.morte:
            return False

    # Un atto di nascita contro un'eta' dichiarata: vince l'atto.
    #
    # E' il correttivo che questo modulo ha dovuto imparare a sue spese.
    # Senza, dire "l'eta' non puo' vietare" diventa troppo, e comincia a
    # fondere il bambino con l'adulto che porta il suo nome: Orazio
    # Pelliccia, nato nel 1824, si prendeva le menzioni di un Orazio
    # Pelliccia che nel 1832 dichiarava trentatre anni — cioe' di suo
    # nonno, perche' in questo paese il primogenito porta il nome del
    # nonno e la trappola e' esattamente li'.
    #
    # La tolleranza non e' quella stretta del riconoscimento: e' la
    # stessa soglia oltre la quale la fase 7 dichiara un'eta'
    # incoerente. Devono essere lo stesso numero, altrimenti la
    # ricostruzione fabbrica di sua mano cio' che il controllo poi
    # segnala — ed e' successo: 113 segnalazioni nuove in un colpo.
    # Si guarda la **mediana** delle eta' dichiarate, non ognuna. Un
    # singolo numero fuori misura e' quello che questi registri fanno di
    # continuo — un vicino che tira a indovinare — e lasciargli il potere
    # di veto costa piu' di quanto renda: misurato, cinquanta famiglie
    # che tornavano una restavano due per colpa di una riga sola. Una
    # mediana lontana venticinque anni non e' piu' un'imprecisione: e'
    # un'altra persona, ed e' cosi' che si distingue il nonno dal nipote
    # che ne porta il nome.
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.nascita_certa is None:
            continue
        dichiarati = sorted(
            m.anno_nascita for m in altra.menzioni if m.anno_nascita is not None
        )
        if not dichiarati:
            continue
        mediana = dichiarati[len(dichiarati) // 2]
        if abs(mediana - una.nascita_certa) > qualita.SCARTO_ETA_MASSIMO:
            return False

    # Nessuno compare vivo prima di essere nato o dopo essere morto. Sono
    # gli stessi controlli del riconoscimento, e restano perche' non
    # dipendono da un'eta' dichiarata ma dalla data dell'atto, che e'
    # stampata sul registro.
    for una, altra in ((prima, seconda), (seconda, prima)):
        if una.nascita_certa is not None:
            if any(m.presente and m.anno < una.nascita_certa for m in altra.menzioni):
                return False
        if una.morte is not None:
            if any(m.presente and m.anno > una.morte for m in altra.menzioni):
                return False
            if identita._morta_prima(
                una.morte, altra.parti, una.sesso or altra.sesso
            ):
                return False

    return _prole_possibile(prima, seconda)


def _prole_possibile(prima: Persona, seconda: Persona) -> bool:
    """Se i figli delle due schede stanno insieme in una vita sola.

    E' il controllo che prende il posto dell'eta' dichiarata, ed e' di
    qualita' molto migliore perche' non si regge su cosa qualcuno ha
    detto di ricordare: si regge sulle date degli atti di nascita, che
    sono scritte sul registro il giorno stesso.

    Due donne che portano lo stesso nome e hanno lo stesso marito sono la
    stessa donna, a meno che i loro parti, messi in fila, non possano
    stare in una sola vita fertile: allora sono due, e quasi sempre sono
    madre e figlia — perche' la figlia ha il nome della madre e ha
    sposato un uomo che ha il nome del padre, che in un paese dove il
    primogenito porta il nome del nonno succede di continuo.
    """
    parti = sorted(prima.parti + seconda.parti)
    if not identita._fertilita_plausibile(parti, prima.sesso or seconda.sesso):
        return False

    # Due parti nello stesso anno sono gemelli o due mesi di scarto fra
    # dicembre e gennaio: possibili. Tre no.
    if parti:
        for anno in set(parti):
            if parti.count(anno) > 2:
                return False
    return True


def _per_menzione(persone: list[Persona]) -> dict[int, Persona]:
    return {
        menzione.id: persona
        for persona in persone if persona.menzioni
        for menzione in persona.menzioni
    }
