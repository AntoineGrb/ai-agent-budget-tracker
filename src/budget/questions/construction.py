"""Quelles questions poser, et dans quel ordre (spec 06). Fonctions pures."""

from collections.abc import Iterable, Sequence

from budget.classement.modeles import ResultatClassement
from budget.classement.moteur import est_paiement_carte
from budget.classement.virements_internes import est_virement_joint, est_virement_tr
from budget.config import Contexte
from budget.models import Nature, Source, Transaction
from budget.questions.modeles import Question, TypeQuestion
from budget.regles import Regle, toujours_demander
from budget.salaires import rattacher_salaire


def est_virement(transaction: Transaction) -> bool:
    """RG-06 : virement du joint, ou virement TR hors versement PEA."""
    return est_virement_joint(transaction) or est_virement_tr(transaction)


def _ordre(question: Question) -> tuple[object, ...]:
    """Chronologique, joint avant TR à date égale."""
    t = question.transaction
    return (t.date_operation, t.source is Source.TR, t.position)


def construire_questions(
    transactions: Sequence[Transaction],
    resultat: ResultatClassement,
    contexte: Contexte,
    regles: Iterable[Regle],
) -> list[Question]:
    """Questions du mois : virements, puis salaires, puis autres opérations hors carte."""
    regles = list(regles)
    par_id = {t.id: t for t in transactions}
    classements = {c.transaction_id: c for c in resultat.classements}
    virements: list[Question] = []
    salaires: list[Question] = []
    operations: list[Question] = []
    for t in transactions:
        classement = classements[t.id]
        if (
            classement.nature is Nature.REVENU
            and (classement.detail or "").startswith("salaires.")
            and classement.mois_rattachement is None
        ):  # RG-04 : mois de paie à déterminer
            rattachement = rattacher_salaire(t, contexte)
            assert rattachement is not None
            salaires.append(
                Question(
                    type=TypeQuestion.SALAIRE,
                    motif="RG-04",
                    transaction=t,
                    mois_possibles=rattachement.mois_possibles,
                    nom_salaire=rattachement.nom,
                    regle_proposable=False,
                )
            )
            continue
        if classement.nature is not None or est_paiement_carte(t):
            continue  # paiements carte : sous-catégorie à l'étape 7

        contrepartie = par_id.get(classement.contrepartie_id or "")
        if contrepartie is not None and classements[contrepartie.id].nature is not None:
            contrepartie = None  # l'autre côté est déjà classé : question sur ce côté seul
        if t.source is Source.TR and contrepartie is not None:
            continue  # une paire produit une seule question, portée par le côté joint

        demander = toujours_demander(regles, t)  # RG-14
        if est_virement(t):
            virements.append(
                Question(
                    type=TypeQuestion.VIREMENT,
                    motif="RG-14" if demander else "RG-06",
                    transaction=t,
                    contrepartie=contrepartie,
                    regle_proposable=not demander,
                )
            )
        else:
            operations.append(
                Question(
                    type=TypeQuestion.OPERATION,
                    motif="RG-14" if demander else "non_classe",
                    transaction=t,
                    regle_proposable=not demander,
                )
            )
    return (
        sorted(virements, key=_ordre)
        + sorted(salaires, key=_ordre)
        + sorted(operations, key=_ordre)
    )
