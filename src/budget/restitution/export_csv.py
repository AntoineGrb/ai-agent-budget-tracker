"""`budget export` : CSV du mois classé (jalon 1, spec 07).

UTF-8 avec BOM, séparateur `;`, montants `-3116,50` sans séparateur de milliers ni symbole,
dates `JJ/MM/AAAA`.
"""

import csv
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from budget.classement.modeles import Classement
from budget.errors import ControleError
from budget.ledger import Ledger
from budget.models import Transaction
from budget.restitution.operations import LigneOperation, lignes_operations

COLONNES = (
    "Date d'opération",
    "Date de débit",
    "Compte",
    "Libellé",
    "Montant",
    "Nature",
    "Catégorie",
    "Sous-catégorie",
    "Exceptionnel",
    "Lien",
    "Règle appliquée",
)


def montant_csv(montant: Decimal) -> str:
    return f"{montant:.2f}".replace(".", ",")


def ecrire_csv(lignes: Sequence[LigneOperation], chemin: Path) -> Path:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8-sig", newline="") as flux:
        ecrivain = csv.writer(flux, delimiter=";", lineterminator="\r\n")
        ecrivain.writerow(COLONNES)
        for ligne in lignes:
            ecrivain.writerow(
                (
                    f"{ligne.date_operation:%d/%m/%Y}",
                    f"{ligne.date_debit:%d/%m/%Y}" if ligne.date_debit else "",
                    ligne.compte,
                    ligne.libelle,
                    montant_csv(ligne.montant),
                    ligne.nature,
                    ligne.categorie,
                    ligne.sous_categorie,
                    "oui" if ligne.exceptionnel else "",
                    ligne.lien,
                    ligne.regle,
                )
            )
    return chemin


def references_liens(ledger: Ledger, mois: str) -> dict[str, tuple[Transaction, Classement | None]]:
    """Transactions des autres mois importés, pour retrouver une dépense liée (RG-07)."""
    references: dict[str, tuple[Transaction, Classement | None]] = {}
    for autre in ledger.mois_importes():
        if autre == mois:
            continue
        classements = {c.transaction_id: c for c in ledger.classements(autre)}
        references.update((t.id, (t, classements.get(t.id))) for t in ledger.transactions(autre))
    return references


def exporter_mois(ledger: Ledger, mois: str, dossier: Path) -> tuple[Path, int]:
    """Écrit `budget_AAAA-MM.csv` à partir du classement enregistré ; renvoie chemin et lignes."""
    if mois not in ledger.mois_importes():
        raise ControleError(
            f"mois {mois} non importé : lancer d'abord `budget import --mois {mois}`"
        )
    classements = {c.transaction_id: c for c in ledger.classements(mois)}
    if not classements:
        raise ControleError(
            f"mois {mois} non classé : lancer d'abord `budget classify --mois {mois}`"
        )
    lignes = lignes_operations(
        ledger.transactions(mois), classements, references_liens(ledger, mois)
    )
    return ecrire_csv(lignes, dossier / f"budget_{mois}.csv"), len(lignes)
