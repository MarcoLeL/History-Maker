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
| 3 | **transcribe** | Claude Code | `data/trascrizioni/` — gli atti in JSON |
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

Per la fase di trascrizione serve **Claude Code**, che usa il tuo
abbonamento Claude Pro — nessun credito API, nessun costo aggiuntivo:

```bash
npm install -g @anthropic-ai/claude-code
claude          # una volta sola, per autenticarti con l'abbonamento
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

# 3. trascrivi con Claude Code (usa l'abbonamento)
python -m history_maker transcribe --stima             # invocazioni, contesto, tempo
python -m history_maker transcribe --limite 20         # prova su 20 pagine
python -m history_maker transcribe --attendi           # tutto, aspettando il rinnovo quota

# 4. costruisci il database e la sintesi
python -m history_maker dataset
```

### Consiglio sull'ordine

Fai `discover`, guarda il catalogo, e **solo allora** scarica. Poi
trascrivi venti pagine con `--limite 20` e leggi il JSON che ne esce: è il
momento per correggere il prompt in `src/history_maker/prompt.py`, che
conosce il formulario dei tre regimi ma non conosce il tuo paese. Solo
dopo lancia il resto.

### La quota, non il denaro

Con l'abbonamento non c'è nulla da pagare a consumo, ma la quota si
esaurisce e si rinnova a finestre. Un secolo di registri sono migliaia di
pagine: il lavoro **si fermerà più volte**, ed è normale.

- `--attendi` lascia che si fermi e riprenda da solo.
- Senza `--attendi` si ferma pulito e ti dice di rilanciare più tardi.
- In entrambi i casi **ogni pagina finita è salvata subito**: rilanciare
  non rifà mai il lavoro già fatto.

Due leve se la quota finisce troppo in fretta, entrambe in
`config/torrebruna.yaml`:

- `modello: claude-sonnet-5` consuma molta meno quota di Opus e su una
  scrittura leggibile se la cava bene.
- `pagine_per_chiamata` più alto ammortizza meglio il sovraccarico (vedi
  sotto), a costo di risposte più lunghe.

### Perché le pagine vanno a gruppi

Ogni invocazione di Claude Code porta con sé il suo prompt di sistema e le
definizioni degli strumenti: **~50.000 token di impalcatura**, contro i
~2.500 di una singola immagine. Il sovraccarico è per *chiamata*, non per
*immagine*, quindi trascrivere una pagina alla volta sprecherebbe il 95%
della quota. A quattro pagine per chiamata il costo scende a ~20.000
token a pagina — misurato, non stimato.

È il prezzo di questa strada: l'API diretta costerebbe ~2.500 token a
pagina, ma richiede credito prepagato che l'abbonamento non include.

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

Claude Code non offre l'equivalente di `output_config.format` dell'API,
quindi la conformità non è garantita: lo schema è chiesto a parole nel
prompt e ogni risposta passa dal validatore in
`src/history_maker/schema.py`, che aggiunge le chiavi mancanti e scarta
quelle inattese. Se un gruppo di pagine restituisce una risposta
inutilizzabile, viene ritentato **una pagina per volta**, così una pagina
illeggibile non trascina con sé le altre tre.

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

Gli 83 test girano offline e non consumano quota: il formato del portale
è collaudato su manifest campione, la fase di download contro un finto
server IIIF locale che riproduce anche il 403 sulla sintassi
`/full/full/0/`, e la fase di trascrizione contro un finto eseguibile
`claude` programmabile — raggruppamento, riallineamento delle risposte,
ripiego a pagina singola ed esaurimento della quota compresi.
