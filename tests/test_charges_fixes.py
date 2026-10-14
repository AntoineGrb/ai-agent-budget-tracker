"""RG-18 : affectation et rapprochement des charges fixes (spec 05)."""

from decimal import Decimal

import pytest

from budget.classement import RapprochementCharge, ResultatClassement, classer_mois
from budget.config import Contexte, charger_contexte
from budget.errors import ControleError
from budget.models import Transaction
from tests.conftest import EXEMPLE_CONTEXTE, fabriquer_transaction, transactions_fixtures


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


def _rapprochements(resultat: ResultatClassement) -> dict[str, RapprochementCharge]:
    return {r.nom: r for r in resultat.rapprochements}


def _details(resultat: ResultatClassement) -> dict[str, str | None]:
    return {c.transaction_id: c.detail for c in resultat.classements}


def _pret(montant: str) -> Transaction:
    return fabriquer_transaction(
        "XXXXXXXXXXX ECHEANCE 05/06/26", montant, type_operation="Remboursement de prêt"
    )


@pytest.mark.parametrize(
    ("mois", "principal", "secondaire"),
    [("2026-06", "1787.46", "121.89"), ("2026-07", "1471.00", "105.84")],
)
def test_rg18_prets_apparies_par_montant(
    contexte: Contexte, mois: str, principal: str, secondaire: str
) -> None:
    """RG-18 : les deux prêts, de critères identiques, sont appariés par montant croissant."""
    resultat = classer_mois(mois, transactions_fixtures(mois), contexte)
    rapprochements = _rapprochements(resultat)
    assert rapprochements["pret_principal"].reel == Decimal(principal)
    assert rapprochements["pret_secondaire"].reel == Decimal(secondaire)
    assert not [a for a in resultat.alertes if "opérations attendues" in a.message]


def test_rg18_juin_tout_au_montant_attendu(contexte: Contexte) -> None:
    resultat = classer_mois("2026-06", transactions_fixtures("2026-06"), contexte)
    assert len(resultat.rapprochements) == 8
    assert all(r.reel == r.attendu and not r.hors_seuil for r in resultat.rapprochements)
    assert sum(r.attendu for r in resultat.rapprochements) == Decimal("2797.25")
    assert not [a for a in resultat.alertes if a.code == "RG-18"]


def test_rg18_juillet_ecarts_et_seuils(contexte: Contexte) -> None:
    """RG-18 : écarts, seuil général de 10 % et tolérance propre de 25 % pour la garde."""
    resultat = classer_mois("2026-07", transactions_fixtures("2026-07"), contexte)
    rapprochements = _rapprochements(resultat)
    attendus = {
        "pret_principal": ("1471.00", "-316.46", "-17.70", "10", True),
        "pret_secondaire": ("105.84", "-16.05", "-13.17", "10", True),
        "garde_enfant": ("735.65", "67.41", "10.09", "25", False),
    }
    for nom, (reel, ecart, ecart_pct, seuil, hors_seuil) in attendus.items():
        r = rapprochements[nom]
        assert (r.reel, r.ecart, r.ecart_pct, r.seuil_pct, r.hors_seuil) == (
            Decimal(reel),
            Decimal(ecart),
            Decimal(ecart_pct),
            Decimal(seuil),
            hors_seuil,
        ), nom
    assert all(r.reel is not None for r in resultat.rapprochements)
    assert [a.message for a in resultat.alertes if a.code == "RG-18"] == [
        "charge pret_principal : 1 471,00 € au lieu de 1 787,46 € (-17,70 %, seuil 10 %)",
        "charge pret_secondaire : 105,84 € au lieu de 121,89 € (-13,17 %, seuil 10 %)",
    ]


@pytest.mark.parametrize(("montant", "hors_seuil"), [("-17.05", False), ("-17.06", True)])
def test_rg18_seuil_en_inegalite_stricte(
    contexte: Contexte, montant: str, hors_seuil: bool
) -> None:
    """RG-18 : +10,00 % pile reste dans le seuil de 10 %."""
    offre = fabriquer_transaction("OFFRE PREMIUM", montant, type_operation="Cotisation")
    rapprochement = _rapprochements(classer_mois("2026-06", [offre], contexte))["banque_offre"]
    assert rapprochement.hors_seuil is hors_seuil


def test_rg18_charge_absente(contexte: Contexte) -> None:
    """RG-18 : une charge sans opération dans le mois produit une alerte « absente »."""
    offre = fabriquer_transaction("OFFRE PREMIUM", "-15.50", type_operation="Cotisation")
    resultat = classer_mois("2026-06", [offre], contexte)
    energie = _rapprochements(resultat)["energie"]
    assert (energie.reel, energie.ecart, energie.ecart_pct, energie.transaction_ids) == (
        None,
        None,
        None,
        (),
    )
    assert not energie.hors_seuil
    messages = [a.message for a in resultat.alertes if a.code == "RG-18"]
    assert len(messages) == 7
    assert (
        "charge energie attendue absente : 68,92 € attendus, aucune opération du mois" in messages
    )


def test_rg18_charge_a_critere_unique_somme_ses_candidates(contexte: Contexte) -> None:
    premiere = fabriquer_transaction("OFFRE PREMIUM", "-10.00", type_operation="Cotisation")
    seconde = fabriquer_transaction("OFFRE PREMIUM", "-5.50", type_operation="Cotisation")
    resultat = classer_mois("2026-06", [premiere, seconde], contexte)
    offre = _rapprochements(resultat)["banque_offre"]
    assert offre.reel == Decimal("15.50")
    assert set(offre.transaction_ids) == {premiere.id, seconde.id}


def test_rg18_plus_de_candidates_que_de_charges(contexte: Contexte) -> None:
    """RG-18 : trois prêts pour deux charges : plus proche montant, alerte, surplus non classé."""
    gros, petit, intrus = _pret("-1780.00"), _pret("-120.00"), _pret("-600.00")
    resultat = classer_mois("2026-06", [gros, petit, intrus], contexte)
    details = _details(resultat)
    assert details[gros.id] == "charges_fixes.pret_principal"
    assert details[petit.id] == "charges_fixes.pret_secondaire"
    assert details[intrus.id] is None
    assert (
        "charges pret_secondaire, pret_principal : 2 opérations attendues, 3 trouvées ; "
        "appariement au plus proche montant attendu, les opérations en trop restent non classées"
        in [a.message for a in resultat.alertes]
    )


def test_rg18_moins_de_candidates_que_de_charges(contexte: Contexte) -> None:
    """RG-18 : un seul prêt : affecté à la charge la plus proche, l'autre est absente."""
    seul = _pret("-1471.00")
    resultat = classer_mois("2026-06", [seul], contexte)
    assert _details(resultat)[seul.id] == "charges_fixes.pret_principal"
    rapprochements = _rapprochements(resultat)
    assert rapprochements["pret_secondaire"].reel is None
    messages = [a.message for a in resultat.alertes if a.code == "RG-18"]
    assert any("2 opérations attendues, 1 trouvées" in m for m in messages)
    assert any(m.startswith("charge pret_secondaire attendue absente") for m in messages)


def test_rg18_credit_jamais_candidat(contexte: Contexte) -> None:
    avoir = fabriquer_transaction("OFFRE PREMIUM", "15.50", type_operation="Cotisation")
    resultat = classer_mois("2026-06", [avoir], contexte)
    assert _details(resultat)[avoir.id] is None


def test_rg18_contexte_ambigu(contexte: Contexte) -> None:
    """Un débit qui correspond à deux charges fixes de critères différents lève `ControleError`."""
    autre = contexte.charges_fixes[-1].model_copy(
        update={"nom": "offre", "libelle_contient": "OFFRE"}
    )
    ambigu = contexte.model_copy(update={"charges_fixes": (*contexte.charges_fixes, autre)})
    offre = fabriquer_transaction("OFFRE PREMIUM", "-15.50", type_operation="Cotisation")
    with pytest.raises(
        ControleError, match=r"RG-18 : .*plusieurs charges fixes .*banque_offre, offre"
    ):
        classer_mois("2026-06", [offre], ambigu)
