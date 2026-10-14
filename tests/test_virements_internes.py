"""Appariement des virements internes joint ↔ Trade Republic (spec 05)."""

from datetime import date, timedelta

import pytest

from budget.classement import classer_mois
from budget.classement.virements_internes import (
    DELAI_VIREMENT_INTERNE_JOURS,
    apparier_virements_internes,
)
from budget.config import Contexte, charger_contexte
from budget.models import Nature, Source, Transaction
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures

JOUR = date(2026, 6, 15)


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _joint(
    montant: str, libelle: str = "VIR INST VERS PRENOM NOM - VELO", jour: date = JOUR
) -> Transaction:
    type_operation = "Virement émis" if montant.startswith("-") else "Virement en votre faveur"
    return fabriquer_transaction(
        libelle, montant, date_operation=jour, type_operation=type_operation
    )


def _tr(montant: str, jour: date = JOUR, libelle: str = "INCOMING TRANSFER") -> Transaction:
    return fabriquer_transaction(
        libelle, montant, source=Source.TR, date_operation=jour, type_operation="Virement"
    )


def test_juin_deux_appariements() -> None:
    paires = apparier_virements_internes(transactions_fixtures("2026-06"))
    assert sorted((j.date_operation, j.montant, t.date_operation) for j, t in paires) == [
        (date(2026, 6, 15), -250, date(2026, 6, 15)),
        (date(2026, 6, 29), -500, date(2026, 6, 29)),
    ]


def test_juillet_deux_appariements_non_classes(contexte: Contexte) -> None:
    transactions = transactions_fixtures("2026-07")
    resultat = classer_mois("2026-07", transactions, contexte)
    apparies = [c for c in resultat.classements if c.contrepartie_id is not None]
    assert len(apparies) == 4
    assert all(c.nature is None for c in apparies)
    montants = {t.id: t.montant for t in transactions}
    assert sorted(abs(montants[c.transaction_id]) for c in apparies) == [300, 300, 1000, 1000]


@pytest.mark.parametrize(
    ("decalage", "apparie"),
    [
        (0, True),
        (DELAI_VIREMENT_INTERNE_JOURS, True),
        (DELAI_VIREMENT_INTERNE_JOURS + 1, False),
        (-1, False),
    ],
)
def test_fenetre_de_trois_jours(decalage: int, apparie: bool) -> None:
    joint, tr = _joint("-250.00"), _tr("250.00", JOUR + timedelta(days=decalage))
    assert (apparier_virements_internes([joint, tr]) == [(joint, tr)]) is apparie


def test_sens_inverse_tr_vers_joint() -> None:
    joint, tr = _joint("80.00", "DE MONSIEUR NOM PRENOM"), _tr("-80.00")
    assert apparier_virements_internes([joint, tr]) == [(joint, tr)]


def test_meme_signe_ou_autre_montant_non_apparies() -> None:
    assert apparier_virements_internes([_joint("250.00"), _tr("250.00")]) == []
    assert apparier_virements_internes([_joint("-250.00"), _tr("249.99")]) == []


def test_ambiguite_deux_virements_joint_de_meme_montant() -> None:
    """Deux virements de même montant dans la fenêtre : aucun appariement."""
    premier, second = _joint("-250.00"), _joint("-250.00", jour=JOUR + timedelta(days=1))
    tr = _tr("250.00", JOUR + timedelta(days=1))
    assert apparier_virements_internes([premier, second, tr]) == []


def test_ambiguite_deux_receptions_tr() -> None:
    joint = _joint("-250.00")
    assert (
        apparier_virements_internes([joint, _tr("250.00"), _tr("250.00", JOUR + timedelta(days=2))])
        == []
    )


def test_versement_pea_exclu() -> None:
    assert (
        apparier_virements_internes([_joint("15.20"), _tr("-15.20", libelle="VERSEMENT PEA")]) == []
    )


def test_contrepartie_de_dotation(contexte: Contexte) -> None:
    """Règle 9 : la contrepartie TR d'une dotation devient une dotation."""
    joint = _joint("-500.00", "VIR INST vers PRENOM NOM - Mensuel")
    tr = _tr("500.00")
    resultat = classer_mois("2026-06", [joint, tr], contexte)
    cote_joint, cote_tr = resultat.classements
    assert (cote_joint.nature, cote_joint.detail, cote_joint.contrepartie_id) == (
        Nature.DOTATION,
        "dotations.perso",
        tr.id,
    )
    assert (cote_tr.nature, cote_tr.detail, cote_tr.contrepartie_id) == (
        Nature.DOTATION,
        "dotations.perso:contrepartie",
        joint.id,
    )
    assert resultat.virements_internes_apparies == 1


def test_contrepartie_hors_dotation_reste_non_classee(contexte: Contexte) -> None:
    joint, tr = _joint("-250.00"), _tr("250.00")
    cote_joint, cote_tr = classer_mois("2026-06", [joint, tr], contexte).classements
    assert (cote_joint.nature, cote_joint.contrepartie_id) == (None, tr.id)
    assert (cote_tr.nature, cote_tr.contrepartie_id) == (None, joint.id)
