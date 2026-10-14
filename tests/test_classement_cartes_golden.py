"""Paiements carte de juin puis juillet sur les fixtures : groupes, catégoriseur, lot, RG-15."""

from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.agents.categoriseur import CategoriseurLLM
from budget.categories import SousCategorie
from budget.classement.cartes import est_paiement_carte
from budget.classement.modeles import Classement, OrigineClassement
from budget.config import Contexte, charger_contexte
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.libelles import cle_marchand
from budget.models import Compte, Source, Transaction
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import (
    bloc_exceptionnels,
    bloc_validation,
    classer,
    classer_et_questionner,
)
from budget.regles import TypeRegle
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE
from tests.scenarios.juin import AppelsCategoriseur, juin_complet, modele_propositions

AUJOURD_HUI = date(2026, 10, 14)


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
        yield ledger


def _cartes(transactions: list[Transaction]) -> list[Transaction]:
    return [t for t in transactions if est_paiement_carte(t)]


def _par_id(rapport_ou_etat: object) -> dict[str, Classement]:
    resultat = rapport_ou_etat.resultat  # type: ignore[attr-defined]
    return {c.transaction_id: c for c in resultat.classements}


def test_juin_avant_validation(ledger: Ledger, contexte: Contexte) -> None:
    """RG-13 automatique, Amazon à compléter, 62 groupes couvrant 100 opérations."""
    etat = classer("2026-06", ledger, contexte)
    classements = _par_id(etat)
    cartes = _cartes(etat.transactions)
    assert len(cartes) == 105
    assert Counter(t.source for t in cartes) == {Source.CA_CB: 48, Source.TR: 57}

    rg13 = sorted(
        (abs(t.montant), classements[t.id].sous_categorie, classements[t.id].origine)
        for t in cartes
        if classements[t.id].detail == "RG-13"
    )
    assert rg13 == [
        (Decimal("19.99"), SousCategorie.ACHATS_DIVERS, OrigineClassement.REGLE_FIXE),
        (Decimal("49.72"), SousCategorie.ACHATS_DIVERS, OrigineClassement.REGLE_FIXE),
    ]
    assert [(cle_marchand(t.libelle_normalise), t.montant) for t in etat.amazon] == [
        ("AMAZON PAYMENTS", Decimal("-218.68")),
        ("AMAZON EU SARL", Decimal("-89.99")),
        ("AMAZON PAYMENTS", Decimal("-78.38")),
    ]
    assert len(etat.groupes) == 62
    assert Counter(g.compte for g in etat.groupes) == {Compte.JOINT: 23, Compte.PERSO: 39}
    assert sum(len(g.transactions) for g in etat.groupes) == 100
    exemples = {(g.cle, len(g.transactions), g.total) for g in etat.groupes}
    assert {
        ("CARREFOUR CITY SAINT MAUR D", 8, Decimal("-128.62")),
        ("RBCPJ PARIS", 6, Decimal("-188.00")),
        ("GOOGLE*GOOGLE PLAY APP", 5, Decimal("-124.37")),
        ("UBER *EATS", 4, Decimal("-71.20")),
    } <= exemples
    # La nature n'est jamais modifiée : dépense en sortie, remboursement en entrée
    assert all(
        classements[t.id].nature == ("depense" if t.montant < 0 else "remboursement")
        for t in cartes
    )


def test_juin_scenario_complet(ledger: Ledger, contexte: Contexte) -> None:
    appels = AppelsCategoriseur()
    rapport = juin_complet(ledger, contexte, appels)

    # Un seul appel, avec exactement les 62 clés, uniques et triées ; jamais Amazon (RG-13)
    assert len(appels.prompts) == 1
    libelles = appels.prompts[0].splitlines()
    assert len(libelles) == 62 and libelles == sorted(set(libelles))
    assert not [libelle for libelle in libelles if libelle.startswith("AMAZON")]

    lot = rapport.bilan_lot
    assert lot is not None
    assert (lot.lignes, lot.proposes, lot.valides, lot.en_attente) == (65, 62, 65, 0)
    assert (lot.corriges, lot.regles_creees) == (4, 62)
    regles = ledger.regles()
    assert len(regles) == 66
    assert Counter(r.type for r in regles)[TypeRegle.MARCHAND_CARTE] == 62

    classements = _par_id(rapport)
    cartes = _cartes(rapport.transactions)
    assert all(classements[t.id].sous_categorie is not None for t in cartes)
    assert rapport.groupes == [] and rapport.amazon == [] and rapport.questions == []

    # Correction : RELAY PARIS en Loisirs › Sorties, et non la proposition Achats › Divers
    relay = next(t for t in cartes if cle_marchand(t.libelle_normalise) == "RELAY PARIS")
    assert classements[relay.id].sous_categorie is SousCategorie.LOISIRS_SORTIES

    # RG-13 : décisions sans règle
    amazon = {
        t.montant: classements[t.id]
        for t in cartes
        if cle_marchand(t.libelle_normalise).startswith("AMAZON")
    }
    assert amazon[Decimal("-78.38")].origine is OrigineClassement.DECISION
    assert amazon[Decimal("-78.38")].sous_categorie is SousCategorie.ENFANTS_VETEMENTS_EQUIPEMENT
    assert not [r for r in regles if r.valeur.startswith("AMAZON")]

    # RG-09 : avoirs et achats du même marchand, même sous-catégorie, net nul
    for source, cle in ((Source.CA_CB, "MANOMANO PARIS"), (Source.TR, "ZALANDO PAYMENTS")):
        operations = [
            t for t in cartes if t.source is source and cle_marchand(t.libelle_normalise) == cle
        ]
        assert len(operations) == 2 and sum(t.montant for t in operations) == 0
        assert len({classements[t.id].sous_categorie for t in operations}) == 1

    # RG-15 : OLIVERSTORE -883,16 € marquée exceptionnelle, seule candidate
    assert rapport.bilan_exceptionnels is not None
    assert (rapport.bilan_exceptionnels.a_poser, rapport.bilan_exceptionnels.oui) == (1, 1)
    [exceptionnel] = [t for t in rapport.transactions if classements[t.id].exceptionnel]
    assert (exceptionnel.montant, exceptionnel.date_operation) == (
        Decimal("-883.16"),
        date(2026, 6, 1),
    )


def test_juin_second_classify_sans_appel_ni_question(ledger: Ledger, contexte: Contexte) -> None:
    premier = juin_complet(ledger, contexte, AppelsCategoriseur())
    appels = AppelsCategoriseur()
    interlocuteur = InterlocuteurScripte([])
    second = classer_et_questionner(
        "2026-06", ledger, contexte, interlocuteur, CategoriseurLLM(modele_propositions(appels))
    )
    assert appels.prompts == [] and interlocuteur.invites == []
    assert (second.bilan, second.bilan_lot, second.bilan_exceptionnels) == (None, None, None)
    assert second.resultat == premier.resultat


def test_juillet_apres_juin(ledger: Ledger, contexte: Contexte) -> None:
    juin_complet(ledger, contexte, AppelsCategoriseur())
    importer_mois("2026-07", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    etat = classer("2026-07", ledger, contexte)
    classements = _par_id(etat)
    cartes = _cartes(etat.transactions)
    assert len(cartes) == 138

    # Règles marchand de juin réutilisées, avoir MILIBOO compris (RG-09)
    par_regle = [t for t in cartes if classements[t.id].origine is OrigineClassement.REGLE]
    assert len(par_regle) == 38
    assert len({(t.source, cle_marchand(t.libelle_normalise)) for t in par_regle}) == 20
    [miliboo] = [t for t in par_regle if cle_marchand(t.libelle_normalise) == "MILIBOO CHAVANOD"]
    assert miliboo.montant == Decimal("258.99")
    assert classements[miliboo.id].nature == "remboursement"
    assert classements[miliboo.id].sous_categorie is SousCategorie.ACHATS_MAISON_DECO

    rg13 = [t.montant for t in cartes if classements[t.id].detail == "RG-13"]
    assert len(rg13) == 8 and {Decimal("9.00"), Decimal("39.99")} <= set(rg13)
    assert [(t.source, t.date_operation, t.montant) for t in etat.amazon] == [
        (Source.CA_CB, date(2026, 6, 27), Decimal("-57.80")),
        (Source.TR, date(2026, 7, 25), Decimal("-51.98")),
    ]
    assert len(etat.groupes) == 71
    assert Counter(g.compte for g in etat.groupes) == {Compte.JOINT: 51, Compte.PERSO: 20}

    # Bloc 2 seul, sans répondre au bloc 1 : 71 libellés envoyés, Amazon saisis en 72 et 73
    appels = AppelsCategoriseur()
    interlocuteur = InterlocuteurScripte(["72", "8", "4", "73", "8", "4", ""])
    lot = bloc_validation(
        "2026-07", ledger, contexte, interlocuteur, CategoriseurLLM(modele_propositions(appels))
    )
    assert lot is not None and (lot.lignes, lot.valides, lot.en_attente) == (73, 73, 0)
    assert len(appels.prompts) == 1 and len(appels.prompts[0].splitlines()) == 71

    # RG-15 : MIDAS -1 173,86 € ; les candidates hors carte dépendent du bloc 1, non répondu ici
    etat = classer("2026-07", ledger, contexte)
    assert [(cle_marchand(t.libelle_normalise), t.montant) for t in etat.exceptionnelles] == [
        ("MIDAS CHAMPIGNY SUR MA", Decimal("-1173.86"))
    ]
    interlocuteur = InterlocuteurScripte(["n"])
    bilan = bloc_exceptionnels("2026-07", ledger, contexte, interlocuteur)
    assert bilan is not None and bilan.non == 1
    assert classer("2026-07", ledger, contexte).exceptionnelles == []
