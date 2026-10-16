"""Classement d'un mois, sans question ni LLM (cadrage §7).

Seules les opérations suivies sont classées : les débits, et les avoirs carte (RG-19). Chacune
reçoit la première imputation applicable, dans l'ordre :

1. règles fixes : débit différé neutralisé (RG-03), investissement TR non compté (RG-11), Amazon
   sous le seuil en Achats › Autre (RG-13) ;
2. décision enregistrée pour l'opération ;
3. règle apprise la plus spécifique : règle marchand pour un paiement carte, sinon règle de
   virement ou de prélèvement, sauf RG-14.

Le reste est en attente : question pour un débit hors carte, validation en lot pour un paiement
carte.
"""

from collections.abc import Sequence
from decimal import Decimal

from budget.categories import SousCategorie
from budget.classement.cartes import amazon_sous_le_seuil, est_paiement_carte
from budget.classement.modeles import Classement, OrigineClassement, ResultatClassement
from budget.classement.trade_republic import est_investissement
from budget.errors import ControleError
from budget.libelles import normaliser_libelle
from budget.models import Source, Transaction
from budget.parsers.credit_agricole import MOTIF_DEBIT_DIFFERE, TYPE_DEBIT_DIFFERE
from budget.regles import Memoire, regle_marchand_retenue, regle_retenue


def est_suivie(transaction: Transaction) -> bool:
    """RG-19 : un débit, ou un avoir carte (RG-09) ; les autres crédits sont ignorés."""
    return transaction.montant < 0 or est_paiement_carte(transaction)


def est_debit_differe(transaction: Transaction) -> bool:
    """RG-03 : ligne de débit différé d'une carte sur le compte joint."""
    return (
        transaction.source is Source.CA_JOINT
        and transaction.type_operation is not None
        and normaliser_libelle(transaction.type_operation) == normaliser_libelle(TYPE_DEBIT_DIFFERE)
        and MOTIF_DEBIT_DIFFERE.search(transaction.libelle) is not None
    )


def _regle_fixe(transaction: Transaction, seuil_amazon: Decimal) -> Classement | None:
    fixe = OrigineClassement.REGLE_FIXE
    if est_debit_differe(transaction):  # RG-03 : remplacée par le détail des cartes
        return Classement(
            transaction_id=transaction.id, non_comptee=True, origine=fixe, detail="RG-03"
        )
    if est_investissement(transaction):  # RG-11
        return Classement(
            transaction_id=transaction.id, non_comptee=True, origine=fixe, detail="RG-11"
        )
    if est_paiement_carte(transaction) and amazon_sous_le_seuil(transaction, seuil_amazon):
        return Classement(
            transaction_id=transaction.id,
            sous_categorie=SousCategorie.ACHATS_AUTRE,
            origine=fixe,
            detail="RG-13",
        )
    return None


def classer_operation(
    transaction: Transaction, memoire: Memoire, seuil_amazon: Decimal
) -> Classement:
    """Imputation d'une opération suivie, ou classement en attente."""
    fixe = _regle_fixe(transaction, seuil_amazon)
    if fixe is not None:
        return fixe
    decision = memoire.decisions.get(transaction.id)
    if decision is not None:
        return Classement(
            transaction_id=transaction.id,
            sous_categorie=decision.sous_categorie,
            non_comptee=decision.non_comptee,
            origine=OrigineClassement.DECISION,
        )
    if est_paiement_carte(transaction):
        # RG-09 : un avoir reçoit l'imputation de son marchand
        regle = regle_marchand_retenue(memoire.regles, transaction)
    else:
        regle = regle_retenue(memoire.regles, transaction)
    if regle is not None:
        return Classement(
            transaction_id=transaction.id,
            sous_categorie=regle.sous_categorie,
            non_comptee=regle.non_comptee,
            origine=OrigineClassement.REGLE,
            regle_id=regle.id,
        )
    return Classement.attente(transaction)


def classer_mois(
    mois: str,
    transactions: Sequence[Transaction],
    seuil_amazon: Decimal,
    memoire: Memoire | None = None,
) -> ResultatClassement:
    """Classe les opérations suivies d'un mois. Fonction pure : ni base, ni fichier."""
    hors_mois = [t for t in transactions if t.mois_budgetaire != mois]
    if hors_mois:
        raise ControleError(
            f"classement du mois {mois} : {len(hors_mois)} transaction(s) d'un autre mois "
            f"budgétaire (première : {hors_mois[0].fichier}, position {hors_mois[0].position})"
        )
    memoire = memoire or Memoire()
    return ResultatClassement(
        mois=mois,
        classements=tuple(
            classer_operation(t, memoire, seuil_amazon) for t in transactions if est_suivie(t)
        ),
    )
