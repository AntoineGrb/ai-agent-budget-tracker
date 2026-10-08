"""Interface en ligne de commande `budget`."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from budget import __version__
from budget.config import charger_contexte
from budget.errors import ConfigError
from budget.montants import formater_montant

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
