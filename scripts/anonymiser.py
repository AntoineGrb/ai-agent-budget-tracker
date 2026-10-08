"""Génère les fixtures anonymisées d'un mois à partir des extractions réelles.

Usage : uv run python scripts/anonymiser.py --mois 2026-06

Lit data/raw/AAAA-MM/ et data/anonymisation.yaml, écrit tests/fixtures/AAAA-MM/ avec les mêmes
noms de fichiers, puis affiche un rapport de revue (virements, chèques, remises) à relire avant
tout commit.

CSV (cp1252, octets de fin de ligne conservés) :
  1. table de correspondance exacte, insensible à la casse, sur des mots entiers ;
  2. IBAN français remplacés par un IBAN fictif de même format, à clé « 00 » (jamais valide) ;
  3. séquences de 10 chiffres ou plus (comptes, prêts, contrats, références client) ;
  4. dans les libellés multi-lignes du joint : séquences de 6 chiffres ou plus en lignes 3 et
     suivantes, et ligne 2 réduite à un numéro (chèques, remises).
  Les identifiants créanciers SEPA (ICS) sont toujours conservés. Une séquence remplacée garde sa
  longueur : de 11 chiffres ou plus, elle est masquée en « X » (aucune séquence de 11 chiffres ne
  subsiste) ; plus courte, elle devient une suite de chiffres déterministe (même valeur d'un
  fichier à l'autre), dérivée d'une graine secrète pour ne pas permettre de retrouver l'origine.

PDF : caviardage PyMuPDF des valeurs de la table, des IBAN français et des jetons de 11 chiffres
ou plus (hors ICS), métadonnées effacées.
Le script vérifie que tous les mots hors zones caviardées, montants compris, sont inchangés.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pymupdf
import yaml

RACINE = Path(__file__).resolve().parent.parent
MOTIF_IBAN_FR = re.compile(r"FR\d{12}[0-9A-Z]{11}\d{2}")
MOTIF_ICS = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}")
MOTIF_CHAMP_CITE = re.compile(r'"((?:[^"]|"")*)"')
MOTIF_FIN_DE_LIGNE = re.compile(r"(\r\n|\r|\n)")
MOTIF_JETON = re.compile(r"\S+")
TYPES_A_REVOIR = ("virement", "chèque", "cheque", "remise")


class Anonymiseur:
    def __init__(self, remplacements: dict[str, str], graine: str) -> None:
        for valeur in remplacements.values():
            if any(c in valeur for c in ';"\r\n'):
                raise ValueError(
                    f'valeur de remplacement interdite (; " ou saut de ligne) : {valeur!r}'
                )
        self.graine = graine
        self.remplacements = {cle.lower(): valeur for cle, valeur in remplacements.items()}
        cles = sorted(remplacements, key=len, reverse=True)
        self.motif_table = (
            re.compile(
                r"(?<!\w)(" + "|".join(re.escape(cle) for cle in cles) + r")(?!\w)",
                re.IGNORECASE,
            )
            if cles
            else None
        )

    # Remplacements élémentaires -------------------------------------------------------------

    def _chiffres(self, valeur: str) -> str:
        """Suite de chiffres déterministe, de même longueur que `valeur`."""
        resultat = ""
        compteur = 0
        while len(resultat) < len(valeur):
            bloc = hashlib.sha256(f"{self.graine}|{valeur}|{compteur}".encode()).hexdigest()
            resultat += "".join(str(int(c, 16) % 10) for c in bloc)
            compteur += 1
        return resultat[: len(valeur)]

    def _masquer(self, valeur: str) -> str:
        return "X" * len(valeur) if len(valeur) >= 11 else self._chiffres(valeur)

    def _iban(self, correspondance: re.Match[str]) -> str:
        iban = correspondance.group(0)
        return "FR00" + self._chiffres(iban)[: len(iban) - 4]

    def _table(self, texte: str) -> str:
        if self.motif_table is None:
            return texte
        return self.motif_table.sub(lambda m: self.remplacements[m.group(0).lower()], texte)

    def _par_jeton(self, texte: str, motif: re.Pattern[str]) -> str:
        """Remplace les séquences de chiffres de `motif`, sauf dans les jetons ICS."""

        def jeton(m: re.Match[str]) -> str:
            if MOTIF_ICS.fullmatch(m.group(0)):
                return m.group(0)
            return motif.sub(lambda c: self._masquer(c.group(0)), m.group(0))

        return MOTIF_JETON.sub(jeton, texte)

    # Règles par zone ----------------------------------------------------------------------------

    def texte_general(self, texte: str) -> str:
        texte = self._table(texte)
        texte = MOTIF_IBAN_FR.sub(self._iban, texte)
        return self._par_jeton(texte, re.compile(r"\d{10,}"))

    def champ_libelle(self, champ: str) -> str:
        """Libellé multi-lignes du joint : ligne 1 type, ligne 2 détail, lignes 3+ références."""
        morceaux = MOTIF_FIN_DE_LIGNE.split(champ)
        lignes, separateurs = morceaux[0::2], morceaux[1::2]
        sortie = []
        for rang, ligne in enumerate(lignes):
            ligne = self.texte_general(ligne)
            if rang >= 2:
                ligne = self._par_jeton(ligne, re.compile(r"\d{6,}"))
            elif rang == 1 and re.fullmatch(r"\s*\d{6,}\s*", ligne):
                ligne = re.sub(r"\d+", lambda c: self._masquer(c.group(0)), ligne)
            sortie.append(ligne)
        return "".join(
            ligne + (separateurs[i] if i < len(separateurs) else "")
            for i, ligne in enumerate(sortie)
        )

    def csv(self, texte: str) -> str:
        resultat = []
        dernier = 0
        for m in MOTIF_CHAMP_CITE.finditer(texte):
            resultat.append(self.texte_general(texte[dernier : m.start()]))
            resultat.append('"' + self.champ_libelle(m.group(1)) + '"')
            dernier = m.end()
        resultat.append(self.texte_general(texte[dernier:]))
        return "".join(resultat)

    # PDF --------------------------------------------------------------------------------------

    def termes_pdf(self, texte_page: str) -> set[str]:
        termes = set(MOTIF_IBAN_FR.findall(texte_page))
        for jeton in MOTIF_JETON.findall(texte_page):
            if re.search(r"\d{11}", jeton) and not MOTIF_ICS.fullmatch(jeton.strip("()")):
                termes.add(jeton)
        if self.motif_table is not None:
            termes |= {m.group(0) for m in self.motif_table.finditer(texte_page)}
        return termes


def _mots_hors_zones(page: pymupdf.Page, zones: list[pymupdf.Rect]) -> list[tuple[object, ...]]:
    mots = []
    for x0, y0, x1, y1, mot, *_ in page.get_text("words"):
        rect = pymupdf.Rect(x0, y0, x1, y1)
        if not any(rect.intersects(zone) for zone in zones):
            mots.append((mot, round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))
    return mots


def anonymiser_pdf(source: Path, cible: Path, anonymiseur: Anonymiseur) -> None:
    document = pymupdf.open(source)
    for page in document:
        zones: list[pymupdf.Rect] = []
        for terme in anonymiseur.termes_pdf(page.get_text()):
            zones.extend(page.search_for(terme))
        # Un mot partiellement caviardé, « (FR76…) » par exemple, laisse des fragments : la
        # comparaison exclut l'emprise complète des mots touchés, pas seulement les zones.
        emprises = zones + [
            pymupdf.Rect(mot[:4])
            for mot in page.get_text("words")
            if any(pymupdf.Rect(mot[:4]).intersects(zone) for zone in zones)
        ]
        avant = _mots_hors_zones(page, emprises)
        for zone in zones:
            page.add_redact_annot(zone, fill=(1, 1, 1))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        if _mots_hors_zones(page, emprises) != avant:
            raise RuntimeError(
                f"{source.name}, page {page.number + 1} : le caviardage a modifié des mots "
                "hors des zones visées (montants ou positions)"
            )
    document.set_metadata({})
    document.del_xml_metadata()
    document.save(cible, garbage=4, deflate=True, clean=True)


def anonymiser_csv(source: Path, cible: Path, anonymiseur: Anonymiseur) -> None:
    with source.open(encoding="cp1252", newline="") as flux:
        texte = flux.read()
    with cible.open("w", encoding="cp1252", newline="") as flux:
        flux.write(anonymiseur.csv(texte))


# Rapport de revue -------------------------------------------------------------------------------


def _revue_csv(chemin: Path) -> list[str]:
    texte = chemin.read_text(encoding="cp1252")
    lignes = []
    for m in MOTIF_CHAMP_CITE.finditer(texte):
        champ = m.group(1)
        if champ.split("\n", 1)[0].strip().lower().startswith(TYPES_A_REVOIR):
            lignes.append(
                " | ".join(ligne.strip() for ligne in champ.splitlines() if ligne.strip())
            )
    return lignes


def _revue_pdf(chemin: Path) -> list[str]:
    lignes = []
    for page in pymupdf.open(chemin):
        texte = page.get_text().splitlines()
        for i, ligne in enumerate(texte):
            if re.search(r"transfer|Virement|Debit", ligne):
                lignes.append(f"p{page.number + 1}: " + " ".join(texte[i : i + 3]))
    return lignes


def afficher_revue(dossier: Path) -> None:
    revues: dict[str, Callable[[Path], list[str]]] = {".csv": _revue_csv, ".pdf": _revue_pdf}
    print(f"\n=== Rapport de revue : {dossier} ===")
    for fichier in sorted(dossier.iterdir()):
        print(f"\n--- {fichier.name}")
        for ligne in revues[fichier.suffix.lower()](fichier):
            print(f"  {ligne}")
    print("\nRelire ces lignes avant de committer les fixtures.")


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument("--mois", required=True, help="Mois à traiter, format AAAA-MM")
    arguments = parseur.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", arguments.mois):
        parseur.error("--mois doit être au format AAAA-MM")

    source = RACINE / "data" / "raw" / arguments.mois
    table = RACINE / "data" / "anonymisation.yaml"
    cible = RACINE / "tests" / "fixtures" / arguments.mois
    if not source.is_dir():
        print(f"Dossier introuvable : {source}", file=sys.stderr)
        return 1
    if not table.is_file():
        print(f"Table de correspondance introuvable : {table}", file=sys.stderr)
        return 1

    config = yaml.safe_load(table.read_text(encoding="utf-8"))
    anonymiseur = Anonymiseur(
        {str(k): str(v) for k, v in (config.get("remplacements") or {}).items()},
        str(config["graine"]),
    )
    cible.mkdir(parents=True, exist_ok=True)
    for fichier in sorted(source.iterdir()):
        if fichier.suffix.lower() == ".csv":
            anonymiser_csv(fichier, cible / fichier.name, anonymiseur)
        elif fichier.suffix.lower() == ".pdf":
            anonymiser_pdf(fichier, cible / fichier.name, anonymiseur)
        else:
            continue
        print(f"Écrit : {cible / fichier.name}")
    afficher_revue(cible)
    return 0


if __name__ == "__main__":
    sys.exit(main())
