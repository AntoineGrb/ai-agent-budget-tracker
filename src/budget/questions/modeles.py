"""Questions posées dans le terminal (cadrage §7)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from budget.models import Transaction


class TypeQuestion(StrEnum):
    VIREMENT = "virement"
    OPERATION = "operation"  # tout autre débit hors carte, ou paiement Amazon (RG-13)


class Question(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TypeQuestion
    motif: str  # "RG-06", "RG-14" ou "RG-13"
    transaction: Transaction
    regle_proposable: bool = True  # faux pour RG-13, RG-14 et les règles « toujours demander »
