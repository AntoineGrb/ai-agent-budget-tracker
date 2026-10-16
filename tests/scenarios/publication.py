"""Scénario de publication (spec 09) : juin et juillet classés par les réponses de référence,
synthèses commentées par un `FunctionModel`, instant fixe."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from budget.agents.synthese import CommentateurLLM
from budget.config import Contexte
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.publication.contenu import ContenuPublication, preparer_contenu
from tests.conftest import DOSSIER_FIXTURES
from tests.scenarios.reference import AppelsCategoriseur, derouler

MAINTENANT = datetime(2026, 10, 16, 18, 40)
MOIS = ("2026-06", "2026-07")

# Montants tous présents dans les agrégats : le validateur de l'agent synthèse les accepte
COMMENTAIRES = {
    "2026-06": (
        "Joint : 7 566,46 €, surtout Logement (3 289,26 €). Perso : 1 322,86 €.",
        ["Courses joint : +27,36 € de budget.", "Loisirs perso : 685,69 €."],
    ),
    "2026-07": (
        "Joint : 9 404,27 €, surtout Logement (4 416,17 €). Perso : 1 154,49 €.",
        ["Courses joint : +157,92 € de budget.", "Divers joint : 618,45 €, nouveau ce mois."],
    ),
}


@dataclass
class AppelsCommentateur:
    """Ce que l'agent synthèse a reçu : un prompt par appel."""

    prompts: list[str] = field(default_factory=list)


def commentateur_reference(appels: AppelsCommentateur | None = None) -> CommentateurLLM:
    """Répond au prompt de l'agent synthèse d'après le mois des agrégats reçus."""

    def repondre(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        if appels is not None:
            appels.prompts.append(prompt)
        resume, points = COMMENTAIRES[json.loads(prompt)["agregats"]["mois"]]
        args = {"resume": resume, "points_cles": points}
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])

    return CommentateurLLM(FunctionModel(repondre))


def preparer_ledger(chemin: Path, contexte: Contexte) -> Ledger:
    """Juin et juillet importés et classés ; juin synthétisé avec commentaire."""
    ledger = Ledger(chemin)
    for mois in MOIS:
        importer_mois(mois, DOSSIER_FIXTURES, ledger, date(2026, 10, 15))
    for mois in MOIS:
        derouler(mois, ledger, contexte, AppelsCategoriseur())
    preparer_contenu("2026-06", ledger, contexte, commentateur_reference())
    return ledger


def contenu(ledger: Ledger, contexte: Contexte, mois: str = "2026-07") -> ContenuPublication:
    return preparer_contenu(mois, ledger, contexte, commentateur_reference())
