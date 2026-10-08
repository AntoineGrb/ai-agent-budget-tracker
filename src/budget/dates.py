"""Parsing des dates françaises des relevés et utilitaires de mois."""

import calendar
import re
import unicodedata
from datetime import date

from budget.errors import ParseError

_MOIS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    # Abréviations usuelles, employées par Trade Republic à partir de juillet (« 08 juil. 2026 »)
    "janv.": 1,
    "fevr.": 2,
    "avr.": 4,
    "juil.": 7,
    "sept.": 9,
    "oct.": 10,
    "nov.": 11,
    "dec.": 12,
}

_FORMAT_NUMERIQUE = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4}|[0-9]{2})")
_FORMAT_LITTERAL = re.compile(r"([0-9]{1,2})\s+([a-z]+\.?)\s+([0-9]{4})")


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def parse_date_fr(texte: str) -> date:
    """Accepte `JJ/MM/AAAA`, `JJ/MM/AA` (siècle 2000) et `J[J] <mois> AAAA` (`30 juin 2026`).

    Le mois, en toutes lettres ou abrégé (`juil.`), est insensible à la casse et aux accents.
    Tout autre format lève `ParseError`.
    """
    nettoye = texte.strip()
    if correspondance := _FORMAT_NUMERIQUE.fullmatch(nettoye):
        jour, mois, annee = correspondance.groups()
        annee_complete = int(annee) + 2000 if len(annee) == 2 else int(annee)
        return _construire(int(jour), int(mois), annee_complete, texte)
    if correspondance := _FORMAT_LITTERAL.fullmatch(_sans_accents(nettoye)):
        jour, nom_mois, annee = correspondance.groups()
        if nom_mois not in _MOIS:
            raise ParseError(f"mois inconnu {nom_mois!r} dans la date {texte!r}")
        return _construire(int(jour), _MOIS[nom_mois], int(annee), texte)
    raise ParseError(
        f"date invalide {texte!r} : formats attendus JJ/MM/AAAA, JJ/MM/AA ou « 30 juin 2026 »"
    )


def _construire(jour: int, mois: int, annee: int, texte: str) -> date:
    try:
        return date(annee, mois, jour)
    except ValueError as erreur:
        raise ParseError(f"date inexistante {texte!r} : {erreur}") from erreur


def mois_budgetaire(d: date) -> str:
    """Renvoie le mois au format `AAAA-MM`."""
    return f"{d.year:04d}-{d.month:02d}"


def dernier_jour_du_mois(annee: int, mois: int) -> date:
    """Renvoie la date du dernier jour du mois donné."""
    return date(annee, mois, calendar.monthrange(annee, mois)[1])
