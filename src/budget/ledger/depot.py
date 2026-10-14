"""Écriture et lecture des transactions dans le ledger SQLite."""

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict

from budget.categories import SousCategorie
from budget.classement.modeles import Classement, OrigineClassement, ResultatClassement
from budget.errors import ControleError, LedgerError
from budget.horloge import horodatage
from budget.ledger import memoire
from budget.ledger.memoire import PropositionEnregistree
from budget.ledger.migrations import appliquer_migrations, version
from budget.models import Nature, Source, Transaction
from budget.montants import depuis_centimes, en_centimes
from budget.questions.modeles import Question
from budget.regles import Decision, Memoire, Regle

_COLONNES = (
    "id",
    "source",
    "compte",
    "fichier",
    "position",
    "rang",
    "date_operation",
    "date_debit",
    "mois_budgetaire",
    "type_operation",
    "libelle",
    "libelle_brut",
    "libelle_normalise",
    "ics",
    "carte",
    "montant_centimes",
    "solde_apres_centimes",
)
_COLONNES_CLASSEMENT = (
    "transaction_id",
    "nature",
    "sous_categorie",
    "mois_rattachement",
    "origine",
    "detail",
    "contrepartie_id",
    "lien_id",
    "exceptionnel",
    "regle_id",
)
_TAILLE_LOT = 500
"""Nombre de paramètres d'une clause `IN`, sous la limite de variables de SQLite."""


@dataclass(frozen=True)
class FichierImporte:
    """Transactions d'un fichier, prêtes à être écrites."""

    source: Source
    fichier: str
    empreinte: str
    transactions: tuple[Transaction, ...]


class LigneRapport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    fichier: str
    lues: int
    ajoutees: int
    deja_presentes: int


class RapportImport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mois: str
    lignes: tuple[LigneRapport, ...]

    @property
    def lues(self) -> int:
        return sum(ligne.lues for ligne in self.lignes)

    @property
    def ajoutees(self) -> int:
        return sum(ligne.ajoutees for ligne in self.lignes)

    @property
    def deja_presentes(self) -> int:
        return sum(ligne.deja_presentes for ligne in self.lignes)


class Ledger:
    """Base SQLite du foyer. Ouvre (ou crée) la base et applique les migrations manquantes."""

    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        chemin.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit : chaque transaction est ouverte explicitement (BEGIN / COMMIT).
        self._connexion = sqlite3.connect(chemin, isolation_level=None)
        try:
            self._connexion.execute("PRAGMA foreign_keys = ON")
            appliquer_migrations(self._connexion, chemin.name)
        except sqlite3.DatabaseError as erreur:
            self._connexion.close()
            raise LedgerError(f"{chemin.name} : base SQLite illisible ({erreur})") from erreur
        except LedgerError:
            self._connexion.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        type_erreur: type[BaseException] | None,
        erreur: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.fermer()

    def fermer(self) -> None:
        self._connexion.close()

    @property
    def version(self) -> int:
        return version(self._connexion)

    # Écriture ---------------------------------------------------------------------------------

    def enregistrer_import(self, mois: str, fichiers: Sequence[FichierImporte]) -> RapportImport:
        """Écrit les imports d'un mois dans une seule transaction SQL : tout ou rien (RG-16)."""
        self._connexion.execute("BEGIN IMMEDIATE")
        try:
            lignes = [self._enregistrer_fichier(mois, fichier) for fichier in fichiers]
        except BaseException:
            self._connexion.execute("ROLLBACK")
            raise
        self._connexion.execute("COMMIT")
        return RapportImport(mois=mois, lignes=tuple(lignes))

    def _enregistrer_fichier(self, mois: str, fichier: FichierImporte) -> LigneRapport:
        identifiants = [t.id for t in fichier.transactions]
        if len(set(identifiants)) != len(identifiants):
            raise ControleError(f"RG-16 : {fichier.fichier} : identifiants en double")
        for transaction in fichier.transactions:
            if transaction.mois_budgetaire != mois or transaction.source != fichier.source:
                raise ControleError(
                    f"{fichier.fichier}, position {transaction.position} : transaction "
                    f"{transaction.source.value} du mois budgétaire "
                    f"{transaction.mois_budgetaire}, attendu {fichier.source.value} {mois}"
                )

        existant = self._connexion.execute(
            "SELECT id, importe_le FROM imports WHERE source = ? AND mois = ?",
            (fichier.source.value, mois),
        ).fetchone()
        if existant is not None:
            return self._comparer_import(mois, fichier, int(existant[0]), str(existant[1]))

        ailleurs = sorted(set(self._mois_des_identifiants(identifiants)))
        if ailleurs:
            raise ControleError(
                f"RG-16 : {fichier.fichier} contient des opérations déjà importées pour le mois "
                f"{', '.join(ailleurs)} : le fichier a probablement été déposé dans le mauvais "
                f"dossier (import demandé : {mois})"
            )

        curseur = self._connexion.execute(
            "INSERT INTO imports (source, mois, fichier, empreinte, nb_operations, importe_le) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                fichier.source.value,
                mois,
                fichier.fichier,
                fichier.empreinte,
                len(identifiants),
                horodatage(),
            ),
        )
        self._connexion.executemany(
            f"INSERT INTO transactions (import_id, {', '.join(_COLONNES)}) "
            f"VALUES (?, {', '.join('?' for _ in _COLONNES)})",
            ((curseur.lastrowid, *_vers_ligne(t)) for t in fichier.transactions),
        )
        return LigneRapport(
            source=fichier.source,
            fichier=fichier.fichier,
            lues=len(identifiants),
            ajoutees=len(identifiants),
            deja_presentes=0,
        )

    def _comparer_import(
        self, mois: str, fichier: FichierImporte, import_id: int, importe_le: str
    ) -> LigneRapport:
        """Réimport : sans effet si les identifiants sont identiques, refusé sinon (RG-16)."""
        en_base = {
            str(ligne[0])
            for ligne in self._connexion.execute(
                "SELECT id FROM transactions WHERE import_id = ?", (import_id,)
            )
        }
        nouveaux = {t.id for t in fichier.transactions}
        if nouveaux != en_base:
            raise ControleError(
                f"RG-16 : {fichier.source.value} {mois} déjà importé le {importe_le} avec un "
                f"contenu différent : {fichier.fichier} a {len(nouveaux - en_base)} opération(s) "
                f"en plus et {len(en_base - nouveaux)} en moins. Le remplacement d'un mois déjà "
                "importé n'est pas pris en charge"
            )
        return LigneRapport(
            source=fichier.source,
            fichier=fichier.fichier,
            lues=len(nouveaux),
            ajoutees=0,
            deja_presentes=len(nouveaux),
        )

    def _mois_des_identifiants(self, identifiants: list[str]) -> Iterator[str]:
        for debut in range(0, len(identifiants), _TAILLE_LOT):
            lot = identifiants[debut : debut + _TAILLE_LOT]
            requete = (
                "SELECT DISTINCT imports.mois FROM transactions "
                "JOIN imports ON imports.id = transactions.import_id "
                f"WHERE transactions.id IN ({', '.join('?' for _ in lot)})"
            )
            for (mois,) in self._connexion.execute(requete, lot):
                yield str(mois)

    def enregistrer_classement(self, mois: str, resultat: ResultatClassement) -> None:
        """Remplace atomiquement le classement du mois : c'est une projection recalculable."""
        if resultat.mois != mois:
            raise ControleError(f"classement du mois {resultat.mois} enregistré pour {mois}")
        attendus = {
            str(ligne[0])
            for ligne in self._connexion.execute(
                "SELECT id FROM transactions WHERE mois_budgetaire = ?", (mois,)
            )
        }
        recus = [c.transaction_id for c in resultat.classements]
        if len(set(recus)) != len(recus) or set(recus) != attendus:
            raise ControleError(
                f"classement du mois {mois} incomplet ou incohérent : {len(attendus)} "
                f"transaction(s) en base, {len(recus)} classement(s) reçus dont "
                f"{len(set(recus) - attendus)} hors du mois et {len(recus) - len(set(recus))} "
                "en double ; chaque transaction doit avoir exactement un classement"
            )

        self._connexion.execute("BEGIN IMMEDIATE")
        try:
            for table, colonne in (
                ("charges_fixes_transactions", "mois"),
                ("charges_fixes_mois", "mois"),
                ("alertes", "mois"),
                ("classements", "mois_budgetaire"),
            ):
                self._connexion.execute(f"DELETE FROM {table} WHERE {colonne} = ?", (mois,))
            self._connexion.executemany(
                f"INSERT INTO classements (mois_budgetaire, {', '.join(_COLONNES_CLASSEMENT)}) "
                f"VALUES (?, {', '.join('?' for _ in _COLONNES_CLASSEMENT)})",
                ((mois, *_classement_vers_ligne(c)) for c in resultat.classements),
            )
            self._connexion.executemany(
                "INSERT INTO alertes (mois, code, transaction_id, message) VALUES (?, ?, ?, ?)",
                ((mois, a.code, a.transaction_id, a.message) for a in resultat.alertes),
            )
            for r in resultat.rapprochements:
                self._connexion.execute(
                    "INSERT INTO charges_fixes_mois (mois, nom, attendu_centimes, reel_centimes, "
                    "seuil_pct, hors_seuil) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        mois,
                        r.nom,
                        en_centimes(r.attendu),
                        en_centimes(r.reel) if r.reel is not None else None,
                        str(r.seuil_pct),
                        int(r.hors_seuil),
                    ),
                )
                self._connexion.executemany(
                    "INSERT INTO charges_fixes_transactions (mois, nom, transaction_id) "
                    "VALUES (?, ?, ?)",
                    ((mois, r.nom, transaction_id) for transaction_id in r.transaction_ids),
                )
        except BaseException:
            self._connexion.execute("ROLLBACK")
            raise
        self._connexion.execute("COMMIT")

    # Mémoire des réponses (spec 06) ------------------------------------------------------------

    def _en_transaction[T](self, action: Callable[[], T]) -> T:
        self._connexion.execute("BEGIN IMMEDIATE")
        try:
            resultat = action()
        except BaseException:
            self._connexion.execute("ROLLBACK")
            raise
        self._connexion.execute("COMMIT")
        return resultat

    def regles(self) -> list[Regle]:
        """Toutes les règles apprises, actives ou non, par numéro croissant."""
        return memoire.lire_regles(self._connexion)

    def decisions(self) -> dict[str, Decision]:
        return memoire.lire_decisions(self._connexion)

    def memoire(self) -> Memoire:
        return Memoire(
            decisions=self.decisions(),
            regles=self.regles(),
            exceptionnels=memoire.lire_exceptionnels(self._connexion),
        )

    def desactiver_regle(self, numero: int) -> Regle:
        """Désactive une règle ; `RegleError` si elle est inconnue ou déjà inactive."""
        return self._en_transaction(lambda: memoire.desactiver_regle(self._connexion, numero))

    def operations_par_regle(self) -> dict[int, int]:
        return memoire.operations_par_regle(self._connexion)

    def mois_de_regle(self, numero: int) -> list[str]:
        return memoire.mois_de_regle(self._connexion, numero)

    def propositions(self, *, en_attente: bool = False) -> list[PropositionEnregistree]:
        return memoire.lire_propositions(self._connexion, en_attente=en_attente)

    def propositions_en_attente(
        self,
    ) -> dict[tuple[Source, str], PropositionEnregistree]:
        return memoire.propositions_en_attente(self._connexion)

    def enregistrer_propositions(
        self,
        mois: str,
        propositions: Mapping[tuple[Source, str], SousCategorie],
        modele: str | None,
    ) -> None:
        self._en_transaction(
            lambda: memoire.enregistrer_propositions(self._connexion, mois, propositions, modele)
        )

    def corriger_proposition(
        self, mois: str, source: Source, cle: str, sous_categorie: SousCategorie
    ) -> None:
        self._en_transaction(
            lambda: memoire.corriger_proposition(self._connexion, mois, source, cle, sous_categorie)
        )

    def valider_proposition(self, regle: Regle) -> Regle:
        """Règle marchand et proposition validée, dans une seule transaction SQL."""
        return self._en_transaction(lambda: memoire.valider_proposition(self._connexion, regle))

    def exceptionnels(self) -> dict[str, bool]:
        return memoire.lire_exceptionnels(self._connexion)

    def enregistrer_exceptionnel(self, transaction_id: str, exceptionnel: bool) -> None:
        self._en_transaction(
            lambda: memoire.enregistrer_exceptionnel(self._connexion, transaction_id, exceptionnel)
        )

    def lire_sql(self, requete: str, parametres: Sequence[object] = ()) -> list[tuple[object, ...]]:
        """Lecture brute, réservée à la restitution (journal) : jamais d'écriture."""
        if not requete.lstrip().upper().startswith("SELECT"):
            raise LedgerError("lire_sql n'accepte que des requêtes SELECT")
        return [tuple(ligne) for ligne in self._connexion.execute(requete, parametres)]

    def ajouter_regle(self, regle: Regle) -> Regle:
        """Ajoute une règle hors dialogue ; `RegleError` en cas de conflit ou de doublon."""
        return self._en_transaction(lambda: memoire.inserer_regle(self._connexion, regle))

    def marquer_question_posee(self, question: Question) -> int:
        return self._en_transaction(
            lambda: memoire.marquer_question_posee(self._connexion, question)
        )

    def enregistrer_reponse(
        self, question: Question, decisions: Sequence[Decision], regle: Regle | None
    ) -> Regle | None:
        """Écrit une réponse immédiatement et atomiquement ; renvoie la règle créée, numérotée."""
        return self._en_transaction(
            lambda: memoire.enregistrer_reponse(self._connexion, question, decisions, regle)
        )

    def liens_existants(self) -> set[str]:
        """Dépenses déjà liées à un virement (RG-07)."""
        requete = "SELECT DISTINCT lien_id FROM decisions WHERE lien_id IS NOT NULL"
        return {str(ligne[0]) for ligne in self._connexion.execute(requete)}

    # Lecture ----------------------------------------------------------------------------------

    def transactions(
        self, mois: str | None = None, source: Source | None = None
    ) -> list[Transaction]:
        """Transactions filtrées, triées par source puis position."""
        conditions: list[str] = []
        parametres: list[str] = []
        if mois is not None:
            conditions.append("mois_budgetaire = ?")
            parametres.append(mois)
        if source is not None:
            conditions.append("source = ?")
            parametres.append(source.value)
        filtre = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        requete = (
            f"SELECT {', '.join(_COLONNES)} FROM transactions {filtre} ORDER BY source, position"
        )
        return [_depuis_ligne(ligne) for ligne in self._connexion.execute(requete, parametres)]

    def classements(self, mois: str) -> list[Classement]:
        """Classement enregistré du mois, dans l'ordre des transactions (source, position)."""
        colonnes = ", ".join(f"classements.{c}" for c in _COLONNES_CLASSEMENT)
        requete = (
            f"SELECT {colonnes} FROM classements "
            "JOIN transactions ON transactions.id = classements.transaction_id "
            "WHERE classements.mois_budgetaire = ? "
            "ORDER BY transactions.source, transactions.position"
        )
        return [
            _classement_depuis_ligne(ligne) for ligne in self._connexion.execute(requete, (mois,))
        ]

    def mois_importes(self) -> list[str]:
        requete = "SELECT DISTINCT mois FROM imports ORDER BY mois"
        return [str(ligne[0]) for ligne in self._connexion.execute(requete)]


def _vers_ligne(t: Transaction) -> tuple[object, ...]:
    """Dates en ISO `AAAA-MM-JJ`, montants en centimes entiers : aucun adaptateur implicite."""
    return (
        t.id,
        t.source.value,
        t.compte.value,
        t.fichier,
        t.position,
        t.rang,
        t.date_operation.isoformat(),
        t.date_debit.isoformat() if t.date_debit else None,
        t.mois_budgetaire,
        t.type_operation,
        t.libelle,
        t.libelle_brut,
        t.libelle_normalise,
        t.ics,
        t.carte,
        en_centimes(t.montant),
        en_centimes(t.solde_apres) if t.solde_apres is not None else None,
    )


def _depuis_ligne(ligne: Sequence[object]) -> Transaction:
    valeurs = dict(zip(_COLONNES, ligne, strict=True))
    montant = valeurs.pop("montant_centimes")
    solde = valeurs.pop("solde_apres_centimes")
    date_debit = valeurs["date_debit"]
    if not isinstance(montant, int) or not (solde is None or isinstance(solde, int)):
        raise LedgerError(f"transaction {valeurs['id']} : montant non entier en base")
    return Transaction.model_validate(
        {
            **valeurs,
            "date_operation": date.fromisoformat(str(valeurs["date_operation"])),
            "date_debit": date.fromisoformat(str(date_debit)) if date_debit else None,
            "montant": depuis_centimes(montant),
            "solde_apres": depuis_centimes(solde) if solde is not None else None,
        }
    )


def _classement_vers_ligne(c: Classement) -> tuple[object, ...]:
    return (
        c.transaction_id,
        c.nature.value if c.nature is not None else None,
        c.sous_categorie.value if c.sous_categorie is not None else None,
        c.mois_rattachement,
        c.origine.value,
        c.detail,
        c.contrepartie_id,
        c.lien_id,
        int(c.exceptionnel),
        c.regle_id,
    )


def _classement_depuis_ligne(ligne: Sequence[object]) -> Classement:
    valeurs = dict(zip(_COLONNES_CLASSEMENT, ligne, strict=True))
    nature, sous_categorie = valeurs["nature"], valeurs["sous_categorie"]
    return Classement.model_validate(
        {
            **valeurs,
            "nature": Nature(str(nature)) if nature is not None else None,
            "sous_categorie": SousCategorie(str(sous_categorie)) if sous_categorie else None,
            "origine": OrigineClassement(str(valeurs["origine"])),
            "exceptionnel": bool(valeurs["exceptionnel"]),
            "regle_id": int(str(valeurs["regle_id"])) if valeurs["regle_id"] is not None else None,
        }
    )
