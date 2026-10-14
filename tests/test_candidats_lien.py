"""RG-07 : dépenses candidates pour un lien à une dépense (spec 06)."""

from datetime import date, timedelta
from decimal import Decimal

from budget.classement import Classement, OrigineClassement, classer_mois
from budget.config import charger_contexte
from budget.models import Nature, Source, Transaction
from budget.questions.candidats import (
    FENETRE_LIEN_JOURS,
    TAILLE_PAGE_LIENS,
    EtatLiens,
    candidats_lien,
    page,
)
from budget.questions.construction import construire_questions
from budget.questions.modeles import Question, TypeQuestion
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures

JOUR = date(2026, 6, 15)


def _question(montant: str = "-250.00") -> Question:
    virement = fabriquer_transaction(
        "VIR INST VERS PRENOM NOM - VELO",
        montant,
        date_operation=JOUR,
        type_operation="Virement émis",
    )
    return Question(type=TypeQuestion.VIREMENT, motif="RG-06", transaction=virement)


def _depense(montant: str, decalage: int = 0, source: Source = Source.CA_CB) -> Transaction:
    return fabriquer_transaction(
        "MARCHAND", montant, source=source, date_operation=JOUR + timedelta(days=decalage)
    )


def _classe(t: Transaction, nature: Nature | None, detail: str | None = None) -> Classement:
    return Classement.pour(t, nature, OrigineClassement.REGLE_FIXE, detail)


def test_rg07_velo_decathlon_en_premiere_page() -> None:
    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    transactions = transactions_fixtures("2026-06")
    resultat = classer_mois("2026-06", transactions, contexte)
    [velo] = [
        q for q in construire_questions(transactions, resultat, contexte, []) if q.contrepartie
    ]
    etat = EtatLiens(transactions, {c.transaction_id: c for c in resultat.classements})
    premiere_page = page(candidats_lien(velo, etat), 0)
    assert len(premiere_page) == TAILLE_PAGE_LIENS
    assert (Source.TR, Decimal("-409.97")) in [(t.source, t.montant) for t in premiere_page]
    # Ni les opérations de la paire, ni les charges fixes
    ids = {t.id for t in candidats_lien(velo, etat)}
    assert velo.transaction.id not in ids and velo.contrepartie is not None
    assert velo.contrepartie.id not in ids
    fixes = {
        c.transaction_id
        for c in resultat.classements
        if (c.detail or "").startswith("charges_fixes.")
    }
    assert not ids & fixes


def test_rg07_fenetre_ratio_et_tri() -> None:
    question = _question()
    proche = _depense("-125.00", 2)
    loin = _depense("-300.00", -(FENETRE_LIEN_JOURS - 1))
    hors_fenetre = _depense("-300.00", FENETRE_LIEN_JOURS)
    trop_petit = _depense("-124.99", 1)
    entree = _depense("200.00", 1)
    meme_jour = _depense("-130.00", 0, Source.TR)
    etat = EtatLiens([proche, loin, hors_fenetre, trop_petit, entree, meme_jour], {})
    assert candidats_lien(question, etat) == [meme_jour, proche, loin]


def test_rg07_exclusions_par_nature_et_liens_existants() -> None:
    question = _question()
    depense, non_classee, epargne, fixe, liee = (_depense("-200.00", i) for i in range(5))
    classements = {
        depense.id: _classe(depense, Nature.DEPENSE),
        non_classee.id: _classe(non_classee, None),
        epargne.id: _classe(epargne, Nature.EPARGNE),
        fixe.id: _classe(fixe, Nature.DEPENSE, "charges_fixes.energie"),
        liee.id: _classe(liee, Nature.DEPENSE),
    }
    etat = EtatLiens([depense, non_classee, epargne, fixe, liee], classements, {liee.id})
    assert candidats_lien(question, etat) == [depense, non_classee]


def test_rg07_mois_precedent_inclus_si_fourni() -> None:
    question = _question()
    mai = fabriquer_transaction(
        "MARCHAND", "-300.00", source=Source.CA_CB, date_operation=date(2026, 5, 20)
    )
    assert candidats_lien(question, EtatLiens([mai], {})) == [mai]


def test_rg07_pagination() -> None:
    question = _question()
    depenses = [_depense("-200.00", i % 20) for i in range(23)]
    candidats = candidats_lien(question, EtatLiens(depenses, {}))
    assert [len(page(candidats, n)) for n in range(4)] == [10, 10, 3, 0]
