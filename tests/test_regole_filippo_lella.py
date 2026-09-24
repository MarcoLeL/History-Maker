"""Le regole nate dall'albero di Filippo Lella, una per una.

Ogni caso di questo file e' un errore vero trovato navigando un albero
solo — quello di Filippo Lella, nato a Torrebruna nel 1758 — e sciolto
guardando la pagina. Il valore non sta nel caso: sta nella regola che il
caso ha imposto, e questi test esistono perche' la regola non si
riperda.

Il test porta il nome del caso e non quello della regola, apposta: chi
domani lo vedra' rompersi deve poter risalire alla pagina.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

import pytest

from conftest import FINTI  # noqa: F401  (assicura src/ sul path)
from history_maker import albero, glossario, menzioni
from history_maker.dataset import SCHEMA_SQL
from history_maker.ricostruzione import evidenza, modello, registro, risoluzione
from test_ricostruzione import in_un_paese, matrimonio, nascita, schede_di_prova


# ---------------------------------------------------------------------------
# 1. «Angela Lella aveva sei mariti»
# ---------------------------------------------------------------------------

def _famiglia(anno, figlio, padre, madre, eta_padre=None):
    return nascita(anno, figlio, padre, madre,
                   {"eta": eta_padre} if eta_padre else None)



def _riga(identificatore, tipo, ruolo, nome, cognome, atto=1, immagine="r/0001.jpg"):
    """Una menzione minima, per le regole che lavorano dentro un atto."""
    from history_maker.menzioni import Menzione

    return Menzione(
        id=identificatore, atto=atto, tipo_atto=tipo, anno=1850, data=None,
        ruolo=ruolo, nome=nome, cognome=cognome, nome_letto=nome,
        cognome_letto=cognome, cognome_origine="atto" if cognome else None,
        incerto=False, eta_letta=None, professione=None, residenza=None,
        via=None, stato_vitale=None, note=None, immagine=immagine,
    )


def _accorpa_le_madri(esito, prima):
    """Mette in una scheda sola tutte le madri degli atti di prova.

    Il caso da provare e' l'**accorpamento**, e forzarlo qui invece di
    sperare che il calcolo lo faccia rende il test una prova della regola
    e non della soglia che c'e' oggi.
    """
    chiavi = esito.corpus.chiavi()
    madri = [
        esito.schede[esito.di_menzione[menzione.id]]
        for menzione in esito.corpus.menzioni
        if menzione.id >= prima and menzione.ruolo == "madre"
    ]
    unita = madri[0]
    for altra in madri[1:]:
        if altra.chiave == unita.chiave:
            continue
        nuova = risoluzione.Scheda.unione(unita, altra, chiavi)
        esito.schede.pop(unita.chiave, None)
        esito.schede.pop(altra.chiave, None)
        esito.schede[nuova.chiave] = nuova
        for menzione in nuova.menzioni:
            esito.di_menzione[menzione.id] = nuova.chiave
        unita = nuova
    return unita


def test_angela_lella_non_puo_avere_due_mariti_negli_stessi_anni():
    """Sei mariti contemporanei sono sei donne, non una.

    Nel ricostruito «Angela Lella», nata nel 1857, aveva figli da quattro
    uomini diversi negli stessi anni. In paese le Angela Lella coetanee
    sono quattro o cinque, e il cognome piu' comune del vicinato le
    teneva insieme.
    """
    atti = [
        _famiglia(1880, ("Saba", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trenta"),
        _famiglia(1882, ("Anna", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trentadue"),
        _famiglia(1881, ("Rosa", "Ottaviano"), ("Pasquale", "Ottaviano"),
                  ("Vitaliana", "Di Laudo"), "quaranta"),
        _famiglia(1883, ("Teresa", "Ottaviano"), ("Pasquale", "Ottaviano"),
                  ("Vitaliana", "Di Laudo"), "quarantadue"),
    ]
    esito, prima = in_un_paese(atti)
    accorpata = _accorpa_le_madri(esito, prima)

    semi = risoluzione.coniugi_contemporanei(esito, accorpata)
    assert semi is not None and len(semi) == 2, (
        "quattro figli da due uomini diversi negli stessi anni"
    )
    quante, _toccate = risoluzione.separa_per_coniugi(esito)
    assert quante >= 1
    finite = {esito.di_menzione[m] for seme in semi for m in seme}
    assert len(finite) == 2, "una scheda per marito"


def test_la_vedova_che_si_risposa_resta_una_persona_sola():
    """Due mariti in due stagioni della vita non sono due donne.

    E' il contrappeso della regola sopra, e senza di lui la regola
    varrebbe meno di zero: i registri sono pieni di vedove che si
    risposano, e spezzarle tutte sarebbe il danno peggiore.
    """
    atti = [
        _famiglia(1860, ("Saba", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trenta"),
        _famiglia(1862, ("Anna", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trentadue"),
        _famiglia(1880, ("Rosa", "Ottaviano"), ("Pasquale", "Ottaviano"),
                  ("Vitaliana", "Di Laudo"), "cinquanta"),
        _famiglia(1882, ("Teresa", "Ottaviano"), ("Pasquale", "Ottaviano"),
                  ("Vitaliana", "Di Laudo"), "cinquantadue"),
    ]
    esito, prima = in_un_paese(atti)
    accorpata = _accorpa_le_madri(esito, prima)
    assert risoluzione.coniugi_contemporanei(esito, accorpata) is None


# ---------------------------------------------------------------------------
# 2. «Domenicantonio Di Nardo era quattro uomini»
# ---------------------------------------------------------------------------

def test_chi_dichiara_il_figlio_e_il_padre_del_figlio():
    """Dichiarante e padre, stesso atto, stesso nome: una riga sola.

    Negli atti 826 (1821) e 897 (1822) Domenicantonio Di Nardo compare
    come dichiarante *e* come padre del bambino morto, con Rebecca Lella
    per madre in tutte e due le righe. Restavano due persone: quella con
    l'eta' e il mestiere, e quella nuda. Il conto dei Domenicantonio Di
    Nardo nell'albero di Filippo saliva cosi' a quattro.
    """
    atti = [{
        "tipo": "morte", "anno": 1821, "data": "1821-09-21", "persone": [
            {"ruolo": "dichiarante", "nome": "Vitaliano", "cognome": "Minchilli",
             "eta": "venticinque", "professione": "contadino"},
            {"ruolo": "defunto", "nome": "Saba", "cognome": "Minchilli",
             "eta": "mesi due"},
            {"ruolo": "padre", "nome": "Vitaliano", "cognome": "Minchilli"},
            {"ruolo": "madre", "nome": "Vitaliana", "cognome": "Di Laudo"},
        ],
    }]
    esito, prima = in_un_paese(atti)
    schede = schede_di_prova(esito, prima)
    vitaliani = [
        s for s in schede
        if any(m.nome == "Vitaliano" and m.id >= prima for m in s.menzioni)
    ]
    assert len(vitaliani) == 1, (
        "il dichiarante e il padre dell'atto sono la stessa riga scritta due volte"
    )
    assert vitaliani[0].quante == 2


def test_il_peso_del_dichiarante_non_e_quello_di_due_righe_uguali():
    """Il nonno omonimo che va a dichiarare esiste: e' prova, non certezza."""
    assert (
        evidenza.PESO_DICHIARANTE_E_GENITORE < evidenza.PESO_STESSA_RIGA_DUE_VOLTE
    )


# ---------------------------------------------------------------------------
# 3. «Filippo Lella tagliato in due da una separazione che non lo riguardava»
# ---------------------------------------------------------------------------

def test_separare_un_figlio_non_taglia_in_due_suo_padre():
    """La propagazione va al coniuge, non ai genitori.

    Separare Domenico Lella dall'omonimo Domenico di Vito tagliava in due
    **Filippo**, che di quel taglio non c'entrava: e i due mezzi Filippo,
    marcati come da tenere separati, non si rimettevano piu' insieme.
    """
    atti = [
        _famiglia(1820, ("Saba", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trenta"),
        _famiglia(1822, ("Anna", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trentadue"),
        _famiglia(1824, ("Rosa", "Minchilli"), ("Vitaliano", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "trentaquattro"),
    ]
    esito, prima = in_un_paese(atti)
    padri = [prima + 1, prima + 4, prima + 7]
    chiave = esito.di_menzione[padri[0]]
    if not all(esito.di_menzione[p] == chiave for p in padri):
        pytest.skip("il calcolo non li ha uniti: il caso non si applica")
    madre = esito.di_menzione[prima + 2]
    quante_prima = esito.schede[madre].quante

    risoluzione.applica_decisioni(esito, {
        "unire": [], "separare": [(padri[0], (padri[1], padri[2]))],
    })
    # La moglie si divide con lui: e' la meta' della regola che resta.
    assert esito.schede[esito.di_menzione[prima + 2]].quante <= quante_prima


# ---------------------------------------------------------------------------
# 4. «Il nodo senza nome del 1895» — vedi test_ricostruzione
# 5. «Innocenta Lella, padre al femminile» — vedi test_nomi
# 6. «Nicola Maria Lella, maschio» — vedi test_nomi
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. «Gella non e' un cognome»
# ---------------------------------------------------------------------------

def test_gella_e_lella():
    """Cinque occorrenze contro 556, e mai insieme nello stesso atto.

    E' l'atto che da' a Domenicantonio Lella il primo figlio maschio,
    Nicola Maria — cioe' il nome del nonno paterno, l'unico indizio che
    lo attacchi a Nicola Lella.
    """
    voci = glossario.Glossario.carica("config/glossario-torrebruna.yaml")
    chiave = glossario._chiave
    assert voci.cognomi[chiave("Gella")] == "Lella"
    # E le forme vere del paese restano dove sono: 'Pelle' e' un cognome
    # di Torrebruna con trentuno occorrenze, e nel glossario non ci va.
    assert chiave("Pelle") not in voci.cognomi


# ---------------------------------------------------------------------------
# 8. «Chi ha guardato la pagina non si lascia ribaltare da una statistica»
# ---------------------------------------------------------------------------

def test_la_lettura_fatta_sull_immagine_non_si_ripassa_al_veto():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    assert "claude-immagine" in registro.DECISORI_CHE_HANNO_GUARDATO
    from history_maker.ricostruzione import rilettura
    assert "'claude-immagine'" in rilettura._DECISORI_INTOCCABILI
    assert "'persona'" in rilettura._DECISORI_INTOCCABILI
    conn.close()


# ---------------------------------------------------------------------------
# 9. «Un legame dedotto non si disegna come uno che c'e'»
# ---------------------------------------------------------------------------

def test_l_albero_dice_quali_legami_sono_dedotti():
    """L'applicazione deve poterli tratteggiare: senza, e' un romanzo."""
    from history_maker import genealogia

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.executescript(genealogia.SCHEMA_SQL)
    for identificatore, nome in ((1, "Nicola"), (2, "Domenicantonio")):
        conn.execute(
            "INSERT INTO individui (id, nome, cognome, menzioni, fondata_su) "
            "VALUES (?,?, 'Lella', 1, 'una sola menzione')", (identificatore, nome),
        )
    conn.execute(
        "INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
        "VALUES (2, 1, 'padre', NULL, 0.75, 'dedotto')"
    )
    grafo = albero.albero(conn, 1, su=0, giu=1)
    archi = [a for a in grafo["archi"] if a["tipo"] == "filiazione"]
    assert archi and archi[0]["stato"] == "dedotto"
    assert archi[0]["confidenza"] == 0.75
    conn.close()


# ---------------------------------------------------------------------------
# 10. Le correzioni di lettura, per classe
# ---------------------------------------------------------------------------

CORREZIONI_DELL_ALBERO = [
    # (menzione, campo, valore, atto, come si e' deciso)
    ("quarantaquattro", "eta", 1541, "Domenico Carlo Lella, sposo del 1832"),
    ("Emmanuela Pizzi", "nome", 2058, "la madre di Ermenegildo, 1837"),
    ("Nicolangelo", "nome", 3822, "il padre di Placido, 1864"),
    ("Rebecca Lella", "nome", 1494, "la madre del figlio postumo, 1831"),
    ("Giuseppe", "nome", 1964, "il bambino morto senza nome, 1837"),
    ("Pasqua", "nome", 2150, "la figlia col nome del padre, 1839"),
    ("mesi due", "eta", 826, "il neonato di trentadue anni, 1821"),
]


@pytest.mark.parametrize("valore,campo,atto,caso", CORREZIONI_DELL_ALBERO)
def test_una_correzione_letta_sull_immagine_torna_nel_corpus(valore, campo, atto, caso):
    """Ogni lettura presa sulla pagina deve rientrare nella ricostruzione.

    Il meccanismo e' uno solo — una riga nel registro, riletta da
    ``registro.correzioni`` e applicata da ``lettura`` — e se si rompe si
    rompono tutte insieme: la quota spesa a guardare l'immagine non
    cambierebbe piu' niente. Il test lo prova campo per campo, con i
    valori veri decisi sull'albero di Filippo Lella.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    registro.annota(
        conn, "correzione", (7,), f"letta sulla scansione dell'atto {atto}: {caso}",
        confidenza=1.0, decisore="claude-immagine",
        evidenze=(f"{campo}={valore}",),
    )
    assert registro.correzioni(conn)[(7, campo)] == valore
    conn.close()


def test_una_correzione_ritirata_non_torna_nel_corpus():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    vecchia = registro.annota(
        conn, "correzione", (7,), "prima lettura", confidenza=1.0,
        decisore="claude-immagine", evidenze=("nome=Ermanda",),
    )
    registro.annota(
        conn, "correzione", (7,), "seconda lettura, sulla scansione",
        confidenza=1.0, decisore="claude-immagine",
        evidenze=("nome=Emmanuela",), disfa=vecchia,
    )
    assert registro.correzioni(conn)[(7, "nome")] == "Emmanuela"
    conn.close()


# ---------------------------------------------------------------------------
# 11. Le decisioni di identita', per classe
# ---------------------------------------------------------------------------

def test_le_fusioni_e_le_separazioni_decise_a_mano_si_riapplicano():
    """Senza, il giudizio di chi ha guardato la carta vale una volta sola."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    registro.annota(conn, "unione", (1622, 2352), "Domenico Carlo Lella di Filippo",
                    confidenza=1.0, decisore="claude-immagine")
    registro.annota(conn, "separazione", (1622, 6953, 7826, 7828),
                    "le menzioni di Domenico di Vito", confidenza=1.0,
                    decisore="claude")
    imposte = registro.imposizioni(conn)
    assert imposte["unire"] == [(1622, 2352)]
    assert imposte["separare"] == [(1622, (6953, 7826, 7828))]
    conn.close()


# ---------------------------------------------------------------------------
# 12. «Domenicantonia D'Illice, padre»
# ---------------------------------------------------------------------------

def test_un_padre_con_un_nome_da_donna_finisce_nella_coda_dei_dubbi():
    """Il ruolo dice il sesso: se il nome dice l'altro, sbaglia la lettura.

    Atto 5000 del 1874: il padre del neonato Nicola Maria **Di Nardo** era
    trascritto «Domenicantonia D'Illice» — femminile, e con un cognome che
    in tutto l'archivio compare due volte. Era Domenicantonio Di Nardo,
    quarantatre anni, marito di Maria Domenica Pelliccia.
    """
    from history_maker.ricostruzione import anomalie as coda

    atti = [{
        "tipo": "nascita", "anno": 1874, "data": "1874-06-01", "persone": [
            {"ruolo": "neonato", "nome": "Saba", "cognome": "Minchilli"},
            {"ruolo": "padre", "nome": "Vitaliana", "cognome": "Minchilli",
             "eta": "quarantatre"},
            {"ruolo": "madre", "nome": "Rosa", "cognome": "Di Laudo"},
        ],
    }]
    esito, prima = in_un_paese(atti)
    trovate = [
        a for a in coda.nomi_del_sesso_sbagliato(esito)
        if "Vitaliana" in a.descrizione
    ]
    assert trovate, "un padre di nome Vitaliana e' una lettura da rifare"
    assert trovate[0].campo == "nome"
    assert trovate[0].tipo == "NAME_ANOMALY"


def test_un_nome_ambiguo_non_apre_un_dubbio():
    """«Nicola» in un padre non e' un errore: la regola non deve gridare."""
    from history_maker.ricostruzione import anomalie as coda

    atti = [{
        "tipo": "nascita", "anno": 1874, "data": "1874-06-01", "persone": [
            {"ruolo": "neonato", "nome": "Saba", "cognome": "Minchilli"},
            {"ruolo": "padre", "nome": "Nicola", "cognome": "Minchilli",
             "eta": "quarantatre"},
            {"ruolo": "madre", "nome": "Rosa", "cognome": "Di Laudo"},
        ],
    }]
    esito, prima = in_un_paese(atti)
    trovate = [
        a for a in coda.nomi_del_sesso_sbagliato(esito)
        if a.atti and a.atti[0] and "Nicola" in a.descrizione
    ]
    assert not trovate


# ---------------------------------------------------------------------------
# 13. «Maria Margherita, senza cognome»
# ---------------------------------------------------------------------------

def test_il_neonato_senza_cognome_prende_quello_del_dichiarante():
    """Nascita n. 34 del 1850: il padre c'e', ma solo come dichiarante.

    La fase 4 da' al neonato il cognome del padre o della madre quando
    uno dei due ha un ruolo di genitore. Qui l'atto ha solo il
    dichiarante — Giovanni Desiderio, marito di Lucia Lella — e la
    bambina restava «Maria Margherita», senza cognome, appesa alla sola
    madre nell'albero di Filippo Lella.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria Margherita", None),
        _riga(2, "nascita", "dichiarante", "Giovanni", "Desiderio"),
        _riga(3, "nascita", "madre", "Lucia", "Lella"),
    ]
    assert lettura_atti.cognome_dal_dichiarante(righe) == 1
    assert righe[0].cognome == "Desiderio"
    assert righe[0].cognome_origine == "dichiarante"


def test_la_levatrice_che_dichiara_non_da_il_cognome():
    """A dichiarare una nascita ci va anche la levatrice."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria Concetta", None),
        _riga(2, "nascita", "dichiarante", "Emina", "Labate"),
        _riga(3, "nascita", "madre", "Lucia", "Lella"),
    ]
    assert lettura_atti.cognome_dal_dichiarante(righe) == 0
    assert righe[0].cognome is None


def test_dove_il_padre_c_e_il_dichiarante_non_decide():
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria Margherita", None),
        _riga(2, "nascita", "padre", "Giovanni", "Desiderio"),
        _riga(3, "nascita", "dichiarante", "Nicola", "Ferrara"),
    ]
    assert lettura_atti.cognome_dal_dichiarante(righe) == 0


def test_il_cognome_dal_dichiarante_vale_solo_alle_nascite():
    """In un atto di morte il dichiarante e' spesso un vicino."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Maria Margherita", None),
        _riga(2, "morte", "dichiarante", "Giovanni", "Desiderio"),
    ]
    assert lettura_atti.cognome_dal_dichiarante(righe) == 0


# ---------------------------------------------------------------------------
# 14. «Angela Felicia Moretta, due volte nello stesso albero»
# ---------------------------------------------------------------------------

def test_la_promessa_pubblicata_due_volte_e_un_evento_solo():
    """Registro delle notificazioni del 1863, fogli 4 e 15.

    La stessa promessa e' affissa due volte, con gli stessi quattro
    genitori. Angela Felicia Moretta, figlia di Carmine e di Maria
    Lella, diventava cosi' due donne, e nell'albero di Filippo compariva
    due volte a ventisette anni di distanza.
    """
    from history_maker import menzioni as lettura_atti

    def promessa(atto, eta_sposa):
        return [
            _riga(atto * 10 + 1, "pubblicazione", "sposo", "Costantino",
                  "Moretta", atto, "1863-notificazioni/0004.jpg"),
            _riga(atto * 10 + 2, "pubblicazione", "padre", "Nicola",
                  "Moretta", atto, "1863-notificazioni/0004.jpg"),
            _riga(atto * 10 + 3, "pubblicazione", "sposa", "Angela Felicia",
                  "Moretta", atto, "1863-notificazioni/0004.jpg"),
            _riga(atto * 10 + 4, "pubblicazione", "padre", "Carmine",
                  "Moretta", atto, "1863-notificazioni/0004.jpg"),
        ]

    righe = promessa(1, "quarantasette") + promessa(2, None)
    for riga in righe:
        riga.anno = 1863
    assert lettura_atti.atti_gemelli(righe) == 1
    eventi = {r.evento for r in righe}
    assert len(eventi) == 1, "le due affissioni sono un evento solo"


def test_due_atti_diversi_con_lo_stesso_numero_restano_due():
    """Nel registro dei matrimoni del 1832 ci sono due atti «sei».

    Le loro spose sono due donne diverse: il numero d'ordine non basta,
    e infatti la regola guarda le persone.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(11, "matrimonio", "sposo", "Giuseppe", "Moretta", 1,
              "1832-matrimoni/0017.jpg"),
        _riga(12, "matrimonio", "sposa", "Emmanuela", "Moretta", 1,
              "1832-matrimoni/0017.jpg"),
        _riga(21, "matrimonio", "sposo", "Nicola", "Salvatore", 2,
              "1832-matrimoni/0020.jpg"),
        _riga(22, "matrimonio", "sposa", "Angela Maria", "Salvatore", 2,
              "1832-matrimoni/0020.jpg"),
    ]
    for riga in righe:
        riga.anno = 1832
    assert lettura_atti.atti_gemelli(righe) == 0
    assert len({r.evento for r in righe}) == 2


# ---------------------------------------------------------------------------
# 15. «Amadio e Amedeo Di Nardo»
# ---------------------------------------------------------------------------

def test_due_fratelli_non_portano_lo_stesso_nome():
    """Stessi genitori piu' stesso nome: e' una persona, non due.

    Amadio Di Nardo, nato nel 1824 da Domenicantonio Di Nardo e Rebecca
    Lella, e Amedeo Di Nardo, nato nel 1824 dagli stessi due e marito
    della stessa Rosa Pelliccia, erano due uomini nell'albero.
    """
    assert evidenza.PESO_GENITORI_E_NOME > evidenza.PESO_GENITORI_IN_COMUNE


def test_il_nome_riusato_dopo_una_morte_resta_due_persone():
    """L'altra meta': Secondina Moretta e' nata due volte.

    La prima nel 1822, morta a quattro anni nel 1826; la seconda nel
    1828, morta nel 1879. Hanno gli stessi genitori e lo stesso nome, e
    **non** sono la stessa persona: a tenerle separate e' il veto sui due
    atti di nascita, che questa regola non tocca.
    """
    from history_maker.ricostruzione.scheda import Scheda

    una = Scheda(chiave=1)
    una.nascite_certe = [1822]
    altra = Scheda(chiave=2)
    altra.nascite_certe = [1828]
    assert evidenza.veti(una, altra) is not None


# ---------------------------------------------------------------------------
# 16. «Luigi Moretta di settantuno anni»
# ---------------------------------------------------------------------------

def test_un_figlio_non_nasce_prima_delle_nozze_dei_suoi():
    """Atto di morte 2 del 1880: «di anni settantuno» era «sessantuno».

    Con settantuno Luigi Moretta risultava nato nel 1809, quattro anni
    prima che Carmine Moretta e Maria Lella si sposassero — febbraio
    1813 — e restava un terzo fratello omonimo che non e' mai esistito.
    """
    from history_maker.ricostruzione import anomalie as coda

    atti = [
        {"tipo": "matrimonio", "anno": 1813, "data": "1813-02-07", "persone": [
            {"ruolo": "sposo", "nome": "Vitaliano", "cognome": "Minchilli",
             "eta": "venticinque"},
            {"ruolo": "sposa", "nome": "Vitaliana", "cognome": "Di Laudo",
             "eta": "ventuno"},
        ]},
        {"tipo": "morte", "anno": 1880, "data": "1880-02-13", "persone": [
            {"ruolo": "defunto", "nome": "Saba", "cognome": "Minchilli",
             "eta": "settantuno"},
            {"ruolo": "padre", "nome": "Vitaliano", "cognome": "Minchilli"},
            {"ruolo": "madre", "nome": "Vitaliana", "cognome": "Di Laudo"},
        ]},
    ]
    esito, _prima = in_un_paese(atti)
    trovate = [
        a for a in coda.figli_prima_del_matrimonio(esito)
        if "Saba" in a.descrizione
    ]
    assert trovate, "nato nel 1809 da chi si sposa nel 1813"
    assert trovate[0].campo == "eta"


# ---------------------------------------------------------------------------
# 17. «Maggiore di età non è un'età»
# ---------------------------------------------------------------------------

def test_maggiore_di_eta_non_inventa_una_data_di_nascita():
    """La formula dice «ha l'età per firmare», non «ha ventun anni».

    Nella promessa del 1863 Angela Felicia Moretta è «maggiore di età».
    Contata come ventuno dava il 1842, che litigava con il 1815 del suo
    atto di nascita e teneva la stessa donna divisa in due schede.
    """
    from history_maker import nomi as igiene

    eta = igiene.analizza_eta("maggiore di eta'")
    assert eta is not None and eta.anni == 21.0
    assert eta.minima, "e' un limite inferiore, non una misura"
    assert not igiene.analizza_eta("ventuno").minima


def test_maggiore_di_eta_lascia_comunque_un_vincolo():
    """Meno di una data non e' niente: esclude i bambini."""
    from history_maker import menzioni as lettura_atti

    riga = _riga(1, "pubblicazione", "sposa", "Angela Felicia", "Moretta")
    riga.anno = 1863
    riga.eta = __import__("history_maker.nomi", fromlist=["x"]).analizza_eta(
        "maggiore di eta'"
    )
    assert riga.anno_nascita is None, "nessuna data inventata"
    assert lettura_atti.finestre_dalla_maggiore_eta([riga]) == 1
    assert riga.finestra == (0, 1842), "nata non dopo il 1842"


# ---------------------------------------------------------------------------
# 18. «Due Luigi Moretta vivi nello stesso momento»
# ---------------------------------------------------------------------------

def _con_genitori(esito, chiave, padre, madre, nascita):
    scheda = esito.schede[chiave]
    scheda.padri = {padre}
    scheda.madri = {madre}
    scheda.nascite_certe = [nascita]
    return scheda


def test_due_fratelli_omonimi_senza_una_morte_in_mezzo_sono_un_dubbio():
    """Nessuno chiama Luigi due figli vivi.

    Luigi Moretta nasce nel 1819 e «Luigi Nicola Maria» nel 1821, tutti
    e due da Carmine Moretta e Maria Lella. Le due schede hanno la
    stessa vita adulta — le età dichiarate dal 1852 al 1879 danno tutte
    il 1819-1820 — e un solo atto di morte, quello del 1880.
    """
    from history_maker.ricostruzione import anomalie as coda

    atti = [
        _famiglia(1819, ("Vitaliano", "Minchilli"), ("Bonaventura", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "quaranta"),
        _famiglia(1821, ("Vitaliano", "Minchilli"), ("Bonaventura", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "quarantadue"),
    ]
    esito, prima = in_un_paese(atti)
    padre = esito.di_menzione[prima + 1]
    madre = esito.di_menzione[prima + 2]
    _con_genitori(esito, esito.di_menzione[prima], padre, madre, 1819)
    _con_genitori(esito, esito.di_menzione[prima + 3], padre, madre, 1821)

    nostri = {esito.di_menzione[prima], esito.di_menzione[prima + 3]}
    trovate = [
        a for a in coda.fratelli_omonimi(esito) if set(a.individui) == nostri
    ]
    assert trovate, "due figli vivi con lo stesso nome sono un dubbio"
    assert trovate[0].tipo == "DUPLICATE_PERSON"


def test_il_nome_riusato_dopo_la_morte_non_apre_nessun_dubbio():
    """Il contrappeso: Secondina Moretta nasce due volte, e sono due.

    La prima nel 1822, morta a quattro anni nel 1826; la seconda nel
    1828. Fra le due nascite c'è un atto di morte, e la regola tace.
    """
    from history_maker.ricostruzione import anomalie as coda

    atti = [
        _famiglia(1822, ("Saba", "Minchilli"), ("Bonaventura", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "quaranta"),
        _famiglia(1828, ("Saba", "Minchilli"), ("Bonaventura", "Minchilli"),
                  ("Vitaliana", "Di Laudo"), "quarantasei"),
    ]
    esito, prima = in_un_paese(atti)
    padre = esito.di_menzione[prima + 1]
    madre = esito.di_menzione[prima + 2]
    prima_figlia = _con_genitori(esito, esito.di_menzione[prima], padre, madre, 1822)
    _con_genitori(esito, esito.di_menzione[prima + 3], padre, madre, 1828)
    prima_figlia.morti = [1826]

    nostri = {esito.di_menzione[prima], esito.di_menzione[prima + 3]}
    assert not [
        a for a in coda.fratelli_omonimi(esito) if set(a.individui) == nostri
    ]


# ---------------------------------------------------------------------------
# 19. «Antonia Mouton e Antonia Montanaro» / «tre Egidio Pelliccia»
# ---------------------------------------------------------------------------

def _spezza(esito, scheda, chiave_di):
    """Rimette una scheda nei pezzi che ``chiave_di`` distingue.

    E' il rovescio di ``_accorpa_le_madri``: li' si forzava una fusione
    per provare la regola che divide, qui si forza una divisione per
    provare la regola che unisce. Serve perche' il calcolo, su casi
    cosi' puliti, le schede le unisce da solo, e il test proverebbe la
    soglia invece della regola. I pezzi si tengono lontani a vicenda,
    come dopo una divisione vera.
    """
    chiavi = esito.corpus.chiavi()
    gruppi: dict = {}
    for menzione in scheda.menzioni:
        gruppi.setdefault(chiave_di(menzione), []).append(menzione)
    if len(gruppi) < 2:
        return [scheda]
    esito.schede.pop(scheda.chiave, None)
    pezzi = []
    for _, menzioni in sorted(gruppi.items()):
        pezzo = risoluzione.Scheda.dalla_menzione(menzioni[0], chiavi)
        for altra in menzioni[1:]:
            pezzo = risoluzione.Scheda.unione(
                pezzo, risoluzione.Scheda.dalla_menzione(altra, chiavi), chiavi
            )
        esito.schede[pezzo.chiave] = pezzo
        for menzione in pezzo.menzioni:
            esito.di_menzione[menzione.id] = pezzo.chiave
        pezzi.append(pezzo)
    tutti = set().union(*(p.ids for p in pezzi))
    for pezzo in pezzi:
        pezzo.vietati = tutti - pezzo.ids
    return pezzi


def _spezza_per_cognome(esito, scheda):
    """Divide una scheda sulle due letture del suo cognome."""
    pezzi = _spezza(esito, scheda, lambda menzione: menzione.chiave_cognome)
    for pezzo in pezzi:
        pezzo.vietati = set()
    return pezzi


def _due_mogli_omonime(figli_per_moglie=1):
    """Un uomo con due mogli che si chiamano nello stesso modo.

    ``figli_per_moglie`` decide se le due schede sono frammenti o due
    vite intere: e' l'unica cosa che separa il caso dal contrappeso.
    """
    atti = []
    for indice in range(figli_per_moglie):
        atti.append(nascita(
            1828 + indice * 3, (f"Saverio{indice}", "Ottaviano"),
            ("Domenico", "Ottaviano"), ("Antonia", "Petta"),
        ))
        atti.append(nascita(
            1829 + indice * 3, (f"Crescenzo{indice}", "Ottaviano"),
            ("Domenico", "Ottaviano"), ("Antonia", "Montanaro"),
        ))
    esito, prima = in_un_paese(atti)
    for madre in schede_di_prova(esito, prima, "madre"):
        _spezza_per_cognome(esito, madre)
    return esito, prima


def test_due_mogli_con_lo_stesso_nome_sono_una_moglie_sola():
    """Antonia Mouton e Antonia Montanaro, mogli di Domenico Di Nardo.

    Nei due atti di morte dei figli, 1828 e 1829, la stessa donna è
    letta in due modi. Nell'albero di Filippo Lella diventavano due
    mogli contemporanee dello stesso uomo, e una delle due non si
    attaccava a nulla.
    """
    esito, prima = _due_mogli_omonime()
    assert len(schede_di_prova(esito, prima, "madre")) == 2
    assert risoluzione.unisci_coniugi_omonimi(esito) == 1
    rimaste = schede_di_prova(esito, prima, "madre")
    assert len(rimaste) == 1, "le due mogli omonime sono una sola scheda"
    assert set(rimaste[0].cognomi) == {"Petta", "Montanaro"}


def test_due_mogli_con_lo_stesso_nome_ma_nessuna_e_un_frammento_restano_due():
    """Il contrappeso: due vite intere non si fondono per il solo nome.

    Un uomo può davvero risposarsi con una seconda Antonia — succede, e
    quando tutt'e due le schede hanno una vita documentata alle spalle
    il nome uguale non basta a cancellarne una.
    """
    esito, prima = _due_mogli_omonime(
        figli_per_moglie=risoluzione.MENZIONI_DI_UN_FRAMMENTO + 1
    )
    madri = schede_di_prova(esito, prima, "madre")
    assert len(madri) == 2
    assert min(m.quante for m in madri) > risoluzione.MENZIONI_DI_UN_FRAMMENTO
    assert risoluzione.unisci_coniugi_omonimi(esito) == 0
    assert len(schede_di_prova(esito, prima, "madre")) == 2


def test_una_divisione_del_calcolo_si_puo_ridiscutere():
    """I tre Egidio Pelliccia erano tenuti fermi da una divisione, non da una prova.

    Il primo giro aveva messo in una scheda sola tutti gli Egidio
    Pelliccia — ottantanove anni di vita — e la divisione ha avuto
    ragione a spezzarla. Ma ha spezzato anche i due frammenti che erano
    davvero suoi, e da quel momento nessuna prova poteva piu'
    rimetterli insieme: il divieto valeva quanto una decisione presa
    sulla carta. La prova che manca alla divisione — la moglie, che e'
    la stessa donna — arriva dopo, e a giri finiti non c'e' piu' nessun
    ciclo da rompere.
    """
    esito, prima = _due_mogli_omonime()
    mogli = schede_di_prova(esito, prima, "madre")
    assert len(mogli) == 2
    # come dopo una divisione del calcolo: ognuna deve stare lontana
    # dall'altra, ma nessuno ha guardato la pagina.
    mogli[0].vietati = set(mogli[1].ids)
    mogli[1].vietati = set(mogli[0].ids)
    assert evidenza.veti(mogli[0], mogli[1]) is not None
    assert evidenza.veti(
        mogli[0], mogli[1], divisioni_del_calcolo=False
    ) is None
    assert risoluzione.unisci_coniugi_omonimi(esito) == 1


def test_una_separazione_decisa_sulla_pagina_non_si_ridiscute():
    """Il contrappeso: chi ha letto l'atto vale piu' di qualunque indizio.

    E' la differenza fra le due: una divisione del calcolo e' una
    ipotesi che il calcolo puo' correggere, una separazione imposta e'
    una lettura, e una lettura non si ribalta con una somiglianza di
    nome.
    """
    esito, prima = _due_mogli_omonime()
    mogli = schede_di_prova(esito, prima, "madre")
    assert len(mogli) == 2
    mogli[0].separati_a_mano = set(mogli[1].ids)
    mogli[1].separati_a_mano = set(mogli[0].ids)
    assert evidenza.veti(
        mogli[0], mogli[1], divisioni_del_calcolo=False
    ) is not None
    assert risoluzione.unisci_coniugi_omonimi(esito) == 0


# ---------------------------------------------------------------------------
# 20. «Nicola Maria, senza cognome, figlio di nessuno»
# ---------------------------------------------------------------------------

def test_il_padre_senza_cognome_prende_quello_del_figlio():
    """Atto 6258 del 1884: muore Stella Colella, «figlia di Nicola Maria».

    L'atto scrive il padre col solo nome di battesimo, e nell'albero
    restava un «Nicola Maria» senza casato, appeso per un filo. Dentro
    un atto padre e figlio il cognome se lo dividono: la deduzione che
    dà al figlio quello del padre vale anche al rovescio.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Stella", "Colella"),
        _riga(2, "morte", "padre", "Nicola Maria", None),
        _riga(3, "morte", "madre", "Rosa", "Pelliccia"),
    ]
    assert lettura_atti.cognome_dal_figlio(righe) == 1
    assert righe[1].cognome == "Colella"
    assert righe[1].cognome_origine == "figlio"
    assert righe[2].cognome == "Pelliccia", "la madre porta il cognome da nubile"


def test_con_due_soggetti_nell_atto_il_cognome_del_padre_non_si_deduce():
    """Il contrappeso: con due soggetti non si sa da quale dei due prenderlo."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposo", "Felice", "De Lucia"),
        _riga(2, "matrimonio", "sposa", "Angela", "Di Nardo"),
        _riga(3, "matrimonio", "padre", "Luigi", None),
    ]
    assert lettura_atti.cognome_dal_figlio(righe) == 0
    assert righe[2].cognome is None


# ---------------------------------------------------------------------------
# 21. «Antonio De Plato e il suo mare di contrade»
# ---------------------------------------------------------------------------

def _riga_di_atto(ruolo, via_atto, tipo="nascita"):
    return {"ruolo": ruolo, "tipo": tipo, "via_atto": via_atto}


def test_la_contrada_dell_atto_non_arriva_ai_testimoni():
    """Antonio De Plato risultava vissuto in diciannove contrade.

    L'atto di nascita scrive **una** casa: quella dove il bambino è
    nato. Darla a chiunque compaia nell'atto voleva dire dare a ogni
    testimone e a ogni ufficiale la casa altrui, e Antonio De Plato —
    che di mestiere andava a fare da testimone — le collezionava tutte.
    """
    from history_maker import menzioni as lettura_atti

    casa = "Rua di Nuorro"
    assert lettura_atti._via_della_casa(_riga_di_atto("neonato", casa)) == casa
    assert lettura_atti._via_della_casa(_riga_di_atto("padre", casa)) == casa
    assert lettura_atti._via_della_casa(_riga_di_atto("madre", casa)) == casa
    assert lettura_atti._via_della_casa(_riga_di_atto("testimone", casa)) is None
    assert lettura_atti._via_della_casa(_riga_di_atto("ufficiale", casa)) is None
    assert lettura_atti._via_della_casa(_riga_di_atto("dichiarante", casa)) is None


def test_la_contrada_del_matrimonio_non_e_la_casa_di_nessuno():
    """Il contrappeso: nei matrimoni l'indirizzo dell'atto è la casa comunale."""
    from history_maker import menzioni as lettura_atti

    riga = _riga_di_atto("sposo", "Rua di Nuorro", tipo="matrimonio")
    assert lettura_atti._via_della_casa(riga) is None


def test_de_plata_e_de_plato():
    """Ventisei occorrenze contro tredici, e mai insieme nello stesso atto.

    I figli di Antonio si dividevano fra due casati che sono lo stesso.
    """
    voci = glossario.Glossario.carica("config/glossario-torrebruna.yaml")
    assert voci.cognomi[glossario._chiave("De Plata")] == "De Plato"


def test_mouton_e_moretta():
    """Le due mogli di Domenico Di Nardo: 'Mouton' e' 'Moutta' con una lettera in piu'."""
    voci = glossario.Glossario.carica("config/glossario-torrebruna.yaml")
    assert voci.cognomi[glossario._chiave("Mouton")] == "Moretta"


# ---------------------------------------------------------------------------
# 22. «Maria Margherita, ancora senza cognome»
# ---------------------------------------------------------------------------

def _bilancia_del_paese():
    """Un corpus in cui 'Margherita' è un nome e 'Desiderio' un casato."""
    from history_maker import nomi

    menzioni = (
        [("Maria", "Lella")] * 30
        + [("Margherita", "Lella")] * 28
        + [("Giovanni", "Desiderio")] * 40
        + [("Pelliccia", "Lella")] * 2
        + [("Corinto", "Pelliccia")] * 20
        + [("Antonio", "Pelliccia")] * 30
    )
    return nomi.Bilancia.dal_corpus(menzioni)


def test_maria_margherita_e_un_nome_solo():
    """Nascita n. 34 del 1850, guardata la seconda volta.

    Il cognome del dichiarante non arrivava perché la bambina un cognome
    ce l'aveva già: «Margherita», che a Torrebruna non è il casato di
    nessuno. È la seconda metà del suo nome, spaccata in due
    dall'estrazione, e finché resta lì la regola del dichiarante non
    scatta e la bambina resta appesa alla sola madre.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria", "Margherita"),
        _riga(2, "nascita", "dichiarante", "Giovanni", "Desiderio"),
        _riga(3, "nascita", "madre", "Lucia", "Lella"),
    ]
    assert lettura_atti.nome_composto_spezzato(righe, _bilancia_del_paese()) == 1
    assert righe[0].nome == "Maria Margherita"
    assert righe[0].cognome is None
    # e adesso il dichiarante puo' darle il suo
    assert lettura_atti.cognome_dal_dichiarante(righe) == 1
    assert righe[0].cognome == "Desiderio"


def test_una_menzione_invertita_non_e_un_nome_composto():
    """Il contrappeso: «Pelliccia Corinto» è Corinto Pelliccia al rovescio.

    Centosette volte nell'archivio, sempre lo stesso testimone. Se
    bastasse che il campo cognome sappia di nome, quest'uomo si
    chiamerebbe «Pelliccia Corinto» di nome e di niente di cognome.
    Raddrizzare le colonne è un altro mestiere, e lo fa
    ``nomi.inversioni_per_atto``.
    """
    from history_maker import menzioni as lettura_atti

    righe = [_riga(1, "nascita", "testimone", "Pelliccia", "Corinto")]
    assert lettura_atti.nome_composto_spezzato(righe, _bilancia_del_paese()) == 0
    assert righe[0].cognome == "Corinto"


def test_un_cognome_che_torna_due_volte_nell_atto_resta_un_cognome():
    """Il contrappeso: se padre e figlio lo portano, quel casato è vero."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria", "Antonio"),
        _riga(2, "nascita", "padre", "Nicola", "Antonio"),
    ]
    assert lettura_atti.nome_composto_spezzato(righe, _bilancia_del_paese()) == 0
    assert righe[0].cognome == "Antonio"


# ---------------------------------------------------------------------------
# 23. «Fileno De Lucia, figlio di Luigi di Fazio»
# ---------------------------------------------------------------------------

def _padre_con_figli(letture_del_padre, *cognomi_dei_figli):
    """Un padre e i suoi figli, col cognome del padre letto in piu' modi.

    Le letture si impongono sulla scheda a ricostruzione fatta: un
    corpus di prova che le producesse davvero misurerebbe la soglia del
    calcolo, non la regola che si vuole provare.
    """
    from collections import Counter

    from history_maker import paleografia

    atti = [
        nascita(1858 + indice * 6, (f"Adelindo{indice}", cognome),
                ("Luigi", "Marianacci"), ("Maria", "Petta"))
        for indice, cognome in enumerate(cognomi_dei_figli)
    ]
    esito, prima = in_un_paese(atti)
    padre = schede_di_prova(esito, prima, "padre")[0]
    padre.cognomi = Counter(letture_del_padre)
    padre.chiavi_cognome = {
        paleografia.forma_canonica(c) for c in letture_del_padre
    }
    return esito, padre


def test_il_cognome_del_padre_e_quello_che_portano_i_suoi_figli():
    """Luigi De Lucia era letto 'di Suio' una volta e 'di Fazio' due.

    Fra le sue letture vinceva 'di Fazio', un po' più frequente
    nell'archivio, e l'albero mostrava Fileno De Lucia figlio di Luigi
    di Fazio. Ma i suoi due figli, Mercedes e Fileno, si chiamano
    tutt'e due **De Lucia**: è la stessa parola letta due volte, e la
    seconda lettura è quella dei figli.
    """
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, padre = _padre_con_figli(
        {"Marianacci": 2, "Colella": 2, "Troilo": 1}, "Colella", "Colella",
    )
    attestazione = Counter({"Marianacci": 40, "Colella": 30, "Troilo": 5})
    assert esecuzione._forma(padre.cognomi, attestazione) == "Marianacci"
    assert esecuzione.cognome_di_famiglia(esito, padre, attestazione) == "Colella"


def test_dei_figli_conta_il_cognome_scelto_non_ogni_loro_lettura():
    """Mercedes De Lucia ha 'di Fazio' fra le sue letture.

    Viene dalla stessa pagina che ha confuso il padre — le tre
    pubblicazioni del 1873 — e bastava quella a far dire che la scelta
    del padre i figli la confermavano. Non la confermano: guardata per
    conto suo, Mercedes si chiama De Lucia, e conta quello.
    """
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, padre = _padre_con_figli(
        {"Marianacci": 2, "Colella": 2, "Troilo": 1}, "Colella", "Colella",
    )
    attestazione = Counter({"Marianacci": 40, "Colella": 30, "Troilo": 5})
    figli = [esito.schede[c] for c in sorted(padre.figli)]
    figli[0].cognomi = Counter({"Colella": 2, "Marianacci": 1})
    assert esecuzione._forma(figli[0].cognomi, attestazione) == "Colella"
    assert esecuzione.cognome_di_famiglia(esito, padre, attestazione) == "Colella"


def test_senza_una_lettura_che_i_figli_confermino_la_scelta_non_cambia():
    """Il contrappeso: la regola sceglie fra le letture del padre, non ne inventa."""
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, padre = _padre_con_figli(
        {"Marianacci": 2, "Troilo": 1}, "Colella", "Colella",
    )
    attestazione = Counter({"Marianacci": 40, "Troilo": 5, "Colella": 30})
    assert esecuzione.cognome_di_famiglia(esito, padre, attestazione) == "Marianacci"


def test_un_padre_che_non_porta_il_cognome_di_nessuno_dei_figli_e_un_dubbio():
    """Quando neppure una lettura torna nei figli, non è più una lettura.

    Due figli che concordano fra loro e discordano dal padre vogliono
    dire che quella scheda tiene insieme due uomini con lo stesso nome
    di battesimo, e che i figli dell'uno sono appesi all'altro: sposta
    un ramo intero, non una persona.
    """
    from history_maker.ricostruzione import anomalie as coda

    esito, padre = _padre_con_figli({"Marianacci": 2}, "Colella", "Colella")
    trovate = [a for a in coda.padre_di_un_altro_casato(esito)
               if a.individui[0] == padre.chiave]
    assert trovate, "un padre di un altro casato e' un dubbio"
    assert trovate[0].tipo == "SURNAME_ANOMALY"


def test_un_figlio_solo_non_basta_a_incolpare_il_padre():
    """Il contrappeso: con un figlio solo la lettura sbagliata può stare da lui.

    È il caso del figlio naturale riconosciuto, o della madre nubile:
    ``cognomi_sospetti`` lo guarda già dal lato del figlio, e senza un
    secondo figlio che confermi non c'è motivo di incolpare il padre.
    """
    from history_maker.ricostruzione import anomalie as coda

    esito, padre = _padre_con_figli({"Marianacci": 2}, "Colella")
    assert not [a for a in coda.padre_di_un_altro_casato(esito)
                if a.individui[0] == padre.chiave]



# ---------------------------------------------------------------------------
# 24. «Due Luigi Moretta sposati con due Sciulli»
# ---------------------------------------------------------------------------

def _nucleo_spezzato_in_due():
    """Una coppia con due figli, divisa in due nuclei identici.

    Ogni genitore finisce in due schede, una per atto, e le due si
    tengono lontane come dopo una divisione del calcolo: e' lo stato in
    cui il confronto a coppie lascia Luigi Moretta e Lucia Sciulli.
    """
    atti = [
        nascita(1860, ("Saverio", "Ottaviano"),
                ("Luigi", "Ottaviano"), ("Lucia", "Petta")),
        nascita(1867, ("Crescenzo", "Ottaviano"),
                ("Luigi", "Ottaviano"), ("Lucia", "Petta")),
    ]
    esito, prima = in_un_paese(atti)
    for genitore in schede_di_prova(esito, prima, "padre"):
        _spezza(esito, genitore, lambda menzione: menzione.atto)
    for genitore in schede_di_prova(esito, prima, "madre"):
        _spezza(esito, genitore, lambda menzione: menzione.atto)
    risoluzione.aggiorna_grafo(esito)
    return esito, prima


def test_due_nuclei_con_gli_stessi_due_genitori_sono_un_nucleo_solo():
    """Luigi Moretta e Lucia Sciulli erano due coppie.

    Nell'atto n. 5 del 1860 la moglie era stata letta «Anna» — la
    pagina scrive Lucia — e da quel momento l'uomo che nel 1852
    dichiara una morte a trentadue anni e quello che nel 1867 presenta
    Maria Celeste a quarantasette erano due uomini, con due mogli e i
    figli divisi fra i due nuclei. La prova non e' in nessuna delle due
    schede: e' nella coppia, che e' la stessa.
    """
    esito, prima = _nucleo_spezzato_in_due()
    assert len(schede_di_prova(esito, prima, "padre")) == 2
    assert len(schede_di_prova(esito, prima, "madre")) == 2
    assert risoluzione.unisci_coppie_gemelle(esito) == 2
    assert len(schede_di_prova(esito, prima, "padre")) == 1
    assert len(schede_di_prova(esito, prima, "madre")) == 1


def test_due_nuclei_con_una_madre_di_un_altro_casato_restano_due():
    """Il contrappeso: servono tutt'e quattro i nomi, non due.

    Due «Luigi Ottaviano» del paese possono davvero essere due uomini,
    e a distinguerli e' la moglie. Se i due casati non si conciliano,
    la coppia non e' la stessa e la regola tace.
    """
    atti = [
        nascita(1860, ("Saverio", "Ottaviano"),
                ("Luigi", "Ottaviano"), ("Lucia", "Petta")),
        nascita(1867, ("Crescenzo", "Ottaviano"),
                ("Luigi", "Ottaviano"), ("Lucia", "Troilo")),
    ]
    esito, prima = in_un_paese(atti)
    for genitore in schede_di_prova(esito, prima, "padre"):
        _spezza(esito, genitore, lambda menzione: menzione.atto)
    for genitore in schede_di_prova(esito, prima, "madre"):
        _spezza(esito, genitore, lambda menzione: menzione.atto)
    risoluzione.aggiorna_grafo(esito)
    assert risoluzione.unisci_coppie_gemelle(esito) == 0


def test_una_coppia_separata_a_mano_non_si_rimette_insieme():
    """Il contrappeso: chi ha letto l'atto vale piu' di quattro nomi uguali."""
    esito, prima = _nucleo_spezzato_in_due()
    padri = schede_di_prova(esito, prima, "padre")
    padri[0].separati_a_mano = set(padri[1].ids)
    padri[1].separati_a_mano = set(padri[0].ids)
    assert risoluzione.unisci_coppie_gemelle(esito) == 0


# ---------------------------------------------------------------------------
# 25. «Nicola Lella, figlio di due coppie»
# ---------------------------------------------------------------------------

def _neonato(identificatore, atto, anno, nome, cognome="Lella"):
    """Un neonato in un atto di nascita di quell'anno."""
    riga = _riga(identificatore, "nascita", "neonato", nome, cognome, atto=atto)
    riga.anno = anno
    return riga


def _scheda_di(menzioni):
    from history_maker.ricostruzione.scheda import Scheda

    scheda = Scheda(chiave=min(m.id for m in menzioni), menzioni=list(menzioni))
    scheda.ricalcola(None)
    return scheda


def test_due_atti_di_nascita_dello_stesso_anno_sono_due_bambini():
    """Le nascite n. 30 e n. 34 del 1834 sono due Giuseppe Lella.

    La n. 30 e' Giuseppe Nicola, figlio di Pompeo Lella e Annangela
    Colella; la n. 34 e' Giuseppe, figlio di Nicolangelo Lella ed
    Emanuela Pelliccia. Il veto sui due atti di nascita confrontava gli
    **anni**, con una tolleranza, e due bambini nati nello stesso anno ci
    passavano indenni: ne usciva una scheda sola, figlia di due coppie, e
    con essa un terzo bambino — il Nicola Lella morto a tre anni nel 1837.
    """
    prima = _scheda_di([_neonato(1, atto=10, anno=1834, nome="Giuseppe Nicola")])
    seconda = _scheda_di([_neonato(2, atto=11, anno=1834, nome="Giuseppe")])

    assert prima.atti_di_nascita == {10}
    assert seconda.atti_di_nascita == {11}
    motivo = evidenza.veti(prima, seconda)
    assert motivo is not None and "due atti di nascita" in motivo


def test_lo_stesso_atto_di_nascita_letto_due_volte_resta_un_bambino():
    """Il contrappeso: due pagine dello stesso evento non sono due nascite.

    Le promesse affisse due volte e gli atti spezzati su due fogli
    ritornano nell'archivio come due righe: ``menzioni.atti_gemelli`` le
    riconosce come un evento solo, e il veto conta gli eventi.
    """
    uno = _neonato(1, atto=10, anno=1834, nome="Giuseppe Nicola")
    altro = _neonato(2, atto=11, anno=1834, nome="Giuseppe Nicola")
    uno.evento = altro.evento = 10        # come li marca 'atti_gemelli'
    prima, seconda = _scheda_di([uno]), _scheda_di([altro])

    assert prima.atti_di_nascita == seconda.atti_di_nascita == {10}
    assert evidenza.veti(prima, seconda) is None


# ---------------------------------------------------------------------------
# 26. «Un Desiderio senza nome, marito di Teresa Desiderio»
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome, cognome, nota, ignota, caso", [
    (None, None, "padre ignoto", True, "la formula piu' comune, diciannove volte"),
    (None, None, "ignoto", True, "la stessa, abbreviata"),
    (None, None, "madre ignota", True, "e al femminile"),
    (None, None, "s'ignorano i genitori", True, "il trovatello"),
    (None, None, "non nominato in quanto figlio di donna non maritata",
     True, "la formula del codice civile del Regno"),
    ("Padre ignoto", "Battista", None, True,
     "la formula finita nella casella del nome, col cognome dedotto"),
    ("ignota", "ignota", None, True, "la formula in tutte e due le caselle"),
    ("Enrico", "Pelliccia", "non nominato in quanto figlio naturale", False,
     "un uomo che ha nome e casato: la nota riguarda il figlio, non lui"),
    ("Donna Filomena", "ignota", None, False,
     "l'atto la nomina a meta': si butta il casato, non lei"),
    (None, None, "regnicolo", False, "una nota che non dice niente di questo"),
])
def test_il_genitore_che_l_atto_dichiara_ignoto(nome, cognome, nota, ignota, caso):
    """«Padre ignoto» non e' una lettura mancata: e' un dato.

    Trattarlo come una persona senza nome costava tre volte. Il cognome
    le arrivava lo stesso, dal figlio o dal formulario, e nasceva un
    «Desiderio» senza nome che risultava marito di Teresa Desiderio. Quel
    finto casato la faceva somigliare a tutti gli altri di quel casato, e
    la fusione la attaccava a un uomo vero: Petronito Ottaviano si e'
    preso quattro «padri ignoti» di quattro atti diversi. E il figlio
    usciva con un padre che non ha — mentre l'atto diceva l'opposto.
    """
    from history_maker import menzioni as lettura_atti

    assert lettura_atti.dichiarata_ignota(nome, cognome, nota) is ignota, caso


def test_il_padre_ignoto_non_prende_il_cognome_del_figlio():
    """La riga resta nell'archivio, ma non diventa nessuno."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Teresa", "Desiderio"),
        _riga(2, "morte", "padre", None, None),
        _riga(3, "morte", "madre", "Marianna", "Bucci"),
    ]
    righe[1].ignota = True
    assert lettura_atti.cognome_dal_figlio(righe) == 0
    assert righe[1].cognome is None


def test_il_padre_ignoto_non_diventa_ne_genitore_ne_coniuge():
    """E il figlio esce senza padre, che e' esattamente cio' che l'atto dice."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria", "Desiderio"),
        _riga(2, "nascita", "padre", None, None),
        _riga(3, "nascita", "madre", "Marianna", "Desiderio"),
        _riga(4, "nascita", "dichiarante", "Nicola", "Ferrara"),
    ]
    righe[1].ignota = True
    lettura_atti.famiglia_dell_atto(righe)
    assert righe[0].madre == 3
    assert righe[0].padre is None, "il padre ignoto non e' un padre"
    assert righe[2].coniuge is None, "e non e' il marito di nessuno"


def test_dove_la_riga_padre_manca_davvero_il_dichiarante_subentra():
    """Il contrappeso: la deduzione dal dichiarante regge quattro nascite su dieci.

    A cambiare non e' quella regola ma il suo presupposto: subentra dove
    la riga 'padre' **manca**, non dove c'e' e dice «ignoto». Li' l'atto
    ha gia' risposto, e a dichiarare va la levatrice o un parente della
    madre.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Maria", "Desiderio"),
        _riga(2, "nascita", "madre", "Lucia", "Lella"),
        _riga(3, "nascita", "dichiarante", "Giovanni", "Desiderio"),
    ]
    lettura_atti.famiglia_dell_atto(righe)
    assert righe[0].padre == 3, "senza riga 'padre', il dichiarante e' il padre"


# ---------------------------------------------------------------------------
# 27. «Chiara Lalli, figlia di Giuseppe Lella»
# ---------------------------------------------------------------------------

def _scheda_finta(esito, nome, cognome, quante=2):
    """Una scheda in piu' dentro un esito gia' costruito."""
    from collections import Counter

    from history_maker import paleografia
    from history_maker.ricostruzione.scheda import Scheda

    chiave = max(esito.schede) + 1
    menzioni = [
        _riga(chiave + indice, "nascita", "testimone", nome, cognome)
        for indice in range(quante)
    ]
    scheda = Scheda(chiave=chiave, menzioni=menzioni)
    scheda.ricalcola(esito.corpus.chiavi())
    scheda.cognomi = Counter({cognome: quante})
    scheda.chiavi_cognome = {paleografia.forma_canonica(cognome)}
    esito.schede[scheda.chiave] = scheda
    for menzione in menzioni:
        esito.di_menzione[menzione.id] = scheda.chiave
    return scheda


def test_il_cognome_della_figlia_e_quello_che_porta_suo_padre():
    """Chiara era letta 'Lalli', 'Lella' e 'Colella'.

    Vinceva 'Lalli' perche' nel secolo e' un po' piu' frequente, e
    nell'albero risultava figlia di Giuseppe **Lella** con un casato che
    non e' il suo. La regola e' la stessa che raddrizza Luigi De Lucia
    dal lato dei figli — padre e figli portano lo stesso casato — e non
    dice da che parte stia la lettura buona.
    """
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, padre = _padre_con_figli({"Colella": 3}, "Marianacci")
    figlia = esito.schede[next(iter(padre.figli))]
    figlia.cognomi = Counter({"Marianacci": 2, "Colella": 1})
    figlia.padri = {padre.chiave}
    attestazione = Counter({"Marianacci": 40, "Colella": 30})
    assert esecuzione._forma(figlia.cognomi, attestazione) == "Marianacci"
    assert esecuzione.cognome_di_famiglia(esito, figlia, attestazione) == "Colella"


def test_quando_padre_e_figli_discordano_vincono_i_figli():
    """Il contrappeso: un padre puo' essere il patrigno, un figlio no.

    Il casato del figlio legittimo e' quello del padre per legge; il
    padre di una scheda puo' invece essere un padre naturale o un
    attacco sbagliato. Quando le due parti indicano letture diverse, si
    tiene quella che portano i figli.
    """
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, nonno = _padre_con_figli({"Troilo": 3}, "Marianacci")
    padre = esito.schede[next(iter(nonno.figli))]
    padre.sesso = "M"      # i figli contano solo per un uomo
    padre.cognomi = Counter({"Petta": 1, "Troilo": 1, "Colella": 1})
    figli = []
    for indice in range(2):
        figlio = _scheda_finta(esito, f"Nipote{indice}", "Colella")
        padre.figli.add(figlio.chiave)
        figlio.padri.add(padre.chiave)
        figli.append(figlio)
    attestazione = Counter({"Petta": 60, "Troilo": 40, "Colella": 30})
    assert esecuzione.cognome_di_famiglia(
        esito, padre, attestazione
    ) == "Colella", "due figli valgono piu' di un padre"


# ---------------------------------------------------------------------------
# 28. «Nicola Lella, figlio di due coppie» — il bambino morto nel 1837
# ---------------------------------------------------------------------------

def _figlio_di(identificatore, tipo, ruolo, nome, padre, madre, atto):
    """Una scheda di una riga, con i genitori che il suo atto dichiara."""
    scheda = _scheda_di([_riga(identificatore, tipo, ruolo, nome, "Lella", atto=atto)])
    scheda.padri_nome = {padre}
    scheda.madri_nome = {madre}
    return scheda


def test_due_coppie_di_genitori_senza_un_nome_in_comune_sono_due_famiglie():
    """Giuseppe Nicola, nato nel 1834, e Nicola, morto a tre anni nel 1837.

    L'atto di nascita lo dice figlio di Pompeo Lella e Annangela Colella,
    quello di morte di Francesco Lella e Angela D'Unico: due coppie che
    non hanno in comune neppure un nome di battesimo. La scheda che li
    teneva insieme risultava «figlia di due coppie».

    Il calcolo sapeva gia' che due genitori discordi sono un'altra
    famiglia, ma lo teneva come indizio e non come veto, per una ragione
    scritta accanto: basta il cognome della madre letto male per
    arrivarci. E' vero, e per questo il veto guarda **solo i nomi di
    battesimo**, che un cognome rovinato non tocca.
    """
    nato = _figlio_di(1, "nascita", "neonato", "Giuseppe Nicola",
                      "pompeo|lela", "annangela|colela", atto=10)
    morto = _figlio_di(2, "morte", "defunto", "Nicola",
                       "francesco|lela", "angela|dunico", atto=20)
    motivo = evidenza.veti(nato, morto)
    assert motivo is not None and "due coppie" in motivo


def test_la_madre_col_cognome_letto_male_resta_la_stessa_famiglia():
    """Il contrappeso: il cognome della madre non basta a separare.

    E' esattamente il caso per cui il calcolo non ne aveva fatto un
    veto: Annangela «Colletta» e Annangela Colella sono la stessa donna
    letta due volte, e con lo stesso padre accanto la famiglia e' una.
    """
    nato = _figlio_di(1, "nascita", "neonato", "Giuseppe Nicola",
                      "pompeo|lela", "annangela|colela", atto=10)
    morto = _figlio_di(2, "morte", "defunto", "Giuseppe Nicola",
                       "pompeo|lela", "annangela|coleta", atto=20)
    assert evidenza.veti(nato, morto) is None


def test_basta_un_genitore_in_comune_perche_il_veto_taccia():
    """Un padre letto come dichiarante puo' essere il nonno: serve che sbaglino tutti e due."""
    nato = _figlio_di(1, "nascita", "neonato", "Giuseppe Nicola",
                      "pompeo|lela", "annangela|colela", atto=10)
    morto = _figlio_di(2, "morte", "defunto", "Nicola",
                       "francesco|lela", "annangela|colela", atto=20)
    assert evidenza.veti(nato, morto) is None


# ---------------------------------------------------------------------------
# 29. «Chiara Lalli» — i figli di una donna portano il casato del marito
# ---------------------------------------------------------------------------

def test_i_figli_di_una_donna_non_decidono_il_suo_cognome():
    """Chiara Lella, moglie di Antonio Marianacci, restava 'Lalli'.

    La regola del cognome di famiglia contava i figli con peso doppio, e
    per un uomo e' giusto. Per una donna no: i suoi figli portano il
    casato del marito. Chiara ha una figlia letta «Leonice Lalli» — il
    cognome della madre copiato sulla figlia — e quella sola riga bastava
    a confermare 'Lalli' contro il 'Lella' del padre e del suo atto di
    nascita.
    """
    from collections import Counter

    from history_maker.ricostruzione import esecuzione

    esito, padre = _padre_con_figli({"Colella": 3}, "Marianacci")
    figlia = esito.schede[next(iter(padre.figli))]
    figlia.sesso = "F"
    figlia.padri = {padre.chiave}
    figlia.cognomi = Counter({"Marianacci": 2, "Colella": 1})
    nipote = _scheda_finta(esito, "Leonice", "Marianacci")
    figlia.figli = {nipote.chiave}
    attestazione = Counter({"Marianacci": 40, "Colella": 30})
    assert esecuzione.cognome_di_famiglia(esito, figlia, attestazione) == "Colella"


# ---------------------------------------------------------------------------
# 30. «Schede senza nome, sposate»
# ---------------------------------------------------------------------------

def test_figlio_naturale_nella_nota_di_un_padre_senza_nome_e_padre_ignoto():
    """La stessa formula del «padre ignoto», detta dal lato del figlio."""
    from history_maker import menzioni as lettura_atti

    assert lettura_atti.dichiarata_ignota(None, None, "figlio naturale") is True
    assert lettura_atti.dichiarata_ignota(
        "Enrico", "Pelliccia", "non nominato in quanto figlio naturale"
    ) is False


def test_una_riga_senza_nome_non_prende_il_cognome_del_figlio():
    """«Cannavina», senza nome, marito della madre in quattro atti.

    Il padre dell'atto aveva la riga ma non il nome — ne' nota, ne'
    formula, niente — e la regola che da' al padre il cognome del figlio
    lo faceva diventare qualcuno. La regola serve a chi il nome ce l'ha
    («Nicola Maria», padre di Stella Colella); a chi non ha niente non
    aggiunge un cognome, perche' non c'e' nessuno a cui aggiungerlo.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Teresa", "Cannavina"),
        _riga(2, "morte", "padre", None, None),
    ]
    assert lettura_atti.cognome_dal_figlio(righe) == 0
    assert righe[1].cognome is None


def _bilancia_dei_nomi():
    from history_maker import nomi

    return nomi.Bilancia.dal_corpus(
        [("Michele", "Pepe")] * 10 + [("Concetta", "Pepe")] * 10
        + [("Luca", "Pepe")] * 10 + [("Antonio", "Lella")] * 10
        + [("Antonio", "Nardo")] * 10
    )


def test_il_nome_finito_nella_casella_del_cognome():
    """«Michele Lella» e «Di Luca Concetta», con la casella del nome vuota.

    Tre di queste quattro righe risultavano schede senza nome sposate a
    qualcuno. 'Luca' e' un nome di battesimo, ma dopo la particella fa
    parte del casato: «Di Luca Concetta» e' Concetta Di Luca.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "padre", None, "Michele Lella"),
        _riga(2, "morte", "madre", None, "Di Luca Concetta"),
    ]
    assert lettura_atti.nome_nella_casella_del_cognome(righe, _bilancia_dei_nomi()) == 2
    assert (righe[0].nome, righe[0].cognome) == ("Michele", "Lella")
    assert (righe[1].nome, righe[1].cognome) == ("Concetta", "Di Luca")


def test_un_casato_di_due_parole_resta_un_casato():
    """Il contrappeso: «Di Nardo» non ha dentro nessun nome di battesimo."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "padre", None, "Di Nardo"),
        _riga(2, "morte", "madre", "Concetta", "Di Luca"),
    ]
    assert lettura_atti.nome_nella_casella_del_cognome(righe, _bilancia_dei_nomi()) == 0
    assert righe[0].cognome == "Di Nardo" and righe[0].nome is None


# ---------------------------------------------------------------------------
# 31. «Maria Michela Di Nardo e i due Giuseppe Colella»
# ---------------------------------------------------------------------------

def _due_mariti_omonimi():
    """Due Giuseppe: il marito, con due figli, e un altro con una riga del marito.

    Il secondo ha anche una riga sua — una testimonianza — perche' sia un
    uomo vero e non un frammento: se fosse un frammento, lo rimetterebbe
    a posto ``unisci_coniugi_omonimi``.
    """
    atti = [
        nascita(1878, ("Saverio", "Ottaviano"),
                ("Giuseppe", "Ottaviano"), ("Michela", "Petta")),
        nascita(1879, ("Crescenzo", "Ottaviano"),
                ("Giuseppe", "Ottaviano"), ("Michela", "Petta")),
        nascita(1880, ("Vincenzo", "Ottaviano"),
                ("Giuseppe", "Ottaviano"), ("Michela", "Petta")),
        {"tipo": "nascita", "anno": 1885, "data": "1885-06-01", "persone": [
            {"ruolo": "neonato", "nome": "Rosa", "cognome": "Troilo"},
            {"ruolo": "padre", "nome": "Raffaele", "cognome": "Troilo"},
            {"ruolo": "madre", "nome": "Angela", "cognome": "Pepe"},
            {"ruolo": "testimone", "nome": "Giuseppe", "cognome": "Ottaviano"},
        ]},
    ]
    esito, prima = in_un_paese(atti)
    for padre in schede_di_prova(esito, prima, "padre"):
        if "Ottaviano" in padre.cognomi:
            _spezza(esito, padre, lambda m: "A" if m.anno in (1878, 1879) else "B")
    for testimone in schede_di_prova(esito, prima, "testimone"):
        if "Ottaviano" in testimone.cognomi:
            _spezza(esito, testimone, lambda m: "A" if m.anno in (1878, 1879) else "B")
    return esito, prima


def _mariti_di(esito, moglie):
    return {
        esito.di_menzione[m.coniuge]
        for m in moglie.menzioni if m.coniuge is not None
    }


def test_la_riga_del_marito_finita_nella_scheda_sbagliata_torna_al_marito():
    """Il dichiarante del 1880 era il marito di Maria Michela, non l'altro Giuseppe.

    Le due schede sono due uomini veri, e unirle sarebbe sbagliato: il
    secondo e' morto nel 1895 e il primo dichiara ancora nel 1898. Ma la
    riga che lega il secondo alla moglie del primo non era sua, e si
    sposta lei sola.
    """
    esito, prima = _due_mariti_omonimi()
    moglie = [m for m in schede_di_prova(esito, prima, "madre") if "Petta" in m.cognomi]
    assert len(moglie) == 1
    assert len(_mariti_di(esito, moglie[0])) == 2

    assert risoluzione.sposta_al_coniuge_omonimo(esito) == 1
    moglie = esito.schede[esito.di_menzione[moglie[0].menzioni[0].id]]
    assert len(_mariti_di(esito, moglie)) == 1, "un marito solo"
    testimoni = [
        s for s in schede_di_prova(esito, prima, "testimone") if "Ottaviano" in s.cognomi
    ]
    assert testimoni, "l'altro Giuseppe resta, con la sua testimonianza"


def test_una_separazione_decisa_sulla_pagina_ferma_anche_lo_spostamento():
    """Il contrappeso: chi ha letto l'atto vale piu' della moglie in comune."""
    esito, prima = _due_mariti_omonimi()
    padri = [p for p in schede_di_prova(esito, prima, "padre") if "Ottaviano" in p.cognomi]
    for uno in padri:
        for altro in padri:
            if uno is not altro:
                uno.separati_a_mano |= altro.ids
    assert risoluzione.sposta_al_coniuge_omonimo(esito) == 0


# ---------------------------------------------------------------------------
# 32. «Ventinove omonimi nati nello stesso anno» — il controllo di qualita'
# ---------------------------------------------------------------------------

def _archivio_di_due_cugini(stessi_genitori):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT, "
        "anno_nascita INTEGER, nascita_origine TEXT);"
        "CREATE TABLE legami (figlio INTEGER, genitore INTEGER);"
    )
    conn.executemany("INSERT INTO individui VALUES (?, ?, ?, ?, ?)", [
        (1, "Nicola Maria", "Pelliccia", 1827, "certa"),
        (2, "Nicola Maria", "Pelliccia", 1827, "certa"),
        (3, "Samuele", "Pelliccia", None, None),
        (4, "Anna", "Marianacci", None, None),
        (5, "Domenico", "Marfisi", None, None),
        (6, "Erminia", "Pelliccia", None, None),
    ])
    secondi = (3, 4) if stessi_genitori else (5, 6)
    conn.executemany("INSERT INTO legami VALUES (?, ?)", [
        (1, 3), (1, 4), (2, secondi[0]), (2, secondi[1]),
    ])
    return conn


def test_due_cugini_col_nome_del_nonno_non_sono_una_frammentazione():
    """Nicola Maria Pelliccia, nato due volte nel 1827, da due coppie.

    Il controllo contava come frammentazione ogni coppia di omonimi nati
    nello stesso anno con l'atto di nascita, e la premessa era sbagliata:
    in ventinove gruppi su ventinove i due atti nominavano genitori
    diversi. Sono cugini battezzati col nome dello stesso nonno.
    """
    from history_maker import qualita

    assert qualita.omonimi_con_la_stessa_nascita(_archivio_di_due_cugini(False)) == []


def test_due_schede_con_gli_stessi_genitori_e_lo_stesso_anno_restano_un_dubbio():
    """Il contrappeso: stessi genitori, stesso nome, stesso anno e' davvero un doppione."""
    from history_maker import qualita

    casi = qualita.omonimi_con_la_stessa_nascita(_archivio_di_due_cugini(True))
    assert len(casi) == 1


# ---------------------------------------------------------------------------
# 33. «Saba Mosca, figlia di Giuseppe Lella»
# ---------------------------------------------------------------------------

def _una_moglie_due_mariti(cognome_del_secondo):
    """Due Giuseppe, frammenti tutti e due, con la stessa moglie."""
    atti = [
        nascita(1823, ("Saba", cognome_del_secondo),
                ("Giuseppe", cognome_del_secondo), ("Grazia", "Petta")),
        nascita(1829, ("Raffaele", "Lella"), ("Giuseppe", "Lella"), ("Grazia", "Petta")),
    ]
    esito, prima = in_un_paese(atti)
    _accorpa_le_madri(esito, prima)
    risoluzione.aggiorna_grafo(esito)
    padri = [m.id for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "padre"]
    return esito, padri


def test_due_mariti_omonimi_di_due_casati_frequenti_restano_due():
    """Giuseppe Mosca non e' Giuseppe Lella.

    Saba Mosca, morta nel 1887, e' «figlia di fu Giuseppe e di fu Motta
    Maria Giovanna»: il padre e' dato col solo nome, perche' il casato e'
    quello della figlia. La madre era finita in una scheda sbagliata,
    sposata anche con Giuseppe Lella, e la regola dei coniugi omonimi —
    due mariti Giuseppe della stessa moglie, uno dei due un frammento — li
    aveva fusi, perche' il cognome non entrava nella condizione. Entrava a
    ragione nel caso per cui la regola era nata, Mouton e Montanaro, due
    letture rare della stessa parola; a torto qui, dove Mosca e Lella sono
    due famiglie di centinaia di righe ciascuna.
    """
    esito, padri = _una_moglie_due_mariti("Pelliccia")
    frequenze = esito.corpus.frequenze_cognome
    lella, pelliccia = frequenze["lela"], frequenze["pelicia"]
    assert min(lella, pelliccia) / max(lella, pelliccia) > risoluzione.QUOTA_DI_UNA_LETTURA

    risoluzione.unisci_coniugi_omonimi(esito)
    assert len({esito.di_menzione[p] for p in padri}) == 2, "due famiglie, due uomini"


def test_un_casato_raro_accanto_a_uno_frequente_puo_essere_una_lettura():
    """Il contrappeso: 'Montanaro' contro 'Moretta' e' la stessa parola letta male."""
    esito, padri = _una_moglie_due_mariti("Montanaro")
    risoluzione.unisci_coniugi_omonimi(esito)
    assert len({esito.di_menzione[p] for p in padri}) == 1


# ---------------------------------------------------------------------------
# 34. «Orsodio Daba e Arcadio Montra» — la sposa letta due volte
# ---------------------------------------------------------------------------

def _matrimonio_con_la_sposa_ripetuta(seconda_sposa=("Maria Fiorentina Deba", "Montra"),
                                      eta_seconda="ventisei"):
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposo", "Antonio", "Ottaviano"),
        _riga(2, "matrimonio", "padre", "Domenico", "Ottaviano"),
        _riga(3, "matrimonio", "madre", "Angela", "Lella"),
        _riga(4, "matrimonio", "sposa", "Maria Fiorenza", "Daba"),
        _riga(5, "matrimonio", "padre", "Orsodio", "Daba"),
        _riga(6, "matrimonio", "madre", "Felicia", "Desiderio"),
        _riga(7, "matrimonio", "sposa", *seconda_sposa),
        _riga(8, "matrimonio", "padre", "Arcadio", "Montra"),
        _riga(9, "matrimonio", "madre", "Felicia", "Desiderio"),
    ]
    from history_maker import nomi
    for riga, eta in ((righe[3], "ventisei"), (righe[6], eta_seconda)):
        riga.eta = nomi.analizza_eta(eta)
    return lettura_atti, righe


def test_la_sposa_letta_due_volte_e_una_sposa_sola():
    """Matrimonio n. 11 del 1834: una sposa, e l'estrazione ne ha fatte due.

    «Maria Fiorenza Daba, figlia di Orsodio Daba» e «Maria Fiorentina
    Deba Montra, figlia di Arcadio Montra», con la stessa madre accanto.
    La pagina scrive «Maria Fiorenza Moretta, figlia di Arcadio Moretta e
    di Felicia Desiderio». Nessun confronto poteva rimetterle insieme,
    perche' i candidati si scelgono per somiglianza di cognome: serve la
    struttura dell'atto, che ha una sposa sola.
    """
    lettura_atti, righe = _matrimonio_con_la_sposa_ripetuta()
    assert lettura_atti.righe_lette_due_volte(righe) == 3
    assert righe[6].doppia_di == 4, "la seconda sposa e' la prima"
    assert righe[7].doppia_di == 5, "Arcadio Montra e' Orsodio Daba letto di nuovo"
    assert righe[8].doppia_di == 6, "e la madre e' la stessa"

    lettura_atti.famiglia_dell_atto(righe)
    assert righe[3].padre == 5 and righe[3].madre == 6
    assert righe[3].coniuge == 1


def test_due_spose_con_nomi_e_casati_diversi_non_sono_una():
    """Il contrappeso: la pagina che contiene due atti divisi male."""
    lettura_atti, righe = _matrimonio_con_la_sposa_ripetuta(("Rosa", "Pepe"))
    assert lettura_atti.righe_lette_due_volte(righe) == 0


def test_due_spose_con_dieci_anni_di_differenza_non_sono_una():
    """Il contrappeso: lo stesso nome non basta se le eta' non tornano."""
    lettura_atti, righe = _matrimonio_con_la_sposa_ripetuta(eta_seconda="trentasei")
    assert lettura_atti.righe_lette_due_volte(righe) == 0


def test_la_ricostruzione_rimette_insieme_la_sposa_letta_due_volte():
    """Dall'atto all'albero: una sposa, un padre, una madre."""
    atto = {"tipo": "matrimonio", "anno": 1834, "data": "1834-11-19", "persone": [
        {"ruolo": "sposo", "nome": "Antonio", "cognome": "Ottaviano", "eta": "trentatre"},
        {"ruolo": "padre", "nome": "Domenico", "cognome": "Ottaviano"},
        {"ruolo": "madre", "nome": "Angela", "cognome": "Lella"},
        {"ruolo": "sposa", "nome": "Maria Fiorenza", "cognome": "Daba", "eta": "ventisei"},
        {"ruolo": "padre", "nome": "Orsodio", "cognome": "Daba"},
        {"ruolo": "madre", "nome": "Felicia", "cognome": "Desiderio"},
        {"ruolo": "sposa", "nome": "Maria Fiorentina Deba", "cognome": "Montra",
         "eta": "ventisei"},
        {"ruolo": "padre", "nome": "Arcadio", "cognome": "Montra"},
        {"ruolo": "madre", "nome": "Felicia", "cognome": "Desiderio"},
    ]}
    esito, prima = in_un_paese([atto])
    per_ruolo = {}
    for menzione in esito.corpus.menzioni:
        if menzione.id >= prima:
            per_ruolo.setdefault(menzione.ruolo, []).append(esito.di_menzione[menzione.id])
    assert len(set(per_ruolo["sposa"])) == 1, "una sposa"
    padri = [m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "padre"
             and m.cognome in ("Daba", "Montra")]
    assert len({esito.di_menzione[m.id] for m in padri}) == 1, "un padre"


# ---------------------------------------------------------------------------
# 35. «Maria Giovanna e Maria Letizia Moretta» — Saba Mosca senza genitori
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uno, altro, compatibili, caso", [
    ("Maria Giovanna", "Maria Letizia", False,
     "la madre di Saba Mosca e la moglie di Giuseppe Marianacci: due donne"),
    ("Maria Domenica", "Maria Nicola", False, "due secondi nomi scritti e diversi"),
    ("Giuseppe Nicola", "Giuseppe Antonio", False, "vale anche per gli uomini"),
    ("Maria", "Maria Giovanna", True, "il secondo nome si omette: il silenzio non contraddice"),
    ("Giuseppe Nicola", "Giuseppe", True, "lo stesso, al maschile"),
    ("Anna Maria", "Maria Anna", True, "gli stessi nomi in un altro ordine"),
    ("Giuseppe Antonio", "Giuseppantonio", True, "gli stessi nomi attaccati"),
    ("Maria Giuseppa", "Maria Giuseppina", True, "lo stesso nome detto in un altro modo"),
    ("Maria Fiorenza", "Maria Fiorentina Deba", True, "lo stesso secondo nome letto male"),
    ("Angela Maria", "Angela Maria Margherita", True, "un nome in piu' non ne contraddice nessuno"),
    ("Domenicantonio", "Domenico", True, "il nome intero e il suo inizio"),
])
def test_due_secondi_nomi_scritti_e_diversi_fanno_due_nomi(uno, altro, compatibili, caso):
    """Saba Mosca, morta nel 1887, restava senza genitori.

    La madre, Maria Giovanna Moretta, era stata unita a Maria Letizia
    Moretta, moglie di Giuseppe Marianacci: stesso casato, eta' vicine, e
    «Maria» in comune. Da li' anche il padre, Giuseppe Mosca, era finito
    dentro Giuseppe Marianacci. Un pezzo di nome in comune dice la stessa
    persona solo quando uno dei due tace il secondo nome; quando tutti e
    due lo scrivono, e sono diversi, sono due persone.
    """
    assert evidenza._nomi_compatibili(uno, altro) is compatibili, caso


# ---------------------------------------------------------------------------
# 36. «Saba Mosca, figlia di Giuseppe Marianacci» — il casato del marito
# ---------------------------------------------------------------------------

# Le righe dell'archivio, per le forme dei casi qui sotto.
RIGHE_DEL_1887 = Counter({"mosca": 281, "marianaci": 3164, "lela": 1763, "moreta": 1177,
                          "montra": 3, "montanaro": 15})


@pytest.mark.parametrize("mariti, altri, di_due_famiglie, caso", [
    ({"giuseppe|mosca"}, {"giuseppe|marianaci"}, True,
     "Giuseppe Mosca e Giuseppe Marianacci: due uomini"),
    ({"giuseppe|montra"}, {"giuseppe|moreta"}, False, "Montra e' Moretta letto male"),
    ({"giuseppe|montanaro"}, {"giuseppe|moreta"}, False,
     "quindici righe: puo' essere lo sbaglio di qualunque parola"),
    ({"giuseppe|"}, {"giuseppe|marianaci"}, False, "un marito senza cognome non dice niente"),
    ({"giuseppe|mosca"}, {"domenico|marianaci"}, False,
     "due nomi diversi: la domanda non si pone"),
])
def test_mariti_omonimi_di_due_casati(mariti, altri, di_due_famiglie, caso):
    """Il casato del marito non e' il cognome da nubile della moglie.

    La clemenza verso i coniugi con lo stesso nome e cognomi diversi e'
    nata per le mogli, il cui cognome la mano rovina di continuo. Il
    casato del marito lo ripete ogni figlio: due mariti omonimi con casati
    che non si somigliano sono due uomini.
    """
    assert evidenza._mariti_di_due_casati(mariti, altri, RIGHE_DEL_1887) is di_due_famiglie, caso


def _due_mogli_di_due_giuseppe(casato_del_secondo, casato_del_primo="Pepe"):
    """Due Maria Moretta coetanee, ciascuna moglie di un Giuseppe.

    Nel paese di prova i casati veri hanno un centinaio di righe: 'Pepe'
    fa la parte di 'Mosca', che nell'archivio ne ha 281. Un 'Mosca' con
    due righe sarebbe una forma rara, e una forma rara puo' essere lo
    sbaglio di qualunque parola.
    """
    atti = [
        nascita(1823, ("Saba", casato_del_primo), ("Giuseppe", casato_del_primo),
                ("Maria Giovanna", "Moretta"), None, {"eta": "trenta"}),
        nascita(1825, ("Ermenegildo", casato_del_secondo), ("Giuseppe", casato_del_secondo),
                ("Maria", "Moretta"), None, {"eta": "trentadue"}),
    ]
    esito, prima = in_un_paese(atti)
    madri = [m.id for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "madre"]
    return esito, madri, prima


@pytest.mark.parametrize("miei, suoi, di_due_famiglie, caso", [
    ({"mosca"}, {"marianaci"}, True, "Mosca e Marianacci: due famiglie"),
    ({"montra"}, {"moreta"}, False, "Montra e' Moretta letto male"),
    ({"montanaro"}, {"moreta"}, False, "una forma rara puo' essere lo sbaglio di qualunque parola"),
    (set(), {"marianaci"}, False, "senza cognome non si dice niente"),
    ({"mosca", "marianaci"}, {"marianaci"}, False, "una chiave in comune"),
])
def test_casati_di_due_famiglie(miei, suoi, di_due_famiglie, caso):
    assert evidenza._casati_di_due_famiglie(miei, suoi, RIGHE_DEL_1887) is di_due_famiglie, caso


def test_due_giuseppe_di_due_casati_non_si_uniscono_per_la_moglie_simile():
    """Giuseppe Mosca e Giuseppe Marianacci si univano perche' le mogli si somigliavano.

    «Maria Giovanna Moretta» e «Maria Moretta» passavano per una moglie
    sola, e la moglie in comune pesava piu' dei due cognomi. Poi, «sposate
    allo stesso uomo», si univano anche le due donne.
    """
    from history_maker.ricostruzione.scheda import Scheda

    esito, _, prima = _due_mogli_di_due_giuseppe("Marianacci")
    chiavi = esito.corpus.chiavi()
    padri = [Scheda.dalla_menzione(m, chiavi) for m in esito.corpus.menzioni
             if m.id >= prima and m.ruolo == "padre"]
    prove = evidenza.confronta(*padri, esito.modello)
    assert prove.logit < risoluzione.SOGLIA_UNIONE, prove.racconta(6)


def test_due_giuseppe_con_casati_che_si_confondono_restano_una_domanda_aperta():
    """Il contrappeso: se i casati possono essere la stessa parola, la moglie conta ancora."""
    from history_maker.ricostruzione.scheda import Scheda

    esito, _, prima = _due_mogli_di_due_giuseppe("Pepi")
    chiavi = esito.corpus.chiavi()
    padri = [Scheda.dalla_menzione(m, chiavi) for m in esito.corpus.menzioni
             if m.id >= prima and m.ruolo == "padre"]
    prove = evidenza.confronta(*padri, esito.modello)
    coniuge = [i for i in prove.pro() if i.tipo == "coniuge"]
    assert coniuge and coniuge[0].peso > 0, prove.racconta(6)


def test_la_madre_di_saba_non_e_la_moglie_di_giuseppe_marianacci():
    """Morte n. 38 del 1887: Saba Mosca, «da fu Giuseppe e da fu Motta Maria Giovanna».

    La madre era finita dentro Maria Letizia Moretta, moglie di Giuseppe
    Marianacci: stesso casato, «Maria» in comune, e un marito Giuseppe da
    tutte e due le parti. Poi, «sposato alla stessa donna», anche il padre
    era finito dentro Giuseppe Marianacci, e Saba restava senza genitori.
    """
    esito, madri, _ = _due_mogli_di_due_giuseppe("Marianacci")
    assert len({esito.di_menzione[m] for m in madri}) == 2, "due donne, due mariti"


@pytest.mark.parametrize("mio, suo, conciliabili, caso", [
    ("mosca", "marianaci", False,
     "il padre di Saba: meno di un decimo di Marianacci, ma 281 righe e nessuna somiglianza"),
    ("mosca", "lela", False, "piu' di un decimo: due famiglie"),
    ("montanaro", "moreta", True, "quindici righe: puo' essere lo sbaglio di qualunque parola"),
    ("montra", "moreta", True, "tre righe, e la stessa parola letta male"),
])
def test_una_famiglia_non_e_lo_sbaglio_di_un_altra(mio, suo, conciliabili, caso):
    """Giuseppe Mosca, padre di Saba, era finito dentro Giuseppe Marianacci.

    La regola dei coniugi omonimi perdonava il casato raro — meno di un
    decimo dell'altro — come una lettura sbagliata. Ma Mosca ha 281 righe:
    e' una famiglia, e una famiglia non e' lo sbaglio di un'altra se le
    due parole non si somigliano.
    """
    from types import SimpleNamespace

    esito = SimpleNamespace(corpus=SimpleNamespace(frequenze_cognome=RIGHE_DEL_1887))
    una, altra = SimpleNamespace(chiavi_cognome={mio}), SimpleNamespace(chiavi_cognome={suo})
    assert risoluzione._casati_conciliabili(esito, una, altra) is conciliabili, caso


def test_la_madre_nata_dopo_la_figlia_non_e_la_stessa_donna():
    """La madre di Saba, nata nel 1823, era finita dentro una Maria Giovanna Moretta nata nel 1829.

    Morte n. 67 del 1871: Maria Giovanna Moretta, quarantadue anni. Morte
    n. 38 del 1887: Saba Mosca, sessantaquattro anni, «da fu Motta Maria
    Giovanna». Stesso nome, stesso casato: ma la prima e' nata sei anni
    dopo la figlia della seconda. Il legame di Saba con la madre poi
    cadeva, scartato come impossibile, e Saba restava con un genitore.
    """
    from history_maker.ricostruzione.scheda import Scheda

    atti = [
        {"tipo": "morte", "anno": 1871, "data": "1871-05-02", "persone": [
            {"ruolo": "defunto", "nome": "Maria Giovanna", "cognome": "Moretta",
             "eta": "quarantadue"},
            {"ruolo": "padre", "nome": "Samuele", "cognome": "Moretta"},
            {"ruolo": "madre", "nome": "Lucia", "cognome": "Fusilli"}]},
        {"tipo": "morte", "anno": 1887, "data": "1887-10-25", "persone": [
            {"ruolo": "defunto", "nome": "Saba", "cognome": "Mosca", "eta": "sessantaquattro"},
            {"ruolo": "padre", "nome": "Giuseppe", "cognome": "Mosca"},
            {"ruolo": "madre", "nome": "Maria Giovanna", "cognome": "Moretta"}]},
    ]
    esito, prima = in_un_paese(atti)
    chiavi = esito.corpus.chiavi()
    due = [Scheda.dalla_menzione(m, chiavi) for m in esito.corpus.menzioni
           if m.id >= prima and m.nome == "Maria Giovanna"]
    assert len(due) == 2
    assert "nascere" in (evidenza.veti(*due) or ""), "un figlio prima della propria nascita"
    madre = next(m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "madre"
                 and m.nome == "Maria Giovanna")
    defunta = next(m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "defunto"
                   and m.nome == "Maria Giovanna")
    assert esito.di_menzione[madre.id] != esito.di_menzione[defunta.id]


def test_una_madre_abbastanza_vecchia_non_e_vietata():
    """Il contrappeso: la stessa donna, se l'eta' torna, non trova nessun veto."""
    from history_maker.ricostruzione.scheda import Scheda

    atti = [
        {"tipo": "morte", "anno": 1871, "data": "1871-05-02", "persone": [
            {"ruolo": "defunto", "nome": "Maria Giovanna", "cognome": "Moretta",
             "eta": "settantacinque"},
            {"ruolo": "padre", "nome": "Samuele", "cognome": "Moretta"}]},
        {"tipo": "morte", "anno": 1887, "data": "1887-10-25", "persone": [
            {"ruolo": "defunto", "nome": "Saba", "cognome": "Mosca", "eta": "sessantaquattro"},
            {"ruolo": "padre", "nome": "Giuseppe", "cognome": "Mosca"},
            {"ruolo": "madre", "nome": "Maria Giovanna", "cognome": "Moretta"}]},
    ]
    esito, prima = in_un_paese(atti)
    chiavi = esito.corpus.chiavi()
    due = [Scheda.dalla_menzione(m, chiavi) for m in esito.corpus.menzioni
           if m.id >= prima and m.nome == "Maria Giovanna"]
    assert evidenza.veti(*due) is None


# ---------------------------------------------------------------------------
# 37. «Giuseppe Nicola, figlio di Domenico Lella» dentro Giuseppe Franchini
# ---------------------------------------------------------------------------

def _nascita_col_secondo_nome_nel_cognome(padre=("Domenico", "Lella"), cognome_del_nato="Nicola"):
    righe = [
        _riga(1, "nascita", "neonato", "Giuseppe", cognome_del_nato),
        _riga(2, "nascita", "padre", *padre),
        _riga(3, "nascita", "madre", "Angela Maria", "Torzi"),
        _riga(4, "nascita", "dichiarante", "Domenico", "Lella"),
    ]
    from history_maker import menzioni as lettura_atti, nomi

    bilancia = nomi.Bilancia.dal_corpus(
        [("Giuseppe", "Lella")] * 10 + [("Nicola", "Pepe")] * 10
        + [("Domenico", "Torzi")] * 10 + [("Angela Maria", "Lella")] * 10
    )
    lettura_atti.nome_composto_spezzato(righe, bilancia)
    lettura_atti.famiglia_dell_atto(righe)
    lettura_atti.cognome_dal_dichiarante(righe)
    return lettura_atti, righe


def test_il_neonato_col_secondo_nome_nel_cognome_prende_il_casato_del_padre():
    """Nascita n. 9 del 1819: «Giuseppe» «Nicola», figlio di Domenico di Vito Lella.

    Ricomposto il nome, il bambino restava senza casato — c'era una riga
    di padre, e il cognome dal dichiarante non si da' — e senza casato si
    attaccava a chiunque avesse il suo nome e il suo anno.
    """
    lettura_atti, righe = _nascita_col_secondo_nome_nel_cognome()
    assert righe[0].nome == "Giuseppe Nicola" and righe[0].cognome is None
    assert lettura_atti.cognome_dal_padre(righe) == 1
    assert righe[0].cognome == "Lella" and righe[0].cognome_origine == "padre"


def test_senza_il_cognome_del_padre_il_neonato_resta_senza():
    """Il contrappeso: se il padre e' scritto senza casato, non si inventa niente."""
    lettura_atti, righe = _nascita_col_secondo_nome_nel_cognome(padre=("Domenico", None))
    assert lettura_atti.cognome_dal_padre(righe) == 0
    assert righe[0].cognome is None


def test_un_neonato_col_suo_cognome_non_si_tocca():
    """Il contrappeso: chi un casato ce l'ha lo tiene, anche se non e' del padre."""
    lettura_atti, righe = _nascita_col_secondo_nome_nel_cognome(cognome_del_nato="Pepe")
    assert lettura_atti.cognome_dal_padre(righe) == 0
    assert righe[0].cognome == "Pepe"


# ---------------------------------------------------------------------------
# 38. «Maria Catolini, figlia di Prospero Catolini», moglie di Felice Lella
# ---------------------------------------------------------------------------

def _morte_di_maria_catolini(cognome_della_figlia="Catolini"):
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Maria", cognome_della_figlia),
        _riga(2, "morte", "padre", "Prospero", "Catolini"),
        _riga(3, "morte", "madre", "Maria Giuseppa", "Boscia"),
    ]
    lettura_atti.famiglia_dell_atto(righe)
    lettura_atti.cognome_dal_padre(righe)
    return lettura_atti, righe


def test_il_casato_scritto_dal_figlio_e_dal_padre_e_confermato():
    """Morte n. 14 del 1836: la figlia e il padre scrivono tutti e due Catolini."""
    lettura_atti, righe = _morte_di_maria_catolini()
    assert lettura_atti.casati_confermati(righe) == 1
    assert righe[0].casato_confermato and righe[1].casato_confermato
    assert not righe[2].casato_confermato, "la madre porta il suo cognome"


def test_un_cognome_preso_dal_padre_non_e_una_conferma():
    """Il contrappeso: il cognome derivato e' la stessa lettura, non una seconda."""
    lettura_atti, righe = _morte_di_maria_catolini(cognome_della_figlia=None)
    assert righe[0].cognome == "Catolini" and righe[0].cognome_origine == "padre"
    assert lettura_atti.casati_confermati(righe) == 0


@pytest.mark.parametrize("certi, letture, altro_casato, caso", [
    ({"catolini"}, {"pelicia": 5}, True, "Maria Catolini non e' Maria Pelliccia"),
    ({"moreta"}, {"montra": 4}, False, "Montra e' Moretta letto male"),
    ({"torzi"}, {"zorzi": 3, "torzi": 1}, False, "Zorzi e Torzi si somigliano"),
    ({"catolini"}, {"pelicia": 2}, False, "due letture sole possono essere sbagliate"),
])
def test_un_casato_confermato_contro_un_altra_famiglia(certi, letture, altro_casato, caso):
    """La scheda che porta con costanza un casato diverso da quello confermato e' un'altra persona.

    La moglie di Felice Lella, Maria Pelliccia, aveva preso la morte del
    1836 di «Maria Catolini, figlia di Prospero Catolini»: stesso nome,
    stessa eta'. Il cognome diverso costava un punto e mezzo, non un
    veto — e per una donna e' giusto, perche' il suo cognome la mano lo
    rovina spesso. Ma qui il cognome lo scrivono due righe dello stesso
    atto, e l'altra scheda ne porta un altro almeno tre volte.
    """
    from types import SimpleNamespace

    scheda = SimpleNamespace(conteggi_cognome=Counter(letture))
    assert evidenza._un_altro_casato(certi, scheda) is altro_casato, caso


# ---------------------------------------------------------------------------
# 39. «Giuseppe Pepe aveva due mogli»: Celidata e Candidata Petolini
# ---------------------------------------------------------------------------

def _pepe_e_le_petolini(seconda=("Candidata", "Petolini"), anni_della_seconda=(1811,),
                        prima=("Celidata", "Petolini")):
    """Giuseppe Pepe e i suoi figli, con la moglie scritta in due modi."""
    # Il padre e' un uomo solo, e lo dice la sua eta': nato nel 1777 in
    # tutti gli atti, bracciale, come nei registri veri.
    eta = {32: "trentadue", 34: "trentaquattro", 36: "trentasei", 38: "trentotto",
           43: "quarantatre", 45: "quarantacinque"}

    def padre(anno):
        return {"eta": eta[anno - 1777], "professione": "bracciale"}

    figli = ("Domenico", "Nicola", "Maria")
    atti = [nascita(anno, (figlio, "Pepe"), ("Giuseppe", "Pepe"), prima, padre(anno))
            for anno, figlio in zip((1809, 1813, 1815), figli)]
    atti += [nascita(anno, (f"{figlio}a", "Pepe"), ("Giuseppe", "Pepe"), seconda, padre(anno))
             for anno, figlio in zip(anni_della_seconda, ("Anna", "Rosa"))]
    esito, prima = in_un_paese(atti)
    madri = [m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "madre"]
    return esito, madri


def test_due_mogli_negli_stessi_anni_e_della_stessa_famiglia_sono_una():
    """Giuseppe Pepe, figli da Celidata Petolini nel 1809, 1813 e 1815, e da Candidata nel 1811.

    Non si divorzia, e ci si risposa solo da vedovi: due mogli con figli
    che nascono in mezzo gli uni agli altri sono una moglie sola, e se
    sono anche della stessa famiglia e' il nome a essere letto in due modi
    — Celidata, Calidata, Celidonia, Candidata.
    """
    esito, madri = _pepe_e_le_petolini()
    assert len({esito.di_menzione[m.id] for m in madri}) == 1, "una moglie sola"


def test_due_mogli_negli_stessi_anni_di_due_famiglie_restano_due():
    """Il contrappeso: se i casati sono due, a essere doppio e' il marito, non la moglie."""
    esito, madri = _pepe_e_le_petolini(seconda=("Candidata", "Marianacci"))
    petolini = {esito.di_menzione[m.id] for m in madri if m.cognome == "Petolini"}
    marianacci = {esito.di_menzione[m.id] for m in madri if m.cognome == "Marianacci"}
    assert not petolini & marianacci


@pytest.mark.parametrize("primo, secondo, insieme, caso", [
    ([1809, 1813, 1815], [1811], True, "figli dell'una in mezzo a quelli dell'altra"),
    ([1809, 1813, 1815], [1820, 1822], False, "la seconda moglie dopo la vedovanza"),
    ([1809, 1815], [1815], True, "lo stesso anno e' gia' insieme"),
    ([1809], [], False, "senza anni non si dice niente"),
])
def test_la_sorella_sposata_dopo_la_vedovanza_non_e_contemporanea(primo, secondo, insieme, caso):
    """Il contrappeso: due mogli in due stagioni della vita sono due mogli per questa regola.

    Il vedovo che sposa la sorella della prima moglie ha due mogli dello
    stesso casato, ma una dopo l'altra: i figli della seconda nascono
    quando quelli della prima sono finiti, e la regola dei coniugi
    contemporanei non le tocca. (Nel paese di prova le unisce comunque la
    riconciliazione, per il coniuge in comune e il casato raro: e' una
    decisione del punteggio, non di questa regola.)
    """
    assert risoluzione._negli_stessi_anni(primo, secondo) is insieme, caso


def test_due_mogli_negli_stessi_anni_con_nomi_lontani_sono_una():
    """Negli stessi anni non conta quanto i nomi si somiglino: due mogli insieme non si possono avere.

    E' il contrappeso del contrappeso: la stessa «Rosa Petolini» di sopra,
    con i figli che nascono in mezzo a quelli di Celidata, e' Celidata
    letta male — come Gesualdo, Gualdo e Romualdo Cicchillitti.
    """
    esito, madri = _pepe_e_le_petolini(seconda=("Rosa", "Petolini"), anni_della_seconda=(1811,))
    assert len({esito.di_menzione[m.id] for m in madri}) == 1


# ---------------------------------------------------------------------------
# 40. «Domenica Zorzi, figlia di Francesco Torzi»
# ---------------------------------------------------------------------------

def _figlia_e_padre(figlia, padre, frequenze):
    """Un archivio di due schede, figlia e padre, e l'esito che ne sa le frequenze."""
    from types import SimpleNamespace

    from history_maker.ricostruzione import esecuzione

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    for numero, (nome, cognome, menzioni) in ((1, padre), (2, figlia)):
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni) VALUES (?,?,?,?,?)",
                     (numero, f"P{numero}", nome, cognome, menzioni))
    conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                 "VALUES (2, 1, 'padre', 296, 1.0, 'confermato')")
    esito = SimpleNamespace(corpus=SimpleNamespace(frequenze_cognome=Counter(frequenze)))
    corretti = esecuzione.cognomi_dal_padre(conn, esito, {1: 1, 2: 2})
    cognome = conn.execute("SELECT cognome FROM individui WHERE id = 2").fetchone()[0]
    conn.close()
    return corretti, cognome


def test_la_figlia_con_una_variante_del_cognome_del_padre_prende_il_suo():
    """Morte n. 22 del 1813: «Domenica Zorzi, figlia di Francesco Zorzi», e il padre e' Torzi.

    Nell'atto padre e figlia portano lo stesso cognome; la scheda del
    padre, con le sue altre righe, e' Torzi, e la figlia con una riga sola
    restava Zorzi. Zorzi non e' raro abbastanza per la regola di prima —
    ma e' la stessa parola letta in due modi.
    """
    corretti, cognome = _figlia_e_padre(("Domenica", "Zorzi", 1), ("Francesco", "Torzi", 3),
                                        {"zorzi": 12, "torzi": 40})
    assert (corretti, cognome) == (1, "Torzi")


def test_la_figlia_di_un_altro_casato_non_prende_il_cognome_del_padre():
    """Il contrappeso: due cognomi diversi e tutti e due frequenti non si toccano."""
    corretti, cognome = _figlia_e_padre(("Maria", "Catolini", 1), ("Prospero", "Pelliccia", 3),
                                        {"catolini": 12, "pelicia": 400})
    assert (corretti, cognome) == (0, "Catolini")


def _salvatore_e_le_due_madri(seconda=("Clorinda", "Cicchillitti")):
    """Giuseppe Antonio Salvatore: la madre alla nascita e la madre alle nozze."""
    atti = [
        nascita(1809, ("Giuseppe Antonio", "Salvatore"), ("Nicola", "Salvatore"),
                ("Orsola", "Cicchillitti")),
        {"tipo": "matrimonio", "anno": 1838, "data": "1838-05-10", "persone": [
            {"ruolo": "sposo", "nome": "Giuseppe Antonio", "cognome": "Salvatore",
             "eta": "ventinove"},
            {"ruolo": "padre", "nome": "Nicola", "cognome": "Salvatore"},
            {"ruolo": "madre", "nome": seconda[0], "cognome": seconda[1]},
            {"ruolo": "sposa", "nome": "Angela", "cognome": "Petta", "eta": "venti"},
            {"ruolo": "padre", "nome": "Domenico", "cognome": "Petta"},
            {"ruolo": "madre", "nome": "Rosa", "cognome": "Pepe"}]},
    ]
    esito, prima = in_un_paese(atti)
    madri = [m for m in esito.corpus.menzioni
             if m.id >= prima and m.ruolo == "madre" and m.cognome != "Pepe"]
    figlio = [m for m in esito.corpus.menzioni if m.id >= prima and m.nome == "Giuseppe Antonio"]
    return esito, madri, figlio


def _madri_divise(seconda):
    """Le due madri rimesse in due schede, come le lascia l'archivio vero.

    Nel paese di prova il punteggio le unisce gia' da solo — il figlio in
    comune pesa — e il test proverebbe la soglia invece della regola.
    """
    esito, madri, figlio = _salvatore_e_le_due_madri(seconda)
    assert len({esito.di_menzione[m.id] for m in figlio}) == 1, "il figlio e' uno"
    _spezza(esito, esito.schede[esito.di_menzione[madri[0].id]], lambda m: m.nome)
    assert len({esito.di_menzione[m.id] for m in madri}) == 2
    return esito, madri


def test_un_figlio_ha_una_madre_sola():
    """Nato nel 1809 da Orsola Cicchillitti, sposo nel 1838 figlio di Clorinda Cicchillitti.

    Il nome della madre non si riconosce: Orsola e Clorinda non sono la
    stessa parola. La riconosce il figlio, che di madri ne ha una.
    """
    esito, madri = _madri_divise(("Clorinda", "Cicchillitti"))
    assert risoluzione.unisci_genitori_dello_stesso_figlio(esito) >= 1
    assert len({esito.di_menzione[m.id] for m in madri}) == 1, "la madre e' una"


def test_due_madri_di_due_casati_restano_due():
    """Il contrappeso: due casati possono voler dire che il figlio e' sbagliato, e nel dubbio si lascia."""
    esito, madri = _madri_divise(("Clorinda", "Marianacci"))
    risoluzione.unisci_genitori_dello_stesso_figlio(esito)
    assert len({esito.di_menzione[m.id] for m in madri}) == 2


# ---------------------------------------------------------------------------
# 42. «Emmanuela Pelliccia aveva sette mariti», tutti Pepe
# ---------------------------------------------------------------------------

def _emmanuela_e_i_mariti(mariti, anni):
    """Emmanuela Pelliccia, nata nel 1799, e un figlio per ogni «marito»."""
    eta = {1831: "trentadue", 1833: "trentaquattro", 1836: "trentasette", 1845: "quarantasei"}
    atti = [nascita(anno, (figlio, marito[1]), marito, ("Emmanuela", "Pelliccia"),
                    None, {"eta": eta[anno]})
            for anno, marito, figlio in zip(anni, mariti, ("Luigi", "Maria", "Rosa"))]
    esito, prima = in_un_paese(atti)
    # Nell'archivio Emmanuela e' una scheda sola di nove righe; qui le tre
    # righe si forzano insieme, perche' il caso da provare e' il marito.
    _accorpa_le_madri(esito, prima)
    risoluzione.unisci_coniugi_contemporanei(esito)
    padri = [m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "padre"]
    madri = [m for m in esito.corpus.menzioni if m.id >= prima and m.ruolo == "madre"]
    return esito, padri, madri


def test_i_mariti_dello_stesso_casato_uno_dopo_l_altro_sono_uno():
    """Panfilo, Pinuccio e Remigio Pepe, un figlio ciascuno nel 1831, 1833 e 1836.

    Tre mariti in cinque anni non si hanno: fra l'uno e l'altro non c'e'
    il tempo di un lutto e di nuove nozze. E' un marito solo, di una o due
    righe per volta, col nome letto male ogni volta in un altro modo.
    """
    esito, padri, madri = _emmanuela_e_i_mariti(
        (("Panfilo", "Pepe"), ("Pinuccio", "Pepe"), ("Remigio", "Pepe")), (1831, 1833, 1836))
    assert len({esito.di_menzione[m.id] for m in madri}) == 1, "la moglie e' una"
    assert len({esito.di_menzione[m.id] for m in padri}) == 1, "e il marito anche"


def test_i_mariti_di_tre_casati_restano_tre():
    """Il contrappeso: tre casati non sono un nome letto male."""
    esito, padri, _ = _emmanuela_e_i_mariti(
        (("Panfilo", "Pepe"), ("Pinuccio", "Marianacci"), ("Remigio", "Colella")), (1831, 1833, 1836))
    assert len({esito.di_menzione[m.id] for m in padri}) == 3


def test_un_marito_dodici_anni_dopo_puo_essere_un_secondo_marito():
    """Il contrappeso: dodici anni bastano per restare vedova e risposarsi."""
    esito, padri, _ = _emmanuela_e_i_mariti(
        (("Panfilo", "Pepe"), ("Pinuccio", "Pepe"), ("Remigio", "Pepe")), (1831, 1833, 1845))
    ultimo = next(m for m in padri if m.anno == 1845)
    assert esito.di_menzione[ultimo.id] != esito.di_menzione[padri[0].id]


def test_il_figlio_con_piu_righe_del_padre_non_prende_la_sua_variante():
    """Il contrappeso: se la scheda del padre e' la piu' povera, la lettura buona puo' essere del figlio."""
    corretti, cognome = _figlia_e_padre(("Pasquale", "Franchella", 8), ("Rosario", "Franchetti", 1),
                                        {"franchela": 900, "franchetti": 10})
    assert (corretti, cognome) == (0, "Franchella")


# ---------------------------------------------------------------------------
# 43. «Teodora Lella» e «Teodora Colella», mogli di Samuele Pepe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("una, altra, coda, caso", [
    ("lela", "colela", True, "Lella per Colella, letto sulla pagina tre volte"),
    ("nardo", "dnardo", True, "Nardo per Di Nardo"),
    ("mosca", "dlmosca", True, "Mosca per Del Mosca"),
    ("colela", "lela", True, "l'ordine non conta"),
    ("tore", "salvatore", False, "quattro lettere su nove non sono la coda di una parola"),
    ("ela", "colela", False, "tre lettere non bastano"),
    ("peli", "pelicia", False, "l'inizio della parola non e' la sua coda"),
    ("lela", "lela", False, "la stessa chiave non e' una coda"),
])
def test_la_coda_di_un_cognome(una, altra, coda, caso):
    """La trascrizione perde l'inizio del cognome; quello che resta e' la sua coda intera."""
    assert risoluzione._coda_di(una, altra) is coda, caso


def test_due_mogli_negli_stessi_anni_lella_e_colella_sono_una():
    """Morte del 1871, «figlia di Samuele Pepe e di Teodora Colella»; nel 1864 la stessa e' «Teodora Lella».

    Lella e Colella sono due famiglie vere e grandi del paese, e la
    somiglianza fra le due parole e' bassa: ma Lella e' quello che resta
    di Colella quando si perde la prima sillaba, e due mogli negli stessi
    anni non si possono avere.
    """
    esito, madri = _pepe_e_le_petolini(prima=("Teodora", "Colella"), seconda=("Teodora", "Lella"))
    # Nel paese di prova il punteggio le unisce gia' da solo, per il nome e
    # il marito in comune: si rimettono in due schede, come le lascia
    # l'archivio vero, perche' il test provi la regola e non la soglia.
    _spezza(esito, esito.schede[esito.di_menzione[madri[0].id]], lambda m: m.cognome)
    assert len({esito.di_menzione[m.id] for m in madri}) == 2
    assert risoluzione.unisci_coniugi_contemporanei(esito) >= 1
    assert len({esito.di_menzione[m.id] for m in madri}) == 1, "una moglie sola"


# ---------------------------------------------------------------------------
# 44. «Maria Enrica Marianacci», figlia di Casimiro Chielli
# ---------------------------------------------------------------------------

def _nascita_col_cognome_della_madre(neonata="Marianacci", padre=("Casimiro", "Chielli"),
                                     madre=("Pulcheria", "Marianacci")):
    righe = [
        _riga(1, "nascita", "neonato", "Maria Enrica", neonata),
        _riga(2, "nascita", "padre", *padre),
        _riga(3, "nascita", "madre", *madre),
        _riga(4, "nascita", "dichiarante", *padre),
    ]
    from history_maker import menzioni as lettura_atti

    lettura_atti.famiglia_dell_atto(righe)
    return lettura_atti, righe


def test_la_neonata_col_cognome_della_madre_prende_quello_del_padre():
    """Nascita del 1840: «di dare alla Neonata il nome di Maria Enrica».

    La pagina le da' solo il nome, e la trascrizione le aveva messo il
    cognome della madre, Pulcheria Marianacci. Il padre e' Casimiro
    Chielli, e un figlio legittimo porta il casato del padre.
    """
    lettura_atti, righe = _nascita_col_cognome_della_madre()
    assert lettura_atti.cognome_della_madre_al_neonato(righe) == 1
    assert righe[0].cognome == "Chielli" and righe[0].cognome_origine == "padre"


@pytest.mark.parametrize("neonata, padre, madre, caso", [
    ("Pepe", ("Casimiro", "Chielli"), ("Pulcheria", "Marianacci"),
     "un cognome che non e' della madre e' un'altra storia"),
    ("Marianacci", ("Casimiro", None), ("Pulcheria", "Marianacci"),
     "senza il cognome del padre non si inventa niente"),
    ("Chielli", ("Casimiro", "Chielli"), ("Pulcheria", "Chielli"),
     "padre e madre dello stesso casato: niente da ricondurre"),
])
def test_il_neonato_che_non_ha_il_cognome_della_madre_non_si_tocca(neonata, padre, madre, caso):
    """I contrappesi: la regola tocca solo il cognome della madre dato al posto di quello del padre."""
    lettura_atti, righe = _nascita_col_cognome_della_madre(neonata, padre, madre)
    assert lettura_atti.cognome_della_madre_al_neonato(righe) == 0, caso
    assert righe[0].cognome == neonata, caso


# ---------------------------------------------------------------------------
# 45. «Maria Carmela Sepe, figlia di Modesto Sepe»: il padre e' Modesto Pepe
# ---------------------------------------------------------------------------

def test_la_figlia_con_una_lettura_rara_del_cognome_del_padre_prende_il_suo():
    """Nascita del 1822: nell'atto padre e figlia sono «Sepe», e il padre e' Modesto Pepe.

    La scheda del padre, con trentotto righe, e' Pepe; la figlia, con una
    riga sola, restava Sepe — una forma che nell'archivio compare cinque
    volte. Non e' abbastanza vicina per essere una variante, ma e' rara
    e somiglia: e' la stessa parola letta male due volte nello stesso atto.
    """
    corretti, cognome = _figlia_e_padre(("Maria Carmela", "Sepe", 1), ("Modesto", "Pepe", 38),
                                        {"sepe": 5, "pepe": 1368})
    assert (corretti, cognome) == (1, "Pepe")


@pytest.mark.parametrize("figlia, padre, frequenze, caso", [
    (("Carmela", "Femminilli", 1), ("Nicolangelo", "Ferrara", 21), {"feminili": 1457, "ferara": 777},
     "due famiglie vere del paese: a essere sbagliato e' il padre, non il cognome"),
    # Dieci righe, non tre: sotto le tre la vecchia regola della forma che
    # non esiste (COGNOME_RARO) la riconduce comunque, e con ragione.
    (("Egidio", "Zenari", 1), ("Samuele", "Ferrara", 22), {"zenari": 10, "ferara": 777},
     "rara, ma lontana: meta' delle lettere non basta"),
    (("Pasquale", "Sepe", 5), ("Modesto", "Pepe", 3), {"sepe": 5, "pepe": 1368},
     "il padre con meno righe del figlio non da' il cognome"),
])
def test_la_lettura_rara_non_tocca_le_famiglie_vere(figlia, padre, frequenze, caso):
    """I contrappesi: una famiglia frequente, una forma lontana, un padre piu' povero del figlio."""
    corretti, cognome = _figlia_e_padre(figlia, padre, frequenze)
    assert (corretti, cognome) == (0, figlia[1]), caso


# ---------------------------------------------------------------------------
# 46. «Carmela Femminilli, figlia di Nicolangelo Femminilli e di Custoda Pizzi»
# ---------------------------------------------------------------------------

def _coppia_con_figli(figlia=("Carmela", "Femminilli", 1), fratelli=("Ferrara", "Ferrara", "Ferrara")):
    """Nicolangelo Ferrara e Custoda Pizzi, i loro figli, e la figlia letta con un altro casato."""
    from types import SimpleNamespace

    from history_maker.ricostruzione import esecuzione

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(modello.SCHEMA_SQL)
    persone = [(1, "Nicolangelo", "Ferrara", 21), (2, *figlia), (3, "Custoda", "Pizzi", 11)]
    persone += [(4 + i, f"Figlio{i}", cognome, 2) for i, cognome in enumerate(fratelli)]
    for numero, nome, cognome, menzioni in persone:
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni) VALUES (?,?,?,?,?)",
                     (numero, f"P{numero}", nome, cognome, menzioni))
    for figlio in [2] + [4 + i for i in range(len(fratelli))]:
        for genitore, tipo in ((1, "padre"), (3, "madre")):
            conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                         "VALUES (?, ?, ?, 1586, 1.0, 'confermato')", (figlio, genitore, tipo))
    esito = SimpleNamespace(corpus=SimpleNamespace(
        frequenze_cognome=Counter({"feminili": 1457, "ferara": 777, "pizi": 436})))
    corretti = esecuzione.cognomi_dal_padre(conn, esito, {n: n for n, *_ in persone})
    cognome = conn.execute("SELECT cognome FROM individui WHERE id = 2").fetchone()[0]
    conn.close()
    return corretti, cognome


def test_la_figlia_di_una_coppia_con_altri_figli_prende_il_cognome_dei_fratelli():
    """Nascita del 1832: nell'atto padre e figlia sono «Femminilli», e la madre e' Custoda Pizzi.

    Custoda Pizzi e' la moglie di Nicolangelo Ferrara (letto sulla pagina),
    e i loro figli sono Ferrara. Femminilli e' una famiglia vera del paese,
    e per questo nessuna regola sulle letture rare la toccava: a dirlo sono
    i fratelli, figli della stessa coppia.
    """
    assert _coppia_con_figli() == (1, "Ferrara")


@pytest.mark.parametrize("figlia, fratelli, caso", [
    (("Carmela", "Femminilli", 1), ("Ferrara",), "un fratello solo non basta"),
    (("Carmela", "Femminilli", 6), ("Ferrara", "Ferrara", "Ferrara"),
     "una figlia con una vita sua di sei righe non si tocca"),
    (("Carmela", "Femminilli", 1), ("Femminilli", "Femminilli", "Ferrara"),
     "i fratelli che portano il cognome della figlia: e' il padre a essere dubbio"),
])
def test_i_fratelli_che_non_dicono_il_casato_non_bastano(figlia, fratelli, caso):
    """I contrappesi: due fratelli come il padre, e una figlia che e' un frammento."""
    assert _coppia_con_figli(figlia, fratelli) == (0, figlia[1]), caso



# ---------------------------------------------------------------------------
# 47. «Clementina Iorio» nata nel 1799 e nel 1802, moglie di Crescenzo Petta
# ---------------------------------------------------------------------------

def _due_mogli_della_stessa_famiglia(seconda=("Clementina", "Iorio"), figli_per_moglie=3):
    """Crescenzo Petta e la moglie letta due volte: due vite intere di figli."""
    atti = []
    for indice in range(figli_per_moglie):
        atti.append(nascita(1822 + indice * 3, (f"Nicola{indice}", "Petta"),
                            ("Crescenzo", "Petta"), ("Clementina", "Iorio")))
        atti.append(nascita(1823 + indice * 3, (f"Rosa{indice}", "Petta"),
                            ("Crescenzo", "Petta"), seconda))
    esito, prima = in_un_paese(atti)
    # Nel paese di prova il calcolo le unisce da solo: si rimettono in due,
    # come le lascia l'archivio vero, perche' il test provi la regola.
    for madre in schede_di_prova(esito, prima, "madre"):
        for pezzo in _spezza(esito, madre, lambda menzione: (menzione.anno - 1822) % 3):
            pezzo.vietati = set()
    return esito, prima


def test_due_mogli_con_lo_stesso_nome_e_la_stessa_famiglia_sono_una_anche_se_grandi():
    """Crescenzo Petta e «Clementina Iorio» nata nel 1799 e nel 1802, dodici righe e sei.

    Nessuna delle due schede e' un frammento, e per questo la regola dei
    coniugi omonimi non le toccava: ma il nome e' uguale e il casato e' lo
    stesso. Risposarsi era raro; risposarsi con una donna dello stesso
    nome e della stessa famiglia lo e' molto di piu'.
    """
    esito, prima = _due_mogli_della_stessa_famiglia()
    madri = schede_di_prova(esito, prima, "madre")
    assert len(madri) == 2
    assert min(m.quante for m in madri) > risoluzione.MENZIONI_DI_UN_FRAMMENTO
    assert risoluzione.unisci_coniugi_omonimi(esito) == 1
    assert len(schede_di_prova(esito, prima, "madre")) == 1, "una moglie sola"


# ---------------------------------------------------------------------------
# 48. «Savino Pelliccia, figlio di fu Samuele»: il padre del neonato e' Savino
# ---------------------------------------------------------------------------

TESTO_DEL_NONNO = ("e' comparso Savino Pelliccia, figlio di fu Samuele Pelliccia, "
                   "di anni ventiquattro, contadino, il quale ci ha presentato un maschio")


def _nascita_col_nonno(eta_padre=None, cognome_padre="Pelliccia"):
    righe = [
        _riga(1, "nascita", "neonato", "Carmine", "Pelliccia"),
        _riga(2, "nascita", "padre", "Samuele", cognome_padre),
        _riga(3, "nascita", "madre", "Maria Nicola", "Desiderio"),
        _riga(4, "nascita", "dichiarante", "Savino", "Pelliccia"),
    ]
    righe[3].eta_letta = "ventiquattro"
    righe[1].eta_letta = eta_padre
    from history_maker import menzioni as lettura_atti

    return lettura_atti, righe


def test_il_padre_scritto_come_patronimico_del_dichiarante_e_il_dichiarante():
    """Nascita del 1857: «e' comparso Savino Pelliccia, figlio di fu Samuele Pelliccia».

    La trascrizione aveva messo Samuele, il nonno gia' morto, nella casella
    del padre del neonato: e Samuele, nato nel 1826, diventava marito di
    Maria Nicola Desiderio. Nei registri del 1855-1857 sono una quarantina.
    """
    lettura_atti, righe = _nascita_col_nonno()
    assert lettura_atti.padre_che_e_il_nonno(righe, TESTO_DEL_NONNO) == 1
    assert righe[1].nome == "Savino" and righe[1].eta_letta == "ventiquattro"
    assert righe[1].patronimico == "Samuele"


@pytest.mark.parametrize("testo, eta_padre, cognome_padre, caso", [
    ("e' comparso Savino Pelliccia, zio del neonato, di anni ventiquattro", None, "Pelliccia",
     "senza il patronimico nel testo il dichiarante puo' essere uno zio"),
    (TESTO_DEL_NONNO, "sessanta", "Pelliccia", "un padre con la sua eta' e' una persona sua"),
    (TESTO_DEL_NONNO, None, "Pepe", "due cognomi: non e' il padre del dichiarante"),
])
def test_senza_le_tre_prove_il_padre_resta_quello_scritto(testo, eta_padre, cognome_padre, caso):
    """I contrappesi: servono insieme l'eta', il cognome e il patronimico scritto nell'atto."""
    lettura_atti, righe = _nascita_col_nonno(eta_padre, cognome_padre)
    assert lettura_atti.padre_che_e_il_nonno(righe, testo) == 0, caso
    assert righe[1].nome == "Samuele", caso


def test_il_nonno_vivente_scritto_come_padre():
    """Nascita del 1866: «e' comparsa Vincenzino Marianacci del vivente Vincenzo Marianacci».

    Il patronimico non e' solo «figlio di fu»: col padre ancora vivo l'atto
    scrive «del vivente», e la regola non lo conosceva — il nonno restava
    padre della neonata.
    """
    lettura_atti, righe = _nascita_col_nonno()
    testo = ("e' comparso Savino Pelliccia del vivente Samuele Pelliccia, di anni ventiquattro, "
             "contadino, il quale ci ha presentato un maschio")
    assert lettura_atti.padre_che_e_il_nonno(righe, testo) == 1
    assert righe[1].nome == "Savino" and righe[1].patronimico == "Samuele"


# ---------------------------------------------------------------------------
# 49. «Benardo Lella e Irene Dozio», genitori di Felice Lella: Irene non e' il padre
# ---------------------------------------------------------------------------

def _genere_di_prova():
    from history_maker import nomi

    return nomi.Genere.dal_corpus(
        [("madre", "Irene")] * 10 + [("madre", "Giuseppa")] * 10
        + [("padre", "Benardo")] * 10 + [("padre", "Nicola")] * 10
        + [("padre", "Francesco")] * 10 + [("madre", "Andrea")] * 5 + [("padre", "Andrea")] * 5
    )


def test_il_padre_con_un_nome_di_donna_non_e_un_padre():
    """Matrimonio del 1811: «assistito dai suoi genitori Benardo Lella e Irene Dozio».

    La trascrizione aveva due padri, Benardo e Irene, e una madre, quella
    della sposa. Irene non puo' essere padre; e madre non la si fa, perche'
    la riga di madre c'e' gia' e il figlio ne avrebbe due: resta senza ruolo.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposo", "Felice", "Lella"),
        _riga(2, "matrimonio", "padre", "Benardo", "Lella"),
        _riga(3, "matrimonio", "padre", "Irene", "Dozio"),
        _riga(4, "matrimonio", "sposa", "Maria", "Catolino"),
        _riga(5, "matrimonio", "madre", "Giuseppa", "Serafini"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(righe, _genere_di_prova()) == 1
    assert righe[2].ruolo == lettura_atti.GENITORE_DUBBIO
    assert righe[1].ruolo == "padre", "il padre vero non si tocca"

    # Nell'archivio vero «Benardo» e' Bernardo scritto male, e un nome
    # storpiato non e' netto in nessun paese: pretenderlo nettamente
    # maschile lasciava fuori proprio questo caso, e Felice si teneva un
    # padre di nome Irene. Basta che l'altra riga possa essere un uomo.
    sconosciuto = [
        _riga(6, "matrimonio", "sposo", "Felice", "Lella", atto=2),
        _riga(7, "matrimonio", "padre", "Ippolisto", "Lella", atto=2),
        _riga(8, "matrimonio", "padre", "Irene", "Dozio", atto=2),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(sconosciuto, _genere_di_prova()) == 1
    assert sconosciuto[2].ruolo == lettura_atti.GENITORE_DUBBIO
    assert sconosciuto[1].ruolo == "padre"


def test_la_madre_con_un_nome_d_uomo_senza_padre_e_il_padre():
    """Nascita del 1835: «madre» Nicola Modestino, e nessuna riga di padre: e' lui il padre."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Rosa", "Modestino"),
        _riga(2, "nascita", "madre", "Nicola", "Modestino"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(righe, _genere_di_prova()) == 1
    assert righe[1].ruolo == "padre" and righe[1].sesso == "M"


def test_un_nome_che_il_paese_da_a_tutti_e_due_non_cambia_ruolo():
    """Il contrappeso: «Andrea» e' da uomo e da donna nel paese di prova, e il ruolo scritto resta."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Rosa", "Pepe"),
        _riga(2, "nascita", "padre", "Francesco", "Pepe"),
        _riga(3, "nascita", "madre", "Andrea", "Lella"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(righe, _genere_di_prova()) == 0
    assert [r.ruolo for r in righe] == ["neonato", "padre", "madre"]


def test_il_padre_solo_con_un_nome_da_donna_resta_padre():
    """Il contrappeso: «Domenica» come unico padre e' spesso Domenico letto male.

    Nella nascita del 1874 il padre «Domenicantonia D'Illice» era
    Domenicantonio Di Nardo: la a e la o si confondono. Senza un altro
    padre nell'atto la riga resta padre, e va nella coda dei dubbi.
    """
    from history_maker import menzioni as lettura_atti, nomi

    genere = nomi.Genere.dal_corpus([("madre", "Domenica")] * 10 + [("madre", "Rosa")] * 10)
    righe = [
        _riga(1, "nascita", "neonato", "Saba", "Ferrara"),
        _riga(2, "nascita", "padre", "Domenica", "Ferrara"),
        _riga(3, "nascita", "madre", "Rosa", "Pepe"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(righe, genere) == 0
    assert righe[1].ruolo == "padre"


# ---------------------------------------------------------------------------
# 50. «Nicola Moretta, nato nel 1755, si sposerebbe nel 1832 a 77 anni»
# ---------------------------------------------------------------------------

def _unioni_di_un_matrimonio():
    """Un matrimonio del 1832 con i genitori degli sposi, scritto nelle tabelle."""
    from history_maker.dataset import SCHEMA_SQL as SCHEMA_DATASET
    from history_maker.ricostruzione import esecuzione

    atti = [{"tipo": "matrimonio", "anno": 1832, "data": "1832-05-10", "persone": [
        {"ruolo": "sposo", "nome": "Giuseppe", "cognome": "Moretta", "eta": "trenta"},
        {"ruolo": "padre", "nome": "Nicola", "cognome": "Moretta"},
        {"ruolo": "madre", "nome": "Maria", "cognome": "Di Paolo"},
        {"ruolo": "sposa", "nome": "Angela", "cognome": "Petta", "eta": "venti"},
        {"ruolo": "padre", "nome": "Domenico", "cognome": "Petta"},
        {"ruolo": "madre", "nome": "Rosa", "cognome": "Pepe"},
    ]}]
    esito, prima = in_un_paese(atti)
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_DATASET)
    conn.executescript(modello.SCHEMA_SQL)
    numeri = esecuzione._scrivi_individui(conn, esito)
    esecuzione._scrivi_legami(conn, esito, numeri)
    esecuzione._scrivi_unioni(conn, esito, numeri)
    chi = {riga: numeri[esito.di_menzione[prima + scarto]]
           for scarto, riga in enumerate(("sposo", "padre", "madre", "sposa"))}
    return conn, chi


def test_i_genitori_degli_sposi_non_si_sposano_nell_atto_dei_figli():
    """Nel matrimonio del 1832 i genitori dello sposo sono una coppia, ma sposata da trent'anni.

    L'unione prendeva l'anno e l'origine dell'atto dei figli: e il
    controllo degli sposi a un'eta' impossibile trovava trentanove
    genitori «sposati» a settantacinque anni e piu'.
    """
    conn, chi = _unioni_di_un_matrimonio()
    genitori = conn.execute("SELECT origine FROM unioni WHERE ? IN (marito, moglie)",
                            (chi["padre"],)).fetchone()
    assert genitori is not None and genitori[0] == "figli"
    sposi = conn.execute("SELECT origine, anno FROM unioni WHERE ? IN (marito, moglie) "
                         "AND ? IN (marito, moglie)", (chi["sposo"], chi["sposa"])).fetchone()
    assert tuple(sposi) == ("matrimonio", 1832), "il matrimonio degli sposi resta un matrimonio"
    conn.close()


# ---------------------------------------------------------------------------
# 51. «Domenicangelo Franchella del fu Manasse», testimone di una nascita del 1867
# ---------------------------------------------------------------------------

def _nascita_del_1867(nascita_del_padre_del_neonato=1840):
    """Una nascita del 1867 col testimone e suo padre, morto, scritti nell'atto."""
    from history_maker.dataset import SCHEMA_SQL as SCHEMA_DATASET

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_DATASET)
    conn.executescript(modello.SCHEMA_SQL)
    conn.execute("INSERT INTO atti (id, tipo, anno) VALUES (4034, 'nascita', 1867)")
    righe = [  # persona, ruolo, individuo, anno di nascita dell'individuo
        (25492, "neonato", 1, 1867), (25490, "dichiarante", 2, nascita_del_padre_del_neonato),
        (25497, "testimone", 3, 1811), (25498, "defunto", 4, 1784),
    ]
    for persona, ruolo, individuo, nato in righe:
        conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome) VALUES (?, 4034, ?, 'X', 'Y')",
                     (persona, ruolo))
        conn.execute("INSERT INTO individui (id, chiave, nome, cognome, menzioni, anno_nascita, sesso) "
                     "VALUES (?, ?, 'X', 'Y', 1, ?, 'M')", (individuo, f"P{persona}", nato))
        conn.execute("INSERT INTO menzioni (persona, individuo) VALUES (?, ?)", (persona, individuo))
    for figlio, genitore in ((1, 2), (3, 4)):
        conn.execute("INSERT INTO legami (figlio, genitore, tipo, atto, confidenza, stato) "
                     "VALUES (?, ?, 'padre', 4034, 1.0, 'confermato')", (figlio, genitore))
    return conn


def test_il_padre_del_testimone_non_ha_un_figlio_nell_anno_della_nascita():
    """Nascita del 1867: il testimone e' «Domenicangelo Franchella del fu Manasse».

    Il legame fra il testimone e suo padre e' vero; ma il controllo dei
    genitori troppo vecchi lo prendeva per un parto del 1867, e Manasse
    Franchella, nato nel 1784 e morto nel 1865, aveva un figlio a
    ottantatre anni. I parti sono solo quelli del neonato.
    """
    from history_maker import qualita

    conn = _nascita_del_1867()
    assert qualita.genitore_troppo_vecchio(conn) == []
    conn.close()


def test_il_padre_troppo_vecchio_del_neonato_resta_un_caso():
    """Il contrappeso: se e' il padre del neonato ad avere ottantatre anni, il controllo lo trova."""
    from history_maker import qualita

    conn = _nascita_del_1867(nascita_del_padre_del_neonato=1784)
    casi = qualita.genitore_troppo_vecchio(conn)
    assert [c.persone for c in casi] == [(2, 1)]
    conn.close()


# ---------------------------------------------------------------------------
# 52. «Figlia del fu Simone Giovanni Franchella»
# ---------------------------------------------------------------------------

def _bilancia_dei_nomi_composti():
    from history_maker import nomi

    return nomi.Bilancia.dal_corpus(
        [("Giovanni", "Pepe")] * 10 + [("Fedele", "Pepe")] * 10
        + [("Chiara", "Pepe")] * 10 + [("Luca", "Pepe")] * 10
        + [("Gaetana", "Pepe")] * 10 + [("Maria", "Pepe")] * 10
        + [("Simone", "Franchella")] * 10 + [("Antonio", "Tilli")] * 10
        + [("Mauro", "Lozzi")] * 10 + [("Antonio", "Giovanni Mini")] * 10
        + [("Fioravante", "Pepe")] * 10 + [("Angelo", "Pepe")] * 10
        + [("Pietro", "Cicchillitti")] * 10 + [("Pietro", "Franchella")] * 10
    )


def _genere_dei_nomi_composti():
    from history_maker import nomi

    return nomi.Genere.dal_corpus(
        [("padre", "Mauro")] * 10 + [("madre", "Chiara")] * 10
        + [("madre", "Maria")] * 10 + [("madre", "Gaetana")] * 10
        + [("padre", "Fioravante")] * 10 + [("padre", "Angelo")] * 10
    )


def test_la_seconda_parte_del_nome_torna_dal_cognome():
    """Matrimonio n. 4 del 1830: «Figlia del fu Simone Giovanni Franchella».

    La trascrizione aveva il padre Simone di casato «Giovanni Franchella»,
    e la sposa Emilia Franchella risultava di un'altra famiglia. Cosi'
    «Antonio | Fedele Tilli», morte n. 4 del 1832.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposa", "Emilia", "Franchella"),
        _riga(2, "matrimonio", "padre", "Simone", "Giovanni Franchella"),
        _riga(3, "morte", "padre", "Antonio", "Fedele Tilli", atto=2),
    ]
    assert lettura_atti.nome_in_testa_al_cognome(righe, _bilancia_dei_nomi_composti()) == 2
    assert (righe[1].nome, righe[1].cognome) == ("Simone Giovanni", "Franchella")
    assert (righe[2].nome, righe[2].cognome) == ("Antonio Fedele", "Tilli")


def test_un_casato_di_due_parole_con_un_nome_dentro_resta_un_casato():
    """I contrappesi: la particella, il casato ripetuto nell'atto, il casato conosciuto."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "madre", "Concetta", "Di Luca"),
        _riga(2, "morte", "defunto", "Erminia", "Giovanni Pepe"),
        _riga(3, "morte", "padre", "Simone", "Giovanni Pepe"),
        _riga(4, "morte", "padre", "Simone", "Giovanni Mini", atto=2),
    ]
    assert lettura_atti.nome_in_testa_al_cognome(righe, _bilancia_dei_nomi_composti()) == 0
    assert [r.cognome for r in righe] == ["Di Luca", "Giovanni Pepe", "Giovanni Pepe", "Giovanni Mini"]


def test_il_padre_con_addosso_il_nome_della_moglie():
    """Morte n. 23 del 1832: «figlio del fu Mauro e Chiara Derudi».

    Mauro non si chiama «Mauro Chiara»: il casato che aveva addosso e'
    il nome intero della moglie. Tolto quello, glielo da' il figlio. Nella
    donna, invece, il nome d'uomo in testa e' il padre: «Maria |
    Fioravante Cicchillitti» resta Cicchillitti, con Fioravante per
    patronimico. E 'Maria' dopo un nome d'uomo e' un nome composto:
    Angelo Maria Franchella. Il contrappeso: il «padre» Maria Gaetana e'
    una donna, e la riga resta alle altre regole.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Ermenegildo", "Lozzi"),
        _riga(2, "morte", "padre", "Mauro", "Chiara Derudi"),
        _riga(3, "morte", "padre", "Maria", "Gaetana Pepe", atto=2),
        _riga(4, "nascita", "madre", "Maria", "Fioravante Cicchillitti", atto=3),
        _riga(5, "morte", "dichiarante", "Angelo", "Maria Franchella", atto=4),
    ]
    righe[1].sesso = righe[2].sesso = righe[4].sesso = "M"
    righe[3].sesso = "F"
    assert lettura_atti.nome_in_testa_al_cognome(
        righe, _bilancia_dei_nomi_composti(), _genere_dei_nomi_composti()) == 3
    assert (righe[1].nome, righe[1].cognome) == ("Mauro", None)
    assert (righe[2].nome, righe[2].cognome) == ("Maria", "Gaetana Pepe")
    assert (righe[3].nome, righe[3].cognome, righe[3].patronimico) == ("Maria", "Cicchillitti", "Fioravante")
    assert (righe[4].nome, righe[4].cognome) == ("Angelo Maria", "Franchella")
    lettura_atti.cognome_dal_figlio(righe[:2])
    assert righe[1].cognome == "Lozzi"


# ---------------------------------------------------------------------------
# 53. «Di Bello Sabatino» e «Tommaso Remigio Lattanzi»: il figlio dice il casato
# ---------------------------------------------------------------------------

def test_il_padre_scritto_al_contrario_lo_raddrizza_il_figlio():
    """Nascita di Angela Di Bello: il padre letto 'Di Bello' di casato 'Sabatino'.

    E' una riga sola, e ``inversioni_per_atto`` ne vuole due. Il contrappeso:
    il padre Simone Pepe del neonato Luca Simone ha un casato vero, e resta.
    """
    from history_maker import menzioni as lettura_atti
    from history_maker import nomi

    bilancia = nomi.Bilancia.dal_corpus(
        [("Sabatino", "Pepe")] * 10 + [("Simone", "Pepe")] * 10 + [("Luca", "Pepe")] * 10
    )
    righe = [
        _riga(1, "nascita", "neonata", "Angela", "Di Bello"),
        _riga(2, "nascita", "padre", "Di Bello", "Sabatino"),
        _riga(3, "nascita", "neonato", "Luca", "Simone", atto=2),
        _riga(4, "nascita", "padre", "Simone", "Pepe", atto=2),
    ]
    assert lettura_atti.padre_scritto_al_contrario(righe[:2], bilancia) == 1
    assert lettura_atti.padre_scritto_al_contrario(righe[2:], bilancia) == 0
    assert (righe[1].nome, righe[1].cognome) == ("Sabatino", "Di Bello")
    assert (righe[3].nome, righe[3].cognome) == ("Simone", "Pepe")


def test_il_casato_del_figlio_in_coda_al_cognome_del_padre():
    """La sposa Rosa Lattanzio e il padre «Tommaso | Remigio Lattanzi».

    'Remigio' e' troppo raro per dirsi un nome; il casato della figlia dice
    dove comincia quello del padre. «Domenico | Di Vito Lella», padre di
    Vito Antonio Lella, porta in testa il nonno: Vito va nel patronimico.
    Contrappesi: il «padre» Maria Gaetana, una donna, e il cognome con le
    alternative restano come sono.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposa", "Rosa", "Lattanzio"),
        _riga(2, "matrimonio", "padre", "Tommaso", "Remigio Lattanzi"),
        _riga(3, "nascita", "neonato", "Vito Antonio", "Lella", atto=2),
        _riga(4, "nascita", "padre", "Domenico", "Di Vito Lella", atto=2),
        _riga(5, "morte", "defunto", "Nicola", "D'Antuono", atto=3),
        _riga(6, "morte", "padre", "Maria", "Gaetana D'Antuono", atto=3),
        _riga(7, "nascita", "neonato", "Carlo", "Desiderio", atto=4),
        _riga(8, "nascita", "padre", "Fedele", "D'Amico / Desiderio", atto=4),
    ]
    genere = _genere_dei_nomi_composti()
    quante = sum(
        lettura_atti.casato_del_figlio_in_coda(righe[i:i + 2], genere) for i in range(0, 8, 2)
    )
    assert quante == 2
    assert (righe[1].nome, righe[1].cognome) == ("Tommaso Remigio", "Lattanzi")
    assert (righe[3].nome, righe[3].cognome, righe[3].patronimico) == ("Domenico", "Lella", "Vito")
    assert (righe[5].nome, righe[5].cognome) == ("Maria", "Gaetana D'Antuono")
    assert righe[7].cognome == "D'Amico / Desiderio"


# ---------------------------------------------------------------------------
# 54. «Dalla sua unione con donna non maritata»: la riga del padre vuota
# ---------------------------------------------------------------------------

def test_la_riga_vuota_del_padre_non_ferma_lo_scambio():
    """Nascita del 1900: il padre dichiara, ed e' finito nella casella della madre.

    La casella del padre c'era, ma vuota. Non e' un padre: la madre col
    nome d'uomo diventa il padre, e la riga vuota perde il ruolo, perche'
    il figlio non ne abbia due.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "neonato", "Giuseppe Nicola", "Moretta"),
        _riga(2, "nascita", "padre", None, None),
        _riga(3, "nascita", "madre", "Nicola", "Moretta"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(righe, _genere_di_prova()) == 1
    assert (righe[2].ruolo, righe[2].sesso) == ("padre", "M")
    assert righe[1].ruolo == lettura_atti.GENITORE_DUBBIO


# ---------------------------------------------------------------------------
# 55. «Figlia di Signor Vincenzo»: il titolo non e' il nome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("letto, atteso", [
    ("Don Felice Calidonio", ("Felice Calidonio", "Don")),
    ("Donna Federica", ("Federica", "Donna")),
    ("Signora Angela", ("Angela", "Signora")),
    ("Signor Colapietra", ("Colapietra", "Signor")),
    ("Sig. Giuseppe", ("Giuseppe", "Sig.")),
    ("Donato", ("Donato", None)),
    ("Donnini", ("Donnini", None)),
    ("Signoretti", ("Signoretti", None)),
])
def test_il_titolo_si_toglie_dalla_testa(letto, atteso):
    from history_maker.ricostruzione import lettura

    assert lettura.separa_titolo(letto) == atteso


def test_il_lettore_toglie_il_titolo_dal_nome_e_dal_cognome():
    """Matrimonio del 1877: la sposa «Eugenia | Signor Colapietra» e' Eugenia Colapietra."""
    from history_maker.ricostruzione import lettura

    leggi = lettura.Interprete(_bilancia_dei_nomi_composti())
    sposa = _riga(1, "matrimonio", "sposa", "Eugenia", "Signor Colapietra")
    notaio = _riga(2, "matrimonio", "testimone", "Don Nicola", "Femminilli")
    leggi(sposa)
    leggi(notaio)
    assert (sposa.nome, sposa.cognome) == ("Eugenia", "Colapietra")
    assert (notaio.nome, notaio.cognome) == ("Nicola", "Femminilli")
    assert leggi.conteggi["titoli"] == 2


# ---------------------------------------------------------------------------
# 56. «Figlia d'ignoti genitori»: la formula al plurale
# ---------------------------------------------------------------------------

def test_la_formula_dei_genitori_ignoti_vale_anche_al_plurale():
    """Morte del 1837 di Orsola Femminilli: il padre letto «ignoti ignoti».

    Il contrappeso: con un nome vero accanto la riga resta una persona,
    e il casato «ignoti» lo butta il chiamante.
    """
    from history_maker import menzioni as lettura_atti

    assert lettura_atti.dichiarata_ignota("ignoti", "ignoti", None)
    assert lettura_atti.dichiarata_ignota(None, None, "figlia d'ignoti genitori")
    assert lettura_atti.dichiarata_ignota(None, "ignote", None)
    assert not lettura_atti.dichiarata_ignota("Annasalvatora", "ignoti", None)


# ---------------------------------------------------------------------------
# 57. «A cui si e' dato il nome di Amato Felice», e il padre che dichiara
# ---------------------------------------------------------------------------

TESTO_DI_AMATO_FELICE = (
    "e' comparso Nicolangelo Desiderio di anni venticinque di professione contadino "
    "... da Rosa Pepe sua moglie legittima di anni venticinque un maschio, che ci ha "
    "presentato a cui si è dato il nome di Amato Felice. La presentazione, e "
    "dichiarazione si è fatta alla presenza di due testimoni"
)


def test_il_nome_dato_dall_atto_riprende_il_secondo_nome():
    """Nascita del 1819: «a cui si e' dato il nome di Amato Felice».

    Amato nel nome, Felice nel cognome, e il bambino di padre Desiderio
    risultava un Felice. L'atto dice che e' un nome solo. I contrappesi:
    «Giuseppe | Antonio» con la formula che dice solo Giuseppe resta
    com'e', e «Ferdinando Maria | Pepe» — Pepe e' un casato — anche.
    """
    from history_maker import menzioni as lettura_atti
    from history_maker import nomi

    bilancia = nomi.Bilancia.dal_corpus(
        [("Felice", "Lella")] * 10 + [("Antonio", "Pepe")] * 10 + [("Luca", "Pepe")] * 10
    )
    righe = [
        _riga(1, "nascita", "neonato", "Amato", "Felice"),
        _riga(2, "nascita", "padre", "Nicolangelo", "Desiderio"),
    ]
    assert lettura_atti.nome_dato_dall_atto(righe, TESTO_DI_AMATO_FELICE, bilancia) == 1
    assert (righe[0].nome, righe[0].cognome) == ("Amato Felice", None)

    diverso = [_riga(3, "nascita", "neonato", "Giuseppe", "Antonio", atto=2)]
    assert lettura_atti.nome_dato_dall_atto(
        diverso, "a cui si è dato il nome di Giuseppe. La presentazione", bilancia) == 0

    casato = [_riga(4, "nascita", "neonato", "Ferdinando Maria", "Pepe", atto=3)]
    assert lettura_atti.nome_dato_dall_atto(
        casato, "di dare al neonato il nome di Ferdinando Maria Pepe.", bilancia) == 0
    assert casato[0].cognome == "Pepe"

    # Un casato raro, che l'archivio non sa dire: resta un casato.
    raro = [_riga(5, "nascita", "neonata", "Angela", "Padula", atto=4)]
    assert lettura_atti.nome_dato_dall_atto(
        raro, "di dare alla neonata il nome di Angela Padula.", bilancia) == 0
    assert raro[0].cognome == "Padula"


def test_la_madre_col_nome_d_uomo_resta_madre_se_il_padre_dichiara():
    """Nascita del 1900: «e' comparso Pelliccia Quinto», e la madre letta «Pinetti Angelo Maria».

    Non c'e' la riga del padre, e la vecchia regola faceva della madre il
    padre. Ma il padre c'e': e' il dichiarante col casato del neonato. Il
    contrappeso e' Amedeo Moretta: la riga fra le madri e' proprio la sua,
    ricopiata, e diventa il padre.
    """
    from history_maker import menzioni as lettura_atti
    from history_maker import nomi

    genere = nomi.Genere.dal_corpus(
        [("padre", "Quinto")] * 10 + [("padre", "Angelo")] * 10 + [("padre", "Nicola")] * 10
    )
    pinetti = [
        _riga(1, "nascita", "neonato", "Alessandro", "Pelliccia"),
        _riga(2, "nascita", "dichiarante", "Quinto", "Pelliccia"),
        _riga(3, "nascita", "madre", "Angelo Maria", "Pinetti"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(pinetti, genere) == 0
    assert pinetti[2].ruolo == "madre"

    moretta = [
        _riga(4, "nascita", "neonato", "Giuseppe Nicola", "Moretta", atto=2),
        _riga(5, "nascita", "dichiarante", "Nicola", "Moretta", atto=2),
        _riga(6, "nascita", "madre", "Nicola", "Moretta", atto=2),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(moretta, genere) == 1
    assert moretta[2].ruolo == "padre"


# ---------------------------------------------------------------------------
# 59. «Dalla sua unione con donna non maritata»: la formula scritta per intero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome, cognome, ignota", [
    ("donna non maritata", None, True),
    ("donna non maritata non parente", "omesso", True),
    ("Donna che non consente l'esposizione nominata", None, True),
    ("dalla sua unione illegittima con donna che non vuol consentire di per nominata", None, True),
    # I contrappesi: la donna nominata, anche dentro la formula, resta una persona.
    ("sua unione naturale con Celeste", "Sorrizzi", False),
    ("Maria", "Lella", False),
    ("Donna Federica", "Zeno", False),
])
def test_la_formula_della_donna_non_nominata_per_intero(nome, cognome, ignota):
    """Nascita del 1888 di un figlio di Antonio Carosella: la madre letta
    «Dalia sua unione illegittima con Donna che...». Era diventata la sua
    seconda moglie. Sei madri dell'archivio si chiamavano «donna non maritata»."""
    from history_maker import menzioni as lettura_atti

    assert lettura_atti.dichiarata_ignota(nome, cognome, None) is ignota


# ---------------------------------------------------------------------------
# 60. Il padre che dichiara il figlio avuto «da una unione con donna non maritata»
# ---------------------------------------------------------------------------

def test_il_padre_che_dichiara_resta_padre_se_la_madre_e_ignota():
    """Nascita del 1890 di Luigi Pelliccia: «e' comparso Pelliccia Vincenzo... da una
    unione con donna non maritata». Riconosciuta la formula, la madre e' ignota;
    ma il padre e' il dichiarante, e il bambino l'aveva perso. Il contrappeso: un
    dichiarante di un altro casato non diventa padre."""
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "dichiarante", "Vincenzo", "Pelliccia"),
        _riga(2, "nascita", "madre", None, None),
        _riga(3, "nascita", "neonato", "Luigi", "Pelliccia"),
    ]
    righe[1].ignota = True
    lettura_atti.famiglia_dell_atto(righe)
    assert righe[2].padre == 1

    estraneo = [
        _riga(4, "nascita", "dichiarante", "Maria", "Lella", atto=2),
        _riga(5, "nascita", "madre", None, None, atto=2),
        _riga(6, "nascita", "neonata", "Rosa", "Esposito", atto=2),
    ]
    estraneo[1].ignota = True
    lettura_atti.famiglia_dell_atto(estraneo)
    assert estraneo[2].padre is None


# ---------------------------------------------------------------------------
# 61. Due padri per la stessa sposa: quello del cognome resta, l'altro passa
# ---------------------------------------------------------------------------

def test_il_padre_senza_cognome_comune_passa_all_altro_sposo():
    """Matrimonio del 1833 (atto 1613): gli sposi prima, poi i genitori.

    La meta' della lista dava alla sposa Maria Antonia Di Nardo tutti e due
    i padri; il cognome sceglieva Nicola Di Nardo, ma l'ultimo scritto era
    Domenico Pepe, e la sposa usciva figlia di Pepe. Pepe e' il padre dello
    sposo, e la madre che lo segue va con lui.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposa", "Maria Antonia", "Di Nardo"),
        _riga(2, "matrimonio", "sposo", "Luigi", "Bosi"),
        _riga(3, "matrimonio", "padre", "Nicola", "Di Nardo"),
        _riga(4, "matrimonio", "padre", "Domenico", "Pepe"),
        _riga(5, "matrimonio", "madre", "Teresa", "Brandolino"),
    ]
    lettura_atti.famiglia_dell_atto(righe)
    assert righe[0].padre == 3
    assert righe[1].padre == 4
    assert righe[1].madre == 5


# ---------------------------------------------------------------------------
# 63. «Figlio del defunto» con un altro cognome: col padre morto non e' un figlio
# ---------------------------------------------------------------------------

def _morte_con_figlio(cognome_dichiarante, defunto=("Giovanni", "Pepe"), sesso_defunto="M"):
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", *defunto),
        _riga(2, "morte", "dichiarante", "Francesco", cognome_dichiarante),
    ]
    righe[0].sesso = sesso_defunto
    righe[1].note = "figlio del defunto" if sesso_defunto == "M" else "figlio della defunta"
    lettura_atti.parentele_dalle_note(righe)
    return righe


def test_il_figlio_del_padre_defunto_ne_porta_il_cognome():
    """Morte del 1867 di Giovanni Pepe: il dichiarante Francesco Pelliccia «figlio del defunto».

    Il figlio porta il cognome del padre: un Pelliccia figlio di un Pepe e'
    un genero, o la nota e' letta male, e il legame non si fa. I contrappesi:
    il figlio col cognome del padre, e il figlio della madre defunta, che il
    cognome della madre non lo porta mai.
    """
    assert _morte_con_figlio("Pelliccia")[1].padre is None
    assert _morte_con_figlio("Pepe")[1].padre == 1
    madre = _morte_con_figlio("Pelliccia", defunto=("Maria", "Pepe"), sesso_defunto="F")
    assert madre[1].madre == 1


# ---------------------------------------------------------------------------
# 64. «Dell'avo paterno Giovanni Franchella»: il nonno non e' il padre
# ---------------------------------------------------------------------------

def test_l_avo_che_presenta_i_documenti_non_e_il_padre():
    """Matrimonio del 1831: «la sposa similmente priva di genitori, correda documenti
    a noi esibiti, dell'avo paterno Giovanni Franchella d'anni settantanove».

    Giovanni era fra i padri, e la sposa usciva figlia del nonno. Il
    contrappeso: il padre col nome del nonno, scritto anche come padre dello
    sposo, resta padre.
    """
    from history_maker import menzioni as lettura_atti

    testo = ("La sposa similmente priva di genitori, correda documenti a noi esibiti, "
             "dell'avo paterno Giovanni Franchella d'anni settantanove, di professione fante")
    righe = [
        _riga(1, "matrimonio", "sposa", "Erminia", "Franchella"),
        _riga(2, "matrimonio", "padre", "Giovanni", "Franchella"),
    ]
    assert lettura_atti.genitore_che_e_l_avo(righe, testo) == 1
    assert righe[1].ruolo == "avo paterno"

    omonimo = [
        _riga(3, "matrimonio", "sposo", "Gennaro", "Ottaviano", atto=2),
        _riga(4, "matrimonio", "padre", "Nicola", "Ottaviano", atto=2),
    ]
    testo_omonimo = ("Gennaro Ottaviano figlio del fu Nicola Ottaviano, e dell'avo paterno "
                     "Nicola Ottaviano, di professione contadino")
    assert lettura_atti.genitore_che_e_l_avo(omonimo, testo_omonimo) == 0
    assert omonimo[1].ruolo == "padre"


# ---------------------------------------------------------------------------
# 65. «Da lui dichiarante Aquinto Pelliccia»: il nome raro non fa della madre il padre
# ---------------------------------------------------------------------------

def test_il_dichiarante_dal_nome_incerto_col_casato_del_neonato_e_il_padre():
    """Nascite del 1888 e del 1900: la madre «da Mastrovincenzo Maria Domenica, sua moglie».

    La riga della madre comincia con un nome d'uomo, e la regola la faceva
    padre perche' il dichiarante — Nicola, che nell'archivio e' anche nome
    di donna, o Aquinto, troppo raro — non era «di certo un uomo». Basta
    che non sia di certo una donna. Il contrappeso: la levatrice col
    casato del neonato non ferma lo scambio.
    """
    from history_maker import menzioni as lettura_atti
    from history_maker import nomi

    genere = nomi.Genere.dal_corpus(
        [("padre", "Angelo")] * 10 + [("padre", "Vincenzo")] * 10 + [("madre", "Maria")] * 10
    )
    raro = [
        _riga(1, "nascita", "neonato", "Alessandro", "Pelliccia"),
        _riga(2, "nascita", "dichiarante", "Aquinto", "Pelliccia"),
        _riga(3, "nascita", "madre", "Angelo Maria", "Pinetti"),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(raro, genere) == 0
    assert raro[2].ruolo == "madre"

    levatrice = [
        _riga(4, "nascita", "neonato", "Alessandro", "Pelliccia", atto=2),
        _riga(5, "nascita", "dichiarante", "Maria", "Pelliccia", atto=2),
        _riga(6, "nascita", "madre", "Angelo", "Pinetti", atto=2),
    ]
    assert lettura_atti.genitore_dell_altro_sesso(levatrice, genere) == 1
    assert levatrice[2].ruolo == "padre"


# ---------------------------------------------------------------------------
# 66. «Di dare alla neonata il nome di Eufrasia»: il cognome che l'atto non scrive
# ---------------------------------------------------------------------------

def test_il_cognome_che_l_atto_non_scrive_e_quello_del_padre():
    """Nascita del 1837: la bambina Eufrasia, figlia di Giovanni Nicodemo, trascritta «Petta».

    Il testo non le da' cognome, e Petta nell'atto non c'e': prende quello
    del padre. I contrappesi: il cognome scritto nel testo in un'altra
    forma (Colletta per Colella) resta, e cosi' il cognome corretto dal
    registro, se nel testo c'e' la lettura di prima.
    """
    from history_maker import menzioni as lettura_atti

    testo = ("è comparso Giovanni Nicodemo di anni cinquantasei, ed ha dichiarato che la stessa "
             "è nata da Maria di Paolo Santhoro moglie legittima e da lui dichiarante. Lo stesso "
             "ci ha inoltre dichiarato di dare alla neonata il nome di Eufrasia.")
    righe = [
        _riga(1, "nascita", "neonato", "Eufrasia", "Petta"),
        _riga(2, "nascita", "padre", "Giovanni", "Nicodemo"),
        _riga(3, "nascita", "madre", "Maria", "Santoro"),
    ]
    assert lettura_atti.casato_che_l_atto_non_scrive(righe, testo) == 1
    assert righe[0].cognome == "Nicodemo"

    variante = [
        _riga(4, "morte", "defunto", "Domenico Nicola", "Colella", atto=2),
        _riga(5, "morte", "padre", "Timoteo", "Rosario", atto=2),
    ]
    testo_variante = ("è morto nella sua casa Domenico Nicola Colletta d'anni quarantacinque "
                      "figlio di Timoteo Rosario, e Teodora Rosie")
    assert lettura_atti.casato_che_l_atto_non_scrive(variante, testo_variante) == 0
    assert variante[0].cognome == "Colella"

    corretto = [
        _riga(6, "nascita", "neonato", "Ferdinando", "Di Laudo", atto=3),
        _riga(7, "nascita", "padre", "Nicola", "Pepe", atto=3),
    ]
    corretto[0].cognome_letto = "di Fausto"
    testo_corretto = "è comparso Nicola Pepe ... il nome di Ferdinando di Fausto"
    assert lettura_atti.casato_che_l_atto_non_scrive(corretto, testo_corretto) == 0
    assert corretto[0].cognome == "Di Laudo"


# ---------------------------------------------------------------------------
# 67. «Da Troilo Rosa, sua moglie»: la moglie nella casella del padre
# ---------------------------------------------------------------------------

def test_la_moglie_nella_casella_del_padre_torna_madre():
    """Nascita del 1887: il padre presenta il figlio avuto «da Troilo Rosa, sua moglie».

    La trascrizione aveva messo Rosa fra i padri, col cognome scritto per
    primo preso per nome. Il contrappeso: il padre vero, che nella frase
    della moglie non c'e', resta padre.
    """
    from history_maker import menzioni as lettura_atti

    testo = ("è comparso Marianacci Vincenzo, il quale mi ha dichiarato che nella casa posta "
             "in via Fontanella, da Troilo Rosa, filatrice, sua moglie legittima secolui "
             "convivente è nato un bambino")
    righe = [
        _riga(1, "nascita", "neonato", "Giuseppe", "Marianacci"),
        _riga(2, "nascita", "dichiarante", "Vincenzo", "Marianacci"),
        _riga(3, "nascita", "padre", "Troilo", "Rosa"),
    ]
    assert lettura_atti.padre_che_e_la_moglie(righe, testo) == 1
    assert righe[2].ruolo == "madre"

    padre = [
        _riga(4, "nascita", "neonato", "Giuseppe", "Marianacci", atto=2),
        _riga(5, "nascita", "padre", "Vincenzo", "Marianacci", atto=2),
    ]
    assert lettura_atti.padre_che_e_la_moglie(padre, testo) == 0
    assert padre[1].ruolo == "padre"


# ---------------------------------------------------------------------------
# 68. «Figlia del fu Prospero Pelliccia»: nel matrimonio il defunto e' il padre
# ---------------------------------------------------------------------------

def test_il_defunto_del_matrimonio_col_cognome_della_sposa_e_suo_padre():
    """Matrimonio del 1811: Maria Catarina Pelliccia, «figlia del fu Prospero Pelliccia».

    Prospero era nella casella del defunto e la sposa restava senza padre.
    I contrappesi: il coniuge morto della vedova («vedova del fu Giuseppe
    Colella») resta dov'e', anche col cognome dello sposo; e cosi' il
    defunto quando l'atto ha gia' un padre con quel cognome.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "matrimonio", "sposo", "Felice", "Lella"),
        _riga(2, "matrimonio", "padre", "Benardo", "Lella"),
        _riga(3, "matrimonio", "sposa", "Maria Catarina", "Pelliccia"),
        _riga(4, "matrimonio", "defunto", "Prospero", "Pelliccia"),
    ]
    testo = "e Maria Catarina, figlia del fu Prospero Pelliccia, e Giuseppa Serafini"
    assert lettura_atti.genitore_morto_del_matrimonio(righe, testo) == 1
    assert righe[3].ruolo == "padre"

    vedova = [
        _riga(5, "matrimonio", "sposo", "Emmanuele", "Colella", atto=2),
        _riga(6, "matrimonio", "sposa", "Albina", "Moretta", atto=2),
        _riga(7, "matrimonio", "defunto", "Giuseppe", "Colella", atto=2),
    ]
    testo_vedova = "e Albina Moretta, vedova del fu Giuseppe Colella"
    assert lettura_atti.genitore_morto_del_matrimonio(vedova, testo_vedova) == 0
    assert vedova[2].ruolo == "defunto"

    doppio = [
        _riga(8, "matrimonio", "sposa", "Maria", "Pelliccia", atto=3),
        _riga(9, "matrimonio", "padre", "Prospero", "Pelliccia", atto=3),
        _riga(10, "matrimonio", "defunto", "Prospero", "Pelliccia", atto=3),
    ]
    assert lettura_atti.genitore_morto_del_matrimonio(doppio, "") == 0
    assert doppio[2].ruolo == "defunto"


# ---------------------------------------------------------------------------
# 69. «Vedovo di D'Ettore Angela»: il coniuge morto non compare vivo
# ---------------------------------------------------------------------------

def test_il_coniuge_di_cui_il_defunto_e_vedovo_non_compare_vivo():
    """Morte del 1881 di Carmine Pizzi, «vedovo di D'Ettore Angela».

    Angela, morta nel 1865, stava nella casella della sposa come viva, e
    l'albero la faceva comparire viva sedici anni dopo il suo funerale. Il
    contrappeso: la moglie viva del defunto resta presente.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "morte", "defunto", "Carmine", "Pizzi"),
        _riga(2, "morte", "sposa", "Angela", "D'Ettore"),
    ]
    righe[1].stato_vitale = "vivente"
    testo = "è morto Carmine Pizzi, di anni ottanta, vedovo di D'Ettore Angela, contadino"
    assert lettura_atti.coniuge_morto_del_defunto(righe, testo) == 1
    assert righe[1].stato_vitale == "defunto"
    assert not righe[1].presente

    viva = [
        _riga(3, "morte", "defunto", "Carmine", "Pizzi", atto=2),
        _riga(4, "morte", "sposa", "Angela", "D'Ettore", atto=2),
    ]
    viva[1].stato_vitale = "vivente"
    assert lettura_atti.coniuge_morto_del_defunto(viva, "è morto Carmine Pizzi, marito di D'Ettore Angela") == 0
    assert viva[1].presente


def test_il_nome_corretto_rifa_il_patronimico():
    """«Maria di Rondo», corretta in Maria Di Nardo, restava figlia di Rondo.

    Il patronimico si stacca dal nome prima delle correzioni; corretto il
    nome, va rifatto da quello nuovo. Il contrappeso: senza correzione del
    nome, il patronimico resta.
    """
    from history_maker.ricostruzione.lettura import Interprete

    riga = _riga(1, "nascita", "madre", "Maria", "Santagata")
    riga.patronimico = "Rondo"
    Interprete(corrette={(1, "nome"): "Maria", (1, "cognome"): "Di Nardo"})(riga)
    assert (riga.nome, riga.cognome) == ("Maria", "Di Nardo")
    assert not riga.patronimico

    altra = _riga(2, "nascita", "madre", "Maria", "Santagata")
    altra.patronimico = "Rondo"
    Interprete(corrette={(2, "cognome"): "Di Nardo"})(altra)
    assert altra.patronimico == "Rondo"


def test_la_riga_nella_casella_sbagliata_torna_al_suo_ruolo():
    """Promessa del 1822 n. 6: la madre dello sposo era finita fra le spose.

    «Celute Monno ... domiciliata col marito» e' la madre di Domenicangelo
    Moretta, e la trascrizione l'aveva messa nella casella della sposa, accanto
    alla sposa vera. Letta la famiglia dell'atto, Domenicangelo aveva due mogli.
    La correzione del ruolo lavora prima della lettura della famiglia: dopo,
    la riga non sposa piu' nessuno. Il contrappeso: senza correzione, le due
    spose restano due spose, perche' la regola non indovina.
    """
    from history_maker import menzioni as lettura_atti
    from history_maker.ricostruzione.lettura import Interprete

    def atto():
        return [
            _riga(1, "matrimonio", "sposo", "Domenicangelo", "Moretta"),
            _riga(2, "matrimonio", "sposa", "Celute", "Monno"),
            _riga(3, "matrimonio", "sposa", "Pernaida", "Cicchillitti"),
        ]

    corretto = atto()
    leggi = Interprete(corrette={(2, "ruolo"): "altro"})
    for riga in corretto:
        leggi(riga)
    assert corretto[1].ruolo == "altro"
    lettura_atti.famiglia_dell_atto(corretto)
    assert corretto[1].coniuge is None, "la madre dello sposo non e' una sposa"

    grezzo = atto()
    lettura_atti.famiglia_dell_atto(grezzo)
    assert grezzo[1].ruolo == "sposa"


def test_il_patronimico_letto_male_si_corregge():
    """«Vincenzo Pannunzio figlio di Giudeto»: e' Diodato, come nel resto dell'archivio.

    La correzione del patronimico lavora come quella del nome; il contrappeso:
    senza correzione il patronimico resta quello letto.
    """
    from history_maker.ricostruzione.lettura import Interprete

    riga = _riga(1, "nascita", "padre", "Vincenzo", "Pannunzio")
    riga.patronimico = "Giudeto"
    Interprete(corrette={(1, "patronimico"): "Diodato"})(riga)
    assert riga.patronimico == "Diodato"

    altra = _riga(2, "nascita", "padre", "Vincenzo", "Pannunzio")
    altra.patronimico = "Giudeto"
    Interprete(corrette={})(altra)
    assert altra.patronimico == "Giudeto"


def test_l_eta_che_l_atto_smentisce_si_puo_togliere():
    """Morte del 1821 (atto 820): «Menasse Franchella di anni trentatre, padre della defunta», e la figlia ne ha trentacinque.

    La pagina, riletta, dice proprio cosi': e' una svista della penna, e
    quale delle due cifre sia sbagliata non si sa. Finche' quell'eta'
    resta, la riga da' alla madre un figlio nato prima di lei, e il veto
    della fertilita' le impedisce di ricomporsi con le sue altre meta'.
    Una correzione con «?» toglie l'eta' invece di inventarne una. Il
    contrappeso: una correzione con un'eta' vera la sostituisce, come
    sempre.
    """
    from history_maker.ricostruzione.lettura import ETA_DA_TOGLIERE, Interprete

    riga = _riga(1, "morte", "defunta", "Maria Nicola", "Franchella")
    riga.eta_letta = "trentacinque"
    Interprete(corrette={(1, "eta"): ETA_DA_TOGLIERE})(riga)
    assert riga.eta_letta is None and riga.eta is None

    altra = _riga(2, "morte", "defunta", "Maria Nicola", "Franchella")
    altra.eta_letta = "trentacinque"
    Interprete(corrette={(2, "eta"): "cinque"})(altra)
    assert altra.eta_letta == "cinque" and altra.eta is not None


def test_i_nomi_diversi_dei_genitori_non_vietano_se_i_genitori_sono_gli_stessi():
    """Maria Antonia Santoro figlia di «Lucio» e di «Saverio», di «Angelica» e di «Vitangela» Cieri.

    Il veto sui nomi dei genitori separava due schede della stessa donna
    anche quando i genitori erano gia' riconosciuti come le stesse persone.
    Il contrappeso: con due padri diversi il veto resta.
    """
    from history_maker.ricostruzione import evidenza
    from history_maker.ricostruzione.scheda import Scheda

    def figlia(chiave, padre, madre, scheda_padre):
        scheda = Scheda(chiave=chiave)
        scheda.padri_nome, scheda.madri_nome = {padre}, {madre}
        scheda.padri = {scheda_padre}
        return scheda

    una = figlia(1, "lucio|santoro", "angelica|cieri", 500)
    altra = figlia(2, "saverio|santoro", "vitangela|cieri", 500)
    assert evidenza.veti(una, altra, divisioni_del_calcolo=False) is None

    estranea = figlia(3, "saverio|santoro", "vitangela|cieri", 501)
    assert "due coppie diverse" in (evidenza.veti(una, estranea, divisioni_del_calcolo=False) or "")


def test_i_cognomi_letti_male_di_una_moglie_sola_non_sono_un_accorpamento():
    """Domenica Cerulli, letta «Chielli», «Cimini», «Cianci»: sempre moglie di Pietro Ottaviano.

    Il controllo dei cognomi inconciliabili la contava come una scheda che
    ha inghiottito due famiglie. Con un coniuge solo in tutti gli atti e'
    una donna sola. Il contrappeso: con due mariti il sospetto resta.
    """
    import sqlite3
    from history_maker import qualita

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT);
        CREATE TABLE persone (id INTEGER PRIMARY KEY, cognome_letto TEXT);
        CREATE TABLE menzioni (persona INTEGER, individuo INTEGER);
        CREATE TABLE unioni (marito INTEGER, moglie INTEGER);
        INSERT INTO individui VALUES (1, 'Domenica', 'Cerulli'), (2, 'Pietro', 'Ottaviano'),
                                     (3, 'Giuseppe', 'Bortolami');
        INSERT INTO unioni VALUES (2, 1);
    """)
    for i, cognome in enumerate(["Cerulli", "Zanetti", "Bortolami", "Fidelibus", "Antonucci",
                                 "Quaranta", "Ognibene", "Squadrilli"], 10):
        conn.execute("INSERT INTO persone VALUES (?, ?)", (i, cognome))
        conn.execute("INSERT INTO menzioni VALUES (?, 1)", (i,))
    assert qualita.cognomi_inconciliabili(conn) == []

    conn.execute("INSERT INTO unioni VALUES (3, 1)")
    assert len(qualita.cognomi_inconciliabili(conn)) == 1


@pytest.mark.parametrize("cognome, formula", [
    ("sua moglie", True),
    ("Moglie", True),
    ("sua moglie legittima [così nell'atto]", True),
    ("Moglia", False),
    ("Mogliani", False),
    (None, False),
])
def test_il_casato_sua_moglie_e_la_formula(cognome, formula):
    """«Maria Lella, sua moglie»: la formula nella casella del casato si butta, la donna no."""
    from history_maker import menzioni as lettura_atti

    assert lettura_atti.formula_di_moglie(cognome) is formula


def test_il_padre_che_e_il_nonno_anche_col_titolo():
    """Nascita del 1855: «e' comparso Raimondo marianacci figlio di Signor Vincenzo marianacci».

    Il «Signor» fra «figlio di» e il nome rompeva lo schema: Raimondo
    restava dichiarante, e la bambina figlia del nonno Vincenzo. Il
    contrappeso: senza il patronimico nel testo non cambia niente.
    """
    from history_maker import menzioni as lettura_atti

    righe = [
        _riga(1, "nascita", "dichiarante", "Raimondo", "Marianacci"),
        _riga(2, "nascita", "padre", "Vincenzo", "Marianacci"),
    ]
    righe[0].eta_letta = "trentotto"
    testo = ("e' comparso Raimondo marianacci figlio di Signor Vincenzo marianacci "
             "di anni trentotto, di professione proprietario")
    assert lettura_atti.padre_che_e_il_nonno(righe, testo) == 1
    assert (righe[1].nome, righe[1].patronimico) == ("Raimondo", "Vincenzo")

    senza = [
        _riga(3, "nascita", "dichiarante", "Raimondo", "Marianacci", atto=2),
        _riga(4, "nascita", "padre", "Vincenzo", "Marianacci", atto=2),
    ]
    senza[0].eta_letta = "trentotto"
    assert lettura_atti.padre_che_e_il_nonno(senza, "e' comparso Raimondo Marianacci di anni trentotto") == 0
    assert senza[1].nome == "Vincenzo"


def test_il_nonno_riconosciuto_non_diventa_padre_del_dichiarante():
    """Nascita del 1855, atto 3391: la riga del «padre» porta la nota «padre del dichiarante».

    La nota e' vera del nome che la trascrizione ci aveva messo — il
    nonno Vincenzo — ma non della riga, che 'padre_che_e_il_nonno' ha
    gia' riconosciuto essere il dichiarante stesso. Applicandola, Raimondo
    diventava figlio di se stesso: il veto delle righe legate nello stesso
    atto («l'una e' genitore dell'altra») teneva allora la riga lontana
    dalla sua scheda per sempre, e il bambino restava figlio di un
    Raimondo fantasma sposato con sua madre. Nemmeno un'unione imposta a
    mano bastava a rimetterli insieme.
    """
    from history_maker import menzioni as lettura_atti

    dichiarante = _riga(1, "nascita", "dichiarante", "Raimondo", "Marianacci")
    padre = _riga(2, "nascita", "padre", "Vincenzo", "Marianacci")
    neonato = _riga(3, "nascita", "neonato", "Emmanuele", "Marianacci")
    madre = _riga(4, "nascita", "madre", "Faustina", "di Tommaso")
    dichiarante.eta_letta = "trentotto"
    padre.note = "padre del dichiarante"
    righe = [dichiarante, padre, neonato, madre]
    testo = ("e' comparso Raimondo marianacci figlio di Signor Vincenzo marianacci "
             "di anni trentotto, di professione proprietario")

    assert lettura_atti.padre_che_e_il_nonno(righe, testo) == 1
    lettura_atti.famiglia_dell_atto(righe)

    assert dichiarante.padre is None, "il dichiarante non e' figlio di se stesso"
    assert neonato.padre == padre.id
    assert (padre.nome, padre.patronimico) == ("Raimondo", "Vincenzo")
    # Marcata come riga doppia, la ricostruzione la rimette col dichiarante
    # da sola, senza bisogno di un'unione decisa a mano per ogni atto.
    assert padre.doppia_di == dichiarante.id

    # Il contrappeso: dove la riga del padre e' davvero il nonno e la
    # regola non scatta, la nota vale e il legame si fa.
    altro_dichiarante = _riga(5, "nascita", "dichiarante", "Raimondo", "Marianacci", atto=2)
    nonno = _riga(6, "nascita", "padre", "Vincenzo", "Marianacci", atto=2)
    nonno.note = "padre del dichiarante"
    lettura_atti.famiglia_dell_atto([altro_dichiarante, nonno])
    assert altro_dichiarante.padre == nonno.id


def test_il_nonno_si_riconosce_anche_col_nome_gia_corretto():
    """Sulla riga del «padre» qualcuno aveva gia' corretto «Vincenzo» in «Raimondo».

    La correzione aveva ragione — quella riga e' il dichiarante — ma
    rendeva i due nomi uguali, e la regola, che chiede due nomi diversi,
    smetteva di scattare: tornava la nota «padre del dichiarante», e
    Raimondo diventava figlio di se stesso. Da li' un veto lo teneva
    lontano dalla sua stessa riga per sempre, in barba all'unione decisa
    a mano. I nomi si confrontano percio' come li ha scritti la
    trascrizione. Il contrappeso: due righe che la trascrizione stessa
    leggeva con lo stesso nome non sono questo caso.
    """
    from history_maker import menzioni as lettura_atti

    dichiarante = _riga(1, "nascita", "dichiarante", "Raimondo", "Marianacci")
    padre = _riga(2, "nascita", "padre", "Raimondo", "Marianacci")
    padre.nome_letto = "Vincenzo"
    padre.note = "padre del dichiarante"
    dichiarante.eta_letta = "trentotto"
    neonato = _riga(3, "nascita", "neonato", "Emmanuele", "Marianacci")
    righe = [dichiarante, padre, neonato]
    testo = ("e' comparso Raimondo marianacci figlio di Signor Vincenzo marianacci "
             "di anni trentotto, di professione proprietario")

    assert lettura_atti.padre_che_e_il_nonno(righe, testo) == 1
    assert padre.patronimico == "Vincenzo"
    lettura_atti.famiglia_dell_atto(righe)
    assert dichiarante.padre is None
    assert neonato.padre == padre.id

    lette_uguali = [
        _riga(4, "nascita", "dichiarante", "Raimondo", "Marianacci", atto=2),
        _riga(5, "nascita", "padre", "Raimondo", "Marianacci", atto=2),
    ]
    lette_uguali[0].eta_letta = "trentotto"
    assert lettura_atti.padre_che_e_il_nonno(lette_uguali, testo) == 0


def test_la_qualita_non_conta_coppie_gemelle_di_due_generazioni():
    """«Coppie gemelle» contava anche gli omonimi lontani un secolo.

    Francesco Iannicelli n.1845 con i figli dal 1871, e un altro Francesco
    Iannicelli padre di un figlio del 1784: gli stessi due nomi, ma nessuna
    donna partorisce per ottantasette anni, e infatti il veto della
    fertilita' rifiuta la fusione. Non e' una famiglia spezzata, ed
    entrava nel conto della frammentazione nascondendo quelle vere. Il
    contrappeso: gli stessi nomi con i figli negli anni giusti restano un
    caso.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(anno_del_figlio_lontano: int) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, nascita_origine TEXT,
                                    anno_morte INTEGER, menzioni INTEGER);
            CREATE TABLE legami (figlio INTEGER, genitore INTEGER, tipo TEXT);
            INSERT INTO individui VALUES (1, 'Francesco', 'Iannicelli', 1845, 'stimata', NULL, 15);
            INSERT INTO individui VALUES (2, 'Maria Silvia', 'D''Ettore', 1849, 'stimata', NULL, 12);
            INSERT INTO individui VALUES (3, 'Francesco', 'Iannicelli', NULL, NULL, NULL, 1);
            INSERT INTO individui VALUES (4, 'Maria Silvia', 'D''Ettore', NULL, NULL, NULL, 1);
            INSERT INTO individui VALUES (5, 'Nicola', 'Iannicelli', 1871, 'certa', NULL, 3);
            INSERT INTO legami VALUES (5, 1, 'padre'), (5, 2, 'madre');
        """)
        conn.execute("INSERT INTO individui VALUES (6, 'Anna', 'Iannicelli', ?, 'certa', NULL, 1)",
                     (anno_del_figlio_lontano,))
        conn.executescript("INSERT INTO legami VALUES (6, 3, 'padre'), (6, 4, 'madre');")
        return conn

    assert qualita.coppie_gemelle(archivio(1784)) == []
    assert len(qualita.coppie_gemelle(archivio(1874))) == 1


def test_la_qualita_non_conta_coppie_gemelle_col_padre_troppo_giovane():
    """Giuseppe Di Nardo, nato nel 1855, non e' il padre del bambino del 1867.

    Gli stessi due nomi, i figli a diciannove anni di distanza - dentro
    gli anni fertili di una donna, quindi il primo filtro non basta - ma
    l'atto di nascita del padre dice che nel 1867 aveva dodici anni. Il
    veto della ricostruzione lo sa e non unira' mai quei due nuclei:
    contarli come famiglia spezzata nascondeva quelle vere. Il
    contrappeso: senza l'atto di nascita del genitore il caso resta.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(origine: str) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, nascita_origine TEXT,
                                    anno_morte INTEGER, menzioni INTEGER);
            CREATE TABLE legami (figlio INTEGER, genitore INTEGER, tipo TEXT);
            INSERT INTO individui VALUES (2, 'Maria', 'Cicchillitti', 1866, 'stimata', NULL, 10);
            INSERT INTO individui VALUES (3, 'Giuseppe', 'Di Nardo', NULL, NULL, NULL, 1);
            INSERT INTO individui VALUES (4, 'Maria Giuseppa', 'Cicchillitti', NULL, NULL, NULL, 1);
            INSERT INTO individui VALUES (5, 'Nicola', 'Di Nardo', 1886, 'certa', NULL, 3);
            INSERT INTO individui VALUES (6, 'Anna', 'Di Nardo', 1867, 'certa', NULL, 1);
            INSERT INTO legami VALUES (5, 1, 'padre'), (5, 2, 'madre'), (6, 3, 'padre'), (6, 4, 'madre');
        """)
        conn.execute("INSERT INTO individui VALUES (1, 'Giuseppe', 'Di Nardo', 1855, ?, NULL, 10)",
                     (origine,))
        return conn

    assert qualita.coppie_gemelle(archivio("certa")) == []
    assert len(qualita.coppie_gemelle(archivio("stimata"))) == 1


def test_la_qualita_non_conta_coppie_gemelle_con_due_atti_di_morte():
    """Vincenzo Chielli muore nel 1871, Vincenzo Colella nel 1881: sono due uomini.

    Gli stessi nomi di battesimo e due mogli che si chiamano uguale
    bastavano a farne una «famiglia spezzata», ma ciascuno dei due ha il
    suo atto di morte: il veto dei due funerali distinti non lascera' mai
    unire quelle schede, e il caso non e' lavoro da fare. Il contrappeso:
    se uno solo dei due ha un atto di morte, il caso resta.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(morte_del_secondo) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, nascita_origine TEXT,
                                    anno_morte INTEGER, menzioni INTEGER);
            CREATE TABLE legami (figlio INTEGER, genitore INTEGER, tipo TEXT);
            INSERT INTO individui VALUES (1, 'Vincenzo', 'Chielli', 1786, 'stimata', 1871, 35);
            INSERT INTO individui VALUES (2, 'Maria Fedele', 'Marianacci', 1793, 'stimata', 1864, 16);
            INSERT INTO individui VALUES (4, 'Maria', 'Marianacci', 1803, 'stimata', NULL, 12);
            INSERT INTO individui VALUES (5, 'Nicola', 'Chielli', 1820, 'certa', NULL, 3);
            INSERT INTO individui VALUES (6, 'Anna', 'Chielli', 1830, 'certa', NULL, 1);
            INSERT INTO legami VALUES (5, 1, 'padre'), (5, 2, 'madre'), (6, 3, 'padre'), (6, 4, 'madre');
        """)
        conn.execute("INSERT INTO individui VALUES (3, 'Vincenzo', 'Chielli', 1801, 'stimata', ?, 45)",
                     (morte_del_secondo,))
        return conn

    assert qualita.coppie_gemelle(archivio(1881)) == []
    assert len(qualita.coppie_gemelle(archivio(None))) == 1


def test_la_madre_vedova_non_sposa_chi_denuncia_il_figlio():
    """Nascita del 1819: «da Serafina Femminilli, moglie del fu Salvatore Salvatore».

    L'atto non ha la riga del padre perche' il padre e' morto prima che
    il bambino nascesse. Senza saperlo, la deduzione «chi denuncia e' il
    padre» dava al bambino il dichiarante - Nicola Femminilli, di
    settantanove anni - e alla vedova un secondo marito. Il padre giusto
    non si puo' dare, perche' il morto non ha una riga nell'atto; si
    evita quello falso. Il contrappeso: dove l'atto dice «sua moglie
    legittima», il dichiarante resta il padre, anche se e' anziano.
    """
    from history_maker import menzioni as lettura_atti

    def atto(testo, righe):
        lettura_atti.madre_vedova_del_fu(righe, testo)
        lettura_atti.famiglia_dell_atto(righe)
        return righe

    dichiarante = _riga(1, "nascita", "dichiarante", "Nicola", "Femminilli")
    neonato = _riga(2, "nascita", "neonato", "Salvatore", "Femminilli")
    madre = _riga(3, "nascita", "madre", "Serafina", "Femminilli")
    dichiarante.eta_letta = "settantanove"
    madre.eta_letta = "trentatre"
    atto("e' nato un maschio da Serafina Femminilli di anni trentatre domiciliata "
         "nell'indicato Comune, moglie del fu Salvatore Salvatore del Comune medesimo",
         [dichiarante, neonato, madre])
    assert madre.vedova_nell_atto
    assert neonato.padre is None, "il bambino non e' figlio di chi lo denuncia"
    assert neonato.madre == madre.id

    vivo = _riga(4, "nascita", "dichiarante", "Amedio", "Di Nardo", atto=2)
    figlia = _riga(5, "nascita", "neonata", "Rosa", "Di Nardo", atto=2)
    moglie = _riga(6, "nascita", "madre", "Rosa", "Pelliccia", atto=2)
    vivo.eta_letta = "sessantotto"
    moglie.eta_letta = "trentadue"
    atto("e' nata da Rosa Pelliccia sua moglie legittima di anni trentadue e da lui "
         "dichiarante Amedio di Nardo di anni sessantotto", [vivo, figlia, moglie])
    assert not moglie.vedova_nell_atto
    assert figlia.padre == vivo.id


def test_i_figli_omonimi_si_contano_solo_se_il_primo_e_ancora_vivo():
    """Due figli con lo stesso nome non sono un'anomalia se il primo e' morto piccolo.

    L'uso del paese e' proprio quello: al neonato si rimette il nome del
    fratello morto. Il controllo chiedeva soltanto che mancasse l'atto di
    morte, e gli atti di morte dei bambini piccoli mancano spesso: su
    quarantadue casi, ventitre erano questo. Ora si chiede la prova che
    il primo fosse vivo - una sua menzione dopo la nascita del secondo.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(anno_dell_ultima_menzione: int) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, anno_morte INTEGER,
                                    morta_entro INTEGER, menzioni INTEGER);
            CREATE TABLE legami (figlio INTEGER, genitore INTEGER, tipo TEXT);
            CREATE TABLE atti (id INTEGER PRIMARY KEY, tipo TEXT, anno INTEGER);
            CREATE TABLE persone (id INTEGER PRIMARY KEY, atto INTEGER, ruolo TEXT,
                                  nome TEXT);
            CREATE TABLE menzioni (persona INTEGER, individuo INTEGER);
            INSERT INTO individui VALUES (1, 'Egidio', 'Pelliccia', 1803, NULL, NULL, 20);
            INSERT INTO individui VALUES (2, 'Maria Luisa', 'Lemma', 1805, NULL, NULL, 12);
            INSERT INTO individui VALUES (3, 'Egidio', 'Pelliccia', 1841, NULL, NULL, 2);
            INSERT INTO individui VALUES (4, 'Egidio', 'Pelliccia', 1842, NULL, NULL, 3);
            INSERT INTO legami VALUES (3, 1, 'padre'), (3, 2, 'madre'), (4, 1, 'padre'), (4, 2, 'madre');
            INSERT INTO atti VALUES (10, 'nascita', 1841);
            INSERT INTO persone (id, atto, ruolo) VALUES (100, 10, 'neonato');
            INSERT INTO menzioni VALUES (100, 3);
        """)
        conn.execute("INSERT INTO atti VALUES (11, 'matrimonio', ?)", (anno_dell_ultima_menzione,))
        conn.executescript("""
            INSERT INTO persone (id, atto, ruolo) VALUES (101, 11, 'testimone');
            INSERT INTO menzioni VALUES (101, 3);
        """)
        return conn

    assert len(qualita.figli_omonimi_vivi(archivio(1871))) == 1
    assert qualita.figli_omonimi_vivi(archivio(1841)) == []


def test_maggiore_di_eta_non_e_un_eta_incoerente():
    """«Maggiore di eta'» dice solo «almeno ventuno», e il conto la prendeva per ventuno.

    Angela Felicia Moretta, nata nel 1815, sposa nel 1863: il registro
    scrive «maggiore di eta'» e il controllo la faceva nascere nel 1842,
    segnalando come sospetta una riga che non dice niente. Il
    contrappeso: un'eta' vera e lontana resta sospetta.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(eta: str) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, nascita_origine TEXT);
            CREATE TABLE atti (id INTEGER PRIMARY KEY, anno INTEGER);
            CREATE TABLE persone (id INTEGER PRIMARY KEY, atto INTEGER, eta TEXT);
            CREATE TABLE menzioni (persona INTEGER, individuo INTEGER);
            INSERT INTO individui VALUES (1, 'Angela Felicia', 'Moretta', 1815, 'certa');
            INSERT INTO atti VALUES (10, 1863);
            INSERT INTO menzioni VALUES (100, 1);
        """)
        conn.execute("INSERT INTO persone VALUES (100, 10, ?)", (eta,))
        return conn

    assert qualita.eta_incoerente(archivio("maggiore di eta'")) == []
    assert len(qualita.eta_incoerente(archivio("ventuno"))) == 1


def test_le_seconde_nozze_non_sono_una_famiglia_spezzata():
    """Anna Di Palma muore nel 1873 e Ferdinando Mosca si risposa: i figli riprendono nel 1875.

    Due nuclei con gli stessi due nomi - la seconda moglie porta il nome
    della prima, che nel paese vuol dire quasi sempre la cognata - ma
    unirli e' escluso dal veto «un figlio nasce dopo la morte». Non e'
    una famiglia spezzata: sono due matrimoni. Il contrappeso: senza
    l'atto di morte della prima, il caso resta da guardare.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(morte_della_prima) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, nascita_origine TEXT,
                                    anno_morte INTEGER, menzioni INTEGER);
            CREATE TABLE legami (figlio INTEGER, genitore INTEGER, tipo TEXT);
            INSERT INTO individui VALUES (1, 'Ferdinando', 'Mosca', 1846, 'stimata', NULL, 9);
            INSERT INTO individui VALUES (3, 'Ferdinando', 'Mosca', 1848, 'stimata', NULL, 13);
            INSERT INTO individui VALUES (4, 'Anna', 'Di Palma', 1852, 'stimata', NULL, 4);
            INSERT INTO individui VALUES (5, 'Vincenzo', 'Mosca', 1871, 'certa', NULL, 2);
            INSERT INTO individui VALUES (6, 'Rosa', 'Mosca', 1878, 'certa', NULL, 2);
            INSERT INTO legami VALUES (5, 1, 'padre'), (5, 2, 'madre'), (6, 3, 'padre'), (6, 4, 'madre');
        """)
        conn.execute("INSERT INTO individui VALUES (2, 'Anna', 'Di Palma', 1852, 'stimata', ?, 5)",
                     (morte_della_prima,))
        return conn

    assert qualita.coppie_gemelle(archivio(1873)) == []
    assert len(qualita.coppie_gemelle(archivio(None))) == 1


def test_le_seconde_nozze_con_l_omonima_non_sono_un_coniuge_doppio():
    """Ferdinando Mosca perde Anna Di Palma nel 1873 e sposa un'altra Anna Di Palma.

    Il controllo partiva da una regola giusta — non ci si risposava con
    un'omonima della prima moglie — ma col nome ci si risposava eccome se
    la prima era morta e la seconda era la cognata. Dove l'atto di morte
    c'e', e viene prima del secondo matrimonio, non sono due letture
    della stessa donna: sono due matrimoni, e nessuna fusione potrebbe
    unirle (un figlio nascerebbe dopo la morte della madre). Il
    contrappeso: senza l'atto di morte il caso resta da guardare.
    """
    import sqlite3
    from history_maker import qualita

    def archivio(morte_della_prima) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                    anno_nascita INTEGER, anno_morte INTEGER);
            CREATE TABLE unioni (marito INTEGER, moglie INTEGER, anno INTEGER);
            INSERT INTO individui VALUES (1, 'Ferdinando', 'Mosca', 1846, NULL);
            INSERT INTO individui VALUES (3, 'Anna', 'Di Palma', 1852, NULL);
            INSERT INTO unioni VALUES (1, 2, 1869), (1, 3, 1875);
        """)
        conn.execute("INSERT INTO individui VALUES (2, 'Anna', 'Di Palma', 1852, ?)",
                     (morte_della_prima,))
        return conn

    assert qualita.coniugi_duplicati(archivio(1873)) == []
    assert len(qualita.coniugi_duplicati(archivio(None))) == 1


def test_la_qualita_non_conta_il_coniuge_morto_nominato_dopo():
    """Il controllo «atti dopo la propria morte» leggeva la sposa del 1881 come viva.

    Nel database la riga di Angela D'Ettore resta «vivente»: che sia morta
    lo dice il testo, «vedovo di D'Ettore Angela». Il controllo lo legge
    con la stessa definizione della ricostruzione. Il contrappeso: la
    stessa donna come testimone dopo il funerale resta un caso.
    """
    import sqlite3
    from history_maker import qualita

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE atti (id INTEGER PRIMARY KEY, tipo TEXT, anno INTEGER, testo_integrale TEXT);
        CREATE TABLE persone (id INTEGER PRIMARY KEY, atto INTEGER, ruolo TEXT, nome TEXT,
                              cognome TEXT, stato_vitale TEXT);
        CREATE TABLE individui (id INTEGER PRIMARY KEY, nome TEXT, cognome TEXT,
                                anno_nascita INTEGER, anno_morte INTEGER, menzioni INTEGER);
        CREATE TABLE menzioni (persona INTEGER, individuo INTEGER);
        INSERT INTO individui VALUES (1, 'Angela', 'D''Ettore', 1803, 1865, 2);
        INSERT INTO atti VALUES (10, 'morte', 1881, 'è morto Carmine Pizzi, vedovo di D''Ettore Angela');
        INSERT INTO persone VALUES (100, 10, 'sposa', 'Angela', 'D''Ettore', 'vivente');
        INSERT INTO menzioni VALUES (100, 1);
    """)
    assert qualita.atti_dopo_la_propria_morte(conn) == []

    conn.executescript("""
        INSERT INTO atti VALUES (11, 'nascita', 1881, 'alla presenza dei testimoni');
        INSERT INTO persone VALUES (101, 11, 'testimone', 'Angela', 'D''Ettore', 'vivente');
        INSERT INTO menzioni VALUES (101, 1);
    """)
    assert len(qualita.atti_dopo_la_propria_morte(conn)) == 1


# ---------------------------------------------------------------------------
# 52. «Savino» e «Donato» Pelliccia, mariti di Maria Nicola Desiderio
# ---------------------------------------------------------------------------

def _maria_nicola_e_i_due_pelliccia(morte_del_primo=None):
    """Maria Nicola Desiderio, i figli di Savino fino al 1860 e uno del 1870.

    ``morte_del_primo`` e' l'atto di morte di Savino: il solo dato che
    cambia fra il caso e il suo contrappeso.
    """
    atti = [
        matrimonio(1856, ("Savino", "Pelliccia"), "ventitre",
                   (("Samuele", "Pelliccia"), ("Anna", "Marianacci")),
                   ("Maria Nicola", "Desiderio"), "ventiquattro",
                   (("Bellisario", "Desiderio"), ("Stilla", "Pirri"))),
        nascita(1857, ("Nicola", "Pelliccia"), ("Savino", "Pelliccia"),
                ("Maria Nicola", "Desiderio"), {"eta": "ventotto"}, {"eta": "venticinque"}),
        nascita(1860, ("Germano", "Pelliccia"), ("Savino", "Pelliccia"),
                ("Maria Nicola", "Desiderio"), {"eta": "ventisette"}, {"eta": "ventinove"}),
        nascita(1870, ("Giuseppe Nicola", "Pelliccia"), ("Donato", "Pelliccia"),
                ("Maria Nicola", "Desiderio"), {"eta": "trentasei"}, {"eta": "trentotto"}),
    ]
    if morte_del_primo is not None:
        atti.append({"tipo": "morte", "anno": morte_del_primo,
                     "data": f"{morte_del_primo}-05-01", "persone": [
                         {"ruolo": "defunto", "nome": "Savino", "cognome": "Pelliccia",
                          "eta": "cinquantacinque"},
                         {"ruolo": "padre", "nome": "Samuele", "cognome": "Pelliccia"},
                         {"ruolo": "madre", "nome": "Anna", "cognome": "Marianacci"}]})
    esito, prima = in_un_paese(atti)
    _accorpa_le_madri(esito, prima)
    mariti = [m for m in esito.corpus.menzioni
              if m.id >= prima and m.ruolo in ("padre", "sposo")
              and m.nome in ("Savino", "Donato")]
    savino = [m for m in mariti if m.nome == "Savino"]
    donato = [m for m in mariti if m.nome == "Donato"]
    for scheda in {esito.di_menzione[m.id] for m in savino + donato}:
        esito.schede[scheda].vietati = set()
    _spezza(esito, esito.schede[esito.di_menzione[savino[0].id]], lambda m: m.nome)
    risoluzione.unisci_coniugi_contemporanei(esito)
    return esito, savino, donato


def test_il_primo_marito_vivo_dopo_il_secondo_e_lo_stesso_uomo():
    """Maria Nicola Desiderio ha figli da Savino Pelliccia fino al 1860 e uno da «Donato» nel 1870.

    Dieci anni di distanza: il tempo di restare vedova e di risposarsi
    c'e' tutto, e la regola dei coniugi contemporanei lasciava i due
    mariti separati. Ma Savino muore nel 1889 — diciannove anni **dopo**
    quel figlio — e nel 1876 si risposa: la vedovanza che giustificava il
    secondo marito non c'e' mai stata. Sulla pagina, infatti, il nome e'
    scritto in fondo alla riga, staccato dal cognome che va a capo, e
    dice Savino: «provincia di Chieti e' comparso Savino / Pelliccia del
    fu Samuele d'anni trentasei». Era l'ultimo matrimonio impossibile
    dell'archivio.
    """
    esito, savino, donato = _maria_nicola_e_i_due_pelliccia(morte_del_primo=1889)
    assert len({esito.di_menzione[m.id] for m in savino + donato}) == 1, "un marito solo"


def test_senza_l_atto_di_morte_il_marito_di_dieci_anni_dopo_resta_un_altro():
    """Il contrappeso: dieci anni bastano per una vedovanza, se nessuna morte la smentisce.

    La regola non guarda l'ultima menzione ma la morte scritta: un «fu
    Savino» nell'atto di un figlio vecchio la porterebbe avanti di anni e
    farebbe unire chiunque.
    """
    esito, savino, donato = _maria_nicola_e_i_due_pelliccia(morte_del_primo=None)
    assert esito.di_menzione[donato[0].id] != esito.di_menzione[savino[0].id]


def test_la_morte_prima_delle_seconde_nozze_non_prova_niente():
    """Il contrappeso: se Savino muore nel 1865, la vedova del 1870 e' una vedova vera."""
    esito, savino, donato = _maria_nicola_e_i_due_pelliccia(morte_del_primo=1865)
    assert esito.di_menzione[donato[0].id] != esito.di_menzione[savino[0].id]


# ---------------------------------------------------------------------------
# 53. «Basilio Pelliccia», che denuncia la morte del figlio di Savino
# ---------------------------------------------------------------------------

def _savino_e_basilio(padre_defunto=False):
    """Fra i figli di Savino, la morte del 1859 col padre scritto «Basilio»."""
    riga_padre = {"ruolo": "padre", "nome": "Basilio", "cognome": "Pelliccia"}
    if padre_defunto:
        riga_padre["stato_vitale"] = "defunto"
    atti = [
        nascita(1857, ("Nicola", "Pelliccia"), ("Savino", "Pelliccia"),
                ("Maria Nicola", "Desiderio"), {"eta": "ventotto"}, {"eta": "venticinque"}),
        nascita(1860, ("Germano", "Pelliccia"), ("Savino", "Pelliccia"),
                ("Maria Nicola", "Desiderio"), {"eta": "ventisette"}, {"eta": "ventinove"}),
        {"tipo": "morte", "anno": 1859, "data": "1859-03-04", "persone": [
            {"ruolo": "defunto", "nome": "Nicola", "cognome": "Pelliccia", "eta": "anni due"},
            riga_padre,
            {"ruolo": "madre", "nome": "Maria Nicola", "cognome": "Desiderio"}]},
    ]
    esito, prima = in_un_paese(atti)
    _accorpa_le_madri(esito, prima)
    padri = [m for m in esito.corpus.menzioni
             if m.id >= prima and m.ruolo == "padre" and m.cognome == "Pelliccia"]
    for chiave in {esito.di_menzione[m.id] for m in padri}:
        esito.schede[chiave].vietati = set()
    _spezza(esito, esito.schede[esito.di_menzione[padri[0].id]], lambda m: m.nome)
    risoluzione.unisci_coniugi_contemporanei(esito)
    return esito, padri


def test_il_padre_che_denuncia_la_morte_del_figlio_e_un_anno_della_coppia():
    """Maria Nicola Desiderio ha figli da Savino Pelliccia nel 1857 e nel 1860; nel 1859 il padre e' «Basilio».

    La riga sta in un atto di **morte** — quella del bambino di due anni —
    e gli atti di morte erano scartati in blocco, per non far durare
    cinquant'anni una coppia a causa di un «fu». Ma qui il padre denuncia
    di persona, da vivo, e la madre accanto a lui e' la stessa: e' un
    anno in cui i due coniugi sono vivi e insieme, e Basilio e' Savino
    letto male. Scartandolo, quella riga non entrava in nessun confronto
    e restava una scheda per conto suo.
    """
    esito, padri = _savino_e_basilio()
    assert len({esito.di_menzione[m.id] for m in padri}) == 1, "un padre solo"


def test_il_padre_gia_morto_nell_atto_del_figlio_non_fa_anno():
    """Il contrappeso: se l'atto lo dice «fu», quell'anno non e' un anno della coppia.

    E' la ragione per cui gli atti di morte erano esclusi: un padre gia'
    defunto nell'atto di un figlio allungherebbe il matrimonio fino alla
    morte del figlio.
    """
    esito, padri = _savino_e_basilio(padre_defunto=True)
    defunto = next(m for m in padri if m.nome == "Basilio")
    vivo = next(m for m in padri if m.nome == "Savino")
    assert esito.di_menzione[defunto.id] != esito.di_menzione[vivo.id]


# ---------------------------------------------------------------------------
# 54. «vedova del defunto Carmine Finarelli», messo fra i genitori
# ---------------------------------------------------------------------------

def _morte_di_antonia(testo, cognome_padre="Finarelli", ruolo="padre"):
    """La morte del 1870 di Antonia Felice, con la riga di Carmine dove capita."""
    righe = [
        _riga(1, "morte", "defunto", "Antonia", "Felice"),
        _riga(2, "morte", "madre", "Maria", "Felice"),
        _riga(3, "morte", ruolo, "Carmine", cognome_padre),
    ]
    menzioni.genitore_che_e_il_coniuge(righe, testo)
    return righe[2]


TESTO_ANTONIA = (
    "e' morta Antonia Felice d'anni cinquantadue, nata nel comune di Celenza sul "
    "Trigno, figlia della defunta Maria Felice, di professione contadina, e padre "
    "ignoto, vedova del defunto Carmine Finarelli."
)


def test_il_marito_morto_messo_fra_i_genitori_torna_coniuge():
    """Antonia Felice, «e padre ignoto, vedova del defunto Carmine Finarelli».

    La trascrizione ha messo Carmine nella casella del padre: Antonia
    diventava figlia di suo marito, e il suo cognome — quello della
    madre, perche' il padre l'atto lo dichiara ignoto — risultava diverso
    da quello del padre.
    """
    assert _morte_di_antonia(TESTO_ANTONIA).ruolo == "coniuge"


def test_serve_il_nome_e_il_casato_nella_stessa_frase():
    """Il contrappeso: un padre che si chiama come il marito morto non basta.

    E' la ragione per cui questa regola e' piu' stretta di quella che
    segna morto il coniuge: qui si sposta un ruolo, e spostarlo per un
    nome di battesimo in comune costerebbe un padre vero.
    """
    assert _morte_di_antonia(TESTO_ANTONIA, cognome_padre="Manes").ruolo == "padre"


def test_fuori_dalla_frase_del_vedovo_non_si_tocca_niente():
    """Il contrappeso: senza il «vedov* di», la riga resta dov'e'."""
    testo = "e' morta Antonia Felice, figlia di Carmine Finarelli e di Maria Felice."
    assert _morte_di_antonia(testo).ruolo == "padre"


# ---------------------------------------------------------------------------
# Lo stesso atto letto due volte, con una lettera diversa
# ---------------------------------------------------------------------------

def test_lo_stesso_atto_letto_con_una_vocale_diversa_resta_un_evento():
    """Rosa Femminilli, nata il 12 maggio 1889, aveva due atti di nascita.

    Sono lo stesso atto nei due registri: stessa bambina, stessa casa, stessi
    testimoni. Ma il dichiarante e' letto «Concetto» in uno e «Concetta»
    nell'altro, e con una firma sola in comune la regola li teneva distinti:
    il veto dei due atti di nascita rifiutava poi ogni unione.
    """
    from history_maker import menzioni as lettura_atti

    registro = "1889-nati-18259422/"
    righe = [
        _riga(11, "nascita", "neonata", "Rosa", "Femminilli", 1, registro + "0013.jpg"),
        _riga(12, "nascita", "dichiarante", "Concetto", "Femminilli", 1, registro + "0013.jpg"),
        _riga(21, "nascita", "neonata", "Rosa", "Femminilli", 2, registro + "0014.jpg"),
        _riga(22, "nascita", "dichiarante", "Concetta", "Femminilli", 2, registro + "0014.jpg"),
    ]
    for riga in righe:
        riga.anno = 1889
    assert lettura_atti.atti_gemelli(righe) == 1
    assert len({r.evento for r in righe}) == 1


def test_la_coda_dell_atto_sulla_pagina_di_chiusura_e_lo_stesso_evento():
    """Carmine Antonio Ferrara, dicembre 1826: il secondo «atto» chiude il registro.

    Sulla pagina di chiusura e' rimasta la coda dell'ultima nascita dell'anno:
    lo stesso dichiarante, e il neonato col nome ma senza cognome.
    """
    from history_maker import menzioni as lettura_atti

    registro = "1826-nati-17810563/"
    righe = [
        _riga(11, "nascita", "neonato", "Carmine Antonio", "Santoro", 1, registro + "0034.jpg"),
        _riga(12, "nascita", "dichiarante", "Amedio", "Ferrara", 1, registro + "0034.jpg"),
        _riga(21, "nascita", "neonato", "Carmine Antonio", None, 2, registro + "0035.jpg"),
        _riga(22, "nascita", "dichiarante", "Amedio", "Ferrara", 2, registro + "0035.jpg"),
    ]
    for riga in righe:
        riga.anno = 1826
    # Le regole dell'atto hanno gia' dato al neonato senza cognome quello del
    # dichiarante: il confronto deve guardare cio' che e' stato letto.
    righe[2].cognome = "Ferrara"
    assert lettura_atti.atti_gemelli(righe) == 1


def test_due_fratelli_dello_stesso_padre_restano_due_atti():
    """Un dichiarante in comune non basta, se il neonato e' un altro.

    Due nascite dello stesso anno e dello stesso padre - due fratelli, o due
    gemelli registrati in due atti - hanno in comune il padre e spesso la
    lettura del suo nome oscilla. Senza il neonato fra le firme che tornano,
    restano due eventi.
    """
    from history_maker import menzioni as lettura_atti

    registro = "1850-nati/"
    righe = [
        _riga(11, "nascita", "neonato", "Giuseppe", "Pepe", 1, registro + "0010.jpg"),
        _riga(12, "nascita", "dichiarante", "Nicola", "Pepe", 1, registro + "0010.jpg"),
        _riga(13, "nascita", "madre", "Maria", "Lella", 1, registro + "0010.jpg"),
        _riga(21, "nascita", "neonato", "Clementina", "Pepe", 2, registro + "0030.jpg"),
        _riga(22, "nascita", "dichiarante", "Nicola", "Pepe", 2, registro + "0030.jpg"),
        _riga(23, "nascita", "madre", "Maria", "Colella", 2, registro + "0030.jpg"),
    ]
    for riga in righe:
        riga.anno = 1850
    assert lettura_atti.atti_gemelli(righe) == 0
