import itertools
import shutil
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai import models

from budget.dates import mois_budgetaire
from budget.ledger import Ledger
from budget.libelles import normaliser_libelle
from budget.models import Compte, Source, Transaction

# Cadrage §11 : aucun test ne peut appeler un vrai modèle (FunctionModel et TestModel seulement)
models.ALLOW_MODEL_REQUESTS = False

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
