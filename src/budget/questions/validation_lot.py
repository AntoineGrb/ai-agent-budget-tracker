"""Validation en lot des paiements carte (cadrage §7).

Une ligne par groupe marchand en attente, avec la proposition du catégoriseur, puis une ligne par
paiement Amazon à compléter (RG-13). Entrée valide tout ; un numéro corrige une ligne, vers une
autre sous-catégorie ou « non compté » ; `q` quitte. Chaque groupe validé devient une règle
`marchand_carte` ; chaque ligne Amazon, une décision.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from rich.table import Table
from rich.text import Text

from budget.agents.categoriseur import Categoriseur
from budget.categories import Imputation, SousCategorie
from budget.classement.cartes import GroupeMarchand
from budget.errors import CategorisationError, RegleError
from budget.ledger import Ledger
from budget.libelles import cle_marchand
from budget.models import Compte, Transaction
from budget.montants import formater_montant
from budget.questions.dialogue import ANNULER, QUITTER, Abandon, Interlocuteur, choisir_imputation
from budget.questions.modeles import Question, TypeQuestion
from budget.questions.reponses import decision_pour
from budget.regles import Critere, Regle, TypeRegle

VALIDER = ""
MOTIF_RG13 = "RG-13"


@dataclass
class LigneLot:
    groupe: GroupeMarchand | None = None
    operation: Transaction | None = None  # RG-13 : paiement Amazon à compléter
    proposee: SousCategorie | None = None
    corrigee: Imputation | None = None
    validee: bool = False
    refus: str | None = None

    @property
    def retenue(self) -> Imputation | None:
        if self.corrigee is not None:
            return self.corrigee
        return Imputation(sous_categorie=self.proposee) if self.proposee is not None else None

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        if self.groupe is not None:
            return self.groupe.transactions
        assert self.operation is not None
        return (self.operation,)

    @property
    def compte(self) -> str:
        return "Perso" if self.transactions[0].compte is Compte.PERSO else "Joint"

    @property
    def marchand(self) -> str:
        if self.groupe is not None:
            return self.groupe.cle
        assert self.operation is not None
        cle = cle_marchand(self.operation.libelle_normalise)
        return f"{cle} · {self.operation.date_operation:%d/%m/%Y}"

    def proposition(self) -> str:
        retenue = self.retenue
        if retenue is None:
            return "à compléter (RG-13)" if self.operation is not None else "—"
        suffixe = " (corrigée)" if self.corrigee is not None else ""
        return f"{retenue.libelle_imputation()}{suffixe}"


@dataclass
class BilanLot:
    lignes: int = 0
    proposes: int = 0  # groupes avec une proposition du catégoriseur
    valides: int = 0  # lignes validées (groupes et RG-13)
    corriges: int = 0
    en_attente: int = 0
    regles_creees: int = 0
    avertissement: str | None = None  # raison du mode dégradé, sans contenu
    interrompu: bool = False


def tableau(mois: str, lignes: Sequence[LigneLot]) -> Table:
    table = Table(title=f"Paiements carte à valider — {mois} ({len(lignes)} lignes)")
    for colonne in ("N°", "Compte", "Marchand", "Op.", "Total", "Proposition"):
        table.add_column(colonne, justify="right" if colonne in ("N°", "Op.", "Total") else "left")
    for numero, ligne in enumerate(lignes, 1):
        total = sum((t.montant for t in ligne.transactions), start=Decimal(0))
        table.add_row(
            str(numero),
            ligne.compte,
            ligne.marchand,
            str(len(ligne.transactions)),
            formater_montant(total),
            ligne.proposition(),
        )
    return table


class ValidationLot:
    """Déroule la validation en lot pour un mois."""

    def __init__(
        self,
        interlocuteur: Interlocuteur,
        ledger: Ledger,
        mois: str,
        categoriseur: Categoriseur,
    ) -> None:
        self.interlocuteur = interlocuteur
        self.ledger = ledger
        self.mois = mois
        self.categoriseur = categoriseur

    def derouler(
        self, groupes: Sequence[GroupeMarchand], amazon: Sequence[Transaction]
    ) -> BilanLot:
        bilan = BilanLot()
        bilan.avertissement = self._proposer(groupes)
        attente = self.ledger.propositions_en_attente()
        lignes = []
        for g in groupes:
            enregistree = attente.get((g.source, g.cle))
            lignes.append(
                LigneLot(
                    groupe=g,
                    proposee=enregistree.proposee if enregistree is not None else None,
                    corrigee=enregistree.corrigee if enregistree is not None else None,
                )
            )
        lignes += [LigneLot(operation=t) for t in amazon]
        bilan.lignes = len(lignes)
        bilan.proposes = sum(1 for ligne in lignes if ligne.proposee is not None)
        if bilan.avertissement is not None:
            self.interlocuteur.afficher(
                Text(f"  Avertissement : {bilan.avertissement} ; propositions à saisir.")
            )
        try:
            self._dialoguer(lignes)
        except (Abandon, KeyboardInterrupt, EOFError):
            bilan.interrompu = True
        bilan.valides = sum(1 for ligne in lignes if ligne.validee)
        bilan.corriges = sum(1 for ligne in lignes if ligne.corrigee is not None)
        bilan.en_attente = sum(1 for ligne in lignes if not ligne.validee)
        bilan.regles_creees = sum(1 for ligne in lignes if ligne.validee and ligne.groupe)
        return bilan

    def _proposer(self, groupes: Sequence[GroupeMarchand]) -> str | None:
        """Appel au catégoriseur pour les groupes sans proposition en attente."""
        attente = self.ledger.propositions_en_attente()
        a_soumettre = [
            g for g in groupes if (p := attente.get((g.source, g.cle))) is None or p.retenue is None
        ]
        if not a_soumettre:
            return None
        avertissement = None
        try:
            propositions = self.categoriseur.proposer(a_soumettre)
        except CategorisationError as erreur:
            avertissement = erreur.raison
            propositions = erreur.partielles
        if propositions:
            self.ledger.enregistrer_propositions(
                self.mois, propositions, self.categoriseur.nom_modele
            )
        return avertissement

    def _dialoguer(self, lignes: list[LigneLot]) -> None:
        while True:
            self.interlocuteur.afficher(tableau(self.mois, lignes))
            numeros = [str(i) for i in range(1, len(lignes) + 1)]
            choix = self.interlocuteur.demander(
                "Entrée : tout valider · numéro : corriger une ligne · q : quitter",
                [VALIDER, *numeros, QUITTER],
            )
            if choix == QUITTER:
                raise Abandon
            if choix == VALIDER:
                self._valider(lignes)
                return
            self._corriger(lignes[int(choix) - 1])

    def _corriger(self, ligne: LigneLot) -> None:
        imputation = choisir_imputation(self.interlocuteur, ((ANNULER, "Annuler"),))
        if imputation is None:
            return
        ligne.corrigee = imputation
        if ligne.groupe is not None:  # enregistrée aussitôt : survit à un `q`
            self.ledger.corriger_proposition(
                self.mois, ligne.groupe.source, ligne.groupe.cle, imputation
            )

    def _valider(self, lignes: Sequence[LigneLot]) -> None:
        for ligne in lignes:
            retenue = ligne.retenue
            if retenue is None:
                continue
            if ligne.groupe is not None:
                regle = Regle(
                    type=TypeRegle.MARCHAND_CARTE,
                    source=ligne.groupe.source,
                    critere=Critere.LIBELLE_EXACT,
                    valeur=ligne.groupe.cle,
                    sous_categorie=retenue.sous_categorie,
                    non_comptee=retenue.non_comptee,
                )
                try:
                    self.ledger.valider_proposition(regle)
                except RegleError as erreur:
                    ligne.refus = str(erreur)
                    continue
            else:
                self._valider_rg13(ligne, retenue)
            ligne.validee = True
        refusees = [(i, ligne) for i, ligne in enumerate(lignes, 1) if ligne.refus]
        for numero, ligne in refusees:
            self.interlocuteur.afficher(
                Text(f"  Ligne {numero} laissée en attente : {ligne.refus}")
            )
        restantes = sum(1 for ligne in lignes if not ligne.validee)
        self.interlocuteur.afficher(
            Text(
                f"  {sum(1 for ligne in lignes if ligne.validee)} ligne(s) validée(s), "
                f"{restantes} en attente."
            )
        )
        sans_proposition = sum(1 for ligne in lignes if ligne.retenue is None)
        if sans_proposition:
            self.interlocuteur.afficher(
                Text(
                    f"  {sans_proposition} ligne(s) sans proposition, donc non validée(s) par "
                    "Entrée : les compléter par leur numéro, ou relancer `budget classify` "
                    "quand le catégoriseur répond."
                )
            )

    def _valider_rg13(self, ligne: LigneLot, imputation: Imputation) -> None:
        """RG-13 : une décision, jamais une règle."""
        assert ligne.operation is not None
        question = Question(
            type=TypeQuestion.OPERATION,
            motif=MOTIF_RG13,
            transaction=ligne.operation,
            regle_proposable=False,
        )
        self.ledger.enregistrer_reponse(question, decision_pour(question, imputation), None)
