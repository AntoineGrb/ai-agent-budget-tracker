"""Normalisation typographique des libellés bancaires (spec 04)."""

import re
import unicodedata

_BLANCS = re.compile(r"\s+")


def normaliser_libelle(texte: str) -> str:
    """Majuscules sans diacritiques, blancs réduits à une espace, sans espaces de bord.

    Purement typographique : ponctuation et chiffres sont conservés. L'extraction d'une clé
    marchand (numéros de magasin, villes) relève de l'étape 7.
    """
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return _BLANCS.sub(" ", sans_accents.upper()).strip()
