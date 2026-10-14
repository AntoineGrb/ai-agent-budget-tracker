"""Agent catégoriseur avec FunctionModel : prompt, validateur, relances, lots, mode dégradé."""

import re

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from budget.agents.categoriseur import (
    RELANCES_SORTIE,
    TAILLE_LOT_CATEGORISEUR,
    CategoriseurIndisponible,
    CategoriseurLLM,
    categoriseur,
)
from budget.categories import SousCategorie
from budget.classement.cartes import GroupeMarchand
from budget.errors import CategorisationError
from budget.models import Source
from tests.conftest import fabriquer_transaction
from tests.scenarios.juin import AppelsCategoriseur, modele_propositions


def _groupe(cle: str, source: Source = Source.CA_CB, montant: str = "-12.34") -> GroupeMarchand:
    type_operation = "Avoir" if source is Source.TR else None
    t = fabriquer_transaction(cle, montant, source=source, type_operation=type_operation)
    return GroupeMarchand(source=source, cle=cle, transactions=(t,))


def _reponse(info: AgentInfo, propositions: list[tuple[str, str]]) -> ModelResponse:
    contenu = [{"libelle": libelle, "sous_categorie": sc} for libelle, sc in propositions]
    outil = info.output_tools[0].name
    return ModelResponse(parts=[ToolCallPart(tool_name=outil, args={"response": contenu})])


def test_prompt_ne_contient_que_les_libelles() -> None:
    """Ni montant, ni date, ni compte : les clés masquées, une par ligne, triées."""
    appels = AppelsCategoriseur()
    groupes = [_groupe("UBER *EATS", Source.TR, "-71.20"), _groupe("MONOPRIX", montant="-43.43")]
    resultat = CategoriseurLLM(modele_propositions(appels)).proposer(groupes)
    assert appels.prompts == ["MONOPRIX\nUBER *EATS"]
    prompt = appels.prompts[0]
    assert not re.search(r"\d", prompt)
    assert "Joint" not in prompt and "Perso" not in prompt
    assert resultat == {
        (Source.CA_CB, "MONOPRIX"): SousCategorie.COURSES_SUPERMARCHE,
        (Source.TR, "UBER *EATS"): SousCategorie.RESTOS_LIVRAISON,
    }


def test_instructions_listent_toute_la_taxonomie() -> None:
    appels = AppelsCategoriseur()
    CategoriseurLLM(modele_propositions(appels)).proposer([_groupe("MONOPRIX")])
    [requete] = [m for m in appels.messages[0] if isinstance(m, ModelRequest)]
    instructions = requete.instructions or ""
    assert all(sc.value in instructions for sc in SousCategorie)
    assert "exactement une proposition par libellé" in instructions


def test_cles_identiques_une_fois_masquees_envoyees_une_fois() -> None:
    appels = AppelsCategoriseur()
    groupes = [_groupe("MAGASIN 1234567"), _groupe("MAGASIN 7654321")]
    resultat = CategoriseurLLM(
        modele_propositions(appels, {"MAGASIN #######": "achats.divers"})
    ).proposer(groupes)
    assert appels.prompts == ["MAGASIN #######"]
    assert set(resultat.values()) == {SousCategorie.ACHATS_DIVERS} and len(resultat) == 2


@pytest.mark.parametrize(
    ("premiere", "motif"),
    [
        ([("MONOPRIX", "courses.supermarche")], "libellés manquants : UBER \\*EATS"),
        (
            [
                ("MONOPRIX", "courses.supermarche"),
                ("UBER *EATS", "restos.livraison"),
                ("X", "achats.divers"),
            ],
            "libellés inconnus : X",
        ),
        (
            [
                ("MONOPRIX", "courses.supermarche"),
                ("MONOPRIX", "courses.supermarche"),
                ("UBER *EATS", "restos.livraison"),
            ],
            "libellés en double : MONOPRIX",
        ),
    ],
)
def test_validateur_demande_une_relance(premiere: list[tuple[str, str]], motif: str) -> None:
    complete = [("MONOPRIX", "courses.supermarche"), ("UBER *EATS", "restos.livraison")]
    relances: list[str] = []

    def modele(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        relances.extend(
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        )
        return _reponse(info, complete if relances else premiere)

    resultat = CategoriseurLLM(FunctionModel(modele)).proposer(
        [_groupe("MONOPRIX"), _groupe("UBER *EATS")]
    )
    assert len(resultat) == 2
    assert re.search(motif, relances[0])


def test_relances_epuisees_mode_degrade_sans_erreur_inattendue() -> None:
    requetes: list[int] = []

    def modele(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requetes.append(1)
        return _reponse(info, [])

    with pytest.raises(CategorisationError, match="UnexpectedModelBehavior") as erreur:
        CategoriseurLLM(FunctionModel(modele)).proposer([_groupe("MONOPRIX")])
    assert erreur.value.partielles == {}
    assert len(requetes) == RELANCES_SORTIE + 1


def test_lots_de_cent_et_resultats_partiels() -> None:
    """Au-delà de 100 clés, un appel par lot ; un lot en échec garde les lots déjà reçus."""
    groupes = [_groupe(f"MARCHAND {chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(150)]
    appels = AppelsCategoriseur()
    table = {g.cle: "achats.divers" for g in groupes}
    resultat = CategoriseurLLM(modele_propositions(appels, table)).proposer(groupes)
    assert [len(p.splitlines()) for p in appels.prompts] == [TAILLE_LOT_CATEGORISEUR, 50]
    assert len(resultat) == 150

    lots: list[int] = []

    def second_lot_en_echec(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            str(p.content)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if p.part_kind == "user-prompt"
        )
        lots.append(1)
        if prompt.startswith("MARCHAND AA"):
            return _reponse(info, [(c, "achats.divers") for c in prompt.splitlines()])
        raise RuntimeError("réseau indisponible")

    with pytest.raises(CategorisationError, match="RuntimeError") as erreur:
        CategoriseurLLM(FunctionModel(second_lot_en_echec)).proposer(groupes)
    assert len(erreur.value.partielles) == TAILLE_LOT_CATEGORISEUR


def test_categoriseur_indisponible() -> None:
    with pytest.raises(CategorisationError, match="--sans-llm"):
        CategoriseurIndisponible("catégoriseur désactivé par --sans-llm").proposer(
            [_groupe("MONOPRIX")]
        )


def test_aucune_cle_dans_les_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("DEBUG", logger="budget"):
        CategoriseurLLM(modele_propositions(AppelsCategoriseur())).proposer(
            [_groupe("MONOPRIX"), _groupe("UBER *EATS", Source.TR)]
        )
    journal = "\n".join(r.getMessage() for r in caplog.records)
    assert "2 libellés envoyés" in journal
    assert "MONOPRIX" not in journal and "UBER" not in journal


def test_enumeration_fermee_code_inconnu_relance() -> None:
    """Une sous-catégorie hors de l'énumération est refusée et provoque une relance."""
    reponses = iter([[("MONOPRIX", "courses.hypermarche")], [("MONOPRIX", "courses.supermarche")]])

    def modele(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _reponse(info, next(reponses))

    resultat = CategoriseurLLM(FunctionModel(modele)).proposer([_groupe("MONOPRIX")])
    assert resultat == {(Source.CA_CB, "MONOPRIX"): SousCategorie.COURSES_SUPERMARCHE}
    assert categoriseur.name == "categoriseur"
