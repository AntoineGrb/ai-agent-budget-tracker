"""RG-11 et RG-12 : opérations Trade Republic (spec 05)."""

from decimal import Decimal

import pytest

from budget.classement import Classement, classer_mois
from budget.config import Contexte, charger_contexte
from budget.models import Nature, Source
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _classer_tr(
    contexte: Contexte, type_operation: str, libelle: str, montant: str
) -> tuple[Classement, list[str]]:
    transaction = fabriquer_transaction(
        libelle, montant, source=Source.TR, type_operation=type_operation
    )
    resultat = classer_mois("2026-06", [transaction], contexte)
    codes = [a.code for a in resultat.alertes if a.transaction_id == transaction.id]
    return resultat.classements[0], codes


@pytest.mark.parametrize(
    ("type_operation", "libelle", "montant", "nature", "detail"),
    [
        ("Exécution d'ordre", "SAVINGS PLAN EXECUTION", "-10.00", Nature.EPARGNE, "RG-11:ordre"),
        ("Virement", "VERSEMENT PEA", "-15.20", Nature.EPARGNE, "RG-11:pea"),
        ("Intérêts", "INTEREST PAYMENT", "2.21", Nature.REVENU, "RG-11:interets"),
        ("Rendement", "CASH DIVIDEND", "1.16", Nature.REVENU, "RG-11:dividende"),
        ("Bonus", "CASH REWARD ALLOCATION", "8.54", Nature.REVENU, "RG-11:bonus"),
        ("Avoir", "DECATHLON", "-409.97", Nature.DEPENSE, "paiement_carte"),
        ("Avoir", "ZALANDO", "51.00", Nature.REMBOURSEMENT, "RG-09"),
        ("Virement", "INCOMING TRANSFER", "100.00", None, None),
        ("Prelevement bancaire", "SEPA DIRECT DEBIT", "-11.99", None, None),
    ],
)
def test_rg11_types_tr(
    contexte: Contexte,
    type_operation: str,
    libelle: str,
    montant: str,
    nature: Nature | None,
    detail: str | None,
) -> None:
    """RG-11 : épargne et revenus financiers ; `Avoir` = paiement carte ; le reste à l'étape 6."""
    classement, codes = _classer_tr(contexte, type_operation, libelle, montant)
    assert (classement.nature, classement.detail) == (nature, detail)
    assert codes == []


def test_rg11_type_inconnu(contexte: Contexte) -> None:
    """RG-11 : un type TR inconnu reste non classé et produit l'alerte `TR-TYPE`."""
    transaction = fabriquer_transaction(
        "LIBELLE CRYPTO", "-20.00", source=Source.TR, type_operation="Crypto"
    )
    resultat = classer_mois("2026-06", [transaction], contexte)
    assert resultat.classements[0].nature is None
    [alerte] = [a for a in resultat.alertes if a.transaction_id == transaction.id]
    assert alerte.code == "TR-TYPE"
    assert alerte.transaction_id == transaction.id
    assert alerte.message.startswith("type TR inconnu 'Crypto' : ")
    assert "LIBELLE CRYPTO" not in alerte.message


def test_rg12_sens_lu_dans_la_colonne_juin(contexte: Contexte) -> None:
    """RG-12 : l'`Avoir` Zalando en entrée est un remboursement, DECATHLON en sortie une dépense."""
    transactions = transactions_fixtures("2026-06")
    resultat = classer_mois("2026-06", transactions, contexte)
    par_id = {c.transaction_id: c for c in resultat.classements}
    avoirs = [t for t in transactions if t.source is Source.TR and t.type_operation == "Avoir"]
    [entree] = [t for t in avoirs if t.montant > 0]
    assert entree.montant == Decimal("51.00")
    assert par_id[entree.id].nature is Nature.REMBOURSEMENT
    [decathlon] = [t for t in avoirs if t.montant == Decimal("-409.97")]
    assert par_id[decathlon.id].nature is Nature.DEPENSE


def test_rg11_juin(contexte: Contexte) -> None:
    resultat = classer_mois("2026-06", transactions_fixtures("2026-06"), contexte)
    details = [c.detail for c in resultat.classements if c.detail and c.detail.startswith("RG-11")]
    assert sorted(details) == sorted(
        ["RG-11:ordre"] * 6 + ["RG-11:pea", "RG-11:interets", "RG-11:dividende", "RG-11:bonus"]
    )
    assert not [a for a in resultat.alertes if a.code == "TR-TYPE"]
