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
| 3 | **transcribe** | Gemini (piano gratuito) | `data/trascrizioni/` — gli atti in JSON |
| 4 | **dataset** | SQLite | `data/dataset/` — database, CSV e sintesi |
| 5 | **revisione** | statistica | `revisione.md` — le letture da ricontrollare |
| 6 | **ricostruisci** | modello probabilistico | l'albero: chi è chi, e con quanta sicurezza |
| 7 | **qualita** | aritmetica | `qualita.md` — cosa nell'albero non può essere vero |

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

Per la fase di trascrizione serve una **chiave di Google AI Studio**, che
è gratuita e si prende in un minuto su
[aistudio.google.com/apikey](https://aistudio.google.com/apikey). Non va
messa in nessun file del progetto:

```bash
export GEMINI_API_KEY="la-tua-chiave"          # bash
$env:GEMINI_API_KEY = "la-tua-chiave"          # PowerShell
```

**Non c'è niente da pagare**, e il secolo intero ci sta dentro. Il conteggio
di quel che hai usato oggi sta in `data/.quota-gemini.json` e sopravvive
al riavvio, così rilanciare il comando tre volte nello stesso pomeriggio
non si prende una raffica di 429.

I limiti del piano gratuito sono **tre**, e vanno capiti tutti e tre
perché tirano in direzioni diverse:

| | cos'è | cosa lo tocca |
|---|---|---|
| **RPD** | richieste al giorno | quante pagine fai in una giornata |
| **RPM** | richieste al minuto | quanto vai veloce |
| **TPM** | **token al minuto** | quante pagine puoi mettere in una richiesta |

Il primo è quello che senti — finita la quota, si riprende domani. Il
terzo è quello che sorprende: raggruppare più pagine per chiamata fa più
pagine al giorno, ma con le facciate divise ogni pagina sono due immagini
da ~1.550 token, e il TPM arriva prima della quota giornaliera. È il
motivo per cui `pagine_per_chiamata` è 6 e non 20.

**I valori esatti non sono più pubblicati da Google**: la documentazione
rimanda alla tua pagina personale, e i limiti dipendono dal modello e dal
progetto. Guardali su
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit)
e riportali in `config/torrebruna.yaml` sotto `richieste_al_minuto` e
`richieste_al_giorno` — se il tuo RPD è più alto del default, il lavoro
si accorcia in proporzione.

### L'altra strada: Claude Code sull'abbonamento

Il progetto è nato così e la strada resta aperta: `backend: claude-code`
in `config/torrebruna.yaml`. Non serve nessuna chiave — basta installare
Claude Code e autenticarsi una volta — ma serve sapere perché non è più
il default.

```bash
npm install -g @anthropic-ai/claude-code
claude          # una volta sola, per autenticarti con l'abbonamento
```

Il problema non è la qualità della lettura, che è ottima: è il **ritmo**.
Ogni invocazione della CLI porta con sé circa 50.000 token di prompt di
sistema e definizioni di strumenti, contro i ~2.100 di un'immagine. A
dieci pagine per chiamata sono 5.000 token a pagina di pura impalcatura —
**i sette decimi della quota se ne vanno prima di guardare una pagina** —
e la quota dell'abbonamento si rinnova a finestre di ore. Misurato sul
campo: circa un anno di atti per finestra, cioè mesi per un secolo.

Anche in quel caso nessuna spesa a consumo: se sulla tua macchina è
impostata `ANTHROPIC_API_KEY` o `ANTHROPIC_AUTH_TOKEN` — l'unico modo in
cui Claude Code fatturerebbe l'API invece di usare l'abbonamento — viene
tolta dall'ambiente del processo, con un avviso nel log.

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

# 1. trova i registri di Torrebruna sul portale
python -m history_maker discover --anno 1809      # una prova su un anno
python -m history_maker discover                  # tutto il secolo (~mezz'ora)

# guarda cosa ha trovato prima di scaricare qualsiasi cosa
python -m history_maker catalog
python -m history_maker catalog --scartati   # e perché ha escluso il resto

# 2. scarica le immagini (lungo: diversi GB)
python -m history_maker download --elenca    # prima vedi cosa scaricherebbe
python -m history_maker download

# 3. trascrivi (piano gratuito di Gemini)
python -m history_maker transcribe --stima             # chiamate, giorni, quota di oggi
python -m history_maker transcribe --limite 20         # prova su 20 pagine
python -m history_maker transcribe --attendi           # tutto, aspettando il rinnovo quota

# 4. costruisci il database e la sintesi
python -m history_maker dataset

# 5. scopri dove le trascrizioni probabilmente sbagliano (non consuma quota)
python -m history_maker revisione

# 6. riconosci le persone e ricostruisci l'albero (non consuma quota)
python -m history_maker ricostruisci
python -m history_maker dubbi              # cosa il calcolo non ha saputo decidere
python -m history_maker deduci             # i genitori che nessun atto scrive

# 7. conta cio' che nell'albero non puo' essere vero
python -m history_maker qualita

# solo sui casi che restano ambigui, e solo se vuoi spenderci quota:
python -m history_maker verifica --elenca  # le domande che farebbe all'immagine
python -m history_maker arbitra --elenca   # i casi che manderebbe a un modello

# in qualunque momento: due letture delle stesse pagine, a confronto
python -m history_maker confronta data/trascrizioni data/altra-lettura
```

Per il giro completo su un anno solo, comando per comando, vedi
**[`docs/prima-esecuzione.md`](docs/prima-esecuzione.md)**.

### Consiglio sull'ordine

Fai `discover`, guarda il catalogo, e **solo allora** scarica. Poi
trascrivi venti pagine con `--limite 20` e leggi il JSON che ne esce: è il
momento per correggere il prompt in `src/history_maker/prompt.py`, che
conosce il formulario dei tre regimi ma non conosce il tuo paese. Solo
dopo lancia il resto.

### La risoluzione è il tetto della qualità

**Questa è la cosa più importante della fase 3, e non ha niente a che
vedere con la scelta del modello.**

Una scansione di questi registri è una doppia pagina orizzontale, circa
3700×2300 pixel. Ridotta intera a 1568 px di lato lungo — la misura oltre
la quale nessun modello guadagna dettaglio utile — ogni facciata arriva
al modello con **784 pixel di larghezza**. È la causa prima delle letture
sbagliate: `Femminilli` letto `Tommolilli`, `Rua di Nuorro` letta `Via di
Ricovro`, sono tutte perfettamente leggibili sull'originale.

Ritagliare la cornice nera non basta: la scansione resta orizzontale e il
lato lungo continua a decidere il fattore di riduzione. E ritagliare *una
sola* metà non si può, perché su questi registri una doppia pagina porta
spesso un atto per lato — `ritaglio.lato_scritto` restituisce `None`
apposta, perché buttare via un atto è un errore che nessuno vede.

`dividi_facciate: true` risolve entrambe le cose insieme. Ogni metà
diventa un'immagine verticale, il suo lato lungo è l'altezza, e alla
stessa riduzione la larghezza utile passa da 784 a **~1150 pixel**. Le
due metà si sovrappongono di un dito, così una parola a cavallo della
piega resta intera almeno da un lato, e al modello viene detto che sono
una pagina sola: un solo oggetto JSON per scansione, nessun atto perso.

### La quota, non il denaro

Sul piano gratuito non c'è nulla da pagare, ma le richieste al giorno
sono contate. Un secolo di registri sono migliaia di pagine: il lavoro
**si fermerà più volte**, ed è normale.

- `--attendi` lascia che si fermi e riprenda da solo.
- Senza `--attendi` si ferma pulito e ti dice di rilanciare più tardi.
- In entrambi i casi **ogni pagina finita è salvata subito**: rilanciare
  non rifà mai il lavoro già fatto.
- Un limite di *ritmo* — troppe richieste in un minuto — non ferma niente:
  dura qualche decina di secondi, e il lavoro aspetta e prosegue anche
  senza `--attendi`.

### Perché le pagine vanno a gruppi

Vale per entrambi i motori, ma per ragioni opposte, ed è la cosa da
capire prima di toccare `pagine_per_chiamata`.

Con **Claude Code** il sovraccarico è per invocazione: ogni chiamata porta
il prompt di sistema e le definizioni degli strumenti, **~50.000 token di
impalcatura** contro i ~2.100 di un'immagine. Raggruppare è l'unico modo
di non sprecare la quota — a dieci pagine per chiamata il costo scende da
50.000 a ~7.000 token a pagina, e i sette decimi restano comunque
impalcatura.

Con **Gemini** quel sovraccarico non esiste, ma il piano gratuito conta
le *richieste al giorno*. Raggruppare è l'unico modo di trascrivere
più pagine al giorno: 250 richieste da 6 pagine sono 1.500 pagine, 250 da
10 sarebbero 2.500. Il tetto pratico è il limite di token al minuto —
con le facciate divise sono due immagini da ~1.550 token per pagina — e
sei per chiamata ci stanno comode.

### Come si verifica che un motore legga bene

Non credendo a chi lo dice. `confronta` mette due cartelle di
trascrizioni delle **stesse pagine** una accanto all'altra e conta dove
divergono, distinguendo lo scambio plausibile per una mano ottocentesca
(la `ſ` lunga letta `f`) dalla lettura proprio diversa:

```bash
python -m history_maker transcribe --anno 1809 --rifai   # con il motore nuovo
python -m history_maker confronta data/trascrizioni-vecchie data/trascrizioni
```

Non dice chi ha ragione — per quello bisogna guardare la carta — ma dice
*quanto* e *dove* le due letture si discostano, e chiude con l'elenco dei
punti da verificare sull'originale. Serve per cambiare motore, ma anche
per misurare cosa cambia alzando la risoluzione o aggiungendo una voce al
glossario.

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

Con Gemini lo schema viaggia in `responseSchema` e la risposta è JSON
conforme per costruzione; con Claude Code, che non offre l'equivalente,
va chiesto a parole. In entrambi i casi ogni risposta passa comunque dal
validatore in `src/history_maker/schema.py`, che aggiunge le chiavi
mancanti e scarta quelle inattese: **un vincolo di forma non è un vincolo
di senso**, e costa zero tenerlo. Se un gruppo di pagine restituisce una
risposta inutilizzabile, viene ritentato **una pagina per volta**, così
una pagina illeggibile non trascina con sé le altre.

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

## La fase 5: trovare gli errori di lettura

Un secolo di atti di un solo comune è una fonte molto **ridondante**, e la
ridondanza si può sfruttare per scoprire gli errori senza rileggere nulla.
La fase 5 è pura aritmetica: **non chiama mai il modello, non consuma
quota**, e si può rilanciare all'infinito.

Tre controlli, in ordine di affidabilità:

**Gli indici.** I registri hanno pagine di indice che elencano i cognomi
degli atti di quell'anno: sono una *seconda lettura indipendente degli
stessi nomi*. Se gli atti dicono "Colangelo" e l'indice dice "Colangeli",
una delle due è sbagliata. È il segnale migliore, e costa zero.

**Le frequenze.** In un paese di poche migliaia di anime i cognomi sono
un centinaio e ricorrono migliaia di volte. Una forma vista una volta
sola, a un passo da una vista trecento volte, merita un'occhiata.

**Le varianti.** `Di Nardo`, `Dinardo` e `De Nardo` sono la stessa
famiglia: qui non c'è un errore di lettura ma una normalizzazione da
decidere prima di contare le persone.

La vicinanza non è una distanza di edit generica ma **pesata sulle
confusioni della mano ottocentesca**: la `ſ` lunga letta come `f`, la `m`
resa con lo stesso numero di gambe di `in`, `c`/`e`, `u`/`n`, le doppie
instabili. Scambiare `f` con `s` costa 0,3; scambiarla con `z` costa 1,0.
La tabella è in `src/history_maker/paleografia.py` ed è **il primo posto
da correggere** quando avrai visto le mani dei tuoi registri: ogni
scrivano ha le sue abitudini.

### Non corregge, segnala

Questa è la scelta di fondo. Un cognome raro può essere una lettura
errata, ma può anche essere un **forestiero vero**: una sposa di
Castiglione Messer Marino, un soldato, un prete di passaggio. Sono
esattamente i casi che raccontano i rapporti fra Torrebruna e i paesi
vicini — la mobilità matrimoniale, i legami di parentela fra comuni. Un
correttore automatico li appiattirebbe sui cognomi locali, distruggendo
il segnale più interessante che hai.

Quindi `revisione.md` propone, con il motivo e il rimando all'immagine, e
decidi tu. **Confermare una forma rara è un risultato quanto correggerla.**

Quello che questa fase *non* fa: se una grafia è oggettivamente
illeggibile, nessuna statistica la salva. Riduce gli errori sistematici e
ti dice dove guardare; non sostituisce il tuo occhio sull'originale.

---

---

## La fase 6: dalle menzioni alle persone

La tabella `persone` della fase 4 non contiene persone: contiene
**menzioni**. Un uomo che nasce nel 1820, si sposa nel 1845, ha sei figli
e muore nel 1889 compare almeno dieci volte, ogni volta come una riga
nuova, e nessuna delle dieci sa delle altre. Riconoscerle è il lavoro di
questa fase, e in un paese di poche famiglie è difficile per una ragione
precisa: **il nome non identifica nessuno.**

I Pelliccia sono 5.121 menzioni su 49.889, il primogenito porta il nome
del nonno, e «Domenico Pelliccia» sono cinque uomini diversi nello stesso
mezzo secolo. All'incontrario, la stessa persona compare come `Lella`,
`Lelli`, `Fella`, `Lallo`, perché ogni scrivano ha la sua mano.

### Gli indizi si pesano sul paese, non a tavolino

Ogni indizio vale il logaritmo del rapporto fra due probabilità: quanto è
probabile vedere quella coincidenza se sono la stessa persona, diviso
quanto è probabile vederla per caso in **questo** archivio.

    Pelliccia   log10(0,80 / 0,103)    = +0,89     indizio debole
    Genualdi    log10(0,80 / 0,00012)  = +3,8      indizio forte

Due Pelliccia non sono un indizio: sono la normalità del paese. Due
Genualdi — sei menzioni in un secolo — sono quasi certamente parenti.
Nessuno ha dovuto deciderlo: la differenza la fa la frequenza, e la
frequenza si conta.

Vale per tutto, e soprattutto per le **parentele**: «figlio di Filippo
Lella e Margherita Rossi» è raro nello stesso modo in cui è raro un
cognome, e pesa da sé quanto merita.

### Restano cinque impossibilità, non otto veti

Due atti di nascita distinti, due atti di morte distinti, comparire vivi
dopo il proprio funerale, partorire per più anni di quanti se ne abbiano
di fertili, ricoprire un ruolo prima dell'età che quel ruolo richiede.
Sono le sole cose che il mondo non consente.

Tutto il resto è graduale. Un cognome del tutto diverso costa 1,5, una
parentela in comune ne vale 2,8: **il cognome sbagliato si può
superare**, ed è il caso di Nicola Pelliccia letto `Bellucci` in un atto,
con la stessa moglie e gli stessi figli negli altri. Un'età che non torna
di dodici anni costa un punto, che un coniuge in comune ripaga tre volte.

### Si torna indietro, e resta scritto perché

Le schede si fondono **e si separano**, per quanti giri servono. Ogni
fusione lascia nella tabella `decisioni` le prove a favore, le
contraddizioni, la confidenza e la versione dell'algoritmo che l'ha
presa; ogni persona ha una chiave stabile — l'identificatore della sua
menzione più antica — così che un'annotazione fatta oggi valga ancora
domani.

### I dubbi sono la coda di lavoro, non lo scarto

Quello che il calcolo non decide diventa un'anomalia con una priorità, e
la priorità è **il dubbio moltiplicato per quante persone la risposta
sposterebbe**:

```bash
python -m history_maker dubbi --quanti 20
```

Da lì partono le due code costose, e solo da lì. `verifica` ritaglia
dall'immagine **a piena risoluzione** la banda in cui una parola dovrebbe
trovarsi e fa una domanda chiusa — «il cognome del padre è Lella o
Lelli?» — invece di ritrascrivere una pagina che è già stata letta.
`arbitra` sottopone a un modello di ragionamento i casi della fascia
grigia, con un fascicolo che contiene le prove **a favore e contro**: un
fascicolo che presenta solo l'ipotesi della fusione ottiene fusioni.

Le due code sono le uniche cose di questa fase che consumino quota, e
sono le uniche in cui spenderla cambia qualcosa.

### Le risposte tornano indietro

Una risposta non resta in una tabella: diventa una **decisione con un
autore**, e la ricostruzione successiva la applica.

```bash
python -m history_maker verifica --quante 12   # l'immagine corregge otto età
python -m history_maker arbitra  --quanti 30   # il modello unisce sedici schede
python -m history_maker ricostruisci           # e da qui in poi valgono
```

Vale anche per te:

```bash
python -m history_maker decidi unione 2396 10686 --perche "stessa moglie, stessi figli"
python -m history_maker decidi disfa 47 --perche "l'atto del 1852 dice altro"
```

Le imposizioni scavalcano il punteggio ma **non i veti**: se unire due
schede produrrebbe una donna che partorisce dopo il proprio funerale, la
fusione non si fa e resta scritto perché. E niente si cancella — tornare
indietro è una decisione nuova che nomina la vecchia.

Il ragionamento per esteso, con le misure e gli errori che ha fatto per
strada, sta in **[`docs/ricostruzione.md`](docs/ricostruzione.md)**.

---

## Configurazione

Tutto in `config/torrebruna.yaml`: intervallo di anni, tipologie,
esclusioni, ritmo delle richieste, backend e modello, e come le immagini
arrivano al modello. Per un altro comune basta copiare il file e passarlo
con `-c`.

Le opzioni della fase 3 si possono anche sovrascrivere per una singola
esecuzione, che è il modo di confrontare due strade senza toccare il
YAML fra una prova e l'altra:

```bash
python -m history_maker transcribe --backend claude-code --modello claude-opus-5
python -m history_maker transcribe --senza-testo-integrale
```

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

I test girano offline, non toccano la rete e non consumano quota. Il
formato del portale è collaudato su manifest campione; la fase di
download contro un finto server IIIF locale che riproduce anche il 403
sulla sintassi `/full/full/0/`; la fase di trascrizione due volte, contro
un finto eseguibile `claude` programmabile e contro una finta sessione
HTTP per Gemini — raggruppamento, riallineamento delle risposte, ripiego
a pagina singola ed esaurimento della quota compresi.

Tre cose hanno test propri perché sbagliano in silenzio:

- la traduzione dello schema nel dialetto di `responseSchema`, che se
  sbaglia fa fallire *ogni* chiamata con un 400;
- il conteggio delle richieste giornaliere, che deve sopravvivere al
  riavvio del processo;
- la divisione delle facciate, che se il modello la fraintende produce
  due oggetti dove ne serviva uno e manda fuori sincrono tutta la
  risposta.

La fase 5 è collaudata sul caso che l'ha originata: un cognome raro
vicino a uno frequente viene segnalato, un forestiero vero no.
