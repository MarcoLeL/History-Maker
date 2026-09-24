# La coda di rilettura

Gli strumenti con cui si lavora a mano sull'albero: la coda delle righe da
rileggere sull'immagine, il registro delle decisioni prese guardandola, e il
giro che ricostruisce e misura. Stavano nella cartella di lavoro di una
sessione; sono qui perche' il lavoro possa continuare altrove.

Si lanciano **dalla radice del progetto**, non da questa cartella.

## Il giro

    python strumenti/coda/rari_lotto.py 8        # le prossime pagine da guardare
    python strumenti/coda/pagina.py 992 820 280 1600 800   # l'immagine, o un suo ritaglio
    python strumenti/coda/reg.py < lotto.txt     # registra le letture fatte a occhio
    python strumenti/coda/applica_casi.py        # le mette nel registro del database
    bash strumenti/coda/giro.sh FS               # ricostruisce e misura

`giro.sh` vuole `pieno` come secondo argomento **solo** se si e' toccato il
codice: la cache della ricostruzione comprende le correzioni ma non i moduli.

## I file

| file | cos'e' |
|---|---|
| `verdetti_casi.json` | il registro delle decisioni prese sull'immagine: unioni, separazioni, correzioni, ognuna col suo motivo. E' il lavoro accumulato, l'unico file che non si rifa' da solo |
| `rari.json` | la coda: una voce per pagina, con le righe segnalate e il perche' |
| `rari_fatti.json` | le pagine gia' guardate |
| `rari_manifesto.py` | ricostruisce la coda dal database (ogni tanto, dopo un'ondata di correzioni) |
| `rari_lotto.py` | pesca le prossime pagine non lette e ne salva le immagini in `lotto/` |
| `reg.py` | scrive nel registro le righe `P` (pagina guardata), `C` (correzione), `U` (unione), `S` (separazione) |
| `applica_casi.py` | porta il registro dentro il database, senza ripetere cio' che c'e' gia' |
| `giro.sh` | ricostruzione + qualita' + i due controlli, col server su una copia per tutta la durata |
| `ps.py`, `cerca.py`, `pagina.py` | guardare una scheda, cercare una persona, aprire l'immagine (intera o ritagliata) |
| `scarica_pagina.py` | scarica dal portale la singola pagina che manca, seguendo il manifest IIIF del registro |
| `manifest/` | i manifest IIIF dei 338 registri: dentro c'e' l'indirizzo di ogni pagina |
| `analisi_coniugi_cognomi.py` | i coniugi multipli e i figli col cognome lontano da quello del padre |
| `analisi_cognomi_atto.py` | i figli col cognome diverso dal padre **nello stesso atto**; vuole che il precedente sia stato lanciato prima (legge il suo json) |
| `confronta_qualita.py` | mette due rapporti di qualita' uno accanto all'altro |

## Cosa serve che non e' nel repository

`/data/` e' escluso da git. Mancano due cose, e si rimediano in modo diverso.

**Le immagini** (`data/immagini/`, 6294 pagine per quasi cinque giga) non
servono tutte: un giro di coda ne guarda otto o dieci. `scarica_pagina.py`
prende dal manifest IIIF l'indirizzo della pagina che serve e se la porta
giu' da solo — `pagina.py` e `rari_lotto.py` lo chiamano quando il file non
c'e', quindi il giro funziona anche su un clone vuoto: la prima lettura di
ogni pagina costa un secondo di rete, le altre niente. Le pagine scaricate
restano in `data/immagini/`, fuori da git.

**Il database** (`data/dataset/torrebruna.sqlite`, ottanta mega) invece
serve intero, e senza non si ricostruisce niente. Si rifa' dalle
trascrizioni (`data/trascrizioni/`, quarantadue mega) con la fase
`dataset`, che rimette dentro anche il registro delle decisioni.
