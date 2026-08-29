# History Maker — Torrebruna 1809-1900

Ricostruire un secolo di storia di **Torrebruna** (provincia di Chieti) a
partire dalle sue fonti primarie: gli atti di stato civile conservati
nell'Archivio di Stato di Chieti e pubblicati sul
[Portale Antenati](https://antenati.cultura.gov.it/).

La pipeline fa quattro cose, in quattro comandi separati:

| | fase | strumento | esito |
|---|---|---|---|
| 1 | **discover** | Selenium | `data/catalogo.json` — quali registri esistono |
| 2 | **download** | requests + IIIF | `data/immagini/` — le pagine digitalizzate |
| 3 | **transcribe** | Claude | `data/trascrizioni/` — gli atti in JSON |
| 4 | **dataset** | SQLite | `data/dataset/` — database, CSV e sintesi |

Ogni fase legge l'esito della precedente e può essere rilanciata da sola.
Tutte riprendono da dove si erano interrotte.

---

## Perché Selenium solo nella fase 1

Le pagine di `antenati.cultura.gov.it` stanno dietro un **WAF di AWS** che
risponde alle richieste automatiche con una challenge JavaScript (HTTP 202
e header `x-amzn-waf-action: challenge`). Un browser vero la risolve da
solo, `requests` no.

Gli endpoint che servono i manifest IIIF e le immagini
(`dam-antenati.cultura.gov.it`, `iiif-antenati.cultura.gov.it`) **non**
sono protetti. Quindi il browser serve solo per navigare la ricerca e
leggere l'URL del manifest da ogni galleria; lo scaricamento vero e
proprio — che è il grosso del lavoro — va su HTTP semplice, parallelo e
molto più veloce.

## Come si distingue Torrebruna da Guardiabruna

Guardiabruna è oggi una frazione di Torrebruna, ma **fino al 1928 era
comune autonomo** e teneva registri di stato civile propri. Il portale la
restituisce nella stessa ricerca, e i suoi atti sono un fondo distinto.

Il filtro non guarda l'HTML della pagina dei risultati, che cambia con la
grafica del sito. Guarda il campo `Contesto archivistico` del **manifest
IIIF**, che ha forma `Archivio di Stato di Chieti/Stato civile
italiano/Torrebruna` — una fonte strutturata e stabile. L'esclusione ha
la precedenza sull'inclusione, così un contesto come
`Torrebruna/Guardiabruna` viene comunque scartato.
Lo stesso campo dà anno (`Titolo`) e tipologia (`Tipologia`), che reggono
il filtro 1809-1900.

---

## Installazione

Serve Python 3.10 o successivo e Google Chrome (o Chromium).

```bash
git clone <questo repo> && cd History-Maker
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Per la fase di trascrizione serve una chiave API da
[console.anthropic.com](https://console.anthropic.com/):

```bash
cp .env.example .env      # poi apri .env e incolla la chiave
export ANTHROPIC_API_KEY=sk-ant-...
```

### Se il browser non parte

Selenium scarica da solo il chromedriver giusto, ma ha bisogno di
internet e la versione deve combaciare con quella di Chrome installato.
Se fallisce, prendi il driver da
[chrome-for-testing](https://googlechromelabs.github.io/chrome-for-testing/)
e indicalo a mano:

```bash
export CHROMEDRIVER=/percorso/di/chromedriver
export CHROME_BINARY=/percorso/di/chrome   # solo se non è nel PATH
```

---

## Uso

```bash
# a che punto siamo
python -m history_maker stato

# 1. trova i registri di Torrebruna sul portale (~mezz'ora: una ricerca per anno)
python -m history_maker discover

# guarda cosa ha trovato prima di scaricare qualsiasi cosa
python -m history_maker catalog
python -m history_maker catalog --scartati   # e perché ha escluso il resto

# 2. scarica le immagini (lungo: diversi GB)
python -m history_maker download --elenca    # prima vedi cosa scaricherebbe
python -m history_maker download

# 3. trascrivi con Claude
python -m history_maker transcribe --stima             # quanto costerebbe
python -m history_maker transcribe --limite 20         # prova su 20 pagine
python -m history_maker transcribe --batch             # tutto, a metà prezzo
python -m history_maker transcribe --raccogli          # ritira i risultati

# 4. costruisci il database e la sintesi
python -m history_maker dataset
```

### Consiglio sull'ordine

Fai `discover`, guarda il catalogo, e **solo allora** scarica. Poi
trascrivi venti pagine con `--limite 20`, leggi il JSON che ne esce, e se
il prompt ti convince lancia il lotto completo. Un secolo di registri sono
migliaia di pagine: sbagliare prompt e accorgersene alla fine costa.

### Perché `--batch`

La [Batch API](https://docs.anthropic.com/en/docs/build-with-claude/batch-processing)
costa **la metà** e accetta fino a 100.000 richieste per lotto. I risultati
arrivano di norma entro un'ora, al massimo entro 24. Per migliaia di
pagine è il percorso giusto; le chiamate sincrone servono per provare.

---

## Cosa esce dalla fase 3

Un JSON per pagina, conforme allo schema in `src/history_maker/schema.py`:

```json
{
  "tipo_pagina": "atti",
  "atti": [{
    "numero_atto": "17",
    "tipo": "nascita",
    "data_atto": "1866-03-27",
    "luogo": "Torrebruna",
    "persone": [
      {"ruolo": "neonato", "nome": "Maria", "cognome": "Di Nardo", "eta": null,
       "professione": null, "residenza": "Torrebruna", "stato_vitale": null, "note": null},
      {"ruolo": "padre", "nome": "Giuseppe", "cognome": "Di Nardo", "eta": "trentadue",
       "professione": "contadino", "residenza": "Torrebruna", "stato_vitale": "vivente", "note": null}
    ],
    "testo_integrale": "L'anno milleottocentosessantasei, addì ventisette di marzo…",
    "parti_illeggibili": ["il cognome della levatrice"],
    "affidabilita": "alta"
  }]
}
```

Lo schema è passato all'API come `output_config.format`, quindi la risposta
è JSON valido e conforme per costruzione: non c'è nulla da riparare.

**Ogni campo può essere `null`.** Il prompt vieta esplicitamente di
inventare: un dato illeggibile resta vuoto e finisce in
`parti_illeggibili`. Per una ricerca storica un buco dichiarato vale
infinitamente più di un dato plausibile ma falso. Gli atti marcati
`affidabilita: "bassa"` vanno riletti sull'immagine prima di usarli — le
immagini restano tutte su disco proprio per questo.

## Cosa esce dalla fase 4

- `torrebruna.sqlite` — tabelle `registri`, `atti`, `persone` più un
  indice full-text sul testo degli atti:
  ```sql
  SELECT a.anno, a.numero_atto, p.nome, p.cognome, p.professione
  FROM persone p JOIN atti a ON a.id = p.atto
  WHERE p.cognome = 'Di Nardo' AND a.anno BETWEEN 1840 AND 1860
  ORDER BY a.anno;
  ```
- `atti.csv`, `persone.csv` — per un foglio di calcolo.
- `sintesi.md` — atti per tipo e per decennio, **gli anni mancanti dalla
  serie**, i cognomi e i mestieri più frequenti, la qualità delle
  trascrizioni.

---

## Configurazione

Tutto in `config/torrebruna.yaml`: intervallo di anni, tipologie,
esclusioni, ritmo delle richieste, modello e dimensioni delle immagini.
Per un altro comune basta copiare il file e passarlo con `-c`.

## Cortesia verso il portale

Antenati è un servizio pubblico gratuito. I default sono volutamente
prudenti: 3 download in parallelo, 0,4 secondi di pausa fra le richieste,
ritentativi con backoff crescente. Alzarli fa risparmiare poco tempo e
carica un'infrastruttura che non è tua. Le immagini sono in pubblico
dominio, ma la [nota legale del portale](https://antenati.cultura.gov.it/note-legali/)
resta la fonte da consultare per l'uso che ne farai.

## Test

```bash
pip install -e ".[dev]" && pytest
```

I test girano offline: il formato del portale è collaudato su manifest
campione e la fase di download contro un finto server IIIF locale che
riproduce anche il 403 sulla sintassi `/full/full/0/`.
