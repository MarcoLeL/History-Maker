"""Il confronto fra due letture delle stesse pagine.

Serve a decidere se un motore nuovo legge bene come quello vecchio, e
quindi deve sbagliare **per difetto**: contare come divergenza qualcosa
che divergenza non e' — una ``[?]`` in piu', due persone elencate in
ordine diverso — vuol dire scartare un motore buono. La maggior parte di
questi test difende quel confine.
"""

import json
from pathlib import Path

import pytest

from history_maker import confronto


def _scrivi(cartella: Path, nome: str, pagina: dict) -> None:
    percorso = cartella / "1809-nati-1" / f"{nome}.json"
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(json.dumps(pagina, ensure_ascii=False), encoding="utf-8")


def _pagina(atti=None, tipo_pagina="atti") -> dict:
    return {"tipo_pagina": tipo_pagina, "atti": atti or [], "voci_indice": []}


def _atto(numero="1", tipo="nascita", data="1809-01-02", persone=None) -> dict:
    return {
        "numero_atto": numero,
        "tipo": tipo,
        "data_atto": data,
        "persone": persone or [],
    }


def _persona(ruolo, nome, cognome) -> dict:
    return {"ruolo": ruolo, "nome": nome, "cognome": cognome}


@pytest.fixture
def cartelle(tmp_path):
    return tmp_path / "a", tmp_path / "b"


# --- cosa conta come accordo ------------------------------------------------


def test_letture_identiche_sono_accordo_pieno(cartelle):
    a, b = cartelle
    pagina = _pagina([_atto(persone=[_persona("padre", "Sabatino", "Pepe")])])
    _scrivi(a, "0001", pagina)
    _scrivi(b, "0001", pagina)

    esito = confronto.confronta(a, b)
    assert esito.pagine_comuni == 1
    assert esito.quota("uguali") == 1.0
    assert not esito.divergenze


def test_il_segno_di_incertezza_non_e_una_divergenza(cartelle):
    """``Pizzi`` e ``Pizzi [?]`` sono la stessa lettura, dichiarata diversamente."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[_persona("padre", "Nicola", "Pizzi")])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[_persona("padre", "Nicola", "Pizzi [?]")])]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["diverse"] == 0
    assert esito.quota("uguali") == 1.0


def test_uno_scambio_plausibile_per_la_mano_e_vicinanza_non_divergenza(cartelle):
    """La ``ſ`` lunga letta ``f`` non e' un errore qualunque: si segnala a parte."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[_persona("madre", "Rosa", "Cafone")])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[_persona("madre", "Rosa", "Casone")])]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["vicine"] == 1
    assert esito.accordi["diverse"] == 0


def test_due_nomi_diversi_sono_una_divergenza_vera(cartelle):
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[_persona("neonato", "Erminia", "Pepe")])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[_persona("neonato", "Dominica", "Pepe")])]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["diverse"] == 1
    divergenza = next(d for d in esito.divergenze if d.genere == "diverse")
    assert (divergenza.prima, divergenza.seconda) == ("Erminia", "Dominica")
    assert divergenza.campo == "persona.nome"


def test_un_campo_vuoto_da_una_parte_sola_si_conta_a_parte(cartelle):
    """Un buco dichiarato non e' una lettura sbagliata, ma non e' nemmeno accordo."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[_persona("testimone", "Liborio", None)])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[_persona("testimone", "Liborio", "Marchetti")])]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["mancante-a"] == 1
    assert esito.accordi["diverse"] == 0


def test_campi_vuoti_da_entrambe_le_parti_non_si_contano(cartelle):
    """Altrimenti l'accordo si gonfia con le caselle che nessuno ha compilato."""
    a, b = cartelle
    pagina = _pagina([_atto(persone=[_persona("padre", "Nicola", None)])])
    _scrivi(a, "0001", pagina)
    _scrivi(b, "0001", pagina)

    esito = confronto.confronta(a, b)
    # ruolo e nome, non il cognome vuoto in entrambe.
    assert esito.confronti == 1 + 3 + 2  # tipo_pagina + campi atto + ruolo e nome


# --- appaiare cio' che va appaiato -----------------------------------------


def test_gli_atti_si_appaiano_per_numero_non_per_posizione(cartelle):
    """Se un motore salta un atto, gli altri non devono slittare tutti."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto("7", tipo="nascita"), _atto("8", tipo="nascita")]))
    _scrivi(b, "0001", _pagina([_atto("8", tipo="nascita"), _atto("7", tipo="nascita")]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["diverse"] == 0


def test_un_atto_visto_da_una_parte_sola_non_sparisce(cartelle):
    """E' la cosa piu' importante che questo confronto possa dire."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto("7"), _atto("8")]))
    _scrivi(b, "0001", _pagina([_atto("7")]))

    esito = confronto.confronta(a, b)
    assert (esito.atti_prima, esito.atti_seconda) == (2, 1)
    assert esito.accordi["mancante-b"] >= 3  # i campi dell'atto 8, letti solo dalla prima


def test_le_persone_si_appaiano_per_ruolo(cartelle):
    """Elencare padre e madre in ordine diverso non e' leggere diversamente."""
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[
        _persona("padre", "Sabatino", "Pepe"), _persona("madre", "Rosa", "Colella")])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[
        _persona("madre", "Rosa", "Colella"), _persona("padre", "Sabatino", "Pepe")])]))

    esito = confronto.confronta(a, b)
    assert esito.accordi["diverse"] == 0
    assert esito.quota("uguali") == 1.0


# --- le pagine che non si sovrappongono ------------------------------------


def test_le_pagine_non_condivise_sono_elencate_non_confrontate(cartelle):
    a, b = cartelle
    _scrivi(a, "0001", _pagina())
    _scrivi(a, "0002", _pagina())
    _scrivi(b, "0001", _pagina())

    esito = confronto.confronta(a, b)
    assert esito.pagine_comuni == 1
    assert esito.solo_prima == ["1809-nati-1/0002"]
    assert esito.solo_seconda == []


def test_senza_pagine_in_comune_il_rapporto_spiega_cosa_fare(cartelle):
    a, b = cartelle
    _scrivi(a, "0001", _pagina())
    b.mkdir(parents=True, exist_ok=True)

    testo = confronto.rapporto(confronto.confronta(a, b))
    assert "Nessuna pagina in comune" in testo
    assert "STESSE pagine" in testo


def test_una_cartella_inesistente_non_fa_esplodere_nulla(tmp_path):
    esito = confronto.confronta(tmp_path / "manca", tmp_path / "nemmeno")
    assert esito.pagine_comuni == 0


# --- il rapporto -----------------------------------------------------------


def test_il_rapporto_dice_dove_guardare_non_chi_ha_ragione(cartelle):
    a, b = cartelle
    _scrivi(a, "0001", _pagina([_atto(persone=[_persona("sposo", "Gesualdo", "Pepe")])]))
    _scrivi(b, "0001", _pagina([_atto(persone=[_persona("sposo", "Egidio", "Pepe")])]))

    testo = confronto.rapporto(confronto.confronta(a, b))
    assert "Gesualdo" in testo and "Egidio" in testo
    assert "Non e' una misura di correttezza" in testo
    # Nessuna delle due cartelle viene dichiarata migliore.
    assert "migliore" not in testo.lower()
