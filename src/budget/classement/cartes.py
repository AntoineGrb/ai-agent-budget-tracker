"""Sous-catégorie des paiements carte et groupes marchands (spec 07).

La nature d'un paiement carte vient de son sens (règle 8 de la spec 05) et n'est jamais modifiée
ici. La sous-catégorie est cherchée dans l'ordre : décision (ligne RG-13 validée), RG-13 sous le
seuil, règle `marchand_carte` apprise ; sinon le paiement reste en attente de catégorisation.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from budget.categories import SousCategorie
from budget.classement.modeles import Classement, OrigineClassement
from budget.classement.trade_republic import TYPE_AVOIR, type_tr
from budget.libelles import cle_marchand
from budget.models import Compte, Source, Transaction
from budget.regles import PREFIXES_AMAZON, Memoire, regle_marchand_retenue

DETAIL_RG13 = "RG-13"


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


def sous_categoriser_cartes(
    transactions: Iterable[Transaction],
    classes: dict[str, Classement],
    memoire: Memoire,
    seuil_amazon: Decimal,
) -> None:
    """Complète la sous-catégorie des paiements carte déjà classés en nature (spec 07)."""
    for t in transactions:
        base = classes.get(t.id)
        if not est_paiement_carte(t) or base is None or base.nature is None:
            continue
        if base.sous_categorie is not None:
            continue
        decision = memoire.decisions.get(t.id)
        if decision is not None and decision.sous_categorie is not None:
            classes[t.id] = base.model_copy(
                update={
                    "sous_categorie": decision.sous_categorie,
                    "origine": OrigineClassement.DECISION,
                    "detail": DETAIL_RG13,
                }
            )
        elif amazon_sous_le_seuil(t, seuil_amazon):  # RG-13
            classes[t.id] = base.model_copy(
                update={"sous_categorie": SousCategorie.ACHATS_DIVERS, "detail": DETAIL_RG13}
            )
        elif (regle := regle_marchand_retenue(memoire.regles, t)) is not None:
            # RG-09 : un avoir reçoit la sous-catégorie du marchand, déduite à l'agrégation
            classes[t.id] = base.model_copy(
                update={
                    "sous_categorie": regle.sous_categorie,
                    "origine": OrigineClassement.REGLE,
                    "detail": f"regles.{regle.id}",
                    "regle_id": regle.id,
                }
            )


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
    transactions: Iterable[Transaction],
    classements: Mapping[str, Classement],
) -> list[Transaction]:
    """Paiements carte classés en nature mais sans sous-catégorie."""
    return [
        t
        for t in transactions
        if est_paiement_carte(t)
        and (c := classements.get(t.id)) is not None
        and c.nature is not None
        and c.sous_categorie is None
    ]


def grouper(
    attente: Sequence[Transaction], seuil_amazon: Decimal
) -> tuple[list[GroupeMarchand], list[Transaction]]:
    """Groupes marchands (joint puis perso, total absolu décroissant) et Amazon à compléter.

    Un paiement Amazon au-dessus du seuil n'entre dans aucun groupe : il ne part jamais au LLM et
    n'est couvert par aucune règle (RG-13). Il est présenté seul, dans l'ordre chronologique.
    """
    par_cle: dict[tuple[Source, str], list[Transaction]] = {}
    amazon: list[Transaction] = []
    for t in attente:
        if est_amazon(t):
            if not amazon_sous_le_seuil(t, seuil_amazon):
                amazon.append(t)
            continue
        par_cle.setdefault((t.source, cle_marchand(t.libelle_normalise)), []).append(t)
    groupes = [GroupeMarchand(source, cle, tuple(ts)) for (source, cle), ts in par_cle.items()]
    groupes.sort(key=lambda g: (g.compte is Compte.PERSO, -abs(g.total), g.cle))
    amazon.sort(key=lambda t: (t.date_operation, t.source.value, t.position))
    return groupes, amazon
