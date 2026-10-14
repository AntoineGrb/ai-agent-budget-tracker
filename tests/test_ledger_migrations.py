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
TABLES_V3 = TABLES_V2 | {"questions", "regles", "decisions"}
TABLES_V4 = TABLES_V3 | {"propositions", "exceptionnels"}


def _tables(chemin: Path) -> set[str]:
    with sqlite3.connect(chemin) as connexion:
        lignes = connexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {str(nom) for (nom,) in lignes}


def test_base_neuve_en_version_courante(tmp_path: Path) -> None:
    chemin = tmp_path / "sous_dossier" / "budget.sqlite"
    with Ledger(chemin) as ledger:
        assert ledger.version == 4 == VERSION_COURANTE
        assert ledger.mois_importes() == []
    assert _tables(chemin) == TABLES_V4
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
        assert ledger.version == 4


def test_version_inconnue(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    connexion = sqlite3.connect(chemin)
    connexion.execute("PRAGMA user_version = 99")
    connexion.close()
    with pytest.raises(LedgerError, match="schéma en version 99, .* jusqu'à 4"):
        Ledger(chemin)


def test_fichier_qui_n_est_pas_une_base(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    chemin.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    with pytest.raises(LedgerError, match="base SQLite illisible"):
        Ledger(chemin)


def test_migration_en_echec_annulee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from budget.ledger import migrations

    fautive = migrations.Migration(
        5, "fautive", "CREATE TABLE t (x INTEGER); SELECT * FROM absente"
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", (*MIGRATIONS, fautive))
    monkeypatch.setattr(migrations, "VERSION_COURANTE", 5)
    chemin = tmp_path / "budget.sqlite"
    with pytest.raises(LedgerError, match="échec de la migration 0005"):
        Ledger(chemin)
    assert _tables(chemin) == TABLES_V4  # 0001 à 0004 conservées, 0005 annulée


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
        assert ledger.version == VERSION_COURANTE
        assert len(ledger.transactions("2026-06")) == 159
        assert ledger.classements("2026-06") == []
    assert _tables(chemin) == TABLES_V4


def test_migration_0003_sur_base_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrations 0003 et 0004 appliquées à une base en version 2 qui contient un mois importé."""
    from budget.ledger import migrations

    chemin = tmp_path / "budget.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", MIGRATIONS[:2])
        patch.setattr(migrations, "VERSION_COURANTE", 2)
        with Ledger(chemin) as ledger:
            importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 8))
            assert ledger.version == 2
    assert _tables(chemin) == TABLES_V2

    with Ledger(chemin) as ledger:
        assert ledger.version == VERSION_COURANTE
        assert len(ledger.transactions("2026-06")) == 159
        assert ledger.regles() == [] and ledger.decisions() == {}
    assert _tables(chemin) == TABLES_V4


def test_migration_0004_sur_base_v3_avec_regles_et_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0004 sur une base en version 3 contenant une question, une règle, une décision."""
    from budget.ledger import migrations

    chemin = tmp_path / "budget.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", MIGRATIONS[:3])
        patch.setattr(migrations, "VERSION_COURANTE", 3)
        with Ledger(chemin) as ledger:
            importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 8))
            transaction = ledger.transactions("2026-06")[0]
    # Écriture au format de la version 3 : colonnes connues de ce schéma seulement
    with sqlite3.connect(chemin) as connexion:
        connexion.execute(
            "INSERT INTO questions (id, transaction_id, type, motif, posee_le, repondue_le) "
            "VALUES (1, ?, 'operation', 'non_classe', '2026-10-13T10:00:00+02:00', "
            "'2026-10-13T10:01:00+02:00')",
            (transaction.id,),
        )
        connexion.execute(
            "INSERT INTO regles (id, type, source, critere, valeur, nature, active, creee_le, "
            "question_id) VALUES (1, 'prelevement', 'ca_joint', 'ics', 'FR00ZZZ000000', "
            "'hors_budget', 1, '2026-10-13T10:01:00+02:00', 1)"
        )
        connexion.execute(
            "INSERT INTO decisions (transaction_id, question_id, nature, regle_id, decidee_le) "
            "VALUES (?, 1, 'hors_budget', 1, '2026-10-13T10:01:00+02:00')",
            (transaction.id,),
        )
    assert _tables(chemin) == TABLES_V3

    with Ledger(chemin) as ledger:
        assert ledger.version == 4
        [regle] = ledger.regles()
        assert (regle.id, regle.active, regle.desactivee_le) == (1, True, None)
        assert regle.creee_le is not None
        assert ledger.decisions()[transaction.id].regle_id == 1
        assert ledger.propositions() == [] and ledger.exceptionnels() == {}
    assert _tables(chemin) == TABLES_V4
