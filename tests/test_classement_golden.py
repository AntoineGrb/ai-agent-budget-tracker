"""Classement des fixtures de juin et juillet 2026 : valeurs attendues, rejouabilité (spec 05)."""

import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.classement import ResultatClassement, classer_mois
from budget.config import Contexte, charger_contexte
from budget.errors import ControleError
from budget.importation import NOMS_FICHIERS, importer_mois
from budget.ledger import Ledger
from budget.models import Nature, Source, Transaction
from tests.conftest import (
    DOSSIER_DONNEES_REELLES,
    DOSSIER_FIXTURES,
    EXEMPLE_CONTEXTE,
    RACINE,
    transactions_fixtures,
)

CONTEXTE_REEL = RACINE / "data" / "contexte.yaml"

_Repartition = dict[tuple[Source, Nature | None], tuple[int, str]]

JUIN: _Repartition = {
    (Source.CA_JOINT, Nature.NEUTRALISE): (2, "-3527.17"),
    (Source.CA_JOINT, Nature.REVENU): (3, "7558.78"),
    (Source.CA_JOINT, Nature.DOTATION): (2, "-800.00"),
    (Source.CA_JOINT, Nature.PROVISION): (2, "-0.04"),
    (Source.CA_JOINT, Nature.DEPENSE): (8, "-2797.25"),
    (Source.CA_JOINT, None): (20, "1540.78"),
    (Source.CA_CB, Nature.DEPENSE): (47, "-3727.16"),
    (Source.CA_CB, Nature.REMBOURSEMENT): (1, "199.99"),
    (Source.TR, Nature.DEPENSE): (56, "-1271.07"),
    (Source.TR, Nature.REMBOURSEMENT): (1, "51.00"),
    (Source.TR, Nature.EPARGNE): (7, "-61.50"),
    (Source.TR, Nature.REVENU): (3, "11.91"),
    (Source.TR, Nature.DOTATION): (1, "500.00"),
    (Source.TR, None): (6, "328.01"),
}

# Figées après la première exécution réussie (spec 05, « valeurs de juillet »).
JUILLET: _Repartition = {
    (Source.CA_JOINT, Nature.NEUTRALISE): (2, "-3548.38"),
    (Source.CA_JOINT, Nature.REVENU): (1, "2726.58"),
    (Source.CA_JOINT, Nature.DEPENSE): (8, "-2532.15"),
    (Source.CA_JOINT, None): (17, "759.26"),
    (Source.CA_CB, Nature.DEPENSE): (78, "-4457.68"),
    (Source.CA_CB, Nature.REMBOURSEMENT): (8, "909.30"),
    (Source.TR, Nature.DEPENSE): (51, "-1182.49"),
    (Source.TR, Nature.REMBOURSEMENT): (1, "39.99"),
    (Source.TR, Nature.EPARGNE): (8, "-249.98"),
    (Source.TR, Nature.REVENU): (2, "13.62"),
    (Source.TR, None): (3, "1288.01"),
}


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _repartition(
    transactions: tuple[Transaction, ...], resultat: ResultatClassement
) -> _Repartition:
    par_id = {t.id: t for t in transactions}
    nombres: Counter[tuple[Source, Nature | None]] = Counter()
    totaux: dict[tuple[Source, Nature | None], Decimal] = {}
    for classement in resultat.classements:
        transaction = par_id[classement.transaction_id]
        cle = (transaction.source, classement.nature)
        nombres[cle] += 1
        totaux[cle] = totaux.get(cle, Decimal(0)) + transaction.montant
    return {cle: (nombres[cle], f"{totaux[cle]:.2f}") for cle in nombres}


@pytest.mark.parametrize(("mois", "attendu"), [("2026-06", JUIN), ("2026-07", JUILLET)])
def test_repartition_par_source_et_nature(
    contexte: Contexte, mois: str, attendu: _Repartition
) -> None:
    transactions = transactions_fixtures(mois)
    resultat = classer_mois(mois, transactions, contexte)
    assert _repartition(transactions, resultat) == attendu
    assert [c.transaction_id for c in resultat.classements] == [t.id for t in transactions]


def test_juin_details_alertes_et_synthese(contexte: Contexte) -> None:
    resultat = classer_mois("2026-06", transactions_fixtures("2026-06"), contexte)
    details = Counter(c.detail for c in resultat.classements)
    assert details["dotations.perso"] == details["dotations.provision"] == 1
    assert details["dotations.perso:contrepartie"] == 1
    assert details["provision.reprise"] == details["provision.copropriete"] == 1
    assert sum(n for d, n in details.items() if d and d.startswith("charges_fixes.")) == 8
    assert details["salaires.salaire_1"] == 1 and details["salaires.salaire_2"] == 2
    assert sum(1 for c in resultat.classements if c.nature is None) == 26
    assert resultat.virements_internes_apparies == 2
    assert resultat.alertes_par_code() == Counter({"RG-17": 2, "RG-05": 1})


def test_juillet_salaire_dotations_provision(contexte: Contexte) -> None:
    transactions = transactions_fixtures("2026-07")
    resultat = classer_mois("2026-07", transactions, contexte)
    montants = {t.id: t.montant for t in transactions}
    salaires = [c for c in resultat.classements if c.detail and c.detail.startswith("salaires.")]
    assert [(c.detail, montants[c.transaction_id], c.mois_rattachement) for c in salaires] == [
        ("salaires.salaire_1", Decimal("2726.58"), "2026-07")
    ]
    assert all(r.reel is not None for r in resultat.rapprochements)
    assert sum(1 for r in resultat.rapprochements if r.hors_seuil) == 2
    assert resultat.alertes_par_code() == Counter({"RG-05": 3, "RG-18": 2})
    assert not [c for c in resultat.classements if c.nature is Nature.PROVISION]
    assert resultat.virements_internes_apparies == 2
    [assurance] = [t for t in transactions if t.ics == "FR64ZZZ395200"]
    assert next(c for c in resultat.classements if c.transaction_id == assurance.id).nature is None


@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_aucun_libelle_dans_les_alertes(contexte: Contexte, mois: str) -> None:
    transactions = transactions_fixtures(mois)
    resultat = classer_mois(mois, transactions, contexte)
    for alerte in resultat.alertes:
        for transaction in transactions:
            assert transaction.libelle not in alerte.message
            assert transaction.libelle_normalise not in alerte.message


def _contenu_tables(chemin: Path) -> dict[str, list[tuple[object, ...]]]:
    requetes = {
        "classements": "SELECT * FROM classements ORDER BY transaction_id",
        "alertes": "SELECT mois, code, transaction_id, message FROM alertes ORDER BY id",
        "charges_fixes_mois": "SELECT * FROM charges_fixes_mois ORDER BY mois, nom",
        "charges_fixes_transactions": "SELECT * FROM charges_fixes_transactions ORDER BY 1, 2, 3",
    }
    with sqlite3.connect(chemin) as connexion:
        return {table: connexion.execute(sql).fetchall() for table, sql in requetes.items()}


def test_enregistrement_rejouable(tmp_path: Path, contexte: Contexte) -> None:
    """Une ligne de classement par transaction ; deux exécutions donnent des tables identiques."""
    chemin = tmp_path / "budget.sqlite"
    contenus = []
    with Ledger(chemin) as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 8))
        transactions = ledger.transactions("2026-06")
        for _ in range(2):
            resultat = classer_mois("2026-06", transactions, contexte)
            ledger.enregistrer_classement("2026-06", resultat)
            contenus.append(_contenu_tables(chemin))
            assert ledger.classements("2026-06") == list(resultat.classements)
    assert contenus[0] == contenus[1]
    assert len(contenus[0]["classements"]) == len(transactions) == 159
    assert len(contenus[0]["alertes"]) == 3
    assert len(contenus[0]["charges_fixes_mois"]) == 8
    assert len(contenus[0]["charges_fixes_transactions"]) == 8


def test_enregistrement_incomplet_refuse(ledger_vide: Ledger, contexte: Contexte) -> None:
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, date(2026, 10, 8))
    resultat = classer_mois("2026-06", ledger_vide.transactions("2026-06"), contexte)
    partiel = resultat.model_copy(update={"classements": resultat.classements[1:]})
    with pytest.raises(ControleError, match="158 classement"):
        ledger_vide.enregistrer_classement("2026-06", partiel)
    assert ledger_vide.classements("2026-06") == []


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_donnees_reelles_classees_sans_erreur(mois: str) -> None:
    compact = mois.replace("-", "")
    fichiers = [
        DOSSIER_DONNEES_REELLES / mois / nom.format(compact) for nom in NOMS_FICHIERS.values()
    ]
    if not all(f.is_file() for f in fichiers):
        pytest.skip(f"extractions réelles absentes : {DOSSIER_DONNEES_REELLES / mois}")
    if not CONTEXTE_REEL.is_file():
        pytest.skip(f"contexte réel absent : {CONTEXTE_REEL}")
    transactions = transactions_fixtures(mois, DOSSIER_DONNEES_REELLES)
    resultat = classer_mois(mois, transactions, charger_contexte(CONTEXTE_REEL))
    assert len(resultat.classements) == len(transactions)
