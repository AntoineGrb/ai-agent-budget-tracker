"""Interface en ligne de commande `budget`."""

import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from budget import __version__
from budget.classement import ResultatClassement
from budget.classement.moteur import est_paiement_carte
from budget.config import charger_contexte
from budget.errors import BudgetError, ConfigError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.models import MOTIF_MOIS, Nature, Source, Transaction
from budget.montants import formater_montant, formater_pourcentage
from budget.questions.dialogue import InterlocuteurTerminal
from budget.questions.execution import RapportClassement, classer_et_questionner

app = typer.Typer(help="Budget Tracker agentique : suivi mensuel des dépenses du foyer.")
config_app = typer.Typer(help="Fichier de contexte du foyer.")
app.add_typer(config_app, name="config")

console = Console()
console_erreur = Console(stderr=True)


def _afficher_version(valeur: bool) -> None:
    if valeur:
        console.print(f"budget {__version__}")
        raise typer.Exit()


@app.callback()
def principal(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_afficher_version,
            is_eager=True,
            help="Affiche la version et quitte.",
        ),
    ] = False,
) -> None:
    """Budget Tracker agentique."""


@config_app.command("verifier")
def verifier(
    chemin: Annotated[Path, typer.Option("--chemin", help="Fichier de contexte à valider.")] = Path(
        "data/contexte.yaml"
    ),
) -> None:
    """Valide le fichier de contexte et affiche un résumé."""
    try:
        contexte = charger_contexte(chemin)
    except ConfigError as erreur:
        console_erreur.print(f"[bold red]{erreur.message}[/]", highlight=False)
        for probleme in erreur.erreurs:
            console_erreur.print(f"  - {probleme}", highlight=False, markup=False)
        raise typer.Exit(code=1) from None

    total_charges = sum((charge.montant for charge in contexte.charges_fixes), start=Decimal(0))
    tableau = Table(title=f"Contexte valide : {chemin}", show_header=False)
    tableau.add_row("Salaires", str(len(contexte.salaires)))
    tableau.add_row("Dotations", str(len(contexte.dotations)))
    tableau.add_row("Charges fixes", str(len(contexte.charges_fixes)))
    tableau.add_row("Charges fixes attendues / mois", formater_montant(total_charges))
    console.print(tableau)


def _valider_mois(valeur: str) -> str:
    if not re.fullmatch(MOTIF_MOIS, valeur):
        raise typer.BadParameter(f"{valeur!r} invalide, format attendu AAAA-MM (ex. 2026-06)")
    return valeur


@app.command("import")
def importer(
    mois: Annotated[
        str, typer.Option("--mois", callback=_valider_mois, help="Mois à importer (AAAA-MM).")
    ],
    dossier: Annotated[
        Path, typer.Option("--dossier", help="Dossier contenant les sous-dossiers AAAA-MM.")
    ] = Path("data/raw"),
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
) -> None:
    """Importe les trois extractions d'un mois dans le ledger (sans effet si déjà importé)."""
    try:
        with Ledger(base) as ledger:
            rapport = importer_mois(mois, dossier, ledger, date.today())
    except BudgetError as erreur:
        console_erreur.print(f"[bold red]{erreur}[/]", highlight=False)
        raise typer.Exit(code=1) from None

    tableau = Table(title=f"Import du mois {mois}")
    for colonne in ("Source", "Fichier", "Lues", "Ajoutées", "Déjà présentes"):
        tableau.add_column(colonne, justify="right" if colonne[0] in "LAD" else "left")
    for ligne in rapport.lignes:
        tableau.add_row(
            ligne.source.value,
            ligne.fichier,
            str(ligne.lues),
            str(ligne.ajoutees),
            str(ligne.deja_presentes),
        )
    tableau.add_section()
    tableau.add_row(
        "Total", "", str(rapport.lues), str(rapport.ajoutees), str(rapport.deja_presentes)
    )
    console.print(tableau)


_SOURCES = {
    Source.CA_JOINT: "Compte joint",
    Source.CA_CB: "Cartes (compte joint)",
    Source.TR: "Trade Republic (compte perso)",
}


@app.command("classify")
def classifier(
    mois: Annotated[
        str, typer.Option("--mois", callback=_valider_mois, help="Mois à classer (AAAA-MM).")
    ],
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
    contexte: Annotated[
        Path, typer.Option("--contexte", help="Fichier de contexte du foyer.")
    ] = Path("data/contexte.yaml"),
    sans_questions: Annotated[
        bool, typer.Option("--sans-questions", help="Classe sans poser de question.")
    ] = False,
) -> None:
    """Classe les opérations du mois : contexte, décisions, règles, puis questions (étapes 5-6)."""
    interactif = not sans_questions and sys.stdin.isatty()
    interlocuteur = InterlocuteurTerminal(console) if interactif else None
    try:
        reference = charger_contexte(contexte)
        with Ledger(base) as ledger:
            rapport = classer_et_questionner(mois, ledger, reference, interlocuteur)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    _afficher_classement(rapport.transactions, rapport.resultat)
    _afficher_bilan(rapport, interactif)


def _afficher_bilan(rapport: RapportClassement, interactif: bool) -> None:
    bilan = rapport.bilan
    if bilan is not None:
        etat = " (dialogue interrompu)" if bilan.interrompu else ""
        console.print(
            f"Questions : {bilan.a_poser} à poser, {bilan.posees} posées, {bilan.repondues} "
            f"répondues, {bilan.passees} passées{etat} ; {bilan.regles_creees} règle(s) créée(s)"
        )
    if rapport.questions:
        motif = "" if interactif else " (--sans-questions ou entrée non interactive)"
        console.print(f"Questions en attente : {len(rapport.questions)}{motif}")
        console.print(
            f"Les poser : `budget classify --mois {rapport.resultat.mois}` dans un terminal"
        )
    else:
        console.print("[green]Aucune question en attente[/]")
    par_id = {t.id: t for t in rapport.transactions}
    non_classees = sum(1 for c in rapport.resultat.classements if c.nature is None)
    cartes = sum(
        1
        for c in rapport.resultat.classements
        if c.sous_categorie is None
        and c.nature in (Nature.DEPENSE, Nature.REMBOURSEMENT)
        and est_paiement_carte(par_id[c.transaction_id])
    )
    console.print(f"Non classées : {non_classees}")
    console.print(f"Paiements carte sans sous-catégorie (étape 7) : {cartes}")


def _afficher_classement(transactions: list[Transaction], resultat: ResultatClassement) -> None:
    par_id = {t.id: t for t in transactions}

    repartition = Table(title=f"Natures de flux : {resultat.mois}")
    for colonne in ("Source", "Nature", "Opérations", "Total"):
        repartition.add_column(
            colonne, justify="right" if colonne in ("Opérations", "Total") else "left"
        )
    non_classees = []
    for source, titre in _SOURCES.items():
        groupes: dict[Nature | None, list[Transaction]] = {}
        for classement in resultat.classements:
            transaction = par_id[classement.transaction_id]
            if transaction.source is source:
                groupes.setdefault(classement.nature, []).append(transaction)
                if classement.nature is None:
                    non_classees.append((transaction, classement.contrepartie_id is not None))
        for nature in [*Nature, None]:
            if nature not in groupes:
                continue
            liste = groupes[nature]
            total = sum((t.montant for t in liste), start=Decimal(0))
            libelle = nature.value if nature is not None else "[yellow]non classées[/]"
            repartition.add_row(titre, libelle, str(len(liste)), formater_montant(total))
            titre = ""
        repartition.add_section()
    console.print(repartition)

    tableau = Table(title=f"Opérations non classées ({len(non_classees)})")
    tableau.add_column("Source", no_wrap=True)
    tableau.add_column("Date", no_wrap=True)
    tableau.add_column("Type", no_wrap=True, overflow="ellipsis", max_width=24)
    tableau.add_column("Libellé", no_wrap=True, overflow="ellipsis", max_width=50)
    tableau.add_column("Montant", justify="right", no_wrap=True)
    tableau.add_column("Apparié", no_wrap=True)
    for transaction, apparie in non_classees:
        tableau.add_row(
            transaction.source.value,
            f"{transaction.date_operation:%d/%m/%Y}",
            transaction.type_operation or "",
            transaction.libelle,
            formater_montant(transaction.montant),
            "oui" if apparie else "",
        )
    console.print(tableau)

    charges = Table(title="Charges fixes (RG-18)")
    for colonne in ("Charge", "Attendu", "Réel", "Écart", "Écart %", "Statut"):
        charges.add_column(colonne, justify="left" if colonne in ("Charge", "Statut") else "right")
    for r in resultat.rapprochements:
        if r.reel is None:
            statut = "[red]absente[/]"
        else:
            statut = (
                f"[red]hors seuil ({formater_pourcentage(r.seuil_pct)})[/]"
                if r.hors_seuil
                else "[green]OK[/]"
            )
        charges.add_row(
            r.nom,
            formater_montant(r.attendu),
            formater_montant(r.reel) if r.reel is not None else "",
            formater_montant(r.ecart) if r.ecart is not None else "",
            formater_pourcentage(r.ecart_pct, signe=True) if r.ecart_pct is not None else "",
            statut,
        )
    console.print(charges)

    if resultat.alertes:
        console.print(f"[bold]Alertes ({len(resultat.alertes)})[/]")
        for code in sorted(resultat.alertes_par_code()):
            console.print(f"[bold yellow]{code}[/]")
            for alerte in resultat.alertes:
                if alerte.code == code:
                    console.print(f"  - {alerte.message}", markup=False, highlight=False)
    else:
        console.print("[green]Aucune alerte[/]")
    console.print(f"Virements internes appariés : {resultat.virements_internes_apparies}")
