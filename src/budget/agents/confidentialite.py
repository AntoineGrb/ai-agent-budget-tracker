"""Défense en profondeur avant tout appel au LLM (cadrage §11, spec 07).

Seules des clés marchand de paiements carte partent au modèle ; elles sont en plus masquées : tout
IBAN devient `[IBAN]`, toute séquence de 6 chiffres ou plus devient autant de `#`.
"""

import re

MASQUE_IBAN = "[IBAN]"

# FR76 3000 6000 0112 3456 7890 189, avec ou sans espaces
_IBAN_FR = re.compile(r"\bFR\d{2}(?: ?[0-9A-Z]{4}){5} ?[0-9A-Z]{3}\b", re.IGNORECASE)
# Motif international générique : pays, clé, puis 11 à 30 caractères alphanumériques
_IBAN_GENERIQUE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[0-9A-Z]){11,30}\b", re.IGNORECASE)
_SIX_CHIFFRES = re.compile(r"\d{6,}")


def masquer(texte: str) -> str:
    """Masque IBAN et séquences de 6 chiffres ou plus."""
    texte = _IBAN_FR.sub(MASQUE_IBAN, texte)
    texte = _IBAN_GENERIQUE.sub(MASQUE_IBAN, texte)
    return _SIX_CHIFFRES.sub(lambda m: "#" * len(m.group()), texte)


def contient_iban(texte: str) -> bool:
    """Vrai si un IBAN (FR ou générique) figure dans le texte : utilisé par les tests."""
    return bool(_IBAN_FR.search(texte) or _IBAN_GENERIQUE.search(texte))
