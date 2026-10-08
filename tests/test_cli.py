from pathlib import Path

from typer.testing import CliRunner

from budget import __version__
from budget.cli import app
from tests.conftest import EXEMPLE_CONTEXTE

runner = CliRunner()


def test_aide() -> None:
    resultat = runner.invoke(app, ["--help"])
    assert resultat.exit_code == 0
    assert "config" in resultat.output


def test_version() -> None:
    resultat = runner.invoke(app, ["--version"])
    assert resultat.exit_code == 0
    assert __version__ in resultat.output


def test_config_verifier_succes() -> None:
    resultat = runner.invoke(app, ["config", "verifier", "--chemin", str(EXEMPLE_CONTEXTE)])
    assert resultat.exit_code == 0, resultat.output
    assert "Charges fixes" in resultat.output
    assert "2 797,25 €" in resultat.output


def test_config_verifier_echec(tmp_path: Path) -> None:
    chemin = tmp_path / "contexte.yaml"
    texte = EXEMPLE_CONTEXTE.read_text(encoding="utf-8")
    chemin.write_text(texte.replace("abonnements.energie}", "abonnements.gaz}"), encoding="utf-8")
    resultat = runner.invoke(app, ["config", "verifier", "--chemin", str(chemin)])
    assert resultat.exit_code == 1
    assert "charges_fixes[5].sous_categorie" in resultat.output
    assert "Traceback" not in resultat.output


def test_config_verifier_fichier_absent(tmp_path: Path) -> None:
    resultat = runner.invoke(app, ["config", "verifier", "--chemin", str(tmp_path / "absent.yaml")])
    assert resultat.exit_code == 1
    assert "introuvable" in resultat.output
