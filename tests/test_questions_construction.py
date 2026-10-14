"""Sélection et ordre des questions (spec 06)."""

from datetime import date
from decimal import Decimal

import pytest

from budget.classement import classer_mois
from budget.config import Contexte, charger_contexte
from budget.models import Nature, Source, Transaction
from budget.questions.construction import construire_questions
from budget.questions.modeles import Question, TypeQuestion
from budget.regles import ICS_PAYPAL, Critere, Memoire, Regle, TypeRegle
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _questions(
    transactions: list[Transaction], contexte: Contexte, regles: list[Regle] | None = None
) -> list[Question]:
    memoire = Memoire(regles=regles or [])
    resultat = classer_mois("2026-06", transactions, contexte, memoire)
    return construire_questions(transactions, resultat, contexte, memoire.regles)


def _virement(montant: str, jour: int, libelle: str = "WEB MADAME TIERS") -> Transaction:
    type_operation = "Virement émis" if montant.startswith("-") else "Virement en votre faveur"
    return fabriquer_transaction(
        libelle, montant, date_operation=date(2026, 6, jour), type_operation=type_operation
    )


def test_ordre_virements_salaires_operations(contexte: Contexte) -> None:
    """Virements, puis salaires, puis opérations, chronologiques, joint avant TR à date égale."""
    operation = fabriquer_transaction(
        "PAYPAL",
        "-4.80",
        date_operation=date(2026, 6, 1),
        type_operation="Prélèvement",
        ics=ICS_PAYPAL,
    )
    salaire = fabriquer_transaction(
        "GROUPAMA GAN VIE VIRT.APTS", "2729.05", date_operation=date(2026, 6, 10)
    )
    tr = fabriquer_transaction(
        "INCOMING TRANSFER",
        "100.00",
        source=Source.TR,
        type_operation="Virement",
        date_operation=date(2026, 6, 20),
    )
    joint = _virement("-40.00", 20)
    plus_tot = _virement("-30.00", 5)
    questions = _questions([operation, salaire, tr, joint, plus_tot], contexte)
    assert [q.transaction.id for q in questions] == [
        plus_tot.id,
        joint.id,
        tr.id,
        salaire.id,
        operation.id,
    ]
    assert [q.type for q in questions] == [
        TypeQuestion.VIREMENT,
        TypeQuestion.VIREMENT,
        TypeQuestion.VIREMENT,
        TypeQuestion.SALAIRE,
        TypeQuestion.OPERATION,
    ]
    assert questions[-1].motif == "RG-14" and not questions[-1].regle_proposable


def test_rg04_question_salaire(contexte: Contexte) -> None:
    """RG-04 : salaire reçu le 10/06 sans période : question à deux mois, sans règle."""
    salaire = fabriquer_transaction(
        "GROUPAMA GAN VIE VIRT.APTS", "2729.05", date_operation=date(2026, 6, 10)
    )
    [question] = _questions([salaire], contexte)
    assert (question.type, question.motif, question.nom_salaire) == (
        TypeQuestion.SALAIRE,
        "RG-04",
        "salaire_1",
    )
    assert question.mois_possibles == ("2026-05", "2026-06")
    assert not question.regle_proposable


def test_une_seule_question_par_paire(contexte: Contexte) -> None:
    joint = _virement("-250.00", 15, "VIR INST VERS PRENOM NOM - VELO")
    tr = fabriquer_transaction(
        "INCOMING TRANSFER",
        "250.00",
        source=Source.TR,
        type_operation="Virement",
        date_operation=date(2026, 6, 15),
    )
    [question] = _questions([tr, joint], contexte)
    assert (question.transaction, question.contrepartie) == (joint, tr)


def test_paiements_carte_jamais_questionnes(contexte: Contexte) -> None:
    transactions = list(transactions_fixtures("2026-06"))
    questions = _questions(transactions, contexte)
    sources = {q.transaction.source for q in questions}
    assert Source.CA_CB not in sources
    assert all(
        q.transaction.type_operation != "Avoir"
        for q in questions
        if q.transaction.source is Source.TR
    )


def test_rg06_virement_couvert_par_une_regle_non_questionne(contexte: Contexte) -> None:
    """RG-06 : seul un virement non couvert produit une question."""
    couvert, autre = _virement("-7.00", 1, "WEB SPOTIFION"), _virement("-8.00", 1, "WEB SPOTIFION")
    regle = Regle(
        id=1,
        type=TypeRegle.VIREMENT,
        source=Source.CA_JOINT,
        critere=Critere.LIBELLE_EXACT,
        valeur="WEB SPOTIFION",
        montant_min=Decimal("-7.00"),
        montant_max=Decimal("-7.00"),
        nature=Nature.HORS_BUDGET,
    )
    questions = _questions([couvert, autre], contexte, [regle])
    assert [q.transaction for q in questions] == [autre]


def test_regle_toujours_demander_sur_un_virement(contexte: Contexte) -> None:
    virement = _virement("-50.00", 4, "VIR INST VERS M NOM")
    regle = Regle(
        id=1,
        type=TypeRegle.TOUJOURS_DEMANDER,
        source=Source.CA_JOINT,
        critere=Critere.LIBELLE_EXACT,
        valeur="VIR INST VERS M NOM",
    )
    [question] = _questions([virement], contexte, [regle])
    assert (question.motif, question.regle_proposable) == ("RG-14", False)
