"""Correspondance entre une transaction et un critère du fichier de contexte (spec 05)."""

from budget.config import AvecCritere
from budget.libelles import normaliser_libelle
from budget.models import Transaction
from budget.montants import formater_montant


def contient(transaction: Transaction, motif: str) -> bool:
    """Le motif, normalisé, est contenu dans le libellé normalisé de la transaction."""
    return normaliser_libelle(motif) in transaction.libelle_normalise


def meme_type(transaction: Transaction, type_operation: str) -> bool:
    """Types d'opération égaux après normalisation des deux côtés."""
    return transaction.type_operation is not None and normaliser_libelle(
        transaction.type_operation
    ) == normaliser_libelle(type_operation)


def correspond(transaction: Transaction, critere: AvecCritere) -> bool:
    """Tous les critères renseignés correspondent (ET logique) ; l'ICS est comparé strictement."""
    if critere.ics is not None and transaction.ics != critere.ics:
        return False
    if critere.libelle_contient is not None and not contient(transaction, critere.libelle_contient):
        return False
    return critere.type_operation is None or meme_type(transaction, critere.type_operation)


def reperer(transaction: Transaction) -> str:
    """Position d'une transaction pour un message, sans son libellé."""
    return (
        f"{transaction.fichier}, position {transaction.position}, "
        f"{transaction.date_operation:%d/%m/%Y}, {formater_montant(transaction.montant)}"
    )
