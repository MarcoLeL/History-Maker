# Note tecniche

Appunti su cosa è stato verificato, cosa è stato dedotto e dove il codice
potrebbe dover cambiare.

## Formato del Portale Antenati

Verificato su manifest campione e riprodotto nei test.

**URL di galleria**
```
https://antenati.cultura.gov.it/ark:/12657/an_ua19944535/w9DWR8x
                                     ^^^^^ ^^^^^^^^^^^^  ^^^^^^^
                                     NAAN  unità arch.   codice immagine
```

**Manifest** — la pagina di galleria lo assegna a una variabile JavaScript:
```html
<script>var manifestId = 'https://dam-antenati.cultura.gov.it/antenati/containers/…/manifest';</script>
```
`iiif.estrai_manifest_url` cerca quella variabile e, se non c'è, ripiega
sul link visibile «IIIF manifest» in fondo al pannello di sinistra.

**Struttura** — IIIF Presentation 2.1:
```
manifest.metadata[]            {label, value}, etichette in italiano
  "Contesto archivistico"      …/Stato civile italiano/Torrebruna
  "Titolo"                     l'anno del registro
  "Tipologia"                  Nati | Morti | Matrimoni | Pubblicazioni | …
manifest.sequences[0].canvases[]
  canvas.label                 numero di pagina ("0001")
  canvas.images[0].resource["@id"]
                               …/iiif/2/<id>/full/full/0/default.jpg
```

**Dimensione delle immagini** — il manifest dichiara `/full/full/0/`, che
il server oggi rifiuta con 403, come pure `/full/max/0/`. Per la piena
risoluzione serve `/full/pct:100/0/`; per un riquadro,
`/full/!<lato>,<lato>/0/`. È l'unico dettaglio del formato che è cambiato
di recente e il primo da controllare se i download iniziano a fallire.

**Ricerca** — `search-registry/` accetta `localita` e `anno`:
```
https://antenati.cultura.gov.it/search-registry/?localita=Torrebruna&anno=1866
```

## Scelte di progetto

**Una ricerca per anno invece della paginazione.** Interrogare
`?localita=Torrebruna&anno=1866` per ciascuno dei 92 anni restituisce
insiemi piccoli e aggira del tutto la paginazione, che è la parte più
fragile da automatizzare in un sito che può cambiare grafica. Una passata
finale senza `anno` raccoglie i registri il cui anno non è indicizzato
come ci si aspetta; il catalogo deduplica per URL ark.

**Dall'HTML si prendono solo i link `ark:`.** Sono la cosa più stabile che
la pagina contenga. Titolo, tipologia, anno e comune vengono invece dal
manifest. Se il portale cambia grafica si rompe al massimo la raccolta dei
link, non il filtro né i nomi delle cartelle.

**Il catalogo è un JSON leggibile a mano.** Se la scoperta automatica
sbaglia — salta un registro, ne prende uno di troppo — si corregge con un
editor e le fasi successive lo rispettano. `discover` è idempotente e non
sovrascrive le correzioni.

**Download ripartibile.** Ogni file viene scritto come `.part` e
rinominato solo a scaricamento finito, così un Ctrl-C non lascia file
troncati che al rilancio sembrerebbero completi.

**Chi trascrive sta dietro un'interfaccia.** `backend.py` separa *cosa* si
chiede a un modello da *chi* glielo chiede: un backend riceve una
`Richiesta` — prompt di sistema, istruzione, immagini nell'ordine in cui
l'istruzione le nomina — e restituisce una `Risposta`. `transcribe.py`
non sa quale motore stia girando. I due che esistono differiscono su tre
punti, e sono i tre che l'interfaccia lascia decidere a loro: come
arrivano le immagini (dal disco con `Read`, o allegate alla richiesta),
se lo schema si può imporre, e come finisce la quota.

**Il default è Gemini sul piano gratuito, e la ragione è il ritmo.** Con
Claude Code il sovraccarico è ~50.000 token per *chiamata* — prompt di
sistema e definizioni degli strumenti — contro ~2.100 per immagine: a
dieci pagine per chiamata i sette decimi della quota se ne vanno in
impalcatura, e la quota dell'abbonamento si rinnova a finestre di ore.
Misurato: circa un anno di atti per finestra, cioè mesi per un secolo.
Con Gemini quel sovraccarico non esiste e il piano gratuito dà 250
richieste al giorno, cioè ~1.500 pagine.

**Le pagine vanno a gruppi comunque, ma per ragioni opposte.** Con Claude
Code per ammortizzare l'impalcatura; con Gemini perché il piano gratuito
conta le *richieste al giorno*, e il numero di pagine per chiamata è
letteralmente il numero di pagine al giorno. Il tetto è il limite di
token al minuto. È la ragione per cui `transcribe` non è un semplice
ciclo.

**La risoluzione è il tetto della qualità, e si decide prima del
modello.** Una scansione è una doppia pagina orizzontale ~3700×2300:
ridotta intera a 1568 px di lato lungo, ogni facciata arriva al modello
con 784 px di larghezza. `prepara_immagini` la divide in due facciate
verticali con un dito di sovrapposizione — la larghezza utile sale a
~1150 px — e il prompt dice al modello che sono una pagina sola.
Ritagliare la sola metà scritta non si può: su questi registri una doppia
pagina porta spesso un atto per lato, e `ritaglio.lato_scritto`
restituisce `None` apposta.

**La risposta va validata comunque.** Gemini accetta un `responseSchema`
e la conformità è per costruzione; Claude Code no, e il testo torna quasi
sempre dentro un blocco markdown (verificato). `backend.estrai_json` lo
ripulisce cercando il primo valore JSON bilanciato, e
`schema.valida_pagina` normalizza la struttura: un vincolo di forma non è
un vincolo di senso, e tenerlo costa zero. Un gruppo che non si lascia
interpretare viene ritentato una pagina per volta, per isolare quella
problematica.

**L'esaurimento della quota non è un errore.** Va distinto da una pagina
illeggibile: `LimiteUsoRaggiunto` interrompe il ciclo, e poiché ogni
pagina finita è già su disco, rilanciare riprende esattamente da lì. Va
distinto anche da un limite di *ritmo*, che dura qualche decina di
secondi e viene semplicemente atteso: il servizio dice lui quanto
(`retryDelay`), e fermare per questo un lavoro di migliaia di pagine
sarebbe assurdo.

**Il confronto sostituisce la fiducia.** `confronto.py` mette due letture
delle stesse pagine una accanto all'altra e conta dove divergono,
distinguendo lo scambio plausibile per la mano ottocentesca dalla lettura
proprio diversa. Non dice chi ha ragione — per quello serve la carta — ma
è il modo di decidere se cambiare motore, alzare la risoluzione o
aggiungere una voce al glossario abbia davvero cambiato qualcosa.

## Cosa non è stato verificato

Il DOM della pagina dei risultati. L'ambiente in cui questo codice è stato
scritto non ha accesso a `antenati.cultura.gov.it`, quindi il formato dei
manifest è collaudato su campioni ma **la struttura della pagina di
ricerca è dedotta**. La raccolta dei link è scritta per essere robusta
(prende ogni `a[href*="/ark:/12657/"]` e in più cerca gli ark nel markup
grezzo), ma è il punto da controllare per primo.

Se `discover` non trova nulla:

```bash
python -m history_maker discover --debug-html /tmp/antenati
```

salva l'HTML di ogni pagina di ricerca. Se dentro non ci sono `ark:/12657`,
i risultati arrivano da una chiamata XHR successiva al caricamento: in quel
caso alza la pausa in `config/torrebruna.yaml` (`rete.pausa_tra_richieste_s`)
o aggiungi un'attesa esplicita in `discover._link_ark`. Se l'HTML è quasi
vuoto, è il WAF: lancia senza `--headless`, così puoi risolvere la
challenge a mano nella finestra una volta sola.

## Consumo indicativo

Nessuno dei due motori si misura in euro, ma si misurano in cose diverse.

**Claude Code**, misurato su invocazioni reali con immagini a 1568 px:

| | token di contesto |
|---|---|
| una pagina per chiamata | ~50.000 a pagina |
| dieci pagine per chiamata | ~7.000 a pagina |
| *di cui impalcatura* | *~5.000 a pagina* |

**Gemini**, sul piano gratuito del `flash` corrente:

| | |
|---|---|
| richieste al giorno | 250 |
| pagine per richiesta | 6 (con le facciate divise) |
| **pagine al giorno** | **1.500** |
| token per pagina | ~3.100 in ingresso, ~1.500 in uscita |

Misurato sulle prime 86 pagine: ~1.540 token prodotti a pagina, di cui un
terzo è `testo_integrale`. Spegnerlo (`testo_integrale: false`) serve a
chi punta all'albero genealogico e non alla ricerca storica, e fa entrare
più pagine in ogni chiamata.

`transcribe --stima` fa il conto sulle pagine effettivamente presenti e
dice anche quante richieste restano oggi. Su migliaia di pagine il lavoro
si fermerà più volte — `--attendi` lo lascia proseguire da solo.
