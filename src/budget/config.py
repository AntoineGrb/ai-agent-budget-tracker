"""Chargement et validation du fichier de contexte du foyer (cadrage §10)."""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import ErrorDetails

from budget.errors import ConfigError
from budget.montants import Montant


class _Modele(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Seuils(_Modele):
    amazon_auto: Montant  # RG-13


class Llm(_Modele):
    modele_categorisation: str = "anthropic:claude-haiku-4-5"


class Contexte(_Modele):
    version: Literal[2]
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
    if donnees.get("version") == 1:
        raise ConfigError(
            f"{chemin} est au format version 1, antérieur au recadrage d'octobre 2026 : le "
            "remplacer par une copie de contexte.example.yaml (version 2)"
        )
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
    elif type_erreur == "literal_error":
        message = f"valeur {valeur!r} invalide, attendu : {ctx.get('expected')}"
    elif type_erreur in ("greater_than_equal", "less_than_equal"):
        borne = ctx.get("ge", ctx.get("le"))
        sens = "supérieure" if type_erreur == "greater_than_equal" else "inférieure"
        message = f"valeur {valeur!r} invalide, attendu une valeur {sens} ou égale à {borne}"
    elif type_erreur == "value_error":
        message = str(ctx.get("error", detail["msg"]))
    else:
        message = f"{detail['msg']} (valeur : {valeur!r})"
    return f"{chemin} : {message}"
