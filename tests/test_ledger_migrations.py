"""Migrations du schéma SQLite (spec 04)."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from budget.errors import LedgerError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.ledger.migrations import MIGRATIONS, VERSION_COURANTE, appliquer_migrations
from tests.conftest import DOSSIER_FIXTURES

TABLES_V1 = {"imports", "transactions"}
TABLES_V2 = TABLES_V1 | {
    "classements",
    "alertes",
    "charges_fixes_mois",
    "charges_fixes_transactions",
}


def _tables(chemin: Path) -> set[str]:
    with sqlite3.connect(chemin) as connexion:
        lignes = connexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {str(nom) for (nom,) in lignes}


def test_base_neuve_en_version_courante(tmp_path: Path) -> None:
    chemin = tmp_path / "sous_dossier" / "budget.sqlite"
    with Ledger(chemin) as ledger:
        assert ledger.version == 2 == VERSION_COURANTE
        assert ledger.mois_importes() == []
    assert _tables(chemin) == TABLES_V2
    assert [m.numero for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))


def test_reouverture_n_applique_rien(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    Ledger(chemin).fermer()
    connexion = sqlite3.connect(chemin, isolation_level=None)
    try:
        assert appliquer_migrations(connexion, chemin.name) == []
    finally:
        connexion.close()
    with Ledger(chemin) as ledger:
        assert ledger.version == 2


def test_version_inconnue(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    connexion = sqlite3.connect(chemin)
    connexion.execute("PRAGMA user_version = 99")
    connexion.close()
    with pytest.raises(LedgerError, match="schéma en version 99, .* jusqu'à 2"):
        Ledger(chemin)


def test_fichier_qui_n_est_pas_une_base(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    chemin.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    with pytest.raises(LedgerError, match="base SQLite illisible"):
        Ledger(chemin)


def test_migration_en_echec_annulee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from budget.ledger import migrations

    fautive = migrations.Migration(
        3, "fautive", "CREATE TABLE t (x INTEGER); SELECT * FROM absente"
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", (*MIGRATIONS, fautive))
    monkeypatch.setattr(migrations, "VERSION_COURANTE", 3)
    chemin = tmp_path / "budget.sqlite"
    with pytest.raises(LedgerError, match="échec de la migration 0003"):
        Ledger(chemin)
    assert _tables(chemin) == TABLES_V2  # 0001 et 0002 conservées, 0003 annulée


def test_migration_0002_sur_base_v1_avec_transactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0002 appliquée à une base en version 1 qui contient déjà un mois importé."""
    from budget.ledger import migrations

    chemin = tmp_path / "budget.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", MIGRATIONS[:1])
        patch.setattr(migrations, "VERSION_COURANTE", 1)
        with Ledger(chemin) as ledger:
            importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 8))
            assert ledger.version == 1
    assert _tables(chemin) == TABLES_V1

    with Ledger(chemin) as ledger:
        assert ledger.version == 2
        assert len(ledger.transactions("2026-06")) == 159
        assert ledger.classements("2026-06") == []
    assert _tables(chemin) == TABLES_V2
