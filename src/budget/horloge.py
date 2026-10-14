"""Seule source des horodatages (spec 07) : figée par les tests pour des sorties déterministes."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

_instant_fige: datetime | None = None


def maintenant() -> datetime:
    """Instant courant, avec fuseau ; l'instant figé s'il y en a un."""
    return _instant_fige if _instant_fige is not None else datetime.now().astimezone()


def horodatage() -> str:
    """Instant courant au format ISO 8601 à la seconde, tel qu'il est stocké en base."""
    return maintenant().isoformat(timespec="seconds")


@contextmanager
def figer(instant: datetime) -> Iterator[None]:
    """Fige l'horloge le temps d'un bloc."""
    global _instant_fige
    precedent, _instant_fige = _instant_fige, instant
    try:
        yield
    finally:
        _instant_fige = precedent
