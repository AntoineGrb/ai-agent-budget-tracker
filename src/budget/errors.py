"""Exceptions du projet : toutes héritent de `BudgetError`."""


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
