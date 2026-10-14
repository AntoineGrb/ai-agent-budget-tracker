"""Persistance des questions, décisions et règles (spec 06)."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import RegleError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.models import Nature, Source
from budget.questions.modeles import Question, Reponse, TypeQuestion
from budget.questions.reponses import construire_regle, decisions_pour
from budget.regles import Critere, Regle, TypeRegle
from tests.conftest import DOSSIER_FIXTURES, fabriquer_transaction, importer_synthetiques

AUJOURD_HUI = date(2026, 10, 14)


def _question_velo(ledger: Ledger) -> Question:
    joint = fabriquer_transaction(
        "VIR INST VERS PRENOM NOM - VELO", "-250.00", type_operation="Virement émis"
    )
    tr = fabriquer_transaction(
        "INCOMING TRANSFER", "250.00", source=Source.TR, type_operation="Virement"
    )
    importer_synthetiques(ledger, "2026-06", [joint, tr])
    return Question(type=TypeQuestion.VIREMENT, motif="RG-06", transaction=joint, contrepartie=tr)


def test_reponse_ecrite_immediatement_avec_regle_liee(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    with Ledger(chemin) as ledger:
        question = _question_velo(ledger)
        reponse = Reponse(nature=Nature.HORS_BUDGET)
        regle = construire_regle(
            question, reponse, montant_min=Decimal("-250"), montant_max=Decimal("-250")
        )
        creee = ledger.enregistrer_reponse(question, decisions_pour(question, reponse), regle)
        assert creee is not None and creee.id == 1

        # Lue par une autre connexion, sans fermer le ledger : la réponse est déjà écrite
        with sqlite3.connect(chemin) as autre:
            questions = autre.execute("SELECT id, type, repondue_le FROM questions").fetchall()
            regles = autre.execute("SELECT id, question_id, nature FROM regles").fetchall()
            decisions = autre.execute(
                "SELECT transaction_id, question_id, regle_id FROM decisions"
            ).fetchall()
        assert [(q[0], q[1]) for q in questions] == [(1, "virement")] and questions[0][2]
        assert regles == [(1, 1, "hors_budget")]
        assert sorted(decisions) == sorted(
            [(question.transaction.id, 1, 1), (question.contrepartie.id, 1, 1)]  # type: ignore[union-attr]
        )


def test_decision_de_dotation_suffixee_pour_la_contrepartie(ledger_vide: Ledger) -> None:
    question = _question_velo(ledger_vide)
    reponse = Reponse(nature=Nature.DOTATION, detail="dotations.perso")
    ledger_vide.enregistrer_reponse(question, decisions_pour(question, reponse), None)
    decisions = ledger_vide.decisions()
    assert decisions[question.transaction.id].detail == "dotations.perso"
    assert question.contrepartie is not None
    assert decisions[question.contrepartie.id].detail == "dotations.perso:contrepartie"


def test_regle_en_conflit_n_ecrit_rien(ledger_vide: Ledger) -> None:
    question = _question_velo(ledger_vide)
    existante = Regle(
        type=TypeRegle.VIREMENT,
        source=Source.CA_JOINT,
        critere=Critere.LIBELLE_EXACT,
        valeur=question.transaction.libelle_normalise,
        montant_min=Decimal("-300"),
        montant_max=Decimal("-200"),
        nature=Nature.COMPLEMENT,
    )
    ledger_vide.ajouter_regle(existante)
    reponse = Reponse(nature=Nature.HORS_BUDGET)
    conflit = existante.model_copy(update={"nature": Nature.HORS_BUDGET})
    with pytest.raises(RegleError, match="conflit"):
        ledger_vide.enregistrer_reponse(question, decisions_pour(question, reponse), conflit)
    assert ledger_vide.decisions() == {}
    assert len(ledger_vide.regles()) == 1


def test_decisions_survivent_a_un_reimport(ledger_vide: Ledger) -> None:
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    transaction = next(
        t for t in ledger_vide.transactions("2026-06") if t.montant == Decimal("-7.00")
    )
    question = Question(type=TypeQuestion.VIREMENT, motif="RG-06", transaction=transaction)
    reponse = Reponse(nature=Nature.HORS_BUDGET)
    ledger_vide.enregistrer_reponse(question, decisions_pour(question, reponse), None)
    rapport = importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    assert rapport.ajoutees == 0
    assert ledger_vide.decisions()[transaction.id].nature is Nature.HORS_BUDGET
