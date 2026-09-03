"""Il ciclo intero, fino a quando non c'e' piu' niente da fare.

'rileggi' e 'arbitra' rispondono a una domanda ciascuno: la pagina dice
cosa c'e' scritto, il ragionamento dice se due schede sono la stessa
persona. Nessuno dei due, da solo, chiude il cerchio — e non e' un
dettaglio, e' la ragione per cui una correzione isolata lascia scoperto
esattamente il campo accanto.

Il caso che ha reso questo modulo necessario: una scheda univa un neonato
del 1868 con un quindicenne morto nel 1869. 'rileggi' aveva verificato
**l'eta'** (il campo su cui l'anomalia era stata aperta) e aveva confermato
che la lettura era giusta — la fusione andava sciolta. Ma il cognome del
quindicenne, 'Morella', non era mai stato messo in dubbio da nessuna
anomalia, e restava li' — mentre il glossario proposto diceva gia', da
prima, che 'Morella' e 'Moretta' sono la stessa forma al 96%. Sciogliere
la fusione senza richiudere il cerchio lascia esattamente questo genere
di scorie: la persona giusta, con ancora addosso una grafia sbagliata che
nessuno tornera' a guardare.

Il ciclo
--------

Ogni giro fa quattro cose, nell'ordine in cui producono l'una il materiale
per l'altra:

1. **ricostruisci** — applica le decisioni prese nel giro precedente
   (correzioni di lettura, fusioni, separazioni) e ricalcola le anomalie
   da capo. Senza questo passo il giro successivo lavorerebbe su un grafo
   vecchio, e le correzioni si accumulerebbero senza mai vedersi a
   vicenda.
2. **rileggi** — le pagine con un dubbio che l'immagine puo' sciogliere:
   grafie, eta', date. Consuma la quota di lettura (Gemini, di norma).
3. **arbitra** — i casi di identita' che il calcolo non decide da solo:
   duplicati, omonimi, schede troppo grosse. Consuma la quota di
   ragionamento (Claude, se configurato cosi' — vedi il modulo
   ``cli``, che permette di scegliere un motore diverso da quello delle
   trascrizioni proprio per questo).
4. **si ferma da solo** quando un giro non produce ne' correzioni ne'
   decisioni: non c'e' piu' niente che i due motori possano fare, e
   continuare vorrebbe dire ripetere le stesse domande già risposte.

Cosa non fa
-----------

Non inventa nuove pagine da rileggere oltre a quelle che un'anomalia ha
gia' segnalato: la selezione resta quella di ``rilettura.casi`` e
``arbitro.casi``, che guardano la coda dei dubbi. Un secolo di registri
sono settemila atti; rileggerli tutti a prescindere costerebbe giorni di
quota per riottenere in gran parte cio' che c'e' gia'. Il ciclo estende
**quante volte** si guarda la coda, non **quanto** si guarda ogni volta.

Non decide da solo quando fermarsi per sempre: la convergenza — un giro
senza correzioni ne' decisioni — e' un buon segnale, non una certezza. Un
archivio di questa taglia puo' benissimo avere fasce di dubbi che nessuno
dei due motori scioglie (l'identita' non si legge su una pagina, un
paleografo non basta), e quelli restano in coda apertamente, con la loro
priorita', per chi vuole guardarli a mano.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Un tetto ai giri, non una previsione di quanti ne servano. Serve alla
# stessa cosa di ogni altro tetto in questo progetto: impedire che un
# errore di battitura o un ciclo che non converge mai giri per ore senza
# che nessuno se ne accorga.
GIRI_MASSIMI = 20

# Quante pagine e quanti casi per giro, di default. Piccoli apposta:
# un giro corto che si ripete e' piu' facile da interrompere, da
# riprendere e da leggere nel rapporto di uno lungo che fa tutto insieme.
PAGINE_PER_GIRO = 40
CASI_PER_GIRO = 15


@dataclasses.dataclass
class Giro:
    """Cosa e' successo in un giro del ciclo, per il rapporto e per la
    decisione se fermarsi."""

    numero: int
    schede_prima: int
    schede_dopo: int
    correzioni_lettura: int = 0
    pagine_esaminate: int = 0
    conflitti_col_contesto: int = 0
    pagine_fallite: int = 0
    quota_lettura_esaurita: bool = False
    decisioni_arbitro: int = 0
    casi_sottoposti: int = 0
    divisioni_proposte: int = 0
    quota_arbitro_esaurita: bool = False
    impossibili: int = 0
    frammentazioni: int = 0
    accorpamenti: int = 0
    sospetti: int = 0

    @property
    def produttivo(self) -> bool:
        """Se questo giro ha fatto avanzare la coda.

        Non e' 'ha cambiato l'archivio': un giro che rilegge venti pagine
        tutte CONFERMATO, o che sottopone venti casi e li vede tornare
        come 'conferma' — un ragionamento scritto, nessuna fusione
        applicata — sta comunque avanzando, perche' quelle pagine e quei
        casi non verranno richiesti una seconda volta (vedi
        ``rilettura.casi`` e ``arbitro.gia_arbitrati``). Fermarsi li'
        vorrebbe dire smettere di guardare la coda proprio mentre la si
        sta ancora scoprendo: la scheda piu' grossa dell'archivio puo'
        benissimo stare alla posizione venticinque, non alla prima.

        Solo pagine e casi **nuovi** contano: se questo giro non ne ha
        trovati — non 'sono tornati senza cambiare niente', ma 'non ce
        n'erano' — allora si e' davvero arrivati in fondo alla coda con i
        criteri di oggi, ed e' li' che vale la pena fermarsi.
        """
        # 'pagine_esaminate' comprende gia' le pagine corrette — vedi
        # 'rilettura.esegui', che le conta insieme in 'fatte' — quindi
        # controllarle entrambe sarebbe ridondante.
        return (
            self.pagine_esaminate > 0
            or self.pagine_fallite > 0
            or self.casi_sottoposti > 0
        )

    @property
    def fermo_per_quota(self) -> bool:
        """Se il giro si e' interrotto perche' la quota e' finita, non
        perche' non c'era piu' niente da fare — la differenza conta per
        decidere se vale la pena riprovare domani."""
        return self.quota_lettura_esaurita or self.quota_arbitro_esaurita


def convergenza(storia: list[Giro]) -> str | None:
    """Se il ciclo puo' fermarsi, e perche'. ``None`` vuol dire: continua.

    Separata dall'orchestrazione perche' e' la parte che vale la pena
    capire senza dover leggere ne' eseguire una chiamata di rete: e' una
    funzione pura su una lista di numeri.
    """
    if not storia:
        return None
    ultimo = storia[-1]
    if not ultimo.produttivo:
        return (
            "convergenza: l'ultimo giro non ha prodotto ne' correzioni ne' "
            "decisioni. Quello che resta in coda non lo sciolgono ne' la "
            "lettura ne' l'arbitrato — serve altro."
        )
    if len(storia) >= GIRI_MASSIMI:
        return (
            f"tetto dei giri raggiunto ({GIRI_MASSIMI}): il ciclo era ancora "
            f"produttivo, non e' detto che sia finito. Si puo' rilanciare."
        )
    return None


def esegui(
    config,
    *,
    giri_massimi: int = GIRI_MASSIMI,
    pagine_per_giro: int = PAGINE_PER_GIRO,
    casi_per_giro: int = CASI_PER_GIRO,
    tipo_arbitro: str | None = None,
    dividi_arbitro: bool = False,
    config_arbitro=None,
    su_giro=None,
) -> list[Giro]:
    """Il ciclo intero: ricostruisci, rileggi, arbitra, e ancora — finche'
    un giro non produce piu' niente o la quota non finisce.

    ``config_arbitro``, se data, e' la configurazione con cui si arbitra —
    tipicamente la stessa di ``config`` ma con un motore diverso: la
    rilettura legge un'immagine ed e' il posto di un motore che legge,
    l'arbitrato ragiona su testo gia' estratto ed e' il posto di un
    motore che ragiona. Se non data, si arbitra con ``config``.

    ``su_giro``, se data, e' chiamata dopo ogni giro con il suo
    :class:`Giro`: e' il gancio con cui la CLI stampa il progresso senza
    che questa funzione debba sapere niente di ``print``.
    """
    from history_maker import qualita
    from history_maker.ricostruzione import (
        anomalie as coda_anomalie, arbitro, cache, esecuzione, lettura,
        registro, rilettura, risoluzione,
    )

    config_arbitro = config_arbitro if config_arbitro is not None else config

    percorso = config.dataset / "torrebruna.sqlite"
    if not percorso.exists():
        raise FileNotFoundError(
            f"manca {percorso}: prima va costruito il dataset "
            f"(python -m history_maker dataset)"
        )

    storia: list[Giro] = []
    for numero in range(1, giri_massimi + 1):
        logger.info("=== giro %d ===", numero)

        # 1. Ricostruisci: applica le decisioni del giro precedente e
        # ricalcola le anomalie da capo. Il primo giro parte da qualunque
        # stato l'archivio sia gia' in.
        schede_prima = _quante_schede(percorso)
        esecuzione.costruisci(config)
        schede_dopo = _quante_schede(percorso)

        giro = Giro(numero=numero, schede_prima=schede_prima, schede_dopo=schede_dopo)

        # 2. Rileggi: le pagine con un dubbio che l'immagine puo' sciogliere.
        conn = sqlite3.connect(percorso)
        conn.row_factory = sqlite3.Row
        atti = rilettura.casi(conn, pagine_per_giro)
        if atti:
            dossier = rilettura.prepara(conn, atti, config.immagini)
            esiti = rilettura.esegui(config, conn, dossier, tetto=len(dossier))
            conn.commit()
            giro.correzioni_lettura = esiti["correzioni"]
            giro.pagine_esaminate = esiti["fatte"]
            giro.conflitti_col_contesto = esiti["conflitti_col_contesto"]
            giro.pagine_fallite = esiti["fallite"]
            giro.quota_lettura_esaurita = esiti["quota_esaurita"]
        conn.close()

        # 3. Arbitra: i casi di identita' che il calcolo non decide da
        # solo. Ricostruisce il grafo in memoria — e' lo stesso prezzo che
        # paga 'python -m history_maker arbitra' da riga di comando.
        conn = sqlite3.connect(percorso)
        deposito = cache.Deposito(config.dataset)
        corpus = lettura.carica(conn, deposito)
        esito = risoluzione.ricostruisci(corpus, deposito=deposito)
        esito.anomalie.extend(coda_anomalie.tutte(esito))
        esclusi = arbitro.gia_arbitrati(conn)
        casi_disponibili = arbitro.casi(esito, casi_per_giro, tipo_arbitro, esclusi)
        if casi_disponibili:
            gia_prese = len(esito.decisioni)
            conteggi = arbitro.arbitra(
                config_arbitro, esito, quanti=casi_per_giro, deposito=deposito,
                tipo=tipo_arbitro, dividi=dividi_arbitro, conn=conn,
            )
            registro.salva(conn, esito.decisioni[gia_prese:])
            conn.commit()
            giro.casi_sottoposti = conteggi["casi"]
            giro.decisioni_arbitro = conteggi["applicate"]
            giro.divisioni_proposte = conteggi.get("divisioni_proposte", 0)
            giro.quota_arbitro_esaurita = conteggi["quota_esaurita"]
        conn.close()

        # 4. Il quadro di qualita', per il rapporto e per capire se vale
        # la pena continuare.
        conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True)
        conteggi_qualita = qualita.conteggi(qualita.analizza(conn))
        conn.close()
        giro.impossibili = conteggi_qualita.get("impossibile", 0)
        giro.frammentazioni = conteggi_qualita.get("frammentazione", 0)
        giro.accorpamenti = conteggi_qualita.get("accorpamento", 0)
        giro.sospetti = conteggi_qualita.get("sospetto", 0)

        storia.append(giro)
        if su_giro is not None:
            su_giro(giro)

        motivo = convergenza(storia)
        if motivo is not None:
            logger.info("ciclo fermato: %s", motivo)
            break
        if giro.fermo_per_quota:
            logger.info(
                "quota esaurita ma il giro era produttivo: "
                "rilanciare piu' tardi per continuare da qui."
            )
            break

    return storia


def _quante_schede(percorso: Path) -> int:
    try:
        conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True)
        return conn.execute("SELECT COUNT(*) FROM individui").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def rapporto(storia: list[Giro]) -> str:
    """La tabella dei giri, in Markdown."""
    righe = [
        "# La pipeline, giro per giro",
        "",
        "| giro | correzioni | conflitti | decisioni | divisioni | schede | "
        "impossibile | framment. | accorp. |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for giro in storia:
        righe.append(
            f"| {giro.numero} | {giro.correzioni_lettura} | "
            f"{giro.conflitti_col_contesto} | {giro.decisioni_arbitro} | "
            f"{giro.divisioni_proposte} | {giro.schede_dopo} | "
            f"{giro.impossibili} | {giro.frammentazioni} | {giro.accorpamenti} |"
        )
    if storia:
        motivo = convergenza(storia)
        righe += ["", motivo or "fermato: tetto dei giri o quota esaurita."]
    return "\n".join(righe)
