"""Mestieri, contrade e residenze: le varianti, la serie, il valore corrente.

Un attributo biografico non e' un campo: e' una **serie**. Domenico Lella
e' bovaro nel 1850, contadino nel 1861 e bracciante nel 1875, e le tre
cose sono vere tutte e tre. Il modello a campo singolo perde le prime due
e fa sembrare le ultime una contraddizione; il modello a fatti datati le
tiene, ma da solo non basta — restano tre stringhe in fila, e chi legge
deve ancora capire se 'bovaro' e 'bovajo' siano due mestieri o due modi
di scrivere lo stesso.

Questo modulo fa le tre cose che mancavano fra il fatto e la scheda.

**Riconduce le grafie.** ``bovaro`` e ``bovajo``, ``strada Trascinella`` e
``strada Trafficinella``. Il criterio non e' lo stesso dei cognomi, ed e'
una differenza che vale la pena spiegare: i mestieri e le contrade di un
paese sono un **insieme quasi chiuso**, mentre i cognomi no. Non esiste
il mestiere forestiero che un giorno potrebbe comparire, come esiste
invece la sposa venuta da fuori con un cognome mai visto. Qui si puo'
essere piu' generosi.

**Costruisce la serie.** Ogni valore con gli anni in cui e' attestato,
in ordine. E' cio' che permette di distinguere un cambiamento di mestiere
da un errore di lettura: il primo ha una data e sta in fondo, il secondo
compare una volta in mezzo a dieci attestazioni contrarie.

**Segnala cio' che non torna.** Un mestiere visto una volta sola in un
secolo, addosso a una persona che per il resto della vita ne dichiara un
altro, e' probabilmente una lettura da controllare. Probabilmente: puo'
anche essere l'unico agrimensore del paese, e per questo si **segnala**,
non si corregge.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from history_maker import normalizza, paleografia
from history_maker.ricostruzione import modello as mod

# Quanto devono somigliarsi due forme per essere lo stesso mestiere o la
# stessa contrada.
#
# Il numero e' alto, e la ragione e' un errore che vale la pena
# raccontare. Il primo tentativo riusava il raggruppamento dei cognomi,
# che unisce **per contatto**: se A somiglia a B e B a C, stanno tutti e
# tre insieme. Per le grafie di un cognome e' giusto — ogni scrivano si
# scosta un poco dal precedente — ma per un vocabolario di parole diverse
# e' un disastro: a 0,70 la catena arrivava da 'agrimensore' a
# 'proprietario' passando per le forme intermedie, e finiva per
# ricondurre a 'proprietario' anche 'muratore', 'calzolaio' e
# 'sanitario'. Qui quindi niente catene: ogni forma rara si confronta
# **direttamente** con le forme frequenti, e la somiglianza deve essere
# quella di due grafie della stessa parola, non di due parole affini.
SOGLIA_VOCABOLARIO = 0.86

# Sotto quante attestazioni una forma e' troppo rara per fare testo, e
# quante volte deve essere piu' frequente la capofila perche' valga la
# pena proporre la sostituzione.
#
# Quattro e non dodici: anche la forma **giusta** di un mestiere puo'
# essere rara. 'Agrimensore' compare otto volte in un secolo, e con una
# dominanza di dodici la sua lettura rovinata 'Agrimenfore' non ci
# sarebbe mai arrivata. Il freno vero non e' questo numero ma la soglia
# di somiglianza, che pretende due grafie della stessa parola.
FORMA_RARA = 3
DOMINANZA = 4


@dataclass
class Vocabolario:
    """Le forme di un campo, raccolte in famiglie di grafie.

    ``capofila`` manda ogni forma vista sulla forma che la rappresenta:
    la piu' attestata della sua famiglia. E' un'**interpretazione**, non
    una correzione: il valore letto resta nel fatto, nella colonna
    ``grezzo``, e non viene mai toccato.
    """

    capofila: dict = field(default_factory=dict)
    frequenze: Counter = field(default_factory=Counter)
    famiglie: dict = field(default_factory=dict)
    # Per ogni famiglia, la forma **leggibile** piu' attestata. Serve
    # perche' la chiave con cui si confronta non e' sempre presentabile:
    # per le contrade e' il solo nucleo del toponimo — 'palazzo' per
    # 'strada sotto il palazzo' — e mostrare quella a chi consulta
    # sarebbe peggio del valore letto.
    etichette: dict = field(default_factory=dict)
    # Come si riduce un valore letto alla chiave con cui si confronta.
    # Per i mestieri basta la normalizzazione; per le contrade no, e la
    # differenza conta: 'Guardiabruna, nella strada Sotto il Palazzo' e
    # 'Guardiabruna, nella strada Vicenne' condividono i due terzi delle
    # lettere, e senza togliere il preambolo di formula il confronto
    # dichiara che sono la stessa via.
    nucleo: bool = False

    @classmethod
    def dal_corpus(
        cls, valori, soglia: float = SOGLIA_VOCABOLARIO, nucleo: bool = False,
        dichiarate: dict | None = None,
    ) -> "Vocabolario":
        """Le forme frequenti fanno testo; le rare si appoggiano a loro.

        Nessuna catena: ogni forma rara cerca la forma **frequente** piu'
        vicina, e ci si appoggia solo se la somiglianza e' quella di due
        grafie della stessa parola. Due forme frequenti restano sempre
        distinte, per quanto si somiglino: se in un secolo si sono viste
        tutte e due molte volte, sono due cose.
        """
        # ``valori`` sono coppie (chiave con cui si confronta, forma
        # leggibile): la prima serve al confronto, la seconda a chi legge.
        frequenze: Counter = Counter()
        leggibili: dict[str, Counter] = {}
        for voce in valori:
            chiave, leggibile = voce if isinstance(voce, tuple) else (voce, voce)
            if not chiave:
                continue
            frequenze[chiave] += 1
            leggibili.setdefault(chiave, Counter())[leggibile or chiave] += 1
        # Le equivalenze dichiarate a mano vengono prima di tutto: chi
        # conosce il paese ne sa piu' di qualunque soglia.
        dichiarate = {
            paleografia.normalizza(letta): paleografia.normalizza(giusta)
            for letta, giusta in (dichiarate or {}).items()
        }
        # Chi fa testo non e' chi supera una soglia assoluta ma chi **non
        # e' dominato da nessuno**. La differenza si vede su
        # 'Agrimensoere', letto otto volte accanto alle centosessantacinque
        # di 'agrimensore': con una soglia assoluta otto bastano a farne
        # una parola a se', e la scheda dell'unico agrimensore del paese
        # finiva per elencare quattro mestieri che erano lo stesso.
        ordinate = sorted(frequenze, key=lambda f: (-frequenze[f], f))
        capofila: dict[str, str] = {}
        famiglie: dict[str, list] = {}

        for forma in ordinate:
            dichiarata = dichiarate.get(forma)
            if dichiarata and dichiarata != forma:
                capofila[forma] = dichiarata
                famiglie.setdefault(dichiarata, [dichiarata]).append(forma)
                continue
            migliore, punteggio = None, soglia
            for candidata in ordinate:
                # Solo verso l'alto, e solo verso una forma che non si sia
                # gia' appoggiata a qualcun altro: cosi' non si formano
                # catene, che e' il modo in cui 'muratore' finiva su
                # 'proprietario'.
                if frequenze[candidata] < frequenze[forma] * DOMINANZA:
                    break
                if capofila.get(candidata) != candidata:
                    continue
                somiglianza = paleografia.somiglianza(forma, candidata)
                if somiglianza >= punteggio:
                    migliore, punteggio = candidata, somiglianza
            testa = migliore if migliore is not None else forma
            capofila[forma] = testa
            famiglie.setdefault(testa, []).append(forma)
        etichette = {
            testa: _piu_letta(leggibili, gruppo)
            for testa, gruppo in famiglie.items()
        }
        return cls(
            capofila=capofila,
            frequenze=frequenze,
            famiglie={testa: tuple(sorted(gruppo)) for testa, gruppo in famiglie.items()},
            etichette=etichette,
            nucleo=nucleo,
        )

    def chiave(self, valore: str | None) -> str | None:
        if not valore:
            return None
        if self.nucleo:
            return normalizza.nucleo_toponimo(valore) or paleografia.normalizza(valore)
        return paleografia.normalizza(valore)

    def canonica(self, valore: str | None) -> str | None:
        """La chiave con cui questa forma si confronta con le altre.

        Diversa da :meth:`interpreta`, che rende la forma **leggibile**:
        qui serve una chiave stabile, e le due grafie della stessa parola
        ne devono avere una sola. E' cio' che permette a 'Agrimenfore' e
        'Agrimensore' di contare come lo stesso mestiere quando si
        confrontano due schede.
        """
        chiave = self.chiave(valore)
        return self.capofila.get(chiave, chiave)

    def frequenze_per_famiglia(self) -> Counter:
        """Quante volte compare ogni mestiere, contando tutte le sue grafie.

        Il peso di un indizio e' il logaritmo della sua rarita', e la
        rarita' di 'agrimensore' e' quella della famiglia intera: contare
        solo la forma capofila lo farebbe sembrare piu' raro di quanto e'.
        """
        totali: Counter = Counter()
        for forma, quante in self.frequenze.items():
            totali[self.capofila.get(forma, forma)] += quante
        return totali

    def interpreta(self, valore: str | None) -> str | None:
        """La forma leggibile che rappresenta questa.

        Non la chiave: quella serve al confronto e per le contrade e' il
        solo nucleo del toponimo. Chi consulta deve leggere 'strada sotto
        il palazzo', non 'palazzo'.
        """
        testa = self.capofila.get(self.chiave(valore))
        if testa is None:
            return None
        return self.etichette.get(testa, testa)

    def quante(self, valore: str | None) -> int:
        return self.frequenze.get(self.chiave(valore), 0)


@dataclass
class Serie:
    """Un attributo nel tempo: cosa, da quando, secondo quale atto."""

    tipo: str
    voci: list = field(default_factory=list)   # (anno, valore, atto)

    @property
    def corrente(self) -> str | None:
        """Il valore da mostrare: l'ultimo attestato, non il piu' frequente.

        E' la scelta giusta per un attributo che cambia. Un uomo che
        dichiara 'bracciante' negli ultimi vent'anni di vita e 'bovaro'
        nei primi cinque **e'** un bracciante, anche se la maggioranza
        delle sue menzioni dice il contrario: la maggioranza qui conta
        gli anni, non le persone.
        """
        return self.voci[-1][1] if self.voci else None

    @property
    def valori(self) -> list:
        visti: dict[str, None] = {}
        for _, valore, _ in self.voci:
            visti.setdefault(valore, None)
        return list(visti)

    def racconta(self) -> str:
        """La serie in italiano: '1850 bovaro, 1875 contadino'."""
        pezzi, ultimo = [], None
        for anno, valore, _ in self.voci:
            if valore != ultimo:
                pezzi.append(f"{anno} {valore}" if anno else valore)
                ultimo = valore
        return ", ".join(pezzi)


def vocabolari(corpus, glossario=None) -> dict:
    """I vocabolari del paese, uno per campo che cambia nel tempo.

    ``glossario`` porta cio' che la statistica non puo' sapere: che
    'bovajo' e 'bovaro' sono lo stesso mestiere detto in due modi. Le due
    forme non si somigliano abbastanza da sembrare due grafie della stessa
    parola — e infatti non lo sono — quindi nessuna soglia le puo'
    unire; solo chi conosce il paese puo' dichiararlo.
    """
    mestieri = Vocabolario(nucleo=False)
    contrade = Vocabolario(nucleo=True)
    dal_glossario = {
        paleografia.normalizza(letta): giusta
        for letta, giusta in (getattr(glossario, "mestieri", None) or {}).items()
    }
    return {
        "professione": Vocabolario.dal_corpus(
            (
                (mestieri.chiave(m.professione), m.professione)
                for m in corpus.menzioni if m.professione
            ),
            dichiarate=dal_glossario,
        ),
        "contrada": Vocabolario.dal_corpus(
            (
                (contrade.chiave(m.via or m.residenza), m.via or m.residenza)
                for m in corpus.menzioni if (m.via or m.residenza)
            ),
            nucleo=True,
        ),
    }


def _grezzo(menzione, tipo: str) -> str | None:
    if tipo == "professione":
        return menzione.professione
    return menzione.via or menzione.residenza


def serie(scheda, tipo: str, vocabolario: Vocabolario) -> Serie:
    """La serie di un attributo per una scheda, in ordine di anno."""
    voci = []
    for menzione in sorted(scheda.menzioni, key=lambda m: (m.anno or 0, m.id)):
        grezzo = _grezzo(menzione, tipo)
        if not grezzo:
            continue
        voci.append((
            menzione.anno,
            vocabolario.interpreta(grezzo) or grezzo,
            menzione.atto,
        ))
    return Serie(tipo=tipo, voci=voci)


# ---------------------------------------------------------------------------
# Le anomalie
# ---------------------------------------------------------------------------

TIPO_ANOMALIA = {
    "professione": "PROFESSION_ANOMALY",
    "contrada": "ADDRESS_ANOMALY",
}


def anomalie(esito, vocabolari_: dict) -> list:
    """I valori che nel resto dell'archivio non esistono.

    Il caso e' quello dei cognomi rovinati, ma con una differenza che
    cambia la conclusione: i mestieri e le contrade di un paese sono un
    insieme quasi chiuso, quindi una forma vista una volta sola e' molto
    piu' sospetta di un cognome visto una volta sola. Molto piu' sospetta,
    non certa: l'unico agrimensore del paese esiste davvero, e appiattirlo
    su 'contadino' cancellerebbe il dato piu' interessante che c'e'.

    Quindi si segnala, con la forma proposta accanto, e decide chi guarda.
    """
    trovate: list[mod.Anomalia] = []
    for tipo, vocabolario in sorted(vocabolari_.items()):
        for chiave in sorted(esito.schede):
            scheda = esito.schede[chiave]
            trovate.extend(_sospetti(scheda, tipo, vocabolario))
    return trovate


def _sospetti(scheda, tipo: str, vocabolario: Vocabolario) -> list:
    """Le forme rare di una scheda, quando il resto della sua vita ne dice un'altra."""
    grezzi = Counter()
    per_forma: dict[str, tuple] = {}
    for menzione in scheda.menzioni:
        grezzo = _grezzo(menzione, tipo)
        if not grezzo:
            continue
        forma = vocabolario.chiave(grezzo)
        if not forma:
            continue
        grezzi[forma] += 1
        per_forma.setdefault(forma, (menzione.anno, menzione.atto, grezzo))

    trovate = []
    for forma, quante_qui in sorted(grezzi.items()):
        quante = vocabolario.quante(forma)
        if quante > FORMA_RARA:
            continue
        capofila = vocabolario.interpreta(forma)
        quante_capofila = vocabolario.quante(capofila)
        # Una forma rara che il vocabolario non riconduce a nessuna
        # famiglia non e' un errore: e' un mestiere raro, e sono i casi
        # che raccontano qualcosa.
        if capofila is None or capofila == forma:
            continue
        if quante_capofila < quante * DOMINANZA:
            continue
        anno, atto, grezzo = per_forma[forma]
        trovate.append(mod.Anomalia(
            tipo=TIPO_ANOMALIA[tipo],
            individui=(scheda.chiave,),
            campo=tipo,
            descrizione=(
                f"{scheda.etichetta()} risulta '{grezzo}' nel {anno} "
                f"({quante} volta/e in tutto il secolo), e nell'archivio quella "
                f"forma somiglia a '{capofila}' ({quante_capofila} volte)"
            ),
            atti=(atto,),
            alternative=(grezzo, capofila),
            spiegazioni=(
                f"e' una lettura rovinata di '{capofila}'",
                f"e' un {tipo} raro davvero, e vale la pena tenerlo",
            ),
            confidenza=0.6,
            impatto=quante_qui,
            gravita="bassa",
        ))
    return trovate


def _piu_letta(leggibili: dict, gruppo: list) -> str:
    """La forma leggibile piu' attestata di una famiglia.

    A parita' vince la piu' corta, e non e' un capriccio: fra 'contadino'
    e 'di condizione contadina' la prima e' quella che il registro usa di
    solito, e la seconda e' una formula.
    """
    conteggi: Counter = Counter()
    for forma in gruppo:
        conteggi.update(leggibili.get(forma, {}))
    if not conteggi:
        return gruppo[0]
    return max(sorted(conteggi), key=lambda f: (conteggi[f], -len(f)))
