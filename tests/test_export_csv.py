"""`budget export` : colonnes, formats, tri, lien RG-07 (spec 07)."""

import csv
import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from budget.config import charger_contexte
from budget.errors import ControleError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.restitution.export_csv import COLONNES, exporter_mois
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE
from tests.scenarios.juin import AppelsCategoriseur, juin_complet


@pytest.fixture(scope="module")
def export_juin(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[bytes, list[dict[str, str]]]]:
    dossier = tmp_path_factory.mktemp("export")
    with Ledger(dossier / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 14))
        juin_complet(ledger, charger_contexte(EXEMPLE_CONTEXTE), AppelsCategoriseur())
        chemin, nombre = exporter_mois(ledger, "2026-06", dossier / "exports")
    assert chemin.name == "budget_2026-06.csv" and nombre == 159
    brut = chemin.read_bytes()
    lignes = list(csv.DictReader(io.StringIO(brut.decode("utf-8-sig")), delimiter=";"))
    yield brut, lignes


def test_format_du_fichier(export_juin: tuple[bytes, list[dict[str, str]]]) -> None:
    brut, lignes = export_juin
    assert brut.startswith(b"\xef\xbb\xbf")  # UTF-8 avec BOM
    entete = brut.decode("utf-8-sig").splitlines()[0]
    assert entete == ";".join(COLONNES)
    assert len(lignes) == 159
    assert not [ligne for ligne in lignes if ligne["Nature"] == "Non classée"]


def test_formats_des_valeurs(export_juin: tuple[bytes, list[dict[str, str]]]) -> None:
    _, lignes = export_juin
    neutralisee = next(ligne for ligne in lignes if ligne["Montant"] == "-3116,50")
    assert (neutralisee["Nature"], neutralisee["Règle appliquée"]) == ("Neutralisé", "RG-03")
    assert neutralisee["Date d'opération"] == "30/06/2026"
    cartes = [ligne for ligne in lignes if ligne["Date de débit"]]
    assert len(cartes) == 48 and {ligne["Date de débit"] for ligne in cartes} == {"30/06/2026"}
    assert all("\n" not in ligne["Libellé"] for ligne in lignes)
    assert {ligne["Compte"] for ligne in lignes} == {"Joint", "Perso"}
    pret = next(ligne for ligne in lignes if ligne["Montant"] == "-1787,46")
    assert pret["Règle appliquée"] == "Contexte : charges_fixes.pret_principal"
    spotifion = next(ligne for ligne in lignes if "Spotifion" in ligne["Libellé"])
    assert spotifion["Règle appliquée"] == "Décision"  # la décision prime sur la règle n° 1
    oliverstore = next(ligne for ligne in lignes if ligne["Montant"] == "-71,74")
    assert oliverstore["Règle appliquée"] == "Règle n° 5"
    assert (spotifion["Catégorie"], spotifion["Sous-catégorie"]) == (
        "Abonnements",
        "Streaming & musique",
    )
    [exceptionnelle] = [ligne for ligne in lignes if ligne["Exceptionnel"] == "oui"]
    assert exceptionnelle["Montant"] == "-883,16"


def test_tri_joint_puis_perso_par_date(export_juin: tuple[bytes, list[dict[str, str]]]) -> None:
    _, lignes = export_juin
    comptes = [ligne["Compte"] for ligne in lignes]
    assert comptes == sorted(comptes, key=lambda c: c == "Perso")
    for compte in ("Joint", "Perso"):
        dates = [
            date(int(d[6:]), int(d[3:5]), int(d[:2]))
            for d in (ligne["Date d'opération"] for ligne in lignes if ligne["Compte"] == compte)
        ]
        assert dates == sorted(dates)


def test_lien_rg07_prend_la_categorie_de_la_depense(
    export_juin: tuple[bytes, list[dict[str, str]]],
) -> None:
    _, lignes = export_juin
    velo = [ligne for ligne in lignes if ligne["Nature"] == "Lien à une dépense"]
    assert len(velo) == 2
    for ligne in velo:
        assert ligne["Lien"] == "Perso · 08/06/2026 · -409,97 €"
        assert (ligne["Catégorie"], ligne["Sous-catégorie"]) == ("Loisirs", "Sport")


def test_mois_non_classe(tmp_path: Path) -> None:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 14))
        with pytest.raises(ControleError, match="non classé"):
            exporter_mois(ledger, "2026-06", tmp_path)
        with pytest.raises(ControleError, match="non importé"):
            exporter_mois(ledger, "2026-07", tmp_path)
