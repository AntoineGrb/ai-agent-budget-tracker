import pytest

from budget.libelles import cle_marchand, normaliser_libelle


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


@pytest.mark.parametrize(
    ("libelle_normalise", "cle"),
    [
        ("AMAZON PAYMENTS PARIS2441535/", "AMAZON PAYMENTS"),
        ("AMAZON PAYMENTS 2441535", "AMAZON PAYMENTS"),
        ("MONOPRIX 1357 STMAU2042885/", "MONOPRIX"),
        ("DECATHLON CRETEI 0008 CRETEIL/", "DECATHLON CRETEI"),
        ("BK K0028 PUTEAU 4819855", "BK"),
        ("ESSO31816ROC1 2131616", "ESSO31816ROC1"),
        ("GUOFA 2 ST MAUR DES FOSS", "GUOFA 2 ST MAUR DES FOSS"),
        ("SUMUP *MES 4 MOMENTS SAINT MAUR DE", "SUMUP *MES 4 MOMENTS SAINT MAUR DE"),
        ("UBER *EATS HELP.UBER.COM", "UBER *EATS HELP.UBER.COM"),
        ("CANVA*/", "CANVA*"),
        ("123456", "123456"),
    ],
)
def test_cle_marchand(libelle_normalise: str, cle: str) -> None:
    """Clé marchand : sans numéro de magasin, de terminal ni de transaction (spec 07)."""
    assert cle_marchand(libelle_normalise) == cle
