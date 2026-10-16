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
from budget.agents.synthese import Commentateur, CommentateurIndisponible, CommentateurLLM
from budget.agregats import Vue
from budget.categories import Categorie, libelle_complet
from budget.config import Contexte, charger_contexte
from budget.environnement import charger_env
from budget.errors import BudgetError, ConfigError
from budget.importation import importer_mois
from budget.ledger import Ledger
from budget.models import MOTIF_MOIS, Compte
from budget.montants import formater_montant
from budget.questions.dialogue import InterlocuteurTerminal
from budget.questions.execution import RapportClassement, classer_et_questionner
from budget.regles import TypeRegle
from budget.restitution.export_csv import exporter_mois
from budget.restitution.operations import depenses_par_categorie
from budget.restitution.synthese import Synthese, produire_synthese

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

    tableau = Table(title=f"Contexte valide : {chemin}", show_header=False)
    tableau.add_row("Seuil Amazon (RG-13)", formater_montant(contexte.seuils.amazon_auto))
    for compte in Compte:
        tableau.add_row(f"Budgets {compte.value}", str(len(contexte.budgets.de(compte))))
    tableau.add_row("Modèle de catégorisation", contexte.llm.modele_categorisation)
    tableau.add_row("Modèle de synthèse", contexte.llm.modele_synthese)
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
) -> None:
    """Classe le mois, pose les questions sur les débits hors carte, puis valide les cartes."""
    interactif = not sans_questions and sys.stdin.isatty()
    interlocuteur = InterlocuteurTerminal(console) if interactif else None
    try:
        reference = charger_contexte(contexte)
        categoriseur = _categoriseur(reference, sans_llm)
        with Ledger(base) as ledger:
            rapport = classer_et_questionner(mois, ledger, reference, interlocuteur, categoriseur)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    _afficher_depenses(rapport)
    _afficher_bilan(rapport, interactif)


def _raison_sans_llm(modele: str, sans_llm: bool, agent: str) -> str | None:
    """Mode dégradé sans erreur : `--sans-llm` ou clé API absente."""
    if sans_llm:
        return f"{agent} désactivé par --sans-llm"
    if modele.startswith("anthropic:") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY absente de l'environnement et de .env"
    return None


def _categoriseur(contexte: Contexte, sans_llm: bool) -> Categoriseur:
    modele = contexte.llm.modele_categorisation
    raison = _raison_sans_llm(modele, sans_llm, "catégoriseur")
    return CategoriseurIndisponible(raison) if raison else CategoriseurLLM(modele)


def _commentateur(contexte: Contexte, sans_llm: bool) -> Commentateur:
    modele = contexte.llm.modele_synthese
    raison = _raison_sans_llm(modele, sans_llm, "agent synthèse")
    return CommentateurIndisponible(raison) if raison else CommentateurLLM(modele)


def _afficher_depenses(rapport: RapportClassement) -> None:
    classements = {c.transaction_id: c for c in rapport.resultat.classements}
    totaux = depenses_par_categorie(rapport.transactions, classements)
    tableau = Table(title=f"Dépenses comptées : {rapport.resultat.mois}")
    for colonne in ("Catégorie", "Joint", "Perso", "Total"):
        tableau.add_column(colonne, justify="left" if colonne == "Catégorie" else "right")

    def cellule(montant: Decimal) -> str:
        return formater_montant(montant) if montant else ""

    par_compte = {Compte.JOINT: Decimal(0), Compte.PERSO: Decimal(0)}
    for categorie in Categorie:
        joint = totaux.get((categorie, Compte.JOINT), Decimal(0))
        perso = totaux.get((categorie, Compte.PERSO), Decimal(0))
        if not joint and not perso:
            continue
        par_compte[Compte.JOINT] += joint
        par_compte[Compte.PERSO] += perso
        tableau.add_row(categorie.libelle, cellule(joint), cellule(perso), cellule(joint + perso))
    tableau.add_section()
    total = par_compte[Compte.JOINT] + par_compte[Compte.PERSO]
    tableau.add_row(
        "Total",
        cellule(par_compte[Compte.JOINT]),
        cellule(par_compte[Compte.PERSO]),
        formater_montant(total),
    )
    console.print(tableau)

    par_id = {t.id: t for t in rapport.transactions}
    non_comptees = [c for c in rapport.resultat.classements if c.non_comptee]
    montant = -sum((par_id[c.transaction_id].montant for c in non_comptees), start=Decimal(0))
    console.print(f"Non comptées : {len(non_comptees)} opération(s), {formater_montant(montant)}")
    if rapport.resultat.en_attente:
        console.print(
            f"[yellow]Total incomplet : {rapport.resultat.en_attente} opération(s) en attente[/]"
        )


def _afficher_bilan(rapport: RapportClassement, interactif: bool) -> None:
    bilan = rapport.bilan
    if bilan is not None:
        etat = " (dialogue interrompu)" if bilan.interrompu else ""
        console.print(
            f"Questions : {bilan.a_poser} à poser, {bilan.posees} posées, {bilan.repondues} "
            f"répondues, {bilan.passees} passées, {bilan.couvertes} couvertes par une règle "
            f"créée en cours de route{etat} ; {bilan.regles_creees} règle(s) créée(s)"
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
    attente = {
        "questions": len(rapport.questions),
        "marchands à valider": len(rapport.groupes),
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


@app.command("synthese")
def synthetiser(
    mois: Annotated[
        str, typer.Option("--mois", callback=_valider_mois, help="Mois à synthétiser (AAAA-MM).")
    ],
    base: Annotated[Path, typer.Option("--base", help="Base SQLite du ledger.")] = Path(
        "data/budget.sqlite"
    ),
    contexte: Annotated[
        Path, typer.Option("--contexte", help="Fichier de contexte du foyer.")
    ] = Path("data/contexte.yaml"),
    sans_llm: Annotated[
        bool, typer.Option("--sans-llm", help="N'appelle pas l'agent synthèse.")
    ] = False,
    regenerer: Annotated[
        bool, typer.Option("--regenerer", help="Redemande un commentaire même inchangé.")
    ] = False,
) -> None:
    """Chiffres du mois par catégorie, contrôlés, puis commentés par l'agent synthèse."""
    try:
        reference = charger_contexte(contexte)
        commentateur = _commentateur(reference, sans_llm)
        with Ledger(base) as ledger:
            synthese = produire_synthese(mois, ledger, reference, commentateur, regenerer=regenerer)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    _afficher_synthese(synthese)


def _ecart(montant: Decimal | None) -> str:
    if montant is None:
        return ""
    return ("+" if montant > 0 else "") + formater_montant(montant)


def _optionnel(montant: Decimal | None) -> str:
    return formater_montant(montant) if montant is not None else ""


def _afficher_vue(vue: Vue, mois: str) -> None:
    moyenne = vue.categories[0].mois_moyenne if vue.categories else 0
    tableau = Table(
        title=f"Vue {vue.compte.value} {mois} : {formater_montant(vue.total)} sur "
        f"{vue.nb_operations} opération(s)",
        caption=f"Moyenne sur {moyenne} mois classé(s) et complet(s) parmi les 3 précédents",
    )
    colonnes = ("Catégorie", "Montant", "Budget", "Écart budget", "Moyenne 3 mois", "Écart moyenne")
    for colonne in colonnes:
        tableau.add_column(colonne, justify="left" if colonne == "Catégorie" else "right")
    for ligne in vue.categories:
        tableau.add_row(
            ligne.categorie.libelle,
            formater_montant(ligne.montant),
            _optionnel(ligne.budget),
            _ecart(ligne.ecart_budget),
            _optionnel(ligne.moyenne_3_mois),
            _ecart(ligne.ecart_moyenne),
        )
    tableau.add_section()
    tableau.add_row("Total", formater_montant(vue.total), "", "", "", "")
    console.print(tableau)
    console.print(
        f"Non comptées : {vue.non_comptees.nombre} opération(s), "
        f"{formater_montant(vue.non_comptees.montant)}"
    )
    grosses = Table(title=f"Plus grosses dépenses, vue {vue.compte.value}")
    for colonne in ("Date", "Montant", "Sous-catégorie", "Marchand"):
        grosses.add_column(colonne, justify="right" if colonne == "Montant" else "left")
    for depense in vue.plus_grosses:
        grosses.add_row(
            f"{depense.date_operation:%d/%m/%Y}",
            formater_montant(depense.montant),
            libelle_complet(depense.sous_categorie),
            depense.marchand or "",
        )
    console.print(grosses)


def _afficher_synthese(synthese: Synthese) -> None:
    agregats = synthese.agregats
    for vue in (agregats.joint, agregats.perso):
        _afficher_vue(vue, agregats.mois)
    historique = Table(title="Historique sur 12 mois")
    for colonne in ("Mois", "Joint", "Perso", "Complet"):
        historique.add_column(colonne, justify="left" if colonne == "Mois" else "right")
    for point in agregats.historique:
        historique.add_row(
            point.mois,
            formater_montant(point.joint),
            formater_montant(point.perso),
            "oui" if point.complet else "non",
        )
    console.print(historique)
    commentaire = synthese.commentaire
    if commentaire is None:
        console.print(
            f"[yellow]Synthèse enregistrée sans commentaire : {synthese.avertissement}[/]",
            highlight=False,
        )
        return
    origine = " (réutilisé, chiffres inchangés)" if synthese.reutilise else ""
    console.print(f"[bold]Commentaire{origine}[/]")
    console.print(commentaire.resume, markup=False, highlight=False)
    for point_cle in commentaire.points_cles:
        console.print(f"  - {point_cle}", markup=False, highlight=False)


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
            r.condition_montant(),
            r.libelle_resultat(),
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
    """Écrit le CSV du mois à partir du classement enregistré."""
    try:
        with Ledger(base) as ledger:
            chemin, nombre = exporter_mois(ledger, mois, sortie)
    except BudgetError as erreur:
        console_erreur.print(str(erreur), style="bold red", markup=False, highlight=False)
        raise typer.Exit(code=1) from None
    console.print(f"{nombre} opérations exportées dans {chemin}", highlight=False)
