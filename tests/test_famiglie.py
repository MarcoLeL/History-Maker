"""La ricostituzione delle famiglie: il nucleo come unita' di riconoscimento.

I casi qui dentro sono tutti presi dall'archivio di Torrebruna, perche'
questo modulo esiste per risolverne di reali e non di immaginati. Vanno
letti a coppie: per ogni cosa che si pretende venga unita ce n'e' una
che le somiglia e che **non** deve esserlo, ed e' quella la meta' che
conta. Unire troppo, qui, e' il danno peggiore: fabbrica un albero che
sembra piu' ricco ed e' falso.
"""

from collections import defaultdict

from history_maker import famiglie, identita

from test_identita import eta, menzione


def riconosci(menzioni):
    """L'intera fase 6 su un pugno di menzioni, come la fa genealogia.

    La lettura delle famiglie si fa **un atto alla volta**: e' dentro il
    singolo atto che 'padre' e 'madre' vogliono dire qualcosa, e passarle
    tutte insieme farebbe credere che un bambino abbia quattro genitori.
    """
    per_atto = defaultdict(list)
    for menzione in menzioni:
        per_atto[menzione.atto].append(menzione)
    for gruppo in per_atto.values():
        identita.famiglia_dell_atto(gruppo)
    return identita.riconosci(menzioni)


def scheda_di(persone, nome):
    """La persona che porta questo nome, pretendendone una sola."""
    trovate = [p for p in persone if nome in p._nomi.keys()]
    assert trovate, f"nessuna scheda per {nome}: ci sono {[dict(p._nomi) for p in persone]}"
    return trovate


# --- prima prova: un figlio ha una madre sola -------------------------------

def test_due_madri_dello_stesso_figlio_si_uniscono_col_padre_in_comune():
    """Il caso di Maria Moretta, nata nel 1809.

    Sua madre e' 'Tecla Monaco' sull'atto di nascita e 'Maruca Decla' su
    quello di matrimonio del 1832. Non c'e' somiglianza da misurare fra
    i due cognomi — la seconda lettura e' rovinata — ma il padre e' lo
    stesso uomo in tutti e due gli atti, e un bambino ha una madre sola.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Maria", "Moretta", anno=1809),
        menzione(2, 1, "padre", "Giuseppe", "Moretta", anno=1809),
        menzione(3, 1, "madre", "Tecla", "Monaco", anno=1809),
        menzione(11, 2, "sposa", "Maria", "Moretta", tipo="matrimonio", anno=1832,
                 eta=eta(23)),
        menzione(12, 2, "padre", "Giuseppe", "Moretta", tipo="matrimonio", anno=1832),
        menzione(13, 2, "madre", "Maruca", "Decla", tipo="matrimonio", anno=1832),
    ]
    persone = riconosci(menzioni)
    madri = [p for p in persone if any(m.ruolo == "madre" for m in p.menzioni)]
    assert len(madri) == 1, "Tecla Monaco e Maruca Decla sono la stessa donna"
    assert {"Tecla", "Maruca"} <= set(madri[0]._nomi)


def test_due_madri_non_si_uniscono_se_anche_i_padri_sono_due():
    """Senza il co-genitore la prova non c'e' piu'.

    Due padri e due madri per lo stesso bambino non dicono che le madri
    siano una: dicono che non si sa chi siano i suoi genitori, e in quel
    caso inventarsi un'unione e' peggio che lasciare il buco.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Maria", "Moretta", anno=1809),
        menzione(2, 1, "padre", "Giuseppe", "Moretta", anno=1809),
        menzione(3, 1, "madre", "Tecla", "Monaco", anno=1809),
        menzione(11, 2, "sposa", "Maria", "Moretta", tipo="matrimonio", anno=1832,
                 eta=eta(23)),
        menzione(12, 2, "padre", "Nicola", "Pelliccia", tipo="matrimonio", anno=1832),
        menzione(13, 2, "madre", "Maruca", "Decla", tipo="matrimonio", anno=1832),
    ]
    persone = riconosci(menzioni)
    madri = [p for p in persone if any(m.ruolo == "madre" for m in p.menzioni)]
    assert len(madri) == 2


# --- seconda prova: due nuclei con la stessa coppia -------------------------

def test_due_nuclei_con_la_stessa_coppia_si_uniscono():
    """Due figli diversi, la stessa coppia letta in due modi.

    Nessun figlio sta in tutti e due gli atti, quindi la prima prova non
    si applica: restano i due nomi della coppia, che presi insieme
    bastano. I due bambini devono risultare fratelli.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Anna", "Pacilli", anno=1840),
        menzione(2, 1, "padre", "Egidio", "Pacilli", anno=1840),
        menzione(3, 1, "madre", "Marianna", "Lella", anno=1840),
        menzione(11, 2, "neonato", "Rosa", "Pacilli", anno=1843),
        menzione(12, 2, "padre", "Egidio", "Peilli", anno=1843),
        menzione(13, 2, "madre", "Marianna", "Lelli", anno=1843),
    ]
    persone = riconosci(menzioni)
    padri = [p for p in persone if any(m.ruolo == "padre" for m in p.menzioni)]
    madri = [p for p in persone if any(m.ruolo == "madre" for m in p.menzioni)]
    assert len(padri) == 1 and len(madri) == 1


def test_la_coppia_si_confronta_anche_senza_cognome():
    """'Domenico x Saba Minchilli' e 'Domenico Di Laudo x Saba Minchilli'.

    Chi il cognome non ce l'ha e' il caso da risolvere, non quello da
    saltare: e' successo che finisse in un bacino tutto suo, dove non lo
    confrontava nessuno, e restava un nucleo a parte per sempre.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Antonio", "Di Laudo", anno=1870),
        menzione(2, 1, "padre", "Domenico", "Di Laudo", anno=1870),
        menzione(3, 1, "madre", "Saba", "Minchilli", anno=1870),
        menzione(11, 2, "neonato", "Luigi", "Di Laudo", anno=1873),
        menzione(12, 2, "padre", "Domenico", None, anno=1873),
        menzione(13, 2, "madre", "Saba", "Minchilli", anno=1873),
    ]
    persone = riconosci(menzioni)
    padri = [p for p in persone if any(m.ruolo == "padre" for m in p.menzioni)]
    assert len(padri) == 1, "il padre senza cognome e' lo stesso uomo"


def test_due_coppie_con_nomi_diversi_restano_due():
    menzioni = [
        menzione(1, 1, "neonato", "Anna", "Pacilli", anno=1840),
        menzione(2, 1, "padre", "Egidio", "Pacilli", anno=1840),
        menzione(3, 1, "madre", "Marianna", "Lella", anno=1840),
        menzione(11, 2, "neonato", "Rosa", "Colella", anno=1843),
        menzione(12, 2, "padre", "Nicola", "Colella", anno=1843),
        menzione(13, 2, "madre", "Teresa", "Torzi", anno=1843),
    ]
    persone = riconosci(menzioni)
    padri = [p for p in persone if any(m.ruolo == "padre" for m in p.menzioni)]
    assert len(padri) == 2


# --- le guardie: cosa non si unisce mai -------------------------------------

def test_il_nonno_non_si_fonde_col_nipote_che_ne_porta_il_nome():
    """Orazio Pelliccia, nato nel 1824, e l'Orazio che nel 1853 ne ha 64.

    E' la trappola di questo paese, dove il primogenito porta il nome del
    nonno: due uomini con lo stesso nome, la stessa moglie di nome, e
    trent'anni di distanza. L'atto di nascita e' un documento e le eta'
    dichiarate sono un ricordo, ma quando il ricordo e' concorde e
    lontano decenni non e' piu' un'imprecisione: e' un altro uomo.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Orazio", "Pelliccia", anno=1824),
        menzione(2, 1, "padre", "Nicola", "Pelliccia", anno=1824),
        menzione(3, 1, "madre", "Rosa", "Torzi", anno=1824),
        menzione(11, 2, "neonato", "Antonio", "Pelliccia", anno=1853),
        menzione(12, 2, "padre", "Orazio", "Pelliccia", anno=1853, eta=eta(64)),
        menzione(13, 2, "madre", "Rosa", "Torzi", anno=1853, eta=eta(60)),
    ]
    persone = riconosci(menzioni)
    orazi = [p for p in persone if "Orazio" in p._nomi]
    assert len(orazi) == 2, "il bambino del 1824 non e' l'uomo di 64 anni del 1853"


def test_due_atti_di_nascita_diversi_restano_due_persone():
    """Il veto piu' solido che ci sia: due atti di nascita sono due bambini."""
    prima = identita.Persona(id=1)
    seconda = identita.Persona(id=2)
    chiavi = identita.ChiaviFamiliari({})
    prima.aggiungi(menzione(1, 1, "neonato", "Maria", "Lella", anno=1840), chiavi)
    seconda.aggiungi(menzione(2, 2, "neonato", "Maria", "Lella", anno=1846), chiavi)
    assert not famiglie._niente_lo_vieta(prima, seconda)


def test_non_si_uniscono_due_persone_di_sesso_diverso():
    chiavi = identita.ChiaviFamiliari({})
    prima = identita.Persona(id=1, sesso="M")
    seconda = identita.Persona(id=2, sesso="F")
    prima.aggiungi(menzione(1, 1, "padre", "Domenico", "Lella"), chiavi)
    seconda.aggiungi(menzione(2, 2, "madre", "Domenica", "Lella"), chiavi)
    assert not famiglie._niente_lo_vieta(prima, seconda)


def test_i_parti_devono_stare_in_una_vita_fertile():
    """Due donne dello stesso nome che partoriscono a sessant'anni di
    distanza non sono una donna longeva: sono madre e figlia."""
    chiavi = identita.ChiaviFamiliari({})
    prima = identita.Persona(id=1, sesso="F")
    seconda = identita.Persona(id=2, sesso="F")
    prima.aggiungi(menzione(1, 1, "madre", "Vitaliana", "Ottaviano", anno=1810), chiavi)
    seconda.aggiungi(menzione(2, 2, "madre", "Vitaliana", "Ottaviano", anno=1871), chiavi)
    prima.parti.append(1810)
    seconda.parti.append(1871)
    assert not famiglie._prole_possibile(prima, seconda)


def test_tre_parti_nello_stesso_anno_sono_impossibili():
    """Due si spiegano — gemelli, o gennaio e dicembre. Tre no."""
    chiavi = identita.ChiaviFamiliari({})
    prima = identita.Persona(id=1, sesso="F")
    seconda = identita.Persona(id=2, sesso="F")
    prima.aggiungi(menzione(1, 1, "madre", "Anna", "Lella", anno=1840), chiavi)
    seconda.aggiungi(menzione(2, 2, "madre", "Anna", "Lella", anno=1840), chiavi)
    # Due parti nello stesso anno: gemelli, oppure gennaio e dicembre.
    assert famiglie._prole_possibile(prima, seconda)
    prima.parti.append(1840)
    assert not famiglie._prole_possibile(prima, seconda)


# --- il veto sui genitori, che qui e' spento apposta ------------------------

def test_i_genitori_discordi_non_vietano_qui():
    """E' la scelta su cui si regge il modulo, e va tenuta ferma da un test.

    Nel riconoscimento normale due schede con genitori dichiarati diversi
    sono due persone, punto. Qui no: quei genitori sono spesso proprio la
    lettura sbagliata che si sta correggendo — i genitori nominati in un
    atto di matrimonio sono una seconda lettura, peggiore, di gente che
    l'archivio conosce gia' da altre pagine.
    """
    chiavi = identita.ChiaviFamiliari({})
    prima = identita.Persona(id=1, sesso="F")
    seconda = identita.Persona(id=2, sesso="F")
    prima.aggiungi(menzione(1, 1, "madre", "Maria Giuseppa", "Franchella"), chiavi)
    seconda.aggiungi(menzione(2, 2, "sposa", "Maria Giuseppa", "Franchella"), chiavi)
    prima.padri.add("giovanni|franchella")
    seconda.padri.add("manupe|franchella")

    assert not identita._senza_contraddizioni(prima, seconda)
    assert famiglie._niente_lo_vieta(prima, seconda)
