"""Le due code che consumano quota: la verifica sull'immagine e l'arbitro.

Sono la parte del sistema che costa denaro o abbonamento, e per questo
sono anche quella che va collaudata **senza spenderne**: qui il motore e'
un finto che risponde quello che gli si dice, e i casi verificano le tre
cose che, sbagliate, si pagano.

**Che la domanda non riparta due volte.** La cache e' indicizzata su tutto
cio' che influenza la risposta; se cambia il modello o il prompt la
domanda si rifa', se non cambia niente non parte. Una cache che sbaglia
in un verso spreca quota, nell'altro spaccia per nuova una risposta
vecchia.

**Che la risposta non diventi un fatto.** Una decisione di un modello
resta una decisione **con un autore**, registrata come tale, e non
scavalca mai le impossibilita' fisiche.

**Che il fascicolo non suggerisca la risposta.** Un fascicolo che
presenta solo le prove a favore ottiene fusioni, e la sua risposta non
vale niente.
"""

import json

from history_maker.backend import Richiesta, Risposta
from history_maker.ricostruzione import (
    anomalie, arbitro, cache, contesto, evidenza, modello, verifica,
)
from history_maker.ricostruzione.modello import Verifica
from history_maker.ricostruzione.scheda import Scheda as modello_scheda
from test_ricostruzione import in_un_paese, nascita, schede_di_prova


class FintoMotore:
    """Un motore che risponde quello che gli si dice, e conta le chiamate."""

    nome = "finto"
    modo_immagini = "allegate"

    def __init__(self, risposte):
        self.risposte = list(risposte)
        self.richieste: list[Richiesta] = []

    def verifica(self):
        return None

    def riferimento_immagine(self, percorso, indice):
        return f"immagine {indice + 1}"

    def esegui(self, richiesta):
        self.richieste.append(richiesta)
        testo = self.risposte.pop(0) if self.risposte else "{}"
        return Risposta(ok=True, testo=testo)


def esito_di_prova():
    return in_un_paese([
        nascita(1870, ("Anna", "Di Laudo"), ("Domenico", "Di Laudo"),
                ("Saba", "Minchilli")),
        nascita(1872, ("Luigi", "Di Laudo"), ("Domenico", "Di Laudo"),
                ("Saba", "Minchillo")),
    ])


# ---------------------------------------------------------------------------
# La verifica sull'immagine
# ---------------------------------------------------------------------------

class TestVerificaSullImmagine:

    def test_la_domanda_e_chiusa_e_ammette_il_non_so(self):
        """Scegliere fra due forme e' piu' facile che leggere da zero.

        E deve restare possibile rispondere che non e' nessuna delle due:
        una domanda che costringe a scegliere ottiene una scelta anche
        quando sulla carta c'e' scritto altro.
        """
        domanda = Verifica(
            atto=12, immagine="r/0001.jpg", campo="il cognome",
            domanda="il cognome scritto qui e' «Lella» o «Lelli»?",
            alternative=("Lella", "Lelli"), bersaglio="Lelli",
        )
        testo = verifica.istruzione(domanda)
        assert "Lella" in testo and "Lelli" in testo
        assert "nessuna di queste" in testo
        assert "Lelli" in testo    # il bersaglio, per ritrovare la riga

    def test_la_stessa_domanda_non_riparte(self):
        """La chiave comprende tutto cio' che cambia la risposta."""
        una = Verifica(
            atto=12, immagine="r/0001.jpg", campo="il cognome",
            domanda="Lella o Lelli?", alternative=("Lella", "Lelli"),
        )
        assert verifica.chiave(una, "gemini-3.5") == verifica.chiave(una, "gemini-3.5")
        # Un altro modello e' un'altra risposta, e spacciarla per questa
        # sarebbe peggio che non averla.
        assert verifica.chiave(una, "gemini-3.5") != verifica.chiave(una, "claude")
        altra = Verifica(
            atto=12, immagine="r/0001.jpg", campo="il cognome",
            domanda="Lella o Lella?", alternative=("Lella",),
        )
        assert verifica.chiave(una, "gemini-3.5") != verifica.chiave(altra, "gemini-3.5")

    def test_una_persona_un_campo_una_domanda(self):
        """Lo stesso dubbio su quattro atti non sono quattro domande.

        Costerebbe quattro volte la quota per la stessa risposta.
        """
        esito, prima = esito_di_prova()
        scheda = schede_di_prova(esito, prima, ruolo="padre")[0]
        anomalie = [
            modello.Anomalia(
                tipo="SURNAME_ANOMALY", individui=(scheda.chiave,), campo="cognome",
                descrizione=f"dubbio {numero}", atti=(atto,), confidenza=0.5,
            )
            for numero, atto in enumerate(sorted(scheda.atti))
        ]
        domande = verifica.domande_da(esito, anomalie, quante=10)
        assert len(domande) <= 1

    def test_la_risposta_del_modello_si_legge(self):
        """Il caso che ha sprecato quota in silenzio.

        ``backend.estrai_json`` rende **gia' la struttura**, non il testo:
        passarla a ``json.loads`` solleva un TypeError, e il ramo di
        ripiego trasformava ogni risposta buona in una vuota. Una
        richiesta pagata per volta, senza lasciare traccia — perche' una
        lettura vuota e' esattamente cio' che si vede quando il modello
        non riesce a leggere la parola.
        """
        risposta = verifica._interpreta(
            '{"lettura": "quaranta", "confidenza": 0.9, "note": "si legge bene"}'
        )
        assert risposta["lettura"] == "quaranta"
        assert risposta["confidenza"] == 0.9
        # E il blocco markdown, che il modello aggiunge quasi sempre.
        incorniciata = verifica._interpreta(
            '```json\n{"lettura": "Lelli", "confidenza": 0.7}\n```'
        )
        assert incorniciata["lettura"] == "Lelli"
        # Una risposta illeggibile resta vuota, senza far cadere niente.
        assert verifica._interpreta("non ho capito")["lettura"] == ""

    def test_la_domanda_sull_eta_dice_quale_sarebbe_l_altra(self):
        """Sull'eta' le due candidate non sono due grafie, sono due numeri."""
        anomalia = modello.Anomalia(
            tipo="AGE_ANOMALY", individui=(1,), campo="eta",
            descrizione="", alternative=("trentuno", "20"),
        )
        domanda = verifica._domanda(anomalia, "l'eta' dichiarata", ("trentuno", "20"))
        assert "trentuno" in domanda and "20" in domanda
        assert "atto di nascita" in domanda

    def test_i_casi_che_l_immagine_non_puo_risolvere_non_ci_arrivano(self):
        """Un'omonimia non si decide guardando una parola."""
        esito, prima = esito_di_prova()
        scheda = schede_di_prova(esito, prima, ruolo="padre")[0]
        anomalia = modello.Anomalia(
            tipo="POSSIBLE_HOMONYM", individui=(scheda.chiave,),
            descrizione="due omonimi", atti=tuple(sorted(scheda.atti))[:1],
        )
        assert verifica.domande_da(esito, [anomalia]) == []


# ---------------------------------------------------------------------------
# L'arbitro
# ---------------------------------------------------------------------------

class TestArbitro:

    def test_il_fascicolo_porta_anche_le_prove_contrarie(self):
        """Un fascicolo che mostra solo le prove a favore ottiene fusioni."""
        esito, prima = esito_di_prova()
        schede = schede_di_prova(esito, prima)
        prove = modello.Evidenza(a_priori=-2.5)
        prove.aggiungi("cognome", 1.2, "cognome minchili")
        prove.aggiungi("eta", -1.1, "nascita 1840 / 1852 (12 anni)")
        anomalia = modello.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(schede[0].chiave, schede[1].chiave),
            descrizione="forse la stessa", evidenza=prove, confidenza=0.5,
            spiegazioni=("sono la stessa persona", "sono due omonimi"),
        )
        fascicolo = contesto.fascicolo(anomalia, esito)
        assert fascicolo["prove_a_favore"]
        assert fascicolo["prove_contrarie"], "le contraddizioni non si nascondono"
        assert len(fascicolo["spiegazioni_possibili"]) >= 2
        # E la domanda lascia aperta la terza risposta.
        assert "non" in fascicolo["domanda"].lower()

    def test_la_cronologia_degli_attributi_e_datata(self):
        """'bovaro nel 1850, contadino nel 1875' e' una biografia.

        'bovaro | contadino' e' una contraddizione apparente, ed e' la
        differenza fra far ragionare un modello e confonderlo.
        """
        esito, prima = in_un_paese([
            nascita(1850, ("Anna", "Lella"), ("Domenico", "Lella"),
                    ("Teresa", "Petta"), dati_padre={"professione": "bovaro"}),
            nascita(1875, ("Luigi", "Lella"), ("Domenico", "Lella"),
                    ("Teresa", "Petta"), dati_padre={"professione": "contadino"}),
        ])
        padre = schede_di_prova(esito, prima, ruolo="padre")[0]
        dati = contesto._scheda(padre, esito)
        assert any(voce.startswith("1850:") for voce in dati["mestieri"])
        assert any(voce.startswith("1875:") for voce in dati["mestieri"])

    def test_una_risposta_netta_diventa_una_decisione_con_un_autore(self):
        esito, prima = esito_di_prova()
        schede = schede_di_prova(esito, prima)
        anomalia = modello.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(schede[0].chiave, schede[0].chiave),
            descrizione="", confidenza=0.5,
        )
        arbitro._applica(esito, anomalia, {
            "decisione": "stessa persona", "confidenza": 0.95,
            "ragionamento": "stessa moglie e stessi figli",
            "prove_usate": ["coniuge in comune"],
        }, "modello-finto")
        ultima = esito.decisioni[-1]
        assert ultima.decisore == "claude"
        assert ultima.modello == "modello-finto"
        assert ultima.versione_prompt == contesto.VERSIONE_PROMPT
        assert ultima.evidenze == ("coniuge in comune",)
        assert ultima.quando

    def test_una_risposta_incerta_non_muove_niente(self):
        """'non deciso' e' un modello che sta funzionando."""
        esito, prima = esito_di_prova()
        schede = schede_di_prova(esito, prima)
        quante = len(esito.schede)
        mosso = arbitro._applica(esito, modello.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(schede[0].chiave, schede[1].chiave),
            descrizione="", confidenza=0.5,
        ), {"decisione": "non deciso", "confidenza": 0.4}, "finto")
        assert mosso is False
        assert len(esito.schede) == quante
        # Ma la risposta resta registrata: serve a sapere, fra un mese,
        # su che tipo di casi quel modello dice di non sapere.
        assert esito.decisioni[-1].azione == "conferma"

    def test_una_fusione_impossibile_si_rifiuta_anche_se_la_chiede_il_modello(self):
        """Una risposta puo' essere ragionevole e sbagliata.

        Un archivio che accetta di scrivere che una donna ha partorito
        dopo il proprio funerale non e' piu' un archivio.
        """
        esito, prima = esito_di_prova()
        chiavi = esito.corpus.chiavi()
        una, altra = sorted(esito.schede)[:2]
        esito.schede[una].nascite_certe = [1820]
        esito.schede[altra].nascite_certe = [1850]
        assert arbitro.unisci(esito, (una, altra), "il modello dice di si'", 0.99) is False

    def test_persone_diverse_non_e_sempre_una_separazione(self):
        """Il caso che la prima esecuzione dal vivo ha scoperto.

        Su un doppio coniuge le entita' del fascicolo non sono due schede
        che potrebbero coincidere: sono **una persona e i suoi due
        coniugi**. 'Sono persone diverse' li' vuol dire 'le due mogli sono
        due donne', e tradurlo in una separazione avrebbe staccato il
        marito dalla prima moglie — l'opposto di quanto detto.
        """
        esito, prima = esito_di_prova()
        schede = schede_di_prova(esito, prima)
        gia_separate = modello.Anomalia(
            tipo="MARITAL_ANOMALY",
            individui=(schede[0].chiave, schede[1].chiave),
            descrizione="",
        )
        assert arbitro._azione(esito, gia_separate, "persone diverse", 0.95) == "conferma"
        # Ma quando le due entita' stanno davvero nella stessa scheda,
        # 'persone diverse' e' un ordine di separare.
        insieme = modello.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(schede[0].chiave, schede[0].chiave),
            descrizione="",
        )
        assert arbitro._azione(esito, insieme, "persone diverse", 0.95) == "separazione"
        assert arbitro._azione(esito, insieme, "persone diverse", 0.4) == "conferma"

    def test_la_risposta_corta_non_si_allinea_a_caso(self):
        """Meno risposte che domande: le mancanti restano 'non deciso'."""
        risposte = arbitro._interpreta('[{"decisione": "stessa persona"}]', 3)
        assert len(risposte) == 3
        assert risposte[1] == {} and risposte[2] == {}

    def test_il_giro_intero_chiede_una_volta_e_poi_riusa(self, tmp_path, monkeypatch):
        """La quota si paga alla prima domanda, non alla seconda.

        E' il caso che tiene insieme tutto il resto: fascicolo, chiamata,
        lettura della risposta, cache. Gira contro un finto motore, cosi'
        il collaudo non costa niente — che e' esattamente la ragione per
        cui esiste.
        """
        from history_maker import backend as motori
        from history_maker.config import Config

        esito, prima = esito_di_prova()
        schede = schede_di_prova(esito, prima)
        esito.anomalie = [modello.Anomalia(
            tipo="DUPLICATE_PERSON",
            individui=(schede[0].chiave, schede[1].chiave),
            descrizione="forse la stessa", confidenza=0.5,
        )]
        finto = FintoMotore([json.dumps([{
            "decisione": "non deciso", "confidenza": 0.5,
            "ragionamento": "le prove non bastano",
        }])])
        monkeypatch.setattr(motori, "crea", lambda config: finto)

        config = Config.carica()
        deposito = cache.Deposito(tmp_path)
        primo = arbitro.arbitra(config, esito, quanti=5, deposito=deposito)
        secondo = arbitro.arbitra(config, esito, quanti=5, deposito=deposito)

        assert primo["risposte"] == 1
        assert len(finto.richieste) == 1, "la seconda volta non deve richiamare"
        assert secondo["riusate"] == 1
        # E il fascicolo e' arrivato al modello, non un riassunto.
        assert "CASO 1" in finto.richieste[0].istruzione
        assert "prove_contrarie" in finto.richieste[0].istruzione

    def test_una_persona_sola_non_occupa_la_coda(self):
        """Le schede grosse hanno impatto alto e se ne prendevano tutta.

        L'impatto entra nella priorita', e un individuo da 1.153 menzioni
        occupava da solo le prime trenta posizioni: una giornata di quota
        spesa su una persona. Ora dopo tre casi passa il turno, e il
        resto scivola in fondo invece di sparire.
        """
        esito, _ = esito_di_prova()
        esito.anomalie = [
            modello.Anomalia(
                tipo="DUPLICATE_PERSON", individui=(1, 100 + numero),
                descrizione=f"caso {numero}", confidenza=0.5, impatto=500,
            )
            for numero in range(8)
        ] + [
            modello.Anomalia(
                tipo="DUPLICATE_PERSON", individui=(2, 3),
                descrizione="un'altra persona", confidenza=0.5, impatto=4,
            )
        ]
        primi = anomalie.coda(esito)[:4]
        assert sum(1 for a in primi if a.individui[0] == 1) == 3
        assert any(a.individui[0] == 2 for a in primi), "l'altra persona passa"
        # E nessun caso e' stato buttato via.
        assert len(anomalie.coda(esito)) == len(esito.anomalie)

    def test_l_arbitro_sa_dividere_una_scheda(self):
        """L'altra meta' del suo lavoro: non solo unire, anche separare.

        Perche' una risposta sia applicabile il fascicolo deve **nominare**
        le menzioni: senza i numeri, 'sono due persone' non dice quali
        righe vanno di qua e quali di la'.
        """
        esito, prima = esito_di_prova()
        # Serve una scheda le cui menzioni stiano in **due periodi
        # separati**: e' l'unica divisione che l'arbitro applica, perche'
        # l'anomalia che la chiede dice 'un arco che una vita sola non
        # copre' e la risposta deve separare due vite, non tagliarne una.
        scheda = max(
            (s for s in esito.schede.values() if s.quante > 2),
            key=lambda s: (max(s.anni) - min(s.anni)) if s.anni else 0,
        )
        meta = (min(scheda.anni) + max(scheda.anni)) / 2
        prime = sorted(m.id for m in scheda.menzioni if m.anno <= meta)
        seconde = sorted(m.id for m in scheda.menzioni if m.anno > meta)
        assert prime and seconde, "serve una scheda con due periodi"
        ids = prime + seconde
        anomalia = modello.Anomalia(
            tipo="IDENTITY_ANOMALY", individui=(scheda.chiave,),
            descrizione="arco di vita impossibile", campo="eta",
        )
        # Il fascicolo numera le righe e lo chiede.
        dossier = contesto.fascicolo(anomalia, esito)
        assert "menzioni vanno con quali" in dossier["domanda"]
        assert any(f"[{ids[0]}]" in riga for riga in dossier["schede"][0]["cronologia"])

        quante = len(esito.schede)
        # I numeri arrivano come il modello li scrive: ricopiati dalla
        # cronologia, quindi fra parentesi quadre. Pretenderli nudi
        # faceva cadere l'intera chiamata con un ValueError, e buttava
        # via anche le risposte buone che le stavano accanto.
        risposta = {
            "decisione": "persone diverse", "confidenza": 0.95,
            "ragionamento": "due vite che non si sovrappongono",
            "gruppi": [[f"[{m}]" for m in prime], [str(m) for m in seconde]],
        }
        # Di default non si applica: sull'archivio di Torrebruna quelle
        # divisioni spezzavano piu' famiglie di quante ne separassero, e
        # la misura sta in 'arbitro.DIVIDE_DI_DEFAULT'.
        assert arbitro._applica(esito, anomalia, risposta, "finto") is False
        assert len(esito.schede) == quante
        assert arbitro._applica(
            esito, anomalia, risposta, "finto", dividi_pure=True
        ) is True
        # Almeno una scheda in piu': quella divisa. Spesso di piu',
        # perche' dividere un uomo lascia sua moglie legata a tutti e due
        # i pezzi, e se i suoi atti si dividono allo stesso modo si
        # divide anche lei.
        assert len(esito.schede) > quante
        # E i due pezzi si ricordano di doversi stare lontani, o il giro
        # dopo la riconciliazione li rimetterebbe insieme.
        pezzi = [s for s in esito.schede.values() if s.ids & set(ids)]
        assert any(s.vietati for s in pezzi)
        assert evidenza.veti(pezzi[0], pezzi[1]) is not None

    def test_una_divisione_che_taglia_una_vita_si_rifiuta(self):
        """La guardia che il giudizio del modello non ha.

        Sui casi di arco impossibile il modello risponde bene — «108 anni
        non sono una vita sola» — ma le menzioni che assegna ai due gruppi
        si sovrappongono nel tempo, e allora non sono due uomini in
        sequenza: e' lo stesso uomo tagliato in due, con la moglie a
        meta' fra i pezzi. Misurato: senza questa guardia, delle 59 coppie
        gemelle che ne uscivano 49 avevano i figli intrecciati.
        """
        esito, prima = esito_di_prova()
        scheda = max(esito.schede.values(), key=lambda s: s.quante)
        meta = len(scheda.menzioni) // 2

        def pezzo(menzioni):
            nuovo = modello_scheda(chiave=min(m.id for m in menzioni))
            nuovo.menzioni = list(menzioni)
            nuovo.ricalcola(esito.corpus.chiavi())
            return nuovo

        # Alternate: i due pezzi coprono lo stesso arco di anni.
        intrecciati = [
            pezzo(scheda.menzioni[0::2]), pezzo(scheda.menzioni[1::2]),
        ]
        assert arbitro._non_sono_due_vite(intrecciati) is True
        # In sequenza: due periodi, e allora sono due vite.
        in_fila = [pezzo(scheda.menzioni[:meta]), pezzo(scheda.menzioni[meta:])]
        assert arbitro._non_sono_due_vite(in_fila) is False

    def test_solo_i_casi_che_un_ragionamento_puo_decidere(self):
        """Una data impossibile non si decide ragionando: si guarda."""
        esito, _ = esito_di_prova()
        esito.anomalie = [
            modello.Anomalia(tipo="DATE_ANOMALY", individui=(1,), descrizione="x"),
            modello.Anomalia(tipo="DUPLICATE_PERSON", individui=(2, 3), descrizione="y"),
        ]
        tipi = {anomalia.tipo for anomalia in arbitro.casi(esito, 10)}
        assert tipi == {"DUPLICATE_PERSON"}


# ---------------------------------------------------------------------------
# La cache
# ---------------------------------------------------------------------------

class TestCache:

    def test_il_conto_si_paga_una_volta_sola(self, tmp_path):
        deposito = cache.Deposito(tmp_path)
        quante = {"volte": 0}

        def calcola():
            quante["volte"] += 1
            return {"risposta": 42}

        chiave = cache.impronta({"domanda": "quanto fa"}, "modello", "1.0")
        assert deposito.ottieni("prova", chiave, calcola) == {"risposta": 42}
        assert deposito.ottieni("prova", chiave, calcola) == {"risposta": 42}
        assert quante["volte"] == 1

    def test_l_impronta_non_dipende_dall_ordine_delle_chiavi(self):
        """Due dizionari uguali devono dare la stessa impronta.

        E non puo' passare da ``hash()``, che in Python cambia a ogni
        processo: una cache cosi' non ritroverebbe mai niente.
        """
        assert cache.impronta({"a": 1, "b": 2}) == cache.impronta({"b": 2, "a": 1})
        assert cache.impronta({"a": 1}) != cache.impronta({"a": 2})

    def test_una_cache_illeggibile_non_ferma_il_lavoro(self, tmp_path):
        deposito = cache.Deposito(tmp_path)
        chiave = cache.impronta("x")
        deposito.ottieni("prova", chiave, lambda: 1)
        percorso = next(tmp_path.glob(".cache-prova-*.pkl"))
        percorso.write_bytes(b"non e' un pickle")
        assert deposito.ottieni("prova", chiave, lambda: 2) == 2
