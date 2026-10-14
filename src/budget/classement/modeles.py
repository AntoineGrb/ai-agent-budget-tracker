"""Modèles du classement par nature de flux (spec 05)."""

from collections import Counter
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from budget.categories import SousCategorie
from budget.models import MOTIF_MOIS, Nature, Transaction
from budget.montants import Montant


class OrigineClassement(StrEnum):
    CONTEXTE = "contexte"  # fichier de contexte du foyer
    REGLE_FIXE = "regle_fixe"  # règle codée en dur, indépendante du contexte (RG-03, RG-11…)
    REGLE = "regle"  # règle apprise (étapes 6 et 7)
    DECISION = "decision"  # décision rattachée à une seule opération (étape 6)
    AUCUNE = "aucune"  # non classée


class _Modele(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Classement(_Modele):
    transaction_id: str
    nature: Nature | None  # None = non classée
    sous_categorie: SousCategorie | None = None
    # mois_budgetaire, sauf salaires : mois de paie (RG-04) ; None = mois de paie à déterminer
    mois_rattachement: str | None = Field(pattern=MOTIF_MOIS)
    origine: OrigineClassement
    detail: str | None = None  # "RG-03", "dotations.perso", "charges_fixes.energie"…
    contrepartie_id: str | None = None  # virement interne apparié
    lien_id: str | None = None  # RG-07, rempli à l'étape 6
    exceptionnel: bool = False  # RG-15, rempli à l'étape 7
    regle_id: int | None = None  # règle apprise appliquée (spec 07)

    @classmethod
    def pour(
        cls,
        transaction: Transaction,
        nature: Nature | None,
        origine: OrigineClassement,
        detail: str | None = None,
        *,
        sous_categorie: SousCategorie | None = None,
    ) -> Self:
        """Classement rattaché au mois budgétaire de la transaction."""
        return cls(
            transaction_id=transaction.id,
            nature=nature,
            sous_categorie=sous_categorie,
            mois_rattachement=transaction.mois_budgetaire,
            origine=origine,
            detail=detail,
        )

    @classmethod
    def non_classee(cls, transaction: Transaction) -> Self:
        return cls.pour(transaction, None, OrigineClassement.AUCUNE)


class Alerte(_Modele):
    code: str  # "RG-04", "RG-05", "RG-08", "RG-17", "RG-18", "TR-TYPE"
    transaction_id: str | None = None
    message: str  # sans libellé : fichier, position, date, montant


class RapprochementCharge(_Modele):  # RG-18
    nom: str
    attendu: Montant  # positif
    reel: Montant | None  # positif ; None si absente
    transaction_ids: tuple[str, ...]
    ecart: Montant | None  # reel - attendu
    ecart_pct: Montant | None  # arrondi au centième
    seuil_pct: Montant
    hors_seuil: bool


class ResultatClassement(_Modele):
    mois: str = Field(pattern=MOTIF_MOIS)
    classements: tuple[Classement, ...]  # une par transaction du mois, dans l'ordre reçu
    alertes: tuple[Alerte, ...]
    rapprochements: tuple[RapprochementCharge, ...]

    @property
    def virements_internes_apparies(self) -> int:
        return sum(1 for c in self.classements if c.contrepartie_id is not None) // 2

    def alertes_par_code(self) -> Counter[str]:
        return Counter(alerte.code for alerte in self.alertes)
