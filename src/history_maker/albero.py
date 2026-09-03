"""Le domande che si fanno a un albero genealogico.

Sta separato da :mod:`genealogia` — che le tabelle le costruisce — perche'
sono due mestieri diversi: quello si esegue una volta e ci mette minuti,
questo risponde a ogni clic e deve costare millisecondi.

Tutto quello che c'e' qui e' in sola lettura. L'applicazione non modifica
mai l'archivio: se una parentela e' sbagliata si corregge la fonte — il
glossario, la trascrizione — e si rifa' la fase 6. Un archivio che si
lascia correggere a mano dall'interfaccia diventa, dopo un mese, un
archivio di cui nessuno sa piu' da dove venga cosa.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# Quante generazioni servire in un colpo solo. Il limite non e' tecnico:
# un albero di sei generazioni ha gia' piu' nodi di quanti se ne leggano
# su uno schermo, e il modo giusto di andare piu' in la' e' spostarsi su
# un'altra persona, che e' esattamente cio' che l'applicazione fa.
GENERAZIONI_MASSIME = 6


def apri(percorso) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _dizionario(riga: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(riga) if riga is not None else None


def _lista(righe) -> list[dict[str, Any]]:
    return [dict(r) for r in righe]


# --- l'anagrafe -------------------------------------------------------------

CAMPI = """
    i.id, i.nome, i.cognome, i.sesso, i.anno_nascita, i.nascita_origine,
    i.anno_morte, i.morta_entro, i.anno_primo, i.anno_ultimo,
    i.professioni, i.residenze, i.contrade, i.varianti_nome,
    i.varianti_cognome, i.menzioni, i.menzioni_incerte, i.fondata_su
"""

# Chi ha un posto nell'albero, e chi invece l'archivio conserva soltanto.
#
# I registri nominano molta piu' gente di quanta ne leghino: l'ufficiale
# di stato civile che firma seimila atti, i testimoni chiamati in
# municipio, il vicino che va a dichiarare una morte, la levatrice. Sono
# un quarto delle persone dell'archivio e nell'albero non hanno un posto
# — nessun legame parte da loro, nessuno ci arriva — ma portano nomi
# comunissimi, e in cima ai risultati di una ricerca finiscono per
# nascondere proprio la persona che si stava cercando.
#
# La riga di confine e' la parentela dichiarata da un atto: figurare come
# padre, madre, figlio, figlia o coniuge di qualcuno. Chi resta fuori non
# viene cancellato — continua a comparire, con il suo ruolo e il suo
# nome, fra chi c'era in ogni atto che lo nomina — ma non si apre e non
# si cerca, perche' aprirlo non porterebbe da nessuna parte.
NELL_ALBERO = """(
        EXISTS (SELECT 1 FROM legami l
                 WHERE l.figlio = i.id OR l.genitore = i.id)
     OR EXISTS (SELECT 1 FROM unioni u
                 WHERE u.marito = i.id OR u.moglie = i.id)
    )"""


def _query_fts(parole: list[str]) -> str:
    """La ricerca full-text, con i nomi composti attaccati.

    Ogni parola diventa un prefisso: 'pell col' trova 'Pelliccia
    Colella'. E' il modo in cui si cerca davvero un nome che non si
    ricorda per intero.

    Ma in questo paese i nomi doppi si scrivono in due modi, e nello
    stesso registro: 'Domenico Antonio' e 'Domenicantonio', 'Nicola
    Angelo' e 'Nicolangelo', 'Pasquale Antonio' e 'Pasquantonio'. Sono la
    stessa persona e per l'indice sono parole diverse — 'domenicantonio'
    non comincia per 'domenico', quindi nemmeno la ricerca per prefisso
    li fa incontrare.

    Il caso vero: Domenicantonio Lella e Clementina Petta hanno sette
    figli fra il 1885 e il 1900, e cercando 'Domenico Antonio Lella' non
    usciva nessuno dei due — usciva un omonimo da una menzione sola, e
    la famiglia sembrava non esistere.

    Quindi alla ricerca normale si affianca, in OR, quella con le parole
    accostate: sia unite di netto, sia togliendo la vocale finale della
    prima, che e' come la lingua le fonde davvero.
    """
    def gruppo(termini: list[str]) -> str:
        return "(" + " AND ".join(f'"{t}"*' for t in termini) + ")"

    alternative = [gruppo(parole)]
    for indice in range(len(parole) - 1):
        prima, dopo = parole[indice], parole[indice + 1]
        for unito in {prima + dopo, prima[:-1] + dopo}:
            if unito in (prima, dopo):
                continue
            alternative.append(
                gruppo(parole[:indice] + [unito] + parole[indice + 2:])
            )
    return " OR ".join(alternative)


def cerca(conn: sqlite3.Connection, testo: str, limite: int = 40) -> dict:
    """Cerca una persona per nome, cognome o entrambi.

    La ricerca passa per l'indice full-text, che comprende **anche le
    grafie originali**: chi cerca 'Giyseppe' — perche' l'ha letto cosi'
    su una pagina — trova Giuseppe. E' il punto in cui la normalizzazione
    dei nomi si ripaga: senza, la persona esisterebbe ma sotto un nome
    che nessuno pensa di digitare.

    Risponde soltanto con chi sta nell'albero (:data:`NELL_ALBERO`).
    ``fuori`` conta quanti omonimi la ricerca ha lasciato fuori: non e'
    un dettaglio da nascondere, perche' chi cerca un nome che l'archivio
    conosce solo come testimone deve capire che la pagina c'e' e non e'
    la ricerca a essere rotta.
    """
    parole = [p for p in testo.replace('"', " ").split() if len(p) > 1]
    if not parole:
        return {"risultati": [], "fuori": 0}

    query = _query_fts(parole)
    righe = conn.execute(
        f"""
        SELECT {CAMPI},
               (SELECT COUNT(*) FROM legami WHERE genitore = i.id) AS figli,
               (SELECT COUNT(*) FROM legami WHERE figlio = i.id) AS genitori
          FROM individui_fts f
          JOIN individui i ON i.id = f.rowid
         WHERE individui_fts MATCH ? AND {NELL_ALBERO}
         ORDER BY bm25(individui_fts) +
                  -- Quanto quella scheda e' documentata, in negativo
                  -- perche' bm25 cresce all'ingiu'. Senza, tre schede
                  -- 'Filippo Lella' escono in ordine di indice e la
                  -- prima e' il frammento da una menzione sola: chi
                  -- cerca ci clicca sopra, vede un albero con un figlio
                  -- e conclude che l'archivio non sa niente di lui,
                  -- mentre il Filippo vero — tredici menzioni, otto
                  -- figli — sta terzo e non lo guarda nessuno. Il
                  -- logaritmo tiene la cosa proporzionata: fra una e
                  -- dieci menzioni la differenza pesa, fra cento e
                  -- centodieci quasi niente. I pesi sono tarati sullo
                  -- scarto vero di bm25 fra due schede dallo stesso
                  -- nome, che nell'archivio e' meno di due punti: sotto
                  -- questi valori il frammento tornerebbe davanti.
                  -1.5 * LOG(1 + i.menzioni)
                  -3.0 * LOG(1 + (SELECT COUNT(*) FROM legami
                                   WHERE genitore = i.id OR figlio = i.id))
         LIMIT ?
        """,
        (query, limite),
    )
    fuori = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM individui_fts f
          JOIN individui i ON i.id = f.rowid
         WHERE individui_fts MATCH ? AND NOT {NELL_ALBERO}
        """,
        (query,),
    ).fetchone()[0]
    return {"risultati": _lista(righe), "fuori": fuori}


def persona(conn: sqlite3.Connection, individuo: int) -> dict | None:
    return _dizionario(
        conn.execute(
            f"SELECT {CAMPI}, {NELL_ALBERO} AS nell_albero "
            "FROM individui i WHERE i.id = ?",
            (individuo,),
        ).fetchone()
    )


# --- i legami ---------------------------------------------------------------

def genitori(conn: sqlite3.Connection, individuo: int) -> list[dict]:
    return _lista(conn.execute(
        f"SELECT {CAMPI}, l.tipo, l.atto FROM legami l JOIN individui i ON i.id = l.genitore "
        "WHERE l.figlio = ? ORDER BY l.tipo DESC",
        (individuo,),
    ))


def figli(conn: sqlite3.Connection, individuo: int) -> list[dict]:
    """I figli, in ordine di nascita, con l'altro genitore accanto.

    L'altro genitore serve a raggruppare i figli per nucleo: un uomo
    vedovo che si risposa ha figli di due madri, e mostrarli in un
    elenco unico nasconderebbe proprio la cosa che conta.
    """
    return _lista(conn.execute(
        f"""
        SELECT {CAMPI}, l.tipo, l.atto,
               (SELECT g.genitore FROM legami g
                 WHERE g.figlio = i.id AND g.genitore <> ?) AS altro_genitore
          FROM legami l JOIN individui i ON i.id = l.figlio
         WHERE l.genitore = ?
         ORDER BY COALESCE(i.anno_nascita, i.anno_primo), i.id
        """,
        (individuo, individuo),
    ))


def coniugi(conn: sqlite3.Connection, individuo: int) -> list[dict]:
    return _lista(conn.execute(
        f"""
        SELECT {CAMPI}, u.anno AS anno_unione, u.atto AS atto_unione, u.origine
          FROM unioni u
          JOIN individui i ON i.id = CASE WHEN u.marito = ? THEN u.moglie ELSE u.marito END
         WHERE u.marito = ? OR u.moglie = ?
         ORDER BY u.anno
        """,
        (individuo, individuo, individuo),
    ))


def fratelli(conn: sqlite3.Connection, individuo: int) -> list[dict]:
    """Chi ha almeno un genitore in comune.

    ``pieno`` distingue i fratelli dai fratellastri: due genitori in
    comune o uno solo. In un paese dove si restava vedovi presto la
    differenza e' frequente e vale la pena mostrarla.
    """
    return _lista(conn.execute(
        f"""
        SELECT {CAMPI}, COUNT(*) AS comuni,
               (COUNT(*) = (SELECT COUNT(*) FROM legami WHERE figlio = ?)) AS pieno
          FROM legami mio
          JOIN legami suo ON suo.genitore = mio.genitore AND suo.figlio <> mio.figlio
          JOIN individui i ON i.id = suo.figlio
         WHERE mio.figlio = ?
         GROUP BY i.id
         ORDER BY COALESCE(i.anno_nascita, i.anno_primo), i.id
        """,
        (individuo, individuo),
    ))


# --- il dossier -------------------------------------------------------------

def menzioni(conn: sqlite3.Connection, individuo: int) -> list[dict]:
    """Ogni riga di registro che nomina questa persona, con il suo atto.

    E' la parte piu' importante della scheda, perche' e' quella che si
    puo' verificare: porta il numero d'atto, il registro, l'immagine
    della pagina e la trascrizione integrale. Tutto il resto
    dell'archivio e' un riordino di queste righe, e chi ne dubita deve
    poter tornare qui e guardare la pagina.
    """
    return _lista(conn.execute(
        """
        SELECT p.id, p.ruolo, p.nome_letto, p.cognome_letto, p.eta,
               p.professione, p.residenza, p.stato_vitale, p.note,
               p.incerto, p.cognome_origine, m.certa,
               a.id AS atto, a.tipo, a.anno, a.numero_atto, a.data_atto,
               a.data_evento, a.ora_evento, a.luogo, a.via, a.registro,
               a.immagine, a.affidabilita, a.incertezze, a.testo_integrale
          FROM menzioni m
          JOIN persone p ON p.id = m.persona
          JOIN atti a ON a.id = p.atto
         WHERE m.individuo = ?
         ORDER BY a.anno, a.numero_atto, p.id
        """,
        (individuo,),
    ))


def compagni_di_atto(conn: sqlite3.Connection, atto: int, escluso: int) -> list[dict]:
    """Chi altro compare nello stesso atto, e chi e' diventato nell'archivio.

    Serve a rendere navigabile anche cio' che non e' parentela: il
    testimone di un matrimonio, la levatrice di una nascita, il vicino
    che denuncia una morte. Sono i legami sociali del paese, e in un
    archivio di seimila atti sono la trama che regge tutto il resto.
    """
    return _lista(conn.execute(
        f"""
        SELECT p.ruolo, p.nome_letto, p.cognome_letto, p.eta, p.professione,
               m.individuo, i.nome, i.cognome, i.anno_nascita, i.anno_morte,
               i.menzioni AS quante, {NELL_ALBERO} AS nell_albero
          FROM persone p
          LEFT JOIN menzioni m ON m.persona = p.id
          LEFT JOIN individui i ON i.id = m.individuo
         WHERE p.atto = ? AND COALESCE(m.individuo, -1) <> ?
         ORDER BY p.id
        """,
        (atto, escluso),
    ))


def scheda(conn: sqlite3.Connection, individuo: int) -> dict | None:
    """Tutto quello che l'archivio sa di una persona."""
    chi = persona(conn, individuo)
    if chi is None:
        return None
    atti = menzioni(conn, individuo)
    return {
        "persona": chi,
        "genitori": genitori(conn, individuo),
        "coniugi": coniugi(conn, individuo),
        "figli": figli(conn, individuo),
        "fratelli": fratelli(conn, individuo),
        "menzioni": atti,
        "conoscenti": [
            {"atto": atto["atto"], "anno": atto["anno"], "tipo": atto["tipo"],
             "persone": compagni_di_atto(conn, atto["atto"], individuo)}
            for atto in atti
        ],
    }


# --- l'albero ---------------------------------------------------------------

def albero(
    conn: sqlite3.Connection, individuo: int, su: int = 3, giu: int = 3
) -> dict:
    """L'albero attorno a una persona: gli avi sopra, la discendenza sotto.

    Rende un grafo — nodi e archi — e non una struttura ad albero, ed e'
    una scelta e non una comodita'. Nelle famiglie di un paese piccolo i
    rami si richiudono: due cugini si sposano, e da quel momento un
    antenato compare due volte. Una struttura ad albero lo duplicherebbe,
    e chi guarda vedrebbe due persone dove ce n'e' una. Un grafo dice la
    verita': quel nodo ha due discendenze che portano a lui.

    ``generazione`` e' negativa verso gli avi e positiva verso i figli,
    con lo zero sulla persona da cui si e' partiti. Serve
    all'impaginazione, e va presa come un'indicazione: quando i rami si
    richiudono una stessa persona puo' stare su due livelli, e allora
    vince quello piu' vicino alla radice.
    """
    su = max(0, min(su, GENERAZIONI_MASSIME))
    giu = max(0, min(giu, GENERAZIONI_MASSIME))

    livelli: dict[int, int] = {individuo: 0}
    archi: dict[tuple[int, int, str], dict] = {}

    _risali(conn, [individuo], su, livelli, archi)
    _scendi(conn, [individuo], giu, livelli, archi)

    # L'altro genitore di ogni figlio che si e' aggiunto scendendo.
    # Senza, la discendenza si vede a meta': il nipote compare appeso
    # alla sola figlia, e chi guarda non ha modo di capire da quale
    # coppia venga — appare, per usare le parole giuste, "totalmente a
    # caso". Pietro Marianacci nell'albero di Filippo Lella era questo:
    # bisnipote vero, per parte di madre, con il padre Marianacci fuori
    # dal disegno e quindi senza niente che lo spiegasse.
    _aggiungi_altri_genitori(conn, livelli, archi)

    # I coniugi di chiunque sia nell'albero: senza, i nuclei familiari si
    # vedrebbero a meta'. Stanno sulla generazione del loro coniuge.
    _aggiungi_coniugi(conn, livelli, archi)

    nodi = _carica_nodi(conn, list(livelli), livelli)
    return {
        "radice": individuo,
        "nodi": nodi,
        "archi": list(archi.values()),
    }


def _risali(
    conn: sqlite3.Connection, fronte: list[int], quanti: int,
    livelli: dict[int, int], archi: dict,
) -> None:
    for _ in range(quanti):
        if not fronte:
            return
        segnaposti = ",".join("?" * len(fronte))
        prossimi = []
        for riga in conn.execute(
            f"SELECT figlio, genitore, tipo, atto FROM legami WHERE figlio IN ({segnaposti})",
            fronte,
        ):
            chiave = (riga["genitore"], riga["figlio"], "filiazione")
            archi.setdefault(chiave, {
                "da": riga["genitore"], "a": riga["figlio"],
                "tipo": "filiazione", "ruolo": riga["tipo"], "atto": riga["atto"],
            })
            livello = livelli[riga["figlio"]] - 1
            if riga["genitore"] not in livelli:
                livelli[riga["genitore"]] = livello
                prossimi.append(riga["genitore"])
            else:
                livelli[riga["genitore"]] = max(livelli[riga["genitore"]], livello)
        fronte = prossimi


def _scendi(
    conn: sqlite3.Connection, fronte: list[int], quanti: int,
    livelli: dict[int, int], archi: dict,
) -> None:
    for _ in range(quanti):
        if not fronte:
            return
        segnaposti = ",".join("?" * len(fronte))
        prossimi = []
        for riga in conn.execute(
            f"SELECT figlio, genitore, tipo, atto FROM legami WHERE genitore IN ({segnaposti})",
            fronte,
        ):
            chiave = (riga["genitore"], riga["figlio"], "filiazione")
            archi.setdefault(chiave, {
                "da": riga["genitore"], "a": riga["figlio"],
                "tipo": "filiazione", "ruolo": riga["tipo"], "atto": riga["atto"],
            })
            livello = livelli[riga["genitore"]] + 1
            if riga["figlio"] not in livelli:
                livelli[riga["figlio"]] = livello
                prossimi.append(riga["figlio"])
            else:
                livelli[riga["figlio"]] = min(livelli[riga["figlio"]], livello)
        fronte = prossimi


def _aggiungi_altri_genitori(
    conn: sqlite3.Connection, livelli: dict[int, int], archi: dict
) -> None:
    """Per ogni figlio nell'albero, il genitore che ancora non c'e'.

    Un figlio ha due genitori e va mostrato appeso a tutti e due: e' la
    coppia a spiegare da dove viene, non il singolo. Il genitore che si
    aggiunge qui sta una generazione sopra il figlio — non sopra la
    persona da cui si e' partiti — e nessuno risale oltre: si aggiunge
    lui e basta, altrimenti da ogni nuora entrerebbe nell'albero tutta
    la sua famiglia d'origine.
    """
    figli = [
        chi for chi, livello in livelli.items() if livello > 0
    ]
    if not figli:
        return
    segnaposti = ",".join("?" * len(figli))
    for riga in conn.execute(
        f"SELECT figlio, genitore, tipo, atto FROM legami "
        f"WHERE figlio IN ({segnaposti})",
        figli,
    ).fetchall():
        archi.setdefault((riga["genitore"], riga["figlio"], "filiazione"), {
            "da": riga["genitore"], "a": riga["figlio"],
            "tipo": "filiazione", "ruolo": riga["tipo"], "atto": riga["atto"],
        })
        if riga["genitore"] not in livelli:
            livelli[riga["genitore"]] = livelli[riga["figlio"]] - 1


def _aggiungi_coniugi(conn: sqlite3.Connection, livelli: dict[int, int], archi: dict) -> None:
    dentro = list(livelli)
    segnaposti = ",".join("?" * len(dentro))
    unioni = conn.execute(
        f"""SELECT marito, moglie, anno, atto, origine FROM unioni
             WHERE marito IN ({segnaposti}) OR moglie IN ({segnaposti})""",
        dentro + dentro,
    ).fetchall()

    # Chi ha un legame di filiazione dentro questo albero e' **ancorato**:
    # la sua generazione viene da li' e non si tocca. Chi non ce l'ha sta
    # nell'albero solo perche' ha sposato qualcuno, e allora la sua
    # generazione e' quella del coniuge — anche se un giro precedente
    # gliene aveva data un'altra.
    #
    # Senza questa distinzione capita cosi': Egidio Pacilli risulta
    # sposato a due Marianna Lella, la figlia e la nipote di Filippo, e
    # finisce sulla generazione della nipote. Da li' non compare piu'
    # accanto alla moglie che si stava guardando, e chi consulta conclude
    # che il marito non c'e'.
    ancorati = {
        chi
        for arco in archi.values() if arco["tipo"] == "filiazione"
        for chi in (arco["da"], arco["a"])
    }

    for riga in unioni:
        marito, moglie = riga["marito"], riga["moglie"]
        if marito is None or moglie is None:
            continue
        archi.setdefault((marito, moglie, "unione"), {
            "da": marito, "a": moglie, "tipo": "unione",
            "anno": riga["anno"], "atto": riga["atto"], "origine": riga["origine"],
        })
        for uno, altro in ((marito, moglie), (moglie, marito)):
            if uno not in livelli:
                continue
            if altro not in livelli or (
                altro not in ancorati and livelli[altro] != livelli[uno]
            ):
                livelli[altro] = livelli[uno]


def _carica_nodi(
    conn: sqlite3.Connection, identificatori: list[int], livelli: dict[int, int]
) -> list[dict]:
    if not identificatori:
        return []
    segnaposti = ",".join("?" * len(identificatori))
    nodi = []
    for riga in conn.execute(
        f"""
        SELECT {CAMPI},
               (SELECT COUNT(*) FROM legami WHERE genitore = i.id) AS quanti_figli,
               (SELECT COUNT(*) FROM legami WHERE figlio = i.id) AS quanti_genitori,
               (SELECT COUNT(*) FROM unioni WHERE marito = i.id OR moglie = i.id) AS quante_unioni
          FROM individui i WHERE i.id IN ({segnaposti})
        """,
        identificatori,
    ):
        nodo = dict(riga)
        nodo["generazione"] = livelli[riga["id"]]
        # Quanto altro c'e' da vedere se si va su questa persona: e' cio'
        # che dice se valga la pena cliccarla.
        nodo["espandibile"] = (
            nodo["quanti_figli"] + nodo["quanti_genitori"] + nodo["quante_unioni"]
        )
        nodi.append(nodo)
    return nodi


# --- il quadro d'insieme ----------------------------------------------------

def statistiche(conn: sqlite3.Connection) -> dict:
    def uno(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "individui": uno("SELECT COUNT(*) FROM individui"),
        "individui_albero": uno(
            f"SELECT COUNT(*) FROM individui i WHERE {NELL_ALBERO}"
        ),
        "menzioni": uno("SELECT COUNT(*) FROM menzioni"),
        "atti": uno("SELECT COUNT(*) FROM atti"),
        "legami": uno("SELECT COUNT(*) FROM legami"),
        "unioni": uno("SELECT COUNT(*) FROM unioni"),
        "unioni_documentate": uno("SELECT COUNT(*) FROM unioni WHERE origine='matrimonio'"),
        "anno_min": uno("SELECT MIN(anno) FROM atti"),
        "anno_max": uno("SELECT MAX(anno) FROM atti"),
        "cognomi": _lista(conn.execute(
            "SELECT cognome, COUNT(*) AS quante FROM individui "
            "WHERE cognome IS NOT NULL GROUP BY cognome "
            "ORDER BY quante DESC LIMIT 30"
        )),
        "famiglie": _lista(conn.execute(
            """
            SELECT u.id, u.anno, u.origine,
                   p.id AS padre, p.nome AS nome_padre, p.cognome AS cognome_padre,
                   m.id AS madre, m.nome AS nome_madre, m.cognome AS cognome_madre,
                   COUNT(DISTINCT lp.figlio) AS figli
              FROM unioni u
              JOIN individui p ON p.id = u.marito
              JOIN individui m ON m.id = u.moglie
              JOIN legami lp ON lp.genitore = u.marito
              JOIN legami lm ON lm.genitore = u.moglie AND lm.figlio = lp.figlio
             GROUP BY u.id ORDER BY figli DESC LIMIT 40
            """
        )),
    }
