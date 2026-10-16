"""Modèles de domaine communs à toutes les étapes."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from budget.montants import Montant

MOTIF_CARTE = r"^X\d{4}$"
MOTIF_MOIS = r"^\d{4}-(0[1-9]|1[0-2])$"


class Source(StrEnum):
    CA_CB = "ca_cb"
    CA_JOINT = "ca_joint"
    TR = "tr"


class Compte(StrEnum):
    JOINT = "joint"
    PERSO = "perso"


class OperationBrute(BaseModel):
    """Opération telle que lue par un parser, avant tout classement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    compte: Compte
    fichier: str
    position: int = Field(ge=1)
    date_operation: date
    date_debit: date | None = None
    type_operation: str | None = None
    libelle: str
    libelle_brut: str
    ics: str | None = None
    carte: str | None = Field(default=None, pattern=MOTIF_CARTE)
    montant: Montant
    solde_apres: Montant | None = None

    @field_validator("montant")
    @classmethod
    def _montant_non_nul(cls, valeur: Decimal) -> Decimal:
        if valeur == 0:
            raise ValueError("le montant d'une opération ne peut pas être nul")
        return valeur


class Transaction(BaseModel):
    """Opération du ledger : les faits lus dans le fichier, plus l'identifiant (RG-16) et le mois
    budgétaire (RG-01). Tout ce qui dépend des règles est une projection à part."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{16}$")  # RG-16
    source: Source
    compte: Compte
    fichier: str
    position: int = Field(ge=1)
    rang: int = Field(ge=1)  # RG-16
    date_operation: date
    date_debit: date | None = None
    mois_budgetaire: str = Field(pattern=MOTIF_MOIS)  # RG-01
    type_operation: str | None = None
    libelle: str
    libelle_brut: str
    libelle_normalise: str
    ics: str | None = None
    carte: str | None = Field(default=None, pattern=MOTIF_CARTE)
    montant: Montant
    solde_apres: Montant | None = None

    @field_validator("montant")
    @classmethod
    def _montant_non_nul(cls, valeur: Decimal) -> Decimal:
        if valeur == 0:
            raise ValueError("le montant d'une transaction ne peut pas être nul")
        return valeur
