"""Journal `decisions.md`, comparé au golden à horloge figée (spec 07)."""

from datetime import date, datetime
from pathlib import Path

import pytest

from budget.config import charger_contexte
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.restitution.journal import ecrire_journal, generer_journal
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE, RACINE
from tests.scenarios.juin import AppelsCategoriseur, juin_complet

GOLDEN = RACINE / "tests" / "golden" / "decisions_2026-06.md"


def test_journal_golden(tmp_path: Path, horloge_figee: datetime) -> None:
    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    journal = tmp_path / "decisions.md"
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 14))
        rapport = juin_complet(ledger, contexte, AppelsCategoriseur(), journal)
        assert rapport.journal == journal
        contenu = generer_journal(ledger, origine="data/budget.sqlite")
    assert (
        journal.read_text(encoding="utf-8").replace(
            (tmp_path / "budget.sqlite").as_posix(), "data/budget.sqlite"
        )
        == contenu
    )
    if not GOLDEN.is_file():  # première exécution : golden à relire puis à versionner
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(contenu, encoding="utf-8")
        pytest.fail(f"golden créé : {GOLDEN}, à relire")
    assert contenu == GOLDEN.read_text(encoding="utf-8")


def test_journal_regenere_meme_sans_interlocuteur(tmp_path: Path, horloge_figee: datetime) -> None:
    from budget.questions.execution import classer_et_questionner

    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    journal = tmp_path / "journal" / "decisions.md"
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, date(2026, 10, 14))
        classer_et_questionner("2026-06", ledger, contexte, None, journal=journal)
        assert journal.read_text(encoding="utf-8").startswith("# Journal des décisions\n")
        assert "## Règles (0 actives, 0 désactivée(s))" in journal.read_text(encoding="utf-8")
        ecrire_journal(ledger, journal)
