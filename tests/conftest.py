import itertools
import shutil
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import pytest

from budget.dates import mois_budgetaire
from budget.importation import construire_transactions
from budget.ledger import FichierImporte, Ledger
from budget.libelles import normaliser_libelle
from budget.models import Compte, Source, Transaction
from budget.parsers.credit_agricole import parser_ca_cartes, parser_ca_joint
from budget.parsers.trade_republic import parser_trade_republic

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_FIXTURES = RACINE / "tests" / "fixtures"
DOSSIER_DONNEES_REELLES = RACINE / "data" / "raw"
TABLE_ANONYMISATION = RACINE / "data" / "anonymisation.yaml"
EXEMPLE_CONTEXTE = RACINE / "contexte.example.yaml"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if DOSSIER_DONNEES_REELLES.is_dir():
        return
    saut = pytest.mark.skip(reason="data/raw/ absent : tests sur données réelles ignorés")
    for item in items:
        if "donnees_reelles" in item.keywords:
            item.add_marker(saut)


def copier_mois(mois: str, dossier: Path) -> Path:
    """Copie les fixtures du mois dans `dossier/AAAA-MM/` et renvoie ce sous-dossier."""
    cible = dossier / mois
    shutil.copytree(DOSSIER_FIXTURES / mois, cible)
    return cible


def ecrire_variante(source: Path, cible: Path, *remplacements: tuple[str, str]) -> Path:
    """Copie `source` dans `cible` (même nom) en appliquant chaque remplacement une fois.

    Les octets sont relus et réécrits en cp1252 pour conserver les fins de ligne d'origine.
    """
    texte = source.read_bytes().decode("cp1252")
    for ancien, nouveau in remplacements:
        assert ancien in texte, f"motif absent de {source.name} : {ancien!r}"
        texte = texte.replace(ancien, nouveau, 1)
    chemin = cible / source.name
    chemin.write_bytes(texte.encode("cp1252"))
    return chemin


@pytest.fixture
def chemin_fixtures() -> Callable[[str], Path]:
    def _chemin(mois: str) -> Path:
        return DOSSIER_FIXTURES / mois

    return _chemin


@pytest.fixture
def chemin_donnees_reelles() -> Callable[[str], Path]:
    def _chemin(mois: str) -> Path:
        dossier = DOSSIER_DONNEES_REELLES / mois
        if not dossier.is_dir():
            pytest.skip(f"données réelles absentes : {dossier}")
        return dossier

    return _chemin


@pytest.fixture
def ledger_vide(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        yield ledger


@cache
def transactions_fixtures(mois: str, dossier: Path = DOSSIER_FIXTURES) -> tuple[Transaction, ...]:
    """Transactions des trois extractions du mois, sans passer par la base (cartes, joint, TR)."""
    compact = mois.replace("-", "")
    cartes = parser_ca_cartes(dossier / mois / f"CA_CB_{compact}.csv")
    joint = parser_ca_joint(dossier / mois / f"CA_CPTE_JOINT_{compact}.csv")
    releve_tr = parser_trade_republic(dossier / mois / f"TR_{compact}.pdf")
    return (
        *construire_transactions([op for releve in cartes.cartes for op in releve.operations]),
        *construire_transactions(joint.operations),
        *construire_transactions(releve_tr.operations),
    )


_NUMEROS = itertools.count(1)


def fabriquer_transaction(
    libelle: str = "OPERATION",
    montant: str = "-10.00",
    *,
    source: Source = Source.CA_JOINT,
    date_operation: date = date(2026, 6, 15),
    type_operation: str | None = None,
    ics: str | None = None,
    carte: str | None = None,
) -> Transaction:
    """Transaction synthétique du mois de sa date, pour les cas absents des fixtures."""
    numero = next(_NUMEROS)
    return Transaction(
        id=f"{numero:016x}",
        source=source,
        compte=Compte.PERSO if source is Source.TR else Compte.JOINT,
        fichier=f"synthetique_{source.value}",
        position=numero,
        rang=1,
        date_operation=date_operation,
        mois_budgetaire=mois_budgetaire(date_operation),
        type_operation=type_operation,
        libelle=libelle,
        libelle_brut=libelle,
        libelle_normalise=normaliser_libelle(libelle),
        ics=ics,
        carte=carte,
        montant=Decimal(montant),
    )


def importer_synthetiques(ledger: Ledger, mois: str, transactions: list[Transaction]) -> None:
    """Importe des transactions synthétiques du mois, un fichier par source."""
    sources = sorted({t.source for t in transactions}, key=lambda s: s.value)
    ledger.enregistrer_import(
        mois,
        [
            FichierImporte(
                source=source,
                fichier=f"synthetique_{source.value}",
                empreinte="0" * 64,
                transactions=tuple(t for t in transactions if t.source is source),
            )
            for source in sources
        ],
    )
