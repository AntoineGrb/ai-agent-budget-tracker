"""Paiements carte : reconnaissance, Amazon (RG-13) et groupes marchands en attente.

Un paiement carte en entrée est un avoir : il reçoit l'imputation de son marchand et se déduit de
son total (RG-09).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from budget.classement.modeles import Classement
from budget.classement.trade_republic import TYPE_AVOIR, type_tr
from budget.libelles import cle_marchand
from budget.models import Compte, Source, Transaction
from budget.regles import PREFIXES_AMAZON


def est_paiement_carte(transaction: Transaction) -> bool:
    """Opération du fichier cartes, ou `Avoir` Trade Republic (RG-12)."""
    return transaction.source is Source.CA_CB or (
        transaction.source is Source.TR and type_tr(transaction) == TYPE_AVOIR
    )


def est_amazon(transaction: Transaction) -> bool:
    """RG-13 : clé marchand commençant par l'un des `PREFIXES_AMAZON`."""
    return cle_marchand(transaction.libelle_normalise).startswith(PREFIXES_AMAZON)


def amazon_sous_le_seuil(transaction: Transaction, seuil: Decimal) -> bool:
    """RG-13 : Amazon de montant absolu strictement inférieur au seuil, avoirs compris."""
    return est_amazon(transaction) and abs(transaction.montant) < seuil


@dataclass(frozen=True)
class GroupeMarchand:
    """Paiements carte en attente d'un mois qui partagent `(source, cle_marchand)`."""

    source: Source
    cle: str
    transactions: tuple[Transaction, ...]

    @property
    def compte(self) -> Compte:
        return self.transactions[0].compte

    @property
    def total(self) -> Decimal:
        return sum((t.montant for t in self.transactions), start=Decimal(0))


def en_attente(
    transactions: Iterable[Transaction], classements: Mapping[str, Classement]
) -> list[Transaction]:
    """Paiements carte encore sans imputation."""
    return [
        t
        for t in transactions
        if est_paiement_carte(t) and (c := classements.get(t.id)) is not None and not c.decidee
    ]


def grouper(attente: Sequence[Transaction]) -> list[GroupeMarchand]:
    """Groupes marchands, joint puis perso, par total absolu décroissant.

    Un paiement Amazon n'entre dans aucun groupe : il ne part jamais au LLM et n'est couvert par
    aucune règle ; au-dessus du seuil, il fait l'objet d'une question (RG-13).
    """
    par_cle: dict[tuple[Source, str], list[Transaction]] = {}
    for t in attente:
        if not est_amazon(t):
            par_cle.setdefault((t.source, cle_marchand(t.libelle_normalise)), []).append(t)
    groupes = [GroupeMarchand(source, cle, tuple(ts)) for (source, cle), ts in par_cle.items()]
    groupes.sort(key=lambda g: (g.compte is Compte.PERSO, -abs(g.total), g.cle))
    return groupes
