"""Seule source des horodatages écrits en base."""

from datetime import datetime


def horodatage() -> str:
    """Instant courant, avec fuseau, au format ISO 8601 à la seconde."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
