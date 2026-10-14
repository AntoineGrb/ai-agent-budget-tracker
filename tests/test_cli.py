import re
from pathlib import Path

from typer.testing import CliRunner, Result

from budget import __version__
from budget.categories import SousCategorie
from budget.cli import app
from budget.ledger import Ledger
from budget.models import Source
from budget.regles import Critere, Regle, TypeRegle
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE

runner = CliRunner()
LARGE = {"COLUMNS": "200"}  # tableaux larges sur une ligne


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
    assert "En attente (--sans-questions ou entrée non interactive) :" in premier.output
    assert "questions : 25" in premier.output
    assert (tmp_path / "decisions.md").is_file()  # journal à côté de la base par défaut
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


def test_classify_sans_questions(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    assert _importer(base).exit_code == 0
    arguments = ["classify", "--mois", "2026-06", "--base", str(base)]
    resultat = runner.invoke(
        app, [*arguments, "--contexte", str(EXEMPLE_CONTEXTE), "--sans-questions"]
    )
    assert resultat.exit_code == 0, resultat.output
    for attendu in (
        "questions : 25",
        "marchands à valider : 62",
        "paiements Amazon à compléter : 3",
        "tags exceptionnels : 1",
        "Paiements carte sans sous-catégorie : 103",  # 105 moins 2 Amazon RG-13 automatiques
    ):
        assert attendu in resultat.output
    with Ledger(base) as ledger:
        assert ledger.decisions() == {}
        assert ledger.regles() == []
        assert ledger.propositions() == []  # aucun appel au LLM sans interlocuteur


def _base_classee(tmp_path: Path) -> Path:
    """Juin importé, une règle marchand MONOPRIX, puis classify sans questions."""
    base = tmp_path / "budget.sqlite"
    assert _importer(base).exit_code == 0
    with Ledger(base) as ledger:
        ledger.ajouter_regle(
            Regle(
                type=TypeRegle.MARCHAND_CARTE,
                source=Source.CA_CB,
                critere=Critere.LIBELLE_EXACT,
                valeur="MONOPRIX",
                sous_categorie=SousCategorie.COURSES_SUPERMARCHE,
            )
        )
    assert _classer(base).exit_code == 0
    return base


def _sous_categories_monoprix(base: Path) -> list[object]:
    with Ledger(base) as ledger:
        lignes = ledger.lire_sql(
            "SELECT c.sous_categorie, c.regle_id FROM classements c "
            "JOIN transactions t ON t.id = c.transaction_id "
            "WHERE t.libelle_normalise LIKE 'MONOPRIX%' AND t.source = 'ca_cb'"
        )
    return sorted(lignes, key=str)


def test_rules_list(tmp_path: Path) -> None:
    base = _base_classee(tmp_path)
    resultat = runner.invoke(app, ["rules", "list", "--base", str(base)], env=LARGE)
    assert resultat.exit_code == 0, resultat.output
    assert "Règles apprises (1)" in resultat.output
    ligne = next(ligne for ligne in resultat.output.splitlines() if "MONOPRIX" in ligne)
    assert "marchand_carte" in ligne and "courses.supermarche" in ligne
    assert re.search(r"│\s+2\s+│", ligne)  # deux opérations couvertes
    filtre = runner.invoke(app, ["rules", "list", "--base", str(base), "--type", "virement"])
    assert "Règles apprises (0)" in filtre.output


def test_rules_disable_remet_en_attente(tmp_path: Path) -> None:
    base = _base_classee(tmp_path)
    assert _sous_categories_monoprix(base) == [("courses.supermarche", 1)] * 2

    resultat = runner.invoke(app, ["rules", "disable", "1", "--base", str(base)])
    assert resultat.exit_code == 0, resultat.output
    assert "Règle désactivée : règle n° 1 (marchand_carte" in resultat.output
    assert "budget classify --mois 2026-06" in resultat.output

    assert _classer(base).exit_code == 0
    assert _sous_categories_monoprix(base) == [(None, None)] * 2
    toutes = runner.invoke(app, ["rules", "list", "--base", str(base), "--toutes"])
    assert "Règles apprises (1)" in toutes.output
    assert (
        "Règles apprises (0)" in runner.invoke(app, ["rules", "list", "--base", str(base)]).output
    )

    deja = runner.invoke(app, ["rules", "disable", "1", "--base", str(base)])
    assert deja.exit_code == 1 and "déjà désactivée" in deja.output
    inconnue = runner.invoke(app, ["rules", "disable", "99", "--base", str(base)])
    assert inconnue.exit_code == 1 and "règle n° 99 inconnue" in inconnue.output


def test_export(tmp_path: Path) -> None:
    base = _base_classee(tmp_path)
    arguments = ["export", "--mois", "2026-06", "--base", str(base), "--sortie", str(tmp_path)]
    resultat = runner.invoke(app, arguments)
    assert resultat.exit_code == 0, resultat.output
    assert "159 opérations exportées" in resultat.output
    assert (tmp_path / "budget_2026-06.csv").is_file()
    non_classe = runner.invoke(app, [*arguments[:2], "2026-07", *arguments[3:]])
    assert non_classe.exit_code == 1 and "non importé" in non_classe.output


def test_classify_sans_llm(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    assert _importer(base).exit_code == 0
    resultat = runner.invoke(
        app,
        [
            "classify",
            "--mois",
            "2026-06",
            "--base",
            str(base),
            "--contexte",
            str(EXEMPLE_CONTEXTE),
            "--sans-llm",
            "--journal",
            str(tmp_path / "journal.md"),
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert (tmp_path / "journal.md").is_file()
