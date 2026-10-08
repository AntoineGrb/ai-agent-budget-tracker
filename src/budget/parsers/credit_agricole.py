"""Parsers des exports CSV du Crédit Agricole : cartes à débit différé et compte joint (spec 02).

Aucun classement ici : les lignes de débit différé du joint restent des opérations ordinaires,
leur neutralisation relève de l'étape 5.
"""

import csv
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from budget.dates import dernier_jour_du_mois, parse_date_fr
from budget.errors import ControleError, ParseError
from budget.models import MOTIF_CARTE, Compte, OperationBrute, Source
from budget.montants import Montant, formater_montant, parse_montant_optionnel

ENCODAGE = "cp1252"

_DATE_DONNEE = re.compile(r"\d{2}/\d{2}/\d{4}")
_EN_TETE_COLONNES = ["Date", "Libellé", "Débit euros", "Crédit euros"]

# Fichier cartes
_SECTION_CARTE = re.compile(r"carte n°\s*[\dX ]*?(\d{4})\s*$")
_ENCOURS_DEBITE = re.compile(r"Encours débité le (.+)")

# Fichier compte joint
_PERIODE = re.compile(
    r"Liste des opérations du compte entre le (\d{2}/\d{2}/\d{4}) et le (\d{2}/\d{2}/\d{4})"
)
_ICS = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}")
_CARTES_LIBELLE = (
    re.compile(r"CARTE X(\d{4})", re.IGNORECASE),
    re.compile(r"Carte N° X ?(\d{4})", re.IGNORECASE),
    re.compile(r"carte \d{6}X+(\d{4})", re.IGNORECASE),
)
_TYPE_DEBIT_DIFFERE = "Prélèvement carte"
_DEBIT_DIFFERE = re.compile(r"DEPENSES CARTE X(\d{4}) AU (\d{2}/\d{2}/\d{2})")  # RG-03


class _Modele(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReleveCarte(_Modele):
    """Section d'une carte dans l'export cartes."""

    carte: str = Field(pattern=MOTIF_CARTE)
    date_debit: date  # RG-01
    encours: Montant  # positif : montant prélevé sur le joint
    operations: tuple[OperationBrute, ...]


class ReleveCartes(_Modele):
    fichier: str
    cartes: tuple[ReleveCarte, ...]


class LigneDebitDiffere(_Modele):
    """Ligne « DEPENSES CARTE Xdddd AU JJ/MM/AA » du compte joint (RG-03)."""

    carte: str = Field(pattern=MOTIF_CARTE)
    date_debit: date
    date_arrete: date
    montant: Montant  # positif


class ReleveJoint(_Modele):
    fichier: str
    periode_debut: date
    periode_fin: date
    operations: tuple[OperationBrute, ...]
    debits_differes: tuple[LigneDebitDiffere, ...]


def _erreur(message: str, fichier: str, ligne: int) -> ParseError:
    return ParseError(message, fichier=fichier, position=f"ligne {ligne}")


def _date(texte: str, fichier: str, ligne: int) -> date:
    try:
        return parse_date_fr(texte)
    except ParseError as erreur:
        raise _erreur(erreur.message, fichier, ligne) from erreur


def _montant_signe(
    debit: str, credit: str, fichier: str, ligne: int, date_operation: date
) -> Decimal:
    """Montant signé d'une ligne à un seul montant : `-débit` ou `+crédit`."""
    try:
        montant_debit = parse_montant_optionnel(debit)
        montant_credit = parse_montant_optionnel(credit)
    except ParseError as erreur:
        raise _erreur(erreur.message, fichier, ligne) from erreur
    jour = f"{date_operation:%d/%m/%Y}"
    if montant_debit is not None and montant_credit is not None:
        raise _erreur(
            f"opération du {jour} : un seul montant attendu, débit et crédit trouvés "
            f"({formater_montant(montant_debit)} et {formater_montant(montant_credit)})",
            fichier,
            ligne,
        )
    montant = montant_credit if montant_debit is None else -montant_debit
    if montant is None:
        raise _erreur(
            f"opération du {jour} : un montant attendu en débit ou en crédit, aucun trouvé",
            fichier,
            ligne,
        )
    if (montant_debit is not None and montant_debit <= 0) or (
        montant_credit is not None and montant_credit <= 0
    ):
        raise _erreur(
            f"opération du {jour} : montant strictement positif attendu dans sa colonne, "
            f"trouvé {formater_montant(montant_debit or montant_credit or Decimal(0))}",
            fichier,
            ligne,
        )
    return montant


def _cellules_donnee(cellules: list[str], fichier: str, ligne: int) -> tuple[str, str, str, str]:
    """Les 4 colonnes d'une ligne de données ; les cellules au-delà doivent être vides."""
    if len(cellules) < 4 or any(c.strip() for c in cellules[4:]):
        raise _erreur(
            f"4 colonnes attendues (Date;Libellé;Débit euros;Crédit euros), "
            f"{len([c for c in cellules if c.strip()])} cellule(s) renseignée(s) sur "
            f"{len(cellules)} trouvée(s)",
            fichier,
            ligne,
        )
    return cellules[0], cellules[1], cellules[2], cellules[3]


# --------------------------------------------------------------------------------------------
# Fichier cartes
# --------------------------------------------------------------------------------------------


class _Section:
    """Section en cours de lecture dans l'export cartes."""

    def __init__(self, carte: str, ligne: int) -> None:
        self.carte = carte
        self.ligne = ligne
        self.date_debit: date | None = None
        self.encours: Decimal | None = None
        self.en_tete_vu = False
        self.operations: list[OperationBrute] = []

    def cloturer(self, fichier: str) -> ReleveCarte:
        if self.date_debit is None or self.encours is None:
            raise _erreur(
                f"section de la carte {self.carte} sans ligne « Encours débité le <date>;"
                "<montant> »",
                fichier,
                self.ligne,
            )
        if not self.en_tete_vu:
            raise _erreur(
                f"section de la carte {self.carte} sans en-tête de colonnes "
                f"« {';'.join(_EN_TETE_COLONNES)} »",
                fichier,
                self.ligne,
            )
        return ReleveCarte(
            carte=self.carte,
            date_debit=self.date_debit,
            encours=self.encours,
            operations=tuple(self.operations),
        )


def _lignes_physiques(chemin: Path) -> Iterator[tuple[int, str]]:
    with chemin.open(encoding=ENCODAGE, newline="") as flux:
        for numero, ligne in enumerate(flux, start=1):
            yield numero, ligne.rstrip("\r\n")


def parser_ca_cartes(chemin: Path) -> ReleveCartes:
    """Lit l'export des cartes à débit différé (`CA_CB_AAAAMM.csv`).

    Lève `ParseError` sur tout format inattendu et `ControleError` si le net d'une carte diffère
    de son encours débité.
    """
    fichier = chemin.name
    sections: list[ReleveCarte] = []
    section: _Section | None = None
    en_zone = False

    for numero, ligne in _lignes_physiques(chemin):
        cellules = next(csv.reader([ligne], delimiter=";"))
        premiere = cellules[0].strip() if cellules else ""

        if correspondance := _SECTION_CARTE.search(ligne):
            if section is not None:
                sections.append(section.cloturer(fichier))
            section = _Section("X" + correspondance.group(1), numero)
            en_zone = False
        elif not any(c.strip() for c in cellules):
            en_zone = False
        elif _DATE_DONNEE.fullmatch(premiere):
            if section is None or not en_zone:
                raise _erreur(
                    "ligne de données hors d'une zone ouverte par l'en-tête "
                    f"« {';'.join(_EN_TETE_COLONNES)} »",
                    fichier,
                    numero,
                )
            section.operations.append(_operation_carte(cellules, section, fichier, numero))
        elif en_zone:
            raise _erreur(
                "ligne inattendue dans la zone de données : date JJ/MM/AAAA ou ligne vide attendue",
                fichier,
                numero,
            )
        elif premiere.startswith("Encours"):
            if section is None:
                raise _erreur("ligne « Encours » hors d'une section carte", fichier, numero)
            section.date_debit, section.encours = _encours(cellules, fichier, numero)
        elif [c.strip() for c in cellules[:4]] == _EN_TETE_COLONNES:
            if section is None or section.encours is None:
                raise _erreur(
                    "en-tête de colonnes avant la ligne « Encours débité le » de la section",
                    fichier,
                    numero,
                )
            section.en_tete_vu = True
            en_zone = True

    if section is not None:
        sections.append(section.cloturer(fichier))
    if not sections:
        raise ParseError("aucune section « carte n° … » trouvée", fichier=fichier)

    releve = ReleveCartes(fichier=fichier, cartes=tuple(sections))
    _verifier_encours(releve)
    return releve


def _encours(cellules: list[str], fichier: str, ligne: int) -> tuple[date, Decimal]:
    correspondance = _ENCOURS_DEBITE.fullmatch(cellules[0].strip())
    if correspondance is None or len(cellules) < 2:
        # Point ouvert de la spec 02 : l'en-tête d'un encours non encore débité n'a pas été
        # observé. On s'arrête plutôt que de deviner.
        raise _erreur(
            "en-tête d'encours non reconnu : attendu « Encours débité le <date>;<montant> »",
            fichier,
            ligne,
        )
    date_debit = _date(correspondance.group(1), fichier, ligne)
    try:
        montant = parse_montant_optionnel(cellules[1])
    except ParseError as erreur:
        raise _erreur(erreur.message, fichier, ligne) from erreur
    if montant is None:
        raise _erreur("montant de l'encours absent", fichier, ligne)
    # L'en-tête porte le solde de la carte (négatif) : l'encours prélevé en est l'opposé.
    return date_debit, -montant


def _operation_carte(
    cellules: list[str], section: _Section, fichier: str, ligne: int
) -> OperationBrute:
    texte_date, libelle, debit, credit = _cellules_donnee(cellules, fichier, ligne)
    date_operation = _date(texte_date, fichier, ligne)
    libelle = libelle.strip()
    return OperationBrute(
        source=Source.CA_CB,
        compte=Compte.JOINT,
        fichier=fichier,
        position=ligne,
        date_operation=date_operation,
        date_debit=section.date_debit,  # RG-01
        libelle=libelle,
        libelle_brut=libelle,
        carte=section.carte,
        montant=_montant_signe(debit, credit, fichier, ligne, date_operation),
    )


def _verifier_encours(releve: ReleveCartes) -> None:
    """Contrôle interne bloquant : pour chaque carte, débits - crédits = encours."""
    for carte in releve.cartes:
        net = -sum((op.montant for op in carte.operations), start=Decimal(0))
        if net != carte.encours:
            raise ControleError(
                f"{releve.fichier} : carte {carte.carte}, encours débité le "
                f"{carte.date_debit:%d/%m/%Y} = {formater_montant(carte.encours)}, "
                f"somme des opérations (débits - crédits) = {formater_montant(net)} "
                f"(écart {formater_montant(abs(net - carte.encours))}) : "
                "une opération manque ou est mal lue"
            )


# --------------------------------------------------------------------------------------------
# Fichier compte joint
# --------------------------------------------------------------------------------------------


def parser_ca_joint(chemin: Path) -> ReleveJoint:
    """Lit l'export du compte joint (`CA_CPTE_JOINT_AAAAMM.csv`), libellés multi-lignes compris."""
    fichier = chemin.name
    periode: tuple[date, date] | None = None
    en_tete_vu = False
    operations: list[OperationBrute] = []
    debits_differes: list[LigneDebitDiffere] = []

    # `newline=""` + module csv : gère les libellés multi-lignes et le mélange CRLF/LF.
    with chemin.open(encoding=ENCODAGE, newline="") as flux:
        lecteur = csv.reader(flux, delimiter=";")
        fin_precedente = 0
        for cellules in lecteur:
            numero = fin_precedente + 1  # première ligne physique de l'enregistrement
            fin_precedente = lecteur.line_num
            premiere = cellules[0].strip() if cellules else ""

            if correspondance := _PERIODE.fullmatch(premiere):
                periode = _periode(correspondance, fichier, numero)
            elif [c.strip() for c in cellules[:4]] == _EN_TETE_COLONNES:
                if periode is None:
                    raise _erreur(
                        "en-tête de colonnes avant la ligne « Liste des opérations du compte "
                        "entre le … et le … »",
                        fichier,
                        numero,
                    )
                en_tete_vu = True
            elif en_tete_vu and _DATE_DONNEE.fullmatch(premiere):
                operation = _operation_joint(cellules, fichier, numero)
                operations.append(operation)
                if (ligne_differe := _debit_differe(operation, fichier, numero)) is not None:
                    debits_differes.append(ligne_differe)

    if periode is None:
        raise ParseError(
            "ligne « Liste des opérations du compte entre le … et le … » introuvable",
            fichier=fichier,
        )
    if not en_tete_vu:
        raise ParseError(
            f"en-tête de colonnes « {';'.join(_EN_TETE_COLONNES)} » introuvable", fichier=fichier
        )
    return ReleveJoint(
        fichier=fichier,
        periode_debut=periode[0],
        periode_fin=periode[1],
        operations=tuple(operations),
        debits_differes=tuple(debits_differes),
    )


def _periode(correspondance: re.Match[str], fichier: str, ligne: int) -> tuple[date, date]:
    debut = _date(correspondance.group(1), fichier, ligne)
    fin = _date(correspondance.group(2), fichier, ligne)
    attendu_fin = dernier_jour_du_mois(debut.year, debut.month)
    if debut.day != 1 or fin != attendu_fin:  # RG-02 : on raisonne par mois civil complet
        raise _erreur(
            f"période du {debut:%d/%m/%Y} au {fin:%d/%m/%Y} : un mois civil complet est attendu "
            f"(par exemple du {debut.replace(day=1):%d/%m/%Y} au {attendu_fin:%d/%m/%Y}) (RG-02)",
            fichier,
            ligne,
        )
    return debut, fin


def _operation_joint(cellules: list[str], fichier: str, ligne: int) -> OperationBrute:
    texte_date, cellule_libelle, debit, credit = _cellules_donnee(cellules, fichier, ligne)
    date_operation = _date(texte_date, fichier, ligne)
    montant = _montant_signe(debit, credit, fichier, ligne, date_operation)

    lignes = [element.strip() for element in cellule_libelle.split("\n")]
    libelle = lignes[1] if len(lignes) > 1 and lignes[1] else lignes[0]
    ics_trouves = [element for element in lignes[2:] if _ICS.fullmatch(element)]
    if len(ics_trouves) > 1:
        raise _erreur(
            f"opération du {date_operation:%d/%m/%Y} ({formater_montant(montant)}) : "
            f"un seul identifiant créancier SEPA attendu, {len(ics_trouves)} trouvés",
            fichier,
            ligne,
        )
    return OperationBrute(
        source=Source.CA_JOINT,
        compte=Compte.JOINT,
        fichier=fichier,
        position=ligne,
        date_operation=date_operation,
        type_operation=lignes[0],
        libelle=libelle,
        libelle_brut=cellule_libelle,
        ics=ics_trouves[0] if ics_trouves else None,
        carte=_carte_du_libelle(libelle),  # RG-17 : alerte à l'étape 5
        montant=montant,
    )


def _carte_du_libelle(libelle: str) -> str | None:
    for motif in _CARTES_LIBELLE:
        if correspondance := motif.search(libelle):
            return "X" + correspondance.group(1)
    return None


def _debit_differe(operation: OperationBrute, fichier: str, ligne: int) -> LigneDebitDiffere | None:
    if operation.type_operation != _TYPE_DEBIT_DIFFERE:
        return None
    correspondance = _DEBIT_DIFFERE.search(operation.libelle)
    if correspondance is None:
        return None
    return LigneDebitDiffere(
        carte="X" + correspondance.group(1),
        date_debit=operation.date_operation,
        date_arrete=_date(correspondance.group(2), fichier, ligne),
        montant=-operation.montant,
    )
