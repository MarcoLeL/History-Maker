"""Il riconoscimento delle persone e la lettura delle famiglie negli atti.

Due gruppi di casi, e il secondo conta piu' del primo:

* che le famiglie si leggano bene dentro l'atto — in particolare i
  matrimoni, dove i genitori nominati sono due coppie e il ruolo non dice
  quale sia di chi;
* che il riconoscimento **non unisca** cio' che non deve. Un albero con
  due rami cuciti a caso e' peggio di due alberi tronchi, perche' non si
  vede che e' rotto.
"""

import pytest

from history_maker import identita


def menzione(id, atto, ruolo, nome, cognome, tipo="nascita", anno=1850, **extra):
    return identita.Menzione(
        id=id, atto=atto, tipo_atto=tipo, anno=anno, data=None, ruolo=ruolo,
        nome=nome, cognome=cognome, nome_letto=nome, cognome_letto=cognome,
        cognome_origine="atto", incerto=False, eta_letta=extra.get("eta_letta"),
        professione=extra.get("professione"), residenza=extra.get("residenza"),
        via=extra.get("via"), stato_vitale=extra.get("stato_vitale"),
        note=None, immagine=None, sesso=extra.get("sesso"),
        patronimico=extra.get("patronimico"), eta=extra.get("eta"),
    )


def eta(anni, approssimata=False):
    return identita.nomi.Eta(float(anni), approssimata, str(anni))


# --- la famiglia dentro l'atto ---------------------------------------------

def test_nascita_attribuisce_i_genitori_al_neonato():
    atto = [
        menzione(1, 1, "neonato", "Maria", "Pepe"),
        menzione(2, 1, "padre", "Giuseppe", "Pepe"),
        menzione(3, 1, "madre", "Calideta", "Petolina"),
        menzione(4, 1, "testimone", "Egidio", "Colaneri"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[0].padre == 2 and atto[0].madre == 3
    # I genitori nominati insieme sono marito e moglie: e' la fonte piu'
    # abbondante di coppie che abbiamo.
    assert atto[1].coniuge == 3 and atto[2].coniuge == 2
    # Il testimone non e' parente di nessuno.
    assert atto[3].padre is None and atto[3].coniuge is None


def test_due_padri_nello_stesso_atto_non_attribuiscono_niente():
    """Succede quando la divisione della pagina ha unito due atti.

    Meglio un buco che una parentela tirata a sorte.
    """
    atto = [
        menzione(1, 1, "neonato", "Maria", "Pepe"),
        menzione(2, 1, "padre", "Giuseppe", "Pepe"),
        menzione(3, 1, "padre", "Nicola", "Lella"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[0].padre is None


def test_morte_lega_il_defunto_ai_suoi_genitori():
    atto = [
        menzione(1, 1, "ufficiale", "Raffaele", "Troilo", tipo="morte"),
        menzione(2, 1, "dichiarante", "Nicola", "Pelliccia", tipo="morte"),
        menzione(3, 1, "defunto", "Erminia", "Pelliccia", tipo="morte"),
        menzione(4, 1, "padre", "Domenico", "Pelliccia", tipo="morte"),
        menzione(5, 1, "madre", "Elenora", "Femminilli", tipo="morte"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[2].padre == 4 and atto[2].madre == 5
    assert atto[1].padre is None


# --- il matrimonio, che e' il caso difficile --------------------------------

def matrimonio(ordine):
    """Costruisce un atto di matrimonio dalla sequenza di ruoli data.

    Cognomi scelti apposta: i genitori dello sposo portano il suo
    cognome, quelli della sposa il suo.
    """
    nomi_per_ruolo = {
        "sposo": ("Domenico", "Desiderio"),
        "sposa": ("Maddalena", "Pelliccia"),
        "padre-sposo": ("Giovanni", "Desiderio"),
        "madre-sposo": ("Liberia", "Torzi"),
        "padre-sposa": ("Noè", "Pelliccia"),
        "madre-sposa": ("Agostina", "Nicodemo"),
    }
    atto = []
    for indice, chiave in enumerate(ordine, start=1):
        nome, cognome = nomi_per_ruolo[chiave]
        ruolo = chiave.split("-")[0]
        atto.append(menzione(indice, 1, ruolo, nome, cognome, tipo="matrimonio"))
    return atto


def test_matrimonio_schema_alternato():
    """Lo schema che vale 367 atti su 678: sposo, i suoi, sposa, i suoi."""
    atto = matrimonio([
        "sposo", "padre-sposo", "madre-sposo",
        "sposa", "padre-sposa", "madre-sposa",
    ])
    identita.famiglia_dell_atto(atto)
    sposo, sposa = atto[0], atto[3]
    assert (sposo.padre, sposo.madre) == (2, 3)
    assert (sposa.padre, sposa.madre) == (5, 6)
    assert sposo.coniuge == 4 and sposa.coniuge == 1


def test_matrimonio_schema_raggruppato():
    """Sposo e sposa insieme, poi i quattro genitori.

    Letto per posizione soltanto, questo schema darebbe alla sposa tutti
    e quattro i genitori.
    """
    atto = matrimonio([
        "sposo", "sposa",
        "padre-sposo", "madre-sposo", "padre-sposa", "madre-sposa",
    ])
    identita.famiglia_dell_atto(atto)
    sposo, sposa = atto[0], atto[1]
    assert (sposo.padre, sposo.madre) == (3, 4)
    assert (sposa.padre, sposa.madre) == (5, 6)


def test_il_cognome_del_padre_vince_sulla_posizione():
    """Quando l'ordine della pagina e' irregolare, decide il cognome."""
    atto = matrimonio([
        "sposo", "sposa", "padre-sposa", "padre-sposo",
    ])
    identita.famiglia_dell_atto(atto)
    assert atto[0].padre == 4   # Giovanni Desiderio -> lo sposo Desiderio
    assert atto[1].padre == 3   # Noè Pelliccia      -> la sposa Pelliccia


def test_sposi_con_lo_stesso_cognome_ricadono_sulla_posizione():
    """Fra cugini il cognome non decide, e allora conta l'ordine."""
    atto = [
        menzione(1, 1, "sposo", "Domenico", "Pelliccia", tipo="matrimonio"),
        menzione(2, 1, "padre", "Giovanni", "Pelliccia", tipo="matrimonio"),
        menzione(3, 1, "sposa", "Maddalena", "Pelliccia", tipo="matrimonio"),
        menzione(4, 1, "padre", "Noè", "Pelliccia", tipo="matrimonio"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[0].padre == 2
    assert atto[2].padre == 4


# --- il riconoscimento ------------------------------------------------------

def riconosci(menzioni):
    """Gli stessi passaggi che fa carica_menzioni, sulle menzioni gia' pronte."""
    for gruppo in identita._per_atto(menzioni).values():
        identita.famiglia_dell_atto(gruppo)
    identita.finestre_dai_figli(menzioni)
    return identita.riconosci(menzioni)


def test_nessuna_menzione_va_persa():
    """La garanzia su cui si regge tutto l'archivio."""
    menzioni = [
        menzione(1, 1, "testimone", "Egidio", "Colaneri", anno=1834, eta=eta(33)),
        menzione(2, 2, "testimone", "Corinto", "Pelliccia", anno=1840, eta=eta(20)),
        menzione(3, 3, "ufficiale", "Vincenzo", "Colella", anno=1850),
        menzione(4, 4, "neonato", None, None, anno=1860),
    ]
    persone = riconosci(menzioni)
    raccolte = [m.id for p in persone for m in p.menzioni]
    assert sorted(raccolte) == [1, 2, 3, 4]


def test_stessi_genitori_stessa_persona():
    """Il caso piu' semplice: il neonato del 1850 e il defunto del 1852."""
    menzioni = [
        menzione(1, 1, "neonato", "Maria", "Pepe", anno=1850),
        menzione(2, 1, "padre", "Giuseppe", "Pepe", anno=1850),
        menzione(3, 1, "madre", "Calideta", "Petolina", anno=1850),
        menzione(4, 2, "defunto", "Maria", "Pepe", tipo="morte", anno=1852, eta=eta(2)),
        menzione(5, 2, "padre", "Giuseppe", "Pepe", tipo="morte", anno=1852),
        menzione(6, 2, "madre", "Calideta", "Petolina", tipo="morte", anno=1852),
    ]
    persone = riconosci(menzioni)
    maria = [p for p in persone if "Maria" in p.nomi]
    assert len(maria) == 1
    assert len(maria[0].menzioni) == 2


def test_genitori_diversi_persone_diverse():
    """Due omonimi con padri diversi non si uniscono, e nulla lo ribalta."""
    menzioni = [
        menzione(1, 1, "neonato", "Domenico", "Pelliccia", anno=1850),
        menzione(2, 1, "padre", "Giuseppe", "Pelliccia", anno=1850),
        menzione(3, 2, "neonato", "Domenico", "Pelliccia", anno=1851),
        menzione(4, 2, "padre", "Nicola", "Pelliccia", anno=1851),
    ]
    persone = riconosci(menzioni)
    domenici = [p for p in persone if "Domenico" in p.nomi]
    assert len(domenici) == 2


def test_due_atti_di_nascita_sono_due_bambini():
    menzioni = [
        menzione(1, 1, "neonato", "Vincenzo", "Colella", anno=1873),
        menzione(2, 2, "neonato", "Vincenzo", "Colella", anno=1881),
    ]
    persone = riconosci(menzioni)
    assert len([p for p in persone if "Vincenzo" in p.nomi]) == 2


def test_nessuno_compare_vivo_dopo_essere_morto():
    menzioni = [
        menzione(1, 1, "defunto", "Nicola", "Troilo", tipo="morte", anno=1850, eta=eta(60)),
        menzione(2, 2, "testimone", "Nicola", "Troilo", anno=1870, eta=eta(80)),
    ]
    persone = riconosci(menzioni)
    assert len(persone) == 2


def test_uomini_e_donne_non_si_confondono():
    menzioni = [
        menzione(1, 1, "padre", "Domenico", "Pepe", anno=1850, sesso="M"),
        menzione(2, 1, "madre", "Domenica", "Pepe", anno=1850, sesso="F"),
    ]
    persone = riconosci(menzioni)
    assert len(persone) == 2


def test_nessuna_maternita_di_sessant_anni():
    """Il veto piu' solido: viene dalla biologia, non dalla statistica."""
    menzioni = [
        menzione(1, 1, "madre", "Vitaliana", "Ottaviano", anno=1810, sesso="F"),
        menzione(2, 1, "padre", "Salvatore", "Colella", anno=1810, sesso="M"),
        menzione(3, 2, "madre", "Vitaliana", "Ottaviano", anno=1871, sesso="F"),
        menzione(4, 2, "padre", "Salvatore", "Colella", anno=1871, sesso="M"),
    ]
    persone = riconosci(menzioni)
    vitaliane = [p for p in persone if "Vitaliana" in p.nomi]
    assert len(vitaliane) == 2


def test_i_frammenti_dello_stesso_uomo_si_riuniscono():
    """Quattro testimoni identici sono un uomo, non quattro quarti d'uomo.

    Il riconoscimento incrementale li apre separati — nessuno porta un
    genitore, quindi si bloccano a vicenda — e tocca al consolidamento
    accorgersi che sono compatibili fra loro.
    """
    menzioni = [
        menzione(i, i, "testimone", "Vincenzo", "Colapietro", anno=1875,
                 eta=eta(68), professione="contadino")
        for i in range(1, 5)
    ]
    persone = riconosci(menzioni)
    assert len(persone) == 1
    assert len(persone[0].menzioni) == 4


def test_eta_incompatibili_restano_separate():
    menzioni = [
        menzione(1, 1, "testimone", "Vincenzo", "Colapietro", anno=1875, eta=eta(30)),
        menzione(2, 2, "testimone", "Vincenzo", "Colapietro", anno=1875, eta=eta(68)),
    ]
    persone = riconosci(menzioni)
    assert len(persone) == 2


def test_chi_non_ha_nome_non_si_attacca_a_nessuno():
    menzioni = [
        menzione(1, 1, "defunto", None, None, tipo="morte", anno=1835),
        menzione(2, 2, "defunto", None, None, tipo="morte", anno=1836),
    ]
    persone = riconosci(menzioni)
    assert len(persone) == 2


# --- le grafie dello stesso cognome ----------------------------------------

def test_la_grafia_rara_si_riunisce_alla_dominante():
    """'Cabella' accanto a 'Colella', con la stessa moglie: un uomo solo."""
    menzioni = [
        menzione(1, 1, "padre", "Salvatore", "Colella", anno=1810, sesso="M"),
        menzione(2, 1, "madre", "Vitaliana", "Ottaviano", anno=1810, sesso="F"),
        menzione(3, 2, "padre", "Salvatore", "Cabella", anno=1812, sesso="M"),
        menzione(4, 2, "madre", "Vitaliana", "Ottaviano", anno=1812, sesso="F"),
    ]
    for gruppo in identita._per_atto(menzioni).values():
        identita.famiglia_dell_atto(gruppo)
    # 'Colella' e' attestatissimo, 'Cabella' quasi mai: e' una penna che
    # ha sbagliato, non una famiglia.
    frequenze = identita.Counter({"Colella": 2295, "Cabella": 7})
    per_id = {m.id: m for m in menzioni}
    persone = identita.consolida(
        identita.riconosci(menzioni), identita.ChiaviFamiliari(per_id), frequenze
    )
    salvatori = [p for p in persone if "Salvatore" in p.nomi]
    assert len(salvatori) == 1


def test_due_famiglie_grandi_non_si_uniscono_mai():
    """Colella e Chielli sono due famiglie del paese, non due grafie.

    La prova strutturale c'e' — stessa moglie, stesso nome — ed e'
    esattamente il caso in cui si sbaglierebbe: a fermare l'unione non e'
    la somiglianza (0,57, nemmeno bassissima) ma il fatto che tutte e due
    le forme siano attestate migliaia di volte.
    """
    menzioni = [
        menzione(1, 1, "padre", "Salvatore", "Colella", anno=1810, sesso="M"),
        menzione(2, 1, "madre", "Vitaliana", "Ottaviano", anno=1810, sesso="F"),
        menzione(3, 2, "padre", "Salvatore", "Chielli", anno=1812, sesso="M"),
        menzione(4, 2, "madre", "Vitaliana", "Ottaviano", anno=1812, sesso="F"),
    ]
    for gruppo in identita._per_atto(menzioni).values():
        identita.famiglia_dell_atto(gruppo)
    frequenze = identita.Counter({"Colella": 2295, "Chielli": 1271})
    per_id = {m.id: m for m in menzioni}
    persone = identita.consolida(
        identita.riconosci(menzioni), identita.ChiaviFamiliari(per_id), frequenze
    )
    assert len([p for p in persone if "Salvatore" in p.nomi]) == 2


# --- le deduzioni da storico ------------------------------------------------

def test_il_dichiarante_e_il_padre_quando_manca_la_riga_padre():
    """Quattro atti di nascita su dieci non hanno una riga 'padre'.

    Il formulario dice "e' comparso Tal dei Tali, il quale ha dichiarato
    che e' nato un bambino da lui e da sua moglie": chi denuncia il
    figlio e' il padre. Senza questa lettura, quattro bambini su dieci
    restano senza paternita' e l'albero si spezza a ogni generazione.
    """
    atto = [
        menzione(1, 1, "dichiarante", "Camillo", "Di Nardo", anno=1842, sesso="M"),
        menzione(2, 1, "neonato", "Valentino", "Di Nardo", anno=1842),
        menzione(3, 1, "madre", "Sabbatina", "Pizzi", anno=1842, sesso="F"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[1].padre == 1
    assert atto[1].madre == 3
    assert atto[0].padre_dedotto


def test_la_levatrice_non_diventa_il_padre():
    """La decima volta il dichiarante non e' il padre: e' donna, o e'
    un parente con un altro cognome. Le tre guardie servono a quello."""
    atto = [
        menzione(1, 1, "dichiarante", "Rosa", "Colella", anno=1842, sesso="F",
                 professione="levatrice"),
        menzione(2, 1, "neonato", "Valentino", "Di Nardo", anno=1842),
        menzione(3, 1, "madre", "Sabbatina", "Pizzi", anno=1842, sesso="F"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[1].padre is None


def test_due_dichiaranti_non_decidono_chi_sia_il_padre():
    atto = [
        menzione(1, 1, "dichiarante", "Camillo", "Di Nardo", anno=1842, sesso="M"),
        menzione(2, 1, "dichiarante", "Giuseppe", "Di Nardo", anno=1842, sesso="M"),
        menzione(3, 1, "neonato", "Valentino", "Di Nardo", anno=1842),
        menzione(4, 1, "madre", "Sabbatina", "Pizzi", anno=1842, sesso="F"),
    ]
    identita.famiglia_dell_atto(atto)
    assert atto[2].padre is None


def test_la_nota_dichiara_una_parentela():
    """4.722 note su 8.434 contengono una parentela scritta a parole."""
    atto = [
        menzione(1, 1, "ufficiale", "Vincenzo", "Colella", tipo="morte", anno=1850),
        menzione(2, 1, "dichiarante", "Giuseppe", "Pepe", tipo="morte", anno=1850, sesso="M"),
        menzione(3, 1, "defunto", "Rita", "Lella", tipo="morte", anno=1850, sesso="F"),
    ]
    atto[1].note = "padre del defunto"
    identita.famiglia_dell_atto(atto)
    assert atto[2].padre == 2


def test_la_nota_regge_anche_il_nonno_materno():
    """'padre della madre' e' una generazione che nessun ruolo esprime."""
    atto = [
        menzione(1, 1, "neonato", "Maria", "Pepe", anno=1850),
        menzione(2, 1, "padre", "Giuseppe", "Pepe", anno=1850, sesso="M"),
        menzione(3, 1, "madre", "Calideta", "Petolina", anno=1850, sesso="F"),
        menzione(4, 1, "testimone", "Nicola", "Petolina", anno=1850, sesso="M"),
    ]
    atto[3].note = "padre della madre"
    identita.famiglia_dell_atto(atto)
    assert atto[2].padre == 4


def test_una_nota_ambigua_non_decide_niente():
    atto = [
        menzione(1, 1, "dichiarante", "Giuseppe", "Pepe", tipo="morte", anno=1850),
        menzione(2, 1, "dichiarante", "Nicola", "Pepe", tipo="morte", anno=1850),
        menzione(3, 1, "testimone", "Carmine", "Pepe", tipo="morte", anno=1850, sesso="M"),
    ]
    atto[2].note = "padre del dichiarante"
    identita.famiglia_dell_atto(atto)
    assert atto[0].padre is None and atto[1].padre is None


def test_nessuno_ha_due_madri():
    """Una bambina nasce e muore nello stesso anno; la madre e' letta
    'Paola' sull'atto di nascita e 'Piacoma' su quello di morte.

    Non sono due donne: e' una lettura sbagliata, e lasciarle divise
    dava a quella bambina tre genitori.
    """
    menzioni = [
        menzione(1, 1, "neonato", "Domenica", "Pepe", anno=1820),
        menzione(2, 1, "padre", "Domenico", "Pepe", anno=1820, sesso="M"),
        menzione(3, 1, "madre", "Paola", "Di Nardo", anno=1820, sesso="F", eta=eta(30)),
        menzione(4, 2, "defunto", "Domenica", "Pepe", tipo="morte", anno=1820, eta=eta(0)),
        menzione(5, 2, "padre", "Domenico", "Pepe", tipo="morte", anno=1820, sesso="M"),
        menzione(6, 2, "madre", "Piacoma", "Di Nardo", tipo="morte", anno=1820, sesso="F"),
    ]
    persone = riconosci(menzioni)
    bambina = [p for p in persone if "Domenica" in p.nomi][0]
    madri = {
        id(p) for p in persone
        for m in bambina.menzioni
        if m.madre is not None and any(x.id == m.madre for x in p.menzioni)
    }
    assert len(madri) == 1


def test_genitori_con_cognomi_estranei_restano_in_conflitto():
    """Se discordano nome **e** cognome non e' una scheda spezzata: e'
    un contrasto fra due pagine, e va lasciato in vista."""
    menzioni = [
        menzione(1, 1, "neonato", "Domenica", "Pepe", anno=1820),
        menzione(2, 1, "madre", "Paola", "Di Nardo", anno=1820, sesso="F"),
        menzione(3, 2, "defunto", "Domenica", "Pepe", tipo="morte", anno=1820, eta=eta(0)),
        menzione(4, 2, "madre", "Rosa", "Marianacci", tipo="morte", anno=1820, sesso="F"),
    ]
    persone = riconosci(menzioni)
    assert len([p for p in persone if "Paola" in p.nomi or "Rosa" in p.nomi]) == 2


def test_nessuno_e_figlio_di_chi_e_nato_dopo():
    """Filippo Lella, nato verso il 1750, figlio di due nati dopo di lui.

    E' l'assurdo piu' visibile che un albero possa mostrare, e nasce a
    monte — due omonimi in una scheda sola — ma va fermato qui, sul
    legame, che e' dove diventa visibile.
    """
    from history_maker import genealogia as g
    assert not g._cronologia_possibile({"anno_nascita": 1750}, {"anno_nascita": 1790}, "padre")
    assert not g._cronologia_possibile({"anno_nascita": 1750}, {"anno_nascita": 1745}, "padre")
    assert g._cronologia_possibile({"anno_nascita": 1790}, {"anno_nascita": 1750}, "padre")


def test_una_madre_non_partorisce_a_settant_anni():
    from history_maker import genealogia as g
    assert not g._cronologia_possibile({"anno_nascita": 1850}, {"anno_nascita": 1780}, "madre")
    assert g._cronologia_possibile({"anno_nascita": 1850}, {"anno_nascita": 1810}, "madre")


def test_senza_date_il_legame_resta():
    """Le date mancano per meta' delle persone: l'assenza non e' una prova."""
    from history_maker import genealogia as g
    assert g._cronologia_possibile({"anno_nascita": None}, {"anno_nascita": 1750}, "padre")


# --- che le persone non restino spezzate -----------------------------------
#
# Sono i casi che l'archivio sbagliava per difetto: non unioni fatte a
# torto, ma unioni **mancate**, che sono l'altro modo di raccontare male
# un paese. Una persona spezzata in cinque schede non si vede che e'
# rotta: si vede solo che i registri nominano cinque uomini.

def test_i_genitori_di_uno_sposo_sono_marito_e_moglie():
    """L'atto dice "figlio di X e di Y": X e Y sono una coppia.

    E' l'unico indizio strutturale che un padre nominato in un atto di
    matrimonio possa portare — non ha eta', non ha genitori suoi, non ha
    figli scritti li' dentro. Senza, ogni sua comparsa apriva una persona
    nuova che non aveva piu' modo di ricongiungersi alle altre.
    """
    atto = matrimonio([
        "sposo", "padre-sposo", "madre-sposo",
        "sposa", "padre-sposa", "madre-sposa",
    ])
    identita.famiglia_dell_atto(atto)
    padre_sposo, madre_sposo = atto[1], atto[2]
    padre_sposa, madre_sposa = atto[4], atto[5]
    assert (padre_sposo.coniuge, madre_sposo.coniuge) == (3, 2)
    assert (padre_sposa.coniuge, madre_sposa.coniuge) == (6, 5)
    # E le due coppie non si mescolano fra loro.
    assert padre_sposo.coniuge != madre_sposa.id


def test_lo_stesso_padre_in_due_matrimoni_e_un_uomo_solo():
    """Filippo Lella marita due figli in due anni: e' sempre lui."""
    menzioni = []
    for numero, (atto, anno, figlio) in enumerate(
        ((1, 1820, "Giuseppe"), (2, 1832, "Donato Carlo")), start=0
    ):
        base = numero * 3
        menzioni += [
            menzione(base + 1, atto, "sposo", figlio, "Lella",
                     tipo="matrimonio", anno=anno, sesso="M"),
            menzione(base + 2, atto, "padre", "Filippo", "Lella",
                     tipo="matrimonio", anno=anno, sesso="M"),
            menzione(base + 3, atto, "madre", "Margherita", "Rossi",
                     tipo="matrimonio", anno=anno, sesso="F"),
        ]
    persone = riconosci(menzioni)
    filippi = [p for p in persone if "Filippo" in p.nomi]
    assert len(filippi) == 1 and len(filippi[0].menzioni) == 2


def test_la_grafia_incerta_del_genitore_non_e_una_contraddizione():
    """Maria Clementina Lella, nata e morta nel 1810.

    L'atto di nascita scrive la madre 'Margherita', quello di morte
    'Margarita': due grafie a 0,80 l'una dall'altra, cioe' sotto la
    soglia che le dichiarerebbe la stessa donna. Trattarle come due madri
    **diverse** era un veto assoluto, e teneva divisa in due bambine una
    bambina che visse nove giorni.
    """
    menzioni = [
        menzione(1, 1, "neonata", "Maria Clementina", "Lella", anno=1810, sesso="F"),
        menzione(2, 1, "padre", "Filippo", "Lella", anno=1810, sesso="M"),
        menzione(3, 1, "madre", "Margherita", "Rossi", anno=1810, sesso="F"),
        menzione(4, 2, "defunta", "Maria Clementina", "Lella",
                 tipo="morte", anno=1810, sesso="F", eta=eta(0)),
        menzione(5, 2, "padre", "Filippo", "Lella", tipo="morte", anno=1810, sesso="M"),
        menzione(6, 2, "madre", "Margarita", "Rossi", tipo="morte", anno=1810, sesso="F"),
    ]
    persone = riconosci(menzioni)
    bambine = [p for p in persone if "Maria Clementina" in p.nomi]
    assert len(bambine) == 1 and len(bambine[0].menzioni) == 2


def test_due_madri_davvero_diverse_restano_un_veto():
    """La regola di sopra non deve diventare una porta aperta.

    'Margherita Rossi' e 'Rosa Marianacci' non sono una grafia incerta:
    sono due donne, e li' il veto deve reggere come prima.
    """
    assert identita._confronta_insiemi(
        {"margherita|rosi"}, {"margarita|rosi"}
    ) == 0
    assert identita._confronta_insiemi(
        {"margherita|rosi"}, {"rosa|marianaci"}
    ) == -1
    assert identita._confronta_insiemi(
        {"margherita|rosi"}, {"margherita|pelicia"}
    ) == -1


def test_il_fascio_senza_parentele_va_alla_persona_documentata():
    """Manasse Franchella, agrimensore, era due uomini invece di uno.

    Da una parte la scheda piena — moglie, figli, atto di morte —
    dall'altra un fascio di righe da testimone con lo stesso nome raro,
    lo stesso mestiere e la stessa eta'. Il fascio non porta parentele:
    unirlo non sposta nessun ramo, cambia solo in quale scheda stiano
    quelle righe.
    """
    menzioni = [
        menzione(1, 1, "padre", "Manasse", "Franchella", anno=1819,
                 sesso="M", eta=eta(34), professione="agrimensore"),
        menzione(2, 1, "madre", "Anna Rosa", "Colella", anno=1819, sesso="F"),
        menzione(3, 1, "neonato", "Egidio", "Franchella", anno=1819, sesso="M"),
        menzione(4, 2, "testimone", "Manasse", "Franchella", anno=1821,
                 sesso="M", eta=eta(36), professione="agrimensore"),
        menzione(5, 3, "testimone", "Manasse", "Franchella", anno=1832,
                 sesso="M", eta=eta(47), professione="agrimensore"),
    ]
    persone = riconosci(menzioni)
    manassi = [p for p in persone if "Manasse" in p.nomi]
    assert len(manassi) == 1
    assert len(manassi[0].menzioni) == 3
    # L'unione e' un'inferenza, e l'archivio la dichiara.
    assert manassi[0].incerte == {4, 5}


def test_il_fascio_resta_solo_se_gli_omonimi_sono_due():
    """Dove ci sono due Domenico Pelliccia compatibili, non si sceglie.

    E' la stessa dottrina del resto del modulo: fra sbagliare unendo e
    sbagliare separando, separare lascia il dato ispezionabile.
    """
    menzioni = [
        # Due omonimi tenuti separati da cio' che li separa davvero: due
        # atti di nascita diversi e due coppie di genitori diverse.
        menzione(1, 1, "neonato", "Domenico", "Pelliccia", anno=1800, sesso="M"),
        menzione(2, 1, "padre", "Nicola", "Pelliccia", anno=1800, sesso="M"),
        menzione(3, 1, "madre", "Angela", "Lella", anno=1800, sesso="F"),
        menzione(4, 2, "neonato", "Domenico", "Pelliccia", anno=1804, sesso="M"),
        menzione(5, 2, "padre", "Giuseppe", "Pelliccia", anno=1804, sesso="M"),
        menzione(6, 2, "madre", "Maria", "Di Nardo", anno=1804, sesso="F"),
        # Un testimone che potrebbe essere l'uno o l'altro.
        menzione(7, 3, "testimone", "Domenico", "Pelliccia", anno=1842,
                 sesso="M", eta=eta(40), professione="contadino"),
    ]
    persone = riconosci(menzioni)
    domenici = [p for p in persone if "Domenico" in p.nomi]
    assert len(domenici) == 3
    assert all(len(p.menzioni) == 1 for p in domenici)


def test_nessuna_donna_partorisce_dopo_essere_morta():
    """Il piu' netto dei controlli sulla cronologia, e mancava."""
    from history_maker import genealogia as g
    morta = {"anno_nascita": 1790, "anno_morte": 1830}
    assert not g._cronologia_possibile(
        {"anno_nascita": 1835, "nascita_origine": "certa"}, morta, "madre"
    )
    assert g._cronologia_possibile(
        {"anno_nascita": 1828, "nascita_origine": "certa"}, morta, "madre"
    )
    # Il figlio postumo del padre morto durante la gravidanza esiste, e
    # gli atti lo scrivono.
    assert g._cronologia_possibile(
        {"anno_nascita": 1831, "nascita_origine": "certa"}, morta, "padre"
    )
    # Quando l'anno di nascita e' solo stimato da un'eta' dichiarata, il
    # margine e' quello dell'arrotondamento dei registri.
    assert g._cronologia_possibile(
        {"anno_nascita": 1832, "nascita_origine": "stimata"}, morta, "madre"
    )
    assert not g._cronologia_possibile(
        {"anno_nascita": 1840, "nascita_origine": "stimata"}, morta, "madre"
    )


def test_chi_partorisce_nel_1844_non_e_morta_nel_1810():
    """La neonata morta a nove giorni non e' madre di nessuno.

    Il ruolo 'madre' non implica la presenza — una madre morta viene
    nominata negli atti dei figli per trent'anni — ma il genitore di un
    atto di **nascita** e' il parto, e chi partorisce quell'anno era
    viva quell'anno.
    """
    menzioni = [
        menzione(1, 1, "neonata", "Clementina", "Lella", anno=1810, sesso="F"),
        menzione(2, 1, "padre", "Filippo", "Lella", anno=1810, sesso="M"),
        menzione(3, 1, "madre", "Margherita", "Rossi", anno=1810, sesso="F"),
        menzione(4, 2, "defunta", "Clementina", "Lella",
                 tipo="morte", anno=1810, sesso="F", eta=eta(0)),
        menzione(5, 2, "padre", "Filippo", "Lella", tipo="morte", anno=1810, sesso="M"),
        menzione(6, 2, "madre", "Margherita", "Rossi", tipo="morte", anno=1810, sesso="F"),
        # Trentaquattro anni dopo, una sua omonima partorisce.
        menzione(7, 3, "madre", "Clementina", "Lella", anno=1844,
                 sesso="F", eta=eta(32)),
        menzione(8, 3, "neonato", "Nicola", "Pepe", anno=1844, sesso="M"),
    ]
    persone = riconosci(menzioni)
    clementine = [p for p in persone if "Clementina" in p.nomi]
    assert len(clementine) == 2
    morta = next(p for p in clementine if p.morte == 1810)
    assert morta.parti == []


def test_nessuno_compare_prima_di_essere_nato():
    """La sposa del 1813 non e' la bambina nata nel 1820.

    E' il veto piu' netto del modulo: non poggia su un'eta' arrotondata
    ma sull'atto di nascita, che e' una data scritta. Senza, Angela Di
    Nardo era due donne — una di ventidue menzioni e una di sette — e la
    scheda sbagliata bloccava quella giusta.
    """
    menzioni = [
        menzione(1, 1, "sposa", "Angela", "Di Nardo", tipo="matrimonio",
                 anno=1813, sesso="F"),
        menzione(2, 1, "padre", "Nicola", "Di Nardo", tipo="matrimonio",
                 anno=1813, sesso="M"),
        menzione(3, 1, "sposo", "Domenico", "Lella", tipo="matrimonio",
                 anno=1813, sesso="M"),
        menzione(4, 2, "neonato", "Angela", "Di Nardo", anno=1820, sesso="F"),
        menzione(5, 2, "padre", "Nicola", "Di Nardo", anno=1820, sesso="M"),
    ]
    persone = riconosci(menzioni)
    angele = [p for p in persone if "Angela" in p.nomi]
    assert len(angele) == 2


def test_la_moglie_e_una_persona_non_un_nome_ricopiato():
    """Giuseppe Lella era due uomini con la stessa moglie.

    Lo scrivano del 1843 scrive 'Coletti' invece di 'Colella'. La donna
    l'archivio la riconosce lo stesso — glielo dicono il nome, il marito
    e la ricucitura delle grafie — ma le due schede del **marito** si
    confrontavano fra loro guardando il cognome ricopiato di lei, e non
    si trovavano. Qui si verifica il passaggio che le fa incontrare: i
    legami riscritti come persone invece che come nomi.
    """
    menzioni = [
        menzione(1, 1, "padre", "Giuseppe", "Lella", anno=1840,
                 sesso="M", eta=eta(40), professione="contadino"),
        menzione(2, 1, "madre", "Annangela", "Colella", anno=1840, sesso="F"),
        menzione(3, 2, "padre", "Giuseppe", "Lella", anno=1843,
                 sesso="M", eta=eta(43), professione="contadino"),
        menzione(4, 2, "madre", "Annangela", "Coletti", anno=1843, sesso="F"),
    ]
    for gruppo in identita._per_atto(menzioni).values():
        identita.famiglia_dell_atto(gruppo)
    per_id = {m.id: m for m in menzioni}
    chiavi = identita.ChiaviFamiliari(per_id)

    # Lo stato di partenza: i due mariti sono due schede, la moglie e'
    # gia' una sola — e' proprio la situazione che si trovava nei dati.
    marito_uno = identita.Persona(id=1)
    marito_uno.aggiungi(menzioni[0], chiavi)
    marito_due = identita.Persona(id=2)
    marito_due.aggiungi(menzioni[2], chiavi)
    moglie = identita.Persona(id=3)
    moglie.aggiungi(menzioni[1], chiavi)
    moglie.aggiungi(menzioni[3], chiavi)
    persone = [marito_uno, marito_due, moglie]

    # Per il confronto fra nomi ricopiati i due mariti non si toccano.
    assert identita._confronta_insiemi(marito_uno.coniugi, marito_due.coniugi) <= 0
    assert not identita._accostabili(marito_uno, marito_due)

    # Per l'identita' della moglie, si'.
    assert identita._accosta_per_identita(persone, chiavi)
    vive = [p for p in persone if p.menzioni]
    mariti = [p for p in vive if "Giuseppe" in p.nomi]
    assert len(mariti) == 1 and len(mariti[0].menzioni) == 2


def test_due_padri_dello_stesso_figlio_sono_un_padre_solo():
    """Carmine Moretta: stessa moglie, stessi figli, dieci anni di scarto.

    L'eta' dichiarata e' il dato piu' debole del registro. Quando
    dall'altra parte c'e' un figlio che e' la **stessa persona**, e'
    l'eta' a doversi piegare, non la parentela.
    """
    menzioni = [
        menzione(1, 1, "padre", "Carmine", "Moretta", anno=1819,
                 sesso="M", eta=eta(41), professione="contadino"),
        menzione(2, 1, "madre", "Maria", "Lella", anno=1819, sesso="F"),
        menzione(3, 1, "neonato", "Luigi", "Moretta", anno=1819, sesso="M"),
        menzione(4, 2, "defunto", "Luigi", "Moretta", tipo="morte", anno=1880,
                 sesso="M", eta=eta(61)),
        menzione(5, 2, "padre", "Carmine", "Moretta", tipo="morte", anno=1880,
                 sesso="M"),
        menzione(6, 2, "madre", "Maria", "Lella", tipo="morte", anno=1880, sesso="F"),
        menzione(7, 3, "padre", "Carmine", "Moretta", anno=1826,
                 sesso="M", eta=eta(38), professione="contadino"),
        menzione(8, 3, "madre", "Maria", "Lella", anno=1826, sesso="F"),
        menzione(9, 3, "neonato", "Pasqualina", "Moretta", anno=1826, sesso="F"),
    ]
    persone = riconosci(menzioni)
    carmini = [p for p in persone if "Carmine" in p.nomi]
    assert len(carmini) == 1


def test_il_fu_non_e_parte_del_nome():
    """'fu Margherita Profio' e 'Margherita Profio' sono una donna sola."""
    from history_maker import nomi as n
    assert n.separa_stato_vitale("fu Margherita") == ("Margherita", True)
    assert n.separa_stato_vitale("il fu Nicola") == ("Nicola", True)
    # E non si tocca chi il nome ce l'ha che comincia per 'fu'.
    assert n.separa_stato_vitale("Fulvio") == ("Fulvio", False)


def test_una_riga_con_una_colonna_sola_non_si_scambia():
    """'padre: Carmine' non diventa un cognome 'Carmine' senza nome."""
    from history_maker import nomi as n
    bilancia = n.Bilancia.dal_corpus(
        [("Carmine", "Moretta")] * 20 + [("Moretta", "Giuseppe")] * 20
    )
    atto = {1: 1, 2: 1, 3: 1}
    letture = {
        1: ("Moretta", "Giuseppe"),
        2: ("Moretta", "Nicola"),
        3: ("Carmine", None),
    }
    scambi = n.inversioni_per_atto(atto, letture, bilancia)
    assert 3 not in scambi


def test_il_padre_di_un_settantenne_non_ha_quarantacinque_anni():
    """La riga del genitore non dichiara l'eta', ma l'atto la dichiara accanto.

    Nicola Lella, padre di un Filippo morto a settant'anni nel 1828, e'
    nato intorno al 1735. Il taglialegna di quarantacinque anni che nel
    1839 fa da padre a un neonato e' nato nel 1794. Portano lo stesso
    nome e lo stesso cognome, e fino a qui finivano nella stessa scheda:
    la riga del 1828 non aveva un'eta', quindi non portava **nessun**
    vincolo di tempo, e si attaccava a chiunque.
    """
    menzioni = [
        menzione(1, 1, "defunto", "Filippo", "Lella", tipo="morte", anno=1828,
                 sesso="M", eta=eta(70)),
        menzione(2, 1, "padre", "Nicola", "Lella", tipo="morte", anno=1828,
                 sesso="M", professione="contadino"),
        menzione(3, 1, "madre", "Antonia", "Coletta", tipo="morte", anno=1828, sesso="F"),
        menzione(4, 2, "padre", "Nicola", "Lella", anno=1839,
                 sesso="M", eta=eta(45), professione="contadino"),
        menzione(5, 2, "madre", "Maria Giuseppa", "Franchella", anno=1839, sesso="F"),
        menzione(6, 2, "neonato", "Nicola", "Lella", anno=1839, sesso="M"),
    ]
    persone = riconosci(menzioni)
    nicola_adulti = [
        p for p in persone
        if "Nicola" in p.nomi and any(m.ruolo == "padre" for m in p.menzioni)
    ]
    assert len(nicola_adulti) == 2
    # E il vincolo e' quello giusto. Filippo, settant'anni nel 1828, e'
    # nato verso il 1758: suo padre non puo' essere nato dopo il 1745,
    # piu' i cinque anni di margine che si lasciano perche' il 1758 viene
    # da un'eta' dichiarata e non da un atto di nascita.
    padre_di_filippo = next(p for p in nicola_adulti if any(m.anno == 1828 for m in p.menzioni))
    assert padre_di_filippo.finestra is not None
    assert padre_di_filippo.finestra[1] == 1758 - identita.ETA_MINIMA_GENITORE + identita.MARGINE_FINESTRA
    # In ogni caso, molto prima del taglialegna nato nel 1794.
    assert padre_di_filippo.finestra[1] < 1794


def test_la_finestra_di_nascita_si_ricava_dal_figlio():
    atto = [
        menzione(1, 1, "neonato", "Maria", "Pepe", anno=1850),
        menzione(2, 1, "padre", "Giuseppe", "Pepe", anno=1850, sesso="M"),
        menzione(3, 1, "madre", "Calideta", "Petolina", anno=1850, sesso="F"),
    ]
    identita.famiglia_dell_atto(atto)
    identita.finestre_dai_figli(atto)
    # Il neonato e' nato quell'anno esatto.
    assert atto[0].finestra == (1850, 1850)
    # Il padre fra i 13 e i 75 anni prima; la madre fra i 13 e i 55.
    assert atto[1].finestra == (1850 - 75, 1850 - 13)
    assert atto[2].finestra == (1850 - 55, 1850 - 13)


def test_una_moglie_diversa_toglie_il_beneficio_del_dubbio():
    """Non e' un veto — ci si risposa — ma non vale piu' zero.

    Con due coniugi che si contraddicono, il nome e un mestiere in comune
    non bastano piu' a unire due righe per sola unicita' del candidato.
    """
    per_id = {}
    def m(id, atto, ruolo, nome, cognome, **extra):
        v = menzione(id, atto, ruolo, nome, cognome, **extra)
        per_id[id] = v
        return v

    atto_uno = [
        m(1, 1, "padre", "Nicola", "Lella", anno=1839, sesso="M",
          eta=eta(45), professione="contadino"),
        m(2, 1, "madre", "Maria Giuseppa", "Franchella", anno=1839, sesso="F"),
        m(3, 1, "neonato", "Anna", "Lella", anno=1839, sesso="F"),
    ]
    atto_due = [
        m(4, 2, "padre", "Nicola", "Lella", anno=1841, sesso="M",
          eta=eta(47), professione="contadino"),
        m(5, 2, "madre", "Rosa", "Pelliccia", anno=1841, sesso="F"),
        m(6, 2, "neonato", "Pietro", "Lella", anno=1841, sesso="M"),
    ]
    for gruppo in (atto_uno, atto_due):
        identita.famiglia_dell_atto(gruppo)
    chiavi = identita.ChiaviFamiliari(per_id)
    persona = identita.Persona(id=1)
    persona.aggiungi(atto_uno[0], chiavi)

    valore = identita.punteggio(persona, atto_due[0], chiavi)
    assert valore is not None
    # Sotto la soglia con cui si unisce un candidato unico.
    assert valore < identita.SOGLIA_CANDIDATO_UNICO
