"""Appariement des virements entre le compte joint et le compte perso Trade Republic (spec 05).

L'argent qui va du joint vers le compte perso (ou l'inverse) apparaît des deux côtés : les deux
opérations sont appariées pour n'être classées qu'une fois.
"""

from collections.abc import Sequence
from datetime import timedelta

from budget.classement.trade_republic import TYPE_VIREMENT, est_versement_pea, type_tr
from budget.libelles import normaliser_libelle
from budget.models import Source, Transaction

DELAI_VIREMENT_INTERNE_JOURS = 3
_TYPES_JOINT = {"VIREMENT EMIS", "VIREMENT EN VOTRE FAVEUR"}


def _cote_joint(transaction: Transaction) -> bool:
    return transaction.source is Source.CA_JOINT and (
        normaliser_libelle(transaction.type_operation or "") in _TYPES_JOINT
    )


def _cote_tr(transaction: Transaction) -> bool:
    return (
        transaction.source is Source.TR
        and type_tr(transaction) == TYPE_VIREMENT
        and not est_versement_pea(transaction)
    )


def _compatibles(joint: Transaction, tr: Transaction) -> bool:
    """Même valeur absolue, sens opposés, reçu côté TR entre J et J + 3 jours."""
    delai = timedelta(days=DELAI_VIREMENT_INTERNE_JOURS)
    return (
        tr.montant == -joint.montant
        and joint.date_operation <= tr.date_operation <= joint.date_operation + delai
    )


def apparier_virements_internes(
    transactions: Sequence[Transaction],
) -> list[tuple[Transaction, Transaction]]:
    """Paires (joint, TR) dont chaque côté a exactement une candidate de l'autre côté.

    En cas d'ambiguïté, aucun appariement : les deux côtés seront traités par question.
    """
    joints = [t for t in transactions if _cote_joint(t)]
    trs = [t for t in transactions if _cote_tr(t)]
    paires = []
    for joint in joints:
        candidates = [tr for tr in trs if _compatibles(joint, tr)]
        if len(candidates) != 1:
            continue
        tr = candidates[0]
        if sum(1 for autre in joints if _compatibles(autre, tr)) == 1:
            paires.append((joint, tr))
    return paires
