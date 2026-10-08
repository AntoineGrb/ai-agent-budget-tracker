"""Garde-fou d'anonymat des fixtures, et conservation des montants par l'anonymisation."""

import csv
import re
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
import yaml

from budget.montants import parse_montant, parse_montant_optionnel
from tests.conftest import DOSSIER_FIXTURES, TABLE_ANONYMISATION

MOIS = ["2026-06", "2026-07"]
FICHIERS = ["CA_CB_{}.csv", "CA_CPTE_JOINT_{}.csv", "TR_{}.pdf"]
MOTIF_IBAN_FR = re.compile(r"FR\d{12}[0-9A-Z]{11}\d{2}")
MOTIF_ICS = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}")
MOTIF_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def _fichiers_fixtures() -> list[Path]:
    return sorted(p for p in DOSSIER_FIXTURES.glob("*/*") if p.suffix in (".csv", ".pdf"))


def _texte(chemin: Path) -> str:
    if chemin.suffix == ".pdf":
        with pymupdf.open(chemin) as document:
            return "\n".join(page.get_text() for page in document)
    return chemin.read_text(encoding="cp1252")


@pytest.mark.parametrize("mois", MOIS)
def test_trois_fichiers_par_mois(mois: str) -> None:
    compact = mois.replace("-", "")
    for modele in FICHIERS:
        assert (DOSSIER_FIXTURES / mois / modele.format(compact)).is_file()


@pytest.mark.parametrize("chemin", _fichiers_fixtures(), ids=lambda p: p.name)
def test_aucun_iban_francais(chemin: Path) -> None:
    # Les IBAN fictifs de l'anonymisation ont la clé « 00 », impossible pour un IBAN réel.
    reels = [iban for iban in MOTIF_IBAN_FR.findall(_texte(chemin)) if not iban.startswith("FR00")]
    assert reels == [], f"{chemin.name} : {len(reels)} IBAN français trouvé(s)"


@pytest.mark.parametrize("chemin", _fichiers_fixtures(), ids=lambda p: p.name)
def test_aucune_sequence_de_11_chiffres(chemin: Path) -> None:
    # Les identifiants créanciers SEPA (ICS) sont conservés volontairement.
    jetons = [
        j for j in re.findall(r"\S+", _texte(chemin)) if not MOTIF_ICS.fullmatch(j.strip("()"))
    ]
    fautifs = [j for j in jetons if re.search(r"\d{11}", j)]
    assert fautifs == [], f"{chemin.name} : {len(fautifs)} séquence(s) de 11 chiffres"


@pytest.mark.skipif(not TABLE_ANONYMISATION.is_file(), reason="data/anonymisation.yaml absent")
@pytest.mark.parametrize("chemin", _fichiers_fixtures(), ids=lambda p: p.name)
def test_aucune_valeur_de_la_table(chemin: Path) -> None:
    table = yaml.safe_load(TABLE_ANONYMISATION.read_text(encoding="utf-8"))
    texte = _texte(chemin)
    trouvees = [
        cle
        for cle in table.get("remplacements") or {}
        if re.search(rf"(?<!\w){re.escape(str(cle))}(?!\w)", texte, re.IGNORECASE)
    ]
    # Le message ne cite pas les valeurs : ce sont des données personnelles.
    assert trouvees == [], f"{chemin.name} : {len(trouvees)} valeur(s) de la table présente(s)"


def _lignes_operations(chemin: Path) -> list[list[str]]:
    with chemin.open(encoding="cp1252", newline="") as flux:
        return [
            ligne
            for ligne in csv.reader(flux, delimiter=";")
            if ligne and MOTIF_DATE.fullmatch(ligne[0])
        ]


def _nets_par_carte(chemin: Path) -> dict[str, Decimal]:
    nets: dict[str, Decimal] = defaultdict(Decimal)
    carte = None
    with chemin.open(encoding="cp1252", newline="") as flux:
        for ligne in csv.reader(flux, delimiter=";"):
            if ligne and (m := re.search(r"carte n° .*(\d{4})$", ligne[0].strip())):
                carte = f"X{m.group(1)}"
            elif ligne and MOTIF_DATE.fullmatch(ligne[0]):
                assert carte is not None
                debit = parse_montant_optionnel(ligne[2]) or Decimal(0)
                credit = parse_montant_optionnel(ligne[3]) or Decimal(0)
                nets[carte] += debit - credit
    return dict(nets)


def test_totaux_cartes_juin_conserves() -> None:
    nets = _nets_par_carte(DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv")
    assert nets == {"X1091": Decimal("3116.50"), "X1481": Decimal("410.67")}


def test_totaux_joint_juin_conserves() -> None:
    lignes = _lignes_operations(DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv")
    debits = sum((parse_montant(ligne[2]) for ligne in lignes if ligne[2]), Decimal(0))
    credits = sum((parse_montant(ligne[3]) for ligne in lignes if ligne[3]), Decimal(0))
    assert len(lignes) == 37  # le cadrage §3 indique 38 : erroné, voir spec 02
    assert debits == Decimal("10127.46")
    assert credits == Decimal("12102.56")


def _montants_csv(chemin: Path) -> list[tuple[str, str, str]]:
    return [(ligne[0], ligne[2], ligne[3]) for ligne in _lignes_operations(chemin)]


def _mots_montants_pdf(chemin: Path) -> list[tuple[int, str, float, float]]:
    with pymupdf.open(chemin) as document:
        return [
            (page.number, mot[4], round(mot[0], 1), round(mot[1], 1))
            for page in document
            for mot in page.get_text("words")
            if re.fullmatch(r"-?\d+,\d{2}|€", mot[4])
        ]


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", MOIS)
def test_montants_et_positions_identiques_aux_fichiers_reels(
    mois: str, chemin_donnees_reelles: Callable[[str], Path]
) -> None:
    reel = chemin_donnees_reelles(mois)
    compact = mois.replace("-", "")
    for nom in (f"CA_CB_{compact}.csv", f"CA_CPTE_JOINT_{compact}.csv"):
        assert _montants_csv(DOSSIER_FIXTURES / mois / nom) == _montants_csv(reel / nom), nom
    nom_pdf = f"TR_{compact}.pdf"
    assert _mots_montants_pdf(DOSSIER_FIXTURES / mois / nom_pdf) == _mots_montants_pdf(
        reel / nom_pdf
    )
