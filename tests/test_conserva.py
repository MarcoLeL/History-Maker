"""Ricostruire il database senza perdere le prove.

'dataset.costruisci' mette da parte il file e lo rifa' dai JSON. Le decisioni
non discendono dai JSON — comprese quelle prese da una persona, che il
loro modulo chiama "la cosa piu' preziosa dell'archivio" — e le
riletture sono quota gia' spesa. Questi test difendono le due tabelle, e
soprattutto difendono il fatto che le decisioni continuino a puntare
alla persona giusta quando gli identificatori scorrono.
"""

import json
import sqlite3

import pytest

from history_maker import dataset


def _db(percorso, atti):
    """Un database minimo: atti e persone, negli id che SQLite assegna."""
    conn = sqlite3.connect(percorso)
    conn.executescript(dataset.SCHEMA_SQL)
    for registro, numero, ruoli in atti:
        cur = conn.execute(
            "INSERT INTO atti (registro, immagine, numero_atto, tipo, anno) "
            "VALUES (?,?,?,?,?)", (registro, f"{registro}/x.jpg", numero, "morte", 1850))
        for ruolo, nome in ruoli:
            conn.execute(
                "INSERT INTO persone (atto, ruolo, nome, nome_letto, cognome, "
                "cognome_letto) VALUES (?,?,?,?,?,?)",
                (cur.lastrowid, ruolo, nome, nome, "Lella", "Lella"))
    conn.commit()
    return conn


def test_una_decisione_segue_la_persona_anche_se_l_id_scorre(tmp_path):
    """Il caso vero: duecento atti recuperati in mezzo all'archivio.

    Ogni rowid successivo scorre, e una correzione che nomina la
    menzione 8050 finirebbe addosso a un'altra persona. La chiave
    stabile — registro, numero d'atto, ruolo, come si chiamava — non
    scorre.
    """
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r1", "uno", [("padre", "Filippo")]),
                          ("r1", "due", [("padre", "Isidoro")])])
    bersaglio = conn.execute(
        "SELECT id FROM persone WHERE nome='Isidoro'").fetchone()[0]
    conn.executescript(
        "CREATE TABLE decisioni (id INTEGER PRIMARY KEY, quando TEXT, azione TEXT,"
        " entita TEXT, motivo TEXT, confidenza REAL, evidenze TEXT,"
        " contraddizioni TEXT, atti TEXT, decisore TEXT, modello TEXT,"
        " versione_prompt TEXT, versione_algoritmo TEXT, disfa INTEGER)")
    conn.execute(
        "INSERT INTO decisioni (quando, azione, entita, motivo, decisore, evidenze) "
        "VALUES ('ora','correzione',?,'la carta dice cosi','persona','nome=Simone')",
        (json.dumps([bersaglio]),))
    conn.commit(); conn.close()

    conservato = dataset.conserva(percorso)
    percorso.unlink()
    # si rifa' con un atto IN PIU' in mezzo: tutti gli id scorrono
    conn = _db(percorso, [("r1", "uno", [("padre", "Filippo")]),
                          ("r1", "unobis", [("padre", "Nuovo")]),
                          ("r1", "due", [("padre", "Isidoro")])])
    conn.close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["decisioni"] == 1 and conti["orfane"] == 0

    conn = sqlite3.connect(percorso)
    entita = json.loads(conn.execute("SELECT entita FROM decisioni").fetchone()[0])
    nuovo = conn.execute("SELECT id FROM persone WHERE nome='Isidoro'").fetchone()[0]
    assert entita == [nuovo]
    assert nuovo != bersaglio          # l'id e' scorso davvero
    conn.close()


def test_una_decisione_senza_piu_bersaglio_si_mette_da_parte(tmp_path):
    """Non si indovina e non si butta: si mette da parte e lo si dice."""
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r1", "uno", [("padre", "Sparito")])])
    men = conn.execute("SELECT id FROM persone").fetchone()[0]
    conn.executescript(
        "CREATE TABLE decisioni (id INTEGER PRIMARY KEY, quando TEXT, azione TEXT,"
        " entita TEXT, motivo TEXT, confidenza REAL, evidenze TEXT,"
        " contraddizioni TEXT, atti TEXT, decisore TEXT, modello TEXT,"
        " versione_prompt TEXT, versione_algoritmo TEXT, disfa INTEGER)")
    conn.execute("INSERT INTO decisioni (quando, azione, entita, decisore) "
                 "VALUES ('ora','correzione',?,'persona')", (json.dumps([men]),))
    conn.commit(); conn.close()

    conservato = dataset.conserva(percorso)
    percorso.unlink()
    conn = _db(percorso, [("r1", "uno", [("padre", "Altro")])])
    conn.close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["decisioni"] == 0 and conti["orfane"] == 1
    conn = sqlite3.connect(percorso)
    assert conn.execute("SELECT COUNT(*) FROM decisioni_orfane").fetchone()[0] == 1
    conn.close()


def test_la_catena_del_disfa_resta_intera(tmp_path):
    """Una decisione che ne supera un'altra la nomina per id: se gli id
    si riscrivono, va riscritto anche il rimando, o la storia si spezza."""
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r1", "uno", [("padre", "Filippo")])])
    men = conn.execute("SELECT id FROM persone").fetchone()[0]
    conn.executescript(
        "CREATE TABLE decisioni (id INTEGER PRIMARY KEY, quando TEXT, azione TEXT,"
        " entita TEXT, motivo TEXT, confidenza REAL, evidenze TEXT,"
        " contraddizioni TEXT, atti TEXT, decisore TEXT, modello TEXT,"
        " versione_prompt TEXT, versione_algoritmo TEXT, disfa INTEGER)")
    conn.execute("INSERT INTO decisioni (id, quando, azione, entita, decisore) "
                 "VALUES (500,'ora','correzione',?,'gemini')", (json.dumps([men]),))
    conn.execute("INSERT INTO decisioni (id, quando, azione, entita, decisore, disfa) "
                 "VALUES (501,'poi','correzione',?,'persona',500)", (json.dumps([men]),))
    conn.commit(); conn.close()

    conservato = dataset.conserva(percorso)
    percorso.unlink()
    conn = _db(percorso, [("r1", "uno", [("padre", "Filippo")])]); conn.close()
    dataset.ripristina(percorso, conservato)

    conn = sqlite3.connect(percorso)
    prima, dopo = conn.execute("SELECT id, disfa FROM decisioni ORDER BY id").fetchall()
    assert prima[1] is None
    assert dopo[1] == prima[0]          # punta ancora a quella giusta
    conn.close()


def test_le_riletture_sopravvivono_alla_ricostruzione(tmp_path):
    """Sono quota gia' spesa: buttarle vuol dire ricomprarle."""
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r1", "uno", [("padre", "Filippo")])])
    atto = conn.execute("SELECT id FROM atti").fetchone()[0]
    from history_maker.ricostruzione import rilettura
    conn.executescript(rilettura.SCHEMA_TABELLA)
    conn.execute("INSERT INTO riletture (chiave, atto, esito, stato) "
                 "VALUES ('abc', ?, 'CONFERMATO', 'risposta')", (atto,))
    conn.commit(); conn.close()

    conservato = dataset.conserva(percorso)
    percorso.unlink()
    conn = _db(percorso, [("r1", "zero", [("padre", "Nuovo")]),
                          ("r1", "uno", [("padre", "Filippo")])])
    conn.close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["riletture"] == 1
    conn = sqlite3.connect(percorso)
    atto_nuovo, esito = conn.execute(
        "SELECT atto, esito FROM riletture").fetchone()
    assert esito == "CONFERMATO"
    assert atto_nuovo == conn.execute(
        "SELECT id FROM atti WHERE numero_atto='uno'").fetchone()[0]
    conn.close()


def test_il_database_di_prima_resta_finche_il_nuovo_non_e_pronto(tmp_path, monkeypatch):
    """Fra 'conserva' e 'ripristina' il registro non deve vivere solo in RAM.

    Se la ricostruzione muore a meta' — un Ctrl+C, il file tenuto aperto
    dal server dell'albero — le decisioni sono l'unica cosa del progetto
    che non si puo' rifare dai JSON.
    """
    from history_maker import dataset

    percorso = tmp_path / "torrebruna.sqlite"
    percorso.write_bytes(b"il registro di prima")
    precedente = tmp_path / "torrebruna.sqlite.precedente"

    class Config:
        pass

    visto = {}

    def esplode(*_a, **_k):
        visto["precedente"] = precedente.exists()
        visto["contenuto"] = precedente.read_bytes() if precedente.exists() else None
        raise KeyboardInterrupt

    monkeypatch.setattr(dataset, "conserva", lambda _p: {"decisioni": []})
    monkeypatch.setattr(dataset.sqlite3, "connect", esplode)
    config = Config()
    config.dataset = tmp_path

    with pytest.raises(KeyboardInterrupt):
        dataset.costruisci(config)

    assert visto["precedente"], "il database di prima e' stato cancellato"
    assert visto["contenuto"] == b"il registro di prima"


def _decisione(conn, menzione, motivo="perche' si", decisore="persona"):
    conn.execute(
        "INSERT INTO decisioni (quando, azione, entita, motivo, confidenza, "
        "evidenze, decisore, modello, versione_prompt) "
        "VALUES ('ora','correzione',?,?,1.0,'nome=X',?,'','')",
        (json.dumps([menzione]), motivo, decisore))


def test_le_orfane_sopravvivono_alla_ricostruzione_dopo(tmp_path):
    """La tabella che esiste per non perdere niente non deve perdersi."""
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r", "1", [("defunto", "Ada")])])
    conn.executescript(dataset.SCHEMA_ORFANE)
    conn.execute(
        "INSERT INTO decisioni_orfane (quando, azione, entita, motivo, decisore, "
        "perche, id_originale, chiavi) VALUES ('2026-01-01','correzione','[9]',"
        "'giudizio','persona','la menzione non c''era', 77, ?)",
        (json.dumps([["r", "1", "morte", "defunto", "Zoe", "Lella", 1]]),))
    conn.commit()
    conn.close()

    conservato = dataset.conserva(percorso)
    assert len(conservato["orfane"]) == 1

    # Si rifa' il database senza la menzione cercata: resta orfana, ma c'e'.
    percorso.unlink()
    _db(percorso, [("r", "1", [("defunto", "Ada")])]).close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["orfane"] == 1 and conti["riprese"] == 0

    conn = sqlite3.connect(percorso)
    riga = conn.execute(
        "SELECT id_originale, chiavi, decisore FROM decisioni_orfane").fetchone()
    assert riga[0] == 77 and riga[2] == "persona"
    assert json.loads(riga[1])[0][4] == "Zoe"


def test_un_orfana_torna_a_casa_se_la_menzione_ricompare(tmp_path):
    """Una trascrizione aggiunta, o un glossario corretto, e la decisione vale."""
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r", "1", [("defunto", "Ada")])])
    conn.executescript(dataset.SCHEMA_ORFANE)
    conn.execute(
        "INSERT INTO decisioni_orfane (quando, azione, entita, motivo, decisore, "
        "perche, id_originale, chiavi) VALUES ('2026-01-01','correzione','[9]',"
        "'giudizio','persona','la menzione non c''era', 77, ?)",
        (json.dumps([["r", "1", "morte", "defunto", "Zoe", "Lella", 1]]),))
    conn.commit()
    conn.close()
    conservato = dataset.conserva(percorso)

    # Adesso Zoe Pepe c'e'.
    percorso.unlink()
    _db(percorso, [("r", "1", [("defunto", "Ada"), ("defunto", "Zoe")])]).close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["riprese"] == 1 and conti["orfane"] == 0

    conn = sqlite3.connect(percorso)
    assert conn.execute("SELECT COUNT(*) FROM decisioni_orfane").fetchone()[0] == 0
    entita, = conn.execute("SELECT entita FROM decisioni").fetchone()
    (menzione,) = json.loads(entita)
    ruolo, nome = conn.execute(
        "SELECT ruolo, nome_letto FROM persone WHERE id=?", (menzione,)).fetchone()
    assert (ruolo, nome) == ("defunto", "Zoe")


def test_un_orfana_senza_chiave_non_si_riattacca_mai(tmp_path):
    """Il suo 'entita' e' un id di un database cancellato: un falso amico.

    E' l'errore vero: una correzione sull'eta' di una bambina di sedici
    mesi, riattaccata col numero vecchio, e' finita addosso al Sindaco.
    """
    percorso = tmp_path / "db.sqlite"
    conn = _db(percorso, [("r", "1", [("ufficiale", "Vincenzo")])])
    conn.executescript(dataset.SCHEMA_ORFANE)
    conn.execute(
        "INSERT INTO decisioni_orfane (quando, azione, entita, motivo, decisore, "
        "perche) VALUES ('2026-01-01','correzione','[1]',"
        "'eta: letto «sedici», sull''immagine «mesi undici»','gemini','vecchia')")
    conn.commit()
    conn.close()

    conservato = dataset.conserva(percorso)
    assert conservato["orfane"][0]["_senza_chiave"] is True

    percorso.unlink()
    _db(percorso, [("r", "1", [("ufficiale", "Vincenzo")])]).close()
    conti = dataset.ripristina(percorso, conservato)
    assert conti["orfane"] == 1 and conti["decisioni"] == 0

    conn = sqlite3.connect(percorso)
    assert conn.execute("SELECT COUNT(*) FROM decisioni").fetchone()[0] == 0
    (perche,) = conn.execute("SELECT perche FROM decisioni_orfane").fetchone()
    assert "nessuna chiave stabile" in perche
