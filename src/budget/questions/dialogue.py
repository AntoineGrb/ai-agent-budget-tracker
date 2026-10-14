"""Dialogue dans le terminal : une question à la fois, réponse enregistrée aussitôt (spec 06).

Le terminal est local : le libellé y est affiché, mais jamais journalisé.
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from rich.console import Console, RenderableType
from rich.text import Text

from budget.categories import Categorie, SousCategorie, categorie_de, libelle_de
from budget.classement.modeles import Classement, OrigineClassement
from budget.config import Contexte
from budget.errors import RegleError
from budget.ledger import Ledger
from budget.models import Nature, Source, Transaction
from budget.montants import formater_montant
from budget.questions.candidats import EtatLiens, candidats_lien, page
from budget.questions.modeles import Question, Reponse, TypeQuestion
from budget.questions.reponses import (
    construire_regle,
    critere_propose,
    decisions_pour,
    lire_plage,
    lire_prefixe,
    regle_toujours_demander,
)
from budget.regles import Critere, Regle, TypeRegle, verifier_nouvelle_regle

journal = logging.getLogger(__name__)

PASSER, QUITTER, ANNULER, PAGE_SUIVANTE = "s", "q", "a", "p"


class Interlocuteur(Protocol):
    def afficher(self, contenu: RenderableType) -> None: ...

    def demander(self, invite: str, choix_valides: Sequence[str]) -> str: ...

    def saisir(self, invite: str) -> str: ...


class Abandon(Exception):
    """L'utilisateur quitte le dialogue (`q` ou Ctrl+C) ; les réponses données sont conservées."""


class InterlocuteurTerminal:
    """Saisie Rich ; ressaisie tant que la réponse n'est pas l'un des choix valides."""

    def __init__(self, console: Console, lire: Callable[[str], str] | None = None) -> None:
        self.console = console
        self._lire = lire or (lambda invite: console.input(invite))

    def afficher(self, contenu: RenderableType) -> None:
        self.console.print(contenu, highlight=False)

    def demander(self, invite: str, choix_valides: Sequence[str]) -> str:
        while True:
            reponse = self._lire(f"{invite} ").strip().lower()
            if reponse in choix_valides:
                return reponse
            visibles = ", ".join(c if c else "Entrée" for c in choix_valides)
            self.console.print(f"Réponse invalide. Choix possibles : {visibles}", markup=False)

    def saisir(self, invite: str) -> str:
        return self._lire(f"{invite} ").strip()


class ScenarioError(Exception):
    """Scénario de test épuisé ou réponse invalide."""


class InterlocuteurScripte:
    """Rejoue une liste de réponses (tests, et plus tard réponses saisies ailleurs)."""

    def __init__(self, reponses: Iterable[str]) -> None:
        self._reponses = list(reponses)
        self.affichages: list[RenderableType] = []
        self.invites: list[str] = []

    @property
    def restantes(self) -> int:
        return len(self._reponses)

    def afficher(self, contenu: RenderableType) -> None:
        self.affichages.append(contenu)

    def _suivante(self, invite: str) -> str:
        self.invites.append(invite)
        if not self._reponses:
            raise ScenarioError(f"scénario épuisé à l'invite {invite!r}")
        return self._reponses.pop(0)

    def demander(self, invite: str, choix_valides: Sequence[str]) -> str:
        reponse = self._suivante(invite)
        if reponse not in choix_valides:
            raise ScenarioError(
                f"réponse {reponse!r} invalide à l'invite {invite!r}, "
                f"attendu : {list(choix_valides)}"
            )
        return reponse

    def saisir(self, invite: str) -> str:
        return self._suivante(invite)

    def texte(self) -> str:
        """Tout ce qui a été affiché, en texte brut."""
        return "\n".join(str(a) for a in self.affichages)


@dataclass
class Bilan:
    a_poser: int
    posees: int = 0
    repondues: int = 0
    passees: int = 0
    regles_creees: int = 0
    interrompu: bool = False


# Choix proposés --------------------------------------------------------------------------------

_CHOIX_VIREMENT_SORTIE: tuple[tuple[str, Nature], ...] = (
    ("Dotation", Nature.DOTATION),
    ("Épargne", Nature.EPARGNE),
    ("Complément", Nature.COMPLEMENT),
    ("Lien à une dépense", Nature.LIEN_DEPENSE),
    ("Dépense", Nature.DEPENSE),
    ("Hors budget", Nature.HORS_BUDGET),
)
_CHOIX_VIREMENT_ENTREE: tuple[tuple[str, Nature], ...] = (
    ("Dotation", Nature.DOTATION),
    ("Désépargne", Nature.EPARGNE),
    ("Complément", Nature.COMPLEMENT),
    ("Lien à une dépense", Nature.LIEN_DEPENSE),
    ("Remboursement", Nature.REMBOURSEMENT),  # RG-09
    ("Revenu", Nature.REVENU),
    ("Hors budget", Nature.HORS_BUDGET),  # RG-10
)
_CHOIX_OPERATION_SORTIE: tuple[tuple[str, Nature], ...] = (
    ("Dépense", Nature.DEPENSE),
    ("Hors budget", Nature.HORS_BUDGET),
)
_CHOIX_OPERATION_ENTREE: tuple[tuple[str, Nature], ...] = (
    ("Remboursement", Nature.REMBOURSEMENT),
    ("Revenu", Nature.REVENU),
    ("Hors budget", Nature.HORS_BUDGET),
)


def choix_de_nature(question: Question) -> tuple[tuple[str, Nature], ...]:
    sortie = question.transaction.montant < 0
    if question.type is TypeQuestion.VIREMENT:
        return _CHOIX_VIREMENT_SORTIE if sortie else _CHOIX_VIREMENT_ENTREE
    return _CHOIX_OPERATION_SORTIE if sortie else _CHOIX_OPERATION_ENTREE


def _menu(options: Sequence[str], suffixes: Sequence[tuple[str, str]], par_ligne: int = 3) -> Text:
    elements = [f"{i}. {libelle}" for i, libelle in enumerate(options, 1)]
    elements += [f"{cle}. {libelle}" for cle, libelle in suffixes]
    lignes = [elements[i : i + par_ligne] for i in range(0, len(elements), par_ligne)]
    return Text("\n".join("  " + "".join(e.ljust(24) for e in ligne).rstrip() for ligne in lignes))


def _signe(montant: Decimal) -> str:
    texte = formater_montant(montant)
    return f"+{texte}" if montant > 0 else texte


def _compte(transaction: Transaction) -> str:
    return "Perso" if transaction.source is Source.TR else "Joint"


def _ligne(transaction: Transaction, suffixe: str = "") -> str:
    return (
        f"  {_compte(transaction)} · {transaction.date_operation:%d/%m/%Y} · "
        f"{_signe(transaction.montant)} · {transaction.type_operation or ''}{suffixe}"
    )


_TITRES = {
    TypeQuestion.VIREMENT: "Virement",
    TypeQuestion.SALAIRE: "Salaire",
    TypeQuestion.OPERATION: "Opération",
}


class Dialogue:
    """Pose les questions une par une et enregistre chaque réponse immédiatement."""

    def __init__(
        self, interlocuteur: Interlocuteur, ledger: Ledger, contexte: Contexte, liens: EtatLiens
    ) -> None:
        self.interlocuteur = interlocuteur
        self.ledger = ledger
        self.contexte = contexte
        self.liens = liens

    def derouler(self, questions: Sequence[Question]) -> Bilan:
        bilan = Bilan(a_poser=len(questions))
        try:
            for numero, question in enumerate(questions, 1):
                bilan.posees += 1
                self.ledger.marquer_question_posee(question)
                self._afficher_question(question, numero, len(questions))
                reponse = self._repondre(question)
                if reponse is None:
                    bilan.passees += 1
                    continue
                regle = self._proposer_regle(question, reponse)
                creee = self.ledger.enregistrer_reponse(
                    question, decisions_pour(question, reponse), regle
                )
                self._noter(question, reponse)
                bilan.repondues += 1
                if creee is not None:
                    bilan.regles_creees += 1
                    self.interlocuteur.afficher(Text(f"  Règle créée : {creee.decrire()}"))
        except (Abandon, KeyboardInterrupt, EOFError):
            bilan.interrompu = True
        journal.info(
            "questions : %d à poser, %d posées, %d répondues, %d passées, %d règles créées",
            bilan.a_poser,
            bilan.posees,
            bilan.repondues,
            bilan.passees,
            bilan.regles_creees,
        )
        return bilan

    # Affichage ---------------------------------------------------------------------------------

    def _afficher_question(self, question: Question, numero: int, total: int) -> None:
        t = question.transaction
        motif = "non classée" if question.motif == "non_classe" else question.motif
        lignes = ["", f"Question {numero}/{total} — {_TITRES[question.type]} ({motif})"]
        if question.type is TypeQuestion.SALAIRE:
            fenetre = self.contexte.fenetre_salaire
            lignes += [
                f"  {_compte(t)} · {t.date_operation:%d/%m/%Y} · {_signe(t.montant)} · "
                f"{question.nom_salaire}",
                f"  Reçu hors de la fenêtre du {fenetre.du_jour} au "
                f"{fenetre.au_jour_mois_suivant}, sans période dans le libellé.",
            ]
        else:
            lignes.append(_ligne(t))
            if question.contrepartie is not None:
                lignes.append(_ligne(question.contrepartie, " (contrepartie)"))
            lignes.append(f"  {t.libelle}")
        self.interlocuteur.afficher(Text("\n".join(lignes)))

    def _choisir(self, options: Sequence[str], suffixes: Sequence[tuple[str, str]]) -> str:
        self.interlocuteur.afficher(_menu(options, suffixes))
        valides = [str(i) for i in range(1, len(options) + 1)] + [cle for cle, _ in suffixes]
        choix = self.interlocuteur.demander(">", valides)
        if choix == QUITTER:
            raise Abandon
        return choix

    # Réponse -----------------------------------------------------------------------------------

    def _repondre(self, question: Question) -> Reponse | None:
        """Réponse à la question, ou `None` pour la passer."""
        sortie_ou_passer = ((PASSER, "Passer"), (QUITTER, "Quitter"))
        if question.type is TypeQuestion.SALAIRE:
            self.interlocuteur.afficher(Text("  Mois de paie ?"))
            choix = self._choisir(question.mois_possibles, sortie_ou_passer)
            if choix == PASSER:
                return None
            mois = question.mois_possibles[int(choix) - 1]
            return Reponse(nature=Nature.REVENU, mois_paie=mois)

        options = choix_de_nature(question)
        while True:
            choix = self._choisir([libelle for libelle, _ in options], sortie_ou_passer)
            if choix == PASSER:
                return None
            nature = options[int(choix) - 1][1]
            reponse = self._completer(question, nature)
            if reponse is not None:
                return reponse

    def _completer(self, question: Question, nature: Nature) -> Reponse | None:
        """Précise la réponse (dotation, dépense liée, sous-catégorie) ; `None` = annulé."""
        if nature is Nature.DOTATION:
            dotations = self.contexte.dotations
            choix = self._choisir([d.nom for d in dotations], ((ANNULER, "Annuler"),))
            if choix == ANNULER:
                return None
            return Reponse(nature=nature, detail=f"dotations.{dotations[int(choix) - 1].nom}")
        if nature is Nature.LIEN_DEPENSE:
            lien = self._choisir_depense(question)
            return Reponse(nature=nature, lien_id=lien) if lien is not None else None
        if nature in (Nature.DEPENSE, Nature.REMBOURSEMENT):
            sous_categorie = self._choisir_sous_categorie()
            if sous_categorie is None:
                return None
            return Reponse(nature=nature, sous_categorie=sous_categorie)
        return Reponse(nature=nature)

    def _choisir_sous_categorie(self) -> SousCategorie | None:
        categories = list(Categorie)
        while True:
            self.interlocuteur.afficher(Text("  Catégorie ?"))
            choix = self._choisir([c.libelle for c in categories], ((ANNULER, "Annuler"),))
            if choix == ANNULER:
                return None
            categorie = categories[int(choix) - 1]
            sous_categories = [sc for sc in SousCategorie if categorie_de(sc) is categorie]
            self.interlocuteur.afficher(Text(f"  Sous-catégorie de {categorie.libelle} ?"))
            choix = self._choisir(
                [libelle_de(sc) for sc in sous_categories], ((ANNULER, "Retour aux catégories"),)
            )
            if choix != ANNULER:
                return sous_categories[int(choix) - 1]

    def _choisir_depense(self, question: Question) -> str | None:
        """RG-07 : dépense liée parmi les candidates, 10 par page ; `None` = annulé."""
        candidats = candidats_lien(question, self.liens)
        if not candidats:
            self.interlocuteur.afficher(Text("  Aucune dépense candidate (RG-07)."))
            return None
        numero_page = 0
        while True:
            affichees = page(candidats, numero_page)
            options = [
                f"{_compte(t)} · {t.date_operation:%d/%m/%Y} · {_signe(t.montant)} · {t.libelle}"
                for t in affichees
            ]
            self.interlocuteur.afficher(Text("  Dépense liée ?"))
            self.interlocuteur.afficher(
                Text("\n".join(f"  {i}. {o}" for i, o in enumerate(options, 1)))
            )
            suffixes = [(ANNULER, "Annuler")]
            if page(candidats, numero_page + 1):
                suffixes.insert(0, (PAGE_SUIVANTE, "Page suivante"))
            self.interlocuteur.afficher(_menu([], suffixes))
            valides = [str(i) for i in range(1, len(affichees) + 1)] + [c for c, _ in suffixes]
            choix = self.interlocuteur.demander(">", valides)
            if choix == ANNULER:
                return None
            if choix == PAGE_SUIVANTE:
                numero_page += 1
                continue
            return affichees[int(choix) - 1].id

    # Règle -------------------------------------------------------------------------------------

    def _proposer_regle(self, question: Question, reponse: Reponse) -> Regle | None:
        """Proposition de règle (cadrage §7), sauf salaire, lien à une dépense et RG-14."""
        if (
            not question.regle_proposable
            or question.type is TypeQuestion.SALAIRE
            or reponse.nature is Nature.LIEN_DEPENSE
        ):
            return None
        while True:
            choix = self.interlocuteur.demander(
                "En faire une règle ?  o. Oui   n. Non (cette opération seulement)   "
                "t. Toujours demander",
                ["o", "n", "t"],
            )
            if choix == "n":
                return None
            regle = (
                regle_toujours_demander(question)
                if choix == "t"
                else self._construire_regle(question, reponse)
            )
            try:
                verifier_nouvelle_regle(regle, self.ledger.regles())
            except RegleError as erreur:
                self.interlocuteur.afficher(Text(f"  {erreur}"))
                continue
            return regle

    def _construire_regle(self, question: Question, reponse: Reponse) -> Regle:
        transaction = question.transaction
        type_regle, critere, valeur = critere_propose(question)
        if critere is Critere.LIBELLE_EXACT and type_regle is TypeRegle.PRELEVEMENT:
            choix = self.interlocuteur.demander(
                "Critère ?  Entrée. Libellé exact   x. Préfixe du libellé", ["", "x"]
            )
            if choix == "x":
                critere = Critere.LIBELLE_PREFIXE
                valeur = self._saisir(
                    "Préfixe du libellé :", lambda texte: lire_prefixe(texte, transaction)
                )

        if type_regle is TypeRegle.VIREMENT:  # condition de montant obligatoire
            condition = self.interlocuteur.demander(
                "Condition de montant ?  Entrée/e. Montant exact   p. Plage", ["", "e", "p"]
            )
            condition = condition or "e"
        else:
            condition = self.interlocuteur.demander(
                "Condition de montant ?  Entrée/n. Aucune   e. Montant exact   p. Plage",
                ["", "n", "e", "p"],
            )
            condition = condition or "n"

        minimum = maximum = None
        if condition == "e":
            minimum = maximum = transaction.montant
        elif condition == "p":
            minimum, maximum = self._saisir(
                "Plage en valeur absolue (ex. 500-600) :",
                lambda texte: lire_plage(texte, transaction),
            )
        return construire_regle(
            question,
            reponse,
            critere=critere,
            valeur=valeur,
            montant_min=minimum,
            montant_max=maximum,
        )

    def _saisir[T](self, invite: str, lire: Callable[[str], T]) -> T:
        while True:
            try:
                return lire(self.interlocuteur.saisir(invite))
            except ValueError as erreur:
                self.interlocuteur.afficher(Text(f"  Saisie refusée : {erreur}"))

    # Suivi -------------------------------------------------------------------------------------

    def _noter(self, question: Question, reponse: Reponse) -> None:
        """Met à jour les candidates RG-07 avec la réponse donnée."""
        if reponse.lien_id is not None:
            self.liens.deja_lies.add(reponse.lien_id)
        for t in question.transactions:
            self.liens.classements[t.id] = Classement(
                transaction_id=t.id,
                nature=reponse.nature,
                sous_categorie=reponse.sous_categorie,
                mois_rattachement=t.mois_budgetaire,
                origine=OrigineClassement.DECISION,
                detail=reponse.detail,
                lien_id=reponse.lien_id,
            )
