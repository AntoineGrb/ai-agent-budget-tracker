"""Dialogue dans le terminal : une question à la fois, réponse enregistrée aussitôt (cadrage §7).

Le terminal est local : le libellé y est affiché, mais jamais journalisé.
"""

import io
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from rich.console import Console, RenderableType
from rich.text import Text

from budget.categories import NON_COMPTE, Categorie, Imputation, libelle_de, sous_categories_de
from budget.errors import RegleError
from budget.ledger import Ledger
from budget.models import Source, Transaction
from budget.montants import formater_montant
from budget.questions.modeles import Question, TypeQuestion
from budget.questions.reponses import (
    construire_regle,
    critere_propose,
    decision_pour,
    lire_plage,
    lire_prefixe,
    regle_toujours_demander,
)
from budget.regles import Critere, Regle, TypeRegle, regle_retenue, verifier_nouvelle_regle

journal = logging.getLogger(__name__)

PASSER, QUITTER, ANNULER = "s", "q", "a"


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
    """Rejoue une liste de réponses (tests)."""

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
        """Tout ce qui a été affiché, rendu en texte brut (tableaux compris)."""
        console = Console(width=200, record=True, file=io.StringIO())
        for affichage in self.affichages:
            console.print(affichage, markup=False, highlight=False)
        return console.export_text()


@dataclass
class Bilan:
    a_poser: int
    posees: int = 0
    repondues: int = 0
    passees: int = 0
    couvertes: int = 0  # sautées : couvertes par une règle créée plus tôt dans le dialogue
    regles_creees: int = 0
    interrompu: bool = False


# Menus -----------------------------------------------------------------------------------------


def menu(options: Sequence[str], suffixes: Sequence[tuple[str, str]], par_ligne: int = 3) -> Text:
    elements = [f"{i}. {libelle}" for i, libelle in enumerate(options, 1)]
    elements += [f"{cle}. {libelle}" for cle, libelle in suffixes]
    lignes = [elements[i : i + par_ligne] for i in range(0, len(elements), par_ligne)]
    return Text("\n".join("  " + "".join(e.ljust(24) for e in ligne).rstrip() for ligne in lignes))


def montant_signe(montant: Decimal) -> str:
    texte = formater_montant(montant)
    return f"+{texte}" if montant > 0 else texte


def nom_compte(transaction: Transaction) -> str:
    return "Perso" if transaction.source is Source.TR else "Joint"


def choisir(
    interlocuteur: Interlocuteur, options: Sequence[str], suffixes: Sequence[tuple[str, str]]
) -> str:
    """Menu numéroté ; `q` lève `Abandon`."""
    interlocuteur.afficher(menu(options, suffixes))
    valides = [str(i) for i in range(1, len(options) + 1)] + [cle for cle, _ in suffixes]
    choix = interlocuteur.demander(">", valides)
    if choix == QUITTER:
        raise Abandon
    return choix


def choisir_imputation(
    interlocuteur: Interlocuteur, suffixes: Sequence[tuple[str, str]]
) -> Imputation | None:
    """Catégorie puis sous-catégorie, ou « Non compté » ; `None` pour un suffixe (passer, annuler).

    Menus construits depuis `categories.py`.
    """
    categories = list(Categorie)
    while True:
        interlocuteur.afficher(Text("  Catégorie ?"))
        choix = choisir(interlocuteur, [*(c.libelle for c in categories), NON_COMPTE], suffixes)
        if not choix.isdigit():
            return None
        if int(choix) == len(categories) + 1:
            return Imputation(non_comptee=True)  # RG-10
        categorie = categories[int(choix) - 1]
        sous_categories = sous_categories_de(categorie)
        interlocuteur.afficher(Text(f"  Sous-catégorie de {categorie.libelle} ?"))
        choix = choisir(
            interlocuteur,
            [libelle_de(sc) for sc in sous_categories],
            ((ANNULER, "Retour aux catégories"),),
        )
        if choix != ANNULER:
            return Imputation(sous_categorie=sous_categories[int(choix) - 1])


_TITRES = {TypeQuestion.VIREMENT: "Virement", TypeQuestion.OPERATION: "Opération"}


class Dialogue:
    """Pose les questions une par une et enregistre chaque réponse immédiatement."""

    def __init__(self, interlocuteur: Interlocuteur, ledger: Ledger) -> None:
        self.interlocuteur = interlocuteur
        self.ledger = ledger

    def derouler(self, questions: Sequence[Question]) -> Bilan:
        bilan = Bilan(a_poser=len(questions))
        try:
            for numero, question in enumerate(questions, 1):
                if (
                    question.regle_proposable
                    and regle_retenue(self.ledger.regles(), question.transaction) is not None
                ):
                    bilan.couvertes += 1
                    continue
                bilan.posees += 1
                self.ledger.marquer_question_posee(question)
                self._afficher_question(question, numero, len(questions))
                reponse = choisir_imputation(
                    self.interlocuteur, ((PASSER, "Passer"), (QUITTER, "Quitter"))
                )
                if reponse is None:
                    bilan.passees += 1
                    continue
                regle = self._proposer_regle(question, reponse)
                creee = self.ledger.enregistrer_reponse(
                    question, decision_pour(question, reponse), regle
                )
                bilan.repondues += 1
                if creee is not None:
                    bilan.regles_creees += 1
                    self.interlocuteur.afficher(Text(f"  Règle créée : {creee.decrire()}"))
        except (Abandon, KeyboardInterrupt, EOFError):
            bilan.interrompu = True
        journal.info(
            "questions : %d à poser, %d posées, %d répondues, %d passées, %d couvertes, "
            "%d règles créées",
            bilan.a_poser,
            bilan.posees,
            bilan.repondues,
            bilan.passees,
            bilan.couvertes,
            bilan.regles_creees,
        )
        return bilan

    def _afficher_question(self, question: Question, numero: int, total: int) -> None:
        t = question.transaction
        lignes = [
            "",
            f"Question {numero}/{total} — {_TITRES[question.type]} ({question.motif})",
            f"  {nom_compte(t)} · {t.date_operation:%d/%m/%Y} · {montant_signe(t.montant)} · "
            f"{t.type_operation or ''}",
            f"  {t.libelle}",
        ]
        self.interlocuteur.afficher(Text("\n".join(lignes)))

    # Règle -------------------------------------------------------------------------------------

    def _proposer_regle(self, question: Question, reponse: Imputation) -> Regle | None:
        """Proposition de règle (cadrage §7), sauf RG-14."""
        if not question.regle_proposable:
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

    def _construire_regle(self, question: Question, reponse: Imputation) -> Regle:
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
