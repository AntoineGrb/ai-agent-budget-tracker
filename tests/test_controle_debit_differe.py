"""Contrôle RG-03 : détail des cartes contre lignes de débit différé du joint (spec 02)."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from budget.controles import verifier_debit_differe
from budget.errors import ControleError
from budget.parsers.credit_agricole import (
    ReleveCartes,
    ReleveJoint,
    parser_ca_cartes,
    parser_ca_joint,
)
from tests.conftest import DOSSIER_FIXTURES, ecrire_variante

JOINT_JUIN = DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv"


def _releves(dossier: Path, mois: str) -> tuple[ReleveCartes, ReleveJoint]:
    compact = mois.replace("-", "")
    return (
        parser_ca_cartes(dossier / f"CA_CB_{compact}.csv"),
        parser_ca_joint(dossier / f"CA_CPTE_JOINT_{compact}.csv"),
    )


@pytest.fixture(scope="module")
def juin() -> tuple[ReleveCartes, ReleveJoint]:
    return _releves(DOSSIER_FIXTURES / "2026-06", "2026-06")


@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_rg03_cas_nominal(mois: str) -> None:
    verifier_debit_differe(*_releves(DOSSIER_FIXTURES / mois, mois))


def test_rg03_mois_different(juin: tuple[ReleveCartes, ReleveJoint]) -> None:
    cartes_juillet = parser_ca_cartes(DOSSIER_FIXTURES / "2026-07" / "CA_CB_202607.csv")
    with pytest.raises(ControleError, match="RG-03 : carte X1091 débitée le 31/07/2026, hors"):
        verifier_debit_differe(cartes_juillet, juin[1])


def test_rg03_ligne_de_debit_differe_manquante(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        JOINT_JUIN, tmp_path, ("DEPENSES CARTE X1481 AU 18/06/26", "DEPENSES DIVERSES")
    )
    cartes = parser_ca_cartes(DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv")
    with pytest.raises(ControleError) as erreur:
        verifier_debit_differe(cartes, parser_ca_joint(chemin))
    assert str(erreur.value) == (
        "RG-03 : carte X1481, débit du 30/06/2026 : 1 ligne de débit différé attendue dans "
        "CA_CPTE_JOINT_202606.csv, 0 trouvée(s)."
    )


def test_rg03_section_carte_manquante(juin: tuple[ReleveCartes, ReleveJoint]) -> None:
    cartes, joint = juin
    sans_x1091 = cartes.model_copy(update={"cartes": cartes.cartes[1:]})
    with pytest.raises(ControleError) as erreur:
        verifier_debit_differe(sans_x1091, joint)
    assert str(erreur.value) == (
        "RG-03 : carte X1091, débit du 30/06/2026 : 1 section carte attendue dans "
        "CA_CB_202606.csv, 0 trouvée(s)."
    )


def test_rg03_ecart_d_un_centime(tmp_path: Path) -> None:
    chemin = ecrire_variante(JOINT_JUIN, tmp_path, ('";3 116,50;', '";3 116,51;'))
    cartes = parser_ca_cartes(DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv")
    with pytest.raises(ControleError) as erreur:
        verifier_debit_differe(cartes, parser_ca_joint(chemin))
    assert str(erreur.value) == (
        "RG-03 : carte X1091, débit du 30/06/2026 : joint = 3 116,51 €, "
        "détail cartes = 3 116,50 € (écart 0,01 €)."
    )


def test_rg03_operation_posterieure_a_la_date_d_arrete(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        JOINT_JUIN,
        tmp_path,
        ("DEPENSES CARTE X1091 AU 18/06/26", "DEPENSES CARTE X1091 AU 16/06/26"),
    )
    cartes = parser_ca_cartes(DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv")
    with pytest.raises(ControleError) as erreur:
        verifier_debit_differe(cartes, parser_ca_joint(chemin))
    message = str(erreur.value)
    assert message.splitlines() == [
        "RG-03 : carte X1091, CA_CB_202606.csv ligne 18 : opération du 17/06/2026 (-258,99 €) "
        "postérieure à la date d'arrêté du 16/06/2026.",
        "RG-03 : carte X1091, CA_CB_202606.csv ligne 19 : opération du 17/06/2026 (-21,09 €) "
        "postérieure à la date d'arrêté du 16/06/2026.",
    ]
    assert "MILIBOO" not in message


def test_rg03_ecart_montant_ne_masque_pas_les_autres_cartes(
    juin: tuple[ReleveCartes, ReleveJoint],
) -> None:
    cartes, joint = juin
    faux = tuple(
        d.model_copy(update={"montant": d.montant + Decimal("1")}) for d in joint.debits_differes
    )
    with pytest.raises(ControleError) as erreur:
        verifier_debit_differe(cartes, joint.model_copy(update={"debits_differes": faux}))
    assert len(str(erreur.value).splitlines()) == 2


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_rg03_donnees_reelles(chemin_donnees_reelles: Callable[[str], Path], mois: str) -> None:
    verifier_debit_differe(*_releves(chemin_donnees_reelles(mois), mois))
