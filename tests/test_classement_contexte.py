"""Classement par le contexte : RG-03, RG-04, RG-05, RG-08, RG-17, ordre, ambiguïté (spec 05)."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from budget.classement import Classement, ResultatClassement, classer_mois
from budget.config import Contexte, Dotation, charger_contexte
from budget.errors import ControleError
from budget.models import Nature, Source, Transaction
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures

MOIS = "2026-06"


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _classer(transactions: Sequence[Transaction], contexte: Contexte) -> ResultatClassement:
    return classer_mois(MOIS, transactions, contexte)


def _classement(resultat: ResultatClassement, transaction: Transaction) -> Classement:
    return next(c for c in resultat.classements if c.transaction_id == transaction.id)


def test_rg03_debits_differes_neutralises(contexte: Contexte) -> None:
    """RG-03 : les deux lignes de débit différé sont neutralisées ; le net des cartes les égale."""
    transactions = transactions_fixtures(MOIS)
    resultat = _classer(transactions, contexte)
    par_id = {t.id: t for t in transactions}
    neutralisees = [par_id[c.transaction_id] for c in resultat.classements if c.detail == "RG-03"]
    assert len(neutralisees) == 2
    assert all(t.source is Source.CA_JOINT for t in neutralisees)
    assert all(
        _classement(resultat, t).nature is Nature.NEUTRALISE
        and _classement(resultat, t).origine == "regle_fixe"
        for t in neutralisees
    )
    net_cartes = sum(t.montant for t in transactions if t.source is Source.CA_CB)
    assert net_cartes == sum(t.montant for t in neutralisees) == Decimal("-3527.17")


def test_rg03_prelevement_carte_sans_motif_non_neutralise(contexte: Contexte) -> None:
    autre = fabriquer_transaction("FRAIS CARTE", type_operation="Prélèvement carte")
    assert _classement(_classer([autre], contexte), autre).nature is None


def test_rg04_salaire_hors_fenetre_sans_periode(contexte: Contexte) -> None:
    """RG-04 : salaire reçu le 10 sans période : REVENU, mois de paie vide, alerte, pas d'arrêt."""
    salaire = fabriquer_transaction(
        "GROUPAMA GAN VIE VIRT.APTS", "2729.05", date_operation=date(2026, 6, 10)
    )
    charge = fabriquer_transaction("OFFRE PREMIUM", "-15.50", type_operation="Cotisation")
    resultat = _classer([salaire, charge], contexte)
    classement = _classement(resultat, salaire)
    assert classement.nature is Nature.REVENU
    assert classement.mois_rattachement is None
    assert classement.detail == "salaires.salaire_1"
    assert _classement(resultat, charge).nature is Nature.DEPENSE
    alertes = [a for a in resultat.alertes if a.code == "RG-04"]
    assert [a.transaction_id for a in alertes] == [salaire.id]
    assert alertes[0].message == (
        "salaire salaire_1 reçu le 10/06/2026, 2 729,05 € : mois de paie à confirmer "
        "(2026-05 ou 2026-06), question à l'étape 6"
    )


def test_rg04_mois_de_paie_des_salaires_de_juin(contexte: Contexte) -> None:
    transactions = transactions_fixtures(MOIS)
    resultat = _classer(transactions, contexte)
    montants = {t.id: t.montant for t in transactions}
    par_mois: dict[str | None, Decimal] = {}
    for c in resultat.classements:
        if c.nature is Nature.REVENU and c.detail and c.detail.startswith("salaires."):
            par_mois[c.mois_rattachement] = (
                par_mois.get(c.mois_rattachement, Decimal(0)) + montants[c.transaction_id]
            )
    assert par_mois == {"2026-06": Decimal("5255.47"), "2026-05": Decimal("2303.31")}


def test_rg05_dotation_au_bon_libelle_et_montant(contexte: Contexte) -> None:
    """RG-05 : libellé et montant déclarés : dotation sans question."""
    dotation = fabriquer_transaction("VIR INST vers PRENOM NOM - Mensuel", "-500.00")
    classement = _classement(_classer([dotation], contexte), dotation)
    assert (classement.nature, classement.detail) == (Nature.DOTATION, "dotations.perso")
    assert classement.origine == "contexte"


def test_rg05_mauvais_montant_non_classe_et_alerte(contexte: Contexte) -> None:
    """RG-05 : bon libellé, mauvais montant : non classée, alerte qui le précise."""
    resultat = _classer(transactions_fixtures(MOIS), contexte)
    alertes = [a for a in resultat.alertes if a.code == "RG-05"]
    assert [a.message for a in alertes] == [
        "dotation perso_conjoint non trouvée : libellé présent le 30/06/2026 avec 550,00 € "
        "au lieu de 500,00 €"
    ]
    assert alertes[0].transaction_id is not None
    assert _classement(resultat, _par_id(alertes[0].transaction_id)).nature is None


def _par_id(identifiant: str) -> Transaction:
    return next(t for t in transactions_fixtures(MOIS) if t.id == identifiant)


def test_rg05_dotation_absente(contexte: Contexte) -> None:
    resultat = _classer([fabriquer_transaction()], contexte)
    messages = [a.message for a in resultat.alertes if a.code == "RG-05"]
    assert len(messages) == 3
    assert messages[0] == (
        "dotation perso non trouvée : aucun virement du joint ne porte son libellé "
        "(500,00 € attendus)"
    )


def test_rg05_credit_au_libelle_de_dotation_ignore(contexte: Contexte) -> None:
    credit = fabriquer_transaction("VIR INST vers PRENOM NOM - Mensuel", "500.00")
    assert _classement(_classer([credit], contexte), credit).nature is None


def test_rg08_reprise_et_paiement_finance(contexte: Contexte) -> None:
    """RG-08 : reprise et paiement financé sont neutralisés ensemble, sans alerte."""
    reprise = fabriquer_transaction("CHARGES TRIM MONSIEUR NOM AN - CHARGES TRIM", "1071.00")
    paiement = fabriquer_transaction("SOPAGI", "-1071.04", ics="FR48ZZZ829660")
    resultat = _classer([reprise, paiement], contexte)
    assert _classement(resultat, reprise).detail == "provision.reprise"
    assert _classement(resultat, paiement).detail == "provision.copropriete"
    assert {_classement(resultat, t).nature for t in (reprise, paiement)} == {Nature.PROVISION}
    assert not [a for a in resultat.alertes if a.code == "RG-08"]


def test_rg08_reprise_seule(contexte: Contexte) -> None:
    """RG-08 : une reprise sans paiement financé dans le mois produit une alerte."""
    reprise = fabriquer_transaction("CHARGES TRIM MONSIEUR NOM AN - CHARGES TRIM", "1071.00")
    resultat = _classer([reprise], contexte)
    assert _classement(resultat, reprise).nature is Nature.PROVISION
    alertes = [a for a in resultat.alertes if a.code == "RG-08"]
    assert [a.transaction_id for a in alertes] == [reprise.id]
    assert alertes[0].message.startswith("reprise de provision sans paiement financé dans le mois")


def test_rg08_paiement_seul(contexte: Contexte) -> None:
    paiement = fabriquer_transaction("SOPAGI", "-1071.04", ics="FR48ZZZ829660")
    resultat = _classer([paiement], contexte)
    alertes = [a for a in resultat.alertes if a.code == "RG-08"]
    assert [a.transaction_id for a in alertes] == [paiement.id]
    assert "(provision.copropriete) sans reprise dans le mois" in alertes[0].message


def test_rg17_cartes_inactive_et_inconnue(contexte: Contexte) -> None:
    """RG-17 : alerte pour une carte inactive ou inconnue, sans effet sur le classement."""
    inactive = fabriquer_transaction(
        "CARTE X9817 CREATE", "81.00", carte="X9817", type_operation="Avoir"
    )
    inconnue = fabriquer_transaction("MARCHAND", "-12.00", source=Source.CA_CB, carte="X0000")
    active = fabriquer_transaction("MARCHAND", "-8.00", source=Source.CA_CB, carte="X1091")
    resultat = _classer([inactive, inconnue, active], contexte)
    alertes = {a.transaction_id: a.message for a in resultat.alertes if a.code == "RG-17"}
    assert set(alertes) == {inactive.id, inconnue.id}
    assert alertes[inactive.id].startswith("carte X9817 inactive : ")
    assert alertes[inconnue.id].startswith("carte X0000 inconnue du contexte : ")
    assert _classement(resultat, inactive).nature is None  # Avoir du joint : pas un paiement carte
    assert _classement(resultat, inconnue).nature is Nature.DEPENSE


def test_rg17_juin(contexte: Contexte) -> None:
    resultat = _classer(transactions_fixtures(MOIS), contexte)
    messages = [a.message for a in resultat.alertes if a.code == "RG-17"]
    assert len(messages) == 2
    assert {m.split(", ", 2)[2] for m in messages} == {
        "16/06/2026, 81,00 €",
        "03/06/2026, 106,80 €",
    }


def test_ordre_dotation_avant_charge_fixe(contexte: Contexte) -> None:
    """Le premier classement applicable l'emporte : un débit déjà dotation n'est pas une charge."""
    charge = contexte.charges_fixes[-1].model_copy(update={"libelle_contient": "Mensuel"})
    modifie = contexte.model_copy(update={"charges_fixes": (*contexte.charges_fixes[:-1], charge)})
    dotation = fabriquer_transaction("VIR INST vers PRENOM NOM - Mensuel", "-500.00")
    assert _classement(_classer([dotation], modifie), dotation).nature is Nature.DOTATION


def test_contexte_ambigu_deux_dotations(contexte: Contexte) -> None:
    """Un libellé correspondant à deux dotations lève `ControleError`."""
    doublon = Dotation(nom="doublon", libelle_contient="Mensuel", montant=Decimal(500))
    ambigu = contexte.model_copy(update={"dotations": (*contexte.dotations, doublon)})
    dotation = fabriquer_transaction("VIR INST vers PRENOM NOM - Mensuel", "-500.00")
    with pytest.raises(
        ControleError, match=r"RG-05 : .*plusieurs dotations .*\(perso, doublon\)"
    ) as erreur:
        _classer([dotation], ambigu)
    assert "Mensuel" not in str(erreur.value)


def test_transaction_d_un_autre_mois_refusee(contexte: Contexte) -> None:
    juillet = fabriquer_transaction(date_operation=date(2026, 7, 1))
    with pytest.raises(ControleError, match="1 transaction"):
        _classer([juillet], contexte)
