"""Chargement et validation du fichier de contexte du foyer (cadrage §10)."""

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import ErrorDetails

from budget.categories import SousCategorie
from budget.errors import ConfigError
from budget.models import MOTIF_CARTE
from budget.montants import Montant

Carte = Annotated[str, StringConstraints(pattern=MOTIF_CARTE)]
Jour = Annotated[int, Field(ge=1, le=31)]
NumeroMois = Annotated[int, Field(ge=1, le=12)]
Pourcentage = Annotated[Montant, Field(ge=0)]


def _en_liste(valeur: Any) -> Any:
    return [valeur] if isinstance(valeur, int) else valeur


ListeMois = Annotated[tuple[NumeroMois, ...] | None, BeforeValidator(_en_liste)]


class _Modele(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _AvecCritere(_Modele):
    """Élément reconnu sur une opération par au moins un critère."""

    ics: str | None = None
    libelle_contient: str | None = None
    type_operation: str | None = None

    @model_validator(mode="after")
    def _au_moins_un_critere(self) -> Self:
        if not (self.ics or self.libelle_contient or self.type_operation):
            raise ValueError(
                "au moins un critère est requis parmi ics, libelle_contient, type_operation"
            )
        return self


class CompteJoint(_Modele):
    banque: Literal["credit_agricole"]
    cartes_actives: tuple[Carte, ...]
    cartes_inactives: tuple[Carte, ...] = ()


class ComptePerso(_Modele):
    banque: Literal["trade_republic"]


class Comptes(_Modele):
    joint: CompteJoint
    perso: ComptePerso


class Salaire(_Modele):
    nom: str
    libelle_contient: str
    periode_regex: str | None = None  # RG-04
    montant_approx: Montant

    @field_validator("periode_regex")
    @classmethod
    def _regex_un_groupe(cls, valeur: str | None) -> str | None:
        if valeur is None:
            return None
        try:
            motif = re.compile(valeur)
        except re.error as erreur:
            raise ValueError(f"expression régulière invalide ({erreur})") from erreur
        if motif.groups != 1:
            raise ValueError(
                f"l'expression doit contenir exactement un groupe capturant, "
                f"{motif.groups} trouvé(s)"
            )
        return valeur


class FenetreSalaire(_Modele):  # RG-04
    du_jour: Jour
    au_jour_mois_suivant: Jour


class Dotation(_Modele):  # RG-05
    nom: str
    libelle_contient: str
    montant: Montant


class PaiementFinance(_AvecCritere):  # RG-08
    nom: str
    periodicite: Literal["mensuelle", "trimestrielle", "semestrielle", "annuelle"] | None = None


class Provision(_Modele):  # RG-08
    reprises_libelle_contient: str
    paiements_finances: tuple[PaiementFinance, ...] = ()


class ChargeFixe(_AvecCritere):  # RG-18
    nom: str
    montant: Montant
    tolerance_pct: Pourcentage | None = None
    sous_categorie: SousCategorie


class Enveloppes(_Modele):
    joint_courant: Montant
    perso: Montant


class DepenseAnnuelle(_Modele):
    nom: str
    mois: ListeMois = None
    montant: Montant | None = None
    sous_categorie: SousCategorie


class MoisParticulier(_Modele):
    mois: NumeroMois
    sens: Literal["revenu", "depense"]
    motif: str
    montant_approx: Montant | None = None


class Seuils(_Modele):
    exceptionnel: Montant  # RG-15
    amazon_auto: Montant  # RG-13
    ecart_charge_fixe_pct: Pourcentage  # RG-18


class Llm(_Modele):
    modele_categorisation: str = "anthropic:claude-haiku-4-5"
    modele_synthese: str = "anthropic:claude-sonnet-5"


class Contexte(_Modele):
    version: Literal[1]
    comptes: Comptes
    salaires: tuple[Salaire, ...]
    fenetre_salaire: FenetreSalaire
    dotations: tuple[Dotation, ...]
    provision: Provision
    charges_fixes: tuple[ChargeFixe, ...]
    enveloppes: Enveloppes
    budgets_sous_categories: dict[SousCategorie, Montant | None] = Field(default_factory=dict)
    depenses_annuelles_connues: tuple[DepenseAnnuelle, ...] = ()
    mois_particuliers: tuple[MoisParticulier, ...] = ()
    seuils: Seuils
    llm: Llm = Llm()


class _ChargeurDecimal(yaml.SafeLoader):
    """`SafeLoader` dont les nombres décimaux sont lus en `Decimal` depuis leur texte source."""


def _construire_decimal(loader: yaml.SafeLoader, noeud: yaml.Node) -> Decimal:
    assert isinstance(noeud, yaml.ScalarNode)
    texte = loader.construct_scalar(noeud).replace("_", "")
    try:
        valeur = Decimal(texte)
    except InvalidOperation as erreur:
        raise yaml.constructor.ConstructorError(
            None, None, f"nombre décimal invalide {texte!r}", noeud.start_mark
        ) from erreur
    if not valeur.is_finite():
        raise yaml.constructor.ConstructorError(
            None, None, f"nombre non fini {texte!r} refusé", noeud.start_mark
        )
    return valeur


_ChargeurDecimal.add_constructor("tag:yaml.org,2002:float", _construire_decimal)


def charger_yaml(chemin: Path) -> Any:
    """Lit un fichier YAML sans jamais produire de `float`."""
    with chemin.open(encoding="utf-8") as flux:
        return yaml.load(flux, Loader=_ChargeurDecimal)  # noqa: S506 (SafeLoader dérivé)


def charger_contexte(chemin: Path) -> Contexte:
    """Charge et valide le fichier de contexte ; toute anomalie lève `ConfigError`."""
    if not chemin.is_file():
        raise ConfigError(f"Fichier de contexte introuvable : {chemin}")
    try:
        donnees = charger_yaml(chemin)
    except yaml.YAMLError as erreur:
        raise ConfigError(f"YAML illisible dans {chemin}", [str(erreur)]) from erreur
    if not isinstance(donnees, dict):
        raise ConfigError(f"Le fichier {chemin} doit contenir un dictionnaire YAML à la racine")
    try:
        return Contexte.model_validate(donnees)
    except ValidationError as erreur:
        problemes = [_decrire(detail) for detail in erreur.errors()]
        raise ConfigError(f"Fichier de contexte invalide : {chemin}", problemes) from erreur


def _chemin_yaml(loc: tuple[int | str, ...]) -> str:
    chemin = ""
    for element in loc:
        if isinstance(element, int):
            chemin += f"[{element}]"
        elif element == "[key]":
            chemin += " (clé)"
        else:
            chemin += f".{element}" if chemin else element
    return chemin or "(racine)"


def _decrire(detail: ErrorDetails) -> str:
    chemin = _chemin_yaml(detail["loc"])
    type_erreur = detail["type"]
    valeur = detail.get("input")
    ctx = detail.get("ctx", {})
    if type_erreur == "extra_forbidden":
        message = "clé inconnue"
    elif type_erreur == "missing":
        message = "clé obligatoire absente"
    elif type_erreur == "enum":
        message = f"valeur inconnue {valeur!r}, attendu l'une de : {ctx.get('expected')}"
    elif type_erreur == "literal_error":
        message = f"valeur {valeur!r} invalide, attendu : {ctx.get('expected')}"
    elif type_erreur == "string_pattern_mismatch":
        message = f"valeur {valeur!r} invalide, format attendu {ctx.get('pattern')}"
    elif type_erreur in ("greater_than_equal", "less_than_equal"):
        borne = ctx.get("ge", ctx.get("le"))
        sens = "supérieure" if type_erreur == "greater_than_equal" else "inférieure"
        message = f"valeur {valeur!r} invalide, attendu une valeur {sens} ou égale à {borne}"
    elif type_erreur == "value_error":
        message = str(ctx.get("error", detail["msg"]))
    else:
        message = f"{detail['msg']} (valeur : {valeur!r})"
    return f"{chemin} : {message}"
