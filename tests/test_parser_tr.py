"""Parser du relevé PDF Trade Republic sur les fixtures (spec 03)."""

from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import ParseError
from budget.models import Compte, OperationBrute, Source
from budget.parsers import trade_republic
from budget.parsers.trade_republic import (
    ReleveTR,
    decouper_releves,
    lire_pages,
    parser_trade_republic,
)
from tests.conftest import DOSSIER_FIXTURES

TR_JUIN = DOSSIER_FIXTURES / "2026-06" / "TR_202606.pdf"
TR_JUILLET = DOSSIER_FIXTURES / "2026-07" / "TR_202607.pdf"


@pytest.fixture(scope="module")
def juin() -> ReleveTR:
    return parser_trade_republic(TR_JUIN)


def _resume(op: OperationBrute) -> tuple[date, str | None, Decimal, Decimal | None]:
    return op.date_operation, op.type_operation, op.montant, op.solde_apres


def test_juin_synthese_et_periode(juin: ReleveTR) -> None:
    assert juin.fichier == "TR_202606.pdf"
    assert juin.produit == "Compte courant"
    assert (juin.periode_debut, juin.periode_fin) == (date(2026, 6, 1), date(2026, 6, 30))
    assert (juin.solde_debut, juin.solde_fin) == (Decimal("1975.22"), Decimal("1533.57"))
    assert (juin.total_entrees, juin.total_sorties) == (Decimal("993.71"), Decimal("1435.36"))
    assert len(juin.operations) == 74


def test_juin_mapping_operation_brute(juin: ReleveTR) -> None:
    assert [op.position for op in juin.operations] == list(range(1, 75))
    for op in juin.operations:
        assert (op.source, op.compte, op.fichier) == (Source.TR, Compte.PERSO, "TR_202606.pdf")
        assert op.date_debit is None and op.ics is None and op.carte is None
        assert op.libelle == op.libelle_brut


def test_juin_premiere_et_derniere_operation(juin: ReleveTR) -> None:
    premiere, derniere = juin.operations[0], juin.operations[-1]
    assert _resume(premiere) == (date(2026, 6, 1), "Bonus", Decimal("8.54"), Decimal("1983.76"))
    assert premiere.libelle == "Cash reward allocation"
    assert _resume(derniere) == (date(2026, 6, 30), "Avoir", Decimal("-1.50"), Decimal("1533.57"))
    assert derniere.libelle == "NYX*AIRSERVFRANCE"


def test_juin_euro_colle_au_solde(juin: ReleveTR) -> None:
    """Le solde `€1238,73` faisait échouer le prototype (73 opérations sur 74)."""
    operation = next(op for op in juin.operations if op.solde_apres == Decimal("1238.73"))
    assert _resume(operation) == (date(2026, 6, 8), "Avoir", Decimal("-409.97"), Decimal("1238.73"))
    assert operation.libelle == "DECATHLON 0008"


def test_rg12_avoir_en_entree(juin: ReleveTR) -> None:
    """RG-12 : le type « Avoir » sert dans les deux sens ; seule la colonne donne le sens."""
    zalando = [op for op in juin.operations if op.libelle == "Zalando Payments"]
    assert [_resume(op) for op in zalando] == [
        (date(2026, 6, 6), "Avoir", Decimal("-51.00"), Decimal("1684.58")),
        (date(2026, 6, 11), "Avoir", Decimal("51.00"), Decimal("1333.69")),
    ]


def test_type_et_description_sur_deux_lignes(juin: ReleveTR) -> None:
    operation = juin.operations[9]
    assert _resume(operation) == (
        date(2026, 6, 2),
        "Exécution d'ordre",
        Decimal("-10.00"),
        Decimal("1875.37"),
    )
    assert operation.libelle.startswith("Savings plan execution")
    assert operation.libelle.endswith("quantity: 0.032138")


def test_juin_types_operation(juin: ReleveTR) -> None:
    assert Counter(op.type_operation for op in juin.operations) == {
        "Avoir": 57,
        "Exécution d'ordre": 6,
        "Virement": 6,
        "Prelevement bancaire": 2,
        "Bonus": 1,
        "Intérêts": 1,
        "Rendement": 1,
    }


def test_virement_sur_deux_lignes_date_type_montants(juin: ReleveTR) -> None:
    """Les descriptions de virements sont anonymisées : seuls date, type et montants comptent."""
    operation = juin.operations[5]
    assert _resume(operation) == (
        date(2026, 6, 2),
        "Virement",
        Decimal("100.00"),
        Decimal("2018.26"),
    )


def test_juillet_valeurs_figees() -> None:
    releve = parser_trade_republic(TR_JUILLET)
    assert (releve.periode_debut, releve.periode_fin) == (date(2026, 7, 1), date(2026, 7, 31))
    assert (releve.solde_debut, releve.total_entrees, releve.total_sorties, releve.solde_fin) == (
        Decimal("1533.57"),
        Decimal("1353.61"),
        Decimal("1444.46"),
        Decimal("1442.72"),
    )
    assert len(releve.operations) == 65
    assert _resume(releve.operations[0]) == (
        date(2026, 7, 1),
        "Intérêts",
        Decimal("1.62"),
        Decimal("1535.19"),
    )
    assert _resume(releve.operations[-1]) == (
        date(2026, 7, 31),
        "Avoir",
        Decimal("-2.95"),
        Decimal("1442.72"),
    )
    assert Counter(op.type_operation for op in releve.operations) == {
        "Avoir": 52,
        "Exécution d'ordre": 6,
        "Virement": 4,
        "Prelevement bancaire": 1,
        "Bonus": 1,
        "Intérêts": 1,
    }
    remboursement = next(op for op in releve.operations if op.position == 51)
    assert _resume(remboursement) == (
        date(2026, 7, 27),
        "Avoir",
        Decimal("39.99"),
        Decimal("1435.07"),
    )


def test_decoupage_des_releves() -> None:
    releves = decouper_releves(lire_pages(TR_JUIN), "TR_202606.pdf")
    assert [(r.synthese.produit, [p.numero for p in r.pages]) for r in releves] == [
        ("Compte courant", [1, 2, 3, 4, 5, 6]),
        ("Compte PEA", [7]),
        ("Compte PEA", [8]),
    ]


def test_pea_ignores() -> None:
    """Le PEA de juillet a des mouvements : aucun ne doit entrer dans le compte courant."""
    releves = decouper_releves(lire_pages(TR_JUILLET), "TR_202607.pdf")
    pea = [r.synthese for r in releves if r.synthese.produit == "Compte PEA"]
    assert [(s.total_entrees, s.total_sorties) for s in pea] == [
        (Decimal("0.00"), Decimal("0.00")),
        (Decimal("205.15"), Decimal("237.15")),
    ]
    assert len(parser_trade_republic(TR_JUILLET).operations) == 65


@pytest.mark.parametrize(("garder", "trouves"), [(slice(6, None), 0), (slice(None), 2)])
def test_exactement_un_compte_courant(
    monkeypatch: pytest.MonkeyPatch, garder: slice, trouves: int
) -> None:
    pages = lire_pages(TR_JUIN)
    pages_modifiees = pages[garder] if trouves == 0 else pages[:6] + pages
    monkeypatch.setattr(trade_republic, "lire_pages", lambda _chemin: pages_modifiees)
    with pytest.raises(ParseError, match=f"« Compte courant » attendu, {trouves} trouvé"):
        parser_trade_republic(TR_JUIN)


def test_pdf_illisible(tmp_path: Path) -> None:
    chemin = tmp_path / "TR_202606.pdf"
    chemin.write_bytes(b"ceci n'est pas un PDF")
    with pytest.raises(ParseError, match="TR_202606.pdf : PDF illisible"):
        parser_trade_republic(chemin)


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_donnees_reelles_identiques_aux_fixtures(
    chemin_donnees_reelles: Callable[[str], Path], mois: str
) -> None:
    """Montants, dates, types et soldes sont conservés par l'anonymisation."""
    nom = f"TR_{mois.replace('-', '')}.pdf"
    reel = parser_trade_republic(chemin_donnees_reelles(mois) / nom)
    fixture = parser_trade_republic(DOSSIER_FIXTURES / mois / nom)
    assert reel.model_dump(exclude={"operations"}) == fixture.model_dump(exclude={"operations"})
    assert [_resume(op) for op in reel.operations] == [_resume(op) for op in fixture.operations]
