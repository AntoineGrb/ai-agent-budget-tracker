import re
from pathlib import Path

from typer.testing import CliRunner, Result

from budget import __version__
from budget.cli import app
from budget.ledger import Ledger
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE

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


def _importer(base: Path, mois: str = "2026-06", dossier: Path = DOSSIER_FIXTURES) -> Result:
    arguments = ["import", "--mois", mois, "--dossier", str(dossier), "--base", str(base)]
    return runner.invoke(app, arguments)


def test_import_puis_reimport(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    premier = _importer(base)
    assert premier.exit_code == 0, premier.output
    assert "Import du mois 2026-06" in premier.output
    ligne_total = next(ligne for ligne in premier.output.splitlines() if "Total" in ligne)
    assert re.findall(r"\d+", ligne_total) == ["159", "159", "0"]

    second = _importer(base)
    assert second.exit_code == 0, second.output
    with Ledger(base) as ledger:
        assert len(ledger.transactions("2026-06")) == 159
    ligne_total = next(ligne for ligne in second.output.splitlines() if "Total" in ligne)
    assert re.findall(r"\d+", ligne_total) == ["159", "0", "159"]


def test_import_echec_sans_trace(tmp_path: Path) -> None:
    resultat = _importer(tmp_path / "budget.sqlite", dossier=tmp_path)
    assert resultat.exit_code == 1
    assert "RG-02 : mois 2026-06 incomplet" in resultat.output
    assert "Traceback" not in resultat.output


def test_import_mois_mal_forme(tmp_path: Path) -> None:
    resultat = _importer(tmp_path / "budget.sqlite", mois="06-2026")
    assert resultat.exit_code == 2
    assert "format attendu AAAA-MM" in resultat.output


def _classer(base: Path, mois: str = "2026-06") -> Result:
    arguments = [
        "classify",
        "--mois",
        mois,
        "--base",
        str(base),
        "--contexte",
        str(EXEMPLE_CONTEXTE),
    ]
    return runner.invoke(app, arguments)


def test_classify_succes_et_rejeu(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    assert _importer(base).exit_code == 0
    premier = _classer(base)
    assert premier.exit_code == 0, premier.output
    assert "Natures de flux : 2026-06" in premier.output
    assert "Opérations non classées (26)" in premier.output
    assert "Charges fixes (RG-18)" in premier.output
    assert "dotation perso_conjoint non trouvée" in premier.output
    assert "Virements internes appariés : 2" in premier.output
    with Ledger(base) as ledger:
        classements = ledger.classements("2026-06")
    assert len(classements) == 159

    second = _classer(base)
    assert second.exit_code == 0, second.output
    with Ledger(base) as ledger:
        assert ledger.classements("2026-06") == classements


def test_classify_mois_non_importe(tmp_path: Path) -> None:
    resultat = _classer(tmp_path / "budget.sqlite", mois="2026-08")
    assert resultat.exit_code == 1
    assert "mois 2026-08 non importé" in resultat.output
    assert "Traceback" not in resultat.output


def test_classify_contexte_absent(tmp_path: Path) -> None:
    arguments = ["classify", "--mois", "2026-06", "--base", str(tmp_path / "b.sqlite")]
    resultat = runner.invoke(app, [*arguments, "--contexte", str(tmp_path / "absent.yaml")])
    assert resultat.exit_code == 1
    assert "introuvable" in resultat.output
