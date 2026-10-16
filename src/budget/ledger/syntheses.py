"""Lecture et écriture de la table `syntheses` : agrégats et commentaire d'un mois (spec 08)."""

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from budget.agents.synthese import CommentaireSynthese
from budget.agregats.modeles import Agregats
from budget.models import MOTIF_MOIS

_COLONNES = (
    "mois",
    "empreinte",
    "agregats",
    "commentaire",
    "modele",
    "calculee_le",
    "commentee_le",
)


class SyntheseEnregistree(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mois: str = Field(pattern=MOTIF_MOIS)
    empreinte: str
    agregats: Agregats
    commentaire: CommentaireSynthese | None  # None : sans LLM ou après échec
    modele: str | None
    calculee_le: datetime
    commentee_le: datetime | None


def lire_synthese(connexion: sqlite3.Connection, mois: str) -> SyntheseEnregistree | None:
    ligne = connexion.execute(
        f"SELECT {', '.join(_COLONNES)} FROM syntheses WHERE mois = ?", (mois,)
    ).fetchone()
    return _depuis_ligne(ligne) if ligne is not None else None


def lire_syntheses(connexion: sqlite3.Connection) -> list[SyntheseEnregistree]:
    """Toutes les synthèses enregistrées, dans l'ordre chronologique."""
    requete = f"SELECT {', '.join(_COLONNES)} FROM syntheses ORDER BY mois"
    return [_depuis_ligne(ligne) for ligne in connexion.execute(requete)]


def _depuis_ligne(ligne: Sequence[object]) -> SyntheseEnregistree:
    v = dict(zip(_COLONNES, ligne, strict=True))
    commentaire, commentee_le = v["commentaire"], v["commentee_le"]
    return SyntheseEnregistree(
        mois=str(v["mois"]),
        empreinte=str(v["empreinte"]),
        agregats=Agregats.model_validate_json(str(v["agregats"])),
        commentaire=(
            CommentaireSynthese.model_validate_json(str(commentaire)) if commentaire else None
        ),
        modele=str(v["modele"]) if v["modele"] is not None else None,
        calculee_le=datetime.fromisoformat(str(v["calculee_le"])),
        commentee_le=datetime.fromisoformat(str(commentee_le)) if commentee_le else None,
    )


def enregistrer_synthese(connexion: sqlite3.Connection, synthese: SyntheseEnregistree) -> None:
    """Remplace la synthèse du mois ; montants en chaînes décimales dans le JSON."""
    commentaire = synthese.commentaire
    connexion.execute(
        f"INSERT OR REPLACE INTO syntheses ({', '.join(_COLONNES)}) "
        f"VALUES ({', '.join('?' for _ in _COLONNES)})",
        (
            synthese.mois,
            synthese.empreinte,
            synthese.agregats.model_dump_json(),
            commentaire.model_dump_json() if commentaire is not None else None,
            synthese.modele,
            synthese.calculee_le.isoformat(timespec="seconds"),
            synthese.commentee_le.isoformat(timespec="seconds") if synthese.commentee_le else None,
        ),
    )
