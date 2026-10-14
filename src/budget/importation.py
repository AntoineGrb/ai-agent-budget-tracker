"""Import d'un mois complet dans le ledger (spec 04) : RG-02, parsers, contrôles, RG-01, RG-16."""

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from budget.controles import verifier_debit_differe
from budget.dates import dernier_jour_du_mois, mois_budgetaire
from budget.errors import ControleError
from budget.ledger import FichierImporte, Ledger, RapportImport
from budget.libelles import normaliser_libelle
from budget.models import MOTIF_MOIS, OperationBrute, Source, Transaction
from budget.parsers.credit_agricole import parser_ca_cartes, parser_ca_joint
from budget.parsers.trade_republic import parser_trade_republic

journal = logging.getLogger(__name__)

NOMS_FICHIERS: dict[Source, str] = {
    Source.CA_CB: "CA_CB_{}.csv",
    Source.CA_JOINT: "CA_CPTE_JOINT_{}.csv",
    Source.TR: "TR_{}.pdf",
}


def premier_jour_mois_suivant(mois: str) -> date:
    annee, numero = (int(partie) for partie in mois.split("-"))
    return date(annee + 1, 1, 1) if numero == 12 else date(annee, numero + 1, 1)


def chemins_du_mois(mois: str, dossier: Path) -> dict[Source, Path]:
    """Chemins attendus des trois extractions : `dossier/AAAA-MM/<nom>AAAAMM.<ext>`."""
    compact = mois.replace("-", "")
    return {source: dossier / mois / nom.format(compact) for source, nom in NOMS_FICHIERS.items()}


def mois_budgetaire_de(operation: OperationBrute) -> str:
    """RG-01 : mois de débit pour les cartes, mois de l'opération pour le joint et TR."""
    if operation.source is Source.CA_CB:
        if operation.date_debit is None:
            raise ControleError(
                f"RG-01 : {operation.fichier}, position {operation.position} : "
                "opération carte sans date de débit"
            )
        return mois_budgetaire(operation.date_debit)
    return mois_budgetaire(operation.date_operation)


def identifiant(
    source: Source, date_operation: date, montant: object, libelle_normalise: str, rang: int
) -> str:
    """RG-16 : 16 premiers caractères hexadécimaux du sha256 de la clé et du rang."""
    cle = f"{source.value}|{date_operation:%Y-%m-%d}|{montant}|{libelle_normalise}|{rang}"
    return hashlib.sha256(cle.encode("utf-8")).hexdigest()[:16]


def construire_transactions(operations: Sequence[OperationBrute]) -> list[Transaction]:
    """`Transaction` d'un même fichier, avec rang d'occurrence et identifiant (RG-01, RG-16)."""
    occurrences: Counter[tuple[object, ...]] = Counter()
    transactions = []
    for operation in sorted(operations, key=lambda op: op.position):
        libelle_normalise = normaliser_libelle(operation.libelle)
        montant = f"{operation.montant:.2f}"  # deux décimales et signe : "-6.80"
        cle = (operation.source, operation.date_operation, montant, libelle_normalise)
        occurrences[cle] += 1
        rang = occurrences[cle]
        transactions.append(
            Transaction(
                id=identifiant(
                    operation.source, operation.date_operation, montant, libelle_normalise, rang
                ),
                source=operation.source,
                compte=operation.compte,
                fichier=operation.fichier,
                position=operation.position,
                rang=rang,
                date_operation=operation.date_operation,
                date_debit=operation.date_debit,
                mois_budgetaire=mois_budgetaire_de(operation),
                type_operation=operation.type_operation,
                libelle=operation.libelle,
                libelle_brut=operation.libelle_brut,
                libelle_normalise=libelle_normalise,
                ics=operation.ics,
                carte=operation.carte,
                montant=operation.montant,
                solde_apres=operation.solde_apres,
            )
        )
    return transactions


def _empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def importer_mois(mois: str, dossier: Path, ledger: Ledger, aujourd_hui: date) -> RapportImport:
    """Importe les trois extractions d'un mois. Toute erreur laisse la base inchangée."""
    if not re.fullmatch(MOTIF_MOIS, mois):
        raise ControleError(f"mois {mois!r} invalide : format attendu AAAA-MM")
    debut = date(int(mois[:4]), int(mois[5:]), 1)
    fin = dernier_jour_du_mois(debut.year, debut.month)

    # 1. Complétude (cadrage §3)
    disponible = premier_jour_mois_suivant(mois)
    if aujourd_hui < disponible:
        raise ControleError(
            f"le mois {mois} ne pourra être importé qu'à partir du {disponible:%d/%m/%Y} : "
            "ses extractions ne sont pas encore complètes"
        )

    # 2. Présence des trois extractions (RG-02)
    chemins = chemins_du_mois(mois, dossier)
    manquants = [chemin for chemin in chemins.values() if not chemin.is_file()]
    if manquants:
        raise ControleError(
            f"RG-02 : mois {mois} incomplet, fichier(s) manquant(s) dans {dossier / mois} : "
            + ", ".join(chemin.name for chemin in manquants)
        )

    # 3. Parsing, contrôles internes compris (specs 02 et 03)
    cartes = parser_ca_cartes(chemins[Source.CA_CB])
    joint = parser_ca_joint(chemins[Source.CA_JOINT])
    releve_tr = parser_trade_republic(chemins[Source.TR])

    # 4. Cohérence de période avec le mois demandé
    for nom, periode_debut, periode_fin in (
        (joint.fichier, joint.periode_debut, joint.periode_fin),
        (releve_tr.fichier, releve_tr.periode_debut, releve_tr.periode_fin),
    ):
        if (periode_debut, periode_fin) != (debut, fin):
            raise ControleError(
                f"{nom} couvre la période du {periode_debut:%d/%m/%Y} au {periode_fin:%d/%m/%Y}, "
                f"attendu le mois {mois} ({debut:%d/%m/%Y} au {fin:%d/%m/%Y}) : fichier "
                "déposé dans le mauvais dossier ?"
            )
    for releve in cartes.cartes:
        if not debut <= releve.date_debit <= fin:
            raise ControleError(
                f"{cartes.fichier} : carte {releve.carte} débitée le "
                f"{releve.date_debit:%d/%m/%Y}, hors du mois {mois} (RG-01) : fichier déposé "
                "dans le mauvais dossier ?"
            )

    # 5. Débit différé (RG-03)
    verifier_debit_differe(cartes, joint)

    # 6. Construction des transactions, fichier par fichier (RG-01, RG-16)
    operations_cartes = [op for releve in cartes.cartes for op in releve.operations]
    fichiers = [
        FichierImporte(
            source=source,
            fichier=chemins[source].name,
            empreinte=_empreinte(chemins[source]),
            transactions=tuple(construire_transactions(operations)),
        )
        for source, operations in (
            (Source.CA_CB, operations_cartes),
            (Source.CA_JOINT, list(joint.operations)),
            (Source.TR, list(releve_tr.operations)),
        )
    ]

    # 7. Écriture atomique et idempotente (RG-16)
    rapport = ledger.enregistrer_import(mois, fichiers)
    journal.info(
        "import %s : %d opérations lues, %d ajoutées, %d déjà présentes",
        mois,
        rapport.lues,
        rapport.ajoutees,
        rapport.deja_presentes,
    )
    return rapport
