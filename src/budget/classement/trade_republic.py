"""Types d'opérations Trade Republic : paiements carte (RG-12) et investissements (RG-11)."""

from budget.libelles import normaliser_libelle
from budget.models import Source, Transaction

TYPE_AVOIR = "AVOIR"  # tout paiement carte, dans les deux sens (RG-12)
TYPE_VIREMENT = "VIREMENT"
TYPE_ORDRE = "EXECUTION D'ORDRE"
DESCRIPTION_VERSEMENT_PEA = "VERSEMENT PEA"


def type_tr(transaction: Transaction) -> str:
    return normaliser_libelle(transaction.type_operation or "")


def est_investissement(transaction: Transaction) -> bool:
    """RG-11 : exécution d'ordre ou versement PEA, non comptés d'office."""
    if transaction.source is not Source.TR:
        return False
    type_operation = type_tr(transaction)
    return type_operation == TYPE_ORDRE or (
        type_operation == TYPE_VIREMENT
        and transaction.libelle_normalise == DESCRIPTION_VERSEMENT_PEA
    )
