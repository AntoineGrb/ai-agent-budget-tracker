"""RG-07 : dépenses candidates pour un lien à une dépense (spec 06)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from budget.classement.modeles import Classement
from budget.models import Nature, Transaction
from budget.questions.modeles import Question

FENETRE_LIEN_JOURS = 31
RATIO_LIEN_MINIMUM = Decimal("0.5")
TAILLE_PAGE_LIENS = 10


@dataclass
class EtatLiens:
    """Transactions du mois classé et du mois précédent importé, avec leur classement courant.

    Mis à jour pendant le dialogue : une dépense liée n'est plus proposée ensuite.
    """

    transactions: Sequence[Transaction]
    classements: dict[str, Classement]
    deja_lies: set[str] = field(default_factory=set)


def _candidate(transaction: Transaction, classement: Classement | None) -> bool:
    if transaction.montant >= 0:
        return False
    if classement is None:
        return True
    if classement.nature not in (None, Nature.DEPENSE):
        return False
    return not (classement.detail or "").startswith("charges_fixes.")


def candidats_lien(question: Question, etat: EtatLiens) -> list[Transaction]:
    """Dépenses candidates, triées par écart de date croissant (RG-07).

    Sorties de nature `DEPENSE` ou non classées, hors charges fixes et hors opérations de la
    question, à moins de `FENETRE_LIEN_JOURS` jours, d'au moins `RATIO_LIEN_MINIMUM` fois le
    montant du virement, et pas déjà liées à un autre virement.
    """
    virement = question.transaction
    exclues = {t.id for t in question.transactions} | etat.deja_lies
    minimum = abs(virement.montant) * RATIO_LIEN_MINIMUM
    retenues = []
    for t in etat.transactions:
        if t.id in exclues or not _candidate(t, etat.classements.get(t.id)):
            continue
        ecart = abs((t.date_operation - virement.date_operation).days)
        if ecart < FENETRE_LIEN_JOURS and abs(t.montant) >= minimum:
            retenues.append((ecart, t))
    retenues.sort(key=lambda e: (e[0], e[1].date_operation, e[1].source.value, e[1].position))
    return [t for _, t in retenues]


def page(candidats: Sequence[Transaction], numero: int) -> list[Transaction]:
    """Page `numero` (à partir de 0) de `TAILLE_PAGE_LIENS` candidates."""
    debut = numero * TAILLE_PAGE_LIENS
    return list(candidats[debut : debut + TAILLE_PAGE_LIENS])
