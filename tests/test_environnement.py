"""Chargement de `.env` (spec 07)."""

from pathlib import Path

import pytest

from budget.environnement import charger_env


def test_charger_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fichier = tmp_path / ".env"
    fichier.write_text(
        "# secrets locaux\n"
        "\n"
        "BUDGET_TEST_CLE=sk-test-123\n"
        'BUDGET_TEST_GUILLEMETS="valeur entre guillemets"\n'
        "export BUDGET_TEST_EXPORT='simple'\n"
        "BUDGET_TEST_EXISTANTE=nouvelle\n"
        "LIGNE SANS EGAL\n",
        encoding="utf-8",
    )
    for nom in ("BUDGET_TEST_CLE", "BUDGET_TEST_GUILLEMETS", "BUDGET_TEST_EXPORT"):
        monkeypatch.delenv(nom, raising=False)
    monkeypatch.setenv("BUDGET_TEST_EXISTANTE", "conservee")

    chargees = charger_env(fichier)

    assert chargees == ["BUDGET_TEST_CLE", "BUDGET_TEST_GUILLEMETS", "BUDGET_TEST_EXPORT"]
    import os

    assert os.environ["BUDGET_TEST_CLE"] == "sk-test-123"
    assert os.environ["BUDGET_TEST_GUILLEMETS"] == "valeur entre guillemets"
    assert os.environ["BUDGET_TEST_EXPORT"] == "simple"
    assert os.environ["BUDGET_TEST_EXISTANTE"] == "conservee"  # jamais écrasée
    for nom in chargees:
        monkeypatch.delenv(nom)


def test_fichier_absent(tmp_path: Path) -> None:
    assert charger_env(tmp_path / ".env") == []
