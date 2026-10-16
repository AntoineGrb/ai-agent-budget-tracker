"""Traçabilité des règles de gestion (cadrage §6, spec 10).

Les règles conservées sont couvertes par au moins un test et citées dans `src/` ; les règles
retirées au recadrage ne sont plus citées nulle part. La liste est lue dans `docs/cadrage.md`.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

from tests.conftest import RACINE

CADRAGE = RACINE / "docs" / "cadrage.md"
MOTIF_REGLE = re.compile(r"(?<![a-z0-9])RG[-_]?(\d{2})(?!\d)", re.IGNORECASE)
ESSAI = Path(__file__).resolve()


def _regles_du_cadrage() -> tuple[set[str], set[str]]:
    """(conservées, retirées) d'après le tableau et la ligne « Règles retirées » du §6."""
    texte = CADRAGE.read_text(encoding="utf-8")
    conservees = set(re.findall(r"^\| (RG-\d{2}) \|", texte, re.MULTILINE))
    ligne = next(ligne for ligne in texte.splitlines() if ligne.startswith("**Règles retirées"))
    retirees = set(re.findall(r"RG-\d{2}", ligne))
    return conservees, retirees


def _citations(texte: str) -> set[str]:
    return {f"RG-{numero}" for numero in MOTIF_REGLE.findall(texte)}


def _commentaires_et_chaines(source: str) -> str:
    """Commentaires et littéraux d'un fichier Python, sans le code."""
    morceaux = []
    for jeton in tokenize.generate_tokens(io.StringIO(source).readline):
        if jeton.type in (tokenize.COMMENT, tokenize.STRING):
            morceaux.append(jeton.string)
    return "\n".join(morceaux)


def _citations_de_src() -> set[str]:
    citees: set[str] = set()
    for fichier in (RACINE / "src" / "budget").rglob("*.py"):
        citees |= _citations(_commentaires_et_chaines(fichier.read_text(encoding="utf-8")))
    return citees


def _noms_et_docstrings_de_tests() -> str:
    """Noms de fonctions de test, plus docstrings de modules, classes et fonctions."""
    morceaux = []
    for fichier in (RACINE / "tests").rglob("*.py"):
        if fichier == ESSAI:
            continue
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        morceaux.append(ast.get_docstring(arbre) or "")
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                morceaux.append(noeud.name)
                morceaux.append(ast.get_docstring(noeud) or "")
    return "\n".join(morceaux)


def _citations_residuelles(retirees: set[str]) -> dict[str, list[str]]:
    """Règles retirées encore citées, avec les fichiers concernés."""
    trouvees: dict[str, list[str]] = {}
    fichiers = [*(RACINE / "src").rglob("*.py"), *(RACINE / "tests").rglob("*.py")]
    for fichier in fichiers:
        if fichier == ESSAI:
            continue
        for regle in _citations(fichier.read_text(encoding="utf-8")) & retirees:
            trouvees.setdefault(regle, []).append(str(fichier.relative_to(RACINE)))
    return trouvees


def test_le_cadrage_liste_les_regles() -> None:
    conservees, retirees = _regles_du_cadrage()
    assert len(conservees) == 12
    assert len(retirees) == 7
    assert not conservees & retirees


def test_regles_conservees_couvertes_par_un_test_et_citees_dans_src() -> None:
    conservees, _ = _regles_du_cadrage()
    sans_test = conservees - _citations(_noms_et_docstrings_de_tests())
    sans_code = conservees - _citations_de_src()
    assert not sans_test, f"règles sans test (nom ou docstring) : {sorted(sans_test)}"
    assert not sans_code, (
        f"règles absentes de src/ (commentaire ou docstring) : {sorted(sans_code)}"
    )


def test_regles_retirees_absentes_du_code_et_des_tests() -> None:
    _, retirees = _regles_du_cadrage()
    assert not _citations_residuelles(retirees), (
        f"règles retirées au recadrage encore citées : {_citations_residuelles(retirees)}"
    )
