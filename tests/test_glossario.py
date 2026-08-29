"""Il glossario: la conoscenza del paese che la statistica non ricava.

I casi sono quelli veri del 1809 di Torrebruna. Sono importanti perche'
sono precisamente quelli che la fase 5 NON puo' risolvere: 'Lama di
Nuorro' e' stato letto due volte su due in modo concorde, e 'Trascinella'
non compare in nessuna trascrizione.
"""

import pytest
import yaml

from history_maker.glossario import Glossario


@pytest.fixture
def glossario(tmp_path) -> Glossario:
    percorso = tmp_path / "glossario.yaml"
    percorso.write_text(
        yaml.safe_dump(
            {
                "toponimi": {
                    "Rua di Nuorro": ["Lama di Nuorro"],
                    "strada Trascinella": [
                        "strada Trafficinella",
                        "strada Frascinella",
                        "Trafficinella",
                        "Frascinella",
                    ],
                },
                "cognomi": {"Franchella": ["Trinchella"]},
                "confermati": ["Cicchillitto"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return Glossario.carica(percorso)


def test_il_consenso_sbagliato_viene_corretto(glossario):
    """Due letture concordi e sbagliate: nessuna frequenza le segnala."""
    valore, fatte = glossario.correggi_campo(
        "in questo Comune di Torrebruna, strada a piedi della Lama di Nuorro", "luogo"
    )
    assert valore == "in questo Comune di Torrebruna, strada a piedi della Rua di Nuorro"
    assert [(c.letto, c.corretto) for c in fatte] == [("lama di nuorro", "Rua di Nuorro")]


def test_una_forma_mai_letta_e_raggiungibile_solo_cosi(glossario):
    """'Trascinella' non compare in nessuna trascrizione: solo il
    glossario puo' arrivarci."""
    assert glossario.correggi_campo("questa Comune, strada Frascinella", "luogo")[0] == (
        "questa Comune, strada Trascinella"
    )
    assert glossario.correggi_campo("strada Trafficinella", "luogo")[0] == (
        "strada Trascinella"
    )


def test_le_forme_lunghe_hanno_la_precedenza(glossario):
    """Senza questo, 'strada Trafficinella' diventerebbe
    'strada strada Trascinella'."""
    assert glossario.correggi_campo("strada Trafficinella", "luogo")[0].count("strada") == 1


def test_il_confronto_ignora_maiuscole_e_spaziatura(glossario):
    assert glossario.correggi_campo("LAMA  DI   NUORRO", "luogo")[0] == "Rua di Nuorro"


def test_gli_accenti_del_resto_della_frase_restano(glossario):
    """Normalizzare per confrontare non deve storpiare il testo reso."""
    valore, _ = glossario.correggi_campo(
        "citta' di Torrebruna, età ignota, Lama di Nuorro, perché così", "luogo"
    )
    assert "età" in valore and "perché" in valore and "così" in valore
    assert "Rua di Nuorro" in valore


def test_una_forma_gia_corretta_non_si_tocca(glossario):
    valore, fatte = glossario.correggi_campo("Rua di Nuorro", "luogo")
    assert valore == "Rua di Nuorro"
    assert fatte == []


def test_i_cognomi_usano_la_loro_mappa(glossario):
    assert glossario.correggi_campo("Trinchella", "cognome")[0] == "Franchella"
    # Un toponimo non deve correggersi con la mappa dei cognomi.
    assert glossario.correggi_campo("Trinchella", "luogo")[0] == "Trinchella"


def test_le_forme_note_arrivano_al_prompt(glossario):
    toponimi, cognomi = glossario.forme_note()
    assert "Rua di Nuorro" in toponimi and "strada Trascinella" in toponimi
    assert "Franchella" in cognomi
    # Solo le forme corrette, mai le storpiature.
    assert not any("Lama" in t for t in toponimi)


def test_una_forma_confermata_si_riconosce(glossario):
    assert glossario.e_confermato("Cicchillitto")
    assert not glossario.e_confermato("Cicchilitti")


def test_senza_file_il_glossario_e_vuoto_non_un_errore(tmp_path):
    """Un progetto nuovo non ha ancora conoscenza locale da dichiarare."""
    vuoto = Glossario.carica(tmp_path / "non-esiste.yaml")
    assert vuoto.forme_note() == ([], [])
    assert vuoto.correggi_campo("Lama di Nuorro", "luogo") == ("Lama di Nuorro", [])


def test_il_glossario_del_progetto_si_carica():
    """Il file vero in config/ deve restare valido."""
    reale = Glossario.carica("config/glossario-torrebruna.yaml")
    toponimi, _ = reale.forme_note()
    assert "Rua di Nuorro" in toponimi
