"""Le prove del glossario che non stanno sull'immagine.

Due prove, ciascuna col suo contrappeso: la stessa persona scritta altrove
con la forma giusta, e il padre che nello stesso atto porta il casato del
figlio. Hanno deciso settanta coppie del glossario che le immagini
lasciavano aperte.
"""

from conftest import FINTI  # noqa: F401  (assicura src/ sul path)
from history_maker import glossario


def _riga(nome, cognome, anno, eta=None, ruolo="testimone"):
    return {"nome": nome, "cognome": cognome, "anno": anno, "eta": eta, "ruolo": ruolo}


def test_la_stessa_persona_scritta_altrove_con_la_forma_giusta():
    """«Francesco Detta», trentasei anni nel 1835: e' Francesco Petta nato nel 1800."""
    letta = [_riga("Francesco", "Detta", 1835, "trentasei")]
    giusta = [_riga("Francesco", "Petta", 1840, "quaranta")]
    assert len(glossario.stessa_persona_altrove(letta, giusta)) == 1


def test_lo_stesso_nome_con_dieci_anni_di_differenza_e_un_altro_uomo():
    """Il contrappeso: di Francesco ce ne sono tanti, e l'anno di nascita li distingue."""
    letta = [_riga("Francesco", "Detta", 1835, "trentasei")]
    giusta = [_riga("Francesco", "Petta", 1840, "trenta")]
    assert glossario.stessa_persona_altrove(letta, giusta) == []


def test_senza_eta_lo_stesso_nome_non_prova_niente():
    """Il contrappeso: senza l'eta' da tutte e due le parti la prova non c'e'."""
    letta = [_riga("Francesco", "Detta", 1835)]
    giusta = [_riga("Francesco", "Petta", 1840, "quaranta")]
    assert glossario.stessa_persona_altrove(letta, giusta) == []


def test_il_padre_e_il_figlio_nello_stesso_atto_portano_lo_stesso_casato():
    """Nascita del 1878: il padre letto «Cannunzio», il neonato «Pannunzio»."""
    atto = [
        _riga("Domenicantonio", "Cannunzio", 1878, ruolo="padre"),
        _riga("Giovanni", "Pannunzio", 1878, ruolo="neonato"),
        _riga("Rosa", "Pepe", 1878, ruolo="madre"),
    ]
    assert glossario.famiglia_nello_stesso_atto(atto, "Cannunzio", "Pannunzio") is not None


def test_la_madre_porta_il_cognome_da_nubile():
    """Il contrappeso: il cognome della madre non dice niente di quello del figlio."""
    atto = [
        _riga("Rosa", "Cannunzio", 1878, ruolo="madre"),
        _riga("Giovanni", "Pannunzio", 1878, ruolo="neonato"),
    ]
    assert glossario.famiglia_nello_stesso_atto(atto, "Cannunzio", "Pannunzio") is None

def test_il_glossario_del_paese_non_riscrive_il_casato_di_marco():
    """Il glossario sostituisce DENTRO il valore: una voce corta si mangia un casato.

    «Marco» fra le varianti di «Mosca» riscriveva anche «di Marco», che a
    Torrebruna e' una famiglia vera (Domenico di Marco e i suoi, ventiquattro
    righe): la moglie di Vincenzo Marianacci finiva nell'albero come «Dorotea
    di Mosca». Una variante nuova va guardata anche cosi': come pezzo di
    parole piu' lunghe.
    """
    from pathlib import Path

    g = glossario.Glossario.carica(Path("config/glossario-torrebruna.yaml"))
    for forma in ("di Marco", "Di Marco", "d'Amico"):
        corretto, _ = g.correggi_campo(forma, "cognome")
        assert corretto == forma, f"il glossario riscrive {forma} in {corretto}"

def _glossario(voci):
    """Un glossario da {forma vera: [storpiature]}, come il file del paese."""
    return glossario.Glossario(cognomi=glossario.Glossario._rovescia(voci))


def test_una_voce_non_si_mangia_il_pezzo_di_un_casato_composto():
    """«Marco» (storpiatura di «Mosca») non deve toccare «di Marco»."""
    g = _glossario({"Mosca": ["Marco"]})
    assert g.correggi_campo("di Marco", "cognome")[0] == "di Marco"
    assert g.correggi_campo("Di Motta", "cognome")[0] == "Di Motta"


def test_la_stessa_voce_corregge_ancora_il_casato_scritto_da_solo():
    """Il contrappeso: la storpiatura da sola resta una storpiatura."""
    g = _glossario({"Mosca": ["Marco"]})
    assert g.correggi_campo("Marco", "cognome")[0] == "Mosca"


def test_una_particella_non_riscrive_un_casato_che_comincia_per_particella():
    """«Della» (storpiatura di «Lella») non deve fare di «Della Penna» un «Lella Penna»."""
    g = _glossario({"Lella": ["Della"]})
    assert g.correggi_campo("Della Penna", "cognome")[0] == "Della Penna"
    assert g.correggi_campo("Della", "cognome")[0] == "Lella"


def test_il_titolo_davanti_al_casato_non_protegge_la_storpiatura():
    """Il contrappeso: «Dottor Savicoli» e' Iavicoli col titolo davanti, non un casato doppio."""
    g = _glossario({"Iavicoli": ["Savicoli"]})
    assert g.correggi_campo("Dottor Savicoli", "cognome")[0] == "Dottor Iavicoli"


def test_sull_indirizzo_la_sostituzione_resta_dentro_la_frase():
    """Il contrappeso: per i luoghi la sostituzione dentro il testo serve."""
    g = glossario.Glossario(toponimi=glossario.Glossario._rovescia(
        {"Trascinella": ["Trafficinella"]}))
    detto = "strada Trafficinella numero due"
    assert g.correggi_campo(detto, "luogo")[0] == "strada Trascinella numero due"


def test_il_casato_scica_non_diventa_sica():
    """La voce andava nel verso sbagliato: il casato e' Scica, «Sica» e' la lettura.

    Matrimonio n. 4 del 1816: «Michele Scica ... pastore, domiciliato nel
    Comune di Palata in Molise, figlio maggiore delli furono Diego Scica»,
    con la c netta. Il glossario diceva scica -> Sica, e tutta la famiglia
    finiva nell'albero col casato sbagliato.
    """
    from pathlib import Path

    g = glossario.Glossario.carica(Path("config/glossario-torrebruna.yaml"))
    assert g.correggi_campo("Scica", "cognome")[0] == "Scica"
    assert g.correggi_campo("Sica", "cognome")[0] == "Scica"


def test_la_voce_sica_non_tocca_i_casati_che_la_contengono():
    """Il contrappeso: «Sica» si corregge solo come parola intera."""
    from pathlib import Path

    g = glossario.Glossario.carica(Path("config/glossario-torrebruna.yaml"))
    for forma in ("Musica", "Sicari", "Scica"):
        assert g.correggi_campo(forma, "cognome")[0] == forma
