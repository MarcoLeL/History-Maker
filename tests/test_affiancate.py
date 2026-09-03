"""Che i due motori non si distruggano piu' a vicenda.

Il guaio che questi test difendono era silenzioso, ed e' il motivo per
cui va difeso con dei test invece che con una nota nella
documentazione: la V1 e la V2 scrivevano nelle stesse tabelle, la V1 non
conosceva ``fatti`` e ``anomalie`` e quindi non le buttava, e trecentomila
righe restavano a puntare a individui rinumerati da zero. Nessun errore,
nessun avviso, e ogni interrogazione che univa le due parti dava numeri
sbagliati senza dirlo.
"""

import sqlite3

import pytest

from history_maker import affiancate, dataset, genealogia, qualita
from history_maker.config import Config
from history_maker.ricostruzione import modello


@pytest.fixture
def config(tmp_path) -> Config:
    """Una configurazione che punta a una cartella vuota."""
    config = Config(
        comune="Torrebruna",
        termine_ricerca="Torrebruna",
        includi_contesto=["Torrebruna"],
        escludi_contesto=[],
        anno_min=1809,
        anno_max=1900,
        tipologie=[],
        catalogo=tmp_path / "catalogo.json",
        immagini=tmp_path / "immagini",
        ridotte=tmp_path / "ridotte",
        trascrizioni=tmp_path / "trascrizioni",
        dataset=tmp_path / "dataset",
    )
    config.dataset.mkdir()
    return config


@pytest.fixture
def principale(config) -> Config:
    """Il database principale con la fase 4 dentro, e nient'altro."""
    conn = sqlite3.connect(affiancate.percorso_principale(config))
    conn.executescript(dataset.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno) "
        "VALUES (1, 'r', '0001.jpg', '1', 'nascita', 1840)"
    )
    conn.execute(
        "INSERT INTO persone (id, atto, ruolo, nome, cognome) "
        "VALUES (1, 1, 'neonato', 'Maria', 'Lella')"
    )
    conn.commit()
    conn.close()
    return config


def test_la_fase_4_si_legge_dal_database_della_v1(principale):
    """Le tabelle della fase 4 ricompaiono con lo stesso nome.

    E' cio' che permette a duemila righe di SQL scritte prima di questa
    separazione di continuare a funzionare senza toccarne una.
    """
    conn = affiancate.apri(principale, "v1")
    assert conn.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM atti").fetchone()[0] == 1
    assert conn.execute("SELECT nome FROM persone").fetchone()[0] == "Maria"
    conn.close()


def test_la_v1_scrive_nel_suo_file_e_non_nel_principale(principale):
    conn = affiancate.apri(principale, "v1")
    conn.executescript(genealogia.SCHEMA_SQL)
    conn.execute("INSERT INTO individui (id, nome, cognome) VALUES (1, 'Maria', 'Lella')")
    conn.commit()
    conn.close()

    # Nel database della V1 c'e'.
    conn = affiancate.apri(principale, "v1", sola_lettura=True)
    assert conn.execute("SELECT COUNT(*) FROM individui").fetchone()[0] == 1
    conn.close()

    # Nel principale no: non c'e' proprio la tabella.
    conn = affiancate.apri(principale, "v2")
    tabelle = {
        riga[0] for riga in conn.execute("SELECT name FROM sqlite_master")
    }
    assert "individui" not in tabelle
    conn.close()


def test_la_v1_non_puo_toccare_la_fase_4(principale):
    """Il motore legge la fonte e non la modifica mai.

    Non e' pignoleria: la fase 4 e' l'unica cosa che costa quota, e una
    fase 6 che potesse riscriverla renderebbe irripetibile tutto il
    resto. Vale anche mentre il motore **scrive** le proprie tabelle.
    """
    conn = affiancate.apri(principale, "v1")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM persone")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DROP TABLE IF EXISTS fase4.persone")
    conn.close()

    conn = affiancate.apri(principale, "v2", sola_lettura=True)
    assert conn.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 1
    conn.close()


def test_un_drop_nudo_non_scende_nel_database_principale(principale):
    """Il difetto che ha quasi distrutto l'archivio, in forma di test.

    SQLite risolve un nome non qualificato cercandolo prima in ``main`` e
    poi nei database attaccati. Al primo avvio di un motore ``individui``
    nel suo database non c'e' ancora — quindi un ``DROP TABLE IF EXISTS
    individui`` scendeva nel principale e buttava la ricostruzione
    dell'altro, senza sollevare niente.

    Due difese, e servono tutt'e due: la fase 4 e' attaccata in sola
    lettura, e i ``DROP`` degli schemi sono qualificati con ``main.``.
    """
    v2 = affiancate.apri(principale, "v2")
    v2.executescript(modello.SCHEMA_SQL)
    v2.execute("INSERT INTO individui (id, chiave, nome) VALUES (1, 'P1', 'Domenico')")
    v2.commit()
    v2.close()

    v1 = affiancate.apri(principale, "v1")
    # Lo schema della V1 comincia proprio con quei DROP.
    v1.executescript(genealogia.SCHEMA_SQL)
    v1.commit()
    v1.close()

    v2 = affiancate.apri(principale, "v2", sola_lettura=True)
    assert v2.execute("SELECT COUNT(*) FROM individui").fetchone()[0] == 1
    v2.close()

    # E un DROP nudo scritto a mano, senza la qualificazione, deve
    # fallire invece di riuscire in silenzio.
    v1 = affiancate.apri(principale, "v1")
    v1.execute("DROP TABLE IF EXISTS individui")   # la sua, che esiste: ok
    with pytest.raises(sqlite3.OperationalError):
        # ora nel suo database non c'e' piu': il nome scende nel
        # principale, e li' non si scrive.
        v1.execute("DROP TABLE IF EXISTS individui")
    v1.close()

    v2 = affiancate.apri(principale, "v2", sola_lettura=True)
    assert v2.execute("SELECT COUNT(*) FROM individui").fetchone()[0] == 1
    v2.close()


def test_eseguire_un_motore_non_cancella_il_lavoro_dell_altro(principale):
    """Il guaio per cui esiste questo modulo, in forma di test.

    Prima: la V2 scriveva individui e fatti, poi la V1 partiva, buttava
    'individui' e lo riempiva daccapo — e lasciava i fatti a puntare a
    identificatori che ora sono di altre persone.
    """
    # La V2 costruisce, con i suoi fatti.
    v2 = affiancate.apri(principale, "v2")
    v2.executescript(modello.SCHEMA_SQL)
    v2.execute("INSERT INTO individui (id, chiave, nome) VALUES (7, 'P7', 'Domenico')")
    v2.execute(
        "INSERT INTO fatti (individuo, tipo, grezzo, anno) VALUES (7, 'professione', 'bovaro', 1850)"
    )
    v2.commit()
    v2.close()

    # La V1 costruisce a sua volta, e rinumera i suoi individui da capo.
    v1 = affiancate.apri(principale, "v1")
    v1.executescript(genealogia.SCHEMA_SQL)
    v1.execute("INSERT INTO individui (id, nome) VALUES (7, 'Angela')")
    v1.commit()
    v1.close()

    # Il fatto della V2 punta ancora al suo Domenico, non all'Angela della V1.
    v2 = affiancate.apri(principale, "v2", sola_lettura=True)
    riga = v2.execute(
        "SELECT i.nome, f.grezzo FROM fatti f JOIN individui i ON i.id = f.individuo"
    ).fetchone()
    assert tuple(riga) == ("Domenico", "bovaro")
    v2.close()


def test_le_viste_non_sopravvivono_alla_connessione(principale):
    """Sono temporanee di proposito.

    Una vista permanente che punta a un database attaccato resta appesa
    quando l'attacco non c'e' piu', e il messaggio d'errore che ne esce
    non dice niente a chi lo legge.
    """
    conn = affiancate.apri(principale, "v1")
    conn.close()

    grezza = sqlite3.connect(affiancate.percorso(principale, "v1"))
    nomi = {riga[0] for riga in grezza.execute("SELECT name FROM sqlite_master")}
    assert "persone" not in nomi
    grezza.close()


def test_un_motore_mai_eseguito_lo_dice_invece_di_fallire(principale):
    conn = affiancate.apri(principale, "v2")
    assert affiancate.ricostruita(conn) is False
    conn.close()

    with pytest.raises(FileNotFoundError, match="mai stata eseguita"):
        affiancate.apri(principale, "v1", sola_lettura=True)


def test_il_confronto_vuole_tutt_e_due_le_ricostruzioni(principale):
    """Meglio dire cosa manca che confrontare qualcosa con il vuoto."""
    v2 = affiancate.apri(principale, "v2")
    v2.executescript(modello.SCHEMA_SQL)
    v2.execute("INSERT INTO individui (id, chiave, nome) VALUES (1, 'P1', 'Domenico')")
    v2.commit()
    v2.close()

    with pytest.raises((ValueError, FileNotFoundError), match="v1|genealogia"):
        affiancate.confronta(principale)


def test_il_confronto_mette_i_numeri_uno_accanto_all_altro(principale):
    for motore, schema, nome in (
        ("v2", modello.SCHEMA_SQL, "Domenico"),
        ("v1", genealogia.SCHEMA_SQL, "Angela"),
    ):
        conn = affiancate.apri(principale, motore)
        conn.executescript(schema)
        colonne = "(id, nome, menzioni)" if motore == "v1" else "(id, chiave, nome, menzioni)"
        valori = (1, nome, 1) if motore == "v1" else (1, "P1", nome, 1)
        conn.execute(
            f"INSERT INTO individui {colonne} VALUES "
            f"({','.join('?' * len(valori))})", valori
        )
        conn.commit()
        conn.close()

    rapporto = affiancate.confronta(principale)
    assert "V1" in rapporto and "V2" in rapporto
    assert "persone riconosciute" in rapporto
    # Le quattro categorie vanno lette insieme: se una sparisce dal
    # rapporto, torna il difetto per cui si guardava da una parte sola.
    for asse in ("impossibile", "frammentazione", "accorpamento", "sospetto"):
        assert asse in rapporto


def test_il_collegamento_conta_cio_che_regge(principale):
    """Le misure che nessun controllo puo' dare.

    Una persona spezzata in due non viola niente: si vede solo qui, come
    un collegamento che manca.
    """
    conn = affiancate.apri(principale, "v2")
    conn.executescript(modello.SCHEMA_SQL)
    # Un bambino nato nel 1840, ritrovato adulto nel 1870, con un figlio.
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, anno_nascita, nascita_origine, "
        "anno_ultimo, menzioni) VALUES (1, 'P1', 'Domenico', 1840, 'certa', 1870, 3)"
    )
    conn.execute(
        "INSERT INTO individui (id, chiave, nome, menzioni) VALUES (2, 'P2', 'Maria', 1)"
    )
    conn.execute("INSERT INTO legami (figlio, genitore, tipo) VALUES (2, 1, 'padre')")
    conn.commit()

    misure = qualita.collegamento(conn)
    assert misure["persone riconosciute"] == 2
    assert misure["che si reggono su una menzione sola"] == 1
    assert misure["nati qui e ritrovati da adulti"] == 1
    assert misure["nati qui di cui si conoscono i figli"] == 1
    conn.close()
