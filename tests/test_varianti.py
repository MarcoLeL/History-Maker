"""Il raggruppamento delle varianti sull'intero corpus.

Tutti i casi vengono dalle pagine vere del 1809 di Torrebruna. Il criterio
non e' la fascia di frequenza ma la somiglianza reciproca, e la ragione e'
nei dati: le quattro forme di 'Cicchillitto' contano 6, 5, 4 e 3
occorrenze, quindi nessuna e' "rara" (<=2) ne' "frequente" (>=10). La
regola raro-contro-frequente, sul gruppo piu' numeroso del corpus, non
scatterebbe mai.
"""

from collections import Counter

import pytest

from history_maker.normalizza import raggruppa_varianti


def correzioni(frequenze: dict[str, int]) -> dict[str, str]:
    return raggruppa_varianti(Counter(frequenze))[0]


def proposte(frequenze: dict[str, int]) -> dict[str, str]:
    return {p.letto: p.proposto for p in raggruppa_varianti(Counter(frequenze))[1]}


# --- famiglie diverse che devono restare diverse ---------------------------

def test_lella_e_colella_restano_separati():
    """Confermato da chi conosce il paese: sono due famiglie diverse.

    Stanno a somiglianza 0,71, sotto la soglia di gruppo. E' il caso che
    fissa il limite inferiore: abbassare la soglia le unirebbe.
    """
    frequenze = {"Lella": 8, "Colella": 6}
    assert correzioni(frequenze) == {}
    assert proposte(frequenze) == {}


def test_cognomi_estranei_non_si_toccano():
    frequenze = {"Nicodemo": 14, "Desiderio": 10, "Lattanzio": 9}
    assert correzioni(frequenze) == {}
    assert proposte(frequenze) == {}


# --- quello che si applica da solo -----------------------------------------

def test_lettura_sbagliata_accanto_a_forma_dominante():
    """'Tr' letto per 'Fr' e' confusione di mano, e 15 contro 2 decide."""
    assert correzioni({"Franchella": 15, "Trinchella": 2}) == {"Trinchella": "Franchella"}


def test_sola_punteggiatura_non_e_una_decisione():
    """'Dettorre' e \"d'Ettorre\" sono la stessa stringa senza apostrofo.

    Qui la dominanza non deve entrarci: non c'e' nessuna lettura da
    scegliere, solo una grafia da uniformare.
    """
    assert correzioni({"d'Ettorre": 5, "Dettorre": 2}) == {"Dettorre": "d'Ettorre"}


def test_doppie_instabili_si_uniformano():
    assert correzioni({"Pelliccia": 22, "Pellicia": 2}) == {"Pellicia": "Pelliccia"}


# --- quello che va sottoposto invece che deciso ----------------------------

def test_due_forme_alla_pari_non_si_correggono_a_vicenda():
    """Nessuna evidenza sulla direzione: e' una proposta, non una
    correzione.

    Senza questa guardia 'Pinti' (1) e 'Pinnti' (1) venivano unite
    d'ufficio, e a scegliere la forma buona era l'ordine alfabetico.
    """
    frequenze = {"Pinti": 1, "Pinnti": 1}
    assert correzioni(frequenze) == {}
    assert "Pinti" in proposte(frequenze) or "Pinnti" in proposte(frequenze)


def test_alternanza_o_i_resta_una_decisione_umana():
    """Le due domande vanno separate.

    La doppia instabile ('Cicchilitto'/'Cicchillitto') e' una convenzione
    grafica nota e si unifica da sola. L'alternanza -o/-i invece puo'
    essere una lettura sbagliata o due rami della stessa famiglia, e non
    la decide una statistica.
    """
    frequenze = {"Cicchilitti": 9, "Cicchillitto": 6, "Cicchilitto": 4}
    assert correzioni(frequenze) == {"Cicchilitto": "Cicchillitto"}
    assert proposte(frequenze) == {"Cicchillitto": "Cicchilitti"}


def test_le_catene_di_correzione_si_risolvono():
    """'Pellicia' verso 'Pellicci' verso 'Pelliccia': la prima deve
    arrivare in fondo, non fermarsi a meta'."""
    corr = correzioni({"Pelliccia": 60, "Pellicci": 6, "Pellicia": 3})
    assert set(corr.values()) == {"Pelliccia"}


def test_le_proposte_sono_ordinate_per_ricorrenza():
    """Una forma vista una volta sola non merita il tempo di nessuno;
    una vista sei volte si'."""
    _, prop = raggruppa_varianti(
        Counter({"Pizzi": 11, "Pozzi": 1, "Cicchilitti": 9, "Cicchillitto": 6})
    )
    assert [p.letto for p in prop] == ["Cicchillitto", "Pozzi"]


def test_la_proposta_spiega_perche(capsys):
    _, prop = raggruppa_varianti(Counter({"Mancini": 2, "Mancino": 2}))
    assert len(prop) == 1
    motivo = prop[0].motivo
    assert "2 volte" in motivo and "somiglianza" in motivo


# --- forma del risultato ---------------------------------------------------

@pytest.mark.parametrize("frequenze", [{}, {"Solo": 3}])
def test_niente_da_raggruppare(frequenze):
    assert raggruppa_varianti(Counter(frequenze)) == ({}, [])


def test_una_variante_non_e_mai_anche_una_proposta():
    """Le due liste sono disgiunte: un caso o si decide o si sottopone."""
    frequenze = {
        "Pelliccia": 22, "Pellicia": 2, "Cicchilitti": 9, "Cicchillitto": 6,
        "Lella": 8, "Lelia": 2,
    }
    corr, prop = raggruppa_varianti(Counter(frequenze))
    assert not (set(corr) & {p.letto for p in prop})


# --- qualita' delle proposte -----------------------------------------------

def test_la_proposta_va_verso_il_parente_piu_stretto():
    """I gruppi si formano per contatto, le proposte no.

    'Pilli'-'Silli'-'Lelle'-'Lella' finiscono nello stesso gruppo per
    catena, ma proporre 'Pilli -> Lella' (somiglianza 0,54) sarebbe
    rumore che fa perdere fiducia in tutto l'elenco.
    """
    _, prop = raggruppa_varianti(
        Counter({"Lella": 34, "Lelle": 1, "Silli": 1, "Pilli": 2})
    )
    for p in prop:
        assert p.somiglianza >= 0.80, f"{p.letto} -> {p.proposto} e' troppo lontano"


def test_una_coppia_alla_pari_si_propone_una_volta_sola():
    """Sono la stessa famiglia scritta in due modi, e vanno sottoposte.

    Ma una volta sola: senza una direzione deterministica comparirebbero
    sia 'Chiello -> Chielli' sia 'Chielli -> Chiello'.
    """
    _, prop = raggruppa_varianti(Counter({"Chiello": 1, "Chielli": 1}))
    assert len(prop) == 1


def test_le_proposte_si_raggruppano_per_forma_proposta(tmp_path):
    """In YAML una chiave ripetuta sovrascrive in silenzio la precedente."""
    from history_maker.config import Config
    from history_maker.dataset import _scrivi_proposte

    _, prop = raggruppa_varianti(
        Counter({"Pizzi": 52, "Pozzi": 9, "Lizzi": 6})
    )
    config = Config(
        comune="Torrebruna", termine_ricerca="Torrebruna", includi_contesto=[],
        escludi_contesto=[], anno_min=1809, anno_max=1900, tipologie=[],
        catalogo=tmp_path / "c.json", immagini=tmp_path / "i", ridotte=tmp_path / "r",
        trascrizioni=tmp_path / "t", dataset=tmp_path,
    )
    testo = _scrivi_proposte(config, prop).read_text(encoding="utf-8")

    import yaml
    letto = yaml.safe_load(testo)["cognomi"]
    # Le due varianti devono sopravvivere entrambe alla rilettura YAML.
    assert sorted(letto["Pizzi"]) == ["Lizzi", "Pozzi"]
    assert testo.count("  Pizzi:") == 1


def test_una_proposta_non_punta_a_una_forma_che_sparira():
    """'Bellicia -> Pellicia' mentre 'Pellicia' diventa 'Pelliccia'
    farebbe decidere su una forma che nel database non c'e' piu'."""
    _, prop = raggruppa_varianti(
        Counter({"Pelliccia": 60, "Pellicia": 3, "Bellicia": 1})
    )
    corr = correzioni({"Pelliccia": 60, "Pellicia": 3, "Bellicia": 1})
    for p in prop:
        assert p.proposto not in corr, f"{p.proposto} viene a sua volta corretto"


def test_i_toponimi_si_raggruppano_con_una_soglia_piu_bassa():
    """Le contrade sono un insieme chiuso e qui si propone soltanto: si
    puo' essere piu' generosi che sui cognomi.

    Il caso vero: 'Rua di Nuorro' letta anche 'Lama di Nuorro' (0,70) e
    'Rua di Nuovo' (0,78). Con la soglia dei cognomi resterebbe divisa in
    tre contrade diverse.
    """
    from history_maker.normalizza import SOGLIA_GRUPPO, SOGLIA_TOPONIMI, _raggruppa

    nuclei = ["rua nuorro", "lama nuorro", "rua nuovo"]
    assert len(_raggruppa(nuclei, SOGLIA_GRUPPO)) == 3
    assert len(_raggruppa(nuclei, SOGLIA_TOPONIMI)) == 1


def test_la_soglia_dei_toponimi_non_fonde_luoghi_diversi():
    """A 0,65 'Porta del Colle' si mangiava 'Portamurella'."""
    from history_maker.normalizza import SOGLIA_TOPONIMI, _raggruppa

    assert len(_raggruppa(["porta colle", "portamurella"], SOGLIA_TOPONIMI)) == 2
