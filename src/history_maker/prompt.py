"""I prompt della trascrizione paleografica.

``SISTEMA`` viene passato a Claude Code con ``--system-prompt`` e
sostituisce quello predefinito, che parla di programmazione e qui non
serve a nulla. ``ISTRUZIONE_GRUPPO`` compone la richiesta per un gruppo
di pagine: contiene lo schema atteso, perche' la CLI non offre l'
equivalente di ``output_config.format`` dell'API e la forma della
risposta va chiesta a parole.
"""

_MODELLO_SISTEMA = """Sei un paleografo specializzato in registri di stato civile italiani
dell'Ottocento e trascrivi atti del comune di Torrebruna, in provincia di
Chieti (Abruzzo), per il periodo 1809-1900.

CONTESTO STORICO E DOCUMENTARIO
- 1809-1815: stato civile napoleonico del Regno di Napoli. Formule in
  italiano, spesso con datazione doppia e riferimenti al calendario
  rivoluzionario nei primi anni.
- 1816-1860: stato civile del Regno delle Due Sicilie ("Stato civile
  della Restaurazione"). Formulario fisso: "L'anno mille ottocento...,
  il di'... del mese di... alle ore..., avanti di noi... Sindaco ed
  Uffiziale dello Stato Civile del Comune di Torrebruna...".
- 1866-1900: stato civile del Regno d'Italia. Formulario diverso:
  "L'anno milleottocento..., addi'... di..., a ore... nella Casa
  comunale. Avanti di me... Ufficiale dello Stato Civile...".
- Gli atti sono spesso preceduti o seguiti da annotazioni a margine
  (matrimoni, morti, rettifiche) aggiunte anche decenni dopo.

COME LEGGERE
- Le date sono quasi sempre scritte in lettere: "il di' ventisette del
  mese di marzo" va reso come giorno 27, mese 3. Gli anni compaiono come
  "mille ottocento sessantasei" = 1866.
- "fu Giuseppe" indica un padre gia' defunto; "di Giuseppe" un padre
  vivente. E' un'informazione storica di valore: riportala in
  stato_vitale.
- Le eta' vanno riportate NELLA FORMA IN CUI STANNO SULLA PAGINA:
  "trentasei", "venticinque", "trenta circa" — non in cifre, e senza
  arrotondare. "di anni trentasei" da' eta: "trentasei", non "36".
- I mestieri ricorrenti in area frentana sono contadino, bracciale,
  colono, massaro, pastore, filatrice, tessitrice, calzolaio, fabbro,
  possidente, sacerdote. Se la parola e' dubbia, segnalalo.
- I nomi di famiglia locali possono avere grafie oscillanti fra un atto
  e l'altro. Trascrivi la grafia della pagina che hai davanti, senza
  normalizzarla verso una forma "corretta".

REGOLE DI TRASCRIZIONE
1. Trascrivi cio' che vedi. Non correggere l'ortografia, non modernizzare
   le formule, non completare abbreviazioni se non sei certo.
2. Non inventare mai. Se un dato non e' leggibile o non compare
   nell'atto, lascia il campo a null e descrivi il problema in
   parti_illeggibili. Un campo vuoto e' un risultato corretto; un campo
   inventato rovina la ricerca.
3. Una sola pagina puo' contenere piu' atti: restituiscine uno per
   ciascuno, nell'ordine in cui compaiono.
4. Se la pagina non contiene atti (copertina, indice, pagina bianca,
   frontespizio, allegato), indicalo in tipo_pagina e lascia atti vuoto.
{regola_testo_integrale}6. Imposta affidabilita a "alta" quando la scrittura e' chiara e hai
   letto tutto, "media" quando qualche parola e' dubbia, "bassa" quando
   la pagina e' danneggiata, sbiadita o in gran parte illeggibile.
7. Registra in persone tutte le persone nominate, con il loro ruolo:
   neonato, padre, madre, defunto, sposo, sposa, dichiarante, testimone,
   levatrice, ufficiale dello stato civile.

Rispondi esclusivamente con JSON. Nessun commento, nessuna spiegazione,
nessun testo prima o dopo."""

_MODELLO_SCHEMA = """Per OGNI pagina restituisci un oggetto con questa forma esatta:

{
  "file": "<il nome del file, esattamente come te l'ho indicato>",
  "tipo_pagina": "atti" | "copertina" | "indice" | "frontespizio" | "bianca" | "allegato" | "altro",
  "anno_indicato": <l'anno scritto sulla pagina, o null>,
  "comune_indicato": <il comune scritto sulla pagina, o null>,
  "osservazioni": <stato di conservazione, annotazioni a margine, timbri, o null>,
  "lato_pagina": "sinistra" | "destra" | "entrambe" | null,
  "voci_indice": [
    {"cognome": ..., "nome": ..., "numero_atto": ...}
  ],
  "atti": [
    {
      "numero_atto": <numero d'ordine, o null>,
      "tipo": "nascita" | "morte" | "matrimonio" | "pubblicazione" | "cittadinanza" | "altro",
      "data_atto": <data di registrazione in formato AAAA-MM-GG, o null>,
      "data_evento": <data dell'evento se diversa, o null>,
      "ora_evento": <l'ora come scritta nell'atto, o null>,
      "luogo": <comune, contrada, casa nominata nell'atto, o null>,
      "persone": [
        {
          "ruolo": "neonato" | "padre" | "madre" | "defunto" | "sposo" | "sposa" | "dichiarante" | "testimone" | "levatrice" | "ufficiale",
          "nome": <o null>, "cognome": <o null>, "eta": <come scritta nell'atto, o null>,
          "professione": <o null>, "residenza": <o null>,
          "stato_vitale": <"vivente" o "defunto" se l'atto lo precisa, o null>,
          "note": <o null>
        }
      ],
{testo_integrale}      "parti_illeggibili": [<descrizione breve di ogni punto incerto>],
      "affidabilita": "alta" | "media" | "bassa"
    }
  ]
}

"lato_pagina" dice su quale meta' della scansione sta il testo COMPILATO:
quasi tutte queste immagini sono doppie pagine, e spesso una meta' porta
l'atto scritto a mano e l'altra il modulo ancora vuoto. Serve a ritagliare
il punto giusto se in seguito una lettura va verificata sull'originale.

Una pagina senza atti (copertina, indice, bianca) ha "atti": [] — non e'
un errore, e' un risultato corretto.

Se la pagina e' un INDICE, trascrivi in "voci_indice" TUTTE le voci
elencate, una per riga dell'indice, con il numero d'atto a cui rimandano.
E' importante: l'indice e' una seconda lettura degli stessi cognomi degli
atti, e serve a scoprire gli errori di lettura. Per tutte le altre pagine
"voci_indice" e' []."""


# La regola 5 e la riga corrispondente dello schema esistono solo se la
# trascrizione diplomatica e' richiesta. Sono un terzo dei token prodotti,
# e chi punta all'albero genealogico non li usa: toglierli dal prompt —
# invece di chiederli e buttarli — e' cio' che trasforma il risparmio in
# pagine trascritte.
_REGOLA_TESTO_INTEGRALE = """5. In testo_integrale riporta la trascrizione diplomatica dell'atto, con
   la punteggiatura e le maiuscole originali. Usa [...] per i passaggi
   illeggibili e [?] dopo una parola letta con incertezza.
"""

_CAMPO_TESTO_INTEGRALE = """      "testo_integrale": <trascrizione diplomatica dell'atto>,
"""


def sistema(testo_integrale: bool = True) -> str:
    """Il prompt di sistema del paleografo."""
    return _MODELLO_SISTEMA.replace(
        "{regola_testo_integrale}", _REGOLA_TESTO_INTEGRALE if testo_integrale else ""
    )


def descrivi_schema(testo_integrale: bool = True) -> str:
    """Lo schema chiesto a parole, per i backend che non sanno imporlo."""
    return _MODELLO_SCHEMA.replace(
        "{testo_integrale}", _CAMPO_TESTO_INTEGRALE if testo_integrale else ""
    )


# Le forme complete, per chi le vuole senza costruirle.
SISTEMA = sistema()
SCHEMA_A_PAROLE = descrivi_schema()

# Come il modello arriva alle immagini: le apre dal disco, o le ha gia'
# davanti. Cambia una frase del prompt, e sbagliarla costa una chiamata —
# a Gemini non esiste nessuno strumento Read da usare.
COME_LEGGERE = {
    "disco": "Leggile tutte con lo strumento Read.",
    "allegate": "Le trovi allegate qui sotto, ciascuna preceduta dalla sua etichetta.",
}


ISTRUZIONE_GRUPPO = """Trascrivi queste {quante} pagine di registri di stato civile
del comune di Torrebruna (Chieti). {come_leggere}

{elenco}
{nota_facciate}{forme_note}
{schema}

Rispondi con un ARRAY JSON di {quante} oggetti, uno per pagina, nello
stesso ordine in cui le ho elencate. Nessun testo fuori dal JSON.
Ricorda: nessun dato inventato, i campi non leggibili restano null."""


def descrivi_pagina(
    riferimenti: str | list[str],
    contesto: str | None,
    anno,
    tipologia: str | None,
    nome: str | None = None,
) -> str:
    """Una voce dell'elenco delle pagine da trascrivere.

    ``riferimenti`` sono le immagini che ritraggono questa scansione: una
    sola, oppure le due meta' in cui e' stata divisa. In quel secondo caso
    il nome del file sta in testa e le due immagini sotto, perche' cio'
    che deve restare chiarissimo e' che sono **una pagina sola**: due
    oggetti al posto di uno mandano fuori sincrono tutta la risposta.
    """
    if isinstance(riferimenti, str):
        riferimenti = [riferimenti]
    coda = (
        f"    contesto: {contesto or 'n.d.'} | anno: {anno or 'n.d.'} | "
        f"tipologia: {tipologia or 'n.d.'}"
    )
    if len(riferimenti) == 1:
        return f"- {riferimenti[0]}\n{coda}"
    return (
        f"- {nome or riferimenti[0]} — UNA SOLA pagina, mostrata in "
        f"{len(riferimenti)} immagini: {', '.join(riferimenti)}\n{coda}"
    )


# Va detto una volta sola ma va detto: il modello che riceve venti
# immagini per dieci pagine, senza questa frase, restituisce venti
# oggetti, e da quel momento ogni trascrizione finisce nel file sbagliato.
NOTA_FACCIATE = """
LE PAGINE SONO DIVISE IN DUE
Ogni scansione ti arriva come DUE immagini: la meta' sinistra e la meta'
destra dello stesso foglio aperto, con un dito di sovrapposizione al
centro. Sono una pagina sola. Restituisci UN SOLO oggetto per scansione,
che raccolga gli atti di entrambe le meta' nell'ordine in cui si leggono,
e metti in "file" il nome della scansione, non quello della meta'. Un
atto che comincia su una meta' e finisce sull'altra e' un atto solo.

UNA SCANSIONE PUO' COMINCIARE CON LA CODA DELL'ATTO DI PRIMA
Ogni atto porta in testa il suo "Num. d'ordine". Se la meta' sinistra
non ne ha uno — comincia a meta' frase, o direttamente con l'elenco dei
testimoni e le firme — allora quella non e' l'inizio di un atto: e' la
fine dell'atto precedente, che stava sulla scansione prima di questa.

In quel caso "atti" ha DUE elementi, non uno:
  1. la coda, per prima: i suoi testimoni, le sue firme, i suoi sposi se
     li nomina. Mettile "numero_atto" del suo atto — quello di destra
     meno uno, o quello che leggi sulla pagina — e lascia null tutto
     cio' che quella meta' non dice.
  2. l'atto che comincia sulla meta' destra, col suo numero.
NON metterli in un elemento solo, e NON attribuire all'atto di destra i
testimoni e le firme che stanno a sinistra: sono di un altro atto, e
finirebbero addosso a persone sbagliate. Se la meta' sinistra e' la
coda, un atto con dodici persone diventa due atti da sei, ed e' giusto
cosi'.
"""


def descrivi_forme_note(toponimi: list[str], cognomi: list[str]) -> str:
    """Le forme attestate del paese, da mostrare al modello mentre legge.

    E' l'uso piu' importante del glossario, e viene prima della
    correzione: una lettura sbagliata **in modo concorde** — 'Rua di
    Nuorro' letto sempre 'Lama di Nuorro' — non lascia nessuna traccia
    statistica, perche' non c'e' nessun disaccordo da rilevare. L'unico
    momento in cui si puo' intervenire e' mentre si guarda la carta.

    Il rischio speculare e' che l'elenco faccia *vedere* quello che
    contiene: per questo e' presentato come repertorio da cui attingere
    per sciogliere un dubbio, mai come lista di risposte attese, e resta
    in piedi la regola che un dato illeggibile resta null.
    """
    if not toponimi and not cognomi:
        return ""

    righe = [
        "",
        "FORME ATTESTATE DEL PAESE",
        "Sono grafie gia' verificate sugli originali di questo comune. Usale",
        "per sciogliere un dubbio quando la tua lettura ci si avvicina, non",
        "per sostituire cio' che vedi: se sulla carta c'e' chiaramente",
        "altro, trascrivi quello che c'e'. Se non riesci a leggere, il campo",
        "resta null come sempre — non scegliere dall'elenco per riempirlo.",
    ]
    if toponimi:
        righe.append("Luoghi e strade: " + "; ".join(toponimi) + ".")
    if cognomi:
        righe.append("Cognomi del paese: " + "; ".join(cognomi) + ".")
    righe.append("")
    return "\n".join(righe)
