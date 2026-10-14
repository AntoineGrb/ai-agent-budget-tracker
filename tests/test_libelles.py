import pytest

from budget.libelles import normaliser_libelle


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("UBER   *EATS HELP.UBER.COM", "UBER *EATS HELP.UBER.COM"),
        ("Prélèvement carte", "PRELEVEMENT CARTE"),
        ("MONOPRIX  1357 STMAU2042885/", "MONOPRIX 1357 STMAU2042885/"),
        (" Cotisation carte ", "COTISATION CARTE"),
        ("Régul opé débitrices", "REGUL OPE DEBITRICES"),
        ("Exécution d'ordre", "EXECUTION D'ORDRE"),
        ("1 071,04 €", "1 071,04 €"),
        ("ligne 1\nligne 2\r\n\tfin", "LIGNE 1 LIGNE 2 FIN"),
        ("", ""),
    ],
)
def test_normaliser_libelle(texte: str, attendu: str) -> None:
    assert normaliser_libelle(texte) == attendu
