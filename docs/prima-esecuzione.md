# Prima esecuzione: il solo 1809

Guida al giro completo su un anno, prima di lanciare tutto il secolo.
Le fasi 1 e 2 richiedono l'accesso a `antenati.cultura.gov.it` e vanno
quindi eseguite dalla tua macchina.

## Preparazione

```bash
cd History-Maker
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

npm install -g @anthropic-ai/claude-code
claude          # una volta sola, per autenticarti con l'abbonamento
```

## Il giro, comando per comando

```bash
# 1. Cerca sul portale i soli registri del 1809 (una ricerca, non 92)
python -m history_maker discover --anno 1809

# Guarda cosa ha trovato PRIMA di scaricare
python -m history_maker catalog
python -m history_maker catalog --scartati    # e perché ha escluso il resto

# 2. Scarica le immagini di quell'anno
python -m history_maker download --anno 1809 --elenca   # prima vedi cosa farebbe
python -m history_maker download --anno 1809

# 3. Trascrivi (usa l'abbonamento, non l'API)
python -m history_maker transcribe --anno 1809 --stima
python -m history_maker transcribe --anno 1809 --limite 8    # prova su 8 pagine
python -m history_maker transcribe --anno 1809               # il resto

# 4. Costruisci il database
python -m history_maker dataset

# 5. Trova le letture probabilmente sbagliate (non consuma quota)
python -m history_maker revisione

# in qualsiasi momento
python -m history_maker stato
```

## Che file ne escono

```
data/
├── catalogo.json                    ← fase 1: i registri trovati, con
│                                       manifest, anno, tipologia, contesto
├── immagini/
│   └── 1809-nati-19944535/          ← una cartella per registro
│       ├── manifest.json            ← il manifest IIIF, come prova d'origine
│       ├── 0001.jpg                 ← le pagine a piena risoluzione
│       ├── 0002.jpg
│       └── ...
├── immagini_ridotte/
│   └── 1809-nati-19944535/          ← copie a 1568 px che legge Claude Code
│       └── 0001.jpg                    (gli originali restano intatti)
├── trascrizioni/
│   └── 1809-nati-19944535/
│       ├── 0001.json                ← un JSON per pagina, con gli atti
│       └── 0002.json                   e la provenienza in `_origine`
└── dataset/
    ├── torrebruna.sqlite            ← registri, atti, persone, voci_indice
    │                                   più l'indice full-text
    ├── atti.csv
    ├── persone.csv
    ├── voci_indice.csv
    ├── sintesi.md                   ← atti per tipo e decennio, cognomi,
    │                                   mestieri, anni mancanti
    └── revisione.md                 ← fase 5: le letture da ricontrollare
```

Il nome della cartella di un registro è `<anno>-<tipologia>-<id unità>`,
così l'ordinamento alfabetico è anche cronologico.

## Una cosa da mettere in conto sul 1809

Il 1809 è il **primo anno** dello stato civile nel Regno di Napoli.
L'obbligo parte da lì, ma i registri più antichi effettivamente conservati
e versati all'Archivio di Stato variano da comune a comune: per molti
comuni della provincia di Chieti la serie comincia dal 1810 o più tardi.

Se `discover --anno 1809` non trova nulla, prima di sospettare il codice
prova ad allargare:

```bash
python -m history_maker discover --dal 1809 --al 1815
python -m history_maker catalog
```

Se anche così il catalogo resta vuoto, allora il problema è nella
raccolta dei link: vedi la sezione «Cosa non è stato verificato» in
`architettura.md` e lancia `discover --debug-html /tmp/antenati`.

## Dove finiscono i file, e perché non nel repo

Tutto sotto `data/`, che è in `.gitignore`. È voluto: un secolo di
scansioni sono diversi GB e un repository di codice non è il posto giusto
per conservarle.

Per un solo anno però potresti volerle tenere sotto controllo di versione.
In quel caso togli `data/` dal `.gitignore` — e valuta prima
[git-lfs](https://git-lfs.com/) per le immagini, perché git normale
gestisce male i binari di grandi dimensioni.

Le **trascrizioni** sono un discorso diverso: sono testo, pesano poco e
sono il vero risultato del lavoro. Quelle vale la pena versionarle sempre:

```bash
git add -f data/trascrizioni data/catalogo.json
git commit -m "Trascrizioni del 1809"
```

Le immagini si possono sempre riscaricare dal portale; una trascrizione
rifatta costa quota e non torna mai identica.
