"""Règles apprises : modèle, correspondance, priorité, conflits, RG-14 (spec 06)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from budget.categories import SousCategorie
from budget.errors import ControleError, RegleError
from budget.models import Nature, Source
from budget.regles import (
    ICS_PAYPAL,
    Critere,
    Regle,
    TypeRegle,
    est_rg14,
    regle_retenue,
    toujours_demander,
    verifier_nouvelle_regle,
)
from tests.conftest import fabriquer_transaction

LIBELLE = "SEPA DIRECT DEBIT TRANSFER TO ORANGE SA ( )"


def _regle(
    critere: Critere = Critere.LIBELLE_EXACT,
    valeur: str = LIBELLE,
    montants: tuple[str, str] | None = None,
    *,
    id: int | None = None,
    type: TypeRegle = TypeRegle.PRELEVEMENT,
    source: Source = Source.CA_JOINT,
    nature: Nature | None = Nature.HORS_BUDGET,
    sous_categorie: SousCategorie | None = None,
) -> Regle:
    return Regle(
        id=id,
        type=type,
        source=source,
        critere=critere,
        valeur=valeur,
        montant_min=Decimal(montants[0]) if montants else None,
        montant_max=Decimal(montants[1]) if montants else None,
        nature=nature,
        sous_categorie=sous_categorie,
    )


# Modèle ----------------------------------------------------------------------------------------


def test_virement_sans_condition_de_montant_refuse() -> None:
    with pytest.raises(ValidationError, match="obligatoirement une condition de montant"):
        _regle(type=TypeRegle.VIREMENT)


def test_lien_depense_jamais_une_regle() -> None:
    """RG-07 : un lien à une dépense est un cas unique."""
    with pytest.raises(ValidationError, match="jamais une règle"):
        _regle(nature=Nature.LIEN_DEPENSE)


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
            nature=Nature.HORS_BUDGET,
        )


def test_nature_et_sous_categorie() -> None:
    with pytest.raises(ValidationError, match="nature est obligatoire"):
        _regle(nature=None)
    with pytest.raises(ValidationError, match="sous-catégorie est obligatoire"):
        _regle(nature=Nature.DEPENSE)
    with pytest.raises(ValidationError, match="ne porte aucun résultat"):
        _regle(type=TypeRegle.TOUJOURS_DEMANDER)
    assert _regle(type=TypeRegle.TOUJOURS_DEMANDER, nature=None).nature is None


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
    prefixe = _regle(Critere.LIBELLE_PREFIXE, "SEPA", nature=Nature.COMPLEMENT)
    exact = _regle(nature=Nature.EPARGNE)
    ics = _regle(Critere.ICS, "FR00ZZZ000000", nature=Nature.REVENU)
    assert regle_retenue([prefixe, exact, ics], t) is ics
    assert regle_retenue([prefixe, exact], t) is exact


def test_priorite_condition_de_montant() -> None:
    """Montant exact > plage > aucune condition."""
    t = fabriquer_transaction(LIBELLE, "-11.99")
    aucune = _regle(nature=Nature.COMPLEMENT)
    plage = _regle(montants=("-20", "-10"), nature=Nature.EPARGNE)
    exact = _regle(montants=("-11.99", "-11.99"), nature=Nature.REVENU)
    assert regle_retenue([aucune, plage, exact], t) is exact
    assert regle_retenue([aucune, plage], t) is plage


def test_priorite_prefixe_long() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    court = _regle(Critere.LIBELLE_PREFIXE, "SEPA", nature=Nature.COMPLEMENT)
    long = _regle(Critere.LIBELLE_PREFIXE, "SEPA DIRECT DEBIT", nature=Nature.EPARGNE)
    assert regle_retenue([court, long], t) is long


def test_regles_contradictoires_en_base_arretent_le_classement() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    premiere = _regle(id=1, nature=Nature.COMPLEMENT)
    seconde = _regle(id=2, nature=Nature.EPARGNE)
    with pytest.raises(ControleError, match="règles n° 1, n° 2 : même spécificité"):
        regle_retenue([premiere, seconde], t)


def test_toujours_demander_l_emporte() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    demander = _regle(
        Critere.LIBELLE_PREFIXE, "SEPA", type=TypeRegle.TOUJOURS_DEMANDER, nature=None
    )
    exact = _regle(montants=("-11.99", "-11.99"))
    assert regle_retenue([exact, demander], t) is None
    assert toujours_demander([demander], t)


def test_regle_marchand_carte_ignoree_avant_l_etape_7() -> None:
    t = fabriquer_transaction(LIBELLE, "-11.99")
    marchand = _regle(
        type=TypeRegle.MARCHAND_CARTE,
        nature=Nature.DEPENSE,
        sous_categorie=SousCategorie.ACHATS_DIVERS,
    )
    assert regle_retenue([marchand], t) is None


# RG-14 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_operation", "ics", "attendu"),
    [
        ("Prélèvement", ICS_PAYPAL, True),
        ("Chèque emis", None, True),
        ("Remise de chèque", None, True),
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
    existante = _regle(montants=("-20", "-10"), id=4, nature=Nature.COMPLEMENT)
    with pytest.raises(RegleError, match="conflit avec la règle n° 4"):
        verifier_nouvelle_regle(_regle(montants=("-15", "-5"), nature=Nature.EPARGNE), [existante])


def test_conflit_avec_une_regle_sans_condition() -> None:
    existante = _regle(montants=("-20", "-10"), id=4, nature=Nature.COMPLEMENT)
    nouvelle = _regle(montants=("-90", "-80"), nature=Nature.EPARGNE)
    verifier_nouvelle_regle(nouvelle, [existante])  # plages disjointes : acceptée
    sans_condition = _regle(id=5, nature=Nature.COMPLEMENT)
    verifier_nouvelle_regle(_regle(nature=Nature.EPARGNE, montants=("-1", "-1")), [sans_condition])
    with pytest.raises(RegleError, match="conflit"):
        verifier_nouvelle_regle(_regle(nature=Nature.EPARGNE), [sans_condition])


def test_doublon_refuse() -> None:
    existante = _regle(id=4)
    with pytest.raises(RegleError, match="déjà couverte par la règle n° 4"):
        verifier_nouvelle_regle(_regle(), [existante])


def test_plages_disjointes_acceptees() -> None:
    existante = _regle(montants=("-600", "-500"), id=1, type=TypeRegle.VIREMENT)
    autre = _regle(montants=("-499.99", "-400"), type=TypeRegle.VIREMENT, nature=Nature.COMPLEMENT)
    verifier_nouvelle_regle(autre, [existante])


def test_specificites_differentes_acceptees() -> None:
    """Un montant exact dans une plage existante est plus spécifique : pas de conflit."""
    existante = _regle(montants=("-600", "-500"), id=1, type=TypeRegle.VIREMENT)
    exacte = _regle(montants=("-550", "-550"), type=TypeRegle.VIREMENT, nature=Nature.DOTATION)
    verifier_nouvelle_regle(exacte, [existante])
    verifier_nouvelle_regle(exacte, [existante.model_copy(update={"active": False})])
