"""Fonctions pures du parser Trade Republic, sur des mots positionnés synthétiques (spec 03)."""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from budget.errors import ParseError
from budget.parsers.trade_republic import (
    Bande,
    Colonnes,
    Mot,
    Page,
    affecter_montants,
    decouper_bandes,
    lire_lignes_page,
    normaliser_montant,
    reperer_colonnes,
)

FICHIER = "TR_TEST.pdf"


def mot(texte: str, x0: float, top: float, largeur: float | None = None) -> Mot:
    return Mot(text=texte, x0=x0, x1=x0 + (largeur or 5.0 * len(texte)), top=top)


def entete(top: float = 346.0, dx: float = 0.0) -> list[Mot]:
    """En-tête du tableau aux positions mesurées sur juin, décalé de `dx` en abscisse."""
    return [
        mot("DATE", 74.4 + dx, top, 13.8),
        mot("TYPE", 102.4 + dx, top, 13.2),
        mot("DESCRIPTION", 151.2 + dx, top, 35.2),
        mot("ENTRÉE", 420.1 + dx, top - 2.9, 19.6),
        mot("SORTIE", 453.1 + dx, top - 2.9, 18.7),
        mot("SOLDE", 502.1 + dx, top, 17.2),
        mot("D'ARGENT", 420.1 + dx, top + 3.0, 26.5),
        mot("D'ARGENT", 453.1 + dx, top + 3.0, 26.5),
    ]


def transaction(
    top: float,
    jour: str,
    type_operation: str,
    description: str,
    mouvement: str,
    solde: str,
    *,
    entree: bool = False,
    dx: float = 0.0,
) -> list[Mot]:
    """Une transaction comme dans le PDF : jour et mois, puis type, montants, puis année."""
    milieu, bas = top + 3.8, top + 7.6
    x_mouvement = (420.1 if entree else 453.1) + dx
    mots = [mot(jour, 74.4 + dx, top, 8.6), mot("juin", 84.7 + dx, top, 11.7)]
    mots += [mot(t, 102.4 + dx + 20 * i, milieu) for i, t in enumerate(type_operation.split())]
    mots += [mot(t, 151.2 + dx + 40 * i, milieu, 35) for i, t in enumerate(description.split())]
    mots += [
        mot(mouvement, x_mouvement, milieu, 19.2),
        mot("€", x_mouvement + 21, milieu, 4.3),
        mot(solde, 513.4 + dx - 5.6 * len(solde), milieu, 5.6 * len(solde)),
        mot("€", 515.0 + dx, milieu, 4.3),
        mot("2026", 74.4 + dx, bas, 17.2),
    ]
    return mots


def page_synthetique(*mots: list[Mot], numero: int = 2) -> Page:
    return Page(numero=numero, hauteur=841.88, mots=tuple(m for groupe in mots for m in groupe))


COLONNES = Colonnes(
    top_entete=349.0,
    x0_type=102.4,
    x0_description=151.2,
    x0_entree=420.1,
    x0_sortie=453.1,
    x1_solde=519.3,
)


def bande(*montants: Mot) -> Bande:
    return Bande(
        page=2,
        rang=3,
        jour="08",
        mois="juin",
        annees=("2026",),
        type_operation=("Avoir",),
        description=("DECATHLON", "0008"),
        montants=montants,
    )


# Normalisation des montants ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("1983,76", Decimal("1983.76")),
        ("€1238,73", Decimal("1238.73")),
        ("8,54€", Decimal("8.54")),
        ("-15,20", Decimal("-15.20")),
        ("€-15,20€", Decimal("-15.20")),
        ("0,00", Decimal("0.00")),
    ],
)
def test_normaliser_montant(texte: str, attendu: Decimal) -> None:
    assert normaliser_montant(texte) == attendu


@pytest.mark.parametrize(
    "texte", ["€", "2026", "2466078", "0.032138", "8,5", "1.983,76", "DL-,001,", "quantity:"]
)
def test_normaliser_montant_refuse_les_non_montants(texte: str) -> None:
    assert normaliser_montant(texte) is None


# Repérage des colonnes ------------------------------------------------------------------------


def test_reperer_colonnes_depuis_les_entetes() -> None:
    colonnes = reperer_colonnes(page_synthetique(entete()), FICHIER)
    assert colonnes is not None
    assert dataclasses.astuple(colonnes) == pytest.approx(dataclasses.astuple(COLONNES))


def test_page_sans_tableau() -> None:
    page = page_synthetique([mot("REMARQUES", 73.7, 146.9)])
    assert reperer_colonnes(page, FICHIER) is None
    assert lire_lignes_page(page, FICHIER) == []


def test_entete_manquant() -> None:
    sans_sortie = [m for m in entete() if m["text"] != "SORTIE"]
    with pytest.raises(ParseError, match=r"page 2 : en-tête de colonne SORTIE : 1 attendu"):
        reperer_colonnes(page_synthetique(sans_sortie), FICHIER)


# Découpage en bandes et lecture des lignes -----------------------------------------------------


def test_lignes_sur_deux_niveaux_et_sens_par_colonne() -> None:
    page = page_synthetique(
        entete(),
        transaction(364.1, "02", "Exécution d'ordre", "Savings plan quantity:", "10,00", "1875,37"),
        transaction(395.8, "11", "Avoir", "Zalando Payments", "51,00", "1926,37", entree=True),
    )
    lignes = lire_lignes_page(page, FICHIER)
    assert [
        (ligne.rang, ligne.date_operation, ligne.type_operation, ligne.description)
        for ligne in lignes
    ] == [
        (1, date(2026, 6, 2), "Exécution d'ordre", "Savings plan quantity:"),
        (2, date(2026, 6, 11), "Avoir", "Zalando Payments"),
    ]
    assert [(ligne.montant, ligne.solde) for ligne in lignes] == [
        (Decimal("-10.00"), Decimal("1875.37")),
        (Decimal("51.00"), Decimal("1926.37")),  # RG-12 : « Avoir » en entrée
    ]


def test_bornes_relues_sur_chaque_page() -> None:
    """Un tableau décalé de 30 pt est lu à l'identique : aucune abscisse codée en dur."""
    page = page_synthetique(
        entete(dx=30), transaction(364.1, "11", "Avoir", "Zalando", "51,00", "1926,37", dx=30)
    )
    ligne = lire_lignes_page(page, FICHIER)[0]
    assert (ligne.description, ligne.montant) == ("Zalando", Decimal("-51.00"))


def test_seconde_ligne_de_description_et_euro_colle() -> None:
    lignes = transaction(364.1, "08", "Avoir", "DECATHLON", "409,97", "1238,73")
    lignes = [m for m in lignes if m["text"] not in ("€",)]
    lignes += [mot("€1238,73", 478.3, 367.9, 35.1), mot("0008", 151.2, 371.7)]
    lignes = [m for m in lignes if m["text"] != "1238,73"]
    ligne = lire_lignes_page(page_synthetique(entete(), lignes), FICHIER)[0]
    assert (ligne.description, ligne.montant, ligne.solde) == (
        "DECATHLON 0008",
        Decimal("-409.97"),
        Decimal("1238.73"),
    )


def test_fin_du_tableau_avant_apercu_et_pied_de_page() -> None:
    page = page_synthetique(
        entete(),
        transaction(364.1, "30", "Avoir", "NYX", "1,50", "1533,57"),
        [mot("APERÇU", 73.7, 579.4), mot("DU", 120, 579.4), mot("SOLDE", 140, 579.4)],
        [mot("HSBC", 74.4, 639.8), mot("1533,57", 400, 639.8), mot("€", 440, 639.8)],
        [mot("Trade", 73.0, 759.2), mot("Republic", 91, 759.2), mot("Bank", 118, 759.2)]
        + [mot("GmbH,", 134, 759.2), mot("Page", 486.9, 759.2)],
    )
    assert len(decouper_bandes(page, COLONNES, FICHIER)) == 1


def test_mots_hors_transaction() -> None:
    page = page_synthetique(entete(), [mot("orphelin", 151.2, 355.0)])
    with pytest.raises(ParseError, match="page 2 : 1 mot\\(s\\) dans le tableau hors"):
        decouper_bandes(page, COLONNES, FICHIER)


def test_annee_absente() -> None:
    mots = [
        m for m in transaction(364.1, "08", "Avoir", "X", "5,50", "1648,70") if m["text"] != "2026"
    ]
    with pytest.raises(ParseError, match="page 2, ligne 1 : colonne DATE : une année attendue"):
        lire_lignes_page(page_synthetique(entete(), mots), FICHIER)


def test_mot_inattendu_dans_les_montants() -> None:
    mots = transaction(364.1, "08", "Avoir", "X", "5,50", "1648,70") + [mot("SECRET", 430, 371.7)]
    with pytest.raises(ParseError) as erreur:
        lire_lignes_page(page_synthetique(entete(), mots), FICHIER)
    assert str(erreur.value) == (
        "TR_TEST.pdf, page 2, ligne 1 : mot inattendu dans les colonnes de montants"
    )


def test_separateur_de_milliers() -> None:
    """Point ouvert de la spec : `12 345,67` arrive en deux mots qu'il faut recoller."""
    mots = transaction(364.1, "24", "Virement", "Incoming", "10", "12345,67", entree=True)
    mots = [m for m in mots if m["text"] != "10"] + [
        mot("10", 420.1, 367.9, 9.0),
        mot("000,00", 430.6, 367.9, 25.0),
    ]
    ligne = lire_lignes_page(page_synthetique(entete(), mots), FICHIER)[0]
    assert (ligne.montant, ligne.solde) == (Decimal("10000.00"), Decimal("12345.67"))


# Affectation des montants ------------------------------------------------------------------------


def test_affecter_montants_sortie_et_entree() -> None:
    solde = mot("1238,73", 485.6, 431.7, 27.8)
    assert affecter_montants(bande(mot("409,97", 453.1, 431.7), solde), COLONNES, FICHIER) == (
        Decimal("-409.97"),
        Decimal("1238.73"),
    )
    assert affecter_montants(bande(mot("51,00", 420.1, 431.7), solde), COLONNES, FICHIER) == (
        Decimal("51.00"),
        Decimal("1238.73"),
    )


@pytest.mark.parametrize(
    ("montants", "attendu"),
    [
        ([mot("1238,73", 485.6, 1, 27.8)], "2 montants attendus .*, 1 trouvé"),
        (
            [mot("5,00", 420.1, 1), mot("5,00", 453.1, 1), mot("1238,73", 485.6, 1, 27.8)],
            "2 montants attendus .*, 3 trouvé",
        ),
        ([mot("5,00", 453.1, 1), mot("1238,73", 470.0, 1, 27.8)], "solde mal aligné"),
        ([mot("5,00", 436.6, 1, 10), mot("1238,73", 485.6, 1, 27.8)], "ni sous ENTRÉE"),
        ([mot("-5,00", 453.1, 1), mot("1238,73", 485.6, 1, 27.8)], "montant positif attendu"),
    ],
)
def test_affecter_montants_configurations_refusees(montants: list[Mot], attendu: str) -> None:
    with pytest.raises(ParseError, match=attendu) as erreur:
        affecter_montants(bande(*montants), COLONNES, FICHIER)
    assert str(erreur.value).startswith("TR_TEST.pdf, page 2, ligne 3 : ")
    assert "DECATHLON" not in str(erreur.value)
