"""Agrégats d'un mois sur des cas synthétiques (RG-09, RG-10, RG-19, moyenne, budgets) et
contrôles bloquants (spec 08)."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.agregats import Agregats, calculer_agregats, controler_mois
from budget.agregats.modeles import LigneCategorie
from budget.categories import Categorie, SousCategorie
from budget.classement import Classement, OrigineClassement
from budget.config import Contexte, charger_contexte
from budget.errors import ConfigError, ControleError
from budget.models import Source, Transaction
from tests.conftest import fabriquer_transaction

SC = SousCategorie
CONTEXTE = Contexte.model_validate(
    {"version": 2, "seuils": {"amazon_auto": 50}, "budgets": {"joint": {"courses": 600}}}
)

Mois = tuple[list[Transaction], list[Classement]]


def _operation(
    mois: Mois,
    montant: str,
    sous_categorie: SousCategorie | None = None,
    *,
    non_comptee: bool = False,
    jour: date = date(2026, 6, 15),
    source: Source = Source.CA_JOINT,
    libelle: str = "PRLV SEPA FOURNISSEUR",
) -> Transaction:
    """Ajoute au mois une opération classée ; ni sous-catégorie ni non compté : en attente."""
    t = fabriquer_transaction(libelle, montant, source=source, date_operation=jour)
    origine = (
        OrigineClassement.DECISION
        if sous_categorie is not None or non_comptee
        else OrigineClassement.AUCUNE
    )
    mois[0].append(t)
    mois[1].append(
        Classement(
            transaction_id=t.id,
            sous_categorie=sous_categorie,
            non_comptee=non_comptee,
            origine=origine,
        )
    )
    return t


def _calculer(mois: str, donnees: dict[str, Mois], contexte: Contexte = CONTEXTE) -> Agregats:
    return calculer_agregats(
        mois,
        {m: t for m, (t, _) in donnees.items()},
        {m: c for m, (_, c) in donnees.items()},
        contexte,
    )


def _ligne(agregats: Agregats, categorie: Categorie, vue: str = "joint") -> LigneCategorie:
    return next(
        ligne for ligne in getattr(agregats, vue).categories if ligne.categorie is categorie
    )


def test_avoir_carte_deduit_de_sa_categorie_rg09() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-100.00", SC.ACHATS_VETEMENTS, source=Source.CA_CB, libelle="ZALANDO")
    _operation(juin, "30.00", SC.ACHATS_VETEMENTS, source=Source.CA_CB, libelle="ZALANDO")
    achats = _ligne(_calculer("2026-06", {"2026-06": juin}), Categorie.ACHATS)
    assert (achats.montant, achats.nb_operations) == (Decimal("70.00"), 2)
    assert [(s.sous_categorie, s.montant) for s in achats.sous_categories] == [
        (SC.ACHATS_VETEMENTS, Decimal("70.00"))
    ]


def test_non_comptees_exclues_des_totaux_rg10() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-42.00", SC.SANTE_PHARMACIE)
    _operation(juin, "-500.00", non_comptee=True)
    _operation(juin, "-11.00", non_comptee=True, source=Source.TR)
    agregats = _calculer("2026-06", {"2026-06": juin})
    assert agregats.joint.total == Decimal("42.00")
    assert (agregats.joint.non_comptees.nombre, agregats.joint.non_comptees.montant) == (
        1,
        Decimal("500.00"),
    )
    assert agregats.perso.total == Decimal("0.00") and agregats.perso.categories == ()
    assert agregats.perso.non_comptees.montant == Decimal("11.00")


def test_credits_ignores_absents_des_totaux_rg19() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-42.00", SC.SANTE_PHARMACIE)
    # Crédit hors carte : aucun classement, il n'est pas suivi
    juin[0].append(fabriquer_transaction("VIREMENT RECU", "2000.00"))
    agregats = _calculer("2026-06", {"2026-06": juin})
    assert agregats.joint.total == Decimal("42.00")
    assert agregats.joint.non_comptees.nombre == 0
    assert agregats.historique[0].complet
    assert [g.montant for g in agregats.joint.plus_grosses] == [Decimal("42.00")]


def test_plus_grosses_depenses_sans_libelle_de_virement() -> None:
    juin: Mois = ([], [])
    for montant in ("-10.00", "-60.00", "-20.00", "-50.00", "-40.00", "-30.00"):
        _operation(juin, montant, SC.DIVERS_AUTRE)
    _operation(juin, "-25.00", SC.COURSES_SUPERMARCHE, source=Source.CA_CB, libelle="MONOPRIX 123")
    grosses = _calculer("2026-06", {"2026-06": juin}).joint.plus_grosses
    assert [g.montant for g in grosses] == [Decimal(m) for m in ("60", "50", "40", "30", "25")]
    assert [g.marchand for g in grosses] == [None, None, None, None, "MONOPRIX"]


def _mois_courses(mois: str, montant: str | None, *, en_attente: bool = False) -> Mois:
    donnees: Mois = ([], [])
    jour = date(int(mois[:4]), int(mois[5:]), 10)
    _operation(donnees, "-1.00", SC.SANTE_PHARMACIE, jour=jour)  # le mois existe toujours
    if montant is not None:
        _operation(donnees, montant, SC.COURSES_SUPERMARCHE, jour=jour)
    if en_attente:
        _operation(donnees, "-5.00", jour=jour)
    return donnees


def test_moyenne_sans_mois_precedent() -> None:
    agregats = _calculer("2026-06", {"2026-06": _mois_courses("2026-06", "-10.00")})
    courses = _ligne(agregats, Categorie.COURSES)
    assert (courses.moyenne_3_mois, courses.mois_moyenne, courses.ecart_moyenne) == (None, 0, None)


def test_moyenne_sur_un_mois() -> None:
    donnees = {
        "2026-05": _mois_courses("2026-05", "-100.00"),
        "2026-06": _mois_courses("2026-06", "-130.00"),
    }
    courses = _ligne(_calculer("2026-06", donnees), Categorie.COURSES)
    assert (courses.moyenne_3_mois, courses.mois_moyenne) == (Decimal("100.00"), 1)
    assert courses.ecart_moyenne == Decimal("30.00")


def test_moyenne_sur_trois_mois_categorie_absente_comptee_pour_zero() -> None:
    donnees = {
        "2026-02": _mois_courses("2026-02", "-999.00"),  # M−4 : hors de la fenêtre
        "2026-03": _mois_courses("2026-03", "-0.01"),
        "2026-04": _mois_courses("2026-04", None),  # aucune course : compte pour 0
        "2026-05": _mois_courses("2026-05", "-10.00"),
        "2026-06": _mois_courses("2026-06", "-5.00"),
    }
    courses = _ligne(_calculer("2026-06", donnees), Categorie.COURSES)
    # 10,01 / 3 = 3,3366… arrondi au centime
    assert (courses.moyenne_3_mois, courses.mois_moyenne) == (Decimal("3.34"), 3)
    assert courses.ecart_moyenne == Decimal("1.66")


def test_moyenne_exclut_le_mois_en_attente_et_arrondit_au_centime_superieur() -> None:
    donnees = {
        "2026-03": _mois_courses("2026-03", "-0.01"),
        "2026-04": _mois_courses("2026-04", "-500.00", en_attente=True),  # incomplet : exclu
        "2026-05": _mois_courses("2026-05", None),
        "2026-06": _mois_courses("2026-06", "-1.00"),
    }
    agregats = _calculer("2026-06", donnees)
    courses = _ligne(agregats, Categorie.COURSES)
    # 0,01 / 2 = 0,005 : ROUND_HALF_UP
    assert (courses.moyenne_3_mois, courses.mois_moyenne) == (Decimal("0.01"), 2)
    assert [(p.mois, p.complet) for p in agregats.historique] == [
        ("2026-03", True),
        ("2026-04", False),
        ("2026-05", True),
        ("2026-06", True),
    ]


def test_budgets_avec_sans_et_sans_depense() -> None:
    contexte = Contexte.model_validate(
        {
            "version": 2,
            "seuils": {"amazon_auto": 50},
            "budgets": {"joint": {"courses": 600, "restos": "150.5"}, "perso": {"restos": 200}},
        }
    )
    juin: Mois = ([], [])
    _operation(juin, "-627.36", SC.COURSES_SUPERMARCHE)
    _operation(juin, "-12.00", SC.SANTE_PHARMACIE)
    agregats = _calculer("2026-06", {"2026-06": juin}, contexte)
    lignes = {ligne.categorie: ligne for ligne in agregats.joint.categories}
    assert list(lignes) == [Categorie.COURSES, Categorie.RESTOS, Categorie.SANTE]
    courses, restos, sante = lignes.values()
    assert (courses.budget, courses.ecart_budget) == (Decimal("600.00"), Decimal("27.36"))
    assert (restos.montant, restos.nb_operations) == (Decimal("0.00"), 0)
    assert (restos.budget, restos.ecart_budget) == (Decimal("150.50"), Decimal("-150.50"))
    assert (sante.budget, sante.ecart_budget) == (None, None)
    assert agregats.joint.total == Decimal("639.36")
    assert [ligne.categorie for ligne in agregats.perso.categories] == [Categorie.RESTOS]


@pytest.mark.parametrize(
    ("budgets", "message"),
    [
        ("joint: {voyages: 100}", r"budgets\.joint\.voyages \(clé\) : valeur 'voyages' invalide"),
        ("perso: {restos: -5}", r"budgets\.perso\.restos : valeur .* supérieure ou égale à 0"),
        ("commun: {restos: 5}", r"budgets\.commun : clé inconnue"),
    ],
)
def test_budget_invalide_refuse(tmp_path: Path, budgets: str, message: str) -> None:
    chemin = tmp_path / "contexte.yaml"
    chemin.write_text(
        f"version: 2\nseuils: {{amazon_auto: 50}}\nbudgets:\n  {budgets}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as erreur:
        charger_contexte(chemin)
    assert any(re.search(message, e) for e in erreur.value.erreurs), erreur.value.erreurs


def _controler(mois: str, donnees: dict[str, Mois], agregats: Agregats | None) -> None:
    transactions = {m: t for m, (t, _) in donnees.items()}
    controler_mois(mois, transactions, {m: c for m, (_, c) in donnees.items()}, agregats)


def test_controle_mois_non_importe() -> None:
    with pytest.raises(ControleError, match=r"non importé : lancer `budget import --mois 2026-07`"):
        _controler("2026-07", {"2026-06": _mois_courses("2026-06", "-1.00")}, None)


def test_controle_operations_en_attente_sans_libelle() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-12.00", SC.SANTE_PHARMACIE)
    attente = [
        _operation(juin, f"-{n}.00", libelle=f"VIREMENT VERS PERSONNE {n}") for n in range(1, 8)
    ]
    juin[0].append(fabriquer_transaction("PAIEMENT CARTE NON CLASSE", "-3.00", source=Source.CA_CB))
    with pytest.raises(ControleError) as erreur:
        _controler("2026-06", {"2026-06": juin}, _calculer("2026-06", {"2026-06": juin}))
    message = str(erreur.value)
    assert "8 opération(s) en attente" in message
    premiere = attente[0]
    assert f"{premiere.fichier}, position {premiere.position}, du 15/06/2026, -1,00 €" in message
    assert "… et 3 autre(s)" in message
    assert "`budget classify --mois 2026-06`" in message
    assert "VIREMENT" not in message and "PAIEMENT" not in message


def test_controle_coherence_des_agregats() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-10.00", SC.COURSES_SUPERMARCHE)
    _operation(juin, "-5.00", SC.COURSES_COMMERCES)
    _operation(juin, "-12.00", SC.SANTE_PHARMACIE)
    agregats = _calculer("2026-06", {"2026-06": juin})
    _controler("2026-06", {"2026-06": juin}, agregats)  # cohérent : aucune erreur

    courses = agregats.joint.categories[0]
    faussee = agregats.model_copy(
        update={
            "joint": agregats.joint.model_copy(
                update={
                    "total": Decimal("27.01"),
                    "categories": (
                        courses.model_copy(update={"sous_categories": courses.sous_categories[:1]}),
                        *agregats.joint.categories[1:],
                    ),
                }
            )
        }
    )
    with pytest.raises(ControleError) as erreur:
        _controler("2026-06", {"2026-06": juin}, faussee)
    message = str(erreur.value)
    assert "vue joint (3 opération(s)) : somme des catégories 27,00 €, total 27,01 €" in message
    assert "vue joint, Courses (2 opération(s)) : somme des sous-catégories 10,00 €" in message
    assert "corriger le code, sans tolérance" in message


def test_aucun_float_dans_les_agregats() -> None:
    juin: Mois = ([], [])
    _operation(juin, "-10.00", SC.COURSES_SUPERMARCHE)
    _operation(juin, "3.50", SC.COURSES_SUPERMARCHE, source=Source.CA_CB, libelle="MONOPRIX")
    mai = _mois_courses("2026-05", "-7.00")
    assert_sans_float(_calculer("2026-06", {"2026-05": mai, "2026-06": juin}).model_dump())


def assert_sans_float(valeur: object) -> None:
    """Parcourt récursivement un objet : aucun `float` nulle part."""
    assert not isinstance(valeur, float), valeur
    if isinstance(valeur, dict):
        for element in valeur.values():
            assert_sans_float(element)
    elif isinstance(valeur, (list, tuple)):
        for element in valeur:
            assert_sans_float(element)
