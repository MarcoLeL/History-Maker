"""«figlio del defunto Saverio» non e' «figlio del defunto».

Nella morte del 1867 (atto 3970) i due dichiaranti, cugini del morto,
sono descritti «figlio del defunto Saverio»: il loro padre. Presi come
figli di Lorenzo Desiderio gli davano un figlio nato nel 1817, due anni
dopo di lui, e la sua scheda veniva spaccata in due a ogni giro.
"""

from history_maker import menzioni as mod


def _menzione(id, ruolo, nome, note=None, eta=None, sesso=None):
    m = mod.Menzione(
        id=id, atto=1, tipo_atto="morte", anno=1867, data=None, ruolo=ruolo,
        nome=nome, cognome="Desiderio", nome_letto=nome, cognome_letto="Desiderio",
        cognome_origine=None, incerto=False, eta_letta=None, professione=None,
        residenza=None, via=None, stato_vitale=None, note=note, immagine=None, eta=eta)
    m.sesso = sesso
    return m


def test_il_nome_dopo_il_defunto_e_un_patronimico():
    dichiarante = _menzione(1, "dichiarante", "Luigi", note="figlio del defunto Saverio")
    defunto = _menzione(2, "defunto", "Lorenzo", sesso="M")
    mod.parentele_dalle_note([dichiarante, defunto])
    assert dichiarante.padre is None


def test_senza_nome_la_nota_lega_al_defunto_dell_atto():
    dichiarante = _menzione(1, "dichiarante", "Luigi", note="figlio del defunto")
    defunto = _menzione(2, "defunto", "Lorenzo", sesso="M")
    mod.parentele_dalle_note([dichiarante, defunto])
    assert dichiarante.padre == defunto.id


def test_la_virgola_dopo_il_ruolo_non_e_un_nome():
    dichiarante = _menzione(1, "dichiarante", "Luigi", note="figlio del defunto, presente all'atto")
    defunto = _menzione(2, "defunto", "Lorenzo", sesso="M")
    mod.parentele_dalle_note([dichiarante, defunto])
    assert dichiarante.padre == defunto.id
