"""Bout en bout sur les fixtures de juin et juillet 2026 : import, questions, lot carte, dépenses.

Réponses de référence : `tests/scenarios/reference.py`. Les totaux sont figés après vérification à
la main des principaux postes (cadrage §11).
"""

import csv
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.agents.categoriseur import CategoriseurLLM
from budget.categories import SousCategorie
from budget.classement import Classement, OrigineClassement
from budget.classement.cartes import est_paiement_carte
from budget.config import Contexte, charger_contexte
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.libelles import cle_marchand
from budget.models import Transaction
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.regles import TypeRegle
from budget.restitution.export_csv import exporter_mois
from budget.restitution.operations import depenses_par_categorie
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE
from tests.scenarios.reference import AppelsCategoriseur, derouler, modele_propositions

DEPENSES = {
    "2026-06": {
        ("abonnements", "joint"): "190.49",
        ("abonnements", "perso"): "135.61",
        ("achats", "joint"): "2282.22",
        ("achats", "perso"): "30.20",
        ("courses", "joint"): "627.36",
        ("courses", "perso"): "30.75",
        ("enfants", "joint"): "974.11",
        ("logement", "joint"): "3289.26",
        ("loisirs", "joint"): "46.66",
        ("loisirs", "perso"): "685.69",
        ("restos", "joint"): "97.36",
        ("restos", "perso"): "206.05",
        ("sante", "perso"): "77.90",
        ("transport", "joint"): "59.00",
        ("transport", "perso"): "156.66",
    },
    "2026-07": {
        ("abonnements", "joint"): "84.61",
        ("abonnements", "perso"): "85.57",
        ("achats", "joint"): "341.83",
        ("achats", "perso"): "126.80",
        ("courses", "joint"): "757.92",
        ("courses", "perso"): "39.97",
        ("divers", "joint"): "618.45",
        ("enfants", "joint"): "735.65",
        ("logement", "joint"): "4416.17",
        ("loisirs", "joint"): "443.30",
        ("loisirs", "perso"): "394.18",
        ("restos", "joint"): "445.58",
        ("restos", "perso"): "262.17",
        ("sante", "joint"): "150.99",
        ("transport", "joint"): "1409.77",
        ("transport", "perso"): "245.80",
    },
}


@dataclass
class Mois:
    rapport: RapportClassement
    appels: AppelsCategoriseur

    @property
    def par_id(self) -> dict[str, Transaction]:
        return {t.id: t for t in self.rapport.transactions}

    @property
    def classements(self) -> dict[str, Classement]:
        return {c.transaction_id: c for c in self.rapport.resultat.classements}

    def par_detail(self, detail: str) -> list[Transaction]:
        par_id = self.par_id
        return [
            par_id[c.transaction_id]
            for c in self.rapport.resultat.classements
            if c.detail == detail
        ]


@dataclass
class Parcours:
    ledger: Ledger
    contexte: Contexte
    juin: Mois
    juillet: Mois
    regles_apres_juin: int


@pytest.fixture(scope="module")
def parcours(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Parcours]:
    contexte = charger_contexte(EXEMPLE_CONTEXTE)
    chemin = tmp_path_factory.mktemp("bout_en_bout") / "budget.sqlite"
    with Ledger(chemin) as ledger:
        for mois in ("2026-06", "2026-07"):
            importer_mois(mois, DOSSIER_FIXTURES, ledger, date(2026, 10, 15))
        appels_juin = AppelsCategoriseur()
        juin = Mois(derouler("2026-06", ledger, contexte, appels_juin), appels_juin)
        regles_apres_juin = len(ledger.regles())
        appels_juillet = AppelsCategoriseur()
        juillet = Mois(derouler("2026-07", ledger, contexte, appels_juillet), appels_juillet)
        yield Parcours(ledger, contexte, juin, juillet, regles_apres_juin)


def test_seuls_debits_et_avoirs_carte_suivis_rg19(parcours: Parcours) -> None:
    juin = parcours.juin
    assert len(juin.rapport.transactions) == 159
    assert len(juin.rapport.resultat.classements) == 137
    for t in juin.rapport.transactions:
        assert (t.id in juin.classements) is (t.montant < 0 or est_paiement_carte(t))
    assert len(parcours.juillet.rapport.resultat.classements) == 169


def test_regles_fixes_rg03_rg11_rg13(parcours: Parcours) -> None:
    juin = parcours.juin
    differes = juin.par_detail("RG-03")
    assert sorted(t.montant for t in differes) == [Decimal("-3116.50"), Decimal("-410.67")]
    investissements = juin.par_detail("RG-11")
    assert len(investissements) == 7  # 6 exécutions d'ordre et un versement PEA
    assert sum(t.montant for t in investissements) == Decimal("-61.50")
    for detail in ("RG-03", "RG-11"):
        assert all(juin.classements[t.id].non_comptee for t in juin.par_detail(detail))
    amazon = juin.par_detail("RG-13")
    assert sorted(t.montant for t in amazon) == [Decimal("-49.72"), Decimal("-19.99")]
    assert {juin.classements[t.id].sous_categorie for t in amazon} == {SousCategorie.ACHATS_AUTRE}


def test_questions_de_juin_rg06_rg13_rg14(parcours: Parcours) -> None:
    bilan = parcours.juin.rapport.bilan
    assert bilan is not None
    assert (bilan.a_poser, bilan.posees, bilan.couvertes) == (26, 24, 2)
    assert (bilan.repondues, bilan.passees, bilan.regles_creees) == (24, 0, 15)
    assert not bilan.interrompu
    assert parcours.juin.rapport.questions == []
    # RG-13 : les 3 Amazon d'au moins 50 € sont des décisions, jamais des règles
    amazon = [
        c
        for c in parcours.juin.rapport.resultat.classements
        if cle_marchand(parcours.juin.par_id[c.transaction_id].libelle_normalise).startswith(
            "AMAZON"
        )
    ]
    assert Counter(c.origine for c in amazon) == {
        OrigineClassement.DECISION: 3,
        OrigineClassement.REGLE_FIXE: 2,
    }


def test_validation_en_lot_de_juin(parcours: Parcours) -> None:
    lot = parcours.juin.rapport.bilan_lot
    assert lot is not None
    assert (lot.lignes, lot.proposes, lot.valides, lot.corriges) == (62, 62, 62, 2)
    assert (lot.en_attente, lot.regles_creees, lot.avertissement) == (0, 62, None)
    # Un seul appel au catégoriseur, avec les 62 clés marchand de juin
    assert len(parcours.juin.appels.prompts) == 1
    assert len(parcours.juin.appels.prompts[0].splitlines()) == 62
    assert parcours.regles_apres_juin == 77


@pytest.mark.parametrize(
    ("mois", "marchand", "net"),
    [
        ("juin", "MANOMANO PARIS", "0.00"),
        ("juin", "ZALANDO PAYMENTS", "0.00"),
        ("juillet", "EG*ABRITEL", "100.00"),
    ],
)
def test_avoirs_carte_deduits_de_leur_categorie_rg09(
    parcours: Parcours, mois: str, marchand: str, net: str
) -> None:
    donnees: Mois = getattr(parcours, mois)
    operations = [
        t for t in donnees.rapport.transactions if cle_marchand(t.libelle_normalise) == marchand
    ]
    assert any(t.montant > 0 for t in operations)
    assert len({donnees.classements[t.id].sous_categorie for t in operations}) == 1
    assert sum(t.montant for t in operations) == Decimal(net)


@pytest.mark.parametrize("mois", ["2026-06", "2026-07"])
def test_depenses_par_categorie(parcours: Parcours, mois: str) -> None:
    donnees = parcours.juin if mois == "2026-06" else parcours.juillet
    assert donnees.rapport.resultat.en_attente == 0
    totaux = depenses_par_categorie(donnees.rapport.transactions, donnees.classements)
    assert {(c.value, compte.value): f"{m:.2f}" for (c, compte), m in totaux.items()} == (
        DEPENSES[mois]
    )


@pytest.mark.parametrize(
    ("mois", "nombre", "montant"), [("juin", 17, "-6149.67"), ("juillet", 14, "-6848.36")]
)
def test_non_comptees_rg10(parcours: Parcours, mois: str, nombre: int, montant: str) -> None:
    donnees: Mois = getattr(parcours, mois)
    non_comptees = [c for c in donnees.rapport.resultat.classements if c.non_comptee]
    assert len(non_comptees) == nombre
    assert sum(donnees.par_id[c.transaction_id].montant for c in non_comptees) == Decimal(montant)


def test_juillet_profite_des_regles_de_juin(parcours: Parcours) -> None:
    juillet = parcours.juillet
    bilan = juillet.rapport.bilan
    assert bilan is not None
    assert (bilan.a_poser, bilan.regles_creees) == (13, 2)
    par_regle = Counter(
        juillet.par_id[c.transaction_id].source.value
        for c in juillet.rapport.resultat.classements
        if c.origine is OrigineClassement.REGLE
        and not est_paiement_carte(juillet.par_id[c.transaction_id])
    )
    # Pajemploi, assurance emprunteur, deux prêts, Octopus, Free, Offre Premium, assurance
    # habitation, Spotifion sur le joint ; Orange sur TR
    assert par_regle == {"ca_joint": 9, "tr": 1}
    lot = juillet.rapport.bilan_lot
    assert lot is not None
    assert (lot.lignes, lot.valides, lot.regles_creees) == (71, 71, 71)
    regles = parcours.ledger.regles()
    assert len(regles) == 150
    assert sum(1 for r in regles if r.type is TypeRegle.MARCHAND_CARTE) == 133


def test_second_classify_sans_question_ni_appel(parcours: Parcours) -> None:
    """Une question tranchée ne revient jamais (cadrage §7)."""
    for mois, donnees in (("2026-06", parcours.juin), ("2026-07", parcours.juillet)):
        appels = AppelsCategoriseur()
        interlocuteur = InterlocuteurScripte([])
        rapport = classer_et_questionner(
            mois,
            parcours.ledger,
            parcours.contexte,
            interlocuteur,
            CategoriseurLLM(modele_propositions(appels)),
        )
        assert (rapport.bilan, rapport.bilan_lot, appels.prompts) == (None, None, [])
        assert rapport.resultat == donnees.rapport.resultat


def test_export_csv(parcours: Parcours, tmp_path: Path) -> None:
    chemin, nombre = exporter_mois(parcours.ledger, "2026-06", tmp_path)
    assert nombre == 137
    with chemin.open(encoding="utf-8-sig", newline="") as flux:
        lignes = list(csv.reader(flux, delimiter=";"))
    assert lignes[0][-2:] == ["Comptée", "Règle appliquée"]
    assert Counter(ligne[7] for ligne in lignes[1:]) == {"oui": 120, "non": 17}
    differe = next(ligne for ligne in lignes[1:] if ligne[4] == "-3116,50")
    assert (differe[2], differe[7], differe[8]) == ("Joint", "non", "RG-03")
