# Lavorare in PyCharm (2025.2.4)

Il progetto si apre già configurato: le configurazioni di esecuzione sono
versionate in `.idea/runConfigurations/`, quindi le trovi nel menu a
tendina in alto a destra appena apri la cartella.

## 1. Aprire il progetto

```bash
git clone -b claude/torrebruna-archivi-scraper-6tpdlu \
  https://github.com/MarcoLeL/History-Maker.git
```

In PyCharm: **File → Open** e scegli la cartella `History-Maker`.

> Il nome della cartella conta: le configurazioni fanno riferimento a un
> modulo chiamato `History-Maker`. Se la cloni con un altro nome, PyCharm
> te lo segnala e basta riselezionare il modulo dal menu a tendina della
> configurazione.

## 2. Interprete Python

**Settings → Project: History-Maker → Python Interpreter →
Add Interpreter → Add Local Interpreter → Virtualenv Environment → New**

Lascia i valori proposti (crea `.venv/` dentro il progetto) e conferma.
Poi, nel terminale di PyCharm (**Alt+F12**, che attiva già il venv):

```bash
pip install -e ".[dev]"
```

Verifica subito con la configurazione **`test · pytest`**: devono passare
111 test. Se passano, l'ambiente è a posto.

## 3. Escludere `data/` dall'indicizzazione

Importante, altrimenti PyCharm indicizza migliaia di scansioni e diventa
lentissimo:

**tasto destro sulla cartella `data/` → Mark Directory as → Excluded**

## 4. Il plugin Claude Code

**Settings → Plugins → Marketplace →** cerca **Claude Code** e installa.
Poi riavvia l'IDE.

Serve che l'eseguibile `claude` sia nel PATH:

```bash
npm install -g @anthropic-ai/claude-code
claude          # una volta sola, per autenticarti con l'abbonamento
```

Da lì hai Claude dentro l'IDE, con la tua rete: possiamo fare il giro
anno per anno guardando insieme i file che escono.

Lo stesso `claude` è anche quello che usa la fase 3 per trascrivere,
quindi installarlo serve comunque.

## 5. Le configurazioni di esecuzione

Nel menu a tendina, nell'ordine in cui vanno usate:

| configurazione | cosa fa |
|---|---|
| `0 · stato` | a che punto è la pipeline |
| `1 · discover 1809` | cerca i registri del 1809 sul portale |
| `1b · catalog` | cosa ha trovato, e perché ha escluso il resto |
| `2 · download 1809 (elenca)` | cosa scaricherebbe, senza scaricare |
| `2b · download 1809` | scarica le immagini |
| `3 · transcribe 1809 (stima)` | invocazioni, contesto e tempo previsti |
| `3b · transcribe 1809 (prova 8 pagine)` | prova il prompt su poche pagine |
| `3c · transcribe 1809` | trascrive tutto l'anno, aspettando la quota |
| `4 · dataset` | costruisce database, CSV e sintesi |
| `5 · revisione` | segnala le letture da ricontrollare |

Per un anno diverso dal 1809 non serve creare nulla: **Edit
Configurations** e cambia `--anno 1809` nel campo *Parameters*. Oppure
duplica la configurazione, che è più comodo se lavori su più anni.

Le configurazioni delle fasi 1-3 girano con *Emulate terminal* attivo,
così l'avanzamento si legge mentre scorre invece di comparire tutto alla
fine.

## 6. Il browser di Selenium

La fase 1 apre una finestra vera di Chrome: è voluto, perché il portale è
protetto da un WAF che con un browser headless è più facile che presenti
una challenge. Se compare un CAPTCHA lo risolvi a mano una volta sola e
la raccolta prosegue.

Se Chrome non parte, in **Edit Configurations → Environment variables**
aggiungi:

```
CHROMEDRIVER=/percorso/di/chromedriver
CHROME_BINARY=/percorso/di/chrome
```

Il driver si scarica da
[chrome-for-testing](https://googlechromelabs.github.io/chrome-for-testing/),
nella stessa versione maggiore del Chrome che hai installato.

## 7. Guardare i risultati senza uscire dall'IDE

- I JSON delle trascrizioni: PyCharm li apre e li formatta da solo.
- Il database SQLite: **doppio clic su `data/dataset/torrebruna.sqlite`**.
  PyCharm Professional apre la finestra *Database* e ci si interrogano le
  tabelle direttamente. In PyCharm Community serve il plugin
  *Database Navigator*, oppure da terminale:
  ```bash
  sqlite3 data/dataset/torrebruna.sqlite \
    "SELECT anno, numero_atto, nome, cognome FROM persone p JOIN atti a ON a.id=p.atto LIMIT 20;"
  ```
- `sintesi.md` e `revisione.md`: PyCharm ha l'anteprima Markdown
  integrata (l'icona in alto a destra dell'editor).
