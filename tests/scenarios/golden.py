"""Fichiers de référence de `tests/golden/` (spec 10).

`BUDGET_GOLDEN=regenerer uv run pytest tests/test_bout_en_bout.py` les réécrit ; toute
régénération est justifiée dans le message de commit, avec le diff attendu.
"""

import os

from tests.conftest import RACINE

DOSSIER_GOLDEN = RACINE / "tests" / "golden"


def verifier_golden(nom: str, obtenu: str) -> None:
    chemin = DOSSIER_GOLDEN / nom
    if os.environ.get("BUDGET_GOLDEN") == "regenerer":
        chemin.parent.mkdir(exist_ok=True)
        chemin.write_text(obtenu, encoding="utf-8", newline="\n")
    attendu = chemin.read_text(encoding="utf-8")
    assert obtenu == attendu, f"{nom} diffère du golden (BUDGET_GOLDEN=regenerer pour le refaire)"
