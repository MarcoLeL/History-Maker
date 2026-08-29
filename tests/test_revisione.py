"""La fase 5: trovare le letture sbagliate senza consumare quota.

Il caso che guida questi test e' quello segnalato dall'autore del
progetto: un cognome che a Torrebruna non e' frequente in mezzo a
cognomi che ricorrono in tutto il secolo. La domanda giusta non e' "come
lo correggo" ma "come faccio ad accorgermene", e soprattutto "come evito
di cancellare i forestieri veri, che sono un dato storico".
"""

import json
import sqlite3

import pytest

from history_maker import dataset, paleografia, revisione
from history_maker.config import Config


# --- somiglianza fra grafie ottocentesche ---------------------------------

@pytest.mark.parametrize(
    "prima, seconda",
    [
        ("Fabrizio", "Sabrizio"),   # s lunga letta come f: la confusione piu' comune
        ("Sciarra", "Sciarro"),     # vocale finale
        ("Palma", "Palina"),        # m reso con in: stesso numero di gambe
        ("Di Nardo", "De Nardo"),   # prefisso
        ("Ricci", "Ricei"),         # c/e
    ],
)
def test_confusioni_della_mano_costano_poco(prima, seconda):
    assert paleografia.somiglianza(prima, seconda) >= 0.90


@pytest.mark.parametrize(
    "prima, seconda",
    [
        ("Di Nardo", "Castiglione"),
        ("Sciarra", "Marchetti"),
        ("Palma", "Ricci"),
    ],
)
def test_cognomi_estranei_restano_lontani(prima, seconda):
    assert paleografia.somiglianza(prima, seconda) < 0.55


@pytest.mark.parametrize(
    "forme",
    [
        ("Di Nardo", "Dinardo", "De Nardo", "Denardo"),
        ("D'Amico", "Damico", "D'amico"),
    ],
)
def test_varianti_del_prefisso_hanno_la_stessa_forma_canonica(forme):
    canoniche = {paleografia.forma_canonica(f) for f in forme}
    assert len(canoniche) == 1


def test_cognome_corto_non_viene_stritolato():
    """'Dino' non deve essere trattato come prefisso 'Di' + 'no'."""
    assert paleografia.forma_canonica("Dino") == "dino"


# --- impianto ------------------------------------------------------------

@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        comune="Torrebruna", termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"], escludi_contesto=["Guardiabruna"],
        anno_min=1809, anno_max=1900, tipologie=[],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )


def _pagina(config, registro, nome, atti=(), voci_indice=(), anno=1866):
    percorso = config.trascrizioni / registro / nome
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        json.dumps(
            {
                "tipo_pagina": "indice" if voci_indice else "atti",
                "anno_indicato": str(anno), "comune_indicato": "Torrebruna",
                "atti": list(atti), "voci_indice": list(voci_indice),
                "osservazioni": None,
                "_origine": {
                    "immagine": f"{registro}/{nome.replace('.json', '.jpg')}",
                    "registro": registro, "ark_url": "https://esempio/ark",
                    "anno": anno, "tipologia": "Nati",
                    "contesto": "Chieti/Torrebruna",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _atto(numero, cognomi):
    return {
        "numero_atto": str(numero), "tipo": "nascita", "data_atto": f"1866-03-{numero:02d}",
        "data_evento": None, "ora_evento": None, "luogo": "Torrebruna",
        "persone": [
            {"ruolo": "padre", "nome": "Giuseppe", "cognome": c, "eta": "trenta",
             "professione": "contadino", "residenza": None, "stato_vitale": None, "note": None}
            for c in cognomi
        ],
        "testo_integrale": f"Atto numero {numero}.",
        "parti_illeggibili": [], "affidabilita": "alta",
    }


# --- il caso che ha originato questa fase ---------------------------------

def test_cognome_raro_vicino_a_uno_frequente_viene_segnalato(config):
    """Un cognome visto una volta, a un passo da uno visto trenta volte."""
    registro = "1866-nati-1"
    for i in range(1, 16):
        _pagina(config, registro, f"{i:04d}.json", atti=[_atto(i, ["Sciarra", "Palma"])])
    # ...e una sola occorrenza di 'Palina', che e' 'Palma' con la m resa in corsivo.
    _pagina(config, registro, "0099.json", atti=[_atto(99, ["Palina"])])
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    sospetti = {s.letto: s for s in segnalazioni if s.tipo == "frequenza"}
    assert "Palina" in sospetti
    assert sospetti["Palina"].proposto == "Palma"
    assert sospetti["Palina"].occorrenze_lette == 1
    assert sospetti["Palina"].occorrenze_proposte >= 10


def test_forestiero_vero_non_viene_ricondotto_a_nulla(config):
    """Un cognome raro e DIVERSO da tutti resta com'e'.

    E' il caso della sposa venuta da un altro paese: raro, ma vero. Se
    l'analisi lo appiattisse su un cognome locale distruggerebbe proprio
    il dato che racconta i rapporti fra i comuni.
    """
    registro = "1866-nati-1"
    for i in range(1, 16):
        _pagina(config, registro, f"{i:04d}.json", atti=[_atto(i, ["Sciarra", "Palma"])])
    _pagina(config, registro, "0099.json", atti=[_atto(99, ["Castiglione"])])
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    assert "Castiglione" not in {s.letto for s in segnalazioni}


def test_due_cognomi_entrambi_rari_non_si_correggono_a_vicenda(config):
    registro = "1866-nati-1"
    _pagina(config, registro, "0001.json", atti=[_atto(1, ["Palma"])])
    _pagina(config, registro, "0002.json", atti=[_atto(2, ["Palina"])])
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    assert not [s for s in segnalazioni if s.tipo == "frequenza"]


# --- il confronto con gli indici ------------------------------------------

def test_discordanza_fra_indice_e_atti(config):
    """La leva migliore: due letture indipendenti dello stesso nome."""
    registro = "1866-nati-1"
    _pagina(config, registro, "0001.json", atti=[_atto(1, ["Sabrizio"])])
    _pagina(
        config, registro, "0050.json",
        voci_indice=[{"cognome": "Fabrizio", "nome": "Giuseppe", "numero_atto": "1"}],
    )
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    discordanze = [s for s in segnalazioni if s.tipo == "indice"]
    assert len(discordanze) == 1
    assert discordanze[0].letto == "Sabrizio"
    assert discordanze[0].proposto == "Fabrizio"


def test_indice_concorde_non_segnala_nulla(config):
    registro = "1866-nati-1"
    _pagina(config, registro, "0001.json", atti=[_atto(1, ["Sciarra"])])
    _pagina(
        config, registro, "0050.json",
        voci_indice=[{"cognome": "Sciarra", "nome": "Giuseppe", "numero_atto": "1"}],
    )
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    assert not [s for s in segnalazioni if s.tipo == "indice"]


def test_voci_indice_finiscono_nel_database(config):
    registro = "1866-nati-1"
    _pagina(
        config, registro, "0050.json",
        voci_indice=[
            {"cognome": "Di Nardo", "nome": "Maria", "numero_atto": "17"},
            {"cognome": "Sciarra", "nome": "Domenico", "numero_atto": "18"},
        ],
    )
    percorso = dataset.costruisci(config)
    conn = sqlite3.connect(percorso)
    assert conn.execute("SELECT COUNT(*) FROM voci_indice").fetchone()[0] == 2
    conn.close()
    assert (config.dataset / "voci_indice.csv").exists()


# --- varianti grafiche ----------------------------------------------------

def test_varianti_dello_stesso_cognome_raggruppate(config):
    registro = "1866-nati-1"
    _pagina(config, registro, "0001.json", atti=[_atto(1, ["Di Nardo"])])
    _pagina(config, registro, "0002.json", atti=[_atto(2, ["Di Nardo"])])
    _pagina(config, registro, "0003.json", atti=[_atto(3, ["Dinardo"])])
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    varianti = [s for s in segnalazioni if s.tipo == "variante"]
    assert len(varianti) == 1
    # La forma piu' attestata fa da riferimento, non la prima incontrata.
    assert varianti[0].letto == "Dinardo" and varianti[0].proposto == "Di Nardo"


# --- atti dichiarati incerti ----------------------------------------------

def test_atti_incerti_raccolti(config):
    registro = "1866-nati-1"
    atto = _atto(1, ["Sciarra"])
    atto["affidabilita"] = "bassa"
    atto["parti_illeggibili"] = ["il cognome della madre"]
    _pagina(config, registro, "0001.json", atti=[atto])
    dataset.costruisci(config)

    _, incerti = revisione.analizza(config)
    assert len(incerti) == 1
    assert incerti[0]["incertezze"] == "il cognome della madre"


# --- report ---------------------------------------------------------------

def test_report_scritto_e_leggibile(config):
    registro = "1866-nati-1"
    for i in range(1, 16):
        _pagina(config, registro, f"{i:04d}.json", atti=[_atto(i, ["Palma"])])
    _pagina(config, registro, "0099.json", atti=[_atto(99, ["Palina"])])
    dataset.costruisci(config)

    percorso = revisione.scrivi_report(config)
    testo = percorso.read_text(encoding="utf-8")
    assert "Palina" in testo and "Palma" in testo
    # L'avvertenza sui forestieri deve essere in cima, non in fondo.
    assert "non e' per forza un errore" in testo
    assert "Segnalazioni, non correzioni" in testo


def test_report_senza_segnalazioni(config):
    _pagina(config, "1866-nati-1", "0001.json", atti=[_atto(1, ["Sciarra"])])
    dataset.costruisci(config)
    testo = revisione.scrivi_report(config).read_text(encoding="utf-8")
    assert "Nessuna segnalazione" in testo


def test_revisione_senza_database_da_istruzioni(config):
    with pytest.raises(FileNotFoundError, match="dataset"):
        revisione.analizza(config)


def test_una_variante_non_viene_segnalata_due_volte(config):
    """'Dinardo' e' gia' spiegato come variante: non va ripetuto fra i sospetti."""
    registro = "1866-nati-1"
    for i in range(1, 16):
        _pagina(config, registro, f"{i:04d}.json", atti=[_atto(i, ["Di Nardo"])])
    _pagina(config, registro, "0099.json", atti=[_atto(99, ["Dinardo"])])
    dataset.costruisci(config)

    segnalazioni, _ = revisione.analizza(config)
    apparizioni = [s for s in segnalazioni if s.letto == "Dinardo"]
    assert len(apparizioni) == 1
    assert apparizioni[0].tipo == "variante"
