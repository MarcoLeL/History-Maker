"""Il backend Gemini, senza toccare la rete ne' consumare quota.

Tre cose vanno collaudate, e sono le tre in cui questo backend puo'
sbagliare in silenzio: la traduzione dello schema nel dialetto di Gemini
(che se sbaglia fa fallire *ogni* chiamata con un 400), il conteggio
delle richieste del piano gratuito (che se sbaglia si prende una raffica
di 429 a meta' lavoro), e la lettura della risposta — dove un JSON
troncato a meta' e' peggio di un errore, perche' sembra un risultato.
"""

import json

import pytest
from PIL import Image

from history_maker import gemini, schema, transcribe
from history_maker.backend import BackendNonDisponibile, LimiteUsoRaggiunto, Richiesta
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
        glossario=tmp_path / "glossario-inesistente.yaml",
        trascrizione=Trascrizione(backend="gemini", modello="gemini-2.5-flash"),
    )


@pytest.fixture
def registro() -> Registro:
    return Registro(
        ark_url="https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x",
        contesto="Chieti/Stato civile italiano/Torrebruna",
        titolo="1866", tipologia="Nati", anno=1866, archive_id="19944535",
    )


@pytest.fixture
def chiave(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "chiave-di-prova")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


class FintaRisposta:
    def __init__(self, corpo, status_code=200):
        self.status_code = status_code
        self._corpo = corpo
        self.text = json.dumps(corpo)

    def json(self):
        return self._corpo


class FintaSessione:
    """Registra le richieste e risponde da una coda preparata."""

    def __init__(self, *risposte):
        self.risposte = list(risposte)
        self.richieste = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.richieste.append({"url": url, "corpo": json, "headers": headers})
        return self.risposte.pop(0)


def _risposta_ok(pagine, token_in=1000, token_out=500):
    return FintaRisposta(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps(pagine)}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": token_in, "candidatesTokenCount": token_out},
        }
    )


# --- lo schema nel dialetto di Gemini --------------------------------------


def test_tipo_unione_diventa_nullable():
    """``["string", "null"]`` non esiste in OpenAPI: il nullo e' un attributo."""
    assert gemini.per_gemini({"type": ["string", "null"]}) == {
        "type": "STRING",
        "nullable": True,
    }


def test_additional_properties_sparisce():
    convertito = gemini.per_gemini(
        {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    )
    assert "additionalProperties" not in convertito
    assert convertito["properties"] == {"a": {"type": "STRING"}}


def test_enum_perde_il_nullo_e_lo_rende_nullable():
    """Un enum con dentro ``None`` fa fallire la richiesta con un 400."""
    convertito = gemini.per_gemini(
        {"type": ["string", "null"], "enum": ["sinistra", "destra", None]}
    )
    assert convertito["enum"] == ["sinistra", "destra"]
    assert convertito["nullable"] is True


def test_ordine_dei_campi_conservato():
    convertito = gemini.per_gemini(schema.PERSONA)
    assert convertito["propertyOrdering"][:3] == ["ruolo", "nome", "cognome"]


def test_lo_schema_del_progetto_non_lascia_residui_di_json_schema():
    """Nessuna chiave che l'API rifiuterebbe deve sopravvivere alla conversione."""
    convertito = gemini.per_gemini(schema.risposta_gruppo())
    vietate = {"additionalProperties", "$ref", "anyOf", "oneOf", "definitions"}

    def controlla(nodo):
        if isinstance(nodo, dict):
            assert not (vietate & set(nodo)), f"chiave vietata in {sorted(set(nodo))}"
            # Nessun tipo unione deve essere sopravvissuto.
            assert not isinstance(nodo.get("type"), list)
            if isinstance(nodo.get("enum"), list):
                assert None not in nodo["enum"]
            for valore in nodo.values():
                controlla(valore)
        elif isinstance(nodo, list):
            for voce in nodo:
                controlla(voce)

    controlla(convertito)


def test_senza_testo_integrale_lo_schema_non_lo_contiene():
    """Toglierlo dal prompt non basta: se resta nello schema, il modello lo produce."""
    atto = schema.risposta_gruppo(testo_integrale=False)["items"]["properties"]["atti"]["items"]
    assert "testo_integrale" not in atto["properties"]
    assert "testo_integrale" not in atto["required"]
    # E con il valore predefinito c'e' ancora.
    completo = schema.risposta_gruppo()["items"]["properties"]["atti"]["items"]
    assert "testo_integrale" in completo["properties"]


def test_il_file_e_nello_schema_del_gruppo():
    """Senza ``file`` l'allineamento resta appeso all'ordine delle risposte."""
    voce = schema.risposta_gruppo()["items"]
    assert voce["properties"]["file"]["type"] == "string"
    assert voce["required"][0] == "file"


# --- il ritmo del piano gratuito -------------------------------------------


def test_il_conteggio_giornaliero_sopravvive_al_processo(tmp_path):
    """Rilanciare il comando non deve azzerare la quota gia' consumata."""
    registro = tmp_path / "quota.json"
    primo = gemini.Ritmo(al_minuto=0, al_giorno=10, registro=registro)
    primo.segna()
    primo.segna()

    secondo = gemini.Ritmo(al_minuto=0, al_giorno=10, registro=registro)
    assert secondo.fatte_oggi() == 2
    assert secondo.restano_oggi() == 8


def test_un_giorno_nuovo_azzera_il_conteggio(tmp_path):
    registro = tmp_path / "quota.json"
    registro.write_text(json.dumps({"giorno": "2001-01-01", "richieste": 999}), encoding="utf-8")
    assert gemini.Ritmo(al_minuto=0, al_giorno=10, registro=registro).restano_oggi() == 10


def test_quota_giornaliera_finita_non_e_un_errore_di_lettura(tmp_path):
    """Va distinta da una pagina illeggibile: si riprende domani, non si scarta."""
    registro = tmp_path / "quota.json"
    ritmo = gemini.Ritmo(al_minuto=0, al_giorno=2, registro=registro)
    ritmo.segna()
    ritmo.segna()
    with pytest.raises(LimiteUsoRaggiunto, match="giornaliere"):
        ritmo.attendi_turno()


def test_senza_tetto_giornaliero_non_si_conta_nulla(tmp_path):
    registro = tmp_path / "quota.json"
    ritmo = gemini.Ritmo(al_minuto=0, al_giorno=0, registro=registro)
    ritmo.segna()
    assert ritmo.restano_oggi() is None
    assert not registro.exists()


def test_un_registro_illeggibile_non_blocca_il_lavoro(tmp_path):
    registro = tmp_path / "quota.json"
    registro.write_text("non e' JSON", encoding="utf-8")
    assert gemini.Ritmo(al_minuto=0, al_giorno=10, registro=registro).fatte_oggi() == 0


# --- la richiesta ----------------------------------------------------------


def test_senza_chiave_il_messaggio_dice_dove_prenderla(config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(BackendNonDisponibile, match="aistudio.google.com"):
        gemini.BackendGemini(config).verifica()


def test_la_chiave_viaggia_nell_intestazione_non_nell_url(config, chiave, tmp_path):
    """Un URL con dentro una chiave finisce nei log di chiunque stia in mezzo."""
    immagine = tmp_path / "0001.jpg"
    Image.new("RGB", (40, 50), "white").save(immagine, "JPEG")
    sessione = FintaSessione(_risposta_ok([{"file": "0001.jpg", "atti": []}]))

    gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", immagini=[immagine], modello="gemini-2.5-flash")
    )

    richiesta = sessione.richieste[0]
    assert richiesta["headers"]["x-goog-api-key"] == "chiave-di-prova"
    assert "chiave-di-prova" not in richiesta["url"]


def test_ogni_immagine_e_preceduta_dalla_sua_etichetta(config, chiave, tmp_path):
    """E' l'etichetta a tenere insieme il nome del file e i pixel giusti."""
    immagini = []
    for nome in ("0001.jpg", "0002.jpg"):
        percorso = tmp_path / nome
        Image.new("RGB", (40, 50), "white").save(percorso, "JPEG")
        immagini.append(percorso)
    sessione = FintaSessione(_risposta_ok([]))

    gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", immagini=immagini, modello="gemini-2.5-flash")
    )

    parti = sessione.richieste[0]["corpo"]["contents"][0]["parts"]
    tipi = ["testo" if "text" in p else "immagine" for p in parti]
    assert tipi == ["testo", "testo", "immagine", "testo", "immagine"]
    assert "0001.jpg" in parti[1]["text"]
    assert "0002.jpg" in parti[3]["text"]


def test_lo_schema_viaggia_nella_richiesta(config, chiave, tmp_path):
    sessione = FintaSessione(_risposta_ok([]))
    gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", schema=schema.risposta_gruppo(), modello="m")
    )
    generazione = sessione.richieste[0]["corpo"]["generationConfig"]
    assert generazione["responseMimeType"] == "application/json"
    assert generazione["responseSchema"]["type"] == "ARRAY"
    # Una trascrizione deve dare la stessa lettura sulla stessa pagina.
    assert generazione["temperature"] == 0.0


def test_il_ragionamento_si_puo_limitare(config, chiave):
    import dataclasses

    config = dataclasses.replace(
        config, trascrizione=dataclasses.replace(config.trascrizione, token_ragionamento=0)
    )
    sessione = FintaSessione(_risposta_ok([]))
    gemini.BackendGemini(config, sessione).esegui(Richiesta(sistema="s", istruzione="i", modello="m"))
    assert sessione.richieste[0]["corpo"]["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 0
    }


def test_ragionamento_lasciato_al_modello_non_compare(config, chiave):
    sessione = FintaSessione(_risposta_ok([]))
    gemini.BackendGemini(config, sessione).esegui(Richiesta(sistema="s", istruzione="i", modello="m"))
    assert "thinkingConfig" not in sessione.richieste[0]["corpo"]["generationConfig"]


# --- la risposta -----------------------------------------------------------


def test_i_consumi_tornano_al_chiamante(config, chiave):
    sessione = FintaSessione(_risposta_ok([{"file": "a.jpg"}], token_in=1234, token_out=567))
    esito = gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", modello="m")
    )
    assert esito.ok
    assert (esito.token_contesto, esito.token_output) == (1234, 567)


def test_il_429_e_quota_non_errore(config, chiave):
    """Con il ritardo suggerito dall'API, che vale piu' di un'attesa a caso."""
    sessione = FintaSessione(
        FintaRisposta(
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "quota superata",
                    "details": [{"@type": "...RetryInfo", "retryDelay": "38s"}],
                }
            },
            status_code=429,
        )
    )
    with pytest.raises(LimiteUsoRaggiunto) as errore:
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", modello="m")
        )
    assert errore.value.attesa_s == 38.0


def test_chiave_rifiutata_lo_dice_subito(config, chiave):
    sessione = FintaSessione(
        FintaRisposta({"error": {"code": 403, "message": "API key not valid"}}, status_code=403)
    )
    with pytest.raises(BackendNonDisponibile, match="GEMINI_API_KEY"):
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", modello="m")
        )


def test_risposta_troncata_non_passa_per_buona(config, chiave):
    """Un JSON tagliato a meta' e' peggio di un errore: sembra un risultato."""
    sessione = FintaSessione(
        FintaRisposta(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '[{"file": "a.jpg", "att'}]},
                     "finishReason": "MAX_TOKENS"}
                ],
                "usageMetadata": {},
            }
        )
    )
    esito = gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", modello="m")
    )
    assert not esito.ok
    assert "troncata" in esito.errore


def test_prompt_bloccato_non_e_una_pagina_illeggibile(config, chiave):
    sessione = FintaSessione(FintaRisposta({"promptFeedback": {"blockReason": "SAFETY"}}))
    esito = gemini.BackendGemini(config, sessione).esegui(
        Richiesta(sistema="s", istruzione="i", modello="m")
    )
    assert not esito.ok and "SAFETY" in esito.errore


# --- la fase 3 sopra questo backend ----------------------------------------


def test_un_gruppo_intero_finisce_nei_file_giusti(config, registro, chiave, monkeypatch):
    """La prova che conta: dalla risposta di Gemini ai JSON su disco."""
    Catalogo(comune="Torrebruna", registri=[registro]).salva(config.catalogo)
    cartella = config.immagini / registro.slug
    cartella.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        Image.new("RGB", (400, 500), "white").save(cartella / f"{i:04d}.jpg", "JPEG")
    pagine = transcribe.pagine_da_trascrivere(config)

    sessione = FintaSessione(
        _risposta_ok(
            [
                # Il modello ripete l'etichetta intera, non il solo nome:
                # e' il caso normale con le immagini allegate.
                {"file": "immagine 1 (0001.jpg)", "tipo_pagina": "copertina", "atti": []},
                {
                    "file": "immagine 2 (0002.jpg)",
                    "tipo_pagina": "atti",
                    "atti": [{"numero_atto": "7", "tipo": "nascita", "persone": []}],
                },
            ]
        )
    )
    motore = gemini.BackendGemini(config, sessione)

    esito = transcribe.trascrivi_gruppo(config, pagine, motore)

    assert (esito.trascritte, esito.fallite) == (2, 0)
    prima = json.loads((config.trascrizioni / registro.slug / "0001.json").read_text("utf-8"))
    seconda = json.loads((config.trascrizioni / registro.slug / "0002.json").read_text("utf-8"))
    assert prima["tipo_pagina"] == "copertina"
    assert seconda["atti"][0]["numero_atto"] == "7"
    # 'file' serviva solo ad allineare: non e' un dato dell'atto.
    assert "file" not in prima


def test_il_prompt_non_chiede_uno_strumento_che_non_esiste(config, registro, chiave, tmp_path):
    """A Gemini non c'e' nessun ``Read`` da usare: chiederlo e' una chiamata buttata."""
    percorso = tmp_path / "0001.jpg"
    Image.new("RGB", (40, 50), "white").save(percorso, "JPEG")
    pagina = transcribe.Pagina(percorso, registro)
    motore = gemini.BackendGemini(config, FintaSessione())

    prompt = transcribe.costruisci_prompt([pagina], [[percorso]], motore=motore)

    assert "strumento Read" not in prompt
    # L'etichetta qualifica il nome col registro: dentro ogni registro le
    # pagine si chiamano tutte '0001-pag-1.jpg', e il nome nudo non
    # distingue niente appena una chiamata ne attraversa piu' d'uno.
    assert f"immagine 1 ({percorso.parent.name}/0001.jpg)" in prompt


def test_una_richiesta_troppo_grande_si_ferma_prima_di_partire(config, chiave, tmp_path,
                                                               monkeypatch):
    """Meglio un messaggio che dice quale manopola girare che un errore di rete."""
    monkeypatch.setattr(gemini, "LIMITE_RICHIESTA_BYTE", 1000)
    percorso = tmp_path / "0001.jpg"
    Image.new("RGB", (400, 400), "white").save(percorso, "JPEG")
    sessione = FintaSessione()

    with pytest.raises(BackendNonDisponibile, match="pagine_per_chiamata"):
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", immagini=[percorso], modello="m")
        )
    assert not sessione.richieste  # non e' nemmeno partita


def test_una_richiesta_malformata_non_si_ritenta(config, chiave):
    """Uno schema che l'API rifiuta fallisce identico ogni volta: ritentarlo brucia quota."""
    sessione = FintaSessione(
        FintaRisposta(
            {"error": {"code": 400, "message": "Invalid JSON payload: unknown name 'foo'"}},
            status_code=400,
        )
    )
    with pytest.raises(BackendNonDisponibile, match="rifiutato la richiesta"):
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", modello="m")
        )


def test_ogni_tentativo_conta_come_google_lo_conta(config, chiave):
    """Anche una richiesta respinta consuma quota: Google conta i tentativi.

    Qui per un po' si contavano solo le 2xx, su un confronto che sembrava
    provarlo — il contatore locale diceva 23 dove AI Studio diceva 17. Il
    confronto era falsato: quelle sette fallite erano contro un ALTRO
    modello, quindi un altro secchiello. Il risultato e' stato un
    contatore che sottostimava, e un lavoro andato a sbattere contro un
    muro che credeva lontano: 493 secondo noi, 503 secondo il cruscotto.
    """
    sessione = FintaSessione(
        FintaRisposta(
            {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "ritmo"}},
            status_code=429,
        )
    )
    motore = gemini.BackendGemini(config, sessione)
    prima = motore.ritmo.fatte_oggi()
    with pytest.raises(LimiteUsoRaggiunto):
        motore.esegui(Richiesta(sistema="s", istruzione="i", modello="m"))
    assert motore.ritmo.fatte_oggi() == prima + 1


@pytest.mark.parametrize(
    "errore, giornaliera",
    [
        ({"message": "Quota exceeded for quota metric 'generate_content_free_tier_requests'"}, True),
        ({"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]}]}, True),
        ({"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProject"}]}]}, False),
        ({"message": "too many requests"}, False),
        ({}, False),
    ],
)
def test_il_muro_del_giorno_si_distingue_dal_limite_al_minuto(errore, giornaliera):
    """Il retryDelay non li distingue: l'API ne allega uno breve a entrambi.

    Confonderli e' costato una corsa: il lavoro ha ritentato per otto
    minuti contro una quota giornaliera esaurita, credendo di aspettare
    qualche secondo di congestione. A dirlo e' il nome della quota
    violata, non il tempo suggerito.
    """
    assert gemini._e_quota_giornaliera(errore) is giornaliera


def test_dopo_troppi_rifiuti_di_fila_si_smette_comunque():
    """La rete di sicurezza che non dipende dal saper leggere l'errore."""
    from history_maker.transcribe import MAX_RITENTATIVI_DI_RITMO, _e_solo_ritmo

    breve = LimiteUsoRaggiunto("ritmo", attesa_s=30)
    assert _e_solo_ritmo(breve, tentativi=1)
    assert not _e_solo_ritmo(breve, tentativi=MAX_RITENTATIVI_DI_RITMO + 1)


def test_una_quota_giornaliera_non_si_ritenta_mai():
    from history_maker.transcribe import _e_solo_ritmo

    muro = LimiteUsoRaggiunto("quota del giorno", attesa_s=30, giornaliera=True)
    assert not _e_solo_ritmo(muro, tentativi=1)


# --- quanto ci vuole -------------------------------------------------------


def _con(config, **campi):
    import dataclasses
    return dataclasses.replace(
        config, trascrizione=dataclasses.replace(config.trascrizione, **campi)
    )


def test_il_tempo_lo_decide_la_generazione_non_la_quota(config):
    """Il difetto che questa funzione esiste per non ripetere.

    La prima versione divideva le chiamate per il limite di richieste al
    minuto: su 86 pagine prometteva due minuti, ce ne sono voluti
    diciannove. Il limite ne consentirebbe dieci al minuto, ne partono 0,7.
    """
    secondi, vincolo = transcribe._tempo_gemini(config, chiamate=15, pagine=86)
    assert vincolo == "la generazione del modello"
    # Misurato sulla corsa vera: 19,3 minuti. La stima non deve essere
    # ottimistica di un ordine di grandezza, come lo era prima.
    assert 15 * 60 <= secondi <= 30 * 60


def test_senza_testo_integrale_si_fa_prima(config):
    """Un terzo di token prodotti in meno e' un terzo di tempo in meno."""
    con, _ = transcribe._tempo_gemini(config, 15, 86)
    senza, _ = transcribe._tempo_gemini(_con(config, testo_integrale=False), 15, 86)
    assert senza < con


@pytest.mark.parametrize("pagine_per_chiamata", [1, 4, 6, 10, 20])
def test_il_limite_di_richieste_al_minuto_non_morde_mai(config, pagine_per_chiamata):
    """Alla velocita' di generazione misurata, l'RPM e' un limite teorico.

    Perche' mordesse servirebbero piu' di due chiamate e mezzo per
    pagina — 60/RPM > 15 s a pagina — e una chiamata porta almeno una
    pagina. E' il motivo per cui la vecchia stima, che calcolava solo
    quello, sbagliava di un ordine di grandezza: misurava l'unico dei tre
    vincoli che non si raggiunge mai.
    """
    pagine = 86
    chiamate = -(-pagine // pagine_per_chiamata)
    _, vincolo = transcribe._tempo_gemini(config, chiamate, pagine)
    assert vincolo == "la generazione del modello"


def test_un_tetto_di_token_basso_diventa_il_vincolo(config):
    _, vincolo = transcribe._tempo_gemini(_con(config, token_al_minuto=1000), 15, 86)
    assert "token al minuto" in vincolo


def test_i_limiti_a_zero_non_vincolano_nulla(config):
    """Zero disattiva il controllo: resta solo il tempo di generazione."""
    _, vincolo = transcribe._tempo_gemini(
        _con(config, richieste_al_minuto=0, token_al_minuto=0), 15, 86
    )
    assert vincolo == "la generazione del modello"


def test_ogni_modello_ha_il_suo_contatore(config, tmp_path):
    """I limiti di Google sono per modello, e i contatori devono esserlo.

    gemini-3.6-flash da' 20 richieste al giorno, un flash-lite ne da' 500:
    sono due secchielli separati. Con un contatore solo, provare un
    modello per mezz'ora falserebbe la contabilita' di quello con cui si
    sta facendo il lavoro vero — ed e' successo.
    """
    import dataclasses

    flash = gemini.BackendGemini(config, FintaSessione())
    lite = gemini.BackendGemini(
        dataclasses.replace(
            config, trascrizione=dataclasses.replace(config.trascrizione, modello="lite")
        ),
        FintaSessione(),
    )
    assert flash.ritmo.registro != lite.ritmo.registro

    flash.ritmo.segna()
    assert flash.ritmo.fatte_oggi() == 1
    assert lite.ritmo.fatte_oggi() == 0


def test_un_servizio_occupato_non_fa_spacchettare_il_gruppo(config, chiave):
    """Un 503 non riguarda nessuna pagina in particolare.

    "This model is currently experiencing high demand" dice che il modello
    e' sovraccarico, non che la richiesta sia sbagliata. Trattandolo come
    fallimento del gruppo, il codice ripiegava a UNA PAGINA PER CHIAMATA:
    dodici richieste al posto di una. E' successo davvero, quattro volte
    in tre minuti, e ha bruciato oltre cento chiamate di quota.
    """
    sessione = FintaSessione(
        FintaRisposta(
            {"error": {"code": 503, "message": "This model is currently experiencing high demand."}},
            status_code=503,
        )
    )
    with pytest.raises(LimiteUsoRaggiunto) as errore:
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", modello="m")
        )
    assert "occupato" in str(errore.value)
    # E' transitorio: si aspetta e si ritenta lo stesso gruppo.
    assert not errore.value.giornaliera
    assert errore.value.attesa_s == gemini.ATTESA_SERVIZIO_OCCUPATO_S


def test_un_500_qualunque_si_tratta_allo_stesso_modo(config, chiave):
    sessione = FintaSessione(FintaRisposta({"error": {"code": 500}}, status_code=500))
    with pytest.raises(LimiteUsoRaggiunto):
        gemini.BackendGemini(config, sessione).esegui(
            Richiesta(sistema="s", istruzione="i", modello="m")
        )
