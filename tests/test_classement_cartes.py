"""Sous-catégorie des paiements carte : décision > RG-13 > règle marchand > attente (spec 07)."""

from decimal import Decimal

import pytest

from budget.categories import SousCategorie
from budget.classement import Classement, OrigineClassement, classer_mois
from budget.classement.cartes import en_attente, grouper
from budget.config import Contexte, charger_contexte
from budget.models import Compte, Nature, Source, Transaction
from budget.regles import Critere, Decision, Memoire, Regle, TypeRegle
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _carte(libelle: str, montant: str, source: Source = Source.CA_CB) -> Transaction:
    type_operation = "Avoir" if source is Source.TR else None
    return fabriquer_transaction(libelle, montant, source=source, type_operation=type_operation)


def _regle(cle: str, sous_categorie: SousCategorie, source: Source = Source.CA_CB) -> Regle:
    return Regle(
        id=12,
        type=TypeRegle.MARCHAND_CARTE,
        source=source,
        critere=Critere.LIBELLE_EXACT,
        valeur=cle,
        sous_categorie=sous_categorie,
    )


def _classer(
    transactions: list[Transaction], contexte: Contexte, memoire: Memoire
) -> dict[str, Classement]:
    resultat = classer_mois("2026-06", transactions, contexte, memoire)
    return {c.transaction_id: c for c in resultat.classements}


def test_regle_marchand_puis_attente(contexte: Contexte) -> None:
    connu = _carte("MONOPRIX 1357 STMAU2042885/", "-12.00")
    avoir = _carte("MONOPRIX 1357 STMAU2042885/", "4.50")
    inconnu = _carte("PETIT COMMERCE", "-8.00")
    memoire = Memoire(regles=[_regle("MONOPRIX", SousCategorie.COURSES_SUPERMARCHE)])
    classes = _classer([connu, avoir, inconnu], contexte, memoire)

    assert (classes[connu.id].nature, classes[connu.id].sous_categorie) == (
        Nature.DEPENSE,
        SousCategorie.COURSES_SUPERMARCHE,
    )
    assert (classes[connu.id].origine, classes[connu.id].regle_id) == (OrigineClassement.REGLE, 12)
    assert classes[connu.id].detail == "regles.12"
    # RG-09 : l'avoir reçoit la sous-catégorie du marchand, sa nature reste un remboursement
    assert (classes[avoir.id].nature, classes[avoir.id].sous_categorie) == (
        Nature.REMBOURSEMENT,
        SousCategorie.COURSES_SUPERMARCHE,
    )
    assert (classes[inconnu.id].sous_categorie, classes[inconnu.id].detail) == (
        None,
        "paiement_carte",
    )


def test_rg13_amazon_sous_le_seuil(contexte: Contexte) -> None:
    """RG-13 : sous 50 €, achats divers sans question, avoirs compris ; à 50 €, en attente."""
    petit = _carte("AMAZON PAYMENTS PARIS2441535/", "-49.72")
    avoir = _carte("AMAZON PAYMENTS PARIS2441535/", "9.00")
    seuil = _carte("AMZN MKTP FR 1234567", "-50.00")
    classes = _classer([petit, avoir, seuil], contexte, Memoire())
    for t in (petit, avoir):
        assert (classes[t.id].sous_categorie, classes[t.id].detail, classes[t.id].origine) == (
            SousCategorie.ACHATS_DIVERS,
            "RG-13",
            OrigineClassement.REGLE_FIXE,
        )
    assert classes[avoir.id].nature is Nature.REMBOURSEMENT
    assert classes[seuil.id].sous_categorie is None


def test_decision_prime_sur_rg13_et_sur_la_regle(contexte: Contexte) -> None:
    amazon = _carte("AMAZON PAYMENTS PARIS2441535/", "-20.00")
    monoprix = _carte("MONOPRIX 1357", "-12.00")
    decisions = {
        t.id: Decision(
            transaction_id=t.id,
            question_id=1,
            nature=Nature.DEPENSE,
            sous_categorie=SousCategorie.LOISIRS_SORTIES,
            detail="RG-13",
        )
        for t in (amazon, monoprix)
    }
    memoire = Memoire(
        decisions=decisions, regles=[_regle("MONOPRIX", SousCategorie.COURSES_SUPERMARCHE)]
    )
    classes = _classer([amazon, monoprix], contexte, memoire)
    for t in (amazon, monoprix):
        assert (classes[t.id].sous_categorie, classes[t.id].origine, classes[t.id].nature) == (
            SousCategorie.LOISIRS_SORTIES,
            OrigineClassement.DECISION,
            Nature.DEPENSE,
        )


def test_tag_exceptionnel_applique(contexte: Contexte) -> None:
    gros = _carte("OLIVERSTORE VILLENEUVE-D'", "-883.16")
    classes = _classer([gros], contexte, Memoire(exceptionnels={gros.id: True}))
    assert classes[gros.id].exceptionnel
    classes = _classer([gros], contexte, Memoire(exceptionnels={gros.id: False}))
    assert not classes[gros.id].exceptionnel


def test_groupes_par_source_et_cle(contexte: Contexte) -> None:
    joint = [_carte("UBER *EATS HELP.UBER.COM", "-30.80"), _carte("MONOPRIX 1357", "-10.00")]
    joint.append(_carte("MONOPRIX 2468 AUTRE/", "-33.43"))
    perso = [_carte("UBER *EATS 1234", "-71.20", Source.TR)]
    amazon = [
        _carte("AMAZON PAYMENTS PARIS2441535/", "-78.38"),
        _carte("AMAZON PAYMENTS PARIS2441535/", "-10.00"),
    ]
    transactions = [*joint, *perso, *amazon]
    classes = _classer(transactions, contexte, Memoire())
    groupes, a_completer = grouper(en_attente(transactions, classes), Decimal("50"))
    assert [(g.compte, g.cle, len(g.transactions), g.total) for g in groupes] == [
        (Compte.JOINT, "MONOPRIX", 2, Decimal("-43.43")),
        (Compte.JOINT, "UBER *EATS HELP.UBER.COM", 1, Decimal("-30.80")),
        (Compte.PERSO, "UBER *EATS", 1, Decimal("-71.20")),
    ]
    assert a_completer == [amazon[0]]  # l'Amazon sous le seuil est déjà classé (RG-13)
