"""Il patronimico: l'unica cosa che i registri scrivono per distinguere."""

from history_maker.ricostruzione import evidenza
from history_maker.ricostruzione.scheda import Scheda


def _scheda(patronimici, chiave="k"):
    s = Scheda(chiave=chiave)
    s.patronimici = set(patronimici)
    return s


def test_due_padri_diversi_sono_un_veto():
    """Il caso vero: «Domenico di Filippo Lella» e «Domenico di Vito Lella»."""
    detto = evidenza.veti(_scheda({"Filippo"}), _scheda({"Vito"}, "j"))
    assert detto and "due padri diversi" in detto


def test_lo_stesso_padre_letto_male_non_e_un_veto():
    """'Nardo' e 'Kardo' sono una penna sola: 0,80 di somiglianza."""
    assert evidenza.veti(_scheda({"Nardo"}), _scheda({"Kardo"}, "j")) is None
    assert evidenza.veti(_scheda({"Leonardo"}), _scheda({"Lonardo"}, "j")) is None
    assert evidenza.veti(_scheda({"Gioachino"}), _scheda({"Gioacchino"}, "j")) is None


def test_un_patronimico_in_comune_basta():
    assert evidenza.veti(_scheda({"Vito", "Filippo"}), _scheda({"Vito"}, "j")) is None


def test_senza_patronimico_non_si_veta_niente():
    assert evidenza.veti(_scheda(set()), _scheda({"Vito"}, "j")) is None
    assert evidenza.veti(_scheda({"Vito"}), _scheda(set(), "j")) is None


def test_due_patronimici_nella_stessa_scheda_sono_una_fusione():
    scheda = _scheda({"Filippo", "Vito"})
    assert any("due patronimici" in g for g in evidenza.incoerenze(scheda))


def test_due_grafie_dello_stesso_patronimico_non_lo_sono():
    assert evidenza.incoerenze(_scheda({"Nardo", "Kardo"})) == []


# --- sposarsi due volte si puo', cambiare nome no --------------------

class _M:
    def __init__(self, atto, tipo_atto, ruolo, nome, cognome):
        self.atto, self.tipo_atto, self.ruolo = atto, tipo_atto, ruolo
        self.nome, self.cognome = nome, cognome


def _con_nozze(*menzioni):
    s = Scheda(chiave="k")
    s.menzioni = list(menzioni)
    return s


def test_due_nozze_con_nomi_diversi_sono_due_persone():
    """Il caso vero: «Angiola di Pardo» nel 1813, «Maria Saveria Clementina»
    nel 1827."""
    scheda = _con_nozze(
        _M(1, "matrimonio", "sposa", "Angiola", "di Pardo"),
        _M(2, "matrimonio", "sposa", "Maria Saveria Clementina", "Di Nardo"))
    assert evidenza._due_matrimoni_con_nomi_diversi(scheda)


def test_le_seconde_nozze_di_una_vedova_non_sono_una_contraddizione():
    scheda = _con_nozze(
        _M(1, "matrimonio", "sposa", "Angela", "Di Nardo"),
        _M(2, "matrimonio", "sposa", "Angiola", "Di Nardo"))
    assert evidenza._due_matrimoni_con_nomi_diversi(scheda) is None


def test_un_matrimonio_solo_non_dice_niente():
    scheda = _con_nozze(_M(1, "matrimonio", "sposa", "Angiola", "di Pardo"))
    assert evidenza._due_matrimoni_con_nomi_diversi(scheda) is None


def test_la_stessa_persona_nello_stesso_atto_non_conta_due_volte():
    """Gli atti nominano gli sposi due volte: in apertura e alla formula."""
    scheda = _con_nozze(
        _M(1, "matrimonio", "sposa", "Angiola", "di Pardo"),
        _M(1, "matrimonio", "sposa", "Angiola Maria", "di Pardo"))
    assert evidenza._due_matrimoni_con_nomi_diversi(scheda) is None


# --- due nomi di battesimo in una scheda sola ------------------------

class _N:
    def __init__(self, nome): self.nome = nome


def _con_nomi(*coppie):
    s = Scheda(chiave="k")
    s.menzioni = [_N(n) for n, quante in coppie for _ in range(quante)]
    return s


def test_due_fratelli_in_una_scheda_sola():
    """Il caso vero: «Nicola Di Nardo» x27 e «Camillo Di Nardo» x34."""
    detto = evidenza._due_nomi_di_battesimo(_con_nomi(("Nicola", 27), ("Camillo", 34)))
    assert detto and "due nomi di battesimo" in detto


def test_un_titolo_in_mezzo_non_e_un_altro_uomo():
    assert evidenza._due_nomi_di_battesimo(
        _con_nomi(("Giovanni", 79), ("Giovanni Dottor Savicoli", 17))) is None


def test_lo_stesso_nome_attaccato_o_staccato():
    assert evidenza._due_nomi_di_battesimo(
        _con_nomi(("Giuseppe Antonio", 6), ("Giuseppantonio", 13))) is None


def test_un_nome_che_e_il_prefisso_dell_altro():
    assert evidenza._due_nomi_di_battesimo(
        _con_nomi(("Domenico", 17), ("Domenicantonio", 10))) is None


def test_una_lettura_sbagliata_isolata_non_spezza_la_scheda():
    """Serve che tutt'e due le forme siano frequenti."""
    assert evidenza._due_nomi_di_battesimo(
        _con_nomi(("Camillo", 60), ("Nicola", 2))) is None


# --- la stessa riga di un atto, letta due volte ----------------------

class _R:
    def __init__(self, atto, ruolo, nome, cognome, evento=None):
        self.atto, self.ruolo, self.nome, self.cognome = atto, ruolo, nome, cognome
        # L'evento a cui la riga appartiene: di norma il suo atto, ma due
        # atti gemelli — le tre pagine di un matrimonio, la promessa
        # affissa due volte — ne condividono uno.
        self.evento = evento or atto


def _in_atto(*righe):
    s = Scheda(chiave=str(id(righe)))
    s.menzioni = list(righe)
    s.atti = {r.atto for r in righe}
    s.eventi = {r.evento for r in righe}
    return s


def test_gli_sposi_nominati_due_volte_sono_una_persona():
    """Il caso vero: l'atto n. 1 del 1813 nomina la sposa in apertura
    e nella formula che la dichiara unita in matrimonio."""
    una = _in_atto(_R(251, "sposa", "Angiola", "di Pardo"))
    altra = _in_atto(_R(251, "sposa", "Angiola", "di Pardo"))
    assert evidenza._nominata_due_volte_nello_stesso_atto(una, altra)


def test_due_testimoni_omonimi_restano_due_persone():
    """Il testimone non e' un ruolo unico: due omonimi ci stanno."""
    una = _in_atto(_R(251, "testimone", "Giuseppe", "Lella"))
    altra = _in_atto(_R(251, "testimone", "Giuseppe", "Lella"))
    assert evidenza._nominata_due_volte_nello_stesso_atto(una, altra) is None


def test_lo_stesso_ruolo_in_atti_diversi_non_dice_niente():
    una = _in_atto(_R(251, "sposa", "Angiola", "di Pardo"))
    altra = _in_atto(_R(999, "sposa", "Angiola", "di Pardo"))
    assert evidenza._nominata_due_volte_nello_stesso_atto(una, altra) is None


def test_due_nomi_diversi_nello_stesso_ruolo_non_si_uniscono():
    una = _in_atto(_R(251, "sposa", "Angiola", "di Pardo"))
    altra = _in_atto(_R(251, "sposa", "Rosa", "Pelliccia"))
    assert evidenza._nominata_due_volte_nello_stesso_atto(una, altra) is None


# --- non si dichiara la propria morte --------------------------------

def test_dichiarante_e_defunto_sono_due_persone():
    """Otto atti dell'archivio avevano la stessa scheda in tutt'e due i ruoli."""
    coppie = [set(x) for x in evidenza.COPPIE_ESCLUSIVE]
    assert {"dichiarante", "defunto"} in coppie
    assert {"testimone", "defunto"} in coppie


def test_le_coppie_di_prima_restano():
    coppie = [set(x) for x in evidenza.COPPIE_ESCLUSIVE]
    assert {"sposo", "sposa"} in coppie and {"padre", "madre"} in coppie


class _Riga:
    def __init__(self, atto, ruolo):
        self.atto, self.ruolo, self.nome, self.cognome = atto, ruolo, None, None
        self.tipo_atto = "morte"


def _scheda_con(*righe):
    s = Scheda(chiave="k")
    s.menzioni = list(righe)
    return s


def test_chi_dichiara_la_propria_morte_e_due_persone():
    scheda = _scheda_con(_Riga(1, "dichiarante"), _Riga(1, "defunto"))
    guai = evidenza.incoerenze(scheda)
    assert any("dichiara due persone" in g for g in guai)


def test_gli_stessi_ruoli_in_atti_diversi_stanno_bene():
    """Dichiarare la morte di uno e morire in un altro atto e' normale."""
    scheda = _scheda_con(_Riga(1, "dichiarante"), _Riga(2, "defunto"))
    assert evidenza.incoerenze(scheda) == []


def test_sposo_e_sposa_nello_stesso_atto_restano_vietati():
    scheda = _scheda_con(_Riga(7, "sposo"), _Riga(7, "sposa"))
    assert any("dichiara due persone" in g for g in evidenza.incoerenze(scheda))
