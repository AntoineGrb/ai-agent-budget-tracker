"""RG-16 : identifiant, rang d'occurrence et idempotence de l'import (spec 04)."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from budget.errors import ControleError
from budget.importation import construire_transactions, importer_mois
from budget.ledger import FichierImporte, Ledger
from budget.models import Source, Transaction
from budget.parsers.credit_agricole import parser_ca_joint
from tests.conftest import DOSSIER_FIXTURES, copier_mois, ecrire_variante

AUJOURD_HUI = date(2026, 10, 8)


def _contenu(chemin: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with sqlite3.connect(chemin) as connexion:
        imports = connexion.execute("SELECT * FROM imports ORDER BY id").fetchall()
        transactions = connexion.execute("SELECT * FROM transactions ORDER BY id").fetchall()
    return imports, transactions


def test_rg16_rangs_d_occurrence(ledger_vide: Ledger) -> None:
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    transactions = ledger_vide.transactions("2026-06")

    varenne = [
        (t.date_operation, t.rang)
        for t in transactions
        if t.source is Source.TR and t.libelle_normalise == "VARENNE CAFE"
    ]
    assert varenne == [(date(2026, 6, 24), 1), (date(2026, 6, 24), 2), (date(2026, 6, 25), 1)]

    rbcpj = [
        (t.rang, t.carte)
        for t in transactions
        if t.libelle_normalise == "RBCPJ PARIS" and t.montant == Decimal("-24.00")
    ]
    assert rbcpj == [(1, "X1091"), (2, "X1091")]

    assert sum(1 for t in transactions if t.rang == 2) == 2
    assert len({t.id for t in transactions}) == len(transactions) == 159


def test_rg16_identifiant_documente() -> None:
    """id = sha256("source|date|montant à 2 décimales signé|libellé normalisé|rang")[:16]."""
    import hashlib

    joint = parser_ca_joint(DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv")
    transaction = construire_transactions(joint.operations)[0]
    cle = "ca_joint|2026-06-30|-3116.50|DEPENSES CARTE X1091 AU 18/06/26|1"
    assert transaction.id == hashlib.sha256(cle.encode()).hexdigest()[:16]


def test_rg16_identifiants_stables_entre_deux_bases(tmp_path: Path) -> None:
    identifiants = []
    for nom in ("a.sqlite", "b.sqlite"):
        with Ledger(tmp_path / nom) as ledger:
            importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
            identifiants.append([t.id for t in ledger.transactions()])
    assert identifiants[0] == identifiants[1]


def test_rg16_reimport_identique_sans_effet(tmp_path: Path) -> None:
    """Un export retéléchargé change son en-tête et son empreinte, pas ses opérations."""
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    avant = _contenu(base)

    dossier = copier_mois("2026-06", tmp_path / "raw")
    ecrire_variante(
        DOSSIER_FIXTURES / "2026-06" / "CA_CB_202606.csv",
        dossier,
        ("Téléchargement du 23/09/2026", "Téléchargement du 01/10/2026"),
    )
    with Ledger(base) as ledger:
        rapport = importer_mois("2026-06", tmp_path / "raw", ledger, AUJOURD_HUI)
    assert (rapport.lues, rapport.ajoutees, rapport.deja_presentes) == (159, 0, 159)
    assert _contenu(base) == avant


def test_rg16_reimport_modifie_refuse(tmp_path: Path) -> None:
    base = tmp_path / "budget.sqlite"
    with Ledger(base) as ledger:
        importer_mois("2026-06", DOSSIER_FIXTURES, ledger, AUJOURD_HUI)
    avant = _contenu(base)

    dossier = copier_mois("2026-06", tmp_path / "raw")
    ecrire_variante(
        DOSSIER_FIXTURES / "2026-06" / "CA_CPTE_JOINT_202606.csv",
        dossier,
        ('12/06/2026;"Cotisation\nOffre Premium\n\n\n\n";15,5;\r\n', ""),
    )
    with Ledger(base) as ledger, pytest.raises(ControleError) as erreur:
        importer_mois("2026-06", tmp_path / "raw", ledger, AUJOURD_HUI)
    message = str(erreur.value)
    assert message.startswith("RG-16 : ca_joint 2026-06 déjà importé le ")
    assert "0 opération(s) en plus et 1 en moins" in message
    assert "Offre Premium" not in message
    assert _contenu(base) == avant


def test_rg16_identifiant_deja_present_sous_un_autre_mois(ledger_vide: Ledger) -> None:
    importer_mois("2026-06", DOSSIER_FIXTURES, ledger_vide, AUJOURD_HUI)
    juin = ledger_vide.transactions("2026-06", Source.TR)
    deplacees: tuple[Transaction, ...] = tuple(
        t.model_copy(update={"mois_budgetaire": "2026-07"}) for t in juin
    )
    fichier = FichierImporte(
        source=Source.TR, fichier="TR_202607.pdf", empreinte="x", transactions=deplacees
    )
    with pytest.raises(ControleError, match="déjà importées pour le mois 2026-06 .* mauvais"):
        ledger_vide.enregistrer_import("2026-07", [fichier])
    assert ledger_vide.mois_importes() == ["2026-06"]
