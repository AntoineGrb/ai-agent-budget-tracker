"""Questions posées dans le terminal et réponses (spec 06)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from budget.categories import SousCategorie
from budget.models import MOTIF_MOIS, Nature, Transaction


class TypeQuestion(StrEnum):
    VIREMENT = "virement"  # RG-06
    SALAIRE = "salaire"  # RG-04
    OPERATION = "operation"  # tout le reste hors paiement carte


class Question(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TypeQuestion
    motif: str  # "RG-06", "RG-14", "RG-04", "non_classe"
    transaction: Transaction  # côté joint pour une paire de virements internes
    contrepartie: Transaction | None = None
    mois_possibles: tuple[str, ...] = ()  # question salaire
    nom_salaire: str | None = None
    regle_proposable: bool = True  # faux pour RG-14 et les règles « toujours demander »

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        if self.contrepartie is None:
            return (self.transaction,)
        return (self.transaction, self.contrepartie)


class Reponse(BaseModel):
    """Réponse à une question, avant sa traduction en décisions et en règle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nature: Nature
    sous_categorie: SousCategorie | None = None
    detail: str | None = None
    lien_id: str | None = None  # RG-07
    mois_paie: str | None = Field(default=None, pattern=MOTIF_MOIS)  # RG-04
