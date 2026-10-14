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


_TROIS_CHIFFRES = re.compile(r"\d{3}")


def cle_marchand(libelle_normalise: str) -> str:
    """Partie stable d'un libellé carte, sans numéro de magasin, de terminal ni de transaction.

    Premier jeton, puis les suivants jusqu'au premier jeton (exclu) contenant au moins trois
    chiffres consécutifs ; `/` et espaces de fin retirés. La ville n'est pas retirée : le format
    du Crédit Agricole ne la sépare pas du nom de façon fiable.
    """
    jetons = libelle_normalise.split(" ")
    gardes = jetons[:1]
    for jeton in jetons[1:]:
        if _TROIS_CHIFFRES.search(jeton):
            break
        gardes.append(jeton)
    cle = " ".join(gardes).rstrip("/ ")
    return cle or libelle_normalise
