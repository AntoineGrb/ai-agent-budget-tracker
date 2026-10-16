"""Traduction d'une réponse en décision et en règle éventuelle. Fonctions pures."""

import re
from decimal import Decimal

from budget.categories import Imputation
from budget.errors import ParseError
from budget.libelles import normaliser_libelle
from budget.models import Transaction
from budget.montants import formater_montant, parse_montant
from budget.questions.modeles import Question, TypeQuestion
from budget.regles import Critere, Decision, Regle, TypeRegle

_PLAGE = re.compile(r"\s*([0-9][0-9 ,.]*?)\s*-\s*([0-9][0-9 ,.]*?)\s*")


def decision_pour(question: Question, reponse: Imputation) -> Decision:
    return Decision(
        transaction_id=question.transaction.id,
        sous_categorie=reponse.sous_categorie,
        non_comptee=reponse.non_comptee,
    )


def critere_propose(question: Question) -> tuple[TypeRegle, Critere, str]:
    """Virement : libellé exact ; avec ICS : ICS ; sinon libellé exact (préfixe sur demande)."""
    t = question.transaction
    if question.type is TypeQuestion.VIREMENT:
        return TypeRegle.VIREMENT, Critere.LIBELLE_EXACT, t.libelle_normalise
    if t.ics is not None:
        return TypeRegle.PRELEVEMENT, Critere.ICS, t.ics
    return TypeRegle.PRELEVEMENT, Critere.LIBELLE_EXACT, t.libelle_normalise


def lire_prefixe(texte: str, transaction: Transaction) -> str:
    """Préfixe saisi, normalisé ; il doit commencer le libellé normalisé de l'opération."""
    prefixe = normaliser_libelle(texte)
    if not prefixe or not transaction.libelle_normalise.startswith(prefixe):
        raise ValueError(
            "le préfixe doit être le début du libellé affiché (casse et accents libres)"
        )
    return prefixe


def lire_plage(texte: str, transaction: Transaction) -> tuple[Decimal, Decimal]:
    """Plage saisie en valeurs absolues (`500-600`), signée comme l'opération et la contenant."""
    correspondance = _PLAGE.fullmatch(texte)
    if correspondance is None:
        raise ValueError("format attendu : deux montants positifs séparés par un tiret (500-600)")
    try:
        bas, haut = (parse_montant(correspondance.group(i)) for i in (1, 2))
    except ParseError as erreur:
        raise ValueError(str(erreur)) from erreur
    if bas > haut:
        raise ValueError(f"plage inversée : {bas} > {haut}")
    minimum, maximum = (-haut, -bas) if transaction.montant < 0 else (bas, haut)
    if not minimum <= transaction.montant <= maximum:
        raise ValueError(
            "la plage doit contenir le montant de l'opération "
            f"({formater_montant(transaction.montant)})"
        )
    return minimum, maximum


def construire_regle(
    question: Question,
    reponse: Imputation,
    *,
    critere: Critere | None = None,
    valeur: str | None = None,
    montant_min: Decimal | None = None,
    montant_max: Decimal | None = None,
) -> Regle:
    """Règle issue d'une réponse ; critère et valeur proposés sauf s'ils sont fournis."""
    type_regle, critere_defaut, valeur_defaut = critere_propose(question)
    return Regle(
        type=type_regle,
        source=question.transaction.source,
        critere=critere or critere_defaut,
        valeur=valeur or valeur_defaut,
        montant_min=montant_min,
        montant_max=montant_max,
        sous_categorie=reponse.sous_categorie,
        non_comptee=reponse.non_comptee,
    )


def regle_toujours_demander(question: Question) -> Regle:
    """Règle « toujours demander » avec le critère proposé, sans condition de montant."""
    _, critere, valeur = critere_propose(question)
    return Regle(
        type=TypeRegle.TOUJOURS_DEMANDER,
        source=question.transaction.source,
        critere=critere,
        valeur=valeur,
    )
