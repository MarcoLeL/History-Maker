"""Le fasi 2, 3 e 4 collaudate senza toccare la rete ne' l'API."""

import json

import pytest
from PIL import Image

from history_maker import dataset, discover, download, transcribe
from history_maker.catalogo import Catalogo, Registro
from history_maker.config import Config, Trascrizione


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        comune="Torrebruna",
        termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"],
        escludi_contesto=["Guardiabruna"],
        anno_min=1809,
        anno_max=1900,
        tipologie=[],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
        # Il backend va fissato: il default del progetto e' Gemini, e
        # questi test collaudano la strada di Claude Code.
        trascrizione=Trascrizione(
            backend="claude-code",
            modello="claude-opus-5",
            pagine_per_chiamata=4,
            # Un'immagine per pagina: questi test collaudano la riduzione e
            # il raggruppamento, non la divisione delle facciate, che ha i
            # suoi in test_facciate.py.
            dividi_facciate=False,
        ),
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866",
        tipologia="Nati",
        anno=1866,
        archive_id="19944535",
    )


# --- fase 1: costruzione delle interrogazioni ------------------------------

def test_url_di_ricerca_per_anno():
    url = discover.url_ricerca("Torrebruna", 1866)
    assert url.startswith(
        "https://antenati.cultura.gov.it/search-registry/?localita=Torrebruna&anno=1866"
    )
    # E chiede il massimo di risultati per pagina: senza, il portale ne
    # mostra dieci e impagina il resto in silenzio.
    assert "s_size=100" in url


def test_url_di_ricerca_senza_anno():
    url = discover.url_ricerca("Torrebruna")
    assert "?localita=Torrebruna" in url and "anno=" not in url


# --- fase 2: nomi dei file ------------------------------------------------

@pytest.mark.parametrize(
    "canvas, indice, atteso",
    [
        ({"label": "12"}, 3, "0012"),          # l'etichetta numerica del portale vince
        ({"label": "Copertina"}, 1, "0001-copertina"),
        ({}, 7, "0007"),                        # senza etichetta si usa la posizione
    ],
)
def test_nome_pagina_ordinabile(canvas, indice, atteso):
    assert download.nome_pagina(canvas, indice) == atteso


def test_nomi_pagina_in_ordine_alfabetico():
    nomi = [download.nome_pagina({"label": str(n)}, n) for n in (2, 10, 100)]
    assert nomi == sorted(nomi)


# --- fase 3: preparazione delle immagini e del prompt ----------------------

def _immagine_finta(percorso, dimensione=(3000, 4000)):
    percorso.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", dimensione, "white").save(percorso, "JPEG")
    return percorso


def test_immagine_ridotta_al_lato_richiesto(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0001.jpg"), registro)
    ridotta = transcribe.prepara_immagine(pagina, config)
    with Image.open(ridotta) as immagine:
        assert max(immagine.size) == config.trascrizione.lato_lungo_px
    # L'originale a piena risoluzione non viene toccato: e' su quello che
    # si rilegge un atto dubbio.
    with Image.open(pagina.percorso) as originale:
        assert originale.size == (3000, 4000)


def test_immagine_piccola_non_viene_ingrandita(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0002.jpg", (800, 600)), registro)
    with Image.open(transcribe.prepara_immagine(pagina, config)) as immagine:
        assert immagine.size == (800, 600)


def test_immagine_ridotta_riusata(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0003.jpg", (900, 900)), registro)
    prima = transcribe.prepara_immagine(pagina, config)
    firma = prima.stat().st_mtime_ns
    assert transcribe.prepara_immagine(pagina, config).stat().st_mtime_ns == firma


def test_prompt_elenca_tutte_le_pagine_e_lo_schema(config, registro, tmp_path):
    pagine = [
        transcribe.Pagina(_immagine_finta(tmp_path / f"{i:04d}.jpg", (400, 400)), registro)
        for i in (1, 2, 3)
    ]
    percorsi = [transcribe.prepara_immagine(p, config) for p in pagine]
    prompt = transcribe.costruisci_prompt(pagine, [[p] for p in percorsi])

    for percorso in percorsi:
        assert str(percorso) in prompt
    assert "ARRAY JSON di 3 oggetti" in prompt
    # Senza output_config.format lo schema va chiesto a parole.
    assert "tipo_pagina" in prompt and "parti_illeggibili" in prompt
    assert "1866" in prompt and "Nati" in prompt


def test_id_richiesta_stabile(config, registro, tmp_path):
    pagina = transcribe.Pagina(_immagine_finta(tmp_path / "0042.jpg", (400, 400)), registro)
    assert pagina.id_richiesta == "1866-nati-19944535--0042"


def test_pagine_da_trascrivere_salta_quelle_gia_fatte(config, registro):
    catalogo = Catalogo(comune="Torrebruna", registri=[registro])
    catalogo.salva(config.catalogo)
    cartella = config.immagini / registro.slug
    for nome in ("0001.jpg", "0002.jpg"):
        _immagine_finta(cartella / nome, (400, 400))
    (cartella / "manifest.json").write_text("{}", encoding="utf-8")  # non e' una pagina

    assert len(transcribe.pagine_da_trascrivere(config)) == 2

    fatta = config.trascrizioni / registro.slug / "0001.json"
    fatta.parent.mkdir(parents=True, exist_ok=True)
    fatta.write_text("{}", encoding="utf-8")
    assert [p.percorso.name for p in transcribe.pagine_da_trascrivere(config)] == ["0002.jpg"]
    assert len(transcribe.pagine_da_trascrivere(config, solo_mancanti=False)) == 2


def test_registri_non_pertinenti_non_vengono_trascritti(config, registro):
    fuori = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua777/abc",
        contesto="Chieti/Guardiabruna", titolo="1866", tipologia="Nati", anno=1866,
        archive_id="777",
    )
    Catalogo(comune="Torrebruna", registri=[registro, fuori]).salva(config.catalogo)
    for reg in (registro, fuori):
        _immagine_finta(config.immagini / reg.slug / "0001.jpg", (400, 400))

    slug_trovati = {p.registro.slug for p in transcribe.pagine_da_trascrivere(config)}
    assert slug_trovati == {registro.slug}


def test_un_solo_registro_quando_il_buco_sta_in_un_fondo_solo(config, registro):
    """L'anno e' un filtro troppo largo per rifare un registro.

    Il caso vero: 1837-morti-17810411 aveva quaranta atti mancanti e
    nove pagine, ma '--anno 1837' ne trascriveva cento in trentaquattro
    chiamate. Con le ultime diciotto della giornata non ci si arrivava.
    """
    altro = Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua999/abc",
        contesto=registro.contesto, titolo=registro.titolo, tipologia="Morti",
        anno=registro.anno, archive_id="999",
    )
    Catalogo(comune="Torrebruna", registri=[registro, altro]).salva(config.catalogo)
    for reg in (registro, altro):
        _immagine_finta(config.immagini / reg.slug / "0001.jpg", (400, 400))

    tutti = transcribe.pagine_da_trascrivere(config)
    assert {p.registro.slug for p in tutti} == {registro.slug, altro.slug}

    solo = transcribe.pagine_da_trascrivere(config, registro=altro.slug)
    assert {p.registro.slug for p in solo} == {altro.slug}
    # basta un pezzo dello slug, non serve scriverlo intero
    assert transcribe.pagine_da_trascrivere(config, registro="999")
    # uno spazio o un ritorno a capo in coda non deve far sparire tutto:
    # e' successo davvero, leggendo i nomi da un file scritto su Windows
    assert transcribe.pagine_da_trascrivere(config, registro=altro.slug + "\r")

    # e un filtro che non combacia si fa sentire, invece di trascrivere
    # zero pagine uscendo con successo
    with pytest.raises(ValueError, match="nessun registro"):
        transcribe.pagine_da_trascrivere(config, registro="non-esiste")


def test_stima_conta_le_invocazioni_non_gli_euro(config, registro, tmp_path):
    pagine = [
        transcribe.Pagina(_immagine_finta(tmp_path / f"{i:04d}.jpg", (400, 400)), registro)
        for i in range(1, 10)
    ]
    testo = transcribe.stima(config, pagine)
    # 9 pagine a 4 per chiamata = 3 chiamate.
    assert "3 chiamate" in testo
    assert "$" not in testo and "euro" not in testo.lower()


# --- fase 4: aggregazione -------------------------------------------------

def _trascrizione(config, registro, nome, atti):
    percorso = config.trascrizioni / registro.slug / nome
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        json.dumps(
            {
                "tipo_pagina": "atti",
                "anno_indicato": str(registro.anno),
                "comune_indicato": "Torrebruna",
                "atti": atti,
                "osservazioni": None,
                "_origine": {
                    "immagine": f"{registro.slug}/{nome.replace('.json', '.jpg')}",
                    "registro": registro.slug,
                    "ark_url": registro.ark_url,
                    "anno": registro.anno,
                    "tipologia": registro.tipologia,
                    "contesto": registro.contesto,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


ATTO = {
    "numero_atto": "17",
    "tipo": "nascita",
    "data_atto": "1866-03-27",
    "data_evento": "1866-03-26",
    "ora_evento": "ore ventidue",
    "luogo": "Torrebruna",
    "persone": [
        {"ruolo": "neonato", "nome": "Maria", "cognome": "Di Nardo", "eta": None,
         "professione": None, "residenza": "Torrebruna", "stato_vitale": None, "note": None},
        {"ruolo": "padre", "nome": "Giuseppe", "cognome": "Di Nardo", "eta": "trentadue",
         "professione": "contadino", "residenza": "Torrebruna", "stato_vitale": "vivente", "note": None},
    ],
    "testo_integrale": "L'anno milleottocentosessantasei, addi' ventisette di marzo...",
    "parti_illeggibili": ["il cognome della levatrice"],
    "affidabilita": "alta",
}


def test_dataset_da_trascrizioni(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    _trascrizione(config, registro, "0002.json", [])  # pagina bianca

    percorso_db = dataset.costruisci(config)
    import sqlite3

    conn = sqlite3.connect(percorso_db)
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 2
    assert conn.execute("SELECT anno FROM atti").fetchone()[0] == 1866
    assert conn.execute(
        "SELECT incertezze FROM atti"
    ).fetchone()[0] == "il cognome della levatrice"

    # La ricerca full-text e' il modo in cui si interroga davvero l'archivio.
    trovati = conn.execute(
        "SELECT numero_atto FROM atti_fts WHERE atti_fts MATCH 'milleottocentosessantasei'"
    ).fetchall()
    assert trovati == [("17",)]
    conn.close()

    assert (config.dataset / "atti.csv").exists()
    assert (config.dataset / "persone.csv").exists()
    sintesi = (config.dataset / "sintesi.md").read_text(encoding="utf-8")
    assert "Di Nardo" in sintesi and "contadino" in sintesi


def test_sintesi_elenca_gli_anni_senza_atti(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    dataset.costruisci(config)
    sintesi = (config.dataset / "sintesi.md").read_text(encoding="utf-8")
    assert "Anni senza atti trascritti" in sintesi
    assert "1809-1865" in sintesi and "1867-1900" in sintesi


def test_ricostruzione_non_duplica(config, registro):
    _trascrizione(config, registro, "0001.json", [ATTO])
    dataset.costruisci(config)
    percorso_db = dataset.costruisci(config)
    import sqlite3

    conn = sqlite3.connect(percorso_db)
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize(
    "anni, atteso",
    [
        ([1810, 1811, 1812, 1820], "1810-1812, 1820"),
        ([1850], "1850"),
        ([], "nessuno"),
    ],
)
def test_intervalli_compattati(anni, atteso):
    assert dataset._compatta_intervalli(anni) == atteso


# --- restrizione a un singolo anno ----------------------------------------

def _registro_anno(anno: int) -> Registro:
    return Registro(
        ark_url=f"https://antenati.cultura.gov.it/ark:/12657/an_ua{anno}/x",
        contesto="Chieti/Stato civile della restaurazione/Torrebruna",
        titolo=str(anno), tipologia="Nati", anno=anno, archive_id=str(anno),
        manifest_url=f"https://esempio/{anno}/manifest", n_immagini=3,
    )


def test_download_ristretto_a_un_anno(config):
    """--anno 1809 deve selezionare solo il 1809, non tutto il secolo."""
    catalogo = Catalogo(
        comune="Torrebruna",
        registri=[_registro_anno(a) for a in (1809, 1810, 1866)],
    )
    catalogo.salva(config.catalogo)

    esito = download.esegui(config, solo_stima=True, dal=1809, al=1809)
    assert esito.scaricate == 0  # --elenca non scarica

    # Il filtro si verifica su quali registri sopravvivono alla selezione.
    from history_maker.catalogo import pertinente

    selezionati = [
        r for r in catalogo.registri
        if pertinente(r, config)[0] and 1809 <= (r.anno or 0) <= 1809
    ]
    assert [r.anno for r in selezionati] == [1809]


def test_trascrizione_ristretta_a_un_anno(config):
    catalogo = Catalogo(
        comune="Torrebruna", registri=[_registro_anno(a) for a in (1809, 1866)]
    )
    catalogo.salva(config.catalogo)
    for registro in catalogo.registri:
        _immagine_finta(config.immagini / registro.slug / "0001.jpg", (400, 400))

    tutte = transcribe.pagine_da_trascrivere(config)
    assert len(tutte) == 2

    solo_1809 = transcribe.pagine_da_trascrivere(config, dal=1809, al=1809)
    assert [p.registro.anno for p in solo_1809] == [1809]


def test_le_pagine_da_trascrivere_sono_in_ordine_di_anno(config, tmp_path):
    """L'ordine conta perche' il lavoro si interrompe a meta'.

    La quota giornaliera finisce, ed e' normale. Quello che cambia e' cosa
    resta in mano: il catalogo elenca i registri come il portale li ha
    restituiti — prima tutti i Nati di cinquant'anni, poi tutti i Morti
    degli stessi — e seguirlo lascia un mosaico con buchi in ogni
    decennio. Per ricostruire parentele serve un blocco compatto.
    """
    from history_maker.catalogo import Catalogo, Registro

    registri = [
        Registro(ark_url=f"https://x/{a}{t}", contesto="Chieti/Torrebruna",
                 titolo=str(a), tipologia=t, anno=a, archive_id=f"{a}{t}")
        for t in ("Nati", "Morti")          # per tipologia, come fa il portale
        for a in (1840, 1820, 1830)          # e in ordine sparso
    ]
    Catalogo(comune="Torrebruna", registri=registri).salva(config.catalogo)
    for r in registri:
        cartella = config.immagini / r.slug
        cartella.mkdir(parents=True, exist_ok=True)
        _immagine_finta(cartella / "0001.jpg", (400, 400))

    anni = [p.registro.anno for p in transcribe.pagine_da_trascrivere(config)]
    assert anni == sorted(anni), f"ordine non cronologico: {anni}"
