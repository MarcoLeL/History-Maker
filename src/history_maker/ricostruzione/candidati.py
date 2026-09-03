"""Chi vale la pena confrontare con chi.

La generazione dei candidati e' il **tetto** della ricostruzione: due
schede che non diventano mai candidate non si uniranno mai, per quanto sia
buono il punteggio che le confronterebbe. E' il difetto piu' silenzioso di
tutta la catena, perche' non lascia traccia — non c'e' un punteggio basso
da guardare, non c'e' un veto da rivedere: semplicemente non e' successo
niente.

Il sistema precedente ne aveva una sola, di strategia: **stesso cognome
(o quasi), stesso nome (o quasi)**. E' ottima e non basta, perche' fallisce
esattamente dove il problema e' piu' grave — quando il cognome e' letto
male. Nell'archivio di Torrebruna otto famiglie spezzate su ottanta non
diventavano nemmeno candidate.

Qui le strategie sono cinque, e ognuna copre il buco delle altre.

**Per nome e cognome.** Il cavallo da tiro: copre la grande maggioranza
dei casi e costa poco, perche' il vicinato delle forme si calcola una
volta sola.

**Per parentela.** Due schede che risultano sposate alla *stessa persona*
gia' riconosciuta, o genitori dello *stesso* bambino, sono candidate
qualunque cosa dica il cognome. Non passa per nessun nome ricopiato: e'
l'evidenza che il sistema precedente aveva imparato a usare tardi e solo
in un giro finale.

**Per coppia.** La ricostituzione delle famiglie, che gli storici della
popolazione fanno da settant'anni. 'Domenico Pelliccia' sono cinque uomini;
'Domenico Pelliccia con Anna Colella' e' una famiglia sola. Due nuclei che
dichiarano gli stessi due nomi di battesimo dei genitori sono candidati
anche quando i cognomi letti divergono del tutto — ed e' il caso delle
'coppie gemelle', la frammentazione che fa piu' danno perche' divide i
fratelli fra due famiglie.

**Per patronimico.** ``Giuseppe di Carmine`` e' l'unico caso in cui la
fonte stessa distingue due omonimi. Costa niente e ripaga.

**Per ufficio.** Il sindaco e il cancelliere firmano centinaia di atti di
seguito senza mai un genitore scritto: si trovano solo cosi'.
"""

from __future__ import annotations

from collections import defaultdict

from history_maker.ricostruzione.scheda import Scheda


def indice_nome_cognome(
    schede: dict[int, Scheda], vicini_cognome: dict, vicini_nome: dict
) -> dict[tuple[str, str], list[int]]:
    """Le schede, scaffalate per ogni coppia (cognome, pezzo di nome).

    Una scheda entra in uno scaffale una volta sola per chiave, anche se
    ha quattrocento menzioni: il sindaco firma 1.371 atti, e senza questa
    accortezza il suo scaffale sarebbe lungo 1.371 e il confronto
    diventerebbe quadratico proprio dove il corpus e' piu' fitto.
    """
    indice: dict[tuple[str, str], list[int]] = defaultdict(list)
    for chiave in sorted(schede):
        scheda = schede[chiave]
        scaffali = set()
        for cognome in sorted(scheda.chiavi_cognome | scheda.alternative_cognome) or [""]:
            for vicino in vicini_cognome.get(cognome, (cognome,)):
                for parte in sorted(scheda.parti_nome) or [""]:
                    for nome in vicini_nome.get(parte, (parte,)):
                        scaffali.add((vicino, nome))
        for scaffale in sorted(scaffali):
            indice[scaffale].append(chiave)
    return dict(indice)


def coppie_per_nome(indice: dict, tetto_scaffale: int = 400):
    """Le coppie candidate che vengono da nome e cognome.

    ``tetto_scaffale`` e' una difesa contro il caso patologico: uno
    scaffale con migliaia di schede produrrebbe milioni di confronti, e
    quando uno scaffale e' cosi' affollato il nome non sta distinguendo
    piu' niente. Quando succede, il caso non viene buttato: diventa
    un'anomalia, perche' e' un posto in cui la ricostruzione **sa** di
    non riuscire a decidere.
    """
    for scaffale in sorted(indice):
        gruppo = indice[scaffale]
        if len(gruppo) < 2 or len(gruppo) > tetto_scaffale:
            continue
        for i, una in enumerate(gruppo):
            for altra in gruppo[i + 1:]:
                yield (una, altra) if una < altra else (altra, una)


def scaffali_affollati(indice: dict, tetto_scaffale: int = 400) -> list[tuple]:
    """Gli scaffali che il blocco per nome non riesce a sfoltire."""
    return [
        (scaffale, len(gruppo))
        for scaffale, gruppo in sorted(indice.items())
        if len(gruppo) > tetto_scaffale
    ]


def coppie_per_parentela(schede: dict[int, Scheda]):
    """Chi condivide un parente **gia' riconosciuto**.

    Due madri dello stesso bambino sono una madre sola; due uomini sposati
    alla stessa donna sono un uomo solo. Nessuno dei due confronti passa
    per un nome ricopiato, ed e' per questo che valgono cosi' tanto.
    """
    for relazione in ("figli", "coniugi", "padri", "madri"):
        gruppi: dict[int, list[int]] = defaultdict(list)
        for chiave in sorted(schede):
            for altra in sorted(getattr(schede[chiave], relazione)):
                gruppi[altra].append(chiave)
        for parente in sorted(gruppi):
            gruppo = gruppi[parente]
            # Un genitore con venti figli genera venti schede 'fratelli':
            # quelle NON sono candidate fra loro (sono fratelli veri).
            # Il taglio a due e' quindi solo per efficienza sui casi
            # patologici; la selezione vera la fa il punteggio.
            if len(gruppo) < 2 or len(gruppo) > 60:
                continue
            for i, una in enumerate(gruppo):
                for altra in gruppo[i + 1:]:
                    yield (una, altra) if una < altra else (altra, una)


def coppie_per_coppia(schede: dict[int, Scheda], nuclei: dict):
    """La ricostituzione delle famiglie: due nuclei con gli stessi due nomi.

    ``nuclei`` mappa la chiave di una coppia — i due nomi di battesimo dei
    genitori in forma canonica — sulle schede che li ricoprono. Il cognome
    non entra nella chiave **apposta**: e' esattamente il campo che in
    questi casi e' letto male.
    """
    for chiave in sorted(nuclei):
        gruppo = nuclei[chiave]
        if len(gruppo) < 2 or len(gruppo) > 80:
            continue
        # I padri fra loro e le madri fra loro: mai un padre con una madre.
        for ruolo in (0, 1):
            parti = sorted({coppia[ruolo] for coppia in gruppo if coppia[ruolo]})
            for i, una in enumerate(parti):
                for altra in parti[i + 1:]:
                    yield (una, altra) if una < altra else (altra, una)


def nuclei_per_nome(schede: dict[int, Scheda], di_menzione: dict) -> dict:
    """I nuclei genitoriali, indicizzati sui soli nomi di battesimo.

    Un nucleo e' una coppia di schede che un atto di nascita dichiara
    padre e madre dello stesso bambino. La chiave sono i due nomi ridotti
    a forma canonica, in ordine fisso: la stessa coppia letta con due
    cognomi diversi finisce comunque nello stesso scaffale.
    """
    nuclei: dict[tuple, set] = defaultdict(set)
    for chiave in sorted(schede):
        scheda = schede[chiave]
        for menzione in scheda.menzioni:
            if menzione.padre is None or menzione.madre is None:
                continue
            padre = di_menzione.get(menzione.padre)
            madre = di_menzione.get(menzione.madre)
            if padre is None or madre is None or padre == madre:
                continue
            nome_padre = _prima_parte(schede.get(padre))
            nome_madre = _prima_parte(schede.get(madre))
            if not nome_padre or not nome_madre:
                continue
            nuclei[(nome_padre, nome_madre)].add((padre, madre))
    return {chiave: sorted(valore) for chiave, valore in nuclei.items()}


def _prima_parte(scheda: Scheda | None) -> str:
    """Il primo pezzo del nome, che e' quello che sopravvive alle varianti.

    'Maria Vincenza' e 'Maria' devono finire nello stesso scaffale, e il
    solo modo e' guardare il primo elemento: il secondo nome negli atti
    compare e scompare senza regola.
    """
    if scheda is None or not scheda.menzioni:
        return ""
    for menzione in scheda.menzioni:
        parti = menzione.parti_nome
        if parti:
            return parti[0]
    return ""


def coppie_per_patronimico(schede: dict[int, Scheda]):
    """Chi porta lo stesso patronimico e lo stesso nome."""
    gruppi: dict[tuple, list[int]] = defaultdict(list)
    for chiave in sorted(schede):
        scheda = schede[chiave]
        for patronimico in sorted(scheda.patronimici):
            for parte in sorted(scheda.parti_nome):
                gruppi[(patronimico, parte)].append(chiave)
    for gruppo_chiave in sorted(gruppi):
        gruppo = gruppi[gruppo_chiave]
        if len(gruppo) < 2 or len(gruppo) > 40:
            continue
        for i, una in enumerate(gruppo):
            for altra in gruppo[i + 1:]:
                yield (una, altra) if una < altra else (altra, una)


def coppie_per_ufficio(schede: dict[int, Scheda]):
    """Chi ricopre lo stesso ufficio con lo stesso nome, negli stessi anni."""
    gruppi: dict[tuple, list[int]] = defaultdict(list)
    for chiave in sorted(schede):
        scheda = schede[chiave]
        if not scheda.uffici:
            continue
        for ufficio in sorted(scheda.uffici):
            for parte in sorted(scheda.parti_nome):
                gruppi[(ufficio, parte)].append(chiave)
    for gruppo_chiave in sorted(gruppi):
        gruppo = gruppi[gruppo_chiave]
        if len(gruppo) < 2 or len(gruppo) > 60:
            continue
        for i, una in enumerate(gruppo):
            for altra in gruppo[i + 1:]:
                yield (una, altra) if una < altra else (altra, una)


def tutte(
    schede: dict[int, Scheda], di_menzione: dict, vicini_cognome: dict,
    vicini_nome: dict, tetto_scaffale: int = 400,
) -> tuple[list[tuple[int, int]], list[tuple]]:
    """Tutte le coppie candidate, senza doppioni e in ordine stabile.

    L'ordine e' deterministico per costruzione: da qui passa l'ordine in
    cui le fusioni vengono valutate, e un insieme lo deciderebbe a caso a
    ogni esecuzione. Una ricostruzione che cambia risultato fra due
    esecuzioni identiche non e' verificabile, e senza verifica un audit
    trail non serve a niente.
    """
    indice = indice_nome_cognome(schede, vicini_cognome, vicini_nome)
    nuclei = nuclei_per_nome(schede, di_menzione)

    viste: set[tuple[int, int]] = set()
    for sorgente in (
        coppie_per_nome(indice, tetto_scaffale),
        coppie_per_parentela(schede),
        coppie_per_coppia(schede, nuclei),
        coppie_per_patronimico(schede),
        coppie_per_ufficio(schede),
    ):
        for coppia in sorgente:
            if coppia[0] != coppia[1]:
                viste.add(coppia)
    return sorted(viste), scaffali_affollati(indice, tetto_scaffale)
