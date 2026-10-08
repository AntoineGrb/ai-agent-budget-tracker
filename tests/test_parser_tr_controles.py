"""Contrôles bloquants C1 à C5 et contrôle de sens du parser Trade Republic (spec 03)."""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from budget.errors import ControleError, ParseError
from budget.parsers.trade_republic import (
    LigneTR,
    SyntheseTR,
    controler_releve,
    decouper_releves,
    lire_lignes_page,
    lire_pages,
)
from tests.conftest import DOSSIER_FIXTURES

FICHIER = "TR_202606.pdf"
DEBUT, FIN = date(2026, 6, 1), date(2026, 6, 30)


@pytest.fixture(scope="module")
def juin() -> tuple[SyntheseTR, list[LigneTR]]:
    """Synthèse et lignes intermédiaires du compte courant de juin, avant contrôles."""
    releves = decouper_releves(lire_pages(DOSSIER_FIXTURES / "2026-06" / FICHIER), FICHIER)
    courant = next(r for r in releves if r.synthese.produit == "Compte courant")
    lignes = [ligne for page in courant.pages for ligne in lire_lignes_page(page, FICHIER)]
    return courant.synthese, lignes


def test_cas_nominal(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    assert len(lignes) == 74
    controler_releve(synthese, DEBUT, FIN, lignes, FICHIER)


def test_c1_operation_manquante(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    sans_uber_trip = lignes[:2] + lignes[3:]
    with pytest.raises(ControleError) as erreur:
        controler_releve(synthese, DEBUT, FIN, sans_uber_trip, FICHIER)
    assert str(erreur.value) == (
        "TR_202606.pdf : C1 : page 1, ligne 4 (opération n° 3), 01/06/2026 : solde précédent "
        "1 985,97 € + mouvement -30,64 € = 1 955,33 €, solde lu 1 915,36 € (écart 39,97 €)"
    )


def test_c1_montant_altere(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    index = next(i for i, ligne in enumerate(lignes) if ligne.solde == Decimal("1238.73"))
    alteree = [*lignes]
    alteree[index] = dataclasses.replace(lignes[index], montant=Decimal("-409.96"))
    with pytest.raises(ControleError, match=r"C1 : page 2, ligne 9 .* \(écart 0,01 €\)") as erreur:
        controler_releve(synthese, DEBUT, FIN, alteree, FICHIER)
    assert "DECATHLON" not in str(erreur.value)


def test_c2_derniere_operation_manquante(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    with pytest.raises(ControleError, match="C2 : total des sorties = 1 433,86 €"):
        controler_releve(synthese, DEBUT, FIN, lignes[:-1], FICHIER)


def test_c2_total_entrees(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    faussee = dataclasses.replace(synthese, total_entrees=Decimal("993.70"))
    with pytest.raises(ControleError, match="C2 : total des entrées = 993,71 €, synthèse = 993,70"):
        controler_releve(faussee, DEBUT, FIN, lignes, FICHIER)


def test_c3_solde_final(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    faussee = dataclasses.replace(synthese, solde_fin=Decimal("1533.58"))
    with pytest.raises(ControleError, match="C3 : dernier solde lu 1 533,57 €"):
        controler_releve(faussee, DEBUT, FIN, lignes, FICHIER)


def test_sens_inversion_entree_sortie(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    """RG-12 : un avoir lu dans la mauvaise colonne est détecté par le sens du solde."""
    synthese, lignes = juin
    index = next(i for i, ligne in enumerate(lignes) if ligne.solde == Decimal("1333.69"))
    inversee = [*lignes]
    inversee[index] = dataclasses.replace(lignes[index], montant=Decimal("-51.00"))
    with pytest.raises(ParseError) as erreur:
        controler_releve(synthese, DEBUT, FIN, inversee, FICHIER)
    message = str(erreur.value)
    assert message.startswith("TR_202606.pdf, page 2, ligne 15 : 11/06/2026 : mouvement -51,00 €")
    assert "colonne ENTRÉE/SORTIE mal attribuée" in message
    assert "Zalando" not in message


@pytest.mark.parametrize(
    ("debut", "fin"),
    [(date(2026, 6, 2), FIN), (DEBUT, date(2026, 6, 29)), (DEBUT, date(2026, 7, 31))],
)
def test_c4_periode_non_mensuelle(
    juin: tuple[SyntheseTR, list[LigneTR]], debut: date, fin: date
) -> None:
    synthese, lignes = juin
    with pytest.raises(ControleError, match="C4 : période .* un mois civil complet"):
        controler_releve(synthese, debut, fin, lignes, FICHIER)


def test_c4_date_hors_periode(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, lignes = juin
    decalee = [dataclasses.replace(lignes[0], date_operation=date(2026, 5, 31)), *lignes[1:]]
    with pytest.raises(ControleError, match=r"C4 : page 1, ligne 1 .* 31/05/2026 hors"):
        controler_releve(synthese, DEBUT, FIN, decalee, FICHIER)


def test_c5_aucune_operation(juin: tuple[SyntheseTR, list[LigneTR]]) -> None:
    synthese, _ = juin
    with pytest.raises(ControleError, match="C5 : aucune opération lue"):
        controler_releve(synthese, DEBUT, FIN, [], FICHIER)


def test_c5_releve_sans_mouvement() -> None:
    vide = SyntheseTR(
        "Compte courant", Decimal("12.00"), Decimal("0.00"), Decimal("0.00"), Decimal("12.00")
    )
    controler_releve(vide, DEBUT, FIN, [], FICHIER)
