"""`budget classify` : classement, questions, puis reclassement avec les réponses (spec 06)."""

from dataclasses import dataclass

from budget.classement import ResultatClassement, classer_mois
from budget.config import Contexte
from budget.errors import ControleError
from budget.ledger import Ledger
from budget.models import Transaction
from budget.questions.candidats import EtatLiens
from budget.questions.construction import construire_questions
from budget.questions.dialogue import Bilan, Dialogue, Interlocuteur
from budget.questions.modeles import Question


@dataclass(frozen=True)
class RapportClassement:
    transactions: list[Transaction]
    resultat: ResultatClassement
    questions: list[Question]  # restant à poser après le dialogue
    bilan: Bilan | None  # None : aucune question posée


def mois_precedent(mois: str) -> str:
    annee, numero = int(mois[:4]), int(mois[5:])
    return f"{annee - 1}-12" if numero == 1 else f"{annee}-{numero - 1:02d}"


def _classer(
    mois: str, ledger: Ledger, contexte: Contexte, transactions: list[Transaction]
) -> tuple[ResultatClassement, list[Question]]:
    memoire = ledger.memoire()
    resultat = classer_mois(mois, transactions, contexte, memoire)
    ledger.enregistrer_classement(mois, resultat)
    return resultat, construire_questions(transactions, resultat, contexte, memoire.regles)


def etat_liens(
    mois: str, ledger: Ledger, transactions: list[Transaction], resultat: ResultatClassement
) -> EtatLiens:
    """RG-07 : candidates du mois classé et du mois précédent s'il est importé."""
    candidates = list(transactions)
    classements = {c.transaction_id: c for c in resultat.classements}
    precedent = mois_precedent(mois)
    if precedent in ledger.mois_importes():
        candidates += ledger.transactions(precedent)
        classements.update((c.transaction_id, c) for c in ledger.classements(precedent))
    return EtatLiens(
        transactions=candidates, classements=classements, deja_lies=ledger.liens_existants()
    )


def classer_et_questionner(
    mois: str, ledger: Ledger, contexte: Contexte, interlocuteur: Interlocuteur | None
) -> RapportClassement:
    """Classe le mois ; avec un interlocuteur, pose les questions puis reclasse."""
    if mois not in ledger.mois_importes():
        raise ControleError(
            f"mois {mois} non importé dans {ledger.chemin} : lancer d'abord "
            f"`budget import --mois {mois}`"
        )
    transactions = ledger.transactions(mois)
    resultat, questions = _classer(mois, ledger, contexte, transactions)
    if interlocuteur is None or not questions:
        return RapportClassement(transactions, resultat, questions, None)

    liens = etat_liens(mois, ledger, transactions, resultat)
    bilan = Dialogue(interlocuteur, ledger, contexte, liens).derouler(questions)
    resultat, questions = _classer(mois, ledger, contexte, transactions)
    return RapportClassement(transactions, resultat, questions, bilan)
