"""Quelles questions poser, et dans quel ordre (RG-06). Fonctions pures."""

from collections.abc import Iterable, Sequence

from budget.classement.cartes import est_amazon, est_paiement_carte
from budget.classement.modeles import ResultatClassement
from budget.classement.trade_republic import TYPE_VIREMENT, type_tr
from budget.libelles import normaliser_libelle
from budget.models import Source, Transaction
from budget.questions.modeles import Question, TypeQuestion
from budget.regles import Regle, toujours_demander

_TYPES_VIREMENT_JOINT = {"VIREMENT EMIS", "VIREMENT EN VOTRE FAVEUR"}


def est_virement(transaction: Transaction) -> bool:
    if transaction.source is Source.CA_JOINT:
        return normaliser_libelle(transaction.type_operation or "") in _TYPES_VIREMENT_JOINT
    return transaction.source is Source.TR and type_tr(transaction) == TYPE_VIREMENT


def construire_questions(
    transactions: Sequence[Transaction], resultat: ResultatClassement, regles: Iterable[Regle]
) -> list[Question]:
    """Une question par débit hors carte en attente (RG-06) et par paiement Amazon en attente
    (RG-13), dans l'ordre chronologique, joint avant TR."""
    regles = list(regles)
    par_id = {t.id: t for t in transactions}
    questions = []
    for classement in resultat.classements:
        t = par_id[classement.transaction_id]
        if classement.decidee:
            continue
        if est_paiement_carte(t):
            if est_amazon(t):  # RG-13 : au-dessus du seuil, une question, jamais de règle
                questions.append(
                    Question(
                        type=TypeQuestion.OPERATION,
                        motif="RG-13",
                        transaction=t,
                        regle_proposable=False,
                    )
                )
            continue  # autres paiements carte : validation en lot
        demander = toujours_demander(regles, t)  # RG-14
        questions.append(
            Question(
                type=TypeQuestion.VIREMENT if est_virement(t) else TypeQuestion.OPERATION,
                motif="RG-14" if demander else "RG-06",
                transaction=t,
                regle_proposable=not demander,
            )
        )
    return sorted(
        questions,
        key=lambda q: (
            q.transaction.date_operation,
            q.transaction.source is Source.TR,
            q.transaction.position,
        ),
    )
