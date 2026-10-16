"""Confidentialité du catégoriseur : masquage, IBAN, garde-fou paiement carte (cadrage §11)."""

import pytest
from pydantic_ai.messages import ModelRequest

from budget.agents.categoriseur import CategoriseurLLM
from budget.agents.confidentialite import contient_iban, masquer
from budget.classement.cartes import GroupeMarchand
from budget.errors import ControleError
from budget.models import Source
from tests.conftest import fabriquer_transaction
from tests.scenarios.reference import AppelsCategoriseur, modele_propositions


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("VIR FR7630006000011234567890189 DUPONT", "VIR [IBAN] DUPONT"),
        ("VIR FR76 3000 6000 0112 3456 7890 189", "VIR [IBAN]"),
        ("TRANSFER DE89370400440532013000", "TRANSFER [IBAN]"),
        ("MAGASIN 1234567", "MAGASIN #######"),
        ("REF 123456 ET 12345", "REF ###### ET 12345"),
        ("ESSO31816ROC1", "ESSO31816ROC1"),
        ("CARREFOUR CITY SAINT MAUR D", "CARREFOUR CITY SAINT MAUR D"),
    ],
)
def test_masquer(texte: str, attendu: str) -> None:
    assert masquer(texte) == attendu
    assert not contient_iban(masquer(texte))


def _groupe(cle: str, source: Source = Source.CA_CB) -> GroupeMarchand:
    type_operation = "Avoir" if source is Source.TR else None
    t = fabriquer_transaction(cle, "-10.00", source=source, type_operation=type_operation)
    return GroupeMarchand(source=source, cle=cle, transactions=(t,))


def _tout_le_texte(appels: AppelsCategoriseur) -> str:
    """Tout ce qui est parti au modèle : instructions et messages."""
    morceaux = []
    for messages in appels.messages:
        for message in messages:
            if isinstance(message, ModelRequest):
                morceaux.append(message.instructions or "")
                morceaux += [str(getattr(part, "content", "")) for part in message.parts]
    return "\n".join(morceaux)


def test_iban_et_six_chiffres_masques_dans_le_prompt() -> None:
    """Le test échoue si un IBAN apparaît dans un prompt (cadrage §11)."""
    appels = AppelsCategoriseur()
    groupes = [
        _groupe("VIR FR7630006000011234567890189"),
        _groupe("MAGASIN 1234567", Source.TR),
    ]
    propositions = {"VIR [IBAN]": "achats.autre", "MAGASIN #######": "achats.autre"}
    resultat = CategoriseurLLM(modele_propositions(appels, propositions)).proposer(groupes)
    assert appels.prompts == ["MAGASIN #######\nVIR [IBAN]"]
    assert not contient_iban(_tout_le_texte(appels))
    assert "1234567" not in _tout_le_texte(appels)
    assert set(resultat) == {(g.source, g.cle) for g in groupes}


def test_operation_hors_carte_refusee_avant_l_appel() -> None:
    appels = AppelsCategoriseur()
    virement = fabriquer_transaction("VIR SALAIRE", "2500.00", type_operation="Virement")
    groupe = GroupeMarchand(source=Source.CA_JOINT, cle="VIR SALAIRE", transactions=(virement,))
    with pytest.raises(ControleError, match="n'est pas un paiement carte"):
        CategoriseurLLM(modele_propositions(appels)).proposer([groupe])
    assert appels.prompts == []
