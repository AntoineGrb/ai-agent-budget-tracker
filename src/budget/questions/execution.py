"""`budget classify` : classement, puis deux blocs de dialogue (cadrage §7).

1. Classement et enregistrement : règles fixes, décisions, règles apprises.
2. Bloc 1 : questions sur les débits hors carte (RG-06).
3. Bloc 2 : validation en lot des paiements carte, avec les propositions du catégoriseur.

Chaque bloc reclasse le mois à partir de la base avant de poser ses questions. `q` dans un bloc
arrête tout le dialogue. Sans interlocuteur, rien n'est posé et le LLM n'est pas appelé.
"""

from dataclasses import dataclass, field

from budget.agents.categoriseur import Categoriseur, CategoriseurIndisponible
from budget.classement import ResultatClassement, classer_mois
from budget.classement.cartes import GroupeMarchand, en_attente, grouper
from budget.config import Contexte
from budget.errors import ControleError
from budget.ledger import Ledger
from budget.models import Transaction
from budget.questions.construction import construire_questions
from budget.questions.dialogue import Bilan, Dialogue, Interlocuteur
from budget.questions.modeles import Question
from budget.questions.validation_lot import BilanLot, ValidationLot


@dataclass(frozen=True)
class EtatMois:
    """Classement enregistré d'un mois et ce qui reste à trancher."""

    transactions: list[Transaction]
    resultat: ResultatClassement
    questions: list[Question]  # bloc 1
    groupes: list[GroupeMarchand]  # bloc 2 : groupes marchands en attente
    amazon: list[Transaction]  # bloc 2 : paiements Amazon à compléter (RG-13)


@dataclass(frozen=True)
class RapportClassement:
    transactions: list[Transaction]
    resultat: ResultatClassement
    questions: list[Question]  # restant à poser après le dialogue
    bilan: Bilan | None = None  # bloc 1 ; None : aucune question posée
    bilan_lot: BilanLot | None = None  # bloc 2
    groupes: list[GroupeMarchand] = field(default_factory=list)
    amazon: list[Transaction] = field(default_factory=list)


def _verifier_importe(mois: str, ledger: Ledger) -> None:
    if mois not in ledger.mois_importes():
        raise ControleError(
            f"mois {mois} non importé dans {ledger.chemin} : lancer d'abord "
            f"`budget import --mois {mois}`"
        )


def classer(mois: str, ledger: Ledger, contexte: Contexte) -> EtatMois:
    """Classe le mois à partir de la base, enregistre le classement et liste ce qui reste."""
    _verifier_importe(mois, ledger)
    transactions = ledger.transactions(mois)
    memoire = ledger.memoire()
    seuil_amazon = contexte.seuils.amazon_auto
    resultat = classer_mois(mois, transactions, seuil_amazon, memoire)
    ledger.enregistrer_classement(mois, resultat)
    classements = {c.transaction_id: c for c in resultat.classements}
    groupes, amazon = grouper(en_attente(transactions, classements), seuil_amazon)
    return EtatMois(
        transactions=transactions,
        resultat=resultat,
        questions=construire_questions(transactions, resultat, memoire.regles),
        groupes=groupes,
        amazon=amazon,
    )


def bloc_questions(
    mois: str, ledger: Ledger, contexte: Contexte, interlocuteur: Interlocuteur
) -> Bilan | None:
    """Bloc 1 : questions sur les débits hors carte ; `None` s'il n'y en a aucune."""
    etat = classer(mois, ledger, contexte)
    if not etat.questions:
        return None
    return Dialogue(interlocuteur, ledger).derouler(etat.questions)


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


def classer_et_questionner(
    mois: str,
    ledger: Ledger,
    contexte: Contexte,
    interlocuteur: Interlocuteur | None,
    categoriseur: Categoriseur | None = None,
) -> RapportClassement:
    """Classe le mois ; avec un interlocuteur, déroule les deux blocs."""
    _verifier_importe(mois, ledger)
    bilan = bilan_lot = None
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
    etat = classer(mois, ledger, contexte)
    return RapportClassement(
        transactions=etat.transactions,
        resultat=etat.resultat,
        questions=etat.questions,
        bilan=bilan,
        bilan_lot=bilan_lot,
        groupes=etat.groupes,
        amazon=etat.amazon,
    )
