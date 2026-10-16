"""Modèles du classement d'un mois (cadrage §4)."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from budget.categories import Imputation
from budget.models import MOTIF_MOIS, Transaction


class OrigineClassement(StrEnum):
    REGLE_FIXE = "regle_fixe"  # règle codée en dur : RG-03, RG-11, RG-13
    REGLE = "regle"  # règle apprise
    DECISION = "decision"  # réponse rattachée à une seule opération
    AUCUNE = "aucune"  # en attente d'une question ou de la validation en lot


class Classement(Imputation):
    transaction_id: str
    origine: OrigineClassement
    detail: str | None = None  # règle fixe appliquée : "RG-03", "RG-11", "RG-13"
    regle_id: int | None = None  # règle apprise appliquée

    @classmethod
    def attente(cls, transaction: Transaction) -> Self:
        return cls(transaction_id=transaction.id, origine=OrigineClassement.AUCUNE)


class ResultatClassement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mois: str = Field(pattern=MOTIF_MOIS)
    # Une par opération suivie du mois (RG-19), dans l'ordre reçu ; les autres crédits n'en ont pas
    classements: tuple[Classement, ...]

    @property
    def en_attente(self) -> int:
        return sum(1 for c in self.classements if not c.decidee)
