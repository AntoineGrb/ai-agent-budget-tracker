from datetime import date

import pytest

from budget.dates import dernier_jour_du_mois, mois_budgetaire, parse_date_fr
from budget.errors import ParseError


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("17/06/2026", date(2026, 6, 17)),
        ("18/06/26", date(2026, 6, 18)),
        ("30 juin 2026", date(2026, 6, 30)),
        ("1 août 2026", date(2026, 8, 1)),
        ("01 aout 2026", date(2026, 8, 1)),
        ("12 Décembre 2026", date(2026, 12, 12)),
        ("3 fevrier 2026", date(2026, 2, 3)),
        ("3 février 2026", date(2026, 2, 3)),
        ("01 juin \n2026", date(2026, 6, 1)),
        ("08 juil. 2026", date(2026, 7, 8)),
        ("08 juil. \n2026", date(2026, 7, 8)),
        ("2 déc. 2026", date(2026, 12, 2)),
        ("15 sept. 2026", date(2026, 9, 15)),
    ],
)
def test_parse_date_fr_formats_acceptes(texte: str, attendu: date) -> None:
    assert parse_date_fr(texte) == attendu


@pytest.mark.parametrize(
    "texte",
    [
        "2026-06-17",
        "",
        "17/6/2026",
        "31/02/2026",
        "30 juine 2026",
        "juin 2026",
        "08 juil 2026",
        "08 juin. 2026",
    ],
)
def test_parse_date_fr_formats_refuses(texte: str) -> None:
    with pytest.raises(ParseError):
        parse_date_fr(texte)


def test_mois_budgetaire() -> None:
    assert mois_budgetaire(date(2026, 6, 30)) == "2026-06"
    assert mois_budgetaire(date(2026, 11, 1)) == "2026-11"


@pytest.mark.parametrize(
    ("annee", "mois", "attendu"),
    [
        (2026, 6, date(2026, 6, 30)),
        (2026, 7, date(2026, 7, 31)),
        (2026, 2, date(2026, 2, 28)),
        (2028, 2, date(2028, 2, 29)),
        (2026, 12, date(2026, 12, 31)),
    ],
)
def test_dernier_jour_du_mois(annee: int, mois: int, attendu: date) -> None:
    assert dernier_jour_du_mois(annee, mois) == attendu
