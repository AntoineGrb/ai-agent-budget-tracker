"""Parser de l'export du compte joint du Crédit Agricole (spec 02)."""

from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import ParseError
from budget.models import Compte, Source
from budget.parsers.credit_agricole import LigneDebitDiffere, ReleveJoint, parser_ca_joint
from tests.conftest import DOSSIER_FIXTURES, ecrire_variante

JOINT_JUIN = DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv"
JOINT_JUILLET = DOSSIER_FIXTURES / "2026-07" / "CA_CPTE_JOINT_202607.csv"


def _totaux(releve: ReleveJoint) -> tuple[Decimal, Decimal]:
    debits = -sum((op.montant for op in releve.operations if op.montant < 0), Decimal(0))
    credits = sum((op.montant for op in releve.operations if op.montant > 0), Decimal(0))
    return debits, credits


@pytest.fixture(scope="module")
def juin() -> ReleveJoint:
    return parser_ca_joint(JOINT_JUIN)


def test_juin_periode_et_totaux(juin: ReleveJoint) -> None:
    assert juin.fichier == "CA_CPTE_JOINT_202606.csv"
    assert (juin.periode_debut, juin.periode_fin) == (date(2026, 6, 1), date(2026, 6, 30))
    assert len(juin.operations) == 37
    assert _totaux(juin) == (Decimal("10127.46"), Decimal("12102.56"))
    assert {(op.source, op.compte) for op in juin.operations} == {(Source.CA_JOINT, Compte.JOINT)}
    assert all(op.date_debit is None and op.solde_apres is None for op in juin.operations)


def test_juin_types_operation(juin: ReleveJoint) -> None:
    assert Counter(op.type_operation for op in juin.operations) == {
        "Virement en votre faveur": 11,
        "Virement émis": 8,
        "Prélèvement": 8,
        "Prélèvement carte": 2,
        "Cotisation": 2,
        "Remboursement de prêt": 2,
        "Avoir": 1,
        "Règlement": 1,
        "Régul opé débitrices": 1,
        "Remise de chèque": 1,
    }


def test_juin_ics_uniquement_sur_les_prelevements(juin: ReleveJoint) -> None:
    avec_ics = [op for op in juin.operations if op.ics]
    assert sorted(op.ics or "" for op in avec_ics) == [
        "DE56AGR00002197951",
        "FR41ZZZ272230",
        "FR48ZZZ829660",
        "FR67ZZZ308137",
        "FR83ZZZ459654",
        "LU96ZZZ0000000000000000058",
        "LU96ZZZ0000000000000000058",
        "LU96ZZZ0000000000000000058",
    ]
    assert {op.type_operation for op in avec_ics} == {"Prélèvement"}


def test_juin_cartes_extraites_des_libelles(juin: ReleveJoint) -> None:
    """RG-17 : la carte est extraite ici, y compris l'ancienne X9817 (alerte à l'étape 5)."""
    assert sorted(
        (op.type_operation or "", op.carte or "") for op in juin.operations if op.carte
    ) == [
        ("Avoir", "X9817"),
        ("Cotisation", "X1481"),
        ("Prélèvement carte", "X1091"),
        ("Prélèvement carte", "X1481"),
        ("Régul opé débitrices", "X9817"),
    ]


def test_juin_debits_differes(juin: ReleveJoint) -> None:
    """RG-03 : une ligne de débit différé par carte active, conservée aussi en opération."""
    assert juin.debits_differes == (
        LigneDebitDiffere(
            carte="X1091",
            date_debit=date(2026, 6, 30),
            date_arrete=date(2026, 6, 18),
            montant=Decimal("3116.50"),
        ),
        LigneDebitDiffere(
            carte="X1481",
            date_debit=date(2026, 6, 30),
            date_arrete=date(2026, 6, 18),
            montant=Decimal("410.67"),
        ),
    )
    operations = [op for op in juin.operations if op.type_operation == "Prélèvement carte"]
    assert [(op.carte, op.montant) for op in operations] == [
        ("X1091", Decimal("-3116.50")),
        ("X1481", Decimal("-410.67")),
    ]


def test_premiere_operation_et_positions(juin: ReleveJoint) -> None:
    premiere = juin.operations[0]
    assert premiere.position == 19
    assert premiere.date_operation == date(2026, 6, 30)
    assert premiere.type_operation == "Prélèvement carte"
    assert premiere.libelle == "DEPENSES CARTE X1091 AU 18/06/26"
    assert [op.position for op in juin.operations[:3]] == [19, 25, 31]


def test_libelle_multiligne_restitue_entier(juin: ReleveJoint) -> None:
    texte = JOINT_JUIN.read_bytes().decode("cp1252")
    debut = texte.index('"Prélèvement\n0211198') + 1
    attendu = texte[debut : texte.index('";43,77;', debut)]

    operation = next(op for op in juin.operations if op.ics == "FR41ZZZ272230")
    assert operation.libelle_brut == attendu
    lignes = operation.libelle_brut.split("\n")
    assert len(lignes) == 6
    assert lignes[0] == operation.type_operation == "Prélèvement"
    assert operation.libelle == lignes[1].strip()
    assert operation.libelle.startswith("0211198 CREDIT AGRICOLE DE PARIS")
    assert operation.montant == Decimal("-43.77")


def test_decoupage_libelle_free(juin: ReleveJoint) -> None:
    operation = next(op for op in juin.operations if op.ics == "FR83ZZZ459654")
    assert operation.type_operation == "Prélèvement"
    assert operation.libelle == "Free Telecom - Free - Free HautDebit 7058444555"
    assert operation.montant == Decimal("-23.99")


def test_libelle_sans_ligne_2_reprend_le_type(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        JOINT_JUIN, tmp_path, ('"Cotisation\nOffre Premium\n', '"Cotisation\n\n')
    )
    operation = next(
        op for op in parser_ca_joint(chemin).operations if op.montant == Decimal("-15.50")
    )
    assert operation.type_operation == operation.libelle == "Cotisation"


def test_deux_ics_dans_un_libelle(tmp_path: Path) -> None:
    chemin = ecrire_variante(
        JOINT_JUIN, tmp_path, ("FR41ZZZ272230\nXXXXXXXXXXXXXXX", "FR41ZZZ272230\nFR41ZZZ272231")
    )
    with pytest.raises(ParseError, match="2 trouvés") as erreur:
        parser_ca_joint(chemin)
    message = str(erreur.value)
    assert "opération du 29/06/2026 (-43,77 €)" in message
    assert "CREDIT AGRICOLE" not in message


def test_mixte_crlf_lf_ignore_le_reste(juin: ReleveJoint) -> None:
    """Les sections d'encours à venir et de fin de fichier ne produisent aucune opération."""
    assert all(op.date_operation.month == 6 for op in juin.operations)
    assert Decimal("-297.30") not in {op.montant for op in juin.operations}


def test_juillet_valeurs_figees() -> None:
    releve = parser_ca_joint(JOINT_JUILLET)
    assert (releve.periode_debut, releve.periode_fin) == (date(2026, 7, 1), date(2026, 7, 31))
    assert len(releve.operations) == 28
    assert _totaux(releve) == (Decimal("12454.27"), Decimal("9859.58"))
    assert Counter(op.type_operation for op in releve.operations) == {
        "Prélèvement": 7,
        "Virement en votre faveur": 6,
        "Virement émis": 6,
        "Prélèvement carte": 2,
        "Remboursement de prêt": 2,
        "Retrait au distributeur": 2,
        "Chèque emis": 1,
        "Cotisation": 1,
        "Règlement": 1,
    }
    assert sum(1 for op in releve.operations if op.ics) == 7
    assert [(d.carte, d.date_debit, d.date_arrete, d.montant) for d in releve.debits_differes] == [
        ("X1091", date(2026, 7, 31), date(2026, 7, 16), Decimal("3021.13")),
        ("X1481", date(2026, 7, 31), date(2026, 7, 16), Decimal("527.25")),
    ]


@pytest.mark.parametrize(
    ("ancien", "nouveau"),
    [
        ("entre le 01/06/2026 et le 30/06/2026", "entre le 01/06/2026 et le 29/06/2026"),
        ("entre le 01/06/2026 et le 30/06/2026", "entre le 02/06/2026 et le 30/06/2026"),
        ("entre le 01/06/2026 et le 30/06/2026", "entre le 01/06/2026 et le 31/07/2026"),
    ],
)
def test_periode_non_mensuelle(tmp_path: Path, ancien: str, nouveau: str) -> None:
    """RG-02 : le fichier joint doit couvrir exactement un mois civil."""
    chemin = ecrire_variante(JOINT_JUIN, tmp_path, (ancien, nouveau))
    with pytest.raises(ParseError, match=r"ligne 16 : période .* mois civil complet"):
        parser_ca_joint(chemin)


@pytest.mark.parametrize(
    ("remplacement", "attendu"),
    [
        ('Virement du mois\n\n\n\n";550;12', "un seul montant attendu"),
        ('Virement du mois\n\n\n\n";;', "aucun trouvé"),
    ],
)
def test_ligne_a_zero_ou_deux_montants(tmp_path: Path, remplacement: str, attendu: str) -> None:
    chemin = ecrire_variante(JOINT_JUIN, tmp_path, ('Virement du mois\n\n\n\n";550;', remplacement))
    with pytest.raises(ParseError, match=attendu) as erreur:
        parser_ca_joint(chemin)
    message = str(erreur.value)
    assert message.startswith("CA_CPTE_JOINT_202606.csv, ligne 31 : opération du 30/06/2026")
    assert "Virement" not in message


def test_montant_illisible(tmp_path: Path) -> None:
    chemin = ecrire_variante(JOINT_JUIN, tmp_path, ('";550;', '";5.50,0;'))
    with pytest.raises(ParseError, match=r"ligne 31 : montant invalide '5.50,0'"):
        parser_ca_joint(chemin)


def test_sans_ligne_de_periode(tmp_path: Path) -> None:
    chemin = ecrire_variante(JOINT_JUIN, tmp_path, ("Liste des opérations", "Liste"))
    with pytest.raises(ParseError, match=r"ligne 18 : en-tête de colonnes avant"):
        parser_ca_joint(chemin)


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_donnees_reelles_identiques_aux_fixtures(
    chemin_donnees_reelles: Callable[[str], Path], mois: str
) -> None:
    """Montants, dates, types, ICS et cartes sont conservés par l'anonymisation."""
    nom = f"CA_CPTE_JOINT_{mois.replace('-', '')}.csv"
    reel = parser_ca_joint(chemin_donnees_reelles(mois) / nom)
    fixture = parser_ca_joint(DOSSIER_FIXTURES / mois / nom)

    def resume(releve: ReleveJoint) -> tuple[object, ...]:
        return (
            releve.periode_debut,
            releve.periode_fin,
            releve.debits_differes,
            [
                (o.position, o.date_operation, o.type_operation, o.ics, o.carte, o.montant)
                for o in releve.operations
            ],
        )

    assert resume(reel) == resume(fixture)
