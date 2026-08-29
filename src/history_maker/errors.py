"""Eccezioni della pipeline, tutte discendenti da :class:`HistoryMakerError`."""

from __future__ import annotations


class HistoryMakerError(Exception):
    """Errore generico della pipeline."""


class ManifestError(HistoryMakerError):
    """Il manifest IIIF manca di un campo atteso o e' malformato."""


class WafChallengeError(HistoryMakerError):
    """Il portale ha risposto con una challenge del WAF di AWS.

    Capita solo sulle pagine del portale, mai sugli endpoint IIIF: e' il
    motivo per cui la fase di scoperta usa un browser vero e la fase di
    download no.
    """


class DiscoveryError(HistoryMakerError):
    """La navigazione del portale non ha prodotto i risultati attesi."""


class TranscriptionError(HistoryMakerError):
    """La trascrizione di un'immagine non e' andata a buon fine."""
