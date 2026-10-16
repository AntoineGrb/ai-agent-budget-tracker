"""Agent synthèse avec FunctionModel : confidentialité, validateur, relances, échec, empreinte."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from budget.agents.confidentialite import contient_iban
from budget.agents.synthese import RELANCES_SORTIE, CommentateurLLM, synthese
from budget.classement.cartes import est_paiement_carte
from budget.config import Contexte, charger_contexte
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.ledger.migrations import MIGRATIONS, VERSION_COURANTE, _instructions
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import classer_et_questionner
from budget.restitution.synthese import produire_synthese
from tests.conftest import DOSSIER_FIXTURES, EXEMPLE_CONTEXTE
from tests.scenarios.reference import AppelsCategoriseur, derouler
from tests.test_agregats import assert_sans_float

JUIN = "2026-06"
CORRECT = ("Joint : 7 566,46 €, surtout Logement (3 289,26 €).", ["Courses : +27,36 € de budget."])


@dataclass
class Appels:
    """Ce que le FunctionModel a reçu : ce qui serait parti au LLM."""

    messages: list[list[ModelMessage]] = field(default_factory=list)

    @property
    def prompts(self) -> list[str]:
        """Un prompt par run : la première requête de chaque run, sans les relances."""
        return [
            str(part.content)
            for messages in self.messages
            if len(messages) == 1 and isinstance(messages[0], ModelRequest)
            for part in messages[0].parts
            if isinstance(part, UserPromptPart)
        ]

    def relances(self) -> list[str]:
        return [
            str(part.content)
            for message in self.messages[-1]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]


def commentateur(
    appels: Appels, reponses: Callable[[int], tuple[str, list[str]]] = lambda _: CORRECT
) -> CommentateurLLM:
    """FunctionModel qui renvoie `reponses(n)` à la n-ième requête d'un run."""

    def repondre(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        appels.messages.append(messages)
        resume, points = reponses(sum(isinstance(m, ModelResponse) for m in messages))
        outil = info.output_tools[0].name
        args = {"resume": resume, "points_cles": points}
        return ModelResponse(parts=[ToolCallPart(tool_name=outil, args=args)])

    return CommentateurLLM(FunctionModel(repondre))


def _classer_juin(chemin: Path, contexte: Contexte) -> Ledger:
    ledger = Ledger(chemin)
    importer_mois(JUIN, DOSSIER_FIXTURES, ledger, date(2026, 10, 15))
    derouler(JUIN, ledger, contexte, AppelsCategoriseur())
    return ledger


@pytest.fixture(scope="module")
def contexte() -> Contexte:
    return charger_contexte(EXEMPLE_CONTEXTE)


@pytest.fixture(scope="module")
def juin(tmp_path_factory: pytest.TempPathFactory, contexte: Contexte) -> Iterator[Ledger]:
    with _classer_juin(tmp_path_factory.mktemp("synthese") / "budget.sqlite", contexte) as ledger:
        yield ledger


def test_prompt_sans_libelle_ni_iban_ni_float(juin: Ledger, contexte: Contexte) -> None:
    """Cadrage §11 : seules les clés marchand masquées des paiements carte partent au LLM."""
    appels = Appels()
    resultat = produire_synthese(JUIN, juin, contexte, commentateur(appels), regenerer=True)
    assert resultat.commentaire is not None
    [prompt] = appels.prompts
    for t in juin.transactions(JUIN):
        if not est_paiement_carte(t):
            assert t.libelle not in prompt and t.libelle_normalise not in prompt, t.position
    [requete] = [m for m in appels.messages[0] if isinstance(m, ModelRequest)]
    assert not contient_iban(prompt) and not contient_iban(requete.instructions or "")
    donnees = json.loads(prompt)
    assert_sans_float(donnees)
    assert donnees["agregats"]["joint"]["total"] == "7566.46"
    assert donnees["budgets"] == {
        "joint": {"courses": "600.00", "restos": "150.00"},
        "perso": {"restos": "200.00"},
    }
    marchands = {
        g["marchand"]
        for vue in ("joint", "perso")
        for g in donnees["agregats"][vue]["plus_grosses"]
    }
    assert marchands == {
        None,
        "OLIVERSTORE VILLENEUVE-D'",
        "SUNNY INCH ST JEAN DE VEDAS",
        "DECATHLON",
        "GOOGLE*GOOGLE PLAY APP",
        "LUNETTESPRTOUS",
        "KINEPOLIS MEGA",
    }


def test_instructions_et_reglages(juin: Ledger, contexte: Contexte) -> None:
    appels = Appels()
    produire_synthese(JUIN, juin, contexte, commentateur(appels), regenerer=True)
    [requete] = [m for m in appels.messages[0] if isinstance(m, ModelRequest)]
    instructions = requete.instructions or ""
    assert "Ne cite que des montants présents dans les données" in instructions
    assert "les deux vues ne s'additionnent jamais" in instructions
    assert synthese.name == "synthese"


def test_montant_absent_provoque_une_relance(juin: Ledger, contexte: Contexte) -> None:
    appels = Appels()
    faux = ("Joint : 7 500,00 € et 15 % de plus.", ["Logement : 3 289,26 €.", "Restos : 98 €."])
    resultat = produire_synthese(
        JUIN,
        juin,
        contexte,
        commentateur(appels, lambda n: faux if n == 0 else CORRECT),
        regenerer=True,
    )
    assert resultat.commentaire is not None and resultat.commentaire.resume == CORRECT[0]
    assert resultat.avertissement is None
    [relance] = appels.relances()
    assert "Montants absents des données : 7 500,00 €, 98 €." in relance
    assert "3 289,26" not in relance and "15 %" not in relance


def test_relances_epuisees_synthese_sans_commentaire(juin: Ledger, contexte: Contexte) -> None:
    appels = Appels()
    toujours_faux = commentateur(appels, lambda _: ("Total : 4 321,00 €.", ["x"]))
    resultat = produire_synthese(JUIN, juin, contexte, toujours_faux, regenerer=True)
    assert len(appels.messages) == RELANCES_SORTIE + 1
    assert resultat.commentaire is None
    assert resultat.avertissement is not None
    assert resultat.avertissement.startswith("agent synthèse en échec (UnexpectedModelBehavior")
    enregistree = juin.synthese(JUIN)
    assert enregistree is not None and enregistree.commentaire is None
    assert enregistree.agregats == resultat.agregats and enregistree.modele is None


def test_erreur_api_synthese_sans_commentaire(juin: Ledger, contexte: Contexte) -> None:
    def modele(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        corps = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "workspace"},
        }
        raise ModelHTTPError(400, "x", corps)

    resultat = produire_synthese(
        JUIN, juin, contexte, CommentateurLLM(FunctionModel(modele)), regenerer=True
    )
    assert (
        resultat.avertissement == "agent synthèse en échec (ModelHTTPError : HTTP 400, workspace)"
    )
    assert resultat.commentaire is None


def test_empreinte_un_seul_appel_tant_que_rien_ne_change(
    tmp_path: Path, contexte: Contexte
) -> None:
    appels = Appels()
    stable = commentateur(appels, lambda _: ("Joint : 7 566,46 €.", ["Perso : 1 322,86 €."]))
    with _classer_juin(tmp_path / "budget.sqlite", contexte) as ledger:
        premiere = produire_synthese(JUIN, ledger, contexte, stable)
        seconde = produire_synthese(JUIN, ledger, contexte, stable)
        assert len(appels.prompts) == 1
        assert (premiere.reutilise, seconde.reutilise) == (False, True)
        assert seconde.commentaire == premiere.commentaire

        produire_synthese(JUIN, ledger, contexte, stable, regenerer=True)
        assert len(appels.prompts) == 2

        # Nouvelle réponse : le prêt de 121,89 €, classé par la règle du prêt principal, passe de
        # Logement à Divers
        [regle] = [r for r in ledger.regles() if r.valeur == "XXXXXXXXXXX ECHEANCE"]
        assert regle.id is not None
        ledger.desactiver_regle(regle.id)
        interlocuteur = InterlocuteurScripte(["10", "2", "n"])
        classer_et_questionner(JUIN, ledger, contexte, interlocuteur)
        assert interlocuteur.restantes == 0
        troisieme = produire_synthese(JUIN, ledger, contexte, stable)
        assert len(appels.messages) == len(appels.prompts) == 3
        assert troisieme.commentaire is not None and not troisieme.reutilise
        divers = troisieme.agregats.joint.categories[-1]
        assert (divers.categorie.value, str(divers.montant)) == ("divers", "121.89")


def test_migration_2_sur_une_base_en_version_1_sans_perte(tmp_path: Path) -> None:
    chemin = tmp_path / "budget.sqlite"
    connexion = sqlite3.connect(chemin, isolation_level=None)
    for instruction in _instructions(MIGRATIONS[0].sql):
        connexion.execute(instruction)
    connexion.execute("PRAGMA user_version = 1")
    connexion.execute(
        "INSERT INTO imports (source, mois, fichier, empreinte, nb_operations, importe_le) "
        "VALUES ('tr', '2026-06', 'TR_202606.pdf', 'e', 0, '2026-10-16T09:00:00+02:00')"
    )
    connexion.close()
    with Ledger(chemin) as ledger:
        assert ledger.version == VERSION_COURANTE == 2
        assert ledger.mois_importes() == ["2026-06"]
        assert ledger.synthese("2026-06") is None
