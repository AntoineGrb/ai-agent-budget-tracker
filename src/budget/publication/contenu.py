"""Ce que publie `budget publish` : synthèses et opérations des mois publiés (spec 09).

Le classeur du mois M est construit en entier à partir du ledger : il contient M et chaque mois
antérieur qui a déjà une synthèse. Chacun repasse par la chaîne de la spec 08
(reclassement, contrôles bloquants, agrégats, commentaire réutilisé si l'empreinte n'a pas
changé). Un mois en échec lève `ControleError` avant toute écriture : la synthèse et les
opérations d'un onglet reflètent donc toujours le même classement.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from budget.agents.synthese import Commentateur
from budget.config import Contexte
from budget.ledger import Ledger
from budget.ledger.syntheses import SyntheseEnregistree
from budget.restitution.operations import LigneOperation, lignes_operations
from budget.restitution.synthese import produire_synthese


@dataclass(frozen=True)
class MoisPublie:
    synthese: SyntheseEnregistree
    operations: Sequence[LigneOperation]  # dans l'ordre de `lignes_operations`

    @property
    def mois(self) -> str:
        return self.synthese.mois


@dataclass(frozen=True)
class ContenuPublication:
    mois: Sequence[MoisPublie]  # ordre chronologique

    @property
    def syntheses(self) -> list[SyntheseEnregistree]:
        return [m.synthese for m in self.mois]


def preparer_contenu(
    mois: str, ledger: Ledger, contexte: Contexte, commentateur: Commentateur
) -> ContenuPublication:
    """Synthétise le mois et les mois antérieurs déjà synthétisés, puis rassemble le contenu."""
    publies = sorted({mois, *(s.mois for s in ledger.syntheses() if s.mois < mois)})
    for m in publies:
        produire_synthese(m, ledger, contexte, commentateur)
    enregistrees = {s.mois: s for s in ledger.syntheses()}
    contenu = []
    for m in publies:
        classements = {c.transaction_id: c for c in ledger.classements(m)}
        operations = lignes_operations(ledger.transactions(m), classements)
        contenu.append(MoisPublie(enregistrees[m], operations))
    return ContenuPublication(contenu)
