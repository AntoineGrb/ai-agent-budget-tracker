from decimal import Decimal

import pytest

from budget.errors import ParseError
from budget.montants import formater_montant, parse_montant, parse_montant_optionnel


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("550", "550.00"),
        ("4,8", "4.80"),
        ("1 071,04", "1071.04"),
        ("3 116,50", "3116.50"),
        ("-3 116.50 €", "-3116.50"),
        ("1983,76 €", "1983.76"),
        ("€1238,73", "1238.73"),
        ("1 071,04", "1071.04"),
        ("1 071,04", "1071.04"),
    ],
)
def test_parse_montant_formats_acceptes(texte: str, attendu: str) -> None:
    resultat = parse_montant(texte)
    assert resultat == Decimal(attendu)
    assert str(resultat) == attendu
    assert isinstance(resultat, Decimal)


@pytest.mark.parametrize("texte", ["", "   ", "1.071,04", "12,5,0", "abc", "1,234", "--5", "5-"])
def test_parse_montant_formats_refuses(texte: str) -> None:
    with pytest.raises(ParseError):
        parse_montant(texte)


def test_parse_montant_optionnel_vide_renvoie_none() -> None:
    assert parse_montant_optionnel("") is None
    assert parse_montant_optionnel("  € ") is None


def test_parse_montant_optionnel_delegue_a_parse_montant() -> None:
    assert parse_montant_optionnel("4,8") == Decimal("4.80")
    with pytest.raises(ParseError):
        parse_montant_optionnel("1.071,04")


@pytest.mark.parametrize(
    ("montant", "attendu"),
    [
        (Decimal("-3116.5"), "-3 116,50 €"),
        (Decimal("1071.04"), "1 071,04 €"),
        (Decimal("0"), "0,00 €"),
        (Decimal("12"), "12,00 €"),
        (Decimal("1234567.891"), "1 234 567,89 €"),
        (Decimal("1.005"), "1,01 €"),
        (Decimal("-0.001"), "0,00 €"),
    ],
)
def test_formater_montant(montant: Decimal, attendu: str) -> None:
    assert formater_montant(montant) == attendu
