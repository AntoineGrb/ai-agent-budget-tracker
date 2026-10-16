"""Lignes d'opérations et dépenses par catégorie d'un mois classé (cadrage §8)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from budget.categories import Categorie, categorie_de, libelle_de
from budget.classement.modeles import Classement, OrigineClassement
from budget.models import Compte, Transaction

COMPTEE, NON_COMPTEE, EN_ATTENTE = "oui", "non", "en attente"


@dataclass(frozen=True)
class LigneOperation:
    transaction_id: str
    date_operation: date
    date_debit: date | None
    compte: str
    libelle: str
    montant: Decimal
    categorie: str
    sous_categorie: str
    comptee: str  # COMPTEE, NON_COMPTEE ou EN_ATTENTE
    regle: str


def libelle_compte(transaction: Transaction) -> str:
    return "Perso" if transaction.compte is Compte.PERSO else "Joint"


def regle_appliquee(classement: Classement) -> str:
    """`Règle n° 12`, `Décision`, `RG-03`, `RG-11`, `RG-13`, ou vide si en attente."""
    if classement.origine is OrigineClassement.REGLE:
        return f"Règle n° {classement.regle_id}"
    if classement.origine is OrigineClassement.DECISION:
        return "Décision"
    return classement.detail or ""


def lignes_operations(
    transactions: Iterable[Transaction], classements: Mapping[str, Classement]
) -> list[LigneOperation]:
    """Une ligne par opération suivie (RG-19), triées par compte, date, source, position."""
    suivies = [t for t in transactions if t.id in classements]
    suivies.sort(
        key=lambda t: (t.compte is Compte.PERSO, t.date_operation, t.source.value, t.position)
    )
    lignes = []
    for t in suivies:
        classement = classements[t.id]
        sc = classement.sous_categorie
        if classement.non_comptee:
            comptee = NON_COMPTEE
        else:
            comptee = COMPTEE if sc is not None else EN_ATTENTE
        lignes.append(
            LigneOperation(
                transaction_id=t.id,
                date_operation=t.date_operation,
                date_debit=t.date_debit,
                compte=libelle_compte(t),
                libelle=" ".join(t.libelle.split()),
                montant=t.montant,
                categorie=categorie_de(sc).libelle if sc is not None else "",
                sous_categorie=libelle_de(sc) if sc is not None else "",
                comptee=comptee,
                regle=regle_appliquee(classement),
            )
        )
    return lignes


def depenses_par_categorie(
    transactions: Iterable[Transaction], classements: Mapping[str, Classement]
) -> dict[tuple[Categorie, Compte], Decimal]:
    """Dépenses comptées, en positif : débits moins avoirs carte (RG-09)."""
    totaux: dict[tuple[Categorie, Compte], Decimal] = {}
    for t in transactions:
        classement = classements.get(t.id)
        if classement is None or classement.sous_categorie is None:
            continue
        cle = (categorie_de(classement.sous_categorie), t.compte)
        totaux[cle] = totaux.get(cle, Decimal(0)) - t.montant
    return totaux
