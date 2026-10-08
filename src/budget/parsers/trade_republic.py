"""Parser du relevé PDF mensuel de Trade Republic, compte courant uniquement (spec 03).

Deux couches :

- la lecture PDF (`pdfplumber`), isolée dans `lire_pages`, qui ne fait que restituer les mots
  positionnés de chaque page ;
- des fonctions pures sur ces mots (`Mot`), qui repèrent les colonnes page par page, découpent
  les transactions en bandes verticales et appliquent les contrôles bloquants C1 à C5.

Le texte brut (`extract_text`) n'est jamais utilisé : il colle les numéros de magasin aux
montants (`CARREFOUR CITY 2466078 6,45 €` lu 24 660 786,45 €).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException
from pydantic import BaseModel, ConfigDict

from budget.dates import dernier_jour_du_mois, parse_date_fr
from budget.errors import ControleError, ParseError
from budget.models import Compte, OperationBrute, Source
from budget.montants import Montant, formater_montant, parse_montant

PRODUIT_COMPTE_COURANT = "Compte courant"

# Tolérances géométriques, en points PDF. Ce sont les seules constantes de position : toutes les
# abscisses de colonnes sont relues sur les en-têtes de chaque page.
TOLERANCE_LIGNE = 1.0
"""Écart vertical maximal entre deux mots d'une même ligne visuelle."""
TOLERANCE_ENTETE = 8.0
"""Demi-hauteur de la bande, autour du mot DESCRIPTION, où chercher les autres en-têtes."""
TOLERANCE_COLONNE = 3.0
"""Marge à gauche d'un en-tête : un mot appartient à la colonne dès `x0 >= x0_entete - marge`."""
TOLERANCE_BORDURE_MONTANTS = 5.0
"""Marge à gauche de l'en-tête ENTRÉE au-delà de laquelle un mot est lu comme un montant."""
TOLERANCE_HAUT_BANDE = 2.0
"""Une transaction commence 2 pt au-dessus du mot du jour (les montants ne le précèdent jamais)."""
TOLERANCE_SOLDE = 8.0
"""Écart maximal entre le bord droit d'un solde et celui de l'en-tête SOLDE (suivi d'un `€`)."""
TOLERANCE_MOUVEMENT = 8.0
"""Écart maximal entre le bord gauche d'un mouvement et celui de l'en-tête ENTRÉE ou SORTIE."""
TOLERANCE_MILLIERS = 5.0
"""Espace maximal entre deux mots d'un même montant à séparateur de milliers (`12 345,67`)."""
FRACTION_PIED_DE_PAGE = 0.75
"""Le pied de page (`Trade Republic Bank GmbH…`) est cherché dans le quart bas de la page."""

_MOTIF_MONTANT = re.compile(r"-?\d+,\d{2}")
_MOTIF_JOUR = re.compile(r"\d{2}")
_MOTIF_ANNEE = re.compile(r"\d{4}")
_MOTIF_MOIS = re.compile(r"[^\W\d_]+\.?")
_MOTIF_PERIODE = re.compile(r"(\d{1,2} [^\W\d_]+\.? \d{4}) - (\d{1,2} [^\W\d_]+\.? \d{4})")
_MOTIF_DEBUT_MILLIERS = re.compile(r"-?\d{1,3}")
_MOTIF_FIN_MILLIERS = re.compile(r"\d{3}(?:\s?\d{3})*,\d{2}")
_SYNTHESE = "SYNTHÈSE DU RELEVÉ DE COMPTE"
_PIED_DE_PAGE = "Trade Republic Bank GmbH"
_FINS_DE_TABLEAU = ("APERÇU", "REMARQUES")


class Mot(TypedDict):
    """Mot positionné, au format de `pdfplumber.Page.extract_words` (sous-ensemble utile)."""

    text: str
    x0: float
    x1: float
    top: float


@dataclass(frozen=True)
class Page:
    numero: int  # 1 = première page du PDF
    hauteur: float
    mots: tuple[Mot, ...]


@dataclass(frozen=True)
class SyntheseTR:
    """Ligne produit de la synthèse d'un relevé : quatre montants de référence."""

    produit: str
    solde_debut: Decimal
    total_entrees: Decimal
    total_sorties: Decimal
    solde_fin: Decimal


@dataclass(frozen=True)
class ReleveBrut:
    """Relevé découpé dans le PDF : sa synthèse et ses pages, pas encore lues."""

    synthese: SyntheseTR
    pages: tuple[Page, ...]


@dataclass(frozen=True)
class Colonnes:
    """Repères d'une page, relus sur l'en-tête du tableau des transactions."""

    top_entete: float
    x0_type: float
    x0_description: float
    x0_entree: float
    x0_sortie: float
    x1_solde: float


@dataclass(frozen=True)
class Bande:
    """Mots d'une transaction, répartis par colonne et dans l'ordre de lecture."""

    page: int
    rang: int  # rang de la transaction sur la page, à partir de 1
    jour: str
    mois: str
    annees: tuple[str, ...]
    type_operation: tuple[str, ...]
    description: tuple[str, ...]
    montants: tuple[Mot, ...]  # mots-montants normalisés (sans `€`), de gauche à droite


@dataclass(frozen=True)
class LigneTR:
    """Transaction lue, avant contrôles."""

    page: int
    rang: int
    date_operation: date
    type_operation: str
    description: str
    montant: Decimal  # +entrée ou -sortie (RG-12)
    solde: Decimal


class ReleveTR(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fichier: str
    produit: str
    periode_debut: date
    periode_fin: date
    solde_debut: Montant
    total_entrees: Montant
    total_sorties: Montant  # positif
    solde_fin: Montant
    operations: tuple[OperationBrute, ...]


# --------------------------------------------------------------------------------------------
# Lecture PDF
# --------------------------------------------------------------------------------------------


def lire_pages(chemin: Path) -> list[Page]:
    """Mots positionnés de chaque page. `y_tolerance=1` sépare les en-têtes sur deux lignes."""
    try:
        with pdfplumber.open(chemin) as pdf:
            return [
                Page(
                    numero=numero,
                    hauteur=float(page.height),
                    mots=tuple(
                        Mot(
                            text=str(mot["text"]),
                            x0=float(mot["x0"]),
                            x1=float(mot["x1"]),
                            top=float(mot["top"]),
                        )
                        for mot in page.extract_words(y_tolerance=TOLERANCE_LIGNE)
                    ),
                )
                for numero, page in enumerate(pdf.pages, start=1)
            ]
    except (OSError, ValueError, PdfminerException) as erreur:  # PDF absent, illisible ou corrompu
        raise ParseError(
            f"PDF illisible ({type(erreur).__name__})", fichier=chemin.name
        ) from erreur


def parser_trade_republic(chemin: Path) -> ReleveTR:
    """Lit le compte courant du relevé PDF et applique les contrôles C1 à C5."""
    fichier = chemin.name
    pages = lire_pages(chemin)
    releves = decouper_releves(pages, fichier)
    courants = [r for r in releves if r.synthese.produit == PRODUIT_COMPTE_COURANT]
    if len(courants) != 1:
        produits = ", ".join(r.synthese.produit for r in releves) or "aucun"
        raise ParseError(
            f"exactement un relevé « {PRODUIT_COMPTE_COURANT} » attendu, "
            f"{len(courants)} trouvé(s) (relevés : {produits})",
            fichier=fichier,
        )
    releve = courants[0]
    debut, fin = lire_periode(releve.pages[0], fichier)
    lignes = [ligne for page in releve.pages for ligne in lire_lignes_page(page, fichier)]
    controler_releve(releve.synthese, debut, fin, lignes, fichier)
    return ReleveTR(
        fichier=fichier,
        produit=releve.synthese.produit,
        periode_debut=debut,
        periode_fin=fin,
        solde_debut=releve.synthese.solde_debut,
        total_entrees=releve.synthese.total_entrees,
        total_sorties=releve.synthese.total_sorties,
        solde_fin=releve.synthese.solde_fin,
        operations=tuple(
            OperationBrute(
                source=Source.TR,
                compte=Compte.PERSO,
                fichier=fichier,
                position=position,
                date_operation=ligne.date_operation,
                type_operation=ligne.type_operation,
                libelle=ligne.description,
                libelle_brut=ligne.description,
                montant=ligne.montant,
                solde_apres=ligne.solde,
            )
            for position, ligne in enumerate(lignes, start=1)
        ),
    )


# --------------------------------------------------------------------------------------------
# Fonctions pures sur les mots
# --------------------------------------------------------------------------------------------


def normaliser_montant(texte: str) -> Decimal | None:
    """Montant d'un mot (`1983,76`, `-15,20`, `€1238,73`), ou `None` si ce n'est pas un montant.

    Les `€` en tête et en fin sont retirés : pdfplumber colle parfois le `€` du montant précédent
    (`€1238,73`), cas qui faisait échouer le prototype.
    """
    nettoye = texte.strip("€")
    if not _MOTIF_MONTANT.fullmatch(nettoye):
        return None
    return parse_montant(nettoye)


def regrouper_lignes(mots: Sequence[Mot]) -> list[list[Mot]]:
    """Regroupe les mots en lignes visuelles (écart vertical ≤ `TOLERANCE_LIGNE`), triées en x."""
    lignes: list[list[Mot]] = []
    for mot in sorted(mots, key=lambda m: m["top"]):
        if lignes and mot["top"] - lignes[-1][0]["top"] <= TOLERANCE_LIGNE:
            lignes[-1].append(mot)
        else:
            lignes.append([mot])
    return [sorted(ligne, key=lambda m: m["x0"]) for ligne in lignes]


def _texte(ligne: Sequence[Mot]) -> str:
    return " ".join(mot["text"] for mot in ligne)


def decouper_releves(pages: Sequence[Page], fichier: str) -> list[ReleveBrut]:
    """Ouvre un relevé à chaque page de synthèse ; les pages suivantes lui appartiennent."""
    releves: list[tuple[SyntheseTR, list[Page]]] = []
    for page in pages:
        if (synthese := lire_synthese(page, fichier)) is not None:
            releves.append((synthese, [page]))
        elif releves:
            releves[-1][1].append(page)
    return [ReleveBrut(synthese=s, pages=tuple(p)) for s, p in releves]


def lire_synthese(page: Page, fichier: str) -> SyntheseTR | None:
    """Ligne produit de la synthèse (`Compte courant 1975,22 € 993,71 € 1435,36 € 1533,57 €`)."""
    lignes = regrouper_lignes(page.mots)
    index = next((i for i, ligne in enumerate(lignes) if _texte(ligne) == _SYNTHESE), None)
    if index is None:
        return None
    for ligne in lignes[index + 1 :]:
        montants = [m for mot in ligne if (m := normaliser_montant(mot["text"])) is not None]
        if not montants:
            continue
        produit = []
        for mot in ligne:
            if normaliser_montant(mot["text"]) is not None:
                break
            produit.append(mot["text"])
        if len(montants) != 4 or not produit:
            raise ParseError(
                f"synthèse du relevé : un produit suivi de 4 montants attendu, "
                f"{len(montants)} montant(s) trouvé(s)",
                fichier=fichier,
                position=f"page {page.numero}",
            )
        return SyntheseTR(" ".join(produit), *montants)
    raise ParseError(
        "synthèse du relevé sans ligne produit", fichier=fichier, position=f"page {page.numero}"
    )


def lire_periode(page: Page, fichier: str) -> tuple[date, date]:
    """Période de l'en-tête (`DATE 01 juin 2026 - 30 juin 2026`), au-dessus de la synthèse."""
    for ligne in regrouper_lignes(page.mots):
        if _texte(ligne) == _SYNTHESE:
            break
        if correspondance := _MOTIF_PERIODE.search(_texte(ligne)):
            try:
                return parse_date_fr(correspondance.group(1)), parse_date_fr(
                    correspondance.group(2)
                )
            except ParseError as erreur:
                raise ParseError(
                    erreur.message, fichier=fichier, position=f"page {page.numero}"
                ) from erreur
    raise ParseError(
        "période « JJ mois AAAA - JJ mois AAAA » introuvable au-dessus de la synthèse",
        fichier=fichier,
        position=f"page {page.numero}",
    )


def reperer_colonnes(page: Page, fichier: str) -> Colonnes | None:
    """Repères de colonnes relus sur l'en-tête du tableau ; `None` si la page n'en a pas."""
    descriptions = [m for m in page.mots if m["text"] == "DESCRIPTION"]
    if not descriptions:
        return None
    if len(descriptions) > 1:
        raise ParseError(
            f"un seul en-tête DESCRIPTION attendu, {len(descriptions)} trouvés",
            fichier=fichier,
            position=f"page {page.numero}",
        )
    reference = descriptions[0]
    bande = [m for m in page.mots if abs(m["top"] - reference["top"]) <= TOLERANCE_ENTETE]

    def entete(texte: str) -> Mot:
        trouves = [m for m in bande if m["text"] == texte]
        if len(trouves) != 1:
            raise ParseError(
                f"en-tête de colonne {texte} : 1 attendu près de DESCRIPTION, "
                f"{len(trouves)} trouvé(s)",
                fichier=fichier,
                position=f"page {page.numero}",
            )
        return trouves[0]

    return Colonnes(
        top_entete=max(m["top"] for m in bande),
        x0_type=entete("TYPE")["x0"],
        x0_description=reference["x0"],
        x0_entree=entete("ENTRÉE")["x0"],
        x0_sortie=entete("SORTIE")["x0"],
        x1_solde=entete("SOLDE")["x1"],
    )


def limite_basse(page: Page, colonnes: Colonnes) -> float:
    """Haut du premier repère sous le tableau : APERÇU, REMARQUES ou pied de page."""
    limites = [page.hauteur]
    for ligne in regrouper_lignes(page.mots):
        top = ligne[0]["top"]
        if top <= colonnes.top_entete:
            continue
        if ligne[0]["text"] in _FINS_DE_TABLEAU or (
            _texte(ligne).startswith(_PIED_DE_PAGE) and top >= FRACTION_PIED_DE_PAGE * page.hauteur
        ):
            limites.append(top)
    return min(limites)


def decouper_bandes(page: Page, colonnes: Colonnes, fichier: str) -> list[Bande]:
    """Découpe la zone utile en une bande verticale par transaction."""
    bas = limite_basse(page, colonnes)
    zone = [m for m in page.mots if colonnes.top_entete < m["top"] < bas]
    bord_date = colonnes.x0_type - TOLERANCE_COLONNE

    debuts: list[tuple[Mot, Mot]] = []
    for ligne in regrouper_lignes(zone):
        dates = [m for m in ligne if m["x0"] < bord_date]
        if (
            len(dates) >= 2
            and _MOTIF_JOUR.fullmatch(dates[0]["text"])
            and _MOTIF_MOIS.fullmatch(dates[1]["text"])
        ):
            debuts.append((dates[0], dates[1]))

    bornes = [jour["top"] - TOLERANCE_HAUT_BANDE for jour, _ in debuts] + [bas]
    orphelins = [m for m in zone if m["top"] < bornes[0]] if debuts else zone
    if orphelins:
        raise ParseError(
            f"{len(orphelins)} mot(s) dans le tableau hors d'une ligne de transaction "
            "(ligne sans jour ni mois dans la colonne DATE ?)",
            fichier=fichier,
            position=f"page {page.numero}",
        )

    bandes = []
    for rang, (jour, mois) in enumerate(debuts, start=1):
        haut, bas_bande = bornes[rang - 1], bornes[rang]
        mots = [m for m in zone if haut <= m["top"] < bas_bande and m is not jour and m is not mois]
        bandes.append(_lire_bande(mots, colonnes, page.numero, rang, jour, mois, fichier))
    return bandes


def _lire_bande(
    mots: list[Mot],
    colonnes: Colonnes,
    page: int,
    rang: int,
    jour: Mot,
    mois: Mot,
    fichier: str,
) -> Bande:
    bord_type = colonnes.x0_type - TOLERANCE_COLONNE
    bord_description = colonnes.x0_description - TOLERANCE_COLONNE
    bord_montants = colonnes.x0_entree - TOLERANCE_BORDURE_MONTANTS

    annees: list[str] = []
    types: list[str] = []
    description: list[str] = []
    candidats: list[Mot] = []
    for ligne in regrouper_lignes(mots):
        for mot in ligne:
            if mot["x0"] < bord_type:
                annees.append(mot["text"])
            elif mot["x0"] < bord_description:
                types.append(mot["text"])
            elif mot["x0"] < bord_montants:
                description.append(mot["text"])
            else:
                candidats.append(mot)

    montants = []
    for mot in _fusionner_milliers(candidats):
        texte = mot["text"].strip("€")
        if not texte:
            continue  # symbole € isolé
        if normaliser_montant(texte) is None:
            raise ParseError(
                "mot inattendu dans les colonnes de montants",
                fichier=fichier,
                position=f"page {page}, ligne {rang}",
            )
        montants.append(Mot(text=texte, x0=mot["x0"], x1=mot["x1"], top=mot["top"]))

    return Bande(
        page=page,
        rang=rang,
        jour=jour["text"],
        mois=mois["text"],
        annees=tuple(annees),
        type_operation=tuple(types),
        description=tuple(description),
        montants=tuple(sorted(montants, key=lambda m: m["x0"])),
    )


def _fusionner_milliers(mots: list[Mot]) -> list[Mot]:
    """Recolle `12` + `345,67` en `12345,67` (point ouvert de la spec : séparateur de milliers)."""
    fusionnes: list[Mot] = []
    for ligne in regrouper_lignes(mots):
        for mot in ligne:
            precedent = fusionnes[-1] if fusionnes else None
            if (
                precedent is not None
                and abs(precedent["top"] - mot["top"]) <= TOLERANCE_LIGNE
                and mot["x0"] - precedent["x1"] <= TOLERANCE_MILLIERS
                and _MOTIF_DEBUT_MILLIERS.fullmatch(precedent["text"])
                and _MOTIF_FIN_MILLIERS.fullmatch(mot["text"].rstrip("€"))
            ):
                fusionnes[-1] = Mot(
                    text=precedent["text"] + mot["text"],
                    x0=precedent["x0"],
                    x1=mot["x1"],
                    top=precedent["top"],
                )
            else:
                fusionnes.append(mot)
    return fusionnes


def affecter_montants(bande: Bande, colonnes: Colonnes, fichier: str) -> tuple[Decimal, Decimal]:
    """Mouvement signé (RG-12 : le sens vient de la colonne) et solde d'une bande."""
    position = f"page {bande.page}, ligne {bande.rang}"
    if len(bande.montants) != 2:
        raise ParseError(
            f"2 montants attendus (mouvement et solde), {len(bande.montants)} trouvé(s)",
            fichier=fichier,
            position=position,
        )
    mouvement, solde = sorted(bande.montants, key=lambda m: m["x1"])
    if abs(solde["x1"] - colonnes.x1_solde) > TOLERANCE_SOLDE:
        raise ParseError(
            f"solde mal aligné : bord droit à {solde['x1']:.1f} pt, attendu à moins de "
            f"{TOLERANCE_SOLDE:g} pt de l'en-tête SOLDE ({colonnes.x1_solde:.1f} pt)",
            fichier=fichier,
            position=position,
        )
    ecart_entree = abs(mouvement["x0"] - colonnes.x0_entree)
    ecart_sortie = abs(mouvement["x0"] - colonnes.x0_sortie)
    if min(ecart_entree, ecart_sortie) > TOLERANCE_MOUVEMENT or ecart_entree == ecart_sortie:
        raise ParseError(
            f"mouvement à {mouvement['x0']:.1f} pt : ni sous ENTRÉE "
            f"({colonnes.x0_entree:.1f} pt) ni sous SORTIE ({colonnes.x0_sortie:.1f} pt)",
            fichier=fichier,
            position=position,
        )
    montant = parse_montant(mouvement["text"])
    if montant <= 0:
        raise ParseError(
            f"mouvement {formater_montant(montant)} : montant positif attendu dans sa colonne",
            fichier=fichier,
            position=position,
        )
    signe = montant if ecart_entree < ecart_sortie else -montant  # RG-12
    return signe, parse_montant(solde["text"])


def lire_ligne(bande: Bande, colonnes: Colonnes, fichier: str) -> LigneTR:
    """Transforme une bande en transaction : date complète, type, description et montants."""
    position = f"page {bande.page}, ligne {bande.rang}"
    annees = [a for a in bande.annees if _MOTIF_ANNEE.fullmatch(a)]
    if len(annees) != 1 or len(bande.annees) != 1:
        raise ParseError(
            f"colonne DATE : une année attendue sous le jour, {len(bande.annees)} mot(s) trouvé(s)",
            fichier=fichier,
            position=position,
        )
    try:
        date_operation = parse_date_fr(f"{bande.jour} {bande.mois} {annees[0]}")
    except ParseError as erreur:
        raise ParseError(erreur.message, fichier=fichier, position=position) from erreur
    if not bande.type_operation:
        raise ParseError("colonne TYPE vide", fichier=fichier, position=position)
    montant, solde = affecter_montants(bande, colonnes, fichier)
    return LigneTR(
        page=bande.page,
        rang=bande.rang,
        date_operation=date_operation,
        type_operation=" ".join(bande.type_operation),
        description=" ".join(bande.description),
        montant=montant,
        solde=solde,
    )


def lire_lignes_page(page: Page, fichier: str) -> list[LigneTR]:
    """Transactions d'une page ; une page sans tableau (remarques, aperçu) n'en a aucune."""
    colonnes = reperer_colonnes(page, fichier)
    if colonnes is None:
        return []
    return [lire_ligne(b, colonnes, fichier) for b in decouper_bandes(page, colonnes, fichier)]


# --------------------------------------------------------------------------------------------
# Contrôles bloquants
# --------------------------------------------------------------------------------------------


def _reperer(ligne: LigneTR, numero: int) -> str:
    return (
        f"page {ligne.page}, ligne {ligne.rang} (opération n° {numero}), "
        f"{ligne.date_operation:%d/%m/%Y}"
    )


def controler_releve(
    synthese: SyntheseTR,
    debut: date,
    fin: date,
    lignes: Sequence[LigneTR],
    fichier: str,
) -> None:
    """Contrôle de sens, puis C1 à C5. Les messages ne citent jamais la description."""
    # C4 : un mois civil complet, toutes les dates dedans
    if debut.day != 1 or fin != dernier_jour_du_mois(debut.year, debut.month):
        raise ControleError(
            f"{fichier} : C4 : période du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}, "
            "un mois civil complet est attendu"
        )
    for numero, ligne in enumerate(lignes, start=1):
        if not debut <= ligne.date_operation <= fin:
            raise ControleError(
                f"{fichier} : C4 : {_reperer(ligne, numero)} hors de la période du "
                f"{debut:%d/%m/%Y} au {fin:%d/%m/%Y}"
            )

    # C5 : au moins une opération, sauf relevé sans mouvement
    if not lignes and (synthese.total_entrees or synthese.total_sorties):
        raise ControleError(
            f"{fichier} : C5 : aucune opération lue, alors que la synthèse annonce "
            f"{formater_montant(synthese.total_entrees)} d'entrées et "
            f"{formater_montant(synthese.total_sorties)} de sorties"
        )

    # Contrôle de sens (étape 7) et C1 : solde recalculé ligne à ligne
    precedent = synthese.solde_debut
    for numero, ligne in enumerate(lignes, start=1):
        variation = ligne.solde - precedent
        if (variation > 0) != (ligne.montant > 0) or variation == 0:
            raise ParseError(
                f"{ligne.date_operation:%d/%m/%Y} : mouvement {formater_montant(ligne.montant)} "
                f"mais solde passé de {formater_montant(precedent)} à "
                f"{formater_montant(ligne.solde)} : colonne ENTRÉE/SORTIE mal attribuée "
                "ou opération manquante (RG-12)",
                fichier=fichier,
                position=f"page {ligne.page}, ligne {ligne.rang}",
            )
        if precedent + ligne.montant != ligne.solde:
            raise ControleError(
                f"{fichier} : C1 : {_reperer(ligne, numero)} : solde précédent "
                f"{formater_montant(precedent)} + mouvement {formater_montant(ligne.montant)} "
                f"= {formater_montant(precedent + ligne.montant)}, solde lu "
                f"{formater_montant(ligne.solde)} (écart "
                f"{formater_montant(abs(ligne.solde - precedent - ligne.montant))})"
            )
        precedent = ligne.solde

    # C2 : totaux de la synthèse
    entrees = sum((ligne.montant for ligne in lignes if ligne.montant > 0), Decimal(0))
    sorties = -sum((ligne.montant for ligne in lignes if ligne.montant < 0), Decimal(0))
    for libelle, lu, attendu in (
        ("entrées", entrees, synthese.total_entrees),
        ("sorties", sorties, synthese.total_sorties),
    ):
        if lu != attendu:
            raise ControleError(
                f"{fichier} : C2 : total des {libelle} = {formater_montant(lu)}, synthèse = "
                f"{formater_montant(attendu)} (écart {formater_montant(abs(lu - attendu))})"
            )

    # C3 : solde final
    if precedent != synthese.solde_fin:
        raise ControleError(
            f"{fichier} : C3 : dernier solde lu {formater_montant(precedent)}, solde de fin de "
            f"la synthèse {formater_montant(synthese.solde_fin)} (écart "
            f"{formater_montant(abs(precedent - synthese.solde_fin))})"
        )
