"""Bloc 2 : validation en lot avec InterlocuteurScripte (spec 07)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from budget.agents.categoriseur import CategoriseurIndisponible, CategoriseurLLM
from budget.categories import SousCategorie
from budget.config import Contexte, charger_contexte
from budget.ledger import Ledger
from budget.models import Source, Transaction
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import bloc_validation, classer
from budget.questions.validation_lot import ValidationLot
from budget.regles import Critere, Regle, TypeRegle
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, importer_synthetiques
from tests.scenarios.juin import AppelsCategoriseur, modele_propositions

MOIS = "2026-06"


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _carte(
    libelle: str, montant: str, jour: int = 10, source: Source = Source.CA_CB
) -> Transaction:
    type_operation = "Avoir" if source is Source.TR else None
    return fabriquer_transaction(
        libelle,
        montant,
        source=source,
        type_operation=type_operation,
        date_operation=date(2026, 6, jour),
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    """Juin synthétique : deux marchands du joint, un perso, un Amazon à compléter."""
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_synthetiques(
            ledger,
            MOIS,
            [
                _carte("MONOPRIX 1357 STMAU2042885/", "-43.43"),
                _carte("MONOPRIX 2468", "-12.00"),
                _carte("RELAY PARIS 1234", "-8.90"),
                _carte("UBER *EATS 98765", "-71.20", source=Source.TR),
                _carte("AMAZON PAYMENTS PARIS2441535/", "-78.38", jour=5),
            ],
        )
        yield ledger


def _llm(appels: AppelsCategoriseur) -> CategoriseurLLM:
    return CategoriseurLLM(modele_propositions(appels))


def test_entree_cree_une_regle_par_groupe(ledger: Ledger, contexte: Contexte) -> None:
    appels = AppelsCategoriseur()
    # Lignes : 1 MONOPRIX, 2 RELAY PARIS, 3 UBER *EATS (perso), 4 AMAZON (RG-13)
    interlocuteur = InterlocuteurScripte(["4", "8", "4", ""])
    bilan = bloc_validation(MOIS, ledger, contexte, interlocuteur, _llm(appels))
    assert bilan is not None
    assert (bilan.lignes, bilan.proposes, bilan.valides, bilan.regles_creees) == (4, 3, 4, 3)
    assert appels.prompts == ["MONOPRIX\nRELAY PARIS\nUBER *EATS"]
    regles = ledger.regles()
    assert [(r.type, r.source, r.valeur, r.sous_categorie) for r in regles] == [
        (TypeRegle.MARCHAND_CARTE, Source.CA_CB, "MONOPRIX", SousCategorie.COURSES_SUPERMARCHE),
        (TypeRegle.MARCHAND_CARTE, Source.CA_CB, "RELAY PARIS", SousCategorie.ACHATS_DIVERS),
        (TypeRegle.MARCHAND_CARTE, Source.TR, "UBER *EATS", SousCategorie.RESTOS_LIVRAISON),
    ]
    assert all(p.validee_le is not None and p.regle_id for p in ledger.propositions())
    # RG-13 : décision sans règle, question de motif RG-13
    [decision] = ledger.decisions().values()
    assert (decision.sous_categorie, decision.detail, decision.regle_id) == (
        SousCategorie.ACHATS_DIVERS,
        "RG-13",
        None,
    )
    motifs = ledger.lire_sql("SELECT type, motif FROM questions")
    assert motifs == [("operation", "RG-13")]
    etat = classer(MOIS, ledger, contexte)
    assert etat.groupes == [] and etat.amazon == []
    assert "Paiements carte à valider — 2026-06 (4 lignes)" in interlocuteur.texte()


def test_correction_prime_et_survit_a_q(ledger: Ledger, contexte: Contexte) -> None:
    appels = AppelsCategoriseur()
    # RELAY PARIS (ligne 2) corrigée en Loisirs › Sorties, puis q
    bilan = bloc_validation(
        MOIS, ledger, contexte, InterlocuteurScripte(["2", "7", "1", "q"]), _llm(appels)
    )
    assert bilan is not None and bilan.interrompu and bilan.corriges == 1
    assert ledger.regles() == []  # rien de validé
    attente = ledger.propositions_en_attente()
    assert attente[(Source.CA_CB, "RELAY PARIS")].retenue is SousCategorie.LOISIRS_SORTIES

    # Second passage : aucune proposition redemandée, correction conservée
    appels = AppelsCategoriseur()
    interlocuteur = InterlocuteurScripte([""])
    bilan = bloc_validation(MOIS, ledger, contexte, interlocuteur, _llm(appels))
    assert appels.prompts == []
    assert "Loisirs › Sorties (corrigée)" in interlocuteur.texte()
    assert bilan is not None and bilan.en_attente == 1  # Amazon non saisi
    relay = next(r for r in ledger.regles() if r.valeur == "RELAY PARIS")
    assert relay.sous_categorie is SousCategorie.LOISIRS_SORTIES


def test_mode_degrade_sans_llm(ledger: Ledger, contexte: Contexte) -> None:
    """Sans catégoriseur : lignes sans proposition, la saisie manuelle crée la règle."""
    interlocuteur = InterlocuteurScripte(["1", "5", "1", ""])
    bilan = bloc_validation(
        MOIS,
        ledger,
        contexte,
        interlocuteur,
        CategoriseurIndisponible("catégoriseur désactivé par --sans-llm"),
    )
    assert bilan is not None
    assert bilan.avertissement == "catégoriseur désactivé par --sans-llm"
    assert (bilan.proposes, bilan.valides, bilan.en_attente) == (0, 1, 3)
    assert [r.valeur for r in ledger.regles()] == ["MONOPRIX"]
    assert "Avertissement : catégoriseur désactivé par --sans-llm" in interlocuteur.texte()


def test_sortie_invalide_apres_relances_mode_degrade(ledger: Ledger, contexte: Contexte) -> None:
    appels = AppelsCategoriseur()
    vide = CategoriseurLLM(modele_propositions(appels, {}))
    bilan = bloc_validation(MOIS, ledger, contexte, InterlocuteurScripte(["q"]), vide)
    assert bilan is not None and bilan.avertissement is not None
    assert "UnexpectedModelBehavior" in bilan.avertissement
    assert ledger.propositions() == []


def test_regle_refusee_laisse_la_ligne_en_attente(ledger: Ledger, contexte: Contexte) -> None:
    etat = classer(MOIS, ledger, contexte)
    ValidationLot(InterlocuteurScripte(["q"]), ledger, MOIS, _llm(AppelsCategoriseur())).derouler(
        etat.groupes, etat.amazon
    )
    # Une règle concurrente apparaît entre le tableau et la validation
    ledger.ajouter_regle(
        Regle(
            type=TypeRegle.MARCHAND_CARTE,
            source=Source.CA_CB,
            critere=Critere.LIBELLE_EXACT,
            valeur="RELAY PARIS",
            sous_categorie=SousCategorie.LOISIRS_SORTIES,
        )
    )
    interlocuteur = InterlocuteurScripte([""])
    bilan = ValidationLot(interlocuteur, ledger, MOIS, _llm(AppelsCategoriseur())).derouler(
        etat.groupes, etat.amazon
    )
    assert "Ligne 2 laissée en attente : règle refusée : conflit" in interlocuteur.texte()
    assert (bilan.valides, bilan.en_attente) == (2, 2)
    assert ledger.propositions_en_attente()[(Source.CA_CB, "RELAY PARIS")].validee_le is None


def test_une_seule_proposition_en_attente_par_marchand(ledger: Ledger) -> None:
    propositions = {(Source.CA_CB, "MONOPRIX"): SousCategorie.COURSES_SUPERMARCHE}
    ledger.enregistrer_propositions(MOIS, propositions, "test")
    ledger.enregistrer_propositions(
        MOIS, {(Source.CA_CB, "MONOPRIX"): SousCategorie.ACHATS_DIVERS}, "test"
    )
    [proposition] = ledger.propositions()
    assert proposition.proposee is SousCategorie.COURSES_SUPERMARCHE
