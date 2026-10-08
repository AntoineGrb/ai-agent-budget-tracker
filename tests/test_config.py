from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from budget.categories import SousCategorie
from budget.config import charger_contexte, charger_yaml
from budget.errors import ConfigError
from budget.models import Compte, OperationBrute, Source
from tests.conftest import EXEMPLE_CONTEXTE

TEXTE_EXEMPLE = EXEMPLE_CONTEXTE.read_text(encoding="utf-8")


def _ecrire(tmp_path: Path, texte: str) -> Path:
    chemin = tmp_path / "contexte.yaml"
    chemin.write_text(texte, encoding="utf-8")
    return chemin


def _variante(tmp_path: Path, avant: str, apres: str) -> Path:
    assert avant in TEXTE_EXEMPLE
    return _ecrire(tmp_path, TEXTE_EXEMPLE.replace(avant, apres, 1))


def _erreurs(chemin: Path) -> list[str]:
    with pytest.raises(ConfigError) as info:
        charger_contexte(chemin)
    return info.value.erreurs


def test_exemple_valide() -> None:
    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    assert len(contexte.salaires) == 2
    assert len(contexte.dotations) == 3
    assert len(contexte.charges_fixes) == 8
    assert contexte.comptes.joint.cartes_actives == ("X1091", "X1481")
    assert contexte.charges_fixes[5].sous_categorie is SousCategorie.ABONNEMENTS_ENERGIE
    assert contexte.depenses_annuelles_connues[0].mois == (6,)
    assert contexte.depenses_annuelles_connues[2].mois == (7, 8)
    assert contexte.llm.modele_synthese == "anthropic:claude-sonnet-5"


def test_montants_yaml_en_decimal_exacts() -> None:
    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    pret = contexte.charges_fixes[0].montant
    offre = contexte.charges_fixes[7].montant
    assert isinstance(pret, Decimal) and str(pret) == "1787.46"
    assert isinstance(offre, Decimal) and str(offre) == "15.50"
    assert isinstance(contexte.enveloppes.perso, Decimal)
    assert isinstance(contexte.seuils.ecart_charge_fixe_pct, Decimal)


def test_chargeur_yaml_ne_produit_jamais_de_float(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, "a: 1787.46\nb: [15.50, 1_000.5, -2.5]\nc: 3\n")
    donnees = charger_yaml(chemin)
    assert donnees == {
        "a": Decimal("1787.46"),
        "b": [Decimal("15.50"), Decimal("1000.5"), Decimal("-2.5")],
        "c": 3,
    }
    assert all(not isinstance(v, float) for v in donnees["b"])


def test_cle_inconnue(tmp_path: Path) -> None:
    erreurs = _erreurs(_variante(tmp_path, "version: 1\n", "version: 1\ninconnue: 3\n"))
    assert erreurs == ["inconnue : clé inconnue"]


def test_cle_inconnue_imbriquee(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, "joint_courant: 1200", "joint_courant: 1200\n  perso_antoine: 1")
    assert _erreurs(chemin) == ["enveloppes.perso_antoine : clé inconnue"]


def test_sous_categorie_inexistante(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, "abonnements.energie}", "abonnements.gaz}")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("charges_fixes[5].sous_categorie : valeur inconnue 'abonnements.gaz'")


def test_carte_sans_x(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, "cartes_actives: [X1091,", "cartes_actives: ['1091',")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("comptes.joint.cartes_actives[0] : valeur '1091' invalide")


def test_regex_a_deux_groupes(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, r"'(\d{2}/\d{4}) NOM2S'", r"'(\d{2})/(\d{4}) NOM2S'")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("salaires[1].periode_regex : ")
    assert "exactement un groupe capturant, 2 trouvé(s)" in erreur


def test_regex_invalide(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, r"'(\d{2}/\d{4}) NOM2S'", r"'(\d{2}/\d{4} NOM2S'")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("salaires[1].periode_regex : expression régulière invalide")


def test_charge_fixe_sans_critere(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, "ics: DE56AGR00002197951, ", "")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("charges_fixes[5] : au moins un critère est requis")


def test_paiement_finance_sans_critere(tmp_path: Path) -> None:
    chemin = _variante(tmp_path, "ics: FR48ZZZ829660, ", "")
    (erreur,) = _erreurs(chemin)
    assert erreur.startswith("provision.paiements_finances[0] : au moins un critère")


@pytest.mark.parametrize(
    ("avant", "apres"),
    [("du_jour: 25", "du_jour: 0"), ("au_jour_mois_suivant: 5", "au_jour_mois_suivant: 32")],
)
def test_fenetre_salaire_bornee(tmp_path: Path, avant: str, apres: str) -> None:
    (erreur,) = _erreurs(_variante(tmp_path, avant, apres))
    assert erreur.startswith("fenetre_salaire.")


def test_plusieurs_erreurs_listees(tmp_path: Path) -> None:
    texte = TEXTE_EXEMPLE.replace("abonnements.energie}", "abonnements.gaz}").replace(
        "version: 1\n", "version: 1\ninconnue: 3\n"
    )
    erreurs = _erreurs(_ecrire(tmp_path, texte))
    assert len(erreurs) == 2


def test_fichier_absent(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        charger_contexte(tmp_path / "absent.yaml")


def test_yaml_illisible(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML illisible"):
        charger_contexte(_ecrire(tmp_path, "version: [1\n"))


def test_racine_non_dictionnaire(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dictionnaire"):
        charger_contexte(_ecrire(tmp_path, "- 1\n"))


def _operation(**valeurs: object) -> OperationBrute:
    base: dict[str, object] = {
        "source": Source.CA_CB,
        "compte": Compte.JOINT,
        "fichier": "CA_CB_202606.csv",
        "position": 1,
        "date_operation": date(2026, 6, 17),
        "date_debit": date(2026, 6, 30),
        "libelle": "MILIBOO CHAVANOD",
        "libelle_brut": "MILIBOO CHAVANOD",
        "carte": "X1091",
        "montant": Decimal("-258.99"),
    }
    return OperationBrute.model_validate(base | valeurs)


def test_operation_brute_valide_et_immuable() -> None:
    operation = _operation()
    assert operation.montant == Decimal("-258.99")
    with pytest.raises(ValidationError):
        operation.montant = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize(
    "valeurs",
    [
        {"montant": Decimal("0")},
        {"montant": -258.99},
        {"carte": "1091"},
        {"position": 0},
        {"inconnu": "x"},
    ],
)
def test_operation_brute_refusee(valeurs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _operation(**valeurs)
