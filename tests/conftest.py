from collections.abc import Callable
from pathlib import Path

import pytest

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
