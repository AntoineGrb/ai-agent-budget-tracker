"""Modèles de domaine communs à toutes les étapes."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from budget.montants import Montant

MOTIF_CARTE = r"^X\d{4}$"


class Source(StrEnum):
    CA_CB = "ca_cb"
    CA_JOINT = "ca_joint"
    TR = "tr"


class Compte(StrEnum):
    JOINT = "joint"
    PERSO = "perso"


class Nature(StrEnum):
    """Nature de flux d'une opération (cadrage §4), utilisée à partir de l'étape 5."""

    DEPENSE = "depense"
    REVENU = "revenu"
    DOTATION = "dotation"
    PROVISION = "provision"
    EPARGNE = "epargne"
    COMPLEMENT = "complement"
    LIEN_DEPENSE = "lien_depense"
    REMBOURSEMENT = "remboursement"
    HORS_BUDGET = "hors_budget"
    NEUTRALISE = "neutralise"


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
