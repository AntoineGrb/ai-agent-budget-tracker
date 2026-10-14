"""Correspondance d'un critère du contexte (spec 05)."""

from decimal import Decimal

from budget.categories import SousCategorie
from budget.classement.criteres import correspond, reperer
from budget.config import ChargeFixe
from tests.conftest import fabriquer_transaction


def _charge(**criteres: str) -> ChargeFixe:
    return ChargeFixe(
        nom="essai",
        montant=Decimal("10"),
        sous_categorie=SousCategorie.ABONNEMENTS_ENERGIE,
        **criteres,
    )


def test_ics_egalite_stricte() -> None:
    transaction = fabriquer_transaction(ics="FR41ZZZ272230")
    assert correspond(transaction, _charge(ics="FR41ZZZ272230"))
    assert not correspond(transaction, _charge(ics="FR41ZZZ27223"))
    assert not correspond(transaction, _charge(ics="fr41zzz272230"))
    assert not correspond(fabriquer_transaction(), _charge(ics="FR41ZZZ272230"))


def test_libelle_normalise_des_deux_cotes() -> None:
    transaction = fabriquer_transaction("Prélèvement  ASSU. CAAE prêt habitat 06/26")
    assert correspond(transaction, _charge(libelle_contient="assu. caae PRET habitat"))
    assert not correspond(transaction, _charge(libelle_contient="ASSU CAAE"))


def test_type_operation_egalite_apres_normalisation() -> None:
    transaction = fabriquer_transaction(type_operation="Remboursement de prêt")
    assert correspond(transaction, _charge(type_operation="REMBOURSEMENT DE PRET"))
    assert not correspond(transaction, _charge(type_operation="Remboursement"))
    assert not correspond(fabriquer_transaction(), _charge(type_operation="Remboursement"))


def test_et_logique_des_criteres() -> None:
    transaction = fabriquer_transaction(
        "FREE TELECOM", ics="FR83ZZZ459654", type_operation="Prélèvement"
    )
    assert correspond(
        transaction,
        _charge(ics="FR83ZZZ459654", libelle_contient="FREE", type_operation="prelevement"),
    )
    assert not correspond(transaction, _charge(ics="FR83ZZZ459654", libelle_contient="ORANGE"))
    assert not correspond(transaction, _charge(ics="FR00ZZZ000000", libelle_contient="FREE"))


def test_reperer_sans_libelle() -> None:
    transaction = fabriquer_transaction("LIBELLE SECRET", "-1071.04")
    texte = reperer(transaction)
    assert "LIBELLE SECRET" not in texte
    assert texte.endswith("15/06/2026, -1 071,04 €")
