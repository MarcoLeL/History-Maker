"""Il raggruppamento delle varianti: stesso risultato, molto meno lavoro.

Confrontare ogni cognome con ogni altro cresce col quadrato delle forme.
Su 86 pagine erano ~200 forme e 20.000 confronti: un secondo. Su 5.099
pagine sono 3.301 forme e 5,4 milioni di confronti a 297 microsecondi
l'uno: **ventisette minuti**, ogni volta che si ricostruisce il dataset.

Il filtro che li evita non e' un'euristica ma un limite dimostrato: la
somiglianza vale ``1 - distanza/lunghezza_massima`` e inserire o
cancellare un carattere costa 1,0, quindi ``distanza >= |len(a)-len(b)|``.
Perche' due forme superino la soglia serve ``len(corta) >= soglia *
len(lunga)``.

Questi test difendono la sola cosa che conta: **il risultato non deve
cambiare**. Un'ottimizzazione che raggruppa diversamente non e' piu'
veloce, e' sbagliata.
"""

import itertools
import random

import pytest

from history_maker import paleografia
from history_maker.normalizza import SOGLIA_GRUPPO, _raggruppa


def _esaustivo(forme, soglia=SOGLIA_GRUPPO):
    """Il raggruppamento ingenuo: tutte le coppie, nessun filtro."""
    padre = {f: f for f in forme}

    def radice(f):
        while padre[f] != f:
            padre[f] = padre[padre[f]]
            f = padre[f]
        return f

    for a, b in itertools.combinations(forme, 2):
        if paleografia.forma_canonica(a) == paleografia.forma_canonica(b) or (
            paleografia.somiglianza(a, b) >= soglia
        ):
            ra, rb = radice(a), radice(b)
            if ra != rb:
                padre[ra] = rb
    gruppi = {}
    for f in forme:
        gruppi.setdefault(radice(f), []).append(f)
    return {frozenset(g) for g in gruppi.values()}


def _insiemi(gruppi):
    return {frozenset(g) for g in gruppi}


COGNOMI = [
    "Pelliccia", "Pellicia", "Pelliccio", "Bracchi", "Bracci", "Brachi",
    "Di Nardo", "Dinardo", "De Nardo", "Cicchillitti", "Cicchillitto",
    "Pizzi", "Pipia", "Sipia", "Troilo", "Trojlo", "Colella", "Colello",
    "Franchella", "Franchelli", "Manes", "Mares", "Torzi", "Torzo",
    "Femminilli", "Tommolilli", "Marianacci", "Nicodemo", "Desiderio",
]


def test_lo_stesso_risultato_del_confronto_esaustivo():
    """La prova che conta: nessun gruppo deve cambiare."""
    assert _insiemi(_raggruppa(COGNOMI)) == _esaustivo(COGNOMI)


def test_regge_su_forme_generate_a_caso():
    """Trenta corpus casuali, per non fidarsi di un solo esempio."""
    alfabeto = "abcdeilmnoprstuz"
    casuale = random.Random(20260831)
    for _ in range(30):
        forme = list({
            "".join(casuale.choices(alfabeto, k=casuale.randint(3, 12)))
            for _ in range(casuale.randint(5, 40))
        })
        assert _insiemi(_raggruppa(forme)) == _esaustivo(forme), forme


def test_le_varianti_vere_restano_insieme():
    """Il caso d'uso: grafie diverse dello stesso cognome."""
    gruppi = _insiemi(_raggruppa(["Pelliccia", "Pellicia", "Bracchi", "Brachi"]))
    assert frozenset({"Pelliccia", "Pellicia"}) in gruppi
    assert frozenset({"Bracchi", "Brachi"}) in gruppi


def test_la_forma_canonica_unisce_anche_lunghezze_diverse():
    """'Di Nardo' e 'Dinardo' differiscono di due caratteri.

    Il filtro sulla lunghezza da solo le separerebbe: per questo il
    raggruppamento per forma canonica viene PRIMA, ed e' per chiave.
    """
    gruppi = _insiemi(_raggruppa(["Di Nardo", "Dinardo"]))
    assert len(gruppi) == 1


def test_due_cognomi_estranei_restano_separati():
    gruppi = _insiemi(_raggruppa(["Pelliccia", "Marianacci", "Nicodemo"]))
    assert len(gruppi) == 3


@pytest.mark.parametrize("forme", [[], ["Solo"], ["Uno", "Due"]])
def test_i_casi_minimi(forme):
    assert _insiemi(_raggruppa(forme)) == _esaustivo(forme)
