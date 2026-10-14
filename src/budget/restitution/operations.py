"""Lignes d'opérations d'un mois, colonnes du cadrage §8 : export CSV, puis onglet mensuel."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from budget.categories import categorie_de, libelle_de
from budget.classement.modeles import Classement, OrigineClassement
from budget.models import Compte, Nature, Transaction
from budget.montants import formater_montant

LIBELLES_NATURE: dict[Nature, str] = {
    Nature.DEPENSE: "Dépense",
    Nature.REVENU: "Revenu",
    Nature.DOTATION: "Dotation",
    Nature.PROVISION: "Provision",
    Nature.EPARGNE: "Épargne",
    Nature.COMPLEMENT: "Complément",
    Nature.LIEN_DEPENSE: "Lien à une dépense",
    Nature.REMBOURSEMENT: "Remboursement",
    Nature.HORS_BUDGET: "Hors budget",
    Nature.NEUTRALISE: "Neutralisé",
}
NON_CLASSEE = "Non classée"


@dataclass(frozen=True)
class LigneOperation:
    transaction_id: str
    date_operation: date
    date_debit: date | None
    compte: str
    libelle: str
    montant: Decimal
    nature: str
    categorie: str
    sous_categorie: str
    exceptionnel: bool
    lien: str
    regle: str


def libelle_compte(transaction: Transaction) -> str:
    return "Perso" if transaction.compte is Compte.PERSO else "Joint"


def regle_appliquee(classement: Classement) -> str:
    """`Règle n° 12`, `Décision`, `Contexte : charges_fixes.energie`, `RG-03`, `RG-13`…"""
    if classement.origine is OrigineClassement.REGLE:
        return f"Règle n° {classement.regle_id}"
    if classement.origine is OrigineClassement.DECISION:
        return "Décision"
    if classement.origine is OrigineClassement.CONTEXTE:
        return f"Contexte : {classement.detail}"
    if classement.origine is OrigineClassement.REGLE_FIXE:
        return classement.detail or ""
    return ""


def lignes_operations(
    transactions: Iterable[Transaction],
    classements: Mapping[str, Classement],
    references: Mapping[str, tuple[Transaction, Classement | None]] | None = None,
) -> list[LigneOperation]:
    """Une ligne par transaction, triées par compte (joint puis perso), date, source, position.

    `references` donne les dépenses liées (RG-07) hors du mois, avec leur classement : un lien
    prend la catégorie de la dépense liée.
    """
    index: dict[str, tuple[Transaction, Classement | None]] = dict(references or {})
    transactions = list(transactions)
    index.update((t.id, (t, classements.get(t.id))) for t in transactions)
    ordonnees = sorted(
        transactions,
        key=lambda t: (t.compte is Compte.PERSO, t.date_operation, t.source.value, t.position),
    )
    lignes = []
    for t in ordonnees:
        classement = classements.get(t.id)
        nature = categorie = sous_categorie = lien = ""
        regle = ""
        exceptionnel = False
        if classement is not None:
            nature = LIBELLES_NATURE[classement.nature] if classement.nature else NON_CLASSEE
            regle = regle_appliquee(classement)
            exceptionnel = classement.exceptionnel
            source_categorie = classement.sous_categorie
            if classement.lien_id is not None and classement.lien_id in index:
                liee, classement_lie = index[classement.lien_id]
                lien = (
                    f"{libelle_compte(liee)} · {liee.date_operation:%d/%m/%Y} · "
                    f"{formater_montant(liee.montant)}"
                )
                source_categorie = classement_lie.sous_categorie if classement_lie else None
            if source_categorie is not None:
                categorie = categorie_de(source_categorie).libelle
                sous_categorie = libelle_de(source_categorie)
        else:
            nature = NON_CLASSEE
        lignes.append(
            LigneOperation(
                transaction_id=t.id,
                date_operation=t.date_operation,
                date_debit=t.date_debit,
                compte=libelle_compte(t),
                libelle=" ".join(t.libelle.split()),
                montant=t.montant,
                nature=nature,
                categorie=categorie,
                sous_categorie=sous_categorie,
                exceptionnel=exceptionnel,
                lien=lien,
                regle=regle,
            )
        )
    return lignes
