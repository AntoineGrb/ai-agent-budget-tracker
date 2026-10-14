"""RG-04 : rattachement des salaires à leur mois de paie (spec 04)."""

from datetime import date
from decimal import Decimal

import pytest

from budget.config import Contexte, Salaire, charger_contexte
from budget.errors import ControleError
from budget.importation import construire_transactions
from budget.models import Source, Transaction
from budget.parsers.credit_agricole import parser_ca_joint
from budget.salaires import RattachementSalaire, rattacher_salaire
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _joint(mois: str) -> list[Transaction]:
    compact = mois.replace("-", "")
    releve = parser_ca_joint(DOSSIER_FIXTURES / mois / f"CA_CPTE_JOINT_{compact}.csv")
    return construire_transactions(releve.operations)


def _salaires(mois: str, contexte: Contexte) -> list[tuple[date, Decimal, RattachementSalaire]]:
    resultats = []
    for transaction in _joint(mois):
        if (rattachement := rattacher_salaire(transaction, contexte)) is not None:
            resultats.append((transaction.date_operation, transaction.montant, rattachement))
    return resultats


def _groupama(contexte: Contexte) -> Transaction:
    return next(t for t in _joint("2026-06") if "GROUPAMA" in t.libelle_normalise)


def test_rg04_juin(contexte: Contexte) -> None:
    """RG-04 : période lue dans le libellé, sinon fenêtre du 25 au 5."""
    assert _salaires("2026-06", contexte) == [
        (
            date(2026, 6, 29),
            Decimal("2729.05"),
            RattachementSalaire(nom="salaire_1", mois_paie="2026-06"),
        ),
        (
            date(2026, 6, 29),
            Decimal("2526.42"),
            RattachementSalaire(nom="salaire_2", mois_paie="2026-06"),
        ),
        (
            date(2026, 6, 1),
            Decimal("2303.31"),
            RattachementSalaire(nom="salaire_2", mois_paie="2026-05"),
        ),
    ]
    juin = sum(m for _, m, r in _salaires("2026-06", contexte) if r.mois_paie == "2026-06")
    assert juin == Decimal("5255.47")


def test_rg04_juillet(contexte: Contexte) -> None:
    assert _salaires("2026-07", contexte) == [
        (
            date(2026, 7, 28),
            Decimal("2726.58"),
            RattachementSalaire(nom="salaire_1", mois_paie="2026-07"),
        )
    ]


def test_rg04_fenetre_debut_du_mois_suivant(contexte: Contexte) -> None:
    salaire = _groupama(contexte).model_copy(update={"date_operation": date(2026, 7, 3)})
    assert rattacher_salaire(salaire, contexte) == RattachementSalaire(
        nom="salaire_1", mois_paie="2026-06"
    )


def test_rg04_fenetre_janvier_vers_decembre(contexte: Contexte) -> None:
    salaire = _groupama(contexte).model_copy(update={"date_operation": date(2027, 1, 5)})
    assert rattacher_salaire(salaire, contexte) == RattachementSalaire(
        nom="salaire_1", mois_paie="2026-12"
    )


def test_rg04_hors_fenetre_sans_periode(contexte: Contexte) -> None:
    """RG-04 : reçu le 10, on ne devine pas : mois de paie à trancher par question."""
    salaire = _groupama(contexte).model_copy(update={"date_operation": date(2026, 6, 10)})
    assert rattacher_salaire(salaire, contexte) == RattachementSalaire(
        nom="salaire_1", mois_paie=None, mois_possibles=("2026-05", "2026-06")
    )


def test_rg04_periode_invalide(contexte: Contexte) -> None:
    plume = next(t for t in _joint("2026-06") if "06/2026 NOM2S" in t.libelle)
    faux = plume.model_copy(update={"libelle": plume.libelle.replace("06/2026", "13/2026")})
    with pytest.raises(ControleError) as erreur:
        rattacher_salaire(faux, contexte)
    message = str(erreur.value)
    assert message.startswith("RG-04 : CA_CPTE_JOINT_202606.csv, position")
    assert "29/06/2026, 2 526,42 €" in message
    assert "'13/2026'" in message
    assert "PLUME" not in message


def test_rg04_deux_salaires_correspondants(contexte: Contexte) -> None:
    doublon = Salaire(nom="salaire_3", libelle_contient="GROUPAMA", montant_approx=Decimal(1))
    ambigu = contexte.model_copy(update={"salaires": (*contexte.salaires, doublon)})
    with pytest.raises(
        ControleError, match="plusieurs salaires du contexte .*salaire_1, salaire_3"
    ):
        rattacher_salaire(_groupama(contexte), ambigu)


def test_rg04_seuls_les_credits_du_joint_sont_candidats(contexte: Contexte) -> None:
    salaire = _groupama(contexte)
    debit = salaire.model_copy(update={"montant": Decimal("-2729.05")})
    autre_source = salaire.model_copy(update={"source": Source.TR})
    assert rattacher_salaire(debit, contexte) is None
    assert rattacher_salaire(autre_source, contexte) is None
