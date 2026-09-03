"""Non ripagare due volte lo stesso conto.

Ci sono due tipi di lavoro costoso in questa fase, e questo modulo li
tratta allo stesso modo perche' hanno lo stesso rimedio.

Il primo e' **aritmetico**: il vicinato delle forme — quali cognomi si
possono confondere con quali — sono nove milioni di distanze pesate, sei
minuti buoni, e il risultato dipende solo dall'elenco delle forme e dalla
soglia. Rifarlo a ogni esecuzione e' tempo bruciato, e finche' e' tempo
bruciato si e' tentati di non rilanciare mai la ricostruzione, che e' il
danno vero.

Il secondo e' **a consumo**: una domanda a Gemini sull'immagine o un caso
mandato a Claude. Li' non si brucia tempo, si brucia quota, e la quota
giornaliera non si ricompra.

La chiave e' sempre un'impronta di **tutto** cio' che influenza il
risultato: i dati in ingresso, la versione dell'algoritmo o del prompt, il
modello. Se cambia qualcosa che conta, la chiave cambia e il conto si
rifa'; se non cambia niente, non si rifa'. E' anche cio' che rende la
fase ripartibile: interrotta a meta', al rilancio ritrova tutto quello
che aveva gia' fatto.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def impronta(*pezzi: Any) -> str:
    """Un'impronta stabile di qualunque cosa si possa scrivere in JSON.

    Stabile fra esecuzioni diverse e fra macchine diverse: niente
    ``hash()``, che in Python cambia a ogni processo, e chiavi ordinate,
    perche' due dizionari uguali devono dare la stessa impronta.
    """
    digestione = hashlib.sha256()
    for pezzo in pezzi:
        digestione.update(
            json.dumps(pezzo, sort_keys=True, default=str, ensure_ascii=False).encode()
        )
        digestione.update(b"\x00")
    return digestione.hexdigest()[:24]


class Deposito:
    """Una cartella di risultati, indicizzati per impronta."""

    def __init__(self, cartella: Path):
        self.cartella = Path(cartella)
        self.letti = 0
        self.scritti = 0

    def _percorso(self, nome: str, chiave: str) -> Path:
        return self.cartella / f".cache-{nome}-{chiave}.pkl"

    def ottieni(self, nome: str, chiave: str, calcola: Callable[[], Any]) -> Any:
        """Il valore in cache, oppure lo calcola e lo mette da parte."""
        percorso = self._percorso(nome, chiave)
        if percorso.exists():
            try:
                with percorso.open("rb") as file:
                    valore = pickle.load(file)
                self.letti += 1
                logger.debug("cache: %s ritrovato", nome)
                return valore
            except (OSError, pickle.PickleError, EOFError, AttributeError):
                # Una cache illeggibile non e' un errore: e' una cache da
                # rifare. Fermare per questo una ricostruzione di sei
                # minuti sarebbe assurdo.
                logger.warning("cache di %s illeggibile, la rifaccio", nome)

        valore = calcola()
        try:
            self.cartella.mkdir(parents=True, exist_ok=True)
            provvisorio = percorso.with_suffix(".parte")
            with provvisorio.open("wb") as file:
                pickle.dump(valore, file, protocol=pickle.HIGHEST_PROTOCOL)
            provvisorio.replace(percorso)
            self.scritti += 1
        except OSError as errore:
            logger.warning("non riesco a salvare la cache di %s: %s", nome, errore)
        return valore

    def dimentica(self, nome: str) -> int:
        """Butta via tutte le versioni di una cache. Rende quante ne ha tolte."""
        quante = 0
        for percorso in sorted(self.cartella.glob(f".cache-{nome}-*.pkl")):
            try:
                percorso.unlink()
                quante += 1
            except OSError:
                pass
        return quante
