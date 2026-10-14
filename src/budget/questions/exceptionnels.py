"""Bloc 3 : tags exceptionnels (RG-15, spec 07).

Une dépense non récurrente d'au moins `seuils.exceptionnel` reçoit une proposition de tag. La
réponse (oui ou non) est enregistrée aussitôt et ne revient jamais ; « passer » la laisse ouverte.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from rich.text import Text

from budget.categories import libelle_complet
from budget.classement.cartes import est_paiement_carte
from budget.classement.modeles import Classement
from budget.ledger import Ledger
from budget.libelles import cle_marchand
from budget.models import Nature, Transaction
from budget.questions.dialogue import (
    PASSER,
    QUITTER,
    Abandon,
    Interlocuteur,
    montant_signe,
    nom_compte,
)

MOIS_RECURRENCE = 3


def cle_recurrence(transaction: Transaction) -> tuple[str, ...]:
    """ICS s'il existe, sinon clé marchand d'un paiement carte, sinon libellé normalisé."""
    if transaction.ics is not None:
        return ("ics", transaction.ics)
    if est_paiement_carte(transaction):
        return (transaction.source.value, cle_marchand(transaction.libelle_normalise))
    return (transaction.source.value, transaction.libelle_normalise)


def candidates_exceptionnelles(
    transactions: Sequence[Transaction],
    classements: Mapping[str, Classement],
    historique: Sequence[Transaction],
    reponses: Mapping[str, bool],
    seuil: Decimal,
) -> list[Transaction]:
    """RG-15 : dépenses ≥ seuil, hors charges fixes, non récurrentes, sans réponse.

    `historique` : transactions des trois mois budgétaires précédents importés.
    """
    recurrentes = {cle_recurrence(t) for t in historique if abs(t.montant) >= seuil}
    candidates = []
    for t in transactions:
        classement = classements.get(t.id)
        if (
            classement is None
            or classement.nature is not Nature.DEPENSE
            or abs(t.montant) < seuil
            or (classement.detail or "").startswith("charges_fixes.")
            or t.id in reponses
            or cle_recurrence(t) in recurrentes
        ):
            continue
        candidates.append(t)
    return sorted(candidates, key=lambda t: (t.date_operation, t.source.value, t.position))


@dataclass
class BilanExceptionnels:
    a_poser: int
    oui: int = 0
    non: int = 0
    passees: int = 0
    interrompu: bool = False


def poser_exceptionnels(
    interlocuteur: Interlocuteur,
    ledger: Ledger,
    candidates: Sequence[Transaction],
    classements: Mapping[str, Classement],
    seuil: Decimal,
) -> BilanExceptionnels:
    bilan = BilanExceptionnels(a_poser=len(candidates))
    try:
        for numero, t in enumerate(candidates, 1):
            classement = classements[t.id]
            categorie = (
                libelle_complet(classement.sous_categorie)
                if classement.sous_categorie is not None
                else "sans sous-catégorie"
            )
            interlocuteur.afficher(
                Text(
                    "\n".join(
                        [
                            "",
                            f"Exceptionnel {numero}/{len(candidates)} — Dépense d'au moins "
                            f"{seuil.normalize():f} € (RG-15)",
                            f"  {nom_compte(t)} · {t.date_operation:%d/%m/%Y} · "
                            f"{montant_signe(t.montant)} · {categorie}",
                            f"  {t.libelle}",
                        ]
                    )
                )
            )
            choix = interlocuteur.demander(
                "Marquer comme exceptionnelle ?  o. Oui   n. Non   s. Passer   q. Quitter",
                ["o", "n", PASSER, QUITTER],
            )
            if choix == QUITTER:
                raise Abandon
            if choix == PASSER:
                bilan.passees += 1
                continue
            ledger.enregistrer_exceptionnel(t.id, choix == "o")
            if choix == "o":
                bilan.oui += 1
            else:
                bilan.non += 1
    except (Abandon, KeyboardInterrupt, EOFError):
        bilan.interrompu = True
    return bilan
