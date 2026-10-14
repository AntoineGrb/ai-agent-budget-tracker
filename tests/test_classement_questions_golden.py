"""Scénario de juin, second passage, juillet après juin (spec 06)."""

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import budget
from budget.classement.moteur import est_paiement_carte
from budget.config import Contexte, charger_contexte
from budget.importation import NOMS_FICHIERS, importer_mois
from budget.ledger import Ledger
from budget.models import Nature, Source
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.questions.modeles import TypeQuestion
from budget.regles import Critere, TypeRegle
from tests.conftest import DOSSIER_DONNEES_REELLES, DOSSIER_FIXTURES, EXEMPLE_CONTEXTE, RACINE
from tests.scenarios.juin import reponses

AUJOURD_HUI = date(2026, 10, 14)
CONTEXTE_REEL = RACINE / "data" / "contexte.yaml"


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
        yield ledger


def _scenario_juin(ledger: Ledger, contexte: Contexte) -> RapportClassement:
    interlocuteur = InterlocuteurScripte(reponses())
    rapport = classer_et_questionner("2026-06", ledger, contexte, interlocuteur)
    assert interlocuteur.restantes == 0
    return rapport


def test_juin_25_questions_au_premier_passage(ledger: Ledger, contexte: Contexte) -> None:
    """RG-06 : 16 virements (une seule question pour la paire « Velo ») et 9 opérations."""
    rapport = classer_et_questionner("2026-06", ledger, contexte, None)
    questions = rapport.questions
    assert len(questions) == 25
    assert Counter(q.type for q in questions) == {
        TypeQuestion.VIREMENT: 16,
        TypeQuestion.OPERATION: 9,
    }
    virements = [q for q in questions if q.type is TypeQuestion.VIREMENT]
    assert Counter(q.transaction.source for q in virements) == {Source.CA_JOINT: 13, Source.TR: 3}
    assert [q.transaction.montant for q in virements if q.contrepartie is not None] == [
        Decimal("-250.00")
    ]
    assert Counter(q.motif for q in questions) == {"RG-06": 16, "RG-14": 4, "non_classe": 5}
    assert rapport.bilan is None


def test_scenario_juin_de_bout_en_bout(ledger: Ledger, contexte: Contexte) -> None:
    rapport = _scenario_juin(ledger, contexte)
    assert rapport.bilan is not None
    assert (rapport.bilan.posees, rapport.bilan.repondues, rapport.bilan.passees) == (25, 25, 0)
    assert rapport.bilan.regles_creees == 4
    assert rapport.questions == []

    par_id = {t.id: t for t in rapport.transactions}
    classements = {c.transaction_id: c for c in rapport.resultat.classements}
    non_classees = [par_id[i] for i, c in classements.items() if c.nature is None]
    assert [t for t in non_classees if not est_paiement_carte(t)] == []

    # RG-07 : les deux côtés de « Velo » sont liés à DECATHLON 409,97 €
    velo = [c for c in classements.values() if c.nature is Nature.LIEN_DEPENSE]
    assert len(velo) == 2
    assert {par_id[c.transaction_id].source for c in velo} == {Source.CA_JOINT, Source.TR}
    [lien_id] = {c.lien_id for c in velo}
    assert lien_id is not None
    assert (par_id[lien_id].source, par_id[lien_id].montant) == (Source.TR, Decimal("-409.97"))

    # RG-09 et RG-10 : un remboursement a une sous-catégorie, un hors budget n'en a pas
    remboursements = [
        c
        for c in classements.values()
        if c.nature is Nature.REMBOURSEMENT and c.origine == "decision"
    ]
    assert remboursements and all(c.sous_categorie is not None for c in remboursements)
    hors_budget = [c for c in classements.values() if c.nature is Nature.HORS_BUDGET]
    assert len(hors_budget) == 10 and all(c.sous_categorie is None for c in hors_budget)

    # La réponse sur la dotation de 550 € lève l'alerte RG-05
    assert not [a for a in rapport.resultat.alertes if a.code == "RG-05"]

    regles = ledger.regles()
    assert [(r.type, r.source, r.critere) for r in regles] == [
        (TypeRegle.VIREMENT, Source.CA_JOINT, Critere.LIBELLE_EXACT),
        (TypeRegle.VIREMENT, Source.CA_JOINT, Critere.LIBELLE_EXACT),
        (TypeRegle.PRELEVEMENT, Source.TR, Critere.LIBELLE_EXACT),
        (TypeRegle.PRELEVEMENT, Source.TR, Critere.LIBELLE_PREFIXE),
    ]
    assert (regles[1].montant_min, regles[1].montant_max, regles[1].detail) == (
        Decimal("-600"),
        Decimal("-500"),
        "dotations.perso_conjoint",
    )
    assert regles[3].valeur == "SEPA DIRECT DEBIT TRANSFER TO ORANGE"


def test_second_passage_sans_question(ledger: Ledger, contexte: Contexte) -> None:
    premier = _scenario_juin(ledger, contexte)
    interlocuteur = InterlocuteurScripte([])
    second = classer_et_questionner("2026-06", ledger, contexte, interlocuteur)
    assert second.questions == [] and second.bilan is None
    assert interlocuteur.invites == []
    assert second.resultat == premier.resultat


def test_juillet_apres_juin(ledger: Ledger, contexte: Contexte) -> None:
    """Règles de juin réutilisées en juillet ; PayPal et chèque reposés (RG-14)."""
    _scenario_juin(ledger, contexte)
    importer_mois("2026-07", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    rapport = classer_et_questionner("2026-07", ledger, contexte, None)
    par_id = {t.id: t for t in rapport.transactions}
    par_regle = {
        (par_id[c.transaction_id].date_operation, par_id[c.transaction_id].montant): c
        for c in rapport.resultat.classements
        if c.origine == "regle"
    }
    assert set(par_regle) == {
        (date(2026, 7, 1), Decimal("-7.00")),
        (date(2026, 7, 27), Decimal("-11.99")),
    }
    assert (
        par_regle[(date(2026, 7, 1), Decimal("-7.00"))].sous_categorie
        == "abonnements.streaming_musique"
    )

    rg14 = [q for q in rapport.questions if q.motif == "RG-14"]
    assert sorted(q.transaction.montant for q in rg14) == [
        Decimal("-18.45"),
        Decimal("-4.80"),
        Decimal("-1.20"),
    ]
    assert all(not q.regle_proposable for q in rg14)
    # Figé après la première exécution (spec 06)
    assert Counter(q.type for q in rapport.questions) == {
        TypeQuestion.VIREMENT: 10,
        TypeQuestion.OPERATION: 6,
    }


def test_aucun_libelle_dans_les_logs(
    ledger: Ledger, contexte: Contexte, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="budget"):
        rapport = _scenario_juin(ledger, contexte)
    assert caplog.records, "le dialogue journalise son bilan"
    journal = "\n".join(r.getMessage() for r in caplog.records)
    for transaction in rapport.transactions:
        assert transaction.libelle not in journal
        assert transaction.libelle_normalise not in journal


def test_aucun_appel_reseau_ni_llm() -> None:
    """Les questions sont posées par du code, sans LLM (cadrage §9, nœud D)."""
    racine = Path(budget.__file__).parent
    fichiers = [racine / "regles.py", racine / "ledger" / "memoire.py"]
    fichiers += sorted((racine / "questions").glob("*.py"))
    interdits = ("pydantic_ai", "anthropic", "httpx", "requests", "urllib", "socket")
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        assert not [m for m in interdits if f"import {m}" in texte or f"from {m}" in texte], fichier


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_donnees_reelles_sans_questions(tmp_path: Path, mois: str) -> None:
    compact = mois.replace("-", "")
    fichiers = [DOSSIER_DONNEES_REELLES / mois / n.format(compact) for n in NOMS_FICHIERS.values()]
    if not all(f.is_file() for f in fichiers) or not CONTEXTE_REEL.is_file():
        pytest.skip("extractions ou contexte réels absents")
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois(mois, DOSSIER_DONNEES_REELLES, ledger, AUJOURD_HUI)
        rapport = classer_et_questionner(mois, ledger, charger_contexte(CONTEXTE_REEL), None)
    assert rapport.bilan is None
    assert len(rapport.resultat.classements) == len(rapport.transactions)
