"""Agent catégoriseur : une sous-catégorie par clé marchand inconnue (cadrage §9).

Le modèle ne reçoit que des clés marchand de paiements carte, masquées, une par ligne : ni montant,
ni date, ni compte. L'énumération `SousCategorie` ferme la sortie ; un validateur vérifie qu'il y a
exactement une proposition par libellé soumis, et demande une relance sinon. Aucun score de
confiance (cadrage §7) : chaque proposition est validée par l'utilisateur.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import pydantic_ai
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from budget.agents.confidentialite import masquer
from budget.categories import SousCategorie, exemples_de, libelle_complet
from budget.classement.cartes import GroupeMarchand, est_paiement_carte
from budget.errors import CategorisationError, ControleError
from budget.models import Source

# La CLI possède sa sortie : pas de bannière au premier appel.
pydantic_ai.BANNER_ENABLED = False

journal = logging.getLogger(__name__)

TAILLE_LOT_CATEGORISEUR = 100
RELANCES_SORTIE = 2
REGLAGES: ModelSettings = {"max_tokens": 8000}  # température par défaut : voir la spec 07


class Proposition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    libelle: str  # clé marchand masquée, recopiée à l'identique
    sous_categorie: SousCategorie  # énumération fermée : rien d'autre n'est possible


@dataclass(frozen=True)
class Soumission:
    """Dépendances de l'agent : les libellés soumis, masqués, uniques et triés."""

    libelles: tuple[str, ...]


def _instructions() -> str:
    lignes = [f"- {sc.value} : {libelle_complet(sc)} ({exemples_de(sc)})" for sc in SousCategorie]
    return "\n".join(
        [
            "Tu catégorises des libellés de paiements carte d'un foyer français.",
            "Chaque libellé est le nom d'un commerçant, parfois suivi d'une ville ; des chiffres "
            "peuvent être masqués par #.",
            "Renvoie exactement une proposition par libellé fourni, avec le libellé recopié à "
            "l'identique, et le code de sous-catégorie le plus probable parmi :",
            *lignes,
        ]
    )


categoriseur: Agent[Soumission, list[Proposition]] = Agent(
    output_type=list[Proposition],
    deps_type=Soumission,
    instructions=_instructions(),
    retries={"output": RELANCES_SORTIE},
    name="categoriseur",
)


@categoriseur.output_validator
def _une_proposition_par_libelle(
    ctx: RunContext[Soumission], propositions: list[Proposition]
) -> list[Proposition]:
    soumis = set(ctx.deps.libelles)
    recus = [p.libelle for p in propositions]
    manquants = sorted(soumis - set(recus))
    inconnus = sorted(set(recus) - soumis)
    doublons = sorted({r for r in recus if recus.count(r) > 1})
    if manquants or inconnus or doublons:
        problemes = [
            f"{titre} : {', '.join(liste)}"
            for titre, liste in (
                ("libellés manquants", manquants),
                ("libellés inconnus", inconnus),
                ("libellés en double", doublons),
            )
            if liste
        ]
        raise ModelRetry(
            "Renvoie exactement une proposition par libellé fourni, recopié à l'identique. "
            + " ; ".join(problemes)
        )
    return propositions


class Categoriseur(Protocol):
    """Propose une sous-catégorie par groupe marchand ; `CategorisationError` en mode dégradé."""

    @property
    def nom_modele(self) -> str | None: ...

    def proposer(
        self, groupes: Sequence[GroupeMarchand]
    ) -> dict[tuple[Source, str], SousCategorie]: ...


class CategoriseurIndisponible:
    """Mode dégradé assumé (`--sans-llm`, clé API absente) : aucune proposition."""

    nom_modele: str | None = None

    def __init__(self, raison: str) -> None:
        self.raison = raison

    def proposer(
        self, groupes: Sequence[GroupeMarchand]
    ) -> dict[tuple[Source, str], SousCategorie]:
        raise CategorisationError(self.raison)


def raison_echec(erreur: Exception) -> str:
    """Cause d'un échec pour le terminal : statut HTTP et message du fournisseur, sans le prompt."""
    raison = type(erreur).__name__
    if isinstance(erreur, ModelHTTPError):
        details = [f"HTTP {erreur.status_code}"]
        corps = erreur.body
        if isinstance(corps, dict) and isinstance(corps.get("error"), dict):
            details.append(str(corps["error"].get("message", "")))
        if erreur.suggested_model_id is not None:
            details.append(f"modèle suggéré : {erreur.suggested_model_id}")
        raison += " : " + ", ".join(d for d in details if d)
    return f"catégoriseur en échec ({raison})"


def verifier_groupes(groupes: Sequence[GroupeMarchand]) -> None:
    """Défense en profondeur : seuls des paiements carte peuvent partir au LLM (cadrage §11)."""
    for groupe in groupes:
        for t in groupe.transactions:
            if not est_paiement_carte(t):
                raise ControleError(
                    f"catégoriseur : l'opération {t.fichier}, position {t.position}, du "
                    f"{t.date_operation:%d/%m/%Y} n'est pas un paiement carte : rien n'est envoyé"
                )


class CategoriseurLLM:
    """Implémentation par l'agent PydanticAI ; un appel par lot de 100 clés au plus."""

    def __init__(self, modele: str | Model) -> None:
        self.modele = modele

    @property
    def nom_modele(self) -> str | None:
        return self.modele if isinstance(self.modele, str) else self.modele.model_name

    def proposer(
        self, groupes: Sequence[GroupeMarchand]
    ) -> dict[tuple[Source, str], SousCategorie]:
        verifier_groupes(groupes)
        # Deux clés identiques une fois masquées ne sont envoyées qu'une fois
        masques = {(g.source, g.cle): masquer(g.cle) for g in groupes}
        libelles = sorted(set(masques.values()))
        par_libelle: dict[str, SousCategorie] = {}
        for debut in range(0, len(libelles), TAILLE_LOT_CATEGORISEUR):
            lot = tuple(libelles[debut : debut + TAILLE_LOT_CATEGORISEUR])
            try:
                par_libelle.update(self._appeler(lot))
            except Exception as erreur:  # réseau, authentification, sortie invalide…
                statut = getattr(erreur, "status_code", "")
                journal.warning("catégoriseur en échec : %s %s", type(erreur).__name__, statut)
                partielles = {cle: par_libelle[m] for cle, m in masques.items() if m in par_libelle}
                raise CategorisationError(raison_echec(erreur), partielles) from erreur
        return {cle: par_libelle[m] for cle, m in masques.items() if m in par_libelle}

    def _appeler(self, lot: tuple[str, ...]) -> dict[str, SousCategorie]:
        debut = time.perf_counter()
        resultat = categoriseur.run_sync(
            "\n".join(lot), model=self.modele, deps=Soumission(lot), model_settings=REGLAGES
        )
        usage = resultat.usage
        journal.info(
            "catégoriseur : %d libellés envoyés à %s, %d propositions en %.1f s "
            "(%d requêtes, %d tokens en entrée, %d en sortie)",
            len(lot),
            self.nom_modele,
            len(resultat.output),
            time.perf_counter() - debut,
            usage.requests,
            usage.input_tokens,
            usage.output_tokens,
        )
        return {p.libelle: p.sous_categorie for p in resultat.output}
