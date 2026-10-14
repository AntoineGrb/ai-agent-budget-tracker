"""Classement d'un mois par nature de flux, sans question ni LLM (spec 05).

Chaque transaction reçoit le premier classement applicable, dans l'ordre :

1. RG-03 : débit différé des cartes, neutralisé ;
2. RG-04 : salaire ;
3. RG-05 : dotation ;
4. RG-08 : reprise de provision ;
5. RG-08 : paiement financé par la provision ;
6. RG-18 : charge fixe ;
7. RG-11 : types Trade Republic ;
8. paiement carte (RG-09, RG-12) ;
9. contrepartie d'une dotation, par appariement des virements internes.

Le reste est non classé et alimentera les questions de l'étape 6.
"""

from collections.abc import Sequence

from budget.classement.contexte import (
    affecter_charges_fixes,
    alertes_cartes,
    alertes_dotations,
    alertes_provision,
    classer_dotation,
    classer_paiement_finance,
    classer_reprise_provision,
    classer_salaire,
)
from budget.classement.modeles import Alerte, Classement, OrigineClassement, ResultatClassement
from budget.classement.trade_republic import TYPE_AVOIR, classer_trade_republic, type_tr
from budget.classement.virements_internes import apparier_virements_internes
from budget.config import Contexte
from budget.errors import ControleError
from budget.libelles import normaliser_libelle
from budget.models import Nature, Source, Transaction
from budget.parsers.credit_agricole import MOTIF_DEBIT_DIFFERE, TYPE_DEBIT_DIFFERE

_REGLE_FIXE = OrigineClassement.REGLE_FIXE


def neutraliser_debit_differe(transaction: Transaction) -> Classement | None:
    """RG-03 : la ligne de débit différé est remplacée par le détail des cartes."""
    if (
        transaction.source is Source.CA_JOINT
        and transaction.type_operation is not None
        and normaliser_libelle(transaction.type_operation) == normaliser_libelle(TYPE_DEBIT_DIFFERE)
        and MOTIF_DEBIT_DIFFERE.search(transaction.libelle)
    ):
        return Classement.pour(transaction, Nature.NEUTRALISE, _REGLE_FIXE, "RG-03")
    return None


def classer_paiement_carte(transaction: Transaction) -> Classement | None:
    """Paiement carte (fichier cartes, `Avoir` TR) : dépense, ou remboursement s'il entre.

    RG-09 : un avoir marchand est un remboursement (sous-catégorie à l'étape 7).
    RG-12 : le sens d'une opération TR se lit dans sa colonne, donc dans le signe du montant.
    """
    if transaction.source is Source.CA_CB or (
        transaction.source is Source.TR and type_tr(transaction) == TYPE_AVOIR
    ):
        if transaction.montant < 0:
            return Classement.pour(transaction, Nature.DEPENSE, _REGLE_FIXE, "paiement_carte")
        return Classement.pour(transaction, Nature.REMBOURSEMENT, _REGLE_FIXE, "RG-09")
    return None


def classer_mois(
    mois: str, transactions: Sequence[Transaction], contexte: Contexte
) -> ResultatClassement:
    """Classe toutes les transactions d'un mois. Fonction pure : ni base, ni fichier."""
    hors_mois = [t for t in transactions if t.mois_budgetaire != mois]
    if hors_mois:
        raise ControleError(
            f"classement du mois {mois} : {len(hors_mois)} transaction(s) d'un autre mois "
            f"budgétaire (première : {hors_mois[0].fichier}, position {hors_mois[0].position})"
        )
    alertes: list[Alerte] = alertes_cartes(transactions, contexte)  # RG-17
    classes: dict[str, Classement] = {}

    # Règles 1 à 5
    for t in transactions:
        classement = (
            neutraliser_debit_differe(t)
            or classer_salaire(t, contexte, alertes)
            or classer_dotation(t, contexte)
            or classer_reprise_provision(t, contexte)
            or classer_paiement_finance(t, contexte)
        )
        if classement is not None:
            classes[t.id] = classement

    # Règle 6 : les charges fixes s'affectent ensemble, pour apparier les prêts par montant
    restantes = [t for t in transactions if t.id not in classes]
    charges, rapprochements = affecter_charges_fixes(restantes, contexte, alertes)
    classes.update((c.transaction_id, c) for c in charges)

    # Règles 7 et 8
    for t in transactions:
        if t.id in classes:
            continue
        classement = classer_trade_republic(t, alertes) or classer_paiement_carte(t)
        if classement is not None:
            classes[t.id] = classement

    # Règle 9 : virements internes ; la contrepartie d'une dotation devient une dotation
    for joint, tr in apparier_virements_internes(transactions):
        cote_joint = classes.get(joint.id) or Classement.non_classee(joint)
        classes[joint.id] = cote_joint.model_copy(update={"contrepartie_id": tr.id})
        if cote_joint.nature is Nature.DOTATION:
            cote_tr = Classement.pour(
                tr, Nature.DOTATION, OrigineClassement.CONTEXTE, f"{cote_joint.detail}:contrepartie"
            )
        else:
            cote_tr = classes.get(tr.id) or Classement.non_classee(tr)
        classes[tr.id] = cote_tr.model_copy(update={"contrepartie_id": joint.id})

    alertes.extend(alertes_dotations(transactions, classes, contexte))  # RG-05
    alertes.extend(alertes_provision(transactions, classes))  # RG-08
    return ResultatClassement(
        mois=mois,
        classements=tuple(classes.get(t.id) or Classement.non_classee(t) for t in transactions),
        alertes=tuple(alertes),
        rapprochements=tuple(rapprochements),
    )
