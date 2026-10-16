"""`budget synthese` : reclassement, contrôles bloquants, agrégats, commentaire (spec 08).

1. Reclasse sans question les mois importés de M−11 à M, pour refléter les règles en vigueur.
2. Contrôles bloquants sur M : `ControleError` qui liste tous les manquements.
3. Agrégats, toujours recalculés et réécrits.
4. Commentaire : réutilisé si l'empreinte de l'entrée n'a pas changé, sauf `regenerer` ; sinon un
   appel à l'agent. Sans LLM ou après un échec, la synthèse est enregistrée sans commentaire.
"""

from dataclasses import dataclass
from datetime import datetime

from budget.agents.synthese import (
    CommentaireSynthese,
    Commentateur,
    EntreeSynthese,
    empreinte,
)
from budget.agregats import Agregats, calculer_agregats, controler_mois
from budget.agregats.calcul import MOIS_HISTORIQUE
from budget.config import Contexte
from budget.dates import decaler_mois
from budget.errors import SyntheseError
from budget.horloge import horodatage
from budget.ledger import Ledger
from budget.ledger.syntheses import SyntheseEnregistree
from budget.questions.execution import classer


@dataclass(frozen=True)
class Synthese:
    agregats: Agregats
    commentaire: CommentaireSynthese | None
    avertissement: str | None  # cause de l'absence de commentaire
    reutilise: bool  # commentaire repris de la base, sans appel


def produire_synthese(
    mois: str,
    ledger: Ledger,
    contexte: Contexte,
    commentateur: Commentateur,
    *,
    regenerer: bool = False,
) -> Synthese:
    """Calcule, contrôle, commente et enregistre la synthèse du mois."""
    fenetre = {decaler_mois(mois, -decalage) for decalage in range(MOIS_HISTORIQUE)}
    etats = {m: classer(m, ledger, contexte) for m in ledger.mois_importes() if m in fenetre}
    transactions = {m: etat.transactions for m, etat in etats.items()}
    classements = {m: etat.resultat.classements for m, etat in etats.items()}
    agregats = (
        calculer_agregats(mois, transactions, classements, contexte) if mois in etats else None
    )
    controler_mois(mois, transactions, classements, agregats)
    assert agregats is not None  # contrôle 1 : le mois est importé

    entree = EntreeSynthese(agregats=agregats, budgets=contexte.budgets)
    modele = contexte.llm.modele_synthese
    cle = empreinte(entree, modele)
    precedente = ledger.synthese(mois)
    maintenant = datetime.fromisoformat(horodatage())
    avertissement = None
    if (
        not regenerer
        and precedente is not None
        and precedente.empreinte == cle
        and precedente.commentaire is not None
    ):
        commentaire, commentee_le, reutilise = (
            precedente.commentaire,
            precedente.commentee_le,
            True,
        )
    else:
        reutilise = False
        try:
            commentaire, commentee_le = commentateur.commenter(entree), maintenant
        except SyntheseError as erreur:
            commentaire, commentee_le, avertissement = None, None, erreur.raison
    ledger.enregistrer_synthese(
        SyntheseEnregistree(
            mois=mois,
            empreinte=cle,
            agregats=agregats,
            commentaire=commentaire,
            modele=modele if commentaire is not None else None,
            calculee_le=maintenant,
            commentee_le=commentee_le,
        )
    )
    return Synthese(agregats, commentaire, avertissement, reutilise)
