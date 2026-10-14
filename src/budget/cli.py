"""Interface en ligne de commande `budget`."""

import os
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
from budget.agents.categoriseur import Categoriseur, CategoriseurIndisponible, CategoriseurLLM
from budget.classement import ResultatClassement
from budget.classement.cartes import est_paiement_carte
from budget.config import Contexte, charger_contexte
from budget.environnement import charger_env
from budget.errors import BudgetError, ConfigError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.models import MOTIF_MOIS, Nature, Source, Transaction
from budget.montants import formater_montant, formater_pourcentage
from budget.questions.dialogue import InterlocuteurTerminal
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.regles import TypeRegle
from budget.restitution.export_csv import exporter_mois
from budget.restitution.journal import condition_regle, resultat_regle

app = typer.Typer(help="Budget Tracker agentique : suivi mensuel des dépenses du foyer.")
config_app = typer.Typer(help="Fichier de contexte du foyer.")
app.add_typer(config_app, name="config")
rules_app = typer.Typer(help="Audit des règles apprises.")
app.add_typer(rules_app, name="rules")

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
    charger_env()  # secrets de .env, sans écraser l'environnement


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
    sans_llm: Annotated[
        bool, typer.Option("--sans-llm", help="N'appelle pas le catégoriseur (saisie manuelle).")
    ] = False,
    journal: Annotated[
        Path | None,
        typer.Option(
            "--journal", help="Journal des décisions (défaut : decisions.md à côté de la base)."
        ),
    ] = None,
) -> None:
    """Classe le mois : contexte, décisions, règles, puis questions, lot carte, exceptionnels."""
    interactif = not sans_questions and sys.stdin.isatty()
    interlocuteur = InterlocuteurTerminal(console) if interactif else None
    try:
        reference = charger_contexte(contexte)
        categoriseur = _categoriseur(reference, sans_llm)
        with Ledger(base) as ledger:
            rapport = classer_et_questionner(
                mois,
                ledger,
                reference,
                interlocuteur,
                categoriseur,
                journal or base.with_name("decisions.md"),
            )
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    _afficher_classement(rapport.transactions, rapport.resultat)
    _afficher_bilan(rapport, interactif)


def _categoriseur(contexte: Contexte, sans_llm: bool) -> Categoriseur:
    """Mode dégradé sans erreur : `--sans-llm` ou clé API absente (spec 07)."""
    if sans_llm:
        return CategoriseurIndisponible("catégoriseur désactivé par --sans-llm")
    modele = contexte.llm.modele_categorisation
    if modele.startswith("anthropic:") and not os.environ.get("ANTHROPIC_API_KEY"):
        return CategoriseurIndisponible("ANTHROPIC_API_KEY absente de l'environnement et de .env")
    return CategoriseurLLM(modele)


def _afficher_bilan(rapport: RapportClassement, interactif: bool) -> None:
    bilan = rapport.bilan
    if bilan is not None:
        etat = " (dialogue interrompu)" if bilan.interrompu else ""
        console.print(
            f"Questions : {bilan.a_poser} à poser, {bilan.posees} posées, {bilan.repondues} "
            f"répondues, {bilan.passees} passées{etat} ; {bilan.regles_creees} règle(s) créée(s)"
        )
    lot = rapport.bilan_lot
    if lot is not None:
        if lot.avertissement:
            console.print(f"[yellow]Catégoriseur : {lot.avertissement}[/]", highlight=False)
        console.print(
            f"Paiements carte : {lot.lignes} lignes, {lot.proposes} proposées, {lot.valides} "
            f"validées, {lot.corriges} corrigées, {lot.en_attente} en attente ; "
            f"{lot.regles_creees} règle(s) marchand créée(s)"
        )
    exceptionnels = rapport.bilan_exceptionnels
    if exceptionnels is not None:
        console.print(
            f"Tags exceptionnels : {exceptionnels.oui} oui, {exceptionnels.non} non, "
            f"{exceptionnels.passees} passé(s)"
        )
    attente = {
        "questions": len(rapport.questions),
        "marchands à valider": len(rapport.groupes),
        "paiements Amazon à compléter": len(rapport.amazon),
        "tags exceptionnels": len(rapport.exceptionnelles),
    }
    if any(attente.values()):
        motif = "" if interactif else " (--sans-questions ou entrée non interactive)"
        console.print(f"En attente{motif} :")
        for libelle, nombre in attente.items():
            if nombre:
                console.print(f"  {libelle} : {nombre}")
        console.print(
            f"Les traiter : `budget classify --mois {rapport.resultat.mois}` dans un terminal"
        )
    else:
        console.print("[green]Rien en attente[/]")
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
    console.print(f"Paiements carte sans sous-catégorie : {cartes}")
    if rapport.journal is not None:
        console.print(f"Journal : {rapport.journal}", highlight=False)


@rules_app.command("list")
def regles_lister(
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
    toutes: Annotated[
        bool, typer.Option("--toutes", help="Inclut les règles désactivées.")
    ] = False,
    type_regle: Annotated[
        TypeRegle | None, typer.Option("--type", help="Filtre sur le type de règle.")
    ] = None,
) -> None:
    """Liste les règles apprises, avec le nombre d'opérations qu'elles classent."""
    with Ledger(base) as ledger:
        regles = [
            r
            for r in ledger.regles()
            if (toutes or r.active) and (type_regle is None or r.type is type_regle)
        ]
        couvertes = ledger.operations_par_regle()
    tableau = Table(title=f"Règles apprises ({len(regles)})")
    for colonne in (
        "N°",
        "Type",
        "Source",
        "Critère",
        "Valeur",
        "Condition",
        "Résultat",
        "Opérations",
        "Créée le",
        "Désactivée le",
    ):
        tableau.add_column(colonne, justify="right" if colonne in ("N°", "Opérations") else "left")
    for r in regles:
        tableau.add_row(
            str(r.id),
            r.type.value,
            r.source.value,
            r.critere.value,
            r.valeur,
            condition_regle(r),
            resultat_regle(r),
            str(couvertes.get(r.id or 0, 0)),
            f"{r.creee_le:%d/%m/%Y}" if r.creee_le else "",
            f"{r.desactivee_le:%d/%m/%Y}" if r.desactivee_le else "",
        )
    console.print(tableau)


@rules_app.command("disable")
def regles_desactiver(
    numero: Annotated[int, typer.Argument(help="Numéro de la règle à désactiver.")],
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
) -> None:
    """Désactive une règle ; ses opérations redeviennent en attente au classify suivant."""
    try:
        with Ledger(base) as ledger:
            regle = ledger.desactiver_regle(numero)
            mois = ledger.mois_de_regle(numero)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    console.print(f"Règle désactivée : {regle.decrire()}", markup=False, highlight=False)
    if mois:
        console.print(f"Mois concernés : {', '.join(mois)}. À relancer :")
        for m in mois:
            console.print(f"  budget classify --mois {m}", markup=False)
    else:
        console.print("Aucune opération classée par cette règle.")


@app.command("export")
def exporter(
    mois: Annotated[
        str, typer.Option("--mois", callback=_valider_mois, help="Mois à exporter (AAAA-MM).")
    ],
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
    sortie: Annotated[Path, typer.Option("--sortie", help="Dossier des exports CSV.")] = Path(
        "data/exports"
    ),
) -> None:
    """Écrit le CSV du mois à partir du classement enregistré (jalon 1)."""
    try:
        with Ledger(base) as ledger:
            chemin, nombre = exporter_mois(ledger, mois, sortie)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    console.print(f"{nombre} opérations exportées dans {chemin}", highlight=False)


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
