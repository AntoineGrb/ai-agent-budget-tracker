"""Dialogue scripté : choix, sous-catégories, passer, quitter, ressaisie, règles (spec 06)."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from rich.console import Console

from budget.config import Contexte, charger_contexte
from budget.ledger import Ledger
from budget.models import Nature, Source, Transaction
from budget.questions.dialogue import (
    InterlocuteurScripte,
    InterlocuteurTerminal,
    ScenarioError,
)
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.regles import ICS_PAYPAL, Critere, Regle, TypeRegle
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, importer_synthetiques

MOIS = "2026-06"


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        yield ledger


def _virement(montant: str, libelle: str = "WEB MADAME TIERS", jour: int = 10) -> Transaction:
    type_operation = "Virement émis" if montant.startswith("-") else "Virement en votre faveur"
    return fabriquer_transaction(
        libelle, montant, date_operation=date(2026, 6, jour), type_operation=type_operation
    )


def _jouer(
    ledger: Ledger, contexte: Contexte, reponses: list[str]
) -> tuple[RapportClassement, InterlocuteurScripte]:
    interlocuteur = InterlocuteurScripte(reponses)
    rapport = classer_et_questionner(MOIS, ledger, contexte, interlocuteur)
    assert interlocuteur.restantes == 0, "réponses non consommées"
    return rapport, interlocuteur


def _classement(rapport: RapportClassement, transaction: Transaction) -> tuple[object, ...]:
    c = next(c for c in rapport.resultat.classements if c.transaction_id == transaction.id)
    return (c.nature, c.sous_categorie, c.detail, c.origine)


def test_virement_sortant_hors_budget_sans_regle(ledger: Ledger, contexte: Contexte) -> None:
    """RG-10 : hors budget, sans sous-catégorie."""
    virement = _virement("-50.00")
    importer_synthetiques(ledger, MOIS, [virement])
    rapport, interlocuteur = _jouer(ledger, contexte, ["6", "n"])
    assert _classement(rapport, virement) == (Nature.HORS_BUDGET, None, None, "decision")
    assert "Question 1/1 — Virement (RG-06)" in interlocuteur.texte()
    assert "WEB MADAME TIERS" in interlocuteur.texte()  # terminal local : libellé affiché
    assert ledger.regles() == []


def test_virement_entrant_remboursement_rg09(ledger: Ledger, contexte: Contexte) -> None:
    """RG-09 : un virement entrant « Remboursement » reçoit une sous-catégorie."""
    virement = _virement("77.90", "IGESTION-BCAC")
    importer_synthetiques(ledger, MOIS, [virement])
    rapport, _ = _jouer(ledger, contexte, ["5", "10", "3", "n"])
    assert _classement(rapport, virement) == (
        Nature.REMBOURSEMENT,
        "sante.optique",
        None,
        "decision",
    )


def test_dotation_puis_regle_sur_plage(ledger: Ledger, contexte: Contexte) -> None:
    virement = _virement("-550.00", "WEB VIREMENT DU MOIS")
    importer_synthetiques(ledger, MOIS, [virement])
    rapport, _ = _jouer(ledger, contexte, ["1", "2", "o", "p", "abc", "600-700", "500-600"])
    assert _classement(rapport, virement) == (
        Nature.DOTATION,
        None,
        "dotations.perso_conjoint",
        "decision",
    )
    [regle] = ledger.regles()
    assert (regle.type, regle.critere, regle.valeur) == (
        TypeRegle.VIREMENT,
        Critere.LIBELLE_EXACT,
        "WEB VIREMENT DU MOIS",
    )
    assert (regle.montant_min, regle.montant_max) == (Decimal("-600"), Decimal("-500"))
    assert ledger.decisions()[virement.id].regle_id == regle.id


def test_sous_categorie_a_deux_niveaux_avec_retour(ledger: Ledger, contexte: Contexte) -> None:
    operation = fabriquer_transaction(
        "OFFRE INCONNUE", "-12.00", type_operation="Prélèvement", ics="FR00ZZZ000000"
    )
    importer_synthetiques(ledger, MOIS, [operation])
    # Dépense, Courses, retour aux catégories, Abonnements, Logiciels & IA ; règle sur ICS
    rapport, interlocuteur = _jouer(ledger, contexte, ["1", "5", "a", "4", "4", "o", ""])
    assert _classement(rapport, operation)[:2] == (Nature.DEPENSE, "abonnements.logiciels_ia")
    [regle] = ledger.regles()
    assert (regle.type, regle.critere, regle.valeur, regle.montant_min) == (
        TypeRegle.PRELEVEMENT,
        Critere.ICS,
        "FR00ZZZ000000",
        None,
    )
    assert "Sous-catégorie de Courses ?" in interlocuteur.texte()


def test_annuler_revient_au_choix_de_nature(ledger: Ledger, contexte: Contexte) -> None:
    virement = _virement("-50.00")
    importer_synthetiques(ledger, MOIS, [virement])
    rapport, _ = _jouer(ledger, contexte, ["1", "a", "3", "n"])  # dotation annulée, complément
    assert _classement(rapport, virement)[0] is Nature.COMPLEMENT


def test_passer_laisse_la_question_ouverte(ledger: Ledger, contexte: Contexte) -> None:
    virement = _virement("-50.00")
    importer_synthetiques(ledger, MOIS, [virement])
    rapport, _ = _jouer(ledger, contexte, ["s"])
    assert rapport.bilan is not None and rapport.bilan.passees == 1
    assert len(rapport.questions) == 1
    assert ledger.decisions() == {}
    rapport, _ = _jouer(ledger, contexte, ["6", "n"])
    assert rapport.questions == []


def test_quitter_apres_trois_reponses(ledger: Ledger, contexte: Contexte) -> None:
    """Les trois réponses sont conservées ; le passage suivant ne pose que les restantes."""
    virements = [_virement(f"-{10 + i}.00", f"WEB TIERS {i}", jour=1 + i) for i in range(5)]
    importer_synthetiques(ledger, MOIS, virements)
    rapport, _ = _jouer(ledger, contexte, ["6", "n"] * 3 + ["q"])
    assert rapport.bilan is not None
    assert (rapport.bilan.repondues, rapport.bilan.interrompu) == (3, True)
    assert set(ledger.decisions()) == {v.id for v in virements[:3]}
    assert [q.transaction.id for q in rapport.questions] == [v.id for v in virements[3:]]

    rapport, interlocuteur = _jouer(ledger, contexte, ["6", "n"] * 2)
    assert "Question 1/2" in interlocuteur.texte()
    assert rapport.questions == []


def test_rg04_question_salaire(ledger: Ledger, contexte: Contexte) -> None:
    """RG-04 : deux choix, mois retenu, nature inchangée, aucune règle, question non reposée."""
    salaire = fabriquer_transaction(
        "GROUPAMA GAN VIE VIRT.APTS", "2729.05", date_operation=date(2026, 6, 10)
    )
    importer_synthetiques(ledger, MOIS, [salaire])
    rapport, interlocuteur = _jouer(ledger, contexte, ["1"])
    classement = rapport.resultat.classements[0]
    assert (classement.nature, classement.mois_rattachement, classement.origine) == (
        Nature.REVENU,
        "2026-05",
        "contexte",
    )
    assert "Question 1/1 — Salaire (RG-04)" in interlocuteur.texte()
    assert not [i for i in interlocuteur.invites if i.startswith("En faire une règle")]
    assert ledger.regles() == []
    assert not [a for a in rapport.resultat.alertes if a.code == "RG-04"]
    rapport, _ = _jouer(ledger, contexte, [])
    assert rapport.questions == []


def test_rg14_paypal_sans_proposition_de_regle(ledger: Ledger, contexte: Contexte) -> None:
    paypal = fabriquer_transaction("PAYPAL", "-25.00", type_operation="Prélèvement", ics=ICS_PAYPAL)
    importer_synthetiques(ledger, MOIS, [paypal])
    _, interlocuteur = _jouer(ledger, contexte, ["1", "8", "4"])
    assert not [i for i in interlocuteur.invites if i.startswith("En faire une règle")]
    assert "Opération (RG-14)" in interlocuteur.texte()


def test_toujours_demander(ledger: Ledger, contexte: Contexte) -> None:
    virement = _virement("-50.00", "VIR SANS MOTIF")
    importer_synthetiques(ledger, MOIS, [virement])
    _jouer(ledger, contexte, ["6", "t"])
    [regle] = ledger.regles()
    assert (regle.type, regle.valeur, regle.montant_min) == (
        TypeRegle.TOUJOURS_DEMANDER,
        "VIR SANS MOTIF",
        None,
    )


def test_conflit_puis_non(ledger: Ledger, contexte: Contexte) -> None:
    """Une règle refusée affiche le motif puis revient à la proposition."""
    virement = _virement("-50.00", "VIR CONFLIT")
    importer_synthetiques(ledger, MOIS, [virement])
    ledger.ajouter_regle(
        Regle(
            type=TypeRegle.VIREMENT,
            source=Source.CA_JOINT,
            critere=Critere.LIBELLE_EXACT,
            valeur="VIR CONFLIT",
            montant_min=Decimal("-100"),
            montant_max=Decimal("-60"),
            nature=Nature.COMPLEMENT,
        )
    )
    # Plage 40-70 : même spécificité, recouvrement, résultat différent
    rapport, interlocuteur = _jouer(ledger, contexte, ["6", "o", "p", "40-70", "n"])
    assert "règle refusée : conflit avec la règle n° 1" in interlocuteur.texte()
    assert len(ledger.regles()) == 1
    assert _classement(rapport, virement)[0] is Nature.HORS_BUDGET


def test_decision_prime_sur_une_regle_creee_ensuite(ledger: Ledger, contexte: Contexte) -> None:
    virement = _virement("-50.00", "VIR TIERS")
    importer_synthetiques(ledger, MOIS, [virement])
    _jouer(ledger, contexte, ["6", "n"])
    ledger.ajouter_regle(
        Regle(
            type=TypeRegle.VIREMENT,
            source=Source.CA_JOINT,
            critere=Critere.LIBELLE_EXACT,
            valeur="VIR TIERS",
            montant_min=Decimal("-50"),
            montant_max=Decimal("-50"),
            nature=Nature.COMPLEMENT,
        )
    )
    rapport, _ = _jouer(ledger, contexte, [])
    assert _classement(rapport, virement)[0] is Nature.HORS_BUDGET


def test_scenario_epuise_ou_invalide(ledger: Ledger, contexte: Contexte) -> None:
    importer_synthetiques(ledger, MOIS, [_virement("-50.00")])
    with pytest.raises(ScenarioError, match="épuisé"):
        classer_et_questionner(MOIS, ledger, contexte, InterlocuteurScripte(["6"]))
    with pytest.raises(ScenarioError, match="invalide"):
        classer_et_questionner(MOIS, ledger, contexte, InterlocuteurScripte(["9"]))


def test_terminal_ressaisie() -> None:
    saisies = iter(["9", " X ", "2"])
    console = Console(record=True, width=100)
    terminal = InterlocuteurTerminal(console, lire=lambda _invite: next(saisies))
    assert terminal.demander(">", ["1", "2", "s"]) == "2"
    assert console.export_text().count("Réponse invalide") == 2
