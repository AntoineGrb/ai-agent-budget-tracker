"""Contrôles bloquants avant toute restitution d'un mois (cadrage §11, spec 08).

1. Le mois est importé : les contrôles de parsing (RG-02, RG-03, solde TR) sont donc passés.
2. Le mois est classé et aucune opération suivie n'est en attente.
3. Pour chaque vue, la somme des catégories est égale au total, et chaque catégorie à la somme de
   ses sous-catégories, au centime. Un échec signale une erreur du calcul, jamais une tolérance.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from budget.agregats.modeles import Agregats
from budget.classement.modeles import Classement
from budget.classement.moteur import est_suivie
from budget.errors import ControleError
from budget.models import Transaction
from budget.montants import formater_montant

NB_CITEES = 5


def controler_mois(
    mois: str,
    transactions_par_mois: Mapping[str, Sequence[Transaction]],
    classements_par_mois: Mapping[str, Sequence[Classement]],
    agregats: Agregats | None,
) -> None:
    """Lève `ControleError` en listant tous les manquements du mois."""
    if mois not in transactions_par_mois:
        raise _erreur(mois, [f"mois non importé : lancer `budget import --mois {mois}`"])
    manquements = []
    decidees = {c.transaction_id for c in classements_par_mois.get(mois, ()) if c.decidee}
    attente = [t for t in transactions_par_mois[mois] if est_suivie(t) and t.id not in decidees]
    if attente:
        citees = "\n".join(
            f"      {t.fichier}, position {t.position}, du {t.date_operation:%d/%m/%Y}, "
            f"{formater_montant(t.montant)}"
            for t in attente[:NB_CITEES]
        )
        suite = (
            f"\n      … et {len(attente) - NB_CITEES} autre(s)" if len(attente) > NB_CITEES else ""
        )
        manquements.append(
            f"{len(attente)} opération(s) en attente, ni comptées ni non comptées :\n"
            f"{citees}{suite}\n    les classer : `budget classify --mois {mois}`"
        )
    if agregats is not None:
        manquements.extend(_coherence(agregats))
    if manquements:
        raise _erreur(mois, manquements)


def _coherence(agregats: Agregats) -> list[str]:
    """Contrôle 3 : le calcul des agrégats est cohérent avec lui-même, au centime."""
    manquements = []
    for vue in (agregats.joint, agregats.perso):
        somme = sum((ligne.montant for ligne in vue.categories), start=Decimal(0))
        if somme != vue.total:
            manquements.append(
                f"vue {vue.compte.value} ({vue.nb_operations} opération(s)) : somme des "
                f"catégories {formater_montant(somme)}, total {formater_montant(vue.total)}"
            )
        for ligne in vue.categories:
            sous_total = sum((s.montant for s in ligne.sous_categories), start=Decimal(0))
            if sous_total != ligne.montant:
                manquements.append(
                    f"vue {vue.compte.value}, {ligne.categorie.libelle} ({ligne.nb_operations} "
                    f"opération(s)) : somme des sous-catégories {formater_montant(sous_total)}, "
                    f"catégorie {formater_montant(ligne.montant)}"
                )
    if manquements:
        manquements[-1] += "\n    erreur du calcul des agrégats : corriger le code, sans tolérance"
    return manquements


def _erreur(mois: str, manquements: Sequence[str]) -> ControleError:
    lignes = "\n".join(f"  - {m}" for m in manquements)
    return ControleError(f"Contrôles bloquants du mois {mois} en échec :\n{lignes}")
