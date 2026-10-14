"""Chargement de `.env` (spec 07), avec la seule bibliothèque standard.

Lignes `CLE=valeur`, commentaires `#`, guillemets entourant la valeur retirés, préfixe `export`
accepté. Une variable déjà définie dans l'environnement n'est jamais écrasée.
"""

import os
from pathlib import Path


def charger_env(chemin: Path = Path(".env")) -> list[str]:
    """Charge les variables absentes de l'environnement ; renvoie leurs noms, pas leurs valeurs."""
    if not chemin.is_file():
        return []
    chargees = []
    for brute in chemin.read_text(encoding="utf-8").splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.removeprefix("export ").partition("=")
        cle, valeur = cle.strip(), valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
            valeur = valeur[1:-1]
        if cle and cle not in os.environ:
            os.environ[cle] = valeur
            chargees.append(cle)
    return chargees
