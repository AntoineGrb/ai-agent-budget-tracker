"""Moteur de règles apprises : modèle, correspondance, priorité, conflits, RG-13, RG-14."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from budget.categories import SousCategorie
from budget.errors import ControleError, RegleError
from budget.models import Source, Transaction
from budget.regles import (
    ICS_PAYPAL,
    Critere,
    Regle,
    TypeRegle,
    est_rg14,
    regle_marchand_retenue,
    regle_retenue,
    toujours_demander,
    verifier_nouvelle_regle,
)
from tests.conftest import fabriquer_transaction

LIBELLE = "SEPA DIRECT DEBIT TRANSFER TO ORANGE SA ( )"
INTERNET = SousCategorie.ABONNEMENTS_INTERNET
AUTRE = SousCategorie.ABONNEMENTS_AUTRE


def _regle(
    critere: Critere = Critere.LIBELLE_EXACT,
    valeur: str = LIBELLE,
    montants: tuple[str, str] | None = None,
    *,
    id: int | None = None,
    type: TypeRegle = TypeRegle.PRELEVEMENT,
    source: Source = Source.CA_JOINT,
    sous_categorie: SousCategorie | None = INTERNET,
    non_comptee: bool = False,
) -> Regle:
    return Regle(
        id=id,
        type=type,
        source=source,
        critere=critere,
        valeur=valeur,
        montant_min=Decimal(montants[0]) if montants else None,
        montant_max=Decimal(montants[1]) if montants else None,
        sous_categorie=sous_categorie,
        non_comptee=non_comptee,
    )


# Modèle ----------------------------------------------------------------------------------------


def test_virement_sans_condition_de_montant_refuse() -> None:
    with pytest.raises(ValidationError, match="obligatoirement une condition de montant"):
        _regle(type=TypeRegle.VIREMENT)


@pytest.mark.parametrize(
    ("montant_min", "montant_max"), [(Decimal("-10"), None), (Decimal("-5"), Decimal("-10"))]
)
def test_condition_de_montant_incoherente(
    montant_min: Decimal, montant_max: Decimal | None
) -> None:
    with pytest.raises(ValidationError):
        Regle(
            type=TypeRegle.PRELEVEMENT,
            source=Source.CA_JOINT,
            critere=Critere.ICS,
            valeur="FR00ZZZ000000",
            montant_min=montant_min,
            montant_max=montant_max,
            non_comptee=True,
        )


def test_imputation_obligatoire_et_exclusive_rg10() -> None:
    with pytest.raises(ValidationError, match="sous-catégorie ou « non compté »"):
        _regle(sous_categorie=None)
    with pytest.raises(ValidationError, match="non comptée n'a pas de sous-catégorie"):
        _regle(non_comptee=True)
    with pytest.raises(ValidationError, match="ne porte aucune imputation"):
        _regle(type=TypeRegle.TOUJOURS_DEMANDER)
    assert _regle(sous_categorie=None, non_comptee=True).libelle_resultat() == "Non compté"
    demander = _regle(type=TypeRegle.TOUJOURS_DEMANDER, sous_categorie=None)
    assert demander.libelle_resultat() == "Toujours demander"


# Correspondance --------------------------------------------------------------------------------


def test_correspondance_par_critere() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99", ics="FR00ZZZ000000")
    assert _regle().s_applique(t)
    assert _regle(Critere.LIBELLE_PREFIXE, "SEPA DIRECT DEBIT TRANSFER TO ORANGE").s_applique(t)
    assert _regle(Critere.ICS, "FR00ZZZ000000").s_applique(t)
    assert not _regle(Critere.LIBELLE_EXACT, "SEPA DIRECT DEBIT").s_applique(t)
    assert not _regle(source=Source.TR).s_applique(t)
    assert not _regle().model_copy(update={"active": False}).s_applique(t)


def test_condition_de_montant_signee() -> None:
    """Une règle sur une sortie ne s'applique pas à une entrée."""
    regle = _regle(montants=("-20", "-10"))
    assert regle.s_applique(fabriquer_transaction(LIBELLE, "-11.99"))
    assert regle.s_applique(fabriquer_transaction(LIBELLE, "-20.00"))
    assert not regle.s_applique(fabriquer_transaction(LIBELLE, "11.99"))
    assert not regle.s_applique(fabriquer_transaction(LIBELLE, "-20.01"))


# Priorité --------------------------------------------------------------------------------------


def test_priorite_ics_puis_libelle_exact_puis_prefixe() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99", ics="FR00ZZZ000000")
    prefixe = _regle(Critere.LIBELLE_PREFIXE, "SEPA", sous_categorie=AUTRE)
    exact = _regle(sous_categorie=None, non_comptee=True)
    ics = _regle(Critere.ICS, "FR00ZZZ000000")
    assert regle_retenue([prefixe, exact, ics], t) is ics
    assert regle_retenue([prefixe, exact], t) is exact


def test_priorite_condition_de_montant() -> None:
    """Montant exact > plage > aucune condition."""
    t = fabriquer_transaction(LIBELLE, "-11.99")
    aucune = _regle(sous_categorie=AUTRE)
    plage = _regle(montants=("-20", "-10"), sous_categorie=None, non_comptee=True)
    exact = _regle(montants=("-11.99", "-11.99"))
    assert regle_retenue([aucune, plage, exact], t) is exact
    assert regle_retenue([aucune, plage], t) is plage


def test_priorite_prefixe_long() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    court = _regle(Critere.LIBELLE_PREFIXE, "SEPA", sous_categorie=AUTRE)
    long = _regle(Critere.LIBELLE_PREFIXE, "SEPA DIRECT DEBIT")
    assert regle_retenue([court, long], t) is long


def test_regles_contradictoires_en_base_arretent_le_classement() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    premiere = _regle(id=1)
    seconde = _regle(id=2, sous_categorie=None, non_comptee=True)
    with pytest.raises(ControleError, match="règles n° 1, n° 2 : même spécificité"):
        regle_retenue([premiere, seconde], t)


def test_toujours_demander_l_emporte() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    demander = _regle(
        Critere.LIBELLE_PREFIXE, "SEPA", type=TypeRegle.TOUJOURS_DEMANDER, sous_categorie=None
    )
    exact = _regle(montants=("-11.99", "-11.99"))
    assert regle_retenue([exact, demander], t) is None
    assert toujours_demander([demander], t)


# RG-14 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_operation", "ics", "attendu"),
    [
        ("Prélèvement", ICS_PAYPAL, True),
        ("Chèque emis", None, True),
        ("Prélèvement", "FR00ZZZ000000", False),
    ],
)
def test_rg14_paypal_et_cheques(type_operation: str, ics: str | None, attendu: bool) -> None:
    t = fabriquer_transaction("PAYPAL", "-25.00", type_operation=type_operation, ics=ics)
    assert est_rg14(t) is attendu


def test_rg14_ignore_les_regles_existantes() -> None:
    """RG-14 : PayPal est toujours posé, même si une règle correspondante existe."""
    t = fabriquer_transaction("PAYPAL", "-25.00", type_operation="Prélèvement", ics=ICS_PAYPAL)
    regle = _regle(Critere.ICS, ICS_PAYPAL)
    assert regle.s_applique(t)
    assert regle_retenue([regle], t) is None
    assert toujours_demander([], t)


# Conflits --------------------------------------------------------------------------------------


def test_conflit_refuse() -> None:
    existante = _regle(montants=("-20", "-10"), id=4)
    with pytest.raises(RegleError, match="conflit avec la règle n° 4"):
        verifier_nouvelle_regle(_regle(montants=("-15", "-5"), sous_categorie=AUTRE), [existante])


def test_conflit_avec_une_regle_sans_condition() -> None:
    existante = _regle(montants=("-20", "-10"), id=4)
    verifier_nouvelle_regle(_regle(montants=("-90", "-80"), sous_categorie=AUTRE), [existante])
    sans_condition = _regle(id=5)
    verifier_nouvelle_regle(_regle(sous_categorie=AUTRE, montants=("-1", "-1")), [sans_condition])
    with pytest.raises(RegleError, match="conflit"):
        verifier_nouvelle_regle(_regle(sous_categorie=AUTRE), [sans_condition])


def test_doublon_refuse() -> None:
    with pytest.raises(RegleError, match="déjà couverte par la règle n° 4"):
        verifier_nouvelle_regle(_regle(), [_regle(id=4)])


def test_specificites_differentes_acceptees() -> None:
    """Un montant exact dans une plage existante est plus spécifique : pas de conflit."""
    existante = _regle(montants=("-600", "-500"), id=1, type=TypeRegle.VIREMENT)
    exacte = _regle(
        montants=("-550", "-550"), type=TypeRegle.VIREMENT, sous_categorie=None, non_comptee=True
    )
    verifier_nouvelle_regle(exacte, [existante])
    verifier_nouvelle_regle(exacte, [existante.model_copy(update={"active": False})])


# Règles marchand carte -------------------------------------------------------------------------


def _marchand(
    valeur: str = "MONOPRIX",
    critere: Critere = Critere.LIBELLE_EXACT,
    sous_categorie: SousCategorie | None = SousCategorie.COURSES_SUPERMARCHE,
    *,
    id: int | None = None,
    source: Source = Source.CA_CB,
) -> Regle:
    return Regle(
        id=id,
        type=TypeRegle.MARCHAND_CARTE,
        source=source,
        critere=critere,
        valeur=valeur,
        sous_categorie=sous_categorie,
    )


def _carte(libelle: str, montant: str = "-12.00", source: Source = Source.CA_CB) -> Transaction:
    type_operation = "Avoir" if source is Source.TR else None
    return fabriquer_transaction(libelle, montant, source=source, type_operation=type_operation)


@pytest.mark.parametrize(
    ("champs", "message"),
    [
        ({"source": Source.CA_JOINT}, "sources ca_cb ou tr"),
        ({"critere": Critere.ICS}, "ne porte pas sur un ICS"),
        ({"sous_categorie": None}, "sous-catégorie ou « non compté »"),
        ({"valeur": "AMAZON PAYMENTS"}, "RG-13"),
        ({"valeur": "AMZN MKTP"}, "RG-13"),
    ],
)
def test_marchand_validation(champs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _marchand(**champs)  # type: ignore[arg-type]


def test_marchand_correspondance_sur_la_cle() -> None:
    """La valeur est comparée à la clé marchand, pas au libellé complet ; avoirs compris (RG-09)."""
    regle = _marchand()
    assert regle.s_applique(_carte("MONOPRIX 1357 STMAU2042885/"))
    assert regle.s_applique(_carte("MONOPRIX 9999 AUTRE/", "51.00"))
    assert not regle.s_applique(_carte("MONOPRIX CITY 1357"))
    assert not regle.s_applique(_carte("MONOPRIX 1357", source=Source.TR))
    assert _marchand("MONOP", Critere.LIBELLE_PREFIXE).s_applique(_carte("MONOPRIX CITY 1357"))


def test_marchand_priorite_et_separation_des_moteurs() -> None:
    t = _carte("MONOPRIX 1357 STMAU2042885/")
    prefixe = _marchand("MONO", Critere.LIBELLE_PREFIXE, SousCategorie.ACHATS_AUTRE)
    exacte = _marchand()
    long = _marchand("MONOPRI", Critere.LIBELLE_PREFIXE, SousCategorie.COURSES_COMMERCES)
    assert regle_marchand_retenue([prefixe, exacte, long], t) is exacte
    assert regle_marchand_retenue([prefixe, long], t) is long
    assert regle_retenue([exacte], t) is None  # une règle marchand ne vaut que pour les cartes


def test_marchand_conflit_et_doublon() -> None:
    existante = _marchand(id=7)
    with pytest.raises(RegleError, match="déjà couverte par la règle n° 7"):
        verifier_nouvelle_regle(_marchand(), [existante])
    with pytest.raises(RegleError, match="conflit avec la règle n° 7"):
        verifier_nouvelle_regle(_marchand(sous_categorie=SousCategorie.ACHATS_AUTRE), [existante])
    verifier_nouvelle_regle(_marchand(source=Source.TR), [existante])  # autre source
    assert "Courses › Supermarché" in existante.decrire()
