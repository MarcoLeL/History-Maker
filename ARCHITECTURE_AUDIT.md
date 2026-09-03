# Audit dell'architettura

Stato del repository al 3 settembre 2026, ramo
`claude/torrebruna-archivi-scraper-6tpdlu`. Tutti i numeri di questo
documento sono letti dal codice e dal database, non stimati.

---

## 1. In breve: la V2 esiste già

Il compito assegnato chiede di progettare una V2 che separi
`DOCUMENT → MENTION → FACT → IDENTITY → PERSON → RELATIONSHIPS → TIMELINE
→ RECONCILED GRAPH`, che pesi le evidenze invece di applicare soglie
fisse, che conservi la provenance, che non forzi le decisioni, che tenga
una coda di revisione e un audit trail reversibile.

**Quel sistema è già stato costruito.** Sta in
`src/history_maker/ricostruzione/` (15 moduli, 5.937 righe), è entrato in
funzione fra il 1º e il 3 settembre, ha 533 test che passano, ed è
documentato in `docs/ricostruzione.md` con le misure di ciò che ha
migliorato e di ciò che ha peggiorato. La catena che il prompt disegna è
scritta parola per parola nel docstring di
[modello.py](src/history_maker/ricostruzione/modello.py:1).

Riscrivere da zero distruggerebbe un sistema **misurato**: non solo il
codice, ma il registro di cosa è stato provato e ha fallito — l'a priori
a −1,3 che produceva 2.129 fatti impossibili, le divisioni automatiche
che alzavano le frammentazioni da 180 a 217, l'aggiustamento delle stime
che spostava le contraddizioni invece di risolverle. Quel registro vale
più del codice.

Quindi questo audit non propone una V2. Propone il **delta**: cosa dei 36
punti del prompt è già soddisfatto, cosa lo è a metà, cosa manca davvero.
La risposta breve è: **26 requisiti su 36 sono soddisfatti, 6 in parte,
4 mancano**. I quattro che mancano sono elencati al §9 e sono lavoro
vero, non rifiniture.

C'è però un problema più urgente di tutti e quattro, e viene prima:
**l'intera fase 6 non è sotto controllo di versione** (§7.1).

---

## 2. Come si esegue oggi

Entry point unico: `python -m history_maker <comando>`
([cli.py](src/history_maker/cli.py:24)). Nove fasi, due delle quali
alternative fra loro.

| # | comando | modulo | cosa fa | costa quota |
|---|---|---|---|---|
| 1 | `discover` | `discover.py` | trova i registri sul Portale Antenati | no |
| 2 | `download` | `download.py`, `iiif.py` | scarica le immagini IIIF | no |
| 3 | `transcribe` | `transcribe.py`, `gemini.py`, `claudecode.py` | trascrizione paleografica | **sì** |
| 4 | `dataset` | `dataset.py`, `ricuci.py` | JSON → SQLite + CSV | no |
| 5 | `revisione` | `revisione.py` | segnala letture sospette per frequenza | no |
| 6 | `genealogia` | `identita.py`, `famiglie.py`, `coerenza.py` | **V1**: pesi fissi e veti | no |
| 6b | `ricostruisci` | `ricostruzione/` | **V2**: log-verosimiglianza | no |
| 6d | `verifica` | `ricostruzione/verifica.py` | domande chiuse all'immagine | **sì** |
| 6e | `arbitra` | `ricostruzione/arbitro.py` | casi grigi a un modello di ragionamento | **sì** |
| 7 | `qualita` | `qualita.py` | conta ciò che non può essere vero | no |
| — | `albero` | `app/`, `albero.py` | server locale di navigazione | no |
| — | `dubbi`, `decidi` | `ricostruzione/registro.py` | coda dei dubbi, decisioni umane | no |

Stato attuale del database (`data/dataset/torrebruna.sqlite`, 133 MB):

```
329 registri, 1809-1900     7.419 atti          49.889 menzioni
14.108 individui           10.391 legami         3.425 unioni
319.112 fatti              14.528 anomalie     134.100 decisioni
```

Di cui 9.170 individui (65%) si reggono su una menzione sola, e 12.321
sono in stato `confermato`, 1.058 `probabile`, 729 `possibile`.

---

## 3. Il flusso dei dati

```
Portale Antenati ──discover──► catalogo.json
                                     │
                               download (IIIF)
                                     ▼
                          data/immagini/*.jpg  ◄──────────────┐
                                     │                        │
                             transcribe (Gemini)              │ ritaglio
                                     ▼                    a piena risoluzione
                       data/trascrizioni/*.json               │
                                     │                        │
                                 dataset                      │
                                     ▼                        │
              ┌──── atti ─── persone ─── voci_indice ────┐    │
              │        (la fase 4: MAI riscritta)        │    │
              └────────────────────┬─────────────────────┘    │
                                   │                          │
                   ┌───────────────┴──────────────┐           │
                   ▼                              ▼           │
            genealogia (V1)              lettura.carica (V2)  │
            pesi fissi + veti                     │           │
                   │                     Corpus + Vocabolari  │
                   │                              ▼           │
                   │                    Modello.dal_corpus    │
                   │                    (frequenze → pesi)    │
                   │                              ▼           │
                   │                    candidati → evidenza  │
                   │                    → risoluzione → veti  │
                   │                              ▼           │
                   │                    riconcilia ⇄ separa   │
                   │                      (fino a 12 giri)    │
                   │                              ▼           │
                   │                      aggiorna_grafo      │
                   │                              ▼           │
                   └──────────► individui, menzioni, legami, unioni
                                  fatti, anomalie ◄────┐      │
                                        │              │      │
                                    dubbi (coda)       │      │
                                   ┌────┴────┐         │      │
                                   ▼         ▼         │      │
                              arbitra    verifica ─────┼──────┘
                             (Claude)   (Gemini img)   │
                                   │         │         │
                                   └────►decisioni◄────┘
                                        (si accumula,
                                     riapplicata al giro dopo)
```

Due proprietà importanti e già rispettate:

* **la fase 4 non viene mai riscritta.** `dataset.py` usa
  `CREATE TABLE IF NOT EXISTS`; la fase 6 costruisce solo *sopra*. La
  trascrizione originale resta intatta, come chiede il §32 del prompt.
* **il ciclo si chiude.** Le decisioni prese da Claude, da Gemini
  sull'immagine o da una persona vengono riapplicate alla ricostruzione
  successiva ([registro.imposizioni](src/history_maker/ricostruzione/registro.py:125),
  [registro.correzioni](src/history_maker/ricostruzione/registro.py:172)),
  e quelle dell'algoritmo **no** — perché ricongelare una conclusione che
  i dati nuovi potrebbero smentire sarebbe l'errore opposto.

---

## 4. I moduli e le responsabilità

Il §31 del prompt elenca 19 responsabilità separate. La mappa sul
repository:

| responsabilità richiesta | dove sta oggi |
|---|---|
| `document_loader` | `discover.py`, `catalogo.py`, `iiif.py` |
| `image_manager` | `download.py`, `ritaglio.py` |
| `transcription_manager` | `transcribe.py`, `prompt.py`, `schema.py` |
| `gemini_reader` | `gemini.py`, `backend.py`, `claudecode.py` |
| `context_builder` | `ricostruzione/contesto.py` — **solo per l'arbitro** |
| `evidence_extractor` | `ricostruzione/lettura.py` (`fatti_dalla_menzione`) |
| `normalizer` | `normalizza.py`, `nomi.py`, `glossario.py`, `ricostruzione/attributi.py` |
| `candidate_generator` | `ricostruzione/candidati.py` (6 strategie) |
| `identity_resolver` | `ricostruzione/risoluzione.py` |
| `relationship_resolver` | `ricostruzione/esecuzione.py` (`_scrivi_legami`, `_scrivi_unioni`) |
| `conflict_detector` | `ricostruzione/anomalie.py`, `qualita.py` |
| `duplicate_detector` | `anomalie.duplicati_probabili` |
| `graph_reconciler` | `risoluzione.riconcilia` / `separa` |
| `review_queue` | `anomalie.coda`, tabella `verifiche` |
| `claude_reasoner` | `ricostruzione/arbitro.py` |
| `cache_manager` | `ricostruzione/cache.py` |
| `quota_manager` | `gemini.Ritmo` |
| `audit_logger` | `ricostruzione/registro.py` |
| `migration_manager` | **assente** (vedi §7.2) |

Diciotto su diciannove. La separazione richiesta esiste, con nomi
italiani invece che inglesi.

---

## 5. Le dipendenze: V1 e V2 non sono separabili

Il prompt assume che la V1 sia una baseline da tenere accanto alla V2.
Nei fatti la V2 **dipende** dalla V1:

```
ricostruzione/lettura.py   → identita.Menzione, identita.carica_menzioni,
ricostruzione/scheda.py      identita.ChiaviFamiliari, identita.GENITORI,
ricostruzione/evidenza.py    identita._chiavi_vicine   ← funzione privata
ricostruzione/risoluzione.py nomi.Bilancia, nomi.Genere, nomi.analizza_eta …
```

Sono **cinque simboli su 2.250 righe** di `identita.py`. Il modulo
contiene due cose molto diverse:

* **la lettura della famiglia dentro l'atto** — «Maria, figlia di
  Giuseppe e di Calideta» non è un'inferenza, è scritto. È il pezzo che
  la V2 usa, ed è il pezzo di valore.
* **il riconoscimento a pesi fissi e veti** — il motore che la V2
  sostituisce, e che la V2 non chiama mai.

Il secondo è morto ma tiene in vita il primo. Finché non sono separati,
«tenere la V1 come baseline» significa tenere in vita anche il codice
obsoleto, e nessuno può toccare la lettura degli atti senza rischiare di
rompere il matcher che nessuno usa più.

---

## 6. Copertura del prompt, requisito per requisito

Legenda: ✅ fatto · ◐ parziale · ❌ assente

| § | requisito | | evidenza nel codice |
|---|---|---|---|
| 3 | catena DOCUMENT→…→GRAPH | ✅ | `modello.py:1-40`, la catena è il docstring |
| 4 | gerarchia delle fonti (L1 immagine … L5 grafo) | ✅ | `Valore.grezzo` mai sovrascritto; `verifica.py` torna all'immagine |
| 5 | il vecchio albero preservato | ◐ | la fase 4 è intatta; ma V1 e V2 si sovrascrivono a vicenda (§7.2) |
| 6 | Pass A: lettura context-blind | ✅ | `prompt.py` non contiene nessun dato genealogico |
| 7 | Pass B: verifica context-aware | ◐ | esiste come **domanda chiusa su un ritaglio**, non come rilettura del documento (§9.1) |
| 8 | ContextBuilder pertinente | ◐ | esiste per l'arbitro (`contesto.fascicolo`), non per documento (§9.1) |
| 8 | TUTTI i coniugi, TUTTI i figli | ✅ | `contesto._parenti` li elenca tutti (troncati a 8) |
| 9 | sezione `possible_errors` | ◐ | le anomalie esistono come tabella; il fascicolo ne porta **una sola** (§9.2) |
| 10 | evidenza strutturata per campo | ✅ | `Valore(grezzo/normalizzato/interpretato/confidenza/stato/motivo)` |
| 10 | stati CONFERMATO…INSUFFICIENT | ✅ | `modello.py:56-73`, incluso `POSSIBILE_OMONIMO` |
| 11 | nomi: niente string matching | ✅ | `paleografia.py` pesa la confondibilità di due grafie ottocentesche |
| 11 | raw/normalized/variants/source/confidence | ✅ | `Valore` + `varianti_nome`/`varianti_cognome` |
| 12 | attributi come serie temporali | ✅ | `attributi.Serie`; 38.449 fatti `professione` datati |
| 13 | identity resolution a candidati | ✅ | `candidati.tutte`: 6 strategie di generazione |
| 14 | evidenze gerarchizzate, no soglia unica | ✅ | pesi in ban misurati sul corpus, `Modello.dal_corpus` |
| 15 | secondi matrimoni | ✅ | `evidenza.tasso_seconde_nozze` misura 1 su 234 dal corpus |
| 16 | figli omonimi | ✅ | `test_i_gemelli_omonimi_restano_due`; `qualita.figli_omonimi_vivi` |
| 17 | riconciliazione globale | ✅ | `risoluzione.riconcilia`/`separa`, fino a 12 giri |
| 18 | merge non distruttivo e reversibile | ✅ | `Decisione.disfa`; `python -m history_maker decidi disfa <n>` |
| 19 | oggetti conflitto espliciti | ◐ | tabella `anomalie` con tipo/gravità/stato; mancano `DOCUMENT_CONFLICT` e i conflitti fra attributi datati |
| 20 | review queue con priorità | ✅ | `Anomalia.priorita` = dubbio × impatto × gravità |
| 21 | Gemini legge, Claude ragiona | ✅ | `verifica.py` (Gemini/immagine) vs `arbitro.py` (ragionamento) |
| 22 | cache, retry, quota, checkpoint, resume | ✅ | `gemini.Ritmo`, `cache.Deposito`, `transcribe --rifai` |
| 22 | quote configurabili, non hardcoded | ✅ | `config.py:64-65` `richieste_al_minuto`, `richieste_al_giorno` |
| 23 | chiave di cache completa | ◐ | atto+campo+domanda+alternative+modello+versione_prompt; **manca l'hash dell'immagine** |
| 24 | audit trail spiegabile | ✅ | `decisioni` con evidenze, contraddizioni, decisore, modello, versione |
| 25 | front-end come vista derivata | ❌ | `albero.py` non legge mai `fatti`, `anomalie`, `prove`, `confidenza` (§9.3) |
| 26 | migrazione V1 → V2 | ❌ | nessuna procedura; le due fasi si distruggono a vicenda (§7.2) |
| 27 | test sui failure case A–O | ◐ | 11 casi su 15 coperti (§10) |
| 28 | non forzare la decisione | ✅ | «non deciso» è una risposta prevista dell'arbitro, e ne è stata 20 volte su 30 |
| 29 | propagazione dell'errore | ✅ | `Anomalia.impatto` conta le persone che la decisione sposterebbe |
| 30 | tabelle di output | ✅ | tutte presenti tranne `merge_proposals`/`split_proposals` separate |
| 31 | architettura modulare | ✅ | 18 responsabilità su 19 separate (§4) |
| 32 | la lista dei NON FARE | ✅ | nessuna violazione trovata |

---

## 7. I punti fragili, in ordine di urgenza

### 7.1 — GRAVE: la fase 6 non è sotto controllo di versione

```
?? src/history_maker/ricostruzione/     ← 15 moduli, 5.937 righe
?? src/history_maker/identita.py        ← 2.250 righe
?? src/history_maker/genealogia.py  qualita.py  famiglie.py  coerenza.py
?? src/history_maker/albero.py      app/        nomi.py      ricuci.py
?? tests/test_ricostruzione.py  test_identita.py  test_coerenza.py  …
```

L'ultimo commit (`747e12e trascrizione quasi finita`) si ferma alla fase
3. **Tutto il cuore del sistema — V1 e V2 insieme, più i loro test — non
è mai stato committato.** Non c'è storia, non c'è modo di tornare a una
versione che funzionava, e una cartella persa cancella mesi di lavoro
misurato.

Nel frattempo sono in *staging* 68 file `.pyc` (`__pycache__/`), che nel
prossimo commit finirebbero nella storia del repository. `.gitignore`
contiene una riga sola: `/data/`.

**È il primo intervento, e viene prima di qualunque discussione
architetturale.** Nessuna delle proposte di questo documento ha senso
finché il codice a cui si applicano non è versionato.

### 7.2 — GRAVE: V1 e V2 si distruggono a vicenda

Entrambe scrivono nelle **stesse tabelle dello stesso database**, e
ciascuna comincia buttando quelle dell'altra:

```
genealogia.py:40   (V1)          modello.py  SCHEMA_SQL   (V2)
DROP TABLE individui;            DROP TABLE individui;
DROP TABLE menzioni;             DROP TABLE menzioni;
DROP TABLE legami;               DROP TABLE legami;
DROP TABLE unioni;               DROP TABLE unioni;
                                 DROP TABLE fatti;
                                 DROP TABLE anomalie;
```

Due conseguenze concrete:

* **eseguire le due fasi in parallelo è impossibile** — l'esatto opposto
  del §1 del prompt («la V2 deve poter essere eseguita in parallelo alla
  V1 senza distruggere i dati esistenti»). Il confronto fra i due
  sistemi, che è la sola prova che il nuovo sia meglio, oggi richiede di
  rieseguire tutto due volte e trascrivere i numeri a mano;
* **`genealogia` dopo `ricostruisci` corrompe il database in silenzio.**
  V1 non conosce `fatti` e `anomalie`, quindi non le butta: 319.112
  fatti e 14.528 anomalie restano a puntare a `individui.id` che nel
  frattempo sono stati rinumerati da zero. Nessun errore, nessun
  avviso, e ogni interrogazione che unisce le due parti dà risultati
  sbagliati.

### 7.3 — MEDIO: `decisioni` cresce senza limite

134.100 righe, di cui **131.645 fusioni dell'algoritmo** accumulate in
due giorni di esecuzioni. La tabella è per costruzione non azzerabile —
è la memoria del progetto — ma ci finisce dentro anche ogni fusione di
ogni tentativo buttato via.

Le decisioni che contano davvero sono 116: 92 conferme e 16 unioni di
Claude, 8 correzioni di Gemini. Sono lo 0,09% della tabella.

Effetti: `registro.storia()` fa una scansione completa con un
`json.loads` per riga (134.100 parse per rispondere a «perché queste due
persone sono la stessa?»), e la crescita è di ~25.000 righe per
esecuzione.

### 7.4 — MEDIO: le correzioni non tornano indietro nello stesso giro

Documentato onestamente in `docs/ricostruzione.md`. Il cognome
raddrizzato dal padre (123 casi) e l'età corretta sull'immagine entrano
al momento della **scrittura**, quindi il riconoscimento dello stesso
giro non li vede. Per i mestieri e le contrade il problema è stato
chiuso — il vocabolario si costruisce *prima* della risoluzione — ma per
i cognomi no.

### 7.5 — MEDIO: il sovra-accorpamento è la categoria che cresce

Dal rapporto corrente (`data/dataset/qualita.md`):

| categoria | casi |
|---|---:|
| **accorpamento** (troppi coniugi 145 + cognomi inconciliabili 50) | **195** |
| frammentazione (coniugi duplicati 118, coppie gemelle 47, figli omonimi 43, omonimi 7) | 215 |
| impossibile | 97 |
| sospetto (età incoerente con l'atto di nascita) | 80 |

L'accorpamento è la categoria scoperta per ultima, e per una ragione
strutturale: **fondere due madri diverse non produce né un assurdo né una
frammentazione.** Non viola niente, quindi tutti i numeri possono
migliorare mentre la precisione peggiora. Il meccanismo è la catena
A~B, B~C con A e C che non si confrontano mai, e la causa è che *l'a
priori si applica alla coppia, mai alla scheda che ne risulta*.

Va detto chiaramente perché tocca una priorità dichiarata: la
frammentazione resta il problema numero uno, ma da quando `qualita` sa
contare l'accorpamento i due numeri sono quasi pari (215 contro 195), e
uno dei due non era visibile fino a poche settimane fa.

### 7.6 — BASSO: assunzioni implicite che reggono ma vanno scritte

* **le chiavi degli individui sono stabili** perché derivano dalla
  menzione più antica del gruppo. Regge finché la fase 4 non cambia gli
  `id` delle menzioni: un `dataset` rieseguito con anni nuovi in mezzo
  potrebbe rinumerare, e allora ogni annotazione umana punterebbe a
  un'altra persona. Non c'è nessun controllo che lo impedisca.
* **`ETA_MINIMA_RUOLO`/`ETA_MASSIMA_RUOLO`** sono costanti scritte a
  mano, dichiaratamente «ciò che il ruolo significa» e non statistica.
  È una scelta difendibile, ma è l'ultima isola di pesi non misurati in
  un sistema che misura tutto il resto.
* **la chiave di cache di `verifica`** contiene il *percorso*
  dell'immagine, non la sua impronta. Riscaricare le immagini a una
  risoluzione diversa non invalida le risposte già date.
* **il glossario è per comune.** `config/glossario-torrebruna.yaml` è
  cablato nel default della configurazione: estendere a un secondo
  comune richiede un secondo file, e non c'è un meccanismo di eredità.

---

## 8. Cosa della V1 è riutilizzabile

| dato / codice | riutilizzabile | come |
|---|---|---|
| `atti`, `persone`, `voci_indice` (fase 4) | **sì, integralmente** | è la fonte di tutto; 49.889 menzioni già normalizzate |
| `identita.Menzione`, `ChiaviFamiliari`, `carica_menzioni` | **sì, è già in uso** | va estratto in un modulo proprio (§5) |
| `nomi.py` (età, patronimici, genere, bilancia) | **sì, è già in uso** | nessun intervento |
| `paleografia.py` (confondibilità delle grafie) | **sì, è già in uso** | nessun intervento |
| il matcher a pesi fissi di `identita.py` | **no** | sostituito; da isolare, non da cancellare |
| `famiglie.py` (il nucleo prima della persona) | **idea sì, codice no** | l'intuizione «la coppia è più identificabile della persona» è viva in `candidati.coppie_per_coppia` |
| `coerenza.py` (cancella le conclusioni assurde) | **no, e con misura** | cancellare gli assurdi nasconde il problema invece di risolverlo |
| `decisioni` non algoritmiche (116 righe) | **sì, preziose** | giudizi umani e di modello, già riapplicati |
| `verifiche` (17 risposte dall'immagine) | **sì** | quota già pagata, da non perdere |
| glossari e vocabolari | **sì** | conoscenza locale non ricavabile dalla statistica |

Niente da buttare. Il dataset attuale è integralmente la base della
prossima esecuzione.

---

## 9. I quattro buchi veri

### 9.1 — Non esiste una rilettura context-aware del documento

È il buco più grosso rispetto al prompt, ed è il §7 per intero.

Oggi ci sono due estremi e niente in mezzo:

* `transcribe` legge la pagina **senza nessun contesto** (Pass A ✅);
* `verifica` fa **una domanda chiusa su una parola**, con al massimo le
  alternative in chiaro.

Manca il passaggio centrale: rimettere davanti al modello **l'immagine
originale + la trascrizione precedente + il contesto genealogico
costruito per quel documento**, e chiedere non «correggi perché torni»
ma «dove l'immagine e il contesto sono d'accordo, dove sono in
conflitto, e dove non basta».

La differenza pratica: `verifica` può risolvere le 80 letture sospette
già identificate, ma non può **scoprire** che un atto è stato letto male
in un punto che nessuna anomalia ha segnalato. È il caso di Marzio
d'Andria Motta, trovato solo perché quel cognome era unico nel secolo.

Va costruito, e ha un prerequisito: il ContextBuilder per documento.

### 9.2 — Il ContextBuilder è per anomalia, non per documento

`contesto.fascicolo()` prende un'`Anomalia` e ne fa un dossier. È fatto
bene — elenca le prove contrarie, non suggerisce la risposta, numera le
menzioni perché la divisione sia applicabile — ma la sua unità è **il
caso da decidere**, non **il documento da rileggere**.

Il prompt chiede un contesto costruito attorno a un atto: la persona
bersaglio, i suoi genitori, i fratelli, **tutti** i coniugi, **tutti** i
figli, la famiglia estesa, i documenti già collegati, le persone simili,
e una sezione `possible_errors` con gli errori già sospettati su quelle
entità.

I dati per costruirlo ci sono tutti (`Scheda`, `esito.schede`, la tabella
`anomalie` indicizzata per individuo). Manca la funzione, e manca
soprattutto la sezione `possible_errors`: oggi un dossier porta **una**
anomalia, quella che l'ha generato, e non le altre che pendono sulle
stesse persone.

### 9.3 — Il front-end non mostra niente di ciò che la V2 produce

`albero.py` serve 18 colonne di `individui`. Non tocca mai `fatti`
(319.112 righe), `anomalie` (14.528), `decisioni` (134.100), né le
colonne `confidenza`, `stato` e `prove` che la V2 ha aggiunto proprio a
`individui`.

Chi consulta l'albero vede quindi *un valore*, e non ha modo di sapere
se quel valore è confermato o soltanto possibile, su quali atti si
regge, quali letture alternative esistono, o che su quella persona pende
un dubbio in coda. È esattamente la «vista derivata» che il §25 chiede e
che oggi non c'è: il sistema **sa** tutte quelle cose e non le mostra.

Costo basso rispetto al valore: sono interrogazioni in sola lettura su
tabelle già indicizzate.

### 9.4 — Manca la separazione della famiglia intera

Documentato in `docs/ricostruzione.md` e misurato tre volte. Dividere una
scheda per volta non funziona: delle 59 coppie gemelle prodotte dalle
divisioni dell'arbitro, **49 avevano i figli intrecciati nel tempo** —
non erano omonimi separati bene, erano famiglie spezzate. Dividere un
uomo lascia sua moglie a metà fra i due pezzi.

Serve un'operazione che divida **il nucleo insieme** — l'uomo, la
moglie, i figli — seguendo gli atti invece del punteggio. È il pezzo di
architettura che manca davvero, ed è il più difficile dei quattro.

---

## 10. I test: 11 casi su 15

533 test passano in 36 secondi. La copertura dei casi A–O del §27:

| | caso | copertura |
|---|---|---|
| A | omonimi realmente diversi | ✅ `test_il_nome_da_solo_non_basta_mai`, `test_due_famiglie_grandi_non_si_uniscono_mai` |
| B | stessa persona con grafie diverse | ✅ `test_la_grafia_rara_si_riunisce_alla_dominante` |
| C | secondo matrimonio | ✅ `test_due_mogli_diverse_restano_due` |
| D | due coniugi che sono la stessa persona | ✅ `test_la_moglie_letta_male_non_e_una_seconda_moglie` |
| E | figli omonimi diversi | ✅ `test_i_gemelli_omonimi_restano_due` |
| F | stesso figlio inserito due volte | ✅ `test_la_coppia_gemella_si_ricompone` |
| G | cognome letto male | ✅ `test_il_cognome_del_tutto_diverso_non_e_un_veto` |
| H | **nome composto letto come due persone** | ❌ c'è solo il caso inverso (`test_il_secondo_nome_che_compare_e_scompare`) |
| I | età errata, identità giusta | ✅ `test_l_eta_che_non_torna_non_e_un_veto` |
| J | professione diversa fra atti | ✅ `test_due_grafie_dello_stesso_mestiere_valgono_come_una` |
| K | **domicilio diverso fra atti** | ◐ la costante c'è (`PESO_CONTRADA_DIVERSA`), il test no |
| L | nome simile, genitori diversi | ✅ `test_due_madri_davvero_diverse_restano_un_veto` |
| M | **relazione padre/figlio errata** | ◐ coperto in `qualita`, non in `ricostruzione` |
| N | **rami familiari che si incrociano** | ❌ nessun test |
| O | **persona nuova non forzata su un candidato** | ◐ `test_chi_non_ha_nome_non_si_attacca_a_nessuno`, ma non il caso con nome |

Nessun test verifica esplicitamente la **conservazione della
provenance** dopo un giro completo: c'è `test_il_grezzo_non_si_perde_mai`
sulla lettura, ma non sull'attraversamento fusione → separazione →
riscrittura.

---

## 11. Piano proposto

In ordine, dal più urgente. I primi due non sono negoziabili; il resto è
la sequenza che propongo, non un impegno preso.

**Fase 0 — mettere in salvo** *(mezz'ora, nessun rischio)*
1. `.gitignore`: `__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`,
   `.idea/`, `/scr_*.pkl`, `/scr_*.json`.
2. Togliere dallo staging i 68 `.pyc`.
3. Committare la fase 6 intera — `ricostruzione/`, `identita.py`,
   `genealogia.py`, `qualita.py`, `albero.py`, `app/`, i test — con un
   messaggio che dica cosa contiene.

**Fase 1 — V1 e V2 davvero in parallelo** *(§7.2, il §1 del prompt)*
4. Prefissare le tabelle della V1 (`v1_individui`, …), oppure aggiungere
   a entrambe una colonna `versione`, così che le due ricostruzioni
   coesistano nello stesso database.
5. Un comando `confronta-ricostruzioni` che metta i due esiti fianco a
   fianco sulle stesse metriche di `qualita`. Oggi quella tabella si
   compila a mano.
6. Estrarre da `identita.py` la lettura degli atti in un modulo proprio,
   così che la V2 non dipenda più da una funzione privata della V1.

**Fase 2 — chiudere l'anello dell'evidenza documentaria** *(§9.1, §9.2)*

Il primo milestone del §34 è `IMMAGINE → EVIDENZA DOCUMENTARIA DI
QUALITÀ`, non `IMMAGINE → ALBERO PERFETTO`. Quindi:

7. `contesto.per_documento(atto)` — il ContextBuilder che il §8 chiede,
   con la sezione `possible_errors` presa dalle anomalie già in tabella.
8. Il Pass B: `rileggi` — immagine + trascrizione + contesto, che
   produce accordi, conflitti e ambiguità **senza** forzare la coerenza
   col grafo. Su un campione di 20-30 atti scelti fra i più dubbi, per
   misurare prima di generalizzare.
9. Aggiungere l'impronta dell'immagine alla chiave di cache.

**Fase 3 — rendere visibile ciò che il sistema già sa** *(§9.3)*
10. Estendere `albero.py` e l'applicazione con: confidenza e stato della
    scheda, i fatti datati con il loro atto, le letture alternative, le
    anomalie aperte, e la storia delle decisioni che hanno toccato quella
    persona.

**Fase 4 — igiene e il buco difficile**
11. Archiviare le decisioni algoritmiche più vecchie di N esecuzioni,
    tenendo per sempre quelle non algoritmiche; indicizzare `decisioni`
    per entità.
12. I test mancanti: casi H, K, M, N, O, e la conservazione della
    provenance attraverso un giro completo.
13. La separazione del nucleo familiare (§9.4). Ultimo perché è il più
    difficile e perché tre tentativi diversi hanno già peggiorato il
    conto: va affrontato con la misura pronta **prima** di scrivere il
    codice.

---

## 12. La domanda del §36

> «Sto costruendo una nuova architettura di evidenze genealogiche o sto
> semplicemente aggiungendo un'altra regola all'algoritmo precedente?»

Per questo repository la risposta onesta è una terza: **l'architettura di
evidenze genealogiche è già stata costruita, e il lavoro che resta è
finirne i bordi.** I quattro buchi del §9 non sono regole in più — sono
un passaggio della pipeline che manca (la rilettura context-aware), un
costruttore di contesto con l'unità sbagliata, una vista che non mostra
il modello sottostante, e un'operazione di grafo che non esiste.

Il rischio vero, qui, non è costruire troppo poco. È rifare da zero
qualcosa che è già stato misurato, e perdere insieme al codice il
registro di cosa è stato provato e non ha funzionato — che è la parte
che costa di più da riguadagnare.

---

## Appendice A — Il Context JSON per documento

La forma che propongo per `contesto.per_documento(atto)` (§9.2, punto 7
del piano). Tutti i campi si ricavano da tabelle che esistono già; non
serve nessun dato nuovo.

L'unità è **l'atto**, e le persone dentro il contesto sono le schede a
cui le sue menzioni sono state assegnate. I tetti sulle liste sono
quelli che `contesto.py` già applica: un fascicolo che non entra nella
finestra non serve a nessuno.

```json
{
  "atto": {
    "id": 4127,
    "tipo": "morte",
    "numero": 18,
    "anno": 1831,
    "registro": "Torrebruna, Morti 1831",
    "immagini": ["an_ua19944535/0031.jpg"],
    "lato_pagina": "sinistra",
    "affidabilita_dichiarata": "media",
    "parti_illeggibili": ["il cognome della madre"]
  },

  "trascrizione_precedente": {
    "versione_prompt": "…",
    "modello": "gemini-3.6-flash",
    "testo_integrale": "…",
    "menzioni": [
      {
        "menzione": 20114,
        "ruolo": "defunto",
        "nome": "Marzio", "cognome": "d'Andria Motta",
        "eta": "trentasei", "professione": "contadino",
        "residenza": "Torrebruna",
        "individuo": 2396
      }
    ]
  },

  "contesto_genealogico": {
    "_nota": "IPOTESI, non verità. Se l'immagine la contraddice, vince l'immagine.",
    "persone": [
      {
        "individuo": 2396,
        "ruolo_nell_atto": "defunto",
        "nome": "Marzio", "cognome": "Lella",
        "confidenza_scheda": 0.62, "stato": "possibile",
        "quante_menzioni": 1,
        "letture_del_nome": ["Marzio (1)"],
        "letture_del_cognome": ["d'Andria Motta (1)"],
        "nascita": {"anno": 1795, "fonte": "eta' dichiarate"},
        "morte": {"anno": 1831, "fonte": "atto di morte"},
        "mestieri": ["1831: contadino"],
        "contrade": [],
        "genitori": {
          "padre":  {"individuo": 50, "etichetta": "Filippo Lella n.1760", "menzioni": 31},
          "madre":  {"individuo": 51, "etichetta": "Margherita Rossi n.1765", "menzioni": 22}
        },
        "fratelli": [
          {"individuo": 210, "etichetta": "Rebecca Lella n.1794"},
          {"individuo": 233, "etichetta": "Giuseppe Lella n.1799"}
        ],
        "coniugi": [
          {"individuo": 812, "etichetta": "Andria Motta n.1798",
           "matrimonio": null, "stato_relazione": "dedotta dagli atti",
           "figli_in_comune": []}
        ],
        "figli": [],
        "famiglia_estesa": {
          "nonni": [], "suoceri": [], "cognati": []
        },
        "documenti_collegati": [
          {"atto": 4127, "anno": 1831, "tipo": "morte", "ruolo": "defunto"}
        ]
      }
    ],

    "persone_simili": [
      {
        "individuo": 9981,
        "etichetta": "Marzio Lella n.1799",
        "perche": "stesso nome, cognome del padre compatibile, 4 anni di scarto",
        "compatibilita": 0.44,
        "prove_a_favore": ["nome marzio raro (+2.90)"],
        "prove_contrarie":  ["genitori discordi (-1.60)"]
      }
    ]
  },

  "possible_errors": [
    {
      "codice": "surname_variant",
      "campo": "cognome",
      "individui": [2396],
      "descrizione": "porta 'dandriamota' (1 volta nel secolo) ma suo padre porta 'lela' (1.657)",
      "confidenza": 0.83,
      "impatto": 11,
      "gravita": "media",
      "stato": "aperta",
      "alternative": ["Lella", "d'Andria Motta"]
    },
    {
      "codice": "age_inconsistency",
      "campo": "eta",
      "individui": [2396],
      "descrizione": "dichiara 36 anni nel 1831; l'atto di nascita del fratello ne farebbe 32",
      "confidenza": 0.55,
      "impatto": 1,
      "gravita": "bassa",
      "stato": "aperta",
      "alternative": ["trentasei", "trentadue"]
    }
  ],

  "domande_aperte": [
    "Il cognome del defunto sulla pagina è 'd'Andria Motta' o 'Lella'?",
    "'Andria Motta' è il cognome del defunto o il nome della moglie?"
  ]
}
```

I codici di `possible_errors` sono quelli del §9 del prompt
(`duplicate_person`, `homonym`, `surname_variant`, `wrong_parent`,
`possible_second_marriage`, `same_name_siblings`, `age_inconsistency`,
`wrong_profession`, …). Sulla tabella `anomalie` esistente sono una
rietichettatura di `tipo` + `campo`: la lista resta estendibile senza
migrazione, perché nessuno dei due campi è vincolato.

## Appendice B — L'esito del Pass B

Quello che `rileggi` deve produrre (§9.1, punto 8 del piano). Una voce
per campo, e la regola che tutto il resto serve a rendere possibile:
**il verdetto è sull'accordo fra immagine e contesto, non sulla
correzione.**

```json
{
  "atto": 4127,
  "modello": "gemini-3.6-flash",
  "versione_prompt": "2.0.0",
  "impronta_immagine": "sha256:…",

  "campi": [
    {
      "campo": "cognome",
      "menzione": 20114,
      "trascrizione_precedente": "d'Andria Motta",
      "lettura_dall_immagine": "d'Andria Motta",
      "attesa_dal_contesto": "Lella",
      "accordo": "CONFLITTO_CON_IL_CONTESTO",
      "confidenza": 0.88,
      "alternative": [],
      "spiegazione": "sulla carta c'è 'marito d'Andria Motta': è il nome della moglie, non il cognome del defunto",
      "va_rivisto": false
    },
    {
      "campo": "eta",
      "menzione": 20114,
      "trascrizione_precedente": "trentasei",
      "lettura_dall_immagine": "trentasei",
      "attesa_dal_contesto": "circa trentadue",
      "accordo": "CONFERMATO",
      "confidenza": 0.94,
      "spiegazione": "l'età è quella; a non tornare è la stima, non la lettura",
      "va_rivisto": false
    }
  ],

  "verdetti_ammessi": [
    "CONFERMATO", "COMPATIBILE", "CONFLITTO_CON_LA_TRASCRIZIONE",
    "CONFLITTO_CON_IL_CONTESTO", "AMBIGUO", "NON_LEGGIBILE",
    "PROVE_INSUFFICIENTI"
  ]
}
```

Due vincoli che il prompt del Pass B deve imporre esplicitamente, e che
sono la ragione per cui questo passaggio è delicato:

* il contesto va presentato **come ipotesi**, con quella parola. Un
  modello a cui si dà il grafo come dato di fatto rilegge l'immagine
  finché non torna, e produce una conferma che non vale niente — è lo
  stesso errore del fascicolo che presenta solo l'ipotesi della fusione;
* `CONFLITTO_CON_IL_CONTESTO` con alta confidenza è un **risultato
  buono**, non un fallimento. È il caso di Marzio: l'immagine ha
  ragione, il grafo aveva torto, e il sistema deve poterlo dire.

Le risposte non diventano fatti: diventano `decisioni` con decisore
`gemini`, riapplicate al giro successivo come già succede per
`verifica`.
