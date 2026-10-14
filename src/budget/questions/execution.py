"""`budget classify` : classement, puis trois blocs de dialogue (specs 06 et 07, cadrage §7).

1. Classement et enregistrement : contexte, décisions, règles, sous-catégories carte.
2. Bloc 1 : questions sur les opérations hors carte (spec 06).
3. Bloc 2 : validation en lot des paiements carte, avec les propositions du catégoriseur.
4. Bloc 3 : tags exceptionnels (RG-15).
5. Régénération du journal `decisions.md`, y compris sans interlocuteur.

Chaque bloc reclasse le mois à partir de la base avant de poser ses questions. `q` dans un bloc
arrête tout le dialogue. Sans interlocuteur, rien n'est posé et le LLM n'est pas appelé.
"""

from dataclasses import dataclass, field
from pathlib import Path

from budget.agents.categoriseur import Categoriseur, CategoriseurIndisponible
from budget.classement import ResultatClassement, classer_mois
from budget.classement.cartes import GroupeMarchand, en_attente, grouper
from budget.config import Contexte
from budget.errors import ControleError
from budget.ledger import Ledger
from budget.models import Transaction
from budget.questions.candidats import EtatLiens
from budget.questions.construction import construire_questions
from budget.questions.dialogue import Bilan, Dialogue, Interlocuteur
from budget.questions.exceptionnels import (
    MOIS_RECURRENCE,
    BilanExceptionnels,
    candidates_exceptionnelles,
    poser_exceptionnels,
)
from budget.questions.modeles import Question
from budget.questions.validation_lot import BilanLot, ValidationLot
from budget.restitution.journal import ecrire_journal


@dataclass(frozen=True)
class EtatMois:
    """Classement enregistré d'un mois et ce qui reste à trancher."""

    transactions: list[Transaction]
    resultat: ResultatClassement
    questions: list[Question]  # bloc 1
    groupes: list[GroupeMarchand]  # bloc 2 : groupes marchands en attente
    amazon: list[Transaction]  # bloc 2 : paiements Amazon à compléter (RG-13)
    exceptionnelles: list[Transaction]  # bloc 3 : candidates RG-15 sans réponse


@dataclass(frozen=True)
class RapportClassement:
    transactions: list[Transaction]
    resultat: ResultatClassement
    questions: list[Question]  # restant à poser après le dialogue
    bilan: Bilan | None = None  # bloc 1 ; None : aucune question posée
    bilan_lot: BilanLot | None = None  # bloc 2
    bilan_exceptionnels: BilanExceptionnels | None = None  # bloc 3
    groupes: list[GroupeMarchand] = field(default_factory=list)
    amazon: list[Transaction] = field(default_factory=list)
    exceptionnelles: list[Transaction] = field(default_factory=list)
    journal: Path | None = None


def mois_precedent(mois: str) -> str:
    annee, numero = int(mois[:4]), int(mois[5:])
    return f"{annee - 1}-12" if numero == 1 else f"{annee}-{numero - 1:02d}"


def _verifier_importe(mois: str, ledger: Ledger) -> None:
    if mois not in ledger.mois_importes():
        raise ControleError(
            f"mois {mois} non importé dans {ledger.chemin} : lancer d'abord "
            f"`budget import --mois {mois}`"
        )


def historique_recurrence(mois: str, ledger: Ledger) -> list[Transaction]:
    """RG-15 : transactions des trois mois budgétaires précédents importés."""
    importes = set(ledger.mois_importes())
    historique: list[Transaction] = []
    precedent = mois
    for _ in range(MOIS_RECURRENCE):
        precedent = mois_precedent(precedent)
        if precedent in importes:
            historique += ledger.transactions(precedent)
    return historique


def classer(mois: str, ledger: Ledger, contexte: Contexte) -> EtatMois:
    """Classe le mois à partir de la base, enregistre le classement et liste ce qui reste."""
    _verifier_importe(mois, ledger)
    transactions = ledger.transactions(mois)
    memoire = ledger.memoire()
    resultat = classer_mois(mois, transactions, contexte, memoire)
    ledger.enregistrer_classement(mois, resultat)
    classements = {c.transaction_id: c for c in resultat.classements}
    groupes, amazon = grouper(en_attente(transactions, classements), contexte.seuils.amazon_auto)
    exceptionnelles = candidates_exceptionnelles(
        transactions,
        classements,
        historique_recurrence(mois, ledger),
        memoire.exceptionnels,
        contexte.seuils.exceptionnel,
    )
    return EtatMois(
        transactions=transactions,
        resultat=resultat,
        questions=construire_questions(transactions, resultat, contexte, memoire.regles),
        groupes=groupes,
        amazon=amazon,
        exceptionnelles=exceptionnelles,
    )


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


def bloc_questions(
    mois: str, ledger: Ledger, contexte: Contexte, interlocuteur: Interlocuteur
) -> Bilan | None:
    """Bloc 1 : questions sur les opérations hors carte ; `None` s'il n'y en a aucune."""
    etat = classer(mois, ledger, contexte)
    if not etat.questions:
        return None
    liens = etat_liens(mois, ledger, etat.transactions, etat.resultat)
    return Dialogue(interlocuteur, ledger, contexte, liens).derouler(etat.questions)


def bloc_validation(
    mois: str,
    ledger: Ledger,
    contexte: Contexte,
    interlocuteur: Interlocuteur,
    categoriseur: Categoriseur,
) -> BilanLot | None:
    """Bloc 2 : validation en lot des paiements carte ; `None` si rien n'est en attente."""
    etat = classer(mois, ledger, contexte)
    if not etat.groupes and not etat.amazon:
        return None
    return ValidationLot(interlocuteur, ledger, mois, categoriseur).derouler(
        etat.groupes, etat.amazon
    )


def bloc_exceptionnels(
    mois: str, ledger: Ledger, contexte: Contexte, interlocuteur: Interlocuteur
) -> BilanExceptionnels | None:
    """Bloc 3 : tags exceptionnels (RG-15) ; `None` s'il n'y a aucune candidate."""
    etat = classer(mois, ledger, contexte)
    if not etat.exceptionnelles:
        return None
    classements = {c.transaction_id: c for c in etat.resultat.classements}
    return poser_exceptionnels(
        interlocuteur, ledger, etat.exceptionnelles, classements, contexte.seuils.exceptionnel
    )


def classer_et_questionner(
    mois: str,
    ledger: Ledger,
    contexte: Contexte,
    interlocuteur: Interlocuteur | None,
    categoriseur: Categoriseur | None = None,
    journal: Path | None = None,
) -> RapportClassement:
    """Classe le mois ; avec un interlocuteur, déroule les trois blocs ; régénère le journal."""
    _verifier_importe(mois, ledger)
    bilan = bilan_lot = bilan_exceptionnels = None
    if interlocuteur is not None:
        bilan = bloc_questions(mois, ledger, contexte, interlocuteur)
        if bilan is None or not bilan.interrompu:
            bilan_lot = bloc_validation(
                mois,
                ledger,
                contexte,
                interlocuteur,
                categoriseur or CategoriseurIndisponible("aucun catégoriseur configuré"),
            )
            if bilan_lot is None or not bilan_lot.interrompu:
                bilan_exceptionnels = bloc_exceptionnels(mois, ledger, contexte, interlocuteur)
    etat = classer(mois, ledger, contexte)
    if journal is not None:
        ecrire_journal(ledger, journal)
    return RapportClassement(
        transactions=etat.transactions,
        resultat=etat.resultat,
        questions=etat.questions,
        bilan=bilan,
        bilan_lot=bilan_lot,
        bilan_exceptionnels=bilan_exceptionnels,
        groupes=etat.groupes,
        amazon=etat.amazon,
        exceptionnelles=etat.exceptionnelles,
        journal=journal,
    )
