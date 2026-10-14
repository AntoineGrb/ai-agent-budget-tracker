"""RG-15 : tags exceptionnels, candidates, récurrence et réponses (spec 07)."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.classement import Classement, OrigineClassement
from budget.config import Contexte, charger_contexte
from budget.ledger import Ledger
from budget.models import Nature, Source, Transaction
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.exceptionnels import candidates_exceptionnelles
from budget.questions.execution import bloc_exceptionnels, classer
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, importer_synthetiques

SEUIL = Decimal("500")


def _depense(
    libelle: str, montant: str, *, ics: str | None = None, jour: date = date(2026, 6, 10)
) -> Transaction:
    return fabriquer_transaction(libelle, montant, ics=ics, date_operation=jour)


def _classement(
    t: Transaction, nature: Nature = Nature.DEPENSE, detail: str | None = None
) -> Classement:
    return Classement.pour(t, nature, OrigineClassement.DECISION, detail)


def test_candidates() -> None:
    gros = _depense("TRAVAUX CUISINE", "-1200.00")
    petit = _depense("COURSES", "-499.99")
    fixe = _depense("PRET", "-1787.46")
    hors_budget = _depense("DEPOT", "-1806.00")
    deja = _depense("MEUBLE", "-600.00")
    transactions = [gros, petit, fixe, hors_budget, deja]
    classements = {
        gros.id: _classement(gros),
        petit.id: _classement(petit),
        fixe.id: _classement(fixe, detail="charges_fixes.pret_principal"),
        hors_budget.id: _classement(hors_budget, Nature.HORS_BUDGET),
        deja.id: _classement(deja),
    }
    assert candidates_exceptionnelles(transactions, classements, [], {deja.id: False}, SEUIL) == [
        gros
    ]


def test_depense_recurrente_non_proposee() -> None:
    """Même ICS au-dessus du seuil le mois précédent : récurrente, donc pas exceptionnelle."""
    juin = _depense("ASSURANCE ANNUELLE", "-650.00", ics="FR00ZZZ111111")
    mai = _depense("ASSURANCE ANNUELLE", "-640.00", ics="FR00ZZZ111111", jour=date(2026, 5, 10))
    mai_petit = _depense("AUTRE", "-640.00", ics="FR00ZZZ222222", jour=date(2026, 5, 10))
    classements = {juin.id: _classement(juin)}
    assert candidates_exceptionnelles([juin], classements, [mai], {}, SEUIL) == []
    assert candidates_exceptionnelles([juin], classements, [mai_petit], {}, SEUIL) == [juin]
    sous_le_seuil = mai.model_copy(update={"montant": Decimal("-100.00")})
    assert candidates_exceptionnelles([juin], classements, [sous_le_seuil], {}, SEUIL) == [juin]


def test_recurrence_par_cle_marchand_pour_un_paiement_carte() -> None:
    juin = fabriquer_transaction("IKEA VILLIERS 1234567", "-700.00", source=Source.CA_CB)
    mai = fabriquer_transaction(
        "IKEA VILLIERS 7654321", "-900.00", source=Source.CA_CB, date_operation=date(2026, 5, 2)
    )
    classements = {juin.id: _classement(juin)}
    assert candidates_exceptionnelles([juin], classements, [mai], {}, SEUIL) == []


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "budget.sqlite") as ledger:
        importer_synthetiques(
            ledger,
            "2026-06",
            [
                fabriquer_transaction("OLIVERSTORE 1234567", "-883.16", source=Source.CA_CB),
                fabriquer_transaction("SUNNY INCH 7654321", "-593.08", source=Source.CA_CB),
            ],
        )
        yield ledger


def test_reponses_enregistrees_et_jamais_reposees(ledger: Ledger, contexte: Contexte) -> None:
    interlocuteur = InterlocuteurScripte(["o", "s"])
    bilan = bloc_exceptionnels("2026-06", ledger, contexte, interlocuteur)
    assert bilan is not None and (bilan.a_poser, bilan.oui, bilan.passees) == (2, 1, 1)
    assert "Exceptionnel 1/2 — Dépense d'au moins 500 € (RG-15)" in interlocuteur.texte()
    etat = classer("2026-06", ledger, contexte)
    [restante] = etat.exceptionnelles  # la question passée revient, pas la réponse donnée
    assert restante.montant == Decimal("-593.08")
    marquees = [c for c in etat.resultat.classements if c.exceptionnel]
    assert len(marquees) == 1

    bilan = bloc_exceptionnels("2026-06", ledger, contexte, InterlocuteurScripte(["n"]))
    assert bilan is not None and bilan.non == 1
    assert bloc_exceptionnels("2026-06", ledger, contexte, InterlocuteurScripte([])) is None
    assert ledger.exceptionnels() == {
        next(t.id for t in etat.transactions if t.montant == Decimal("-883.16")): True,
        restante.id: False,
    }


def test_quitter(ledger: Ledger, contexte: Contexte) -> None:
    bilan = bloc_exceptionnels("2026-06", ledger, contexte, InterlocuteurScripte(["q"]))
    assert bilan is not None and bilan.interrompu
    assert ledger.exceptionnels() == {}
