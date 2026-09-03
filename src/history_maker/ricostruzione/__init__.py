"""La ricostruzione genealogica: dalle trascrizioni alle persone reali.

Questo package sostituisce la fase 6 con un impianto diverso, e la
differenza sta tutta in una frase: **l'albero non e' il database della
verita', e' il risultato di un ragionamento che resta ispezionabile.**

Il percorso e' quello che farebbe un genealogista:

    ATTO -> MENZIONE DOCUMENTARIA -> FATTI -> CANDIDATI ->
    RISOLUZIONE DELL'IDENTITA' -> RELAZIONI -> TIMELINE ->
    CONTROLLO DEL GRAFO -> ANOMALIE -> RICONCILIAZIONE ->
    VERIFICA MIRATA -> AGGIORNAMENTO -> NUOVA RICONCILIAZIONE

Ogni passaggio ha il suo modulo:

``modello``      le entita' e lo schema: dove vivono menzioni, fatti,
                 individui, relazioni, anomalie, decisioni.
``lettura``      dalle tabelle della fase 4 alle menzioni, con lo strato
                 di interpretazione che separa cio' che c'e' scritto da
                 cio' che probabilmente voleva dire.
``evidenza``     quanto vale un indizio, misurato sul corpus invece che
                 deciso a mano; e i pochi veti che restano assoluti.
``candidati``    chi vale la pena confrontare con chi.
``risoluzione``  la risoluzione dell'identita' e la riconciliazione
                 globale, con fusioni **e** separazioni.
``anomalie``     cio' che nel grafo non torna, con priorita' e impatto.
``registro``     l'audit trail: ogni decisione con le sue prove.
``contesto``     i fascicoli compatti per i casi che vanno a un modello.
``arbitro``      Claude sui casi ambigui.
``verifica``     Gemini sull'immagine, una domanda per volta.
``esecuzione``   l'orchestrazione, le tabelle in uscita, il rapporto.
"""

from history_maker.ricostruzione import modello  # noqa: F401

VERSIONE_ALGORITMO = "1.0.0"
