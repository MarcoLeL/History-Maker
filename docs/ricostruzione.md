# La ricostruzione genealogica

Note su come è fatta la fase 6, perché è fatta così, e cosa è stato
misurato. Chi vuole solo usarla trova i comandi nel README; qui c'è il
ragionamento, comprese le cose che non hanno funzionato.

## Il problema

La tabella `persone` della fase 4 non contiene persone: contiene
**menzioni**. Un uomo che nasce nel 1820, si sposa nel 1845, ha sei figli
e muore nel 1889 compare nei registri almeno dieci volte, ogni volta come
una riga nuova, e nessuna delle dieci sa delle altre. A Torrebruna sono
49.889 righe per qualcosa come sedicimila persone.

Riconoscerle è difficile per una ragione precisa: **in un paese di poche
famiglie il nome non identifica nessuno**. I Pelliccia sono 5.121 menzioni
su 49.889; il primogenito porta il nome del nonno, quindi «Domenico
Pelliccia» sono cinque uomini diversi nello stesso mezzo secolo. E
all'incontrario: la stessa persona compare come `Lella`, `Lelli`, `Fella`,
`Lallo`, perché ogni scrivano ha la sua mano e ogni trascrizione i suoi
errori.

I due errori possibili non sono simmetrici, e vale la pena dirlo prima di
tutto il resto:

* **unire due persone diverse** fabbrica qualcuno che non è mai esistito.
  Si vede: produce cose impossibili — una donna che partorisce per
  sessant'anni, un uomo che fa da testimone dopo il proprio funerale — e
  il controllo di qualità le conta;
* **spezzare una persona in due** non produce niente di impossibile.
  Produce un albero con meno parentele di quante l'archivio ne contenga, e
  chi guarda non ha modo di accorgersene.

Il secondo è più insidioso, il primo è più grave. Il numero che conta è la
somma dei due, e si misura con `python -m history_maker qualita`.

## Cosa non ha funzionato

La fase 6 precedente (`identita.py`, `famiglie.py`, `coerenza.py`) è
un sistema a **pesi fissi e veti**: due genitori uguali valgono 4,0, un
coniuge 2,5, il mestiere 0,5; sopra 3,0 si unisce; e otto controlli
possono dire "impossibile" e chiudere la questione.

Su Torrebruna produce 16.693 persone e, dopo la sua passata di
correzione, zero fatti impossibili — ma 266 frammentazioni certe.
Interrogando il codice **su quelle 266**, i motivi per cui non aveva
unito sono:

| perché non ha unito | casi |
|---|---:|
| il punteggio additivo non arriva a 3,0, ma l'evidenza c'era | ~26 su 81 coppie gemelle |
| veto sul cognome, spesso per un `fu Antonio` incollato al cognome | ~30 |
| veto sull'età: 12, 18, 28 anni di scarto | ~10 |
| veto sul nome a 0,80-0,81: `Zelma`/`Zalma`, `Fidelma`/`Fedela` | ~8 |
| i due non diventano mai candidati | ~8 |

Sono cinque difetti diversi di **un solo difetto**: pesi che non sanno
dove si trovano. Un peso fisso sul cognome vale uguale per `Pelliccia` e
per `Genualdi`, e non c'è modo di aggiustarlo — alzarlo cuce insieme i
Pelliccia, abbassarlo lascia separati i Genualdi.

## Il modello

    ATTO → MENZIONE → FATTI → CANDIDATI → IDENTITÀ → RELAZIONI →
    TIMELINE → CONTROLLO DEL GRAFO → ANOMALIE → RICONCILIAZIONE

Le entità stanno in `modello.py`; ogni passaggio ha il suo modulo. Tre
scelte spiegano il resto.

### 1. I pesi si misurano, non si scelgono

Ogni indizio vale il logaritmo del rapporto fra due probabilità:

    peso = log10( P(stessa forma | stessa persona) / frequenza della forma )

Per `Pelliccia`: `log10(0,80 / 0,103) = +0,89`, un indizio debole — che è
esattamente quello che è. Per `Genualdi`, sei menzioni in un secolo:
`log10(0,80 / 0,00012) = +3,8`, tagliato al tetto. La stessa formula, lo
stesso codice, nessuna soglia da rinegoziare.

Vale per tutto: nomi, cognomi, mestieri, contrade, e soprattutto per le
**chiavi di parentela**. «figlio di Filippo Lella e Margherita Rossi» è
raro nello stesso modo in cui è raro un cognome, e il peso se lo calcola
da solo.

I pesi sono in *ban* e si sommano; il totale letto insieme all'a priori
del corpus è il **logit**, e le soglie stanno lì. L'a priori si ricava
dall'archivio (`evidenza.stima_persone`), quindi le stesse soglie valgono
su un comune di quattrocento menzioni e su uno di cinquantamila.

### 2. Restano cinque veti, non otto

Sono le sole cose che il mondo non consente: due atti di nascita
distinti, due atti di morte distinti, comparire vivi dopo il proprio
funerale, partorire per più anni di quanti se ne abbiano di fertili, e
ricoprire un ruolo prima dell'età che quel ruolo richiede — chi si sposa
non ha tre anni.

Tutto il resto è graduale. Un cognome del tutto diverso costa 1,5 ban e
una parentela in comune ne vale 2,8: **il cognome sbagliato si può
superare**, ed è il caso di Nicola Pelliccia letto `Bellucci` in un atto,
con la stessa moglie e gli stessi figli negli altri.

### 3. Si torna indietro

Le schede si fondono **e si separano**, per quanti giri servono. Una
fusione che rende una scheda incoerente viene disfatta, e resta scritto
perché. Ogni decisione ha un autore, una confidenza, le prove a favore, le
contraddizioni e la versione dell'algoritmo: la tabella `decisioni` non si
azzera fra un'esecuzione e l'altra.

Le chiavi delle persone sono stabili — l'identificatore della menzione
più antica del gruppo — perché rinumerare a ogni esecuzione rende
impossibile qualunque annotazione umana.

## Gli errori che questa fase ha fatto, e cosa ne è venuto

Tutti misurati sull'archivio intero. Sono qui perché il codice che li ha
corretti si capisce solo sapendo cosa correggeva.

**Lo scaffale vuoto.** Indicizzare anche chi il cognome ce l'ha sotto la
chiave vuota crea uno scaffale che contiene l'archivio intero: 546
candidati per menzione invece di una decina, e centoventi volte il tempo.

**L'a priori a −1,3.** Con quello, due menzioni `Domenico Pelliccia` senza
nient'altro si univano. Risultato: 8.354 persone al posto di sedicimila e
**2.129 fatti impossibili**. L'a priori giusto è quello dell'archivio,
−4,26 a Torrebruna, e la stessa coppia resta divisa.

**`sua moglie` come cognome.** Quando l'atto non dà il cognome della
donna, la trascrizione scrive nella colonna del cognome quello che l'atto
dice di lei. Una stringa così è rarissima, quindi il modello ne ricavava
una prova fortissima: due donne senza cognome risultavano della stessa
famiglia con +3,0.

**Le grafie isolate.** Una scheda di ventidue menzioni raccoglie qualche
lettura strampalata, e se ogni lettura strampalata può decidere un
confronto quella scheda somiglia a chiunque: `Maria Nicola D'Ettore` si è
presa la sorella `Lucia Antonia` perché fra le sue grafie ce n'era una che
diceva così. Ora contano solo le forme **principali**.

**Il veto lento.** Il controllo "questo ruolo richiede un'età minima"
scorreva le menzioni della scheda a ogni confronto: sulle 1.371 firme del
sindaco, moltiplicate per un milione e mezzo di confronti, era il collo di
bottiglia dell'intera fase. Ora è un numero aggregato.

**L'età dentro il tetto degli indizi deboli.** Nome, cognome ed età
insieme non devono bastare a unire due persone — ma nome e cognome
*soli* non devono bastare, e l'età è un terzo indizio indipendente.
Contandola dentro il tetto, le persone salivano da 13.600 a 18.200 e i
bambini ritrovati da adulti scendevano di un terzo: quattromila
frammentazioni per una distinzione sbagliata.

**Spezzare invece di prevenire.** Le contraddizioni scoperte a posteriori
si possono risolvere in due modi: separando la scheda incoerente, o
impedendo prima la fusione che l'ha creata. Il primo sembra più
completo e non lo è. Spezzando le schede con un'età impossibile o due
parti troppo vicini, le cose impossibili scendevano da 230 a 72 — e le
frammentazioni salivano da 230 a 579, perché spezzare una moglie lascia
il marito con due mogli. Ora quelle contraddizioni si prevengono nei
veti, o si tolgono dal **legame** invece che dalla persona.

**Aggiustare la stima perché il controllo passi.** Sembra ragionevole
spostare un anno di nascita stimato dentro la finestra che i ruoli
impongono: chi si sposa nel 1834 non è nato nel 1830. Provato: toglie 120
matrimoni a un'età impossibile e ne aggiunge 26 di genitori troppo
vecchi, perché rendere qualcuno più vecchio al matrimonio lo rende più
vecchio anche alla nascita dei figli. La contraddizione non sparisce,
cambia posto — e il numero aggiustato la nasconde.

## Cosa ne è venuto

Misurato sull'archivio intero, contro la fase 6 precedente. Le prime
righe sono quello che si vede; le ultime quattro sono quello che conta.

| | vecchia | nuova | |
|---|---:|---:|---|
| persone riconosciute | 16.707 | 13.626 | −3.081 |
| che si reggono su una menzione sola | 11.674 | 8.903 | **−2.771** |
| **bambini nati qui e ritrovati da adulti** | 1.108 | **1.530** | **+38%** |
| **nati qui di cui si conoscono i figli** | 365 | **436** | **+19%** |
| **morti di cui si conosce anche la nascita** | 585 | **959** | **+64%** |
| **catene nonno-genitore-figlio** | 8.054 | **9.078** | **+13%** |
| cose impossibili | 0 | 108 | |
| frammentazioni certe | 266 | 182 | −84 |

Le quattro righe in grassetto sono la frammentazione **che nessun
controllo vede**: quanti bambini l'archivio ritrova da adulti, quante
vite si chiudono fra la nascita e la morte, quante generazioni si
tengono. Sono cresciute tutte, e sono il motivo per cui questa
ricostruzione vale più di quella che sostituisce.

Le due righe finali vanno lette insieme. Le 266 frammentazioni della fase
precedente sono scese a 182; i suoi zero impossibili però non erano zero
prima della sua passata di correzione, che **cancellava** le conclusioni
assurde. Qui i 108 che restano non sono cancellati: 93 sono un'età
dichiarata che non torna con l'anno di un matrimonio — un errore di
lettura, non di identità — e stanno tutti nella coda delle anomalie, con
la domanda già pronta per l'immagine.

## Gli attributi: la serie, non il campo

Mestieri e contrade non sono campi ma **serie**. Domenico Lella è bovaro
nel 1850, contadino nel 1861 e bracciante nel 1875, e le tre cose sono
vere tutte e tre: `fatti` le tiene tutte con il loro anno e il loro atto,
e la scheda mostra i valori dal più recente al più antico.

Il vocabolario del paese riconduce le grafie: `Agrimenfore`,
`Agrimensoere`, `Agrimengore` → `Agrimensore`. Il criterio **non** è
quello dei cognomi, e la differenza è costata due tentativi:

* il raggruppamento per contatto (A somiglia a B, B a C) è giusto per le
  grafie di un cognome e disastroso per un vocabolario di parole diverse:
  a 0,70 la catena arrivava da `agrimensore` a `proprietario`, e ci
  ricondusse anche `muratore`, `calzolaio` e `sanitario`;
* una soglia assoluta di frequenza («chi supera tre occorrenze fa testo»)
  lascia `Agrimensoere` — letto otto volte — a fare parola per conto
  proprio, e la scheda dell'unico agrimensore del paese elencava quattro
  mestieri che erano lo stesso.

La regola che regge è: **fa testo chi non è dominato da nessuno**. Ogni
forma cerca, fra quelle almeno quattro volte più frequenti, quella che le
somiglia come due grafie della stessa parola (0,86), e non si formano
catene. Per le contrade il confronto passa dal solo nucleo del toponimo,
perché `Guardiabruna, nella strada Sotto il Palazzo` e `Guardiabruna,
nella strada Vicenne` condividono i due terzi delle lettere.

Quello che nessuna soglia può sapere è che `bovajo` e `bovaro` sono lo
stesso mestiere: non si somigliano abbastanza da sembrare due grafie
della stessa parola, e infatti non lo sono — sono due modi di dirla. È il
posto della conoscenza locale, e sta nel glossario, sotto `mestieri:`,
accanto ai toponimi.

### Il vocabolario si costruisce prima, non dopo

Per un po' il vocabolario è servito solo a scrivere. Si costruiva a
risoluzione finita, e il confronto fra due schede guardava la forma
normalizzata grezza: due menzioni scritte `Agrimensore` e `Agrimenfore`
non prendevano il punto del mestiere in comune, mentre il sistema, in un
altro punto dello stesso programma, le stampava come la stessa parola.

E non era un punto mancato: era **un punto al contrario**. Chi fa due
mestieri diversi paga una penale, e per il confronto quelle erano due
parole diverse. Sull'archivio vero: 3.289 confronti in cui il mestiere
passa dalla penale al punto a favore, e 1.176 in cui ci passa la
contrada — `via Porta Murella` e `strada Porta Murella` erano due
contrade diverse per il 19% delle menzioni che ne dichiarano una.

Ora il vocabolario si costruisce fra la lettura e la risoluzione e
viaggia col corpus, dentro le chiavi che ogni scheda riceve; le frequenze
con cui il modello pesa un mestiere sono quelle della **famiglia** di
grafie, non della singola forma, altrimenti `agrimensore` sembrerebbe più
raro di quanto è. Costa una ventina di secondi la prima volta e niente le
successive: il risultato sta in cache, con il glossario nella chiave,
perché quelle equivalenze le dichiara una persona e cambiarle deve
rifare il vocabolario anche se le menzioni sono le stesse.

Vale poco e in modo pulito: 36 duplicati in meno (13.610 → 13.574), due
assurdi in meno, le frammentazioni ferme, e il collegamento per persona
identico (0,664 catene nonno-nipote per persona prima e dopo). Non è la
scoperta di un giacimento: è un sistema che smette di contraddirsi.

## Cosa costa

| | |
|---|---|
| lettura e igiene dei nomi | ~60 s la prima volta, poi in cache |
| vicinati delle forme | ~5 min la prima volta, poi in cache |
| primo giro su 49.889 menzioni | ~25 s |
| riconciliazione, nove giri | ~45 s |
| vocabolari, anomalie e scrittura | ~60 s |

In tutto **due minuti e mezzo** a freddo, **un minuto e mezzo** quando le
cache sono calde. Le cache stanno in `data/dataset/.cache-*.pkl` e sono
indicizzate su tutto ciò che cambia il risultato: le forme, la soglia e
la tabella delle confusioni paleografiche per i vicinati; un'impronta
delle tabelle di partenza e le correzioni già registrate per il corpus.
Se cambia una qualunque di quelle cose la cache si rifà da sola.

Non è solo comodità. `verifica` e `arbitra` ricostruiscono il grafo in
memoria ogni volta che partono, e senza la cache del corpus pagherebbero
un minuto prima di poter fare una sola domanda.

## Dove finiscono i dubbi

Quello che il calcolo non decide non si butta: diventa un'anomalia con una
priorità, e la priorità è **il dubbio moltiplicato per quante persone la
risposta sposterebbe**. Un caso al 50% che muove un ramo di quaranta
persone viene prima di un caso al 90% che ne muove due.

Da lì partono le due code costose, e solo da lì:

* `python -m history_maker verifica` chiede all'**immagine originale** le
  parole decisive. Non ritrascrive niente: ritaglia dalla pagina a piena
  risoluzione la banda in cui la parola dovrebbe trovarsi e fa una domanda
  chiusa. La cache è indicizzata su atto, domanda, alternative, modello e
  versione del prompt: una domanda già fatta non riparte;
* `python -m history_maker arbitra` sottopone a un modello di
  ragionamento i casi della **fascia grigia** — quelli in cui l'evidenza
  sta fra la soglia della segnalazione e quella dell'unione. Il fascicolo
  contiene le schede, la cronologia, i parenti e le prove **a favore e
  contro**: un fascicolo che presenta solo l'ipotesi della fusione
  ottiene fusioni.

Le risposte non diventano fatti: diventano decisioni con un autore. Se
domani si scoprirà che quel modello sbagliava un certo tipo di casi, si
potrà vedere quali e disfarli.

## Il giro completo delle decisioni

Una decisione presa da Claude, da Gemini sull'immagine o da una persona
non resta lì: viene **riapplicata** alla ricostruzione successiva.

```bash
python -m history_maker decidi unione 2396 10686 --perche "stessa moglie, stessi figli"
python -m history_maker ricostruisci      # la decisione vale da qui in poi
python -m history_maker decidi disfa 47 --perche "l'atto del 1852 dice altro"
```

Senza questo passaggio il giudizio di chi ha guardato la carta varrebbe
una volta sola: la ricostruzione successiva rifarebbe gli stessi conti e
tornerebbe alla stessa conclusione. Le imposizioni scavalcano il punteggio
ma **non i veti**: se unire due schede produrrebbe una donna che partorisce
dopo il proprio funerale, la fusione non si fa e resta scritto perché.

## Le due code, percorse davvero

Trenta richieste di quota, e servono a dire che il giro funziona per
intero — non a correggere un archivio di cinquantamila menzioni.

**`verifica`, dodici domande all'immagine.** Otto letture su dodici
smentiscono la trascrizione con confidenza ≥ 0,8; le altre la
confermano, e confermare è un risultato quanto correggere — significa
che lì a non tornare è l'identità, non la lettura.

> «Che età è scritta qui? La trascrizione dice *trentasette*; il suo atto
> di nascita ne farebbe circa 25.»
> — *ventiquattro* (0,95). L'immagine dà ragione all'atto di nascita.

Le otto letture sicure e diverse sono diventate **correzioni registrate**
(`decisioni`, autore `gemini`), e la ricostruzione successiva le applica:
`età corretta sull'immagine: 8`. Le letture da ricontrollare scendono da
99 a 94.

**`arbitra`, trenta casi della fascia grigia.** Sedici fusioni con
confidenza ≥ 0,85, venti «non deciso». Applicate: 13.626 → 13.610
persone, e nessun fatto impossibile in più. Le risposte «non deciso»
sono la parte che rassicura di più:

> «Le prove non bastano per collegare con certezza la singola menzione
> isolata all'individuo con atto di nascita del 1856. Sebbene il nome sia
> raro, una sola citazione…»

Due difetti scoperti solo mettendo in moto le code, e nessuno dei due si
sarebbe visto altrimenti:

**Le risposte tornavano vuote.** `backend.estrai_json` rende già la
struttura, non il testo: passarla a `json.loads` solleva un `TypeError`,
e il ramo di ripiego trasformava ogni risposta buona in una vuota. Una
richiesta pagata per volta, senza lasciare traccia — perché una lettura
vuota è esattamente ciò che si vede quando il modello non riesce a
leggere la parola.

**«Persone diverse» non è sempre una separazione.** Su un caso di doppio
coniuge le entità del fascicolo non sono due schede che potrebbero
coincidere: sono *una persona e i suoi due coniugi*. Tradurre quella
risposta in una separazione avrebbe staccato il marito dalla prima
moglie, cioè l'opposto di quanto il modello aveva detto. Ora una
separazione si registra solo quando le due entità sono davvero nella
stessa scheda.

## Un caso per intero: Marzio d'Andria Motta

Atto di morte n. 18 del 1831. Sulla carta:

> è morto nella sua casa **Marzio d'Andria Motta** d'anni trentasei nato
> in Torrebruna di professione contadino domiciliato in detto comune,
> **marito d'Andria Motta** figlio del fu **Filippo Lella** di professione
> contadino e di **Margherita Rossi**

«Andria Motta» è il nome della **moglie**, e chi ha trascritto l'ha
attaccato a lui come cognome. Marzio è un Lella. A dichiararne la morte è
Nicolangelo Lella, che è suo fratello.

È un caso difficile per una ragione precisa: **Marzio compare una volta
sola in tutto il secolo**. Non c'è una seconda menzione con cui
confrontarlo, quindi nessuna quantità di riconoscimento dell'identità può
aiutare. La ricostruzione deve reggersi sui suoi.

Come lo tratta la fase 6, in quattro passaggi:

1. **La famiglia dentro l'atto.** L'atto dice di chi è figlio, e quello
   non è un'inferenza: Marzio finisce fra i figli di Filippo Lella e
   Margherita Rossi, sesto di dieci, fra Rebecca (1794) e Giuseppe (1799).
   Il cognome sbagliato **non lo stacca dalla famiglia** — nel sistema
   precedente un cognome a 0,0 di somiglianza era un veto.
2. **La segnalazione.** `SURNAME_ANOMALY`: «porta 'dandriamota' (1 volta
   in tutto il secolo) ma suo padre porta 'lela' (1.657)».
3. **La correzione.** Un figlio legittimo porta il cognome del padre: non
   è una probabilità, è come funziona l'atto di stato civile. Quando la
   forma letta non esiste nel resto del secolo e quella del padre è otto
   volte più attestata, la scheda mostra **Marzio Lella**. Sono 123 casi
   in tutto l'archivio.
4. **Cosa resta.** La lettura originale sta in `varianti_cognome` e nel
   campo `grezzo` del fatto, che ha `interpretato = 'Lella'` e stato
   *probabile*; nel registro c'è una decisione con il motivo e la
   confidenza. E la ricerca lo trova da tutt'e due i lati: cercando
   «Marzio Lella» e cercando «Andria Motta».

I sette casi che il criterio **non** corregge restano in coda, ed è
giusto così: `Salvatora d'Armi`, figlia di un `Salvatore`, non è un
cognome letto male ma un nome usato come cognome — un altro errore, che
vuole un altro rimedio.

Quello che ancora non succede: la correzione entra al momento della
scrittura, quindi **non torna indietro a nutrire il riconoscimento**. Per
Marzio non cambia niente, perché ha una menzione sola; per qualcun altro
un cognome raddrizzato potrebbe far incontrare due schede che oggi non si
incontrano.

## Separare: la cosa che il modello sa chiedere e che non conviene fare

L'arbitro sapeva unire e non separare, perché il fascicolo non nominava
le righe: «sono due persone» non dice quali menzioni vanno di qua e quali
di là. Ora le numera, il modello risponde con i gruppi, e il calcolo
completa l'assegnazione delle righe che il fascicolo non gli ha mostrato —
**il giudizio a chi legge, la contabilità al calcolo**. I pezzi si
ricordano di doversi stare lontani, altrimenti la riconciliazione del giro
dopo li rimetterebbe insieme.

Funziona, e le risposte sono buone:

> «La scheda copre un arco temporale dal 1809 al 1886 (108 anni),
> chiaramente impossibile per una sola persona. Si tratta di una fusione
> tra il vecchio Nicola Troilo (nato intorno al 1778) e…»

Ventiquattro divisioni, alta confidenza, motivazioni che citano gli atti.
Applicate: le vite impossibili scendono da 73 a 53. E le frammentazioni
salgono da 180 a 217.

Un controllo scritto apposta ha detto perché. Delle 59 coppie gemelle che
ne uscivano, **49 avevano i figli intrecciati nel tempo**: non erano
omonimi separati bene, erano famiglie spezzate. Dividere un uomo lascia
sua moglie a metà fra i pezzi, e la propagazione ai parenti — che segue
gli atti, non un punteggio — peggiora le cose invece di rimediarle (221).
Una guardia che rifiuta le divisioni i cui pezzi si sovrappongono nel
tempo ne ferma quattro su ventiquattro: troppo poche per cambiare il
conto.

Quindi la capacità resta e **il default è non applicarla**
(`arbitra --dividi` per accenderla). È il caso in cui la misura dice il
contrario dell'intuizione, e vale la pena tenerlo scritto: un modello che
risponde bene sul caso singolo può sbagliare l'effetto sul grafo, e
l'unico modo di saperlo è contarlo.

## L'errore opposto: quando una scheda non è una persona

Per mesi la qualità ha guardato da una parte sola — assurdi e
frammentazione — e questo ha nascosto l'errore più grosso che il sistema
facesse. **Fondere due madri diverse non produce né un assurdo né una
frammentazione**: non viola niente. Il sistema poteva quindi peggiorare
in precisione mentre tutti i numeri miglioravano, ed è esattamente quello
che è successo. Il caso che l'ha fatto vedere è arrivato da chi guardava
l'albero, non da una misura: *«Angela Lella, come fa ad aver avuto sei
coniugi?»*

Il conto, allora: **306 persone con tre o più coniugi, 107 con quattro o
più, una con dodici.** E una scheda di 84 menzioni con **29 grafie di
cognome** — `Fidelibus`, `Di Paolo`, `Tilli`, `Antonucci`, `Ninni`,
`Villa` — che non era una persona ma un secchio.

### Il meccanismo: la catena

Ogni singolo passo era difendibile:

```
genitori dello stesso figlio (+2,80); nome simile Mariangela/Mariangiola (+1,76);
   cognomi diversi tilis/peliciafuconceria (0,20) (-1,74)      → unite
coniuge nazario minchili (+3,20); nome angelamaria (+2,50);
   cognomi diversi tili/vila (0,50) (-1,57)                     → unite
```

Un indizio relazionale forte batte un cognome che dice il contrario — che
è la gerarchia giusta. Ma la scheda fusa porta con sé **tutti** i nomi e
**tutti** i cognomi, quindi al giro dopo somiglia a più gente di prima e
ne assorbe altra. A~B, B~C, e A e C non si confrontano mai. È lo stesso
errore che il vocabolario dei mestieri evitava per costruzione, rimasto
vivo nel cuore del sistema: **l'a priori si applica alla coppia, mai alla
scheda che ne risulta.**

### La costante che credevo e non avevo contato

```python
PESO_CONIUGE_DISCORDE = -0.35  # ci si risposa, e le vedove si risposano spesso
```

Il commento era una convinzione scritta come se fosse un dato. Misurata
sull'archivio — chiave sposo + cognome + **suo padre**, perché senza il
padre si contano gli omonimi e non i risposati (viene 1 su 15, tutto
rumore) — la verità è:

> **5 risposati su 1.172 sposi: uno ogni 234.**

Cioè **−2,37 ban**, non −0,35: un fattore cento, e proprio nel punto dove
il calcolo decide se attaccare una moglie in più a un uomo che ne ha già
una. Ora il tasso lo misura il corpus (`tasso_seconde_nozze`) come l'a
priori e le frequenze dei cognomi; sul corpus dà −1,87, più prudente
della chiave stretta.

### Ma una moglie letta male non è una seconda moglie

Far pagare le seconde nozze a tutti spezza gli uomini lungo gli errori di
lettura del cognome delle mogli. Nicolangelo Lella ne ha una sola, e
l'archivio la scrive `Pizzi`, `Motta`, `Moutta`, `Moretta`, `Pelliccia`,
`Desiderio` e — una volta, nel 1837 — `Ermanda Sidri`. Sette cognomi.

Due difese, in quest'ordine:

* **stesso nome di battesimo, cognome diverso**: dentro una famiglia il
  nome del coniuge è il campo stabile e il cognome è quello che la mano
  rovina. Costa poco, come costava a tutti prima.
* **i coniugi si confrontano come schede, non come stringhe**
  (`coniugi_conciliabili`). `Ermanda` contro `Emmanuela` fa 0,56 e i nomi
  non bastano; le due schede invece hanno la stessa età, gli stessi figli
  e lo stesso marito. Quest'ultimo è il punto delicato — il marito è
  proprio la coppia che si sta giudicando — quindi la domanda si fa
  **sotto ipotesi**: «se questi due uomini sono lo stesso, le loro mogli
  sono la stessa donna?», e il coniuge in comune si concede come premessa
  invece di cercarlo. Non ricorre: al secondo livello i coniugi non si
  guardano più.

E quando le due mogli si riconoscono, il risultato **non** è una penale
ridotta: è una prova a favore, perché quei due uomini hanno la stessa
moglie. Vale meno del coniuge in comune vero (2,4 contro 1,6) perché
l'identità è dedotta e non osservata.

### La quarta categoria

`qualita` ha ora **accorpamento** accanto a impossibile, frammentazione e
sospetto: `troppi coniugi` e `cognomi inconciliabili`. Finché non
esisteva, questo intero capitolo era invisibile.

## Quello che resta da fare

* **Le code sono state percorse, non finite.** Sessanta casi arbitrati su
  12.613 di fascia grigia, dodici domande all'immagine su un centinaio di
  letture sospette. Su quel campione le fusioni proposte hanno tolto
  sedici frammentazioni senza aggiungere assurdi, e le letture ne hanno
  corrette otto: numeri piccoli e nella direzione giusta.
* **La separazione automatica non c'è, e non per dimenticanza.** Tre
  strade provate e misurate — spezzare le schede incoerenti, aggiustare
  le stime perché il controllo passi, applicare le divisioni chieste dal
  modello — e tutte e tre peggiorano il conto. Quello che manca è un modo
  di dividere **la famiglia intera** in un colpo solo: l'uomo, la moglie,
  i figli, seguendo gli atti. Finché si divide una persona per volta, il
  resto della famiglia resta a metà.
* **93 dei 108 assurdi** sono un'età dichiarata che non torna con l'anno
  di un matrimonio, e per otto di essi l'immagine ha già detto la sua. È
  la coda naturale di `verifica`, e va percorsa.
* **Le correzioni non tornano indietro entro la stessa esecuzione.** Il
  cognome raddrizzato dal padre e l'età corretta sull'immagine entrano al
  momento della scrittura, o al giro dopo; il riconoscimento di questo
  giro non li vede. Per i mestieri e le contrade il problema è chiuso — il
  vocabolario si costruisce prima — ma per i cognomi no.
* Il controllo di qualità non sa distinguere due omonimi separati bene da
  una famiglia spezzata: conta l'uno e l'altra come «coppie gemelle». È il
  limite che ha reso difficile giudicare le divisioni, e serve un
  controllo che guardi se i figli dei due nuclei si intrecciano nel tempo
  — esiste, ma come script di misura, non come parte della fase 7.
