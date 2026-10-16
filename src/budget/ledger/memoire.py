"""Lecture et écriture des questions, règles, décisions et propositions (cadrage §7).

Chaque réponse est écrite immédiatement, dans sa propre transaction SQL : quitter le dialogue en
cours de route ne perd rien.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from budget.categories import Imputation, SousCategorie
from budget.errors import ControleError, RegleError
from budget.horloge import horodatage
from budget.models import Source
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
    "sous_categorie",
    "non_comptee",
    "active",
    "creee_le",
    "desactivee_le",
)
_COLONNES_DECISION = ("transaction_id", "question_id", "sous_categorie", "non_comptee", "regle_id")


def _centimes(valeur: object) -> int | None:
    if valeur is None:
        return None
    if not isinstance(valeur, int):
        raise ControleError(f"montant de règle non entier en base : {valeur!r}")
    return valeur


def _instant(valeur: object) -> datetime | None:
    return datetime.fromisoformat(str(valeur)) if valeur is not None else None


def _sous_categorie(valeur: object) -> SousCategorie | None:
    return SousCategorie(str(valeur)) if valeur is not None else None


def _entier(valeur: object) -> int | None:
    return int(str(valeur)) if valeur is not None else None


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
        sous_categorie=_sous_categorie(v["sous_categorie"]),
        non_comptee=bool(v["non_comptee"]),
        active=bool(v["active"]),
        creee_le=_instant(v["creee_le"]),
        desactivee_le=_instant(v["desactivee_le"]),
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
            question_id=_entier(v["question_id"]),
            sous_categorie=_sous_categorie(v["sous_categorie"]),
            non_comptee=bool(v["non_comptee"]),
            regle_id=_entier(v["regle_id"]),
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
        "montant_max_centimes, sous_categorie, non_comptee, active, creee_le, question_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            regle.type.value,
            regle.source.value,
            regle.critere.value,
            regle.valeur,
            en_centimes(regle.montant_min) if regle.montant_min is not None else None,
            en_centimes(regle.montant_max) if regle.montant_max is not None else None,
            regle.sous_categorie.value if regle.sous_categorie is not None else None,
            int(regle.non_comptee),
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
        "INSERT INTO questions (transaction_id, type, motif, posee_le) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (transaction_id) DO NOTHING",
        (question.transaction.id, question.type.value, question.motif, horodatage()),
    )
    ligne = connexion.execute(
        "SELECT id FROM questions WHERE transaction_id = ?", (question.transaction.id,)
    ).fetchone()
    return int(ligne[0])


def enregistrer_reponse(
    connexion: sqlite3.Connection, question: Question, decision: Decision, regle: Regle | None
) -> Regle | None:
    """Question répondue, règle éventuelle et décision ; à appeler dans une transaction ouverte."""
    if decision.transaction_id != question.transaction.id:
        raise ControleError("réponse incohérente : la décision porte sur une autre opération")
    question_id = marquer_question_posee(connexion, question)
    maintenant = horodatage()
    connexion.execute(
        "UPDATE questions SET repondue_le = ? WHERE id = ?", (maintenant, question_id)
    )
    creee = inserer_regle(connexion, regle, question_id) if regle is not None else None
    connexion.execute(
        f"INSERT INTO decisions ({', '.join(_COLONNES_DECISION)}, decidee_le) "
        f"VALUES ({', '.join('?' for _ in _COLONNES_DECISION)}, ?)",
        (
            decision.transaction_id,
            question_id,
            decision.sous_categorie.value if decision.sous_categorie is not None else None,
            int(decision.non_comptee),
            creee.id if creee is not None else None,
            maintenant,
        ),
    )
    return creee


# Règles : désactivation et usage -----------------------------------------------------------------


def desactiver_regle(connexion: sqlite3.Connection, numero: int) -> Regle:
    """Désactive une règle active ; `RegleError` si elle est inconnue ou déjà inactive."""
    regle = lire_regles_par_id(connexion).get(numero)
    if regle is None:
        raise RegleError(f"règle n° {numero} inconnue (voir `budget rules list --toutes`)")
    if not regle.active:
        raise RegleError(f"règle n° {numero} déjà désactivée")
    connexion.execute(
        "UPDATE regles SET active = 0, desactivee_le = ? WHERE id = ?", (horodatage(), numero)
    )
    return lire_regles_par_id(connexion)[numero]


def lire_regles_par_id(connexion: sqlite3.Connection) -> dict[int, Regle]:
    return {r.id: r for r in lire_regles(connexion) if r.id is not None}


def operations_par_regle(connexion: sqlite3.Connection) -> dict[int, int]:
    """Nombre d'opérations classées par chaque règle, tous mois confondus."""
    requete = (
        "SELECT regle_id, COUNT(*) FROM classements WHERE regle_id IS NOT NULL GROUP BY regle_id"
    )
    return {int(r): int(n) for r, n in connexion.execute(requete)}


def mois_de_regle(connexion: sqlite3.Connection, numero: int) -> list[str]:
    requete = (
        "SELECT DISTINCT mois_budgetaire FROM classements WHERE regle_id = ? "
        "ORDER BY mois_budgetaire"
    )
    return [str(m) for (m,) in connexion.execute(requete, (numero,))]


# Propositions du catégoriseur --------------------------------------------------------------------


class PropositionEnregistree(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    mois: str
    source: Source
    cle_marchand: str
    proposee: SousCategorie | None
    modele: str | None
    corrigee: Imputation | None  # sous-catégorie ou « non compté » saisi par l'utilisateur
    regle_id: int | None
    proposee_le: datetime
    validee_le: datetime | None

    @property
    def retenue(self) -> Imputation | None:
        """La correction de l'utilisateur prime sur la proposition."""
        if self.corrigee is not None:
            return self.corrigee
        return Imputation(sous_categorie=self.proposee) if self.proposee is not None else None


_COLONNES_PROPOSITION = (
    "id",
    "mois",
    "source",
    "cle_marchand",
    "sous_categorie_proposee",
    "modele",
    "sous_categorie_corrigee",
    "corrigee_non_comptee",
    "regle_id",
    "proposee_le",
    "validee_le",
)


def _proposition_depuis_ligne(ligne: Sequence[object]) -> PropositionEnregistree:
    v = dict(zip(_COLONNES_PROPOSITION, ligne, strict=True))
    corrigee = Imputation(
        sous_categorie=_sous_categorie(v["sous_categorie_corrigee"]),
        non_comptee=bool(v["corrigee_non_comptee"]),
    )
    return PropositionEnregistree(
        id=int(str(v["id"])),
        mois=str(v["mois"]),
        source=Source(str(v["source"])),
        cle_marchand=str(v["cle_marchand"]),
        proposee=_sous_categorie(v["sous_categorie_proposee"]),
        modele=str(v["modele"]) if v["modele"] is not None else None,
        corrigee=corrigee if corrigee.decidee else None,
        regle_id=_entier(v["regle_id"]),
        proposee_le=datetime.fromisoformat(str(v["proposee_le"])),
        validee_le=_instant(v["validee_le"]),
    )


def lire_propositions(
    connexion: sqlite3.Connection, *, en_attente: bool = False
) -> list[PropositionEnregistree]:
    filtre = "WHERE validee_le IS NULL " if en_attente else ""
    requete = f"SELECT {', '.join(_COLONNES_PROPOSITION)} FROM propositions {filtre}ORDER BY id"
    return [_proposition_depuis_ligne(ligne) for ligne in connexion.execute(requete)]


def propositions_en_attente(
    connexion: sqlite3.Connection,
) -> dict[tuple[Source, str], PropositionEnregistree]:
    """Au plus une proposition en attente par `(source, cle_marchand)`."""
    attente: dict[tuple[Source, str], PropositionEnregistree] = {}
    for proposition in lire_propositions(connexion, en_attente=True):
        cle = (proposition.source, proposition.cle_marchand)
        if cle in attente:
            raise ControleError(
                f"propositions n° {attente[cle].id} et n° {proposition.id} en attente pour le "
                f"même marchand ({proposition.source})"
            )
        attente[cle] = proposition
    return attente


def enregistrer_propositions(
    connexion: sqlite3.Connection,
    mois: str,
    propositions: Mapping[tuple[Source, str], SousCategorie],
    modele: str | None,
) -> None:
    """Propositions reçues, enregistrées avant le dialogue ; une attente existante est gardée."""
    attente = propositions_en_attente(connexion)
    instant = horodatage()
    for (source, cle), sous_categorie in propositions.items():
        existante = attente.get((source, cle))
        if existante is not None and existante.retenue is not None:
            continue
        if existante is not None:
            connexion.execute(
                "UPDATE propositions SET sous_categorie_proposee = ?, modele = ? WHERE id = ?",
                (sous_categorie.value, modele, existante.id),
            )
            continue
        connexion.execute(
            "INSERT INTO propositions (mois, source, cle_marchand, sous_categorie_proposee, "
            "modele, proposee_le) VALUES (?, ?, ?, ?, ?, ?)",
            (mois, source.value, cle, sous_categorie.value, modele, instant),
        )


def corriger_proposition(
    connexion: sqlite3.Connection, mois: str, source: Source, cle: str, imputation: Imputation
) -> None:
    """Correction saisie dans le tableau : elle prime sur la proposition et survit à un `q`."""
    sous_categorie = imputation.sous_categorie.value if imputation.sous_categorie else None
    non_comptee = int(imputation.non_comptee)
    existante = propositions_en_attente(connexion).get((source, cle))
    if existante is not None:
        connexion.execute(
            "UPDATE propositions SET sous_categorie_corrigee = ?, corrigee_non_comptee = ? "
            "WHERE id = ?",
            (sous_categorie, non_comptee, existante.id),
        )
        return
    connexion.execute(
        "INSERT INTO propositions (mois, source, cle_marchand, sous_categorie_corrigee, "
        "corrigee_non_comptee, proposee_le) VALUES (?, ?, ?, ?, ?, ?)",
        (mois, source.value, cle, sous_categorie, non_comptee, horodatage()),
    )


def valider_proposition(connexion: sqlite3.Connection, regle: Regle) -> Regle:
    """Crée la règle marchand et marque validée la proposition en attente du même marchand."""
    existante = propositions_en_attente(connexion).get((regle.source, regle.valeur))
    if existante is None or existante.retenue is None:
        raise ControleError(f"aucune proposition en attente pour ce marchand ({regle.source})")
    creee = inserer_regle(connexion, regle)
    connexion.execute(
        "UPDATE propositions SET regle_id = ?, validee_le = ? WHERE id = ?",
        (creee.id, horodatage(), existante.id),
    )
    return creee
