"""Exceptions du projet : toutes héritent de `BudgetError`."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from budget.categories import SousCategorie
    from budget.models import Source


class BudgetError(Exception):
    """Erreur métier du Budget Tracker."""


class ParseError(BudgetError):
    """Fichier illisible ou format inattendu."""

    def __init__(
        self,
        message: str,
        *,
        fichier: str | None = None,
        position: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fichier = fichier
        self.position = position

    def __str__(self) -> str:
        contexte = ", ".join(partie for partie in (self.fichier, self.position) if partie)
        return f"{contexte} : {self.message}" if contexte else self.message


class ControleError(BudgetError):
    """Contrôle de cohérence en échec."""


class ConfigError(BudgetError):
    """Fichier de contexte invalide."""

    def __init__(self, message: str, erreurs: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.erreurs = erreurs or []

    def __str__(self) -> str:
        return "\n".join([self.message, *(f"  - {erreur}" for erreur in self.erreurs)])


class LedgerError(BudgetError):
    """Base SQLite illisible ou de version inconnue."""


class RegleError(BudgetError):
    """Règle apprise invalide ou en conflit avec une règle existante."""


class CategorisationError(BudgetError):
    """Catégoriseur indisponible ou en échec : le classement continue sans proposition."""

    def __init__(
        self, raison: str, partielles: dict[tuple[Source, str], SousCategorie] | None = None
    ) -> None:
        super().__init__(raison)
        self.raison = raison
        self.partielles = partielles or {}


class SyntheseError(BudgetError):
    """Agent synthèse indisponible ou en échec : la synthèse est enregistrée sans commentaire."""

    def __init__(self, raison: str) -> None:
        super().__init__(raison)
        self.raison = raison


class PublicationError(BudgetError):
    """Publication du classeur Excel impossible (fichier verrouillé)."""
