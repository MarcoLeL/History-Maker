"""La premessa di una correzione: parla ancora della persona che ha davanti?"""

from history_maker.revisione_correzioni import _premessa_regge


def test_la_premessa_regge_se_il_campo_dice_ancora_quello():
    assert _premessa_regge(
        "cognome: letto «Torzi», sull'immagine «Lozzi»; …", "cognome", "Torzi")


def test_la_premessa_cade_se_il_campo_dice_un_altra_cosa():
    """Il caso vero: la correzione di una bambina finita su un Sindaco."""
    assert _premessa_regge(
        "eta: letto «sedici», sull'immagine «mesi undici»; …", "eta", "") is False
    assert _premessa_regge(
        "nome: letto «Diodata», sull'immagine «Diodato»; …",
        "nome", "Vincenzo") is False


def test_gli_accenti_e_le_maiuscole_non_fanno_cadere_una_premessa():
    assert _premessa_regge(
        "nome: letto «Nicolo'», sull'immagine «Nicola»; …", "nome", "Nicolò")


def test_un_motivo_che_non_dichiara_niente_non_si_giudica():
    assert _premessa_regge("la pagina dice «Lozzi».", "cognome", "Torzi") is None
    assert _premessa_regge(None, "nome", "Ada") is None


def test_la_premessa_di_un_altro_campo_non_si_giudica():
    assert _premessa_regge(
        "cognome: letto «Torzi», …", "professione", "bovaro") is None


def test_una_conferma_toglie_la_correzione_dalla_coda(tmp_path):
    """Una verifica che resta in una chat non accorcia nessun elenco."""
    import json
    import sqlite3

    from history_maker import dataset, revisione_correzioni

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dataset.SCHEMA_SQL)
    conn.executescript(
        "CREATE TABLE decisioni (id INTEGER PRIMARY KEY, quando TEXT, azione TEXT,"
        " entita TEXT, motivo TEXT, confidenza REAL, evidenze TEXT,"
        " contraddizioni TEXT, atti TEXT, decisore TEXT, modello TEXT,"
        " versione_prompt TEXT, versione_algoritmo TEXT, disfa INTEGER)")
    conn.execute("INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno) "
                 "VALUES (1,'r','p.jpg','1','morte',1850)")
    conn.execute("INSERT INTO persone (id, atto, ruolo, nome, cognome) "
                 "VALUES (7,1,'defunto','Ada','Torzi')")
    conn.execute("INSERT INTO decisioni (quando,azione,entita,motivo,confidenza,"
                 "evidenze,decisore) VALUES ('ora','correzione',?,"
                 "'cognome: letto «Torzi», sull''immagine «Lozzi»',0.9,"
                 "'cognome=Lozzi','gemini')", (json.dumps([7]),))
    conn.commit()

    (prima,) = revisione_correzioni.elenco(conn)
    assert prima["confermata"] is False
    assert revisione_correzioni.riassunto([prima])["da_guardare"] == 1

    conn.execute("INSERT INTO decisioni (quando,azione,entita,motivo,confidenza,"
                 "evidenze,decisore) VALUES ('ora','conferma',?,'guardata sulla "
                 "carta',1.0,'cognome=Lozzi','claude')", (json.dumps([7]),))
    conn.commit()

    (dopo,) = revisione_correzioni.elenco(conn)
    assert dopo["confermata"] is True
    r = revisione_correzioni.riassunto([dopo])
    assert r["da_guardare"] == 0 and r["confermate"] == 1
