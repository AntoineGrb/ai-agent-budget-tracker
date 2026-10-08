"""Parser de l'export cartes du Crédit Agricole (spec 02)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import ControleError, ParseError
from budget.models import Compte, Source
from budget.parsers.credit_agricole import ReleveCarte, ReleveCartes, parser_ca_cartes
from tests.conftest import DOSSIER_FIXTURES, ecrire_variante

CARTES_JUIN = DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv"
CARTES_JUILLET = DOSSIER_FIXTURES / "2026-07" / "CA_CB_202607.csv"


def _totaux(releve: ReleveCarte) -> tuple[Decimal, Decimal]:
    debits = -sum((op.montant for op in releve.operations if op.montant < 0), Decimal(0))
    credits = sum((op.montant for op in releve.operations if op.montant > 0), Decimal(0))
    return debits, credits


@pytest.fixture(scope="module")
def juin() -> ReleveCartes:
    return parser_ca_cartes(CARTES_JUIN)


def test_juin_sections_par_carte(juin: ReleveCartes) -> None:
    """RG-01 : la date de débit de la section s'applique à toutes ses opérations."""
    assert juin.fichier == "CA_CB_202606.csv"
    assert [r.carte for r in juin.cartes] == ["X1091", "X1481"]
    for releve in juin.cartes:
        assert releve.date_debit == date(2026, 6, 30)
        assert {op.date_debit for op in releve.operations} == {date(2026, 6, 30)}
        assert {op.carte for op in releve.operations} == {releve.carte}


@pytest.mark.parametrize(
    ("index", "carte", "nombre", "debits", "credits", "encours"),
    [
        (0, "X1091", 39, "3316.49", "199.99", "3116.50"),
        (1, "X1481", 9, "410.67", "0", "410.67"),
    ],
)
def test_juin_valeurs_golden(
    juin: ReleveCartes,
    index: int,
    carte: str,
    nombre: int,
    debits: str,
    credits: str,
    encours: str,
) -> None:
    releve = juin.cartes[index]
    assert releve.carte == carte
    assert len(releve.operations) == nombre
    assert _totaux(releve) == (Decimal(debits), Decimal(credits))
    assert releve.encours == Decimal(encours)


def test_juin_premiere_operation_et_avoir(juin: ReleveCartes) -> None:
    operations = juin.cartes[0].operations
    premiere = operations[0]
    assert premiere.date_operation == date(2026, 6, 17)
    assert premiere.libelle == premiere.libelle_brut == "MILIBOO CHAVANOD"
    assert premiere.montant == Decimal("-258.99")
    assert premiere.source is Source.CA_CB
    assert premiere.compte is Compte.JOINT
    assert premiere.fichier == "CA_CB_202606.csv"
    assert premiere.position == 18
    assert premiere.type_operation is None
    assert premiere.ics is None
    assert premiere.solde_apres is None

    avoirs = [op for op in operations if op.montant > 0]
    assert len(avoirs) == 1
    assert avoirs[0].date_operation == date(2026, 6, 4)
    assert avoirs[0].libelle == "MANOMANO PARIS"
    assert avoirs[0].montant == Decimal("199.99")


def test_libelle_espaces_internes_conserves(juin: ReleveCartes) -> None:
    libelles = {op.libelle for op in juin.cartes[0].operations}
    assert "UBER   *EATS HELP.UBER.COM" in libelles
    assert all(libelle == libelle.strip() for libelle in libelles)


def test_juillet_valeurs_figees() -> None:
    releve = parser_ca_cartes(CARTES_JUILLET)
    assert [(r.carte, r.date_debit, len(r.operations), r.encours) for r in releve.cartes] == [
        ("X1091", date(2026, 7, 31), 65, Decimal("3021.13")),
        ("X1481", date(2026, 7, 31), 21, Decimal("527.25")),
    ]
    assert [_totaux(r) for r in releve.cartes] == [
        (Decimal("3930.43"), Decimal("909.30")),
        (Decimal("527.25"), Decimal("0")),
    ]


@pytest.mark.parametrize("en_tete", ["-3 116.50 €", "-3 116,50 €", "-3116.50"])
def test_encours_point_ou_virgule_decimale(tmp_path: Path, en_tete: str) -> None:
    chemin = ecrire_variante(CARTES_JUIN, tmp_path, ("-3 116.50 €", en_tete))
    assert parser_ca_cartes(chemin).cartes[0].encours == Decimal("3116.50")


def test_controle_interne_detecte_ligne_supprimee(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        CARTES_JUIN, tmp_path, ("12/06/2026;BOURSORAMA BOULOGNE BILLA;11,00;;\n", "")
    )
    with pytest.raises(ControleError) as erreur:
        parser_ca_cartes(chemin)
    message = str(erreur.value)
    assert "carte X1091" in message
    assert "3 116,50 €" in message
    assert "3 105,50 €" in message
    assert "écart 11,00 €" in message
    assert "BOURSORAMA" not in message


def test_controle_interne_detecte_avoir_lu_en_debit(tmp_path: Path) -> None:
    chemin = ecrire_variante(CARTES_JUIN, tmp_path, ("PARIS;;199,99;", "PARIS;199,99;;"))
    with pytest.raises(ControleError, match="écart 399,98 €"):
        parser_ca_cartes(chemin)


def test_section_sans_en_tete_de_colonnes(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        CARTES_JUIN, tmp_path, ("Date;Libellé;Débit euros;Crédit euros;\n17/06", "\n17/06")
    )
    with pytest.raises(ParseError, match=r"ligne 18 : ligne de données hors"):
        parser_ca_cartes(chemin)


def test_section_sans_donnees_ni_en_tete(tmp_path: Path) -> None:
    texte = CARTES_JUIN.read_bytes().decode("cp1252")
    debut = texte.index("Date;Libellé", texte.index("1481"))
    chemin = tmp_path / CARTES_JUIN.name
    chemin.write_bytes(texte[:debut].encode("cp1252"))
    with pytest.raises(ParseError, match=r"ligne 60 : section de la carte X1481 sans en-tête"):
        parser_ca_cartes(chemin)


def test_section_sans_encours(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        CARTES_JUIN, tmp_path, ("Encours débité le 30 juin 2026;-3 116.50 €", "")
    )
    with pytest.raises(ParseError, match="en-tête de colonnes avant la ligne « Encours débité"):
        parser_ca_cartes(chemin)


def test_en_tete_encours_non_reconnu(tmp_path: Path) -> None:
    """Point ouvert de la spec : un encours non encore débité doit arrêter le parsing."""
    chemin = ecrire_variante(CARTES_JUIN, tmp_path, ("Encours débité le", "Encours à débiter le"))
    with pytest.raises(ParseError, match=r"ligne 15 : en-tête d'encours non reconnu"):
        parser_ca_cartes(chemin)


@pytest.mark.parametrize(
    ("remplacement", "attendu"),
    [
        ("MILIBOO CHAVANOD;258,99;12,00;", "un seul montant attendu"),
        ("MILIBOO CHAVANOD;;;", "aucun trouvé"),
    ],
)
def test_ligne_a_zero_ou_deux_montants(tmp_path: Path, remplacement: str, attendu: str) -> None:
    chemin = ecrire_variante(CARTES_JUIN, tmp_path, ("MILIBOO CHAVANOD;258,99;;", remplacement))
    with pytest.raises(ParseError, match=attendu) as erreur:
        parser_ca_cartes(chemin)
    message = str(erreur.value)
    assert message.startswith("CA_CB_202606.csv, ligne 18 : opération du 17/06/2026")
    assert "MILIBOO" not in message


def test_ligne_inattendue_dans_la_zone_de_donnees(tmp_path: Path) -> None:
    chemin = ecrire_variante(CARTES_JUIN, tmp_path, ("17/06/2026;MILIBOO", "Total;MILIBOO"))
    with pytest.raises(ParseError, match=r"ligne 18 : ligne inattendue"):
        parser_ca_cartes(chemin)


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_donnees_reelles_identiques_aux_fixtures(
    chemin_donnees_reelles: Callable[[str], Path], mois: str
) -> None:
    """L'anonymisation conserve montants, dates et structure : le parsing doit concorder."""
    nom = f"CA_CB_{mois.replace('-', '')}.csv"
    reel = parser_ca_cartes(chemin_donnees_reelles(mois) / nom)
    fixture = parser_ca_cartes(DOSSIER_FIXTURES / mois / nom)

    def resume(releve: ReleveCartes) -> list[tuple[object, ...]]:
        return [
            (
                r.carte,
                r.date_debit,
                r.encours,
                [(o.date_operation, o.montant) for o in r.operations],
            )
            for r in releve.cartes
        ]

    assert resume(reel) == resume(fixture)
