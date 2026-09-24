"""Un'esclusione non deve aprire un buco nella serie.

Il caso che ha imposto questa regola e' vero e viene da Torrebruna.
Escludere ``Matrimoni, pubblicazioni`` e' giusto: la pubblicazione e' la
promessa di matrimonio, nomina gli stessi sposi e gli stessi genitori
dell'atto, e tenerla vorrebbe dire contare due volte le stesse persone.

Ma per il 1870, 1871, 1873, 1874, 1875 e 1888 il registro dei matrimoni
**non esiste** — perduto, o mai versato all'archivio. Li' la
pubblicazione non duplica niente: e' l'unica traccia rimasta di chi si e'
sposato. Escluderla non toglie un doppione, cancella sei anni.
"""

import pytest

from history_maker.catalogo import Catalogo, Registro, pertinente, recuperati, selezione
from history_maker.config import Config


def _config(tmp_path, **campi) -> Config:
    valori = dict(
        comune="Torrebruna",
        termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"],
        escludi_contesto=["Guardiabruna"],
        anno_min=1809,
        anno_max=1900,
        tipologie=[],
        escludi_tipologie=["pubblicazioni"],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )
    valori.update(campi)
    return Config(**valori)


def _registro(anno, tipologia, comune="Torrebruna", pagine=10) -> Registro:
    return Registro(
        ark_url=f"https://antenati.cultura.gov.it/ark:/12657/an_{anno}{tipologia}{comune}",
        contesto=f"Archivio di Stato di Chieti/Stato civile italiano/{comune}",
        titolo=str(anno),
        tipologia=tipologia,
        anno=anno,
        n_immagini=pagine,
    )


def _catalogo(*registri) -> Catalogo:
    return Catalogo(comune="Torrebruna", registri=list(registri))


# --- la regola normale: l'esclusione vale ----------------------------------


def test_dove_l_atto_esiste_la_pubblicazione_resta_fuori(tmp_path):
    """E' il caso comune, ed e' il motivo per cui l'esclusione esiste."""
    config = _config(tmp_path)
    catalogo = _catalogo(
        _registro(1870, "Matrimoni"),
        _registro(1870, "Matrimoni, pubblicazioni"),
    )
    tenuti = selezione(catalogo, config)
    assert [r.tipologia for r in tenuti] == ["Matrimoni"]
    assert recuperati(catalogo, config) == []


# --- l'eccezione: quando l'esclusione cancellerebbe l'anno ------------------


def test_dove_l_atto_manca_la_pubblicazione_rientra(tmp_path):
    config = _config(tmp_path)
    catalogo = _catalogo(
        _registro(1870, "Matrimoni, pubblicazioni"),
        _registro(1870, "Nati"),
        _registro(1870, "Morti"),
    )
    tenuti = selezione(catalogo, config)

    assert "Matrimoni, pubblicazioni" in [r.tipologia for r in tenuti]
    assert [r.anno for r in recuperati(catalogo, config)] == [1870]


def test_il_registro_rientrato_porta_scritto_perche(tmp_path):
    """Un'eccezione silenziosa a una regola scritta dall'utente e' peggio
    di nessuna eccezione."""
    config = _config(tmp_path)
    catalogo = _catalogo(_registro(1870, "Matrimoni, pubblicazioni"))
    rientrato = selezione(catalogo, config)[0]
    assert any("recuperato" in n for n in rientrato.note)


def test_un_anno_coperto_non_tira_dentro_gli_altri(tmp_path):
    """Il recupero e' per anno e per tipo, non una revoca dell'esclusione."""
    config = _config(tmp_path)
    catalogo = _catalogo(
        _registro(1870, "Matrimoni"),
        _registro(1870, "Matrimoni, pubblicazioni"),
        _registro(1871, "Matrimoni, pubblicazioni"),
    )
    rientrati = recuperati(catalogo, config)
    assert [r.anno for r in rientrati] == [1871]


# --- cosa non si recupera MAI ----------------------------------------------


def test_guardiabruna_non_rientra_mai(tmp_path):
    """E' un altro comune: che a Torrebruna manchi quell'anno non c'entra.

    E' la distinzione che regge tutto: una tipologia esclusa e' una scelta
    di merito, che smette di valere quando cancellerebbe un anno; un
    contesto escluso e' un'altra raccolta, e nessun buco la rende
    pertinente.
    """
    config = _config(tmp_path)
    catalogo = _catalogo(
        _registro(1870, "Matrimoni", comune="Guardiabruna"),
        _registro(1870, "Matrimoni, pubblicazioni", comune="Guardiabruna"),
    )
    assert selezione(catalogo, config) == []
    assert recuperati(catalogo, config) == []


def test_un_anno_fuori_intervallo_non_rientra(tmp_path):
    config = _config(tmp_path, anno_max=1860)
    catalogo = _catalogo(_registro(1870, "Matrimoni, pubblicazioni"))
    assert selezione(catalogo, config) == []


def test_il_recupero_si_puo_spegnere(tmp_path):
    """Chi vuole l'esclusione secca, buchi compresi, deve poterla avere."""
    config = _config(tmp_path, recupera_serie_interrotta=False)
    catalogo = _catalogo(_registro(1870, "Matrimoni, pubblicazioni"))
    assert selezione(catalogo, config) == []


# --- il caso vero di Torrebruna --------------------------------------------


def test_il_caso_che_ha_imposto_la_regola(tmp_path):
    """Sei anni di matrimoni che sopravvivono solo come pubblicazioni."""
    config = _config(tmp_path)
    registri = []
    for anno in range(1869, 1877):
        registri += [_registro(anno, "Nati"), _registro(anno, "Morti")]
        if anno in (1869, 1872, 1876):
            registri.append(_registro(anno, "Matrimoni"))
        else:
            registri.append(_registro(anno, "Matrimoni, pubblicazioni"))

    rientrati = sorted(r.anno for r in recuperati(_catalogo(*registri), config))
    assert rientrati == [1870, 1871, 1873, 1874, 1875]

    # E gli anni che il registro vero ce l'hanno restano senza doppione.
    tenuti = selezione(_catalogo(*registri), config)
    matrimoni_1869 = [r for r in tenuti if r.anno == 1869 and "atrimoni" in r.tipologia]
    assert [r.tipologia for r in matrimoni_1869] == ["Matrimoni"]


@pytest.mark.parametrize(
    "tipologia, atteso",
    [
        ("Matrimoni", "matrimoni"),
        ("Matrimoni, pubblicazioni", "matrimoni"),
        ("Matrimoni, processetti", "matrimoni"),
        ("Nati, indici decennali", "nati"),
        ("Morti", "morti"),
    ],
)
def test_il_tipo_base_ignora_le_specificazioni(tipologia, atteso):
    from history_maker.catalogo import tipo_base

    assert tipo_base(_registro(1870, tipologia)) == atteso


# --- i registri che coprono piu' di un anno --------------------------------


def test_un_registro_biennale_copre_entrambi_gli_anni():
    """Ignorarlo inventa lacune che non esistono.

    A Torrebruna dieci registri portano il titolo '1813-1814'.
    Assegnando loro il solo 1813, il 1814 risulta un anno senza atti —
    mentre i suoi atti stanno li' dentro, e il conteggio delle pagine lo
    conferma: 43 pagine di nati contro una mediana di 28 l'anno.
    """
    r = _registro(1813, "Nati")
    r.titolo = "1813-1814"
    assert r.anno_fine == 1814
    assert list(r.anni_coperti) == [1813, 1814]


def test_un_registro_di_un_anno_solo_non_e_un_intervallo():
    r = _registro(1815, "Nati")
    r.titolo = "1815"
    assert r.anno_fine is None
    assert list(r.anni_coperti) == [1815]


def test_il_biennale_risponde_a_una_richiesta_sul_secondo_anno(tmp_path):
    """Chi chiede il 1814 deve ricevere il registro che apre nel 1813."""
    config = _config(tmp_path, anno_min=1814, anno_max=1814)
    r = _registro(1813, "Nati")
    r.titolo = "1813-1814"
    assert pertinente(r, config)[0]


def test_un_biennale_davvero_fuori_intervallo_resta_fuori(tmp_path):
    config = _config(tmp_path, anno_min=1820, anno_max=1830)
    r = _registro(1813, "Nati")
    r.titolo = "1813-1814"
    assert not pertinente(r, config)[0]


def test_il_biennale_copre_la_lacuna_e_non_fa_scattare_il_recupero(tmp_path):
    """Se il 1814 e' dentro il registro vero, la pubblicazione resta fuori."""
    config = _config(tmp_path)
    atto = _registro(1813, "Matrimoni")
    atto.titolo = "1813-1814"
    pubblicazione = _registro(1814, "Matrimoni, pubblicazioni")

    catalogo = _catalogo(atto, pubblicazione)
    assert recuperati(catalogo, config) == []


def test_l_anno_di_chiusura_si_legge_solo_se_e_maggiore():
    """Un titolo con due anni uguali, o al contrario, non e' un intervallo."""
    from history_maker.iiif import estrai_anno_fine

    assert estrai_anno_fine("1813-1814") == 1814
    assert estrai_anno_fine("1813") is None
    assert estrai_anno_fine("1813-1813") is None
    assert estrai_anno_fine("Registro del 1866") is None
    assert estrai_anno_fine("1866-1875") == 1875


# --- senza catalogo: la stessa selezione, ricavata dalle trascrizioni -------


def _pagina(cartella, slug, anno, tipologia):
    import json

    (cartella / slug).mkdir(parents=True, exist_ok=True)
    (cartella / slug / "0001-pag-1.json").write_text(json.dumps({"_origine": {
        "registro": slug, "anno": anno, "tipologia": tipologia,
        "contesto": "Archivio di Stato di Chieti > Stato civile napoleonico > Torrebruna",
    }}), encoding="utf-8")


def test_senza_catalogo_la_pubblicazione_col_suo_atto_resta_fuori(tmp_path):
    """Un clone non ha data/catalogo.json: prima prendeva tutto.

    Le pubblicazioni del 1810 entravano nel database e spostavano di 131 gli
    id di tutte le righe seguenti, a cui puntano le decisioni prese
    sull'immagine: nel clone ogni correzione finiva su un'altra persona.
    """
    from history_maker import dataset

    config = _config(tmp_path)
    _pagina(config.trascrizioni, "1810-matrimoni-18286806", 1810, "Matrimoni")
    _pagina(config.trascrizioni, "1810-matrimoni-pubblicazioni-18286799", 1810,
            "Matrimoni, pubblicazioni")
    assert dataset._ammessi_dalle_trascrizioni(config) == {"1810-matrimoni-18286806"}


def test_senza_catalogo_la_pubblicazione_senza_atto_rientra(tmp_path):
    """Il contrappeso: dove il registro dei matrimoni manca, la pubblicazione resta."""
    from history_maker import dataset

    config = _config(tmp_path)
    _pagina(config.trascrizioni, "1870-matrimoni-pubblicazioni-18259289", 1870,
            "Matrimoni, pubblicazioni")
    assert dataset._ammessi_dalle_trascrizioni(config) == {
        "1870-matrimoni-pubblicazioni-18259289"}
