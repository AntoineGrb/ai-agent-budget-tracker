"""Lecture et écriture des questions, règles apprises et décisions (spec 06).

Chaque réponse est écrite immédiatement, dans sa propre transaction SQL : quitter le dialogue en
cours de route ne perd rien.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from budget.categories import SousCategorie
from budget.errors import ControleError
from budget.models import Nature, Source
from budget.montants import depuis_centimes, en_centimes
from budget.questions.modeles import Question
from budget.regles import Critere, Decision, Regle, TypeRegle, verifier_nouvelle_regle

_COLONNES_REGLE = (
    "id",
    "type",
    "source",
    "critere",
    "valeur",
    "montant_min_centimes",
    "montant_max_centimes",
    "nature",
    "sous_categorie",
    "detail",
    "active",
)
_COLONNES_DECISION = (
    "transaction_id",
    "question_id",
    "nature",
    "sous_categorie",
    "detail",
    "lien_id",
    "mois_paie",
    "regle_id",
)


def horodatage() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _centimes(valeur: object) -> int | None:
    if valeur is None:
        return None
    if not isinstance(valeur, int):
        raise ControleError(f"montant de règle non entier en base : {valeur!r}")
    return valeur


def _regle_depuis_ligne(ligne: Sequence[object]) -> Regle:
    v = dict(zip(_COLONNES_REGLE, ligne, strict=True))
    minimum, maximum = _centimes(v["montant_min_centimes"]), _centimes(v["montant_max_centimes"])
    return Regle(
        id=int(str(v["id"])),
        type=TypeRegle(str(v["type"])),
        source=Source(str(v["source"])),
        critere=Critere(str(v["critere"])),
        valeur=str(v["valeur"]),
        montant_min=depuis_centimes(minimum) if minimum is not None else None,
        montant_max=depuis_centimes(maximum) if maximum is not None else None,
        nature=Nature(str(v["nature"])) if v["nature"] is not None else None,
        sous_categorie=SousCategorie(str(v["sous_categorie"])) if v["sous_categorie"] else None,
        detail=str(v["detail"]) if v["detail"] is not None else None,
        active=bool(v["active"]),
    )


def lire_regles(connexion: sqlite3.Connection) -> list[Regle]:
    requete = f"SELECT {', '.join(_COLONNES_REGLE)} FROM regles ORDER BY id"
    return [_regle_depuis_ligne(ligne) for ligne in connexion.execute(requete)]


def lire_decisions(connexion: sqlite3.Connection) -> dict[str, Decision]:
    requete = f"SELECT {', '.join(_COLONNES_DECISION)} FROM decisions"
    decisions = {}
    for ligne in connexion.execute(requete):
        v = dict(zip(_COLONNES_DECISION, ligne, strict=True))
        decision = Decision(
            transaction_id=str(v["transaction_id"]),
            question_id=int(str(v["question_id"])),
            nature=Nature(str(v["nature"])),
            sous_categorie=SousCategorie(str(v["sous_categorie"])) if v["sous_categorie"] else None,
            detail=str(v["detail"]) if v["detail"] is not None else None,
            lien_id=str(v["lien_id"]) if v["lien_id"] is not None else None,
            mois_paie=str(v["mois_paie"]) if v["mois_paie"] is not None else None,
            regle_id=int(str(v["regle_id"])) if v["regle_id"] is not None else None,
        )
        decisions[decision.transaction_id] = decision
    return decisions


def inserer_regle(
    connexion: sqlite3.Connection, regle: Regle, question_id: int | None = None
) -> Regle:
    """Insère une règle après contrôle des conflits ; à appeler dans une transaction ouverte."""
    verifier_nouvelle_regle(regle, lire_regles(connexion))
    curseur = connexion.execute(
        "INSERT INTO regles (type, source, critere, valeur, montant_min_centimes, "
        "montant_max_centimes, nature, sous_categorie, detail, active, creee_le, question_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            regle.type.value,
            regle.source.value,
            regle.critere.value,
            regle.valeur,
            en_centimes(regle.montant_min) if regle.montant_min is not None else None,
            en_centimes(regle.montant_max) if regle.montant_max is not None else None,
            regle.nature.value if regle.nature is not None else None,
            regle.sous_categorie.value if regle.sous_categorie is not None else None,
            regle.detail,
            int(regle.active),
            horodatage(),
            question_id,
        ),
    )
    assert curseur.lastrowid is not None
    return regle.model_copy(update={"id": curseur.lastrowid})


def marquer_question_posee(connexion: sqlite3.Connection, question: Question) -> int:
    """Enregistre la question à sa première pose (sans effet ensuite) et renvoie son numéro."""
    connexion.execute(
        "INSERT INTO questions (transaction_id, contrepartie_id, type, motif, posee_le) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (transaction_id) DO NOTHING",
        (
            question.transaction.id,
            question.contrepartie.id if question.contrepartie is not None else None,
            question.type.value,
            question.motif,
            horodatage(),
        ),
    )
    ligne = connexion.execute(
        "SELECT id FROM questions WHERE transaction_id = ?", (question.transaction.id,)
    ).fetchone()
    return int(ligne[0])


def enregistrer_reponse(
    connexion: sqlite3.Connection,
    question: Question,
    decisions: Sequence[Decision],
    regle: Regle | None,
) -> Regle | None:
    """Question répondue, règle éventuelle et décision de chaque côté ; en transaction."""
    attendus = {t.id for t in question.transactions}
    if {d.transaction_id for d in decisions} != attendus or len(decisions) != len(attendus):
        raise ControleError(
            "réponse incohérente : une décision par opération de la question est attendue"
        )
    question_id = marquer_question_posee(connexion, question)
    maintenant = horodatage()
    connexion.execute(
        "UPDATE questions SET repondue_le = ? WHERE id = ?", (maintenant, question_id)
    )
    creee = inserer_regle(connexion, regle, question_id) if regle is not None else None
    connexion.executemany(
        f"INSERT INTO decisions ({', '.join(_COLONNES_DECISION)}, decidee_le) "
        f"VALUES ({', '.join('?' for _ in _COLONNES_DECISION)}, ?)",
        (
            (
                d.transaction_id,
                question_id,
                d.nature.value,
                d.sous_categorie.value if d.sous_categorie is not None else None,
                d.detail,
                d.lien_id,
                d.mois_paie,
                creee.id if creee is not None else None,
                maintenant,
            )
            for d in decisions
        ),
    )
    return creee
