import json
import os
import stat
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FINTI = Path(__file__).parent / "finti"


def installa_finto_claude(tmp_path, monkeypatch, script: Path, **ambiente: str) -> Path:
    """Mette nel PATH un finto ``claude`` che esegue lo ``script`` dato.

    Il finto e' un programma Python vero, non uno script di shell, perche'
    i test devono girare anche su Windows: li' ``#!/bin/sh`` non significa
    nulla e ``shutil.which`` — quello con cui il codice cerca
    l'eseguibile — considera solo i file con un'estensione elencata in
    ``PATHEXT``. Un file senza estensione, per Windows, non e' un
    programma.

    Lo script riceve i suoi parametri dall'ambiente (``ambiente``), che il
    processo figlio eredita: passarli sulla riga di comando li mescolerebbe
    agli argomenti che il codice sotto test compone.

    Restituisce la cartella aggiunta al PATH.
    """
    for nome, valore in ambiente.items():
        monkeypatch.setenv(nome, valore)

    cartella = tmp_path / "bin"
    cartella.mkdir(exist_ok=True)
    if os.name == "nt":
        avvio = cartella / "claude.cmd"
        avvio.write_text(
            '@echo off\r\n"{}" "{}" %*\r\n'.format(sys.executable, script),
            encoding="utf-8",
        )
    else:
        avvio = cartella / "claude"
        avvio.write_text(
            '#!/bin/sh\nexec "{}" "{}" "$@"\n'.format(sys.executable, script),
            encoding="utf-8",
        )
        avvio.chmod(avvio.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{cartella}{os.pathsep}{os.environ['PATH']}")
    return cartella


@pytest.fixture
def manifest_torrebruna() -> dict:
    return json.loads((FIXTURES / "manifest_torrebruna.json").read_text(encoding="utf-8"))


@pytest.fixture
def manifest_guardiabruna() -> dict:
    return json.loads((FIXTURES / "manifest_guardiabruna.json").read_text(encoding="utf-8"))


@pytest.fixture
def html_galleria() -> str:
    return (FIXTURES / "galleria.html").read_text(encoding="utf-8")


@pytest.fixture
def conn_di_prova():
    """Un database in memoria con un atto di morte e uno di nascita."""
    import sqlite3

    from history_maker.dataset import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, data_atto) "
        "VALUES (1, 'r', '0001.jpg', '3', 'morte', 1809, '1809-05-02')"
    )
    conn.execute(
        "INSERT INTO atti (id, registro, immagine, numero_atto, tipo, anno, data_atto) "
        "VALUES (2, 'r', '0002.jpg', '4', 'nascita', 1809, '1809-06-11')"
    )
    conn.execute(
        "INSERT INTO persone (atto, ruolo, nome, cognome, cognome_origine, eta, note) "
        "VALUES (1, 'defunto', 'Noè', 'Pelliccia', 'atto', 'di maggior età', 'prova')"
    )
    conn.execute(
        "INSERT INTO persone (atto, ruolo, nome, cognome, cognome_origine) "
        "VALUES (2, 'neonato', 'Maria', 'Colella', 'padre')"
    )
    return conn
