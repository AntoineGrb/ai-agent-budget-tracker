"""Agent synthèse : un court commentaire des chiffres du mois, sans aucun calcul (spec 08).

Le modèle reçoit les agrégats déjà calculés par le code, en JSON, montants en chaînes décimales :
ni libellé de virement ou de prélèvement, ni IBAN, ni nom. Seules les clés marchand masquées des
paiements carte y figurent (cadrage §11). Un validateur vérifie que chaque montant en euros cité
figure dans les données, et demande une relance sinon.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Protocol

import pydantic_ai
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from budget.agents.categoriseur import raison_echec
from budget.agents.confidentialite import masquer
from budget.agregats.modeles import Agregats
from budget.config import Budgets
from budget.errors import SyntheseError
from budget.montants import parse_montant

# La CLI possède sa sortie : pas de bannière au premier appel.
pydantic_ai.BANNER_ENABLED = False

journal = logging.getLogger(__name__)

VERSION_CONSIGNES_SYNTHESE = 1  # à incrémenter à chaque modification des consignes
RELANCES_SORTIE = 2
REGLAGES: ModelSettings = {"max_tokens": 2000}  # température par défaut : voir la spec 08
NOM_AGENT = "agent synthèse"


class EntreeSynthese(BaseModel):
    """Ce qui part au modèle : les chiffres du mois et les budgets renseignés."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agregats: Agregats
    budgets: Budgets

    def prompt(self) -> str:
        """JSON envoyé au modèle, masqué en entier en défense en profondeur."""
        return masquer(self.model_dump_json(indent=1))

    def montants(self) -> frozenset[Decimal]:
        """Valeurs absolues de tous les montants de l'entrée : les seuls citables."""
        return frozenset(abs(m) for m in _decimaux(self.model_dump()))


def _decimaux(valeur: object) -> Iterator[Decimal]:
    if isinstance(valeur, Decimal):
        yield valeur
    elif isinstance(valeur, dict):
        for element in valeur.values():
            yield from _decimaux(element)
    elif isinstance(valeur, (list, tuple)):
        for element in valeur:
            yield from _decimaux(element)


class CommentaireSynthese(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resume: str = Field(max_length=280)  # une à deux phrases
    points_cles: list[str] = Field(min_length=1, max_length=5)


CONSIGNES = "\n".join(
    [
        "Tu commentes les dépenses mensuelles d'un foyer français. Les chiffres ont déjà été "
        "calculés et te sont fournis en JSON ; tu n'en calcules aucun.",
        "Données : `agregats.joint` est la vue du compte joint, `agregats.perso` celle du compte "
        "perso ; les deux vues ne s'additionnent jamais. Les montants sont en euros et les "
        "dépenses en positif. `ecart_budget` et `ecart_moyenne` valent dépense moins référence : "
        "positifs, ce sont des dépassements. `moyenne_3_mois` est vide sans mois antérieur "
        "disponible. `plus_grosses` liste les plus grosses dépenses du mois, `historique` les "
        "totaux des mois précédents.",
        "Réponds pour le mois, vue joint et vue perso séparées, à trois questions : combien le "
        "foyer a dépensé, quelle catégorie pèse le plus, comment les dépenses évoluent.",
        "Cite la catégorie la plus lourde de chaque vue et chaque dépassement de budget. Explique "
        "une forte hausse par rapport à la moyenne à l'aide des plus grosses dépenses.",
        "Ne cite que des montants présents dans les données, au centime près, au format français "
        "(1 234,56 €). Aucun calcul : ni somme, ni différence, ni pourcentage.",
        "Style factuel et bref : un résumé d'une à deux phrases (280 caractères au plus), puis "
        "un à cinq points clés.",
    ]
)

synthese: Agent[EntreeSynthese, CommentaireSynthese] = Agent(
    output_type=CommentaireSynthese,
    deps_type=EntreeSynthese,
    instructions=CONSIGNES,
    retries={"output": RELANCES_SORTIE},
    name="synthese",
)

# `1 234,56 €`, `1 200 €`, `-50 €`, `+27,36 €` : signe facultatif, milliers séparés par une espace
_MONTANT_CITE = re.compile(
    r"(?<![\d.,])[-+−]?\s?(?P<entier>\d{1,3}(?:[   ]\d{3})+|\d+)"
    r"(?P<decimales>[.,]\d{1,2})?\s?€"
)


def montants_cites(texte: str) -> list[tuple[str, Decimal]]:
    """Montants en euros cités dans un texte, avec leur forme d'origine ; pas les pourcentages."""
    return [
        (c.group().strip(), parse_montant(c.group("entier") + (c.group("decimales") or "")))
        for c in _MONTANT_CITE.finditer(texte)
    ]


@synthese.output_validator
def _montants_fournis(
    ctx: RunContext[EntreeSynthese], commentaire: CommentaireSynthese
) -> CommentaireSynthese:
    autorises = ctx.deps.montants()
    inconnus = [
        forme
        for texte in (commentaire.resume, *commentaire.points_cles)
        for forme, montant in montants_cites(texte)
        if abs(montant) not in autorises
    ]
    if inconnus:
        raise ModelRetry(
            f"Montants absents des données : {', '.join(dict.fromkeys(inconnus))}. Ne cite que "
            "des montants fournis dans le JSON, recopiés au centime près, sans aucun calcul."
        )
    return commentaire


def empreinte(entree: EntreeSynthese, modele: str) -> str:
    """sha256 du JSON canonique de l'entrée, du modèle et de la version des consignes."""
    canonique = json.dumps(
        {
            "entree": json.loads(entree.model_dump_json()),
            "modele": modele,
            "version_consignes": VERSION_CONSIGNES_SYNTHESE,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


class Commentateur(Protocol):
    """Commente les agrégats d'un mois ; `SyntheseError` en mode dégradé ou en cas d'échec."""

    def commenter(self, entree: EntreeSynthese) -> CommentaireSynthese: ...


class CommentateurIndisponible:
    """Mode dégradé assumé (`--sans-llm`, clé API absente) : aucun appel."""

    def __init__(self, raison: str) -> None:
        self.raison = raison

    def commenter(self, entree: EntreeSynthese) -> CommentaireSynthese:
        raise SyntheseError(self.raison)


class CommentateurLLM:
    """Implémentation par l'agent PydanticAI : un appel par synthèse."""

    def __init__(self, modele: str | Model) -> None:
        self.modele = modele

    @property
    def nom_modele(self) -> str:
        return self.modele if isinstance(self.modele, str) else self.modele.model_name

    def commenter(self, entree: EntreeSynthese) -> CommentaireSynthese:
        debut = time.perf_counter()
        try:
            resultat = synthese.run_sync(
                entree.prompt(), model=self.modele, deps=entree, model_settings=REGLAGES
            )
        except Exception as erreur:  # réseau, authentification, relances épuisées…
            statut = getattr(erreur, "status_code", "")
            journal.warning("agent synthèse en échec : %s %s", type(erreur).__name__, statut)
            raise SyntheseError(raison_echec(erreur, NOM_AGENT)) from erreur
        usage = resultat.usage
        journal.info(
            "agent synthèse : mois %s commenté par %s en %.1f s "
            "(%d requêtes, %d tokens en entrée, %d en sortie)",
            entree.agregats.mois,
            self.nom_modele,
            time.perf_counter() - debut,
            usage.requests,
            usage.input_tokens,
            usage.output_tokens,
        )
        return resultat.output
