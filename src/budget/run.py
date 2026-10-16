"""`budget run` : import, classement avec questions, synthèse et publication, mois après mois.

Chaque étape réutilise telle quelle la fonction de sa commande et reste idempotente : relancer la
même commande après un arrêt reprend là où le run s'était arrêté (RG-16 pour l'import, questions
tranchées jamais reposées, commentaire réutilisé si les chiffres n'ont pas changé).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from budget.agents.categoriseur import Categoriseur
from budget.agents.synthese import CommentaireSynthese, Commentateur, EntreeSynthese
from budget.categories import SousCategorie
from budget.classement.cartes import GroupeMarchand
from budget.config import Contexte
from budget.dates import decaler_mois
from budget.errors import BudgetError
from budget.importation import importer_mois
from budget.ledger import Ledger, RapportImport
from budget.models import Source
from budget.publication.contenu import ContenuPublication, preparer_contenu
from budget.publication.excel import construire_classeur, enregistrer
from budget.questions.dialogue import Bilan, Interlocuteur
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.questions.validation_lot import BilanLot


class CategoriseurCompte:
    """Délègue au catégoriseur et compte ses appels (récapitulatif du run)."""

    def __init__(self, categoriseur: Categoriseur) -> None:
        self._categoriseur = categoriseur
        self.appels = 0

    @property
    def nom_modele(self) -> str | None:
        return self._categoriseur.nom_modele

    def proposer(
        self, groupes: Sequence[GroupeMarchand]
    ) -> dict[tuple[Source, str], SousCategorie]:
        self.appels += 1
        return self._categoriseur.proposer(groupes)


class CommentateurCompte:
    """Délègue à l'agent synthèse et compte ses appels, réussis ou non."""

    def __init__(self, commentateur: Commentateur) -> None:
        self._commentateur = commentateur
        self.appels = 0

    def commenter(self, entree: EntreeSynthese) -> CommentaireSynthese:
        self.appels += 1
        return self._commentateur.commenter(entree)


@dataclass
class RapportMois:
    mois: str
    importe: RapportImport | None = None
    classement: RapportClassement | None = None
    contenu: ContenuPublication | None = None
    classeur: Path | None = None  # None : publication non demandée (--simulation)

    @property
    def en_attente(self) -> int:
        return self.classement.resultat.en_attente if self.classement else 0

    @property
    def bilan(self) -> Bilan | None:
        return self.classement.bilan if self.classement else None

    @property
    def bilan_lot(self) -> BilanLot | None:
        return self.classement.bilan_lot if self.classement else None

    @property
    def termine(self) -> bool:
        return self.contenu is not None


@dataclass
class RapportRun:
    mois: list[RapportMois] = field(default_factory=list)
    erreur: BudgetError | None = None  # arrêt sur un contrôle, une extraction manquante…
    appels_categoriseur: int = 0
    appels_commentateur: int = 0

    @property
    def arret(self) -> RapportMois | None:
        """Le mois où le run s'est arrêté, ou `None` si tous les mois sont terminés."""
        return next((m for m in self.mois if not m.termine), None)

    @property
    def complet(self) -> bool:
        return self.erreur is None and self.arret is None

    @property
    def questions_posees(self) -> int:
        return sum(m.bilan.posees for m in self.mois if m.bilan)

    @property
    def regles_creees(self) -> int:
        return sum(
            (m.bilan.regles_creees if m.bilan else 0)
            + (m.bilan_lot.regles_creees if m.bilan_lot else 0)
            for m in self.mois
        )


def mois_entre(depuis: str, jusqu_a: str) -> list[str]:
    """Les mois de `depuis` à `jusqu_a` inclus, du plus ancien au plus récent."""
    mois = []
    courant = depuis
    while courant <= jusqu_a:
        mois.append(courant)
        courant = decaler_mois(courant, 1)
    return mois


def executer_run(
    mois: Sequence[str],
    dossier: Path,
    ledger: Ledger,
    contexte: Contexte,
    interlocuteur: Interlocuteur | None,
    categoriseur: Categoriseur,
    commentateur: Commentateur,
    *,
    aujourd_hui: date,
    maintenant: datetime,
    sortie: Path | None,
) -> RapportRun:
    """Enchaîne les quatre étapes ; s'arrête au premier mois en erreur ou incomplet.

    `sortie` est le dossier du classeur Excel ; `None` prépare la publication (contrôles et
    commentaire compris) sans écrire le fichier.
    """
    categoriseur_compte = CategoriseurCompte(categoriseur)
    commentateur_compte = CommentateurCompte(commentateur)
    rapport = RapportRun()
    try:
        for m in mois:
            etape = RapportMois(m)
            rapport.mois.append(etape)
            etape.importe = importer_mois(m, dossier, ledger, aujourd_hui)  # 1. import (RG-16)
            etape.classement = classer_et_questionner(  # 2. questions, puis validation en lot
                m, ledger, contexte, interlocuteur, categoriseur_compte
            )
            if etape.en_attente:  # 3. rien n'est synthétisé tant qu'une opération est en attente
                break
            etape.contenu = preparer_contenu(m, ledger, contexte, commentateur_compte)  # 4.
            if sortie is not None:
                chemin = sortie / f"budget_{m}.xlsx"
                etape.classeur = enregistrer(construire_classeur(etape.contenu, maintenant), chemin)
    except BudgetError as erreur:
        rapport.erreur = erreur
    rapport.appels_categoriseur = categoriseur_compte.appels
    rapport.appels_commentateur = commentateur_compte.appels
    return rapport
