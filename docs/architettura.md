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

**Il prompt di sistema va in cache.** È identico per tutte le pagine:
con `cache_control: ephemeral` si paga per intero una volta sola invece
che migliaia.

**Lo schema JSON è passato all'API.** Con `output_config.format` la
risposta è conforme per costruzione — niente parsing difensivo, niente
riparazioni.

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

## Costi indicativi

Con Claude Opus 5 e immagini a 1568 px di lato lungo, una pagina costa
circa 2.500 token in ingresso e 1.200 in uscita. Su 4.000 pagine sono
grosso modo 170 $ con le chiamate sincrone e 85 $ con la Batch API.
`transcribe --stima` fa il conto sulle pagine effettivamente presenti.
