"""RG-11 et RG-12 : nature des opérations Trade Republic d'après leur type (spec 05)."""

from budget.classement.criteres import reperer
from budget.classement.modeles import Alerte, Classement, OrigineClassement
from budget.libelles import normaliser_libelle
from budget.models import Nature, Source, Transaction

TYPE_AVOIR = "AVOIR"  # tout paiement carte, dans les deux sens (RG-12)
TYPE_VIREMENT = "VIREMENT"
TYPE_PRELEVEMENT = "PRELEVEMENT BANCAIRE"
DESCRIPTION_VERSEMENT_PEA = "VERSEMENT PEA"

# RG-11 : type normalisé -> (nature, détail)
_TYPES_CLASSES: dict[str, tuple[Nature, str]] = {
    "EXECUTION D'ORDRE": (Nature.EPARGNE, "RG-11:ordre"),
    "INTERETS": (Nature.REVENU, "RG-11:interets"),
    "RENDEMENT": (Nature.REVENU, "RG-11:dividende"),
    "BONUS": (Nature.REVENU, "RG-11:bonus"),
}
# Types connus laissés aux règles suivantes : paiement carte (règle 8) ou question (étape 6)
_TYPES_NON_CLASSES = {TYPE_AVOIR, TYPE_VIREMENT, TYPE_PRELEVEMENT}


def type_tr(transaction: Transaction) -> str:
    return normaliser_libelle(transaction.type_operation or "")


def est_versement_pea(transaction: Transaction) -> bool:
    return (
        transaction.source is Source.TR
        and type_tr(transaction) == TYPE_VIREMENT
        and transaction.libelle_normalise == DESCRIPTION_VERSEMENT_PEA
    )


def classer_trade_republic(transaction: Transaction, alertes: list[Alerte]) -> Classement | None:
    """RG-11 : épargne ou revenu financier ; un type inconnu produit l'alerte `TR-TYPE`."""
    if transaction.source is not Source.TR:
        return None
    type_operation = type_tr(transaction)
    if type_operation in _TYPES_CLASSES:
        nature, detail = _TYPES_CLASSES[type_operation]
        return Classement.pour(transaction, nature, OrigineClassement.REGLE_FIXE, detail)
    if est_versement_pea(transaction):
        return Classement.pour(
            transaction, Nature.EPARGNE, OrigineClassement.REGLE_FIXE, "RG-11:pea"
        )
    if type_operation not in _TYPES_NON_CLASSES:
        alertes.append(
            Alerte(
                code="TR-TYPE",
                transaction_id=transaction.id,
                message=f"type TR inconnu {transaction.type_operation!r} : {reperer(transaction)}",
            )
        )
    return None
