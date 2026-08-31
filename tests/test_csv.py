"""I CSV, che sono l'uscita per chi apre un foglio di calcolo.

Due difetti visti sul file vero del 1809: gli accenti illeggibili in
Excel, e l'impossibilita' di trovare i morti senza incrociare a mano due
tabelle.
"""

import csv

import pytest

from history_maker import dataset



def test_il_csv_ha_il_bom_per_excel(tmp_path, conn_di_prova):
    """Senza BOM Excel legge il file con la codepage di sistema e ogni
    accento diventa mojibake: 'eta'' si legge 'etA '."""
    dataset._esporta_csv(conn_di_prova, tmp_path)
    grezzo = (tmp_path / "persone.csv").read_bytes()
    assert grezzo[:3] == b"\xef\xbb\xbf"


def test_gli_accenti_sopravvivono_al_giro(tmp_path, conn_di_prova):
    dataset._esporta_csv(conn_di_prova, tmp_path)
    testo = (tmp_path / "persone.csv").read_text(encoding="utf-8-sig")
    assert "età" in testo and "Noè" in testo


def test_il_separatore_e_quello_che_excel_si_aspetta(tmp_path, conn_di_prova):
    """Excel su Windows italiano separa gli elenchi col punto e virgola:
    con la virgola mette l'intera riga in una colonna sola."""
    dataset._esporta_csv(conn_di_prova, tmp_path)
    prima = (tmp_path / "persone.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert prima.count(";") > 10


def test_il_separatore_si_puo_cambiare(tmp_path, conn_di_prova):
    dataset._esporta_csv(conn_di_prova, tmp_path, separatore=",")
    prima = (tmp_path / "persone.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert prima.count(",") > 10 and ";" not in prima


def test_i_genitori_compaiono_dove_sono_inequivocabili(tmp_path, conn_di_prova):
    """Una nascita nomina un padre solo: attribuirlo al neonato non e' una
    scelta. Un matrimonio ne nomina due e il ruolo non dice quale sia
    quale: li' il campo deve restare vuoto."""
    conn_di_prova.execute(
        "INSERT INTO persone (atto, ruolo, nome, cognome, cognome_origine) "
        "VALUES (2, 'padre', 'Giuseppe', 'Colella', 'atto')"
    )
    conn_di_prova.execute(
        "INSERT INTO persone (atto, ruolo, nome, cognome, cognome_origine) "
        "VALUES (2, 'madre', 'Fortunata', 'Moretta', 'atto')"
    )
    dataset._esporta_csv(conn_di_prova, tmp_path)
    with (tmp_path / "persone.csv").open(encoding="utf-8-sig", newline="") as handle:
        righe = list(csv.DictReader(handle, delimiter=";"))

    neonato = next(r for r in righe if r["ruolo"] == "neonato")
    assert neonato["padre"] == "Giuseppe Colella"
    assert neonato["madre"] == "Fortunata Moretta"
    # Il padre non e' figlio di se stesso.
    padre = next(r for r in righe if r["ruolo"] == "padre")
    assert not padre["padre"]


def test_due_padri_nello_stesso_atto_lasciano_il_campo_vuoto(tmp_path, conn_di_prova):
    """Meglio un buco dichiarato che una parentela inventata."""
    for nome in ("Giuseppe", "Domenico"):
        conn_di_prova.execute(
            "INSERT INTO persone (atto, ruolo, nome, cognome, cognome_origine) "
            f"VALUES (2, 'padre', '{nome}', 'Colella', 'atto')"
        )
    dataset._esporta_csv(conn_di_prova, tmp_path)
    with (tmp_path / "persone.csv").open(encoding="utf-8-sig", newline="") as handle:
        righe = list(csv.DictReader(handle, delimiter=";"))
    neonato = next(r for r in righe if r["ruolo"] == "neonato")
    assert not neonato["padre"]


def test_persone_csv_si_legge_da_solo(tmp_path, conn_di_prova):
    """'Chi sono i morti del 1809' deve essere una domanda che si fa sul
    file, non un incrocio a mano con atti.csv."""
    dataset._esporta_csv(conn_di_prova, tmp_path)
    with (tmp_path / "persone.csv").open(encoding="utf-8-sig", newline="") as handle:
        righe = list(csv.DictReader(handle, delimiter=";"))

    assert "anno" in righe[0] and "tipo_atto" in righe[0]
    morti = [r for r in righe if r["tipo_atto"] == "morte" and r["ruolo"] == "defunto"]
    assert len(morti) == 1
    assert morti[0]["anno"] == "1809"
    assert morti[0]["cognome"] == "Pelliccia"
