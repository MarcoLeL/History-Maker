"""La ricostruzione genealogica: interpretazione, evidenza, identita'.

I casi sono divisi in tre gruppi, e il terzo e' quello che conta.

**L'interpretazione** — che 'Lella fu Michele' venga letto come un cognome
e un patronimico, e non come un cognome bizzarro. Sono errori di lettura
del formulario, e ognuno che sfugge diventa una famiglia spezzata in due.

**L'evidenza** — che due 'Pelliccia' valgano meno di due 'Genualdi', che
un'eta' che non torna non sia un veto, che due atti di nascita lo siano.

**L'identita'** — che il sistema unisca cio' che va unito, separi cio' che
va separato, e **dichiari** i casi in cui non sa. Quasi tutti i casi di
questo gruppo vengono da errori veri dell'archivio di Torrebruna: sono
scritti qui perche' non tornino.
"""

import sqlite3

import pytest

from history_maker.dataset import SCHEMA_SQL
from history_maker.ricostruzione import (
    anomalie, attributi, candidati, evidenza, lettura, modello, risoluzione,
)
from history_maker.ricostruzione.scheda import Scheda, principali


# ---------------------------------------------------------------------------
# Come si costruisce un corpus di prova
# ---------------------------------------------------------------------------

def corpus_da(atti: list[dict]):
    """Un corpus vero, costruito dalle tabelle della fase 4.

    Passa dal database invece di fabbricare le menzioni a mano perche' e'
    li' che sta meta' del lavoro: l'igiene dei nomi, la lettura della
    famiglia dentro l'atto, lo strato di interpretazione. Un test che le
    saltasse proverebbe qualcos'altro.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    for numero, atto in enumerate(atti, start=1):
        conn.execute(
            "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, "
            "data_atto) VALUES (?,?,?,?,?,?,?)",
            (numero, "r", f"{numero:04d}.jpg", str(numero), atto["tipo"],
             atto["anno"], atto.get("data")),
        )
        for persona in atto["persone"]:
            conn.execute(
                "INSERT INTO persone (atto, ruolo, nome, cognome, nome_letto, "
                "cognome_letto, cognome_origine, eta, professione, residenza, via, "
                "stato_vitale, incerto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    numero, persona["ruolo"], persona.get("nome"),
                    persona.get("cognome"), persona.get("nome"),
                    persona.get("cognome"), "atto", persona.get("eta"),
                    persona.get("professione"), persona.get("residenza"),
                    persona.get("via"), persona.get("stato_vitale"),
                    1 if persona.get("incerto") else 0,
                ),
            )
    corpus = lettura.carica(conn)
    # Come in produzione: il vocabolario del paese si costruisce prima
    # della risoluzione, perche' e' il confronto fra due schede a doverlo
    # usare. Un corpus di prova senza vocabolario proverebbe un sistema
    # diverso da quello che gira.
    corpus.vocabolari = attributi.vocabolari(corpus)
    return corpus


def nascita(anno, figlio, padre, madre, dati_padre=None, dati_madre=None):
    """Un atto di nascita: il neonato, il padre, la madre.

    ``dati_padre`` e ``dati_madre`` aggiungono eta', mestiere o contrada
    a uno dei due genitori: sono i campi su cui si gioca meta' dei casi.
    """
    persone = [
        {"ruolo": "neonato", "nome": figlio[0], "cognome": figlio[1]},
        dict({"ruolo": "padre", "nome": padre[0], "cognome": padre[1]},
             **(dati_padre or {})),
        dict({"ruolo": "madre", "nome": madre[0], "cognome": madre[1]},
             **(dati_madre or {})),
    ]
    return {"tipo": "nascita", "anno": anno, "data": f"{anno}-06-01",
            "persone": persone}


# Il vocabolario del paese. Comprende tutti i nomi e i cognomi che i casi
# usano, e serve a una cosa sola: dare loro una **frequenza realistica**.
#
# Un cognome che compare tre volte in un corpus di prova e' rarissimo, e
# un cognome rarissimo e' una prova fortissima: due 'Minchilli' si
# unirebbero da soli, e il caso non proverebbe niente. Il modello legge le
# frequenze del corpus che ha davanti, quindi un corpus di prova che non
# somigli a un paese misura qualcos'altro.
COGNOMI = (
    "Pelliccia", "Colella", "Marianacci", "Lella", "Desiderio", "Pepe",
    "Di Laudo", "Minchilli", "Cicchillitti", "Petta", "Ottaviano", "Troilo",
)
NOMI_M = (
    "Domenico", "Giuseppe", "Vincenzo", "Nicola", "Antonio", "Francesco",
    "Carmine", "Pasquale",
)
NOMI_F = (
    "Maria", "Anna", "Rosa", "Angela", "Teresa", "Saba", "Clementina",
    "Vitaliana",
)


def sfondo(quanti: int = 420) -> list[dict]:
    """Un paese di frequenze plausibili, generato sempre uguale.

    Poche famiglie che ricorrono molto, il primogenito che porta il nome
    del nonno, gli stessi sei nomi di battesimo per tutti: e' la
    condizione in cui la ricostruzione deve funzionare, ed e' quella in
    cui il nome, da solo, non prova niente.
    """
    atti = []
    for indice in range(quanti):
        anno = 1810 + indice % 80
        padre = (NOMI_M[indice % 8], COGNOMI[indice % 12])
        madre = (NOMI_F[(indice * 5) % 8], COGNOMI[(indice * 7) % 12])
        nomi_figlio = NOMI_M if indice % 2 else NOMI_F
        figlio = (nomi_figlio[(indice * 3) % 8], padre[1])
        atti.append(nascita(anno, figlio, padre, madre))
    return atti


def in_un_paese(atti: list[dict]):
    """Gli atti da provare, dentro un paese di frequenze realistiche.

    Rende ``(esito, prima_menzione)``: gli atti di prova stanno in fondo,
    quindi le loro menzioni hanno gli identificatori piu' alti, e da li'
    si ritrovano le schede che interessano senza confonderle con le
    duecento famiglie di contorno.
    """
    di_contorno = sfondo()
    menzioni_di_contorno = sum(len(atto["persone"]) for atto in di_contorno)
    corpus = corpus_da(di_contorno + atti)
    return risoluzione.ricostruisci(corpus), menzioni_di_contorno + 1


def schede_di_prova(esito, prima: int, ruolo: str | None = None) -> list:
    """Le schede toccate dagli atti di prova, non da quelli di contorno."""
    trovate = {}
    for scheda in esito.schede.values():
        for menzione in scheda.menzioni:
            if menzione.id >= prima and (ruolo is None or menzione.ruolo == ruolo):
                trovate[scheda.chiave] = scheda
                break
    return [trovate[chiave] for chiave in sorted(trovate)]


def ricostruisci(atti):
    return risoluzione.ricostruisci(corpus_da(atti))


def modello_del(corpus):
    return evidenza.Modello.dal_corpus(corpus)


def _peso_del_coniuge(atti, confronto_fra_schede: bool) -> float:
    """Quanto vale l'indizio del coniuge fra i due padri degli atti dati.

    Si ferma **prima** della riconciliazione: dopo, i due padri sarebbero
    gia' una scheda sola e non ci sarebbe piu' niente da confrontare. Il
    primo giro le tiene separate ed e' li' che la domanda si pone.
    """
    di_contorno = sfondo()
    prima = sum(len(atto["persone"]) for atto in di_contorno) + 1
    corpus = corpus_da(di_contorno + atti)
    modello_ = modello_del(corpus)
    esito = risoluzione.primo_giro(corpus, modello_)
    risoluzione.aggiorna_grafo(esito)
    modello_.schede = esito.schede

    padri = [
        scheda for chiave, scheda in sorted(esito.schede.items())
        if any(m.id >= prima and m.ruolo == "padre" for m in scheda.menzioni)
    ]
    assert len(padri) == 2, f"servono due schede di padre, ne ho {len(padri)}"
    tetto = evidenza.PESO_CONIUGE_RICONOSCIUTO
    if not confronto_fra_schede:
        # Un tetto a zero: nessun coniuge potra' mai essere riconosciuto,
        # che e' come si comportava prima.
        evidenza.PESO_CONIUGE_RICONOSCIUTO = 0.0
    try:
        prove = evidenza.confronta(padri[0], padri[1], modello_)
    finally:
        evidenza.PESO_CONIUGE_RICONOSCIUTO = tetto
    return sum(i.peso for i in prove.indizi if i.tipo == "coniuge")


# ---------------------------------------------------------------------------
# 1. L'interpretazione
# ---------------------------------------------------------------------------

class TestInterpretazione:
    """Separare cio' che il formulario dice da cio' che il cognome e'."""

    def test_la_filiazione_esce_dal_cognome(self):
        """'Lella fu Michele' e' un cognome e un patronimico.

        Lasciarcelo dentro non fa solo perdere il patronimico: fa peggio,
        perche' 'Lella fu Michele' e 'Lella' non si somigliano abbastanza
        da sembrare la stessa famiglia, e il cognome da indizio a favore
        diventa un veto.
        """
        from history_maker import nomi

        bilancia = nomi.Bilancia.dal_corpus(
            [("Michele", "Lella")] * 6 + [("Domenico", "Lella")] * 6
        )
        assert lettura.separa_filiazione("Lella fu Michele", bilancia) == (
            "Lella", "Michele"
        )

    def test_un_cognome_composto_non_si_tocca(self):
        """'Di Nardo' non e' un patronimico, e 'di Castiglione' e' un paese."""
        from history_maker import nomi

        bilancia = nomi.Bilancia.dal_corpus(
            [("Domenico", "Di Nardo")] * 8 + [("Giuseppe", "Colapietro")] * 8
        )
        assert lettura.separa_filiazione("Di Nardo", bilancia) == ("Di Nardo", None)
        # 'Castiglione' nel corpus non e' il nome di nessuno: nel dubbio
        # si lascia stare.
        assert lettura.separa_filiazione(
            "Colapietro di Castiglione", bilancia
        ) == ("Colapietro di Castiglione", None)

    def test_le_letture_alternative_si_conservano(self):
        """Chi ha trascritto aveva l'immagine davanti: le sue ipotesi valgono."""
        assert lettura.separa_alternative("Chiehi [o Chichi]") == (
            "Chiehi", ("Chichi",)
        )
        assert lettura.separa_alternative("Sepualdo [Epaldo/Gesualdo]") == (
            "Sepualdo", ("Epaldo", "Gesualdo")
        )
        assert lettura.separa_alternative("Pelliccia") == ("Pelliccia", ())

    def test_un_cognome_che_e_solo_un_apposizione_sparisce(self):
        """'sua moglie' non e' un cognome raro: non e' un cognome.

        E' l'errore che costava di piu': una stringa cosi' compare in
        pochissime righe, quindi il modello la trattava come un cognome
        rarissimo e ne ricavava una prova fortissima — due donne senza
        cognome risultavano della stessa famiglia.
        """
        assert lettura.separa_apposizione("sua moglie") == (None, None)
        assert lettura.separa_apposizione("d'Ettorre defunta") == (
            "d'Ettorre", "defunta"
        )

    def test_la_moglie_letta_male_non_e_una_seconda_moglie(self):
        """Nicolangelo Lella ha una moglie sola, scritta in sette modi.

        L'archivio la chiama Pizzi, Moretta, Motta, Moutta, Desiderio e —
        una volta, nel 1837 — 'Ermanda Sidri'. Un secondo coniuge e' raro
        (a Torrebruna si risposa uno sposo su 75) e il calcolo lo fa
        pagare caro, giustamente: senza quel prezzo le schede si fondono
        a catena finche' una donna si ritrova sei mariti.

        Ma il prezzo non lo deve pagare **questo** caso, dove i due
        coniugi sono due letture della stessa donna. I nomi da soli non
        lo dicono — 'Ermanda' contro 'Emmanuela' fa 0,56 — e serve
        confrontare le due mogli come **schede**: stessa eta', lo stesso
        marito, e i figli che tornano.

        Il caso e' scritto sull'indizio del coniuge, ed e' li' che si
        vede: spento il confronto fra schede quell'indizio vale meno di
        zero, acceso vale a favore.
        """
        atti = [
            nascita(1834, ("Carlo", "Minchilli"), ("Nazario", "Minchilli"),
                    ("Emmanuela", "Pizzi"), dati_madre={"eta": 32}),
            # Lo stesso uomo, la stessa donna, una mano peggiore.
            nascita(1837, ("Saba", "Minchilli"), ("Nazario", "Minchilli"),
                    ("Ermanda", "Sidri"), dati_madre={"eta": 35}),
        ]
        acceso = _peso_del_coniuge(atti, confronto_fra_schede=True)
        spento = _peso_del_coniuge(atti, confronto_fra_schede=False)
        assert spento < 0, "il caso non prova niente: non c'era nessuna penale"
        assert acceso > 0, "le due letture della stessa moglie non si sono riconosciute"

    def test_due_mogli_diverse_restano_due(self):
        """E il contrario: due donne diverse non diventano una.

        E' la meta' che tiene in piedi l'altra. Se bastasse un marito
        somigliante per essere la stessa moglie, il confronto fra schede
        sarebbe un modo piu' lento di dire di si' a tutto, e tornerebbero
        le donne con sei mariti.
        """
        atti = [
            nascita(1834, ("Carlo", "Minchilli"), ("Nazario", "Minchilli"),
                    ("Emmanuela", "Pizzi"), dati_madre={"eta": 30}),
            nascita(1866, ("Saba", "Minchilli"), ("Nazario", "Minchilli"),
                    ("Teodora", "Colella"), dati_madre={"eta": 24}),
        ]
        assert _peso_del_coniuge(atti, confronto_fra_schede=True) < 0

    def test_il_cognome_della_moglie_finito_addosso_al_marito(self):
        """Il caso di Marzio, atto di morte n. 18 del 1831.

        Sulla carta c'e' scritto «e' morto Marzio d'Andria Motta d'anni
        trentasei ... **marito d'Andria Motta** figlio del fu Filippo
        Lella e di Margherita Rossi». 'Andria Motta' e' il nome della
        moglie, e chi ha trascritto l'ha attaccato a lui come cognome. A
        dichiarare la morte e' Nicolangelo Lella, che e' suo fratello.

        Marzio compare una volta sola in tutto il secolo, quindi non c'e'
        nessun'altra sua menzione con cui confrontarlo: la ricostruzione
        deve reggersi sui **suoi**, e nove fratelli piu' un padre dicono
        Lella. Il cognome letto resta dov'e'.
        """
        corpus = corpus_da(sfondo() + [
            nascita(1790, ("Domenico", "Lella"), ("Filippo", "Lella"),
                    ("Margherita", "Rossi")),
            nascita(1793, ("Maria", "Lella"), ("Filippo", "Lella"),
                    ("Margherita", "Rossi")),
            nascita(1799, ("Nicolangelo", "Lella"), ("Filippo", "Lella"),
                    ("Margherita", "Rossi")),
            {"tipo": "morte", "anno": 1831, "persone": [
                {"ruolo": "defunto", "nome": "Marzio",
                 "cognome": "d'Andria Motta", "eta": "trentasei"},
                {"ruolo": "padre", "nome": "Filippo", "cognome": "Lella",
                 "stato_vitale": "defunto"},
                {"ruolo": "madre", "nome": "Margherita", "cognome": "Rossi"},
                {"ruolo": "dichiarante", "nome": "Nicolangelo", "cognome": "Lella",
                 "eta": "trenta"},
            ]},
        ])
        esito = risoluzione.ricostruisci(corpus)
        marzio = [
            s for s in esito.schede.values()
            if any(m.nome == "Marzio" for m in s.menzioni)
        ][0]

        # 1. La famiglia: il cognome sbagliato non lo stacca dal padre.
        padri = [esito.schede[c] for c in marzio.padri]
        assert padri and padri[0].cognome_migliore() == "Lella"
        # 2. Il caso viene segnalato, con la forma proposta accanto.
        dubbi = anomalie.cognomi_sospetti(esito)
        suoi = [a for a in dubbi if marzio.chiave in a.individui]
        assert suoi, "un cognome visto una volta sola, con un padre Lella, va segnalato"
        assert suoi[0].campo == "cognome"
        # 3. E la lettura originale non si perde: sta nei fatti.
        fatti = [
            f for m in marzio.menzioni
            for f in lettura.fatti_dalla_menzione(m, 1) if f.tipo == "cognome"
        ]
        assert fatti[0].valore.grezzo == "d'Andria Motta"

    def test_il_grezzo_non_si_perde_mai(self):
        """Interpretare non e' cancellare: la lettura originale resta.

        Serve un paese intero perche' la guardia della filiazione **e'**
        il corpus: 'Michele' si separa dal cognome solo se nel resto
        dell'archivio si comporta da nome di battesimo, e su un atto solo
        non si sa.
        """
        corpus = corpus_da(sfondo() + [
            nascita(1855, ("Michele", "Lella"), ("Domenico", "Lella"),
                    ("Anna", "Colella")),
            nascita(1857, ("Michele", "Pepe"), ("Giuseppe", "Pepe"),
                    ("Rosa", "Colella")),
            nascita(1859, ("Michele", "Colella"), ("Nicola", "Colella"),
                    ("Teresa", "Pepe")),
            nascita(1861, ("Michele", "Petta"), ("Antonio", "Petta"),
                    ("Angela", "Lella")),
            nascita(1863, ("Michele", "Lella"), ("Vincenzo", "Lella"),
                    ("Maria", "Petta")),
            nascita(1865, ("Maria", "Lella"), ("Domenico", "Lella fu Michele"),
                    ("Anna", "Colella")),
        ])
        padre = [
            m for m in corpus.menzioni
            if m.cognome_letto == "Lella fu Michele"
        ][0]
        assert padre.cognome == "Lella"
        assert padre.patronimico == "Michele"
        assert padre.cognome_letto == "Lella fu Michele"


# ---------------------------------------------------------------------------
# 2. L'evidenza
# ---------------------------------------------------------------------------

class TestEvidenza:
    """Quanto vale un indizio, misurato sul paese."""

    def test_un_cognome_frequente_vale_meno_di_uno_raro(self):
        """E' la ragione per cui i pesi non possono essere fissi.

        A Torrebruna i Pelliccia sono un decimo dell'archivio e i
        Genualdi sei menzioni in un secolo. Due Pelliccia sono la
        normalita' del paese; due Genualdi sono quasi certamente parenti.
        """
        from collections import Counter

        modello_ = evidenza.Modello(
            totale=50000,
            cognomi=Counter({"pelicia": 5000, "genualdi": 6}),
            nomi_interi=Counter(), parti_nome=Counter(),
            professioni=Counter(), contrade=Counter(),
        )
        comune = modello_.peso_token(
            "pelicia", modello_.cognomi, evidenza.M_COGNOME_UGUALE,
            evidenza.TETTO_COGNOME,
        )
        raro = modello_.peso_token(
            "genualdi", modello_.cognomi, evidenza.M_COGNOME_UGUALE,
            evidenza.TETTO_COGNOME,
        )
        assert comune < 1.0 < raro
        assert raro == evidenza.TETTO_COGNOME   # un indizio solo non decide

    def test_due_grafie_dello_stesso_mestiere_valgono_come_una(self):
        """'Agrimenfore' e 'Agrimensore' sono lo stesso mestiere.

        Il sistema lo sapeva gia' — il vocabolario dei mestieri riconduce
        le due grafie a una — ma lo sapeva **dopo**: il vocabolario si
        costruiva a risoluzione finita, e il confronto fra due schede
        guardava la forma normalizzata grezza. Due menzioni scritte cosi'
        non prendevano il punto del mestiere in comune, mentre il sistema,
        dieci righe piu' in la', le stampava come la stessa parola.

        Ora il vocabolario si costruisce prima e viaggia col corpus. Il
        caso e' scritto sull'**indizio**, non sull'esito: un mestiere in
        comune non basta quasi mai a decidere da solo, ma e' uno degli
        indizi medi che fanno superare la soglia a un testimone, e o c'e'
        o non c'e'.
        """
        # Il vocabolario non decide per sola somiglianza: una forma e' la
        # storpiatura di un'altra se quell'altra e' molto piu' frequente.
        # Servono quindi degli agrimensori scritti giusti, o 'Agrimenfore'
        # resta legittimamente un mestiere per conto suo.
        altri = [
            nascita(1820 + indice, (NOMI_F[indice], COGNOMI[indice]),
                    (NOMI_M[indice], COGNOMI[indice]),
                    (NOMI_F[7 - indice], COGNOMI[11 - indice]),
                    dati_padre={"professione": "Agrimensore", "eta": 35})
            for indice in range(6)
        ]
        atti = [
            nascita(1840, ("Rosa", "Minchilli"), ("Nicola", "Minchilli"),
                    ("Anna", "Troilo"),
                    dati_padre={"professione": "Agrimensore", "eta": 40}),
            nascita(1843, ("Saba", "Cicchillitti"), ("Domenico", "Cicchillitti"),
                    ("Teresa", "Colella"),
                    dati_padre={"professione": "Agrimenfore", "eta": 43}),
        ]
        corpus = corpus_da(sfondo() + altri + atti)
        vocabolario = corpus.vocabolari["professione"]
        assert vocabolario.canonica("Agrimenfore") == vocabolario.canonica(
            "Agrimensore"
        )

        def peso_del_mestiere(con_vocabolario: bool) -> float:
            chiavi = lettura.Chiavi(
                corpus.per_id, corpus.vocabolari if con_vocabolario else {}
            )
            due = [
                Scheda.dalla_menzione(menzione, chiavi)
                for menzione in corpus.menzioni
                if menzione.professione and menzione.professione.startswith("Agrim")
                and menzione.cognome in ("Minchilli", "Cicchillitti")
            ]
            assert len(due) == 2
            prove = evidenza.confronta(due[0], due[1], modello_del(corpus))
            return sum(i.peso for i in prove.indizi if i.tipo == "mestiere")

        # Col vocabolario e' lo stesso mestiere, e vale a favore. Senza,
        # sono due parole diverse, e il confronto le pagava con la penale
        # dei mestieri che non tornano: non un punto mancato, un punto al
        # contrario.
        assert peso_del_mestiere(True) > 0
        assert peso_del_mestiere(False) < 0

    def test_l_eta_che_non_torna_non_e_un_veto(self):
        """Dodici anni di scarto costano circa un ban, non l'impossibilita'.

        E' il caso di Michelangelo Colella e Maria Marianacci: sedici
        figli in una scheda, due nell'altra, tenute separate dal sistema
        precedente da nove anni di eta' dichiarata.
        """
        assert evidenza.peso_eta(12, False) > -1.5
        assert evidenza.peso_eta(0, False) > 1.0
        # Ma cresce sempre, e non si appiattisce: quaranta anni di scarto
        # devono costare piu' di dodici.
        assert evidenza.peso_eta(40, False) < evidenza.peso_eta(12, False)

    def test_due_atti_di_nascita_sono_due_bambini(self):
        """Il veto piu' netto: contro una data scritta non si negozia."""
        una = Scheda(chiave=1)
        una.nascite_certe = [1820]
        altra = Scheda(chiave=2)
        altra.nascite_certe = [1824]
        assert evidenza.veti(una, altra) is not None

    def test_il_nome_da_solo_non_basta_mai(self):
        """Due omonimi senza altra evidenza restano due persone.

        In un paese dove il primogenito porta il nome del nonno,
        'Domenico Pelliccia' sono cinque uomini diversi in mezzo secolo, e
        cucirli insieme fabbrica una persona che non e' mai esistita.
        """
        from collections import Counter

        # Le frequenze e l'a priori sono quelli misurati su Torrebruna:
        # 'Domenico' 1.580 menzioni su 49.889, 'Pelliccia' 5.121, e
        # sedicimila persone in tutto. Il caso si prova cosi', e non
        # ricostruendo, perche' e' una domanda sulla **scala**: in un
        # archivio di duecento persone due omonimi sono davvero quasi
        # sempre la stessa persona, e la risposta giusta cambia.
        modello_ = evidenza.Modello(
            a_priori=-4.26,
            totale=49889,
            cognomi=Counter({"pelicia": 5121}),
            nomi_interi=Counter({"domenico": 1580}),
            parti_nome=Counter({"domenico": 1580}),
            professioni=Counter(), contrade=Counter(),
        )
        uno, due = Scheda(chiave=1), Scheda(chiave=2)
        for scheda in (uno, due):
            scheda.nomi = Counter({"Domenico": 1})
            scheda.cognomi = Counter({"Pelliccia": 1})
            scheda.chiavi_nome = {"domenico"}
            scheda.chiavi_cognome = {"pelicia"}
            scheda.conteggi_nome = Counter({"domenico": 1})
            scheda.conteggi_cognome = Counter({"pelicia": 1})

        prove = evidenza.confronta(uno, due, modello_)
        assert prove.impossibile is None, "non e' un veto: e' evidenza che non basta"
        assert prove.logit < risoluzione.SOGLIA_UNIONE, prove.racconta()

    def test_il_tetto_agli_indizi_deboli(self):
        """Nome, cognome ed eta' insieme non superano la soglia da soli."""
        # L'a priori e' quello misurato su Torrebruna.
        prove = modello.Evidenza(a_priori=-4.26)
        prove.aggiungi("nome", 2.5, "nome raro")
        prove.aggiungi("cognome", 2.5, "cognome raro")
        evidenza._tetto_agli_indizi_deboli(prove)
        assert prove.totale == pytest.approx(evidenza.QUOTA_INDIZI_DEBOLI * 4.26)
        # E il tetto tiene la conclusione sotto la soglia dell'unione.
        # E' un legame fra due costanti che stanno in moduli diversi, e
        # si rompe in silenzio se qualcuno ne ritocca una: se questo
        # controllo cade, nome e cognome da soli bastano a unire due
        # persone, e meta' di questo impianto smette di servire.
        assert prove.logit < risoluzione.SOGLIA_UNIONE

    def test_le_forme_isolate_non_provano_niente(self):
        """Una grafia vista una volta su venti non fa testo nel confronto.

        Senza questa regola una scheda ricca di varianti somiglia a
        chiunque: 'Maria Nicola D'Ettore', ventidue menzioni, si prendeva
        la sorella 'Lucia Antonia' perche' fra le sue letture ce n'era
        una che diceva cosi'.
        """
        from collections import Counter

        assert principali(Counter({"marianicola": 8, "luciaantonia": 1})) == {
            "marianicola"
        }


# ---------------------------------------------------------------------------
# 3. L'identita'
# ---------------------------------------------------------------------------

class TestIdentita:
    """Unire cio' che va unito, separare il resto, e dichiarare i dubbi."""

    def test_la_coppia_gemella_si_ricompone(self):
        """Due nuclei con la stessa coppia e i figli divisi sono una famiglia.

        E' la frammentazione che fa piu' danno, perche' non spezza una
        persona ma una famiglia: i fratelli finiscono in due nuclei e da
        quel momento non si riconoscono piu' come fratelli. Qui il
        cognome della madre e' letto in due modi — 'Minchilli' e
        'Minchillo' — e il sistema precedente si fermava li'.
        """
        esito, prima = in_un_paese([
            nascita(1870, ("Anna", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
            nascita(1872, ("Luigi", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
            nascita(1875, ("Maria", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchillo")),
        ])
        padri = schede_di_prova(esito, prima, ruolo="padre")
        madri = schede_di_prova(esito, prima, ruolo="madre")
        assert len(padri) == 1, "il padre e' uno solo"
        assert len(madri) == 1, "la madre e' una sola"
        # E i tre bambini sono fratelli, che e' il punto: una coppia
        # gemella non spezza una persona, spezza una famiglia.
        figli = schede_di_prova(esito, prima, ruolo="neonato")
        assert len(figli) == 3
        assert all(madri[0].chiave in figlio.madri for figlio in figli)
        assert all(padri[0].chiave in figlio.padri for figlio in figli)

    def test_il_cognome_del_tutto_diverso_non_e_un_veto(self):
        """Una trascrizione puo' produrre un cognome che non c'entra niente.

        Quando il resto del grafo e' concorde — stessa moglie, stessi
        figli, cronologia compatibile — l'identita' regge lo stesso. E' il
        principio per cui gli attributi sono evidenze e non chiavi.
        """
        esito, prima = in_un_paese([
            nascita(1850, ("Anna", "Pelliccia"), ("Nicola", "Pelliccia"),
                    ("Vitaliana", "Ottaviano")),
            nascita(1852, ("Luigi", "Pelliccia"), ("Nicola", "Pelliccia"),
                    ("Vitaliana", "Ottaviano")),
            # Qui il cognome del padre e' letto 'Bellucci': niente a che
            # vedere con 'Pelliccia'. Ma la moglie e' la stessa.
            nascita(1855, ("Rosa", "Pelliccia"), ("Nicola", "Bellucci"),
                    ("Vitaliana", "Ottaviano")),
        ])
        padri = schede_di_prova(esito, prima, ruolo="padre")
        assert len(padri) == 1
        assert "beluci" in padri[0].chiavi_cognome

    def test_i_gemelli_omonimi_restano_due(self):
        """Due atti di nascita distinti sono due bambini, sempre.

        Rimettere a un neonato il nome di un fratello morto e' un uso
        documentato e frequente: unirli cancellerebbe una nascita e una
        morte vere.
        """
        esito, prima = in_un_paese([
            nascita(1850, ("Giuseppe", "Pepe"), ("Modesto", "Pepe"),
                    ("Marianna", "Lemme")),
            nascita(1854, ("Giuseppe", "Pepe"), ("Modesto", "Pepe"),
                    ("Marianna", "Lemme")),
        ])
        neonati = schede_di_prova(esito, prima, ruolo="neonato")
        assert len(neonati) == 2

    def test_il_secondo_nome_che_compare_e_scompare(self):
        """'Maria Vincenza' e 'Maria' sono la stessa donna, se il resto torna.

        Il sistema precedente le teneva divise da diciotto anni di eta'
        dichiarata; qui l'eta' e' un indizio e il marito e' una prova.
        """
        # Il nome di battesimo e' fuori dal vocabolario del paese apposta:
        # cosi' le due madri non possono confondersi con le duecento
        # famiglie di contorno, e cio' che il caso misura resta una cosa
        # sola — se diciotto anni di eta' dichiarata bastino a separarle.
        esito, prima = in_un_paese([
            nascita(1840, ("Anna", "Cicchillitti"), ("Giuseppe", "Cicchillitti"),
                    ("Calideta Vincenza", "Petta"),
                    dati_madre={"eta": "trentadue"}),
            nascita(1843, ("Rosa", "Cicchillitti"), ("Giuseppe", "Cicchillitti"),
                    ("Calideta", "Petta"), dati_madre={"eta": "cinquanta"}),
        ])
        madri = schede_di_prova(esito, prima, ruolo="madre")
        assert len(madri) == 1, "diciotto anni di scarto non sono due donne"

    def test_la_scheda_incoerente_si_divide(self):
        """Una fusione sbagliata non resta li' per sempre.

        E' la meta' che al sistema precedente mancava del tutto: nessun
        passaggio rileggeva una scheda intera per accorgersi che quella
        donna partoriva per sessantun anni.
        """
        una = Scheda(chiave=1)
        una.nascite_certe = [1820, 1840]
        assert evidenza.incoerenze(una)

    def test_le_menzioni_non_si_perdono_mai(self):
        """Il conto deve tornare: e' l'unica garanzia che l'archivio da'."""
        atti = [
            nascita(1850, ("Anna", "Lella"), ("Domenico", "Lella"),
                    ("Teresa", "Desiderio")),
            nascita(1853, ("Luigi", "Lelli"), ("Domenico", "Lelli"),
                    ("Teresa", "Desiderio")),
            {"tipo": "morte", "anno": 1860, "persone": [
                {"ruolo": "defunto", "nome": "Anna", "cognome": "Lella",
                 "eta": "dieci"},
                {"ruolo": "dichiarante", "nome": "Domenico", "cognome": "Lella"},
            ]},
        ]
        corpus = corpus_da(atti)
        esito = risoluzione.ricostruisci(corpus)
        raccolte = sum(s.quante for s in esito.schede.values())
        distinte = {m.id for s in esito.schede.values() for m in s.menzioni}
        assert raccolte == corpus.totale
        assert len(distinte) == corpus.totale

    def test_ogni_fusione_lascia_scritto_perche(self):
        """Senza audit trail una ricostruzione non e' verificabile.

        L'archivio tiene due registri diversi, e la differenza e'
        deliberata. Le **attribuzioni** — questa riga sta in questa scheda
        — sono decine di migliaia, e le loro prove stanno sulla scheda e
        nella colonna ``prove`` di ``menzioni``. Le **fusioni** e le
        **separazioni** sono poche e cambiano la forma dell'albero: quelle
        vanno nella tabella ``decisioni``, con le prove a favore, le
        contraddizioni e la versione dell'algoritmo che le ha prese.
        """
        esito, prima = in_un_paese([
            nascita(1850, ("Anna", "Pelliccia"), ("Nicola", "Pelliccia"),
                    ("Vitaliana", "Ottaviano")),
            nascita(1852, ("Luigi", "Pelliccia"), ("Nicola", "Pelliccia"),
                    ("Vitaliana", "Ottaviano")),
            nascita(1855, ("Rosa", "Pelliccia"), ("Nicola", "Bellucci"),
                    ("Vitaliana", "Ottaviano")),
        ])
        unioni = [d for d in esito.decisioni if d.azione == "unione"]
        assert unioni, "nessuna fusione registrata"
        for decisione in unioni:
            assert decisione.motivo
            assert decisione.evidenze, "una fusione senza prove non e' una fusione"
            assert 0.0 <= decisione.confidenza <= 1.0
            assert decisione.quando

        # E le attribuzioni lasciano le loro, sulla scheda.
        padri = schede_di_prova(esito, prima, ruolo="padre")
        assert padri[0].prove, "una scheda senza prove non si puo' verificare"

    def test_due_esecuzioni_uguali_danno_lo_stesso_albero(self):
        """Non e' pignoleria: e' la condizione perche' l'audit trail serva.

        Una ricostruzione che cambia risultato fra due esecuzioni
        identiche non e' verificabile — non si puo' dire se una soglia
        ritoccata abbia migliorato le cose o se sia stato il caso. Il
        rischio e' l'ordine degli insiemi, che in Python dipende dal seme
        di hash e cambia a ogni processo: per questo ogni ciclo di questo
        impianto passa da liste ordinate. La fase 6 precedente non lo
        faceva, e su tre esecuzioni dava 16.693, 16.694 e 16.707 persone.
        """
        atti = sfondo(60) + [
            nascita(1870, ("Anna", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
            nascita(1872, ("Luigi", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchillo")),
        ]

        def impronta():
            esito = risoluzione.ricostruisci(corpus_da(atti))
            return [
                (chiave, tuple(m.id for m in esito.schede[chiave].menzioni))
                for chiave in sorted(esito.schede)
            ]

        assert impronta() == impronta()

    def test_le_chiavi_sopravvivono_alla_ricostruzione(self):
        """La chiave di una scheda e' la sua menzione piu' antica.

        Rinumerare le persone a ogni esecuzione rende impossibile sia
        l'annotazione umana sia l'audit trail: il '#2396' di ieri oggi e'
        un'altra persona.
        """
        atti = [
            nascita(1870, ("Anna", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
            nascita(1872, ("Luigi", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
        ]
        prima = ricostruisci(atti)
        seconda = ricostruisci(atti)
        assert sorted(prima.schede) == sorted(seconda.schede)
        for chiave, scheda in prima.schede.items():
            assert scheda.quante == seconda.schede[chiave].quante

    def test_i_fatti_conservano_la_serie_invece_di_sovrascriverla(self):
        """Bovaro nel 1850 e contadino nel 1875 e' la stessa persona.

        Il modello a campi singoli perde il primo valore; il modello a
        fatti datati li tiene tutti e due, ed e' cosi' che si distingue un
        cambiamento di mestiere da un errore di lettura.
        """
        corpus = corpus_da([
            nascita(1850, ("Anna", "Lella"), ("Domenico", "Lella"),
                    ("Teresa", "Desiderio"), dati_padre={"professione": "bovaro"}),
            nascita(1875, ("Luigi", "Lella"), ("Domenico", "Lella"),
                    ("Teresa", "Desiderio"), dati_padre={"professione": "contadino"}),
        ])
        esito = risoluzione.ricostruisci(corpus)
        padre = [s for s in esito.schede.values() if "padre" in s.ruoli][0]
        mestieri = set()
        for menzione in padre.menzioni:
            for fatto in lettura.fatti_dalla_menzione(menzione, 1):
                if fatto.tipo == "professione":
                    mestieri.add((fatto.anno, fatto.valore.grezzo))
        assert mestieri == {(1850, "bovaro"), (1875, "contadino")}


# ---------------------------------------------------------------------------
# 4. I dubbi
# ---------------------------------------------------------------------------

class TestDubbi:
    """Che il sistema dica quello che non sa, invece di indovinare."""

    def test_la_fascia_grigia_diventa_una_coda(self):
        """Cio' che il calcolo non decide non si butta: si mette in fila."""
        esito = ricostruisci([
            nascita(1850, ("Anna", "Colella"), ("Vincenzo", "Colella"),
                    ("Maria", "Marianacci"), dati_padre={"eta": "trenta"}),
            {"tipo": "morte", "anno": 1855, "persone": [
                {"ruolo": "testimone", "nome": "Vincenzo", "cognome": "Colella",
                 "eta": "trentacinque", "professione": "contadino"},
                {"ruolo": "defunto", "nome": "Rosa", "cognome": "Petta"},
            ]},
        ])
        esito.anomalie.extend(anomalie.tutte(esito))
        coda = anomalie.coda(esito)
        assert coda == sorted(coda, key=lambda a: (-a.priorita, a.tipo, a.individui))

    def test_la_priorita_mette_avanti_cio_che_muove_di_piu(self):
        """Il dubbio grosso su un ramo intero viene prima del dubbio piccolo."""
        grande = modello.Anomalia(
            tipo="DUPLICATE_PERSON", individui=(1, 2), descrizione="",
            confidenza=0.5, impatto=40,
        )
        piccola = modello.Anomalia(
            tipo="DUPLICATE_PERSON", individui=(3, 4), descrizione="",
            confidenza=0.5, impatto=2,
        )
        assert grande.priorita > piccola.priorita

    def test_l_omonimia_e_un_risultato_non_un_fallimento(self):
        """Due candidati incompatibili e ugualmente buoni si dichiarano.

        Un archivio che sceglie a caso quando non sa e' peggio di uno che
        ammette di non sapere, perche' non si vede che ha scelto a caso.
        """
        prove = modello.Evidenza()
        prove.aggiungi("nome", 2.0)
        anomalia = risoluzione._anomalia_omonimia(
            [(Scheda(chiave=1), prove), (Scheda(chiave=2), prove)], Scheda(chiave=3)
        )
        assert anomalia.tipo == "POSSIBLE_HOMONYM"
        assert anomalia.gravita == "alta"
        assert len(anomalia.spiegazioni) >= 2


# ---------------------------------------------------------------------------
# 5. La generazione dei candidati
# ---------------------------------------------------------------------------

class TestCandidati:
    """Il tetto della ricostruzione: chi non e' candidato non si unira' mai."""

    def test_lo_scaffale_vuoto_vale_solo_per_chi_non_ha_cognome(self):
        """Il difetto piu' costoso che questa fase abbia avuto.

        Indicizzare anche chi il cognome ce l'ha sotto la chiave vuota
        crea uno scaffale che contiene l'archivio intero: 546 candidati
        per menzione invece di una decina, e centoventi volte il tempo.
        """
        con_cognome = Scheda(chiave=1)
        con_cognome.chiavi_cognome = {"pelicia"}
        con_cognome.parti_nome = {"domenico"}
        senza = Scheda(chiave=2)
        senza.parti_nome = {"domenico"}

        indice = candidati.indice_nome_cognome(
            {1: con_cognome, 2: senza}, {}, {}
        )
        assert indice[("pelicia", "domenico")] == [1]
        assert indice[("", "domenico")] == [2]

    def test_i_nuclei_si_indicizzano_senza_il_cognome(self):
        """Il cognome e' proprio il campo che in quei casi e' letto male."""
        esito = ricostruisci([
            nascita(1870, ("Anna", "Di Laudo"), ("Domenico", "Di Laudo"),
                    ("Saba", "Minchilli")),
        ])
        nuclei = candidati.nuclei_per_nome(esito.schede, esito.di_menzione)
        assert any(chiave == ("domenico", "saba") for chiave in nuclei)
