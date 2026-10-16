"""Chiffres d'un mois classé, vue par vue (cadrage §8, spec 08).

Toutes les dépenses sont en positif : `−Σ montant` des opérations comptées, avoirs carte déduits
(RG-09). Un écart vaut `dépense − référence` : positif, c'est un dépassement.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from budget.categories import Categorie, SousCategorie
from budget.models import MOTIF_MOIS, Compte
from budget.montants import Montant


class _Modele(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MontantSousCategorie(_Modele):
    sous_categorie: SousCategorie
    montant: Montant


class LigneCategorie(_Modele):
    categorie: Categorie
    montant: Montant
    nb_operations: int = Field(ge=0)  # avoirs compris
    sous_categories: tuple[MontantSousCategorie, ...]  # non nulles, par montant décroissant
    budget: Montant | None = None
    ecart_budget: Montant | None = None
    moyenne_3_mois: Montant | None = None
    mois_moyenne: int = Field(ge=0, le=3)  # mois classés et complets parmi M−1 à M−3
    ecart_moyenne: Montant | None = None


class NonComptees(_Modele):
    """Pour information : opérations non comptées de la vue (RG-10), montant en positif."""

    nombre: int = Field(ge=0)
    montant: Montant


class PlusGrosseDepense(_Modele):
    date_operation: date
    montant: Montant
    sous_categorie: SousCategorie
    marchand: str | None  # clé marchand masquée d'un paiement carte, jamais un libellé de virement


class Vue(_Modele):
    compte: Compte
    total: Montant
    categories: tuple[LigneCategorie, ...]  # dans l'ordre de `Categorie`
    non_comptees: NonComptees
    plus_grosses: tuple[PlusGrosseDepense, ...]

    @property
    def nb_operations(self) -> int:
        return sum(ligne.nb_operations for ligne in self.categories)


class PointMensuel(_Modele):
    mois: str = Field(pattern=MOTIF_MOIS)
    joint: Montant
    perso: Montant
    complet: bool  # aucune opération en attente


class Agregats(_Modele):
    mois: str = Field(pattern=MOTIF_MOIS)
    joint: Vue
    perso: Vue
    historique: tuple[PointMensuel, ...]  # mois importés de M−11 à M, chronologiques

    def vue(self, compte: Compte) -> Vue:
        return self.joint if compte is Compte.JOINT else self.perso
