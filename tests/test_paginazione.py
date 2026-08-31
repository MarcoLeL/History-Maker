"""La ricerca del portale e' impaginata, e la prima pagina sembra tutto.

Il guasto che questi test difendono e' costato meta' del catalogo senza
dare alcun segnale. La ricerca mostra **dieci** risultati per pagina e
impagina il resto; i risultati oltre il primo blocco non stanno
nell'HTML. Leggere solo la prima pagina non produce nessun errore: dieci
gallerie sono un numero perfettamente credibile per un anno.

Si e' visto solo guardando la distribuzione: su novanta anni interrogati,
**quarantasei si fermavano esatti a dieci e nessuno ne restituiva
undici**. Un tetto tondo, che e' la firma di una paginazione letta a
meta'.
"""

import pytest

from history_maker.discover import (
    MAX_PAGINE,
    PARAM_DIMENSIONE,
    PARAM_PAGINA,
    RISULTATI_PER_PAGINA,
    pagine_di_risultati,
    url_ricerca,
)


# --- l'URL chiede tutto quello che il portale sa dare ----------------------


def test_l_url_chiede_il_massimo_di_risultati_per_pagina():
    """Il difetto in una riga: senza questo parametro se ne ottengono dieci."""
    url = url_ricerca("Torrebruna", 1850)
    assert f"{PARAM_DIMENSIONE}={RISULTATI_PER_PAGINA}" in url
    assert RISULTATI_PER_PAGINA == 100  # il massimo che il selettore offre


def test_la_prima_pagina_non_porta_il_numero():
    """Un URL pulito per il caso normale, che e' quasi sempre l'unico."""
    assert PARAM_PAGINA not in url_ricerca("Torrebruna", 1850)


def test_le_pagine_successive_lo_portano():
    assert f"{PARAM_PAGINA}=3" in url_ricerca("Torrebruna", 1850, pagina=3)


def test_l_anno_resta_facoltativo():
    """La passata finale interroga senza filtro sull'anno."""
    url = url_ricerca("Torrebruna")
    assert "localita=Torrebruna" in url
    assert "anno=" not in url


# --- quante pagine ci sono, lo dice il portale -----------------------------


@pytest.mark.parametrize(
    "html, atteso",
    [
        ("<span> Pagina 1 di 2 </span>", 2),
        ("<span>Pagina 3 di 17</span>", 17),
        ("PAGINA 1 DI 4", 4),
        ("<span>Pagina 1 di 1</span>", 1),
    ],
)
def test_le_pagine_totali_si_leggono_dalla_pagina(html, atteso):
    assert pagine_di_risultati(html) == atteso


def test_senza_paginazione_si_assume_una_pagina_sola():
    """Una ricerca con pochi risultati non stampa nessun 'Pagina N di M'."""
    assert pagine_di_risultati("<html><body>nessun risultato</body></html>") == 1


def test_un_numero_malformato_non_fa_esplodere_nulla():
    assert pagine_di_risultati("Pagina uno di due") == 1


def test_c_e_un_tetto_al_numero_di_pagine():
    """Un ciclo che chiede 'avanti' all'infinito e' peggio di un risultato
    incompleto: se il portale sbaglia a dichiarare le pagine, ci si ferma."""
    assert 1 < MAX_PAGINE <= 100
