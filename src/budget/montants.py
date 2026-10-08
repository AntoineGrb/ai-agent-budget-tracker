"""Parsing et affichage des montants : `Decimal` exclusivement, jamais `float`."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator

from budget.errors import ParseError

CENTIME = Decimal("0.01")

_CARACTERES_IGNORES = str.maketrans("", "", "   €")
_MOTIF_MONTANT = re.compile(r"-?[0-9]+(?:[.,][0-9]{1,2})?")


def parse_montant(texte: str) -> Decimal:
    """Convertit un montant texte (`"1 071,04"`, `"-3 116.50 €"`…) en `Decimal` à 2 décimales.

    Lève `ParseError` si le texte est vide ou ne respecte pas le format attendu : on ne devine
    jamais un montant ambigu (`"1.071,04"` est refusé).
    """
    nettoye = texte.translate(_CARACTERES_IGNORES)
    if not nettoye:
        raise ParseError("montant vide")
    if not _MOTIF_MONTANT.fullmatch(nettoye):
        raise ParseError(
            f"montant invalide {texte!r} : attendu des chiffres, un signe '-' facultatif "
            "et un séparateur décimal ',' ou '.' suivi de 1 ou 2 chiffres"
        )
    return Decimal(nettoye.replace(",", ".")).quantize(CENTIME)


def parse_montant_optionnel(texte: str) -> Decimal | None:
    """Comme `parse_montant`, mais renvoie `None` si le texte est vide après nettoyage."""
    if not texte.translate(_CARACTERES_IGNORES):
        return None
    return parse_montant(texte)


def formater_montant(montant: Decimal) -> str:
    """Affiche un montant au format français : `Decimal("-3116.5")` → `"-3 116,50 €"`."""
    arrondi = montant.quantize(CENTIME, rounding=ROUND_HALF_UP)
    signe = "-" if arrondi < 0 else ""
    corps = f"{abs(arrondi):,.2f}".replace(",", " ").replace(".", ",")
    return f"{signe}{corps} €"


def _refuser_float(valeur: Any) -> Any:
    if isinstance(valeur, float):
        raise ValueError("montant reçu en float : utiliser un Decimal ou un texte")
    return valeur


Montant = Annotated[Decimal, BeforeValidator(_refuser_float)]
"""Type Pydantic d'un montant : `Decimal`, un `float` en entrée est refusé."""
