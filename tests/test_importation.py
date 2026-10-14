"""Import d'un mois dans le ledger : RG-01, RG-02, complétude, période, atomicité (spec 04)."""

import shutil
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import ControleError, ParseError
from budget.importation import construire_transactions, importer_mois
from budget.ledger import Ledger
from budget.models import Source
from budget.parsers.credit_agricole import parser_ca_cartes
from tests.conftest import DOSSIER_FIXTURES, copier_mois, ecrire_variante

AUJOURD_HUI = date(2026, 10, 8)


def _compter(chemin: Path) -> tuple[int, int]:
    with sqlite3.connect(chemin) as connexion:
        imports = connexion.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        transactions = connexion.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return int(imports), int(transactions)


@pytest.mark.parametrize(
    ("mois", "attendu"),
    [
        ("2026-06", {Source.CA_CB: 48, Source.CA_JOINT: 37, Source.TR: 74}),
        ("2026-07", {Source.CA_CB: 86, Source.CA_JOINT: 28, Source.TR: 65}),
    ],
)
def test_valeurs_golden(ledger_vide: Ledger, mois: str, attendu: dict[Source, int]) -> None:
    rapport = importer_mois(mois, DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    assert {ligne.source: ligne.ajoutees for ligne in rapport.lignes} == attendu
    assert rapport.ajoutees == sum(attendu.values())
    transactions = ledger_vide.transactions(mois)
    assert Counter(t.source for t in transactions) == attendu
    assert {t.mois_budgetaire for t in transactions} == {mois}
    assert ledger_vide.mois_importes() == [mois]


def test_juin_puis_juillet_puis_reimport(ledger_vide: Ledger) -> None:
    for mois in ("2026-06", "2026-07"):
        importer_mois(mois, DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    rapport = importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    assert (rapport.ajoutees, rapport.deja_presentes) == (0, 159)
    assert ledger_vide.mois_importes() == ["2026-06", "2026-07"]
    assert len(ledger_vide.transactions()) == 159 + 179


def test_relecture_identique_a_l_ecriture(ledger_vide: Ledger) -> None:
    """Dates ISO et montants en centimes : la relecture restitue exactement les transactions."""
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    cartes = parser_ca_cartes(DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv")
    construites = construire_transactions([op for r in cartes.cartes for op in r.operations])
    assert ledger_vide.transactions("2026-06", Source.CA_CB) == construites
    tr = ledger_vide.transactions(source=Source.TR)
    assert (tr[0].montant, tr[0].solde_apres) == (Decimal("8.54"), Decimal("1983.76"))


@pytest.mark.parametrize(
    ("libelle", "date_operation", "mois"),
    [
        ("GUOFA 2 ST MAUR DES FOSS", date(2026, 5, 23), "2026-06"),
        ("PICARD SA NEMOURS", date(2026, 6, 15), "2026-07"),
    ],
)
def test_rg01_mois_budgetaire_des_cartes(
    ledger_vide: Ledger, libelle: str, date_operation: date, mois: str
) -> None:
    """RG-01 : un achat carte compte pour le mois de son débit, sa date d'achat est conservée."""
    importer_mois(mois, DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    trouvees = [
        t
        for t in ledger_vide.transactions(mois, Source.CA_CB)
        if t.libelle == libelle and t.date_operation == date_operation
    ]
    assert len(trouvees) == 1
    assert trouvees[0].mois_budgetaire == mois
    assert trouvees[0].date_debit is not None and trouvees[0].date_debit.month == int(mois[5:])


def test_rg01_joint_et_tr_au_mois_de_l_operation(ledger_vide: Ledger) -> None:
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    for source in (Source.CA_JOINT, Source.TR):
        assert {t.date_operation.month for t in ledger_vide.transactions(source=source)} == {6}


def test_rg02_pdf_tr_manquant(tmp_path: Path, ledger_vide: Ledger) -> None:
    dossier = copier_mois("2026-06", tmp_path / "raw")
    (dossier / "TR_202606.pdf").unlink()
    with pytest.raises(ControleError) as erreur:
        importer_mois("2026-06", tmp_path / "raw", ledger_vide, AUJOURD_HUI)
    message = str(erreur.value)
    assert message.startswith("RG-02 : mois 2026-06 incomplet")
    assert message.endswith(": TR_202606.pdf")


def test_rg02_dossier_vide(tmp_path: Path, ledger_vide: Ledger) -> None:
    (tmp_path / "2026-06").mkdir()
    with pytest.raises(ControleError) as erreur:
        importer_mois("2026-06", tmp_path, ledger_vide, AUJOURD_HUI)
    assert str(erreur.value).endswith(": CA_CB_202606.csv, CA_CPTE_JOINT_202606.csv, TR_202606.pdf")


def test_completude_du_mois(ledger_vide: Ledger) -> None:
    with pytest.raises(ControleError, match="2026-06 ne pourra être importé qu'à partir du 01/07"):
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, date(2026, 6, 30))
    assert ledger_vide.mois_importes() == []
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, date(2026, 7, 1))
    assert ledger_vide.mois_importes() == ["2026-06"]


def test_mois_invalide(ledger_vide: Ledger) -> None:
    with pytest.raises(ControleError, match="format attendu AAAA-MM"):
        importer_mois("2026-13", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)


@pytest.mark.parametrize(
    ("fichier_juillet", "nom_juin", "attendu"),
    [
        (
            "CA_CPTE_JOINT_202607.csv",
            "CA_CPTE_JOINT_202606.csv",
            "couvre la période du 01/07/2026 au 31/07/2026, attendu le mois 2026-06",
        ),
        ("TR_202607.pdf", "TR_202606.pdf", "couvre la période du 01/07/2026 au 31/07/2026"),
        ("CA_CB_202607.csv", "CA_CB_202606.csv", "carte X1091 débitée le 31/07/2026, hors"),
    ],
)
def test_fichier_d_un_autre_mois(
    tmp_path: Path, fichier_juillet: str, nom_juin: str, attendu: str
) -> None:
    dossier = copier_mois("2026-06", tmp_path / "raw")
    shutil.copyfile(DOSSIER_FIXTURES / "2026-07" / fichier_juillet, dossier / nom_juin)
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger, pytest.raises(ControleError, match=attendu):
        importer_mois("2026-06", tmp_path / "raw", ledger, AUJOURD_HUI)
    assert _compter(base) == (0, 0)


def test_atomicite_echec_de_parsing(tmp_path: Path) -> None:
    dossier = copier_mois("2026-06", tmp_path / "raw")
    (dossier / "TR_202606.pdf").write_bytes(b"PDF corrompu")
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger, pytest.raises(ParseError):
        importer_mois("2026-06", tmp_path / "raw", ledger, AUJOURD_HUI)
    assert _compter(base) == (0, 0)


def test_atomicite_echec_rg03(tmp_path: Path) -> None:
    dossier = copier_mois("2026-06", tmp_path / "raw")
    ecrire_variante(
        DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv",
        dossier,
        ('";3 116,50;', '";3 116,51;'),
    )
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger, pytest.raises(ControleError, match="RG-03"):
        importer_mois("2026-06", tmp_path / "raw", ledger, AUJOURD_HUI)
    assert _compter(base) == (0, 0)


def test_atomicite_echec_pendant_l_ecriture(tmp_path: Path) -> None:
    """Le 3e fichier échoue en base : les deux premiers, déjà écrits, sont annulés."""
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    with sqlite3.connect(base) as connexion:
        connexion.execute("DELETE FROM transactions WHERE source = 'tr'")
        connexion.execute("DELETE FROM imports WHERE source != 'tr'")
        connexion.execute(
            "DELETE FROM transactions WHERE import_id NOT IN (SELECT id FROM imports)"
        )
    with Ledger(base) as ledger, pytest.raises(ControleError, match="RG-16 : tr 2026-06"):
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    assert _compter(base) == (1, 0)


def test_montants_stockes_en_entiers(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    with sqlite3.connect(base) as connexion:
        types = connexion.execute(
            "SELECT DISTINCT typeof(montant_centimes), typeof(solde_apres_centimes), source "
            "FROM transactions ORDER BY source"
        ).fetchall()
        total = connexion.execute(
            "SELECT typeof(SUM(montant_centimes)) FROM transactions"
        ).fetchone()
    assert types == [
        ("integer", "null", "ca_cb"),
        ("integer", "null", "ca_joint"),
        ("integer", "integer", "tr"),
    ]
    assert total == ("integer",)


@pytest.mark.donnees_reelles
def test_donnees_reelles(tmp_path: Path, chemin_donnees_reelles: Callable[[str], Path]) -> None:
    dossier = chemin_donnees_reelles("2026-06").parent
    chemin_donnees_reelles("2026-07")
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        totaux = [
            importer_mois(m, dossier, ledger, AUJOURD_HUI).ajoutees for m in ("2026-06", "2026-07")
        ]
        assert totaux == [159, 179]
        assert importer_mois("2026-06", dossier, ledger, AUJOURD_HUI).ajoutees == 0
