"""Contrôles de cohérence bloquants entre extractions (cadrage §11)."""

from budget.errors import ControleError
from budget.montants import formater_montant
from budget.parsers.credit_agricole import ReleveCartes, ReleveJoint


def verifier_debit_differe(cartes: ReleveCartes, joint: ReleveJoint) -> None:
    """RG-03 : le détail de chaque carte égale sa ligne de débit différé sur le joint.

    Vérifie, dans l'ordre : même mois, appariement complet carte par carte, montant exact au
    centime, et aucune opération carte postérieure à la date d'arrêté. Toutes les anomalies sont
    réunies dans une seule `ControleError`.
    """
    problemes: list[str] = []
    debut, fin = joint.periode_debut, joint.periode_fin

    # 1. Même mois
    for releve in cartes.cartes:
        if not debut <= releve.date_debit <= fin:
            problemes.append(
                f"carte {releve.carte} débitée le {releve.date_debit:%d/%m/%Y}, hors de la "
                f"période du joint ({debut:%d/%m/%Y} au {fin:%d/%m/%Y}) : les fichiers "
                f"{cartes.fichier} et {joint.fichier} ne portent pas sur le même mois"
            )
    if problemes:
        _lever(problemes)

    # 2. Appariement complet, dans les deux sens
    cles_cartes = [(r.carte, r.date_debit) for r in cartes.cartes]
    cles_joint = [(d.carte, d.date_debit) for d in joint.debits_differes]
    for carte, date_debit in cles_cartes:
        nombre = cles_joint.count((carte, date_debit))
        if nombre != 1:
            problemes.append(
                f"carte {carte}, débit du {date_debit:%d/%m/%Y} : 1 ligne de débit différé "
                f"attendue dans {joint.fichier}, {nombre} trouvée(s)"
            )
    for carte, date_debit in cles_joint:
        nombre = cles_cartes.count((carte, date_debit))
        if nombre != 1:
            problemes.append(
                f"carte {carte}, débit du {date_debit:%d/%m/%Y} : 1 section carte attendue "
                f"dans {cartes.fichier}, {nombre} trouvée(s)"
            )
    if problemes:
        _lever(problemes)

    differes = {(d.carte, d.date_debit): d for d in joint.debits_differes}
    for releve in cartes.cartes:
        differe = differes[(releve.carte, releve.date_debit)]

        # 3. Montant exact
        if differe.montant != releve.encours:
            problemes.append(
                f"carte {releve.carte}, débit du {releve.date_debit:%d/%m/%Y} : "
                f"joint = {formater_montant(differe.montant)}, "
                f"détail cartes = {formater_montant(releve.encours)} "
                f"(écart {formater_montant(abs(differe.montant - releve.encours))})"
            )

        # 4. Cycle cohérent
        for operation in releve.operations:
            if operation.date_operation > differe.date_arrete:
                problemes.append(
                    f"carte {releve.carte}, {cartes.fichier} ligne {operation.position} : "
                    f"opération du {operation.date_operation:%d/%m/%Y} "
                    f"({formater_montant(operation.montant)}) postérieure à la date d'arrêté "
                    f"du {differe.date_arrete:%d/%m/%Y}"
                )
    if problemes:
        _lever(problemes)


def _lever(problemes: list[str]) -> None:
    raise ControleError("\n".join(f"RG-03 : {probleme}." for probleme in problemes))
