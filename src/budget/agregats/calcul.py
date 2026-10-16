"""Calcul des chiffres d'un mois classé : fonction pure, ni base, ni fichier (spec 08)."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from budget.agents.confidentialite import masquer
from budget.agregats.modeles import (
    Agregats,
    LigneCategorie,
    MontantSousCategorie,
    NonComptees,
    PlusGrosseDepense,
    PointMensuel,
    Vue,
)
from budget.categories import Categorie, SousCategorie, categorie_de
from budget.classement.cartes import est_paiement_carte
from budget.classement.modeles import Classement
from budget.classement.moteur import est_suivie
from budget.config import Contexte
from budget.dates import decaler_mois
from budget.libelles import cle_marchand
from budget.models import Compte, Transaction
from budget.montants import CENTIME
from budget.restitution.operations import depenses_par_categorie

ZERO = Decimal("0.00")
NB_PLUS_GROSSES = 5
MOIS_MOYENNE = 3
MOIS_HISTORIQUE = 12


@dataclass(frozen=True)
class MoisClasse:
    """Transactions d'un mois importé et leur classement enregistré."""

    transactions: Sequence[Transaction]
    classements: Mapping[str, Classement]

    @property
    def complet(self) -> bool:
        """Chaque opération suivie (RG-19) est comptée ou non comptée : rien en attente."""
        return all(
            (c := self.classements.get(t.id)) is not None and c.decidee
            for t in self.transactions
            if est_suivie(t)
        )

    def par_categorie(self, compte: Compte) -> dict[Categorie, Decimal]:
        """Dépenses comptées de la vue, avoirs carte déduits (RG-09)."""
        totaux = depenses_par_categorie(self.transactions, self.classements)
        return {categorie: m for (categorie, c), m in totaux.items() if c is compte}

    def comptees(self, compte: Compte) -> list[tuple[Transaction, SousCategorie]]:
        """Opérations comptées de la vue ; les non comptées (RG-10) et les crédits ignorés
        (RG-19), sans sous-catégorie, n'y figurent pas."""
        return [
            (t, c.sous_categorie)
            for t in self.transactions
            if t.compte is compte
            and (c := self.classements.get(t.id)) is not None
            and c.sous_categorie is not None
        ]


def calculer_agregats(
    mois: str,
    transactions_par_mois: Mapping[str, Sequence[Transaction]],
    classements_par_mois: Mapping[str, Sequence[Classement]],
    contexte: Contexte,
) -> Agregats:
    """Chiffres du mois `mois`, à partir des mois importés de M−11 à M."""
    donnees = {
        m: MoisClasse(transactions, {c.transaction_id: c for c in classements_par_mois.get(m, ())})
        for m, transactions in transactions_par_mois.items()
    }
    courant = donnees[mois]
    precedents = [
        donnees[m]
        for decalage in range(1, MOIS_MOYENNE + 1)
        if (m := decaler_mois(mois, -decalage)) in donnees and donnees[m].complet
    ]
    vues = {
        compte: _vue(compte, courant, precedents, contexte.budgets.de(compte)) for compte in Compte
    }
    fenetre = [decaler_mois(mois, -decalage) for decalage in range(MOIS_HISTORIQUE - 1, -1, -1)]
    historique = tuple(
        PointMensuel(
            mois=m,
            joint=_somme(donnees[m].par_categorie(Compte.JOINT).values()),
            perso=_somme(donnees[m].par_categorie(Compte.PERSO).values()),
            complet=donnees[m].complet,
        )
        for m in fenetre
        if m in donnees
    )
    return Agregats(
        mois=mois, joint=vues[Compte.JOINT], perso=vues[Compte.PERSO], historique=historique
    )


def _somme(montants: Iterable[Decimal]) -> Decimal:
    return sum(montants, start=ZERO)


def _vue(
    compte: Compte,
    courant: MoisClasse,
    precedents: Sequence[MoisClasse],
    budgets: Mapping[Categorie, Decimal],
) -> Vue:
    comptees = courant.comptees(compte)
    par_categorie = courant.par_categorie(compte)
    historiques = [p.par_categorie(compte) for p in precedents]
    lignes = []
    for categorie in Categorie:
        operations = [(t, sc) for t, sc in comptees if categorie_de(sc) is categorie]
        budget = budgets.get(categorie)
        if not operations and budget is None:
            continue
        montant = par_categorie.get(categorie, ZERO)
        moyenne = None
        if historiques:
            cumul = _somme([h.get(categorie, ZERO) for h in historiques])
            moyenne = (cumul / len(historiques)).quantize(CENTIME, rounding=ROUND_HALF_UP)
        lignes.append(
            LigneCategorie(
                categorie=categorie,
                montant=montant,
                nb_operations=len(operations),
                sous_categories=_sous_categories(operations),
                budget=budget,
                ecart_budget=montant - budget if budget is not None else None,
                moyenne_3_mois=moyenne,
                mois_moyenne=len(historiques),
                ecart_moyenne=montant - moyenne if moyenne is not None else None,
            )
        )
    non_comptees = [
        t
        for t in courant.transactions
        if t.compte is compte and (c := courant.classements.get(t.id)) is not None and c.non_comptee
    ]
    return Vue(
        compte=compte,
        total=_somme([ligne.montant for ligne in lignes]),
        categories=tuple(lignes),
        non_comptees=NonComptees(
            nombre=len(non_comptees), montant=-_somme([t.montant for t in non_comptees])
        ),
        plus_grosses=_plus_grosses(comptees),
    )


def _sous_categories(
    operations: Sequence[tuple[Transaction, SousCategorie]],
) -> tuple[MontantSousCategorie, ...]:
    montants: dict[SousCategorie, Decimal] = {}
    for t, sc in operations:
        montants[sc] = montants.get(sc, ZERO) - t.montant  # RG-09 : un avoir réduit le total
    ordre = list(SousCategorie)
    tries = sorted(
        ((sc, m) for sc, m in montants.items() if m), key=lambda e: (-e[1], ordre.index(e[0]))
    )
    return tuple(MontantSousCategorie(sous_categorie=sc, montant=m) for sc, m in tries)


def _plus_grosses(
    comptees: Sequence[tuple[Transaction, SousCategorie]],
) -> tuple[PlusGrosseDepense, ...]:
    """Les plus gros débits comptés ; un avoir n'est pas une dépense."""
    debits = sorted(
        ((t, sc) for t, sc in comptees if t.montant < 0),
        key=lambda e: (e[0].montant, e[0].date_operation, e[0].source.value, e[0].position),
    )
    return tuple(
        PlusGrosseDepense(
            date_operation=t.date_operation,
            montant=-t.montant,
            sous_categorie=sc,
            # Cadrage §11 : jamais de libellé de virement ou de prélèvement
            marchand=masquer(cle_marchand(t.libelle_normalise)) if est_paiement_carte(t) else None,
        )
        for t, sc in debits[:NB_PLUS_GROSSES]
    )
