#!/usr/bin/env bash
# Un giro di ricostruzione con le regole nuove, senza toccare decisioni ne'
# glossario. Il server mostra una copia dell'albero per tutta la durata e
# alla fine torna sul database vero.
#
#     bash giro.sh <etichetta>          es. bash giro.sh J
#     bash giro.sh <etichetta> pieno    ricalcola anche il modello delle frequenze
#
# MISURATO (20 settembre): la ricostruzione senza cache dura 7 minuti e mezzo, e
# quattro e quaranta se ne va nel 'modello delle frequenze su 52047 menzioni' —
# le tabelle dei nomi e dei cognomi vicini. Con la cache calda la stessa
# ricostruzione dura 97 secondi.
#
# La chiave della cache comprende le correzioni ma NON il codice: se si cambia
# una regola di menzioni.py, di lettura.py o di scheda.py bisogna passare
# 'pieno', o si rimisura il vecchio. Per le sole decisioni — unioni,
# separazioni, correzioni — la cache va bene.
#
# Una differenza c'e': la ricostruzione con la cache ha dato 14687 schede
# contro 14688. Una su quattordicimila, ma prima di fidarsi di un confronto di
# qualita' conviene rifare il giro 'pieno'.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=.venv/Scripts/python.exe
ETICHETTA="${1:-J}"
PIENO="${2:-}"
ferma_server() {
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }" || true
}
PRIMA=$(ls -t "$S"/qualita_*.md | head -1)

echo "== 0. il server passa su una copia =="
ferma_server
cp data/dataset/torrebruna.sqlite data/dataset_F/torrebruna.sqlite
nohup $PY -m history_maker -c "$S/torrebruna-F.yaml" albero --senza-browser --porta 8000 > server.log 2>&1 &

if [ "$PIENO" = "pieno" ]; then
  echo "== 1. ricostruzione (piena, col modello delle frequenze) =="
  rm -f data/dataset/.cache-*.pkl
  $PY -m history_maker ricostruisci --senza-cache 2>&1 | grep -v "^$" | tail -18
else
  echo "== 1. ricostruzione (con la cache del modello) =="
  $PY -m history_maker ricostruisci 2>&1 | grep -v "^$" | tail -18
fi

echo "== 2. qualita' (contro $(basename "$PRIMA")) =="
$PY -m history_maker qualita 2>&1 | tail -2
cp data/dataset/qualita.md "$S/qualita_$ETICHETTA.md"
$PY "$S/confronta_qualita.py" "$PRIMA" "$S/qualita_$ETICHETTA.md" 2>&1 | tail -6

echo "== 3. i due problemi =="
$PY "$S/analisi_coniugi_cognomi.py" 2>&1 | head -18
$PY "$S/analisi_cognomi_atto.py" 2>&1 | head -8

echo "== 4. i casi di prima =="
$PY "$S/verifica_saba.py" 2>&1 | head -12 || true

echo "== 5. il server torna sul database vero =="
ferma_server
nohup $PY -m history_maker albero --senza-browser --porta 8000 > server.log 2>&1 &
echo "== fatto =="
