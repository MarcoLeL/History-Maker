"""L'igiene dei nomi: eta', patronimici, inversioni, genere.

I casi vengono dalle pagine vere di Torrebruna. Quelli sul genere sono i
piu' importanti del file: se cadono, la ricostruzione delle parentele
fonde padri e madri.
"""

from collections import Counter

import pytest

from history_maker import nomi


# --- l'eta' -----------------------------------------------------------------

@pytest.mark.parametrize(
    "letta, anni",
    [
        ("trenta", 30),
        ("trentasei", 36),
        ("ventuno", 21),
        ("ventotto", 28),
        ("trentotto", 38),
        ("ventitrè", 23),
        ("ventitre", 23),
        ("quarantacinque", 45),
        ("cinquantaquattro", 54),
        ("sessantatre", 63),
        ("novantanove", 99),
        ("cento", 100),
        ("di anni quaranta", 40),
        ("40", 40),
        ("quaranta anni", 40),
    ],
)
def test_eta_in_lettere(letta, anni):
    assert nomi.analizza_eta(letta).anni == anni


def test_i_mesi_non_sono_anni():
    """Un bambino di nove mesi ha 0,75 anni, non nove.

    Serve a ricavare l'anno di nascita di chi muore in fasce: senza, un
    neonato morto nel 1844 risulterebbe nato nel 1835.
    """
    assert nomi.analizza_eta("mesi nove").anni == pytest.approx(0.75)
    assert nomi.analizza_eta("mesi nove").anno_nascita == 0
    assert nomi.analizza_eta("giorni otto").anni < 0.05


def test_neonato_vale_zero_non_ignoto():
    assert nomi.analizza_eta("neonato").anni == 0
    assert nomi.analizza_eta("pochi giorni").anni == 0


def test_maggiore_di_eta_e_un_minimo_non_una_misura():
    eta = nomi.analizza_eta("maggiore di età")
    assert eta.anni == nomi.ETA_MAGGIORE
    assert eta.approssimata


def test_circa_resta_segnato():
    assert nomi.analizza_eta("trenta circa").approssimata
    assert not nomi.analizza_eta("trenta").approssimata


def test_lettura_storta_riconosciuta_ma_marcata():
    """'sessandue' e' 'sessantadue' con una sillaba mangiata.

    Si accetta perche' il bersaglio e' un insieme chiuso — i numeri —
    ma esce approssimata, cosi' chi la usa allarga la tolleranza.
    """
    eta = nomi.analizza_eta("sessandue")
    assert eta.anni == 62
    assert eta.approssimata


def test_quel_che_non_e_un_eta_resta_ignoto():
    for valore in ("contadina", "vivente", "assessore", None, ""):
        assert nomi.analizza_eta(valore) is None


# --- il patronimico ---------------------------------------------------------

def test_patronimico_estratto():
    assert nomi.separa_patronimico("Giuseppe di Carmine") == ("Giuseppe", "Carmine")
    assert nomi.separa_patronimico("Domenico fu Tobia") == ("Domenico", "Tobia")


def test_nome_composto_non_e_un_patronimico():
    assert nomi.separa_patronimico("Maria Teresa") == ("Maria Teresa", None)


def test_cognome_finito_nella_colonna_del_nome_non_si_spezza():
    """'Di Nardo' non e' 'Nardo figlio di': e' un cognome intero."""
    assert nomi.separa_patronimico("Di Nardo") == ("Di Nardo", None)


# --- il genere --------------------------------------------------------------

def genere_di_prova() -> nomi.Genere:
    menzioni = (
        [("padre", "Domenico")] * 8
        + [("madre", "Domenica")] * 8
        + [("padre", "Giuseppe")] * 10
        + [("madre", "Giuseppa")] * 6
        + [("padre", "Nicola")] * 7
        + [("madre", "Maria")] * 20
        + [("sposo", "Salvatore")] * 5
        + [("testimone", "Ignoto")] * 4
    )
    return nomi.Genere.dal_corpus(menzioni)


def test_genere_dai_ruoli():
    genere = genere_di_prova()
    assert genere.di("Domenico") == "M"
    assert genere.di("Domenica") == "F"
    assert genere.di("Maria") == "F"
    assert genere.di("Salvatore") == "M"


def test_nicola_e_maschile_malgrado_la_a():
    """La desinenza sbaglia proprio sui nomi piu' portati del paese."""
    genere = genere_di_prova()
    assert genere.di("Nicola") == "M"
    # E lo sa anche senza averlo mai visto in un ruolo, per l'eccezione.
    assert nomi.Genere(Counter(), Counter()).di("Nicola") == "M"


def test_un_ruolo_non_visto_ricade_sulla_desinenza():
    vuoto = nomi.Genere(Counter(), Counter())
    assert vuoto.di("Rosaria") == "F"
    assert vuoto.di("Rosario") == "M"
    assert vuoto.di("Giovanni") == "M"


# --- le varianti del nome ---------------------------------------------------

def test_la_variante_si_corregge_sulla_forma_dominante():
    genere = genere_di_prova()
    nomi_letti = ["Giuseppe"] * 30 + ["Giyseppe"] * 2
    correzioni, _ = nomi.correzioni_dei_nomi(nomi_letti, genere)
    assert correzioni.get("Giyseppe") == "Giuseppe"


def test_maschile_e_femminile_non_si_fondono_mai():
    """Il caso che rovinerebbe tutto l'albero.

    'Domenico' e 'Domenica' stanno a somiglianza 0,96 — piu' vicini di
    quanto una lettura sbagliata stia alla sua forma buona. Unirli
    farebbe di padre e madre la stessa persona.
    """
    genere = genere_di_prova()
    nomi_letti = ["Domenico"] * 40 + ["Domenica"] * 3 + ["Giuseppe"] * 30 + ["Giuseppa"] * 2
    correzioni, proposte = nomi.correzioni_dei_nomi(nomi_letti, genere)
    assert "Domenica" not in correzioni
    assert "Giuseppa" not in correzioni
    assert all(p.letto != "Domenica" for p in proposte)


def test_il_nome_composto_si_corregge_pezzo_per_pezzo():
    assert nomi.applica_correzioni("Maria Giyseppa", {"Giyseppa": "Giuseppa"}) == "Maria Giuseppa"


def test_le_particelle_non_contano_come_nomi():
    assert nomi.parti_del_nome("Giovanni detto Nanni") == ["Giovanni", "Nanni"]
    assert nomi.parti_del_nome("Maria di Grazia") == ["Maria", "Grazia"]


# --- nome e cognome invertiti -----------------------------------------------

def bilancia_di_prova() -> nomi.Bilancia:
    menzioni = (
        [("Domenico", "Pelliccia")] * 40
        + [("Vincenzo", "Colella")] * 30
        + [("Marianna", "Lella")] * 12
        + [("Modesto", "Pepe")] * 8
    )
    return nomi.Bilancia.dal_corpus(menzioni)


def test_inversione_riconosciuta():
    """Dal 1881 il registro scrive 'Cognome Nome'."""
    bilancia = bilancia_di_prova()
    assert bilancia.invertita("Pelliccia", "Domenico")
    assert not bilancia.invertita("Domenico", "Pelliccia")


def test_niente_inversione_senza_prova_da_tutte_e_due_le_parti():
    """Serve che ENTRAMBE le colonne siano fuori posto.

    Con una prova sola si scambierebbe un nome che e' anche un cognome
    frequente — a Torrebruna 'Salvatore' e' tutte e due le cose.
    """
    bilancia = bilancia_di_prova()
    assert not bilancia.invertita("Domenico", "Sconosciuto")
    assert not bilancia.invertita("Raro", "Rarissimo")


def test_l_inversione_si_decide_per_atto():
    """Due righe certe trascinano le altre della stessa pagina.

    Chi scrive l'intestazione alla rovescia la scrive cosi' per tutto
    l'atto, e una pagina meta' dritta e meta' rovescia e' il peggiore
    dei due mondi.
    """
    bilancia = bilancia_di_prova()
    atto_di = {1: 10, 2: 10, 3: 10, 4: 20}
    letti = {
        1: ("Pelliccia", "Domenico"),   # certa
        2: ("Colella", "Vincenzo"),     # certa
        3: ("Zzz", "Yyy"),              # ignota, ma nello stesso atto
        4: ("Qqq", "Www"),              # ignota, in un altro atto
    }
    scambi = nomi.inversioni_per_atto(atto_di, letti, bilancia)
    assert {1, 2, 3} <= scambi
    assert 4 not in scambi


def test_la_riga_gia_dritta_non_si_trascina():
    bilancia = bilancia_di_prova()
    atto_di = {1: 10, 2: 10, 3: 10}
    letti = {
        1: ("Pelliccia", "Domenico"),
        2: ("Colella", "Vincenzo"),
        3: ("Domenico", "Pelliccia"),   # questa e' scritta bene
    }
    assert 3 not in nomi.inversioni_per_atto(atto_di, letti, bilancia)


# --- il ruolo batte la frequenza ---------------------------------------

def test_la_correzione_non_cambia_il_sesso_di_chi_e_dichiarato():
    """Un padre non diventa una donna perche' il suo nome e' raro.

    'Innocente' compare una volta sola in tutto l'archivio, come padre;
    'Innocenta' nove volte, tutte come madre. Il nome finisce in -e, che
    non ha sesso, quindi entra in tutti e due i confronti e la
    correzione delle varianti lo tira verso la forma piu' attestata. Il
    padre di Maria Lella diventava cosi' «Innocenta».
    """
    from collections import Counter

    genere = nomi.Genere(maschili=Counter(), femminili=Counter({"innocenta": 9}))
    corretto = nomi.applica_correzioni(
        "Innocente", {"Innocente": "Innocenta"}, ruolo="padre", genere=genere,
    )
    assert corretto == "Innocente"


def test_la_correzione_passa_quando_il_sesso_non_cambia():
    from collections import Counter

    genere = nomi.Genere(maschili=Counter({"giuseppe": 268}), femminili=Counter())
    corretto = nomi.applica_correzioni(
        "Giyseppe", {"Giyseppe": "Giuseppe"}, ruolo="padre", genere=genere,
    )
    assert corretto == "Giuseppe"


def test_senza_ruolo_la_correzione_resta_quella_di_prima():
    corretto = nomi.applica_correzioni("Giyseppe", {"Giyseppe": "Giuseppe"})
    assert corretto == "Giuseppe"


def test_il_nome_composto_prende_il_sesso_dal_primo(monkeypatch):
    """«Nicola Maria» e' un uomo, «Maria Nicola» una donna.

    Come parola, 'Nicola' nei registri di Torrebruna vale 534 volte da
    uomo e 103 da donna — perche' 'Maria Nicola' e' un nome femminile e
    il conteggio non sa in che posizione stia. Resta sotto la soglia e
    non dice niente, e a quel punto parlava 'Maria': Nicola Maria Lella,
    nato nel 1885, risultava una bambina, e con lui spariva il primo
    maschio di suo padre.
    """
    from collections import Counter

    genere = nomi.Genere(
        maschili=Counter({"nicola": 534, "maria": 169}),
        femminili=Counter({"nicola": 103, "maria": 1959}),
    )
    assert genere.della_forma("Nicola") is None, "il caso vive sull'ambiguita'"
    assert genere.di("Nicola Maria") == "M"
    assert genere.di("Maria Nicola") == "F"


def test_la_desinenza_parla_solo_in_prima_posizione():
    """In seconda posizione 'Maria' e' un voto, non un sesso."""
    from collections import Counter

    genere = nomi.Genere(maschili=Counter(), femminili=Counter())
    assert genere.di("Zaccaria Maria") == "M"   # sta in MASCHILI_IN_A
    assert genere.di("Rosangela Maria") == "F"


# ---------------------------------------------------------------------------
# L'apostrofo fra due lettere: «vent'uno» e' ventuno, non uno
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scritto, anni", [
    ("vent'uno", 21.0),
    ("trent'uno", 31.0),
    ("cinquant'otto", 58.0),
    ("sessant'uno", 61.0),
])
def test_l_apostrofo_fra_due_lettere_unisce(scritto, anni):
    """Il testimone del matrimonio n. 1 del 1811, «Giuseppe Marianacci d'anni vent'uno».

    L'apostrofo finiva fra i segni da togliere come tutti gli altri, e
    «vent'uno» diventava «vent uno»: il decina si perdeva e restava
    **uno**. Nei registri di Torrebruna sono poche righe, ma l'eta' che
    ne usciva faceva di un testimone un bambino di un anno.
    """
    assert nomi.analizza_eta(scritto).anni == anni


@pytest.mark.parametrize("scritto, anni", [
    ("quarant'anni", 40.0),
    ("ventitre'", 23.0),
    ("d'anni ventidue", 22.0),
    ("maggiore di eta'", 21.0),
])
def test_gli_altri_apostrofi_restano_come_erano(scritto, anni):
    """Il contrappeso: l'apostrofo che chiude una parola o ne apre una di zavorra."""
    assert nomi.analizza_eta(scritto).anni == anni
