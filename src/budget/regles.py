"""Mémoire des réponses : règles apprises et décisions (specs 06 et 07, cadrage §7).

Une décision porte sur une seule opération ; une règle s'applique à toutes les opérations qui lui
correspondent. Une décision prime sur une règle.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from budget.categories import SousCategorie
from budget.errors import ControleError, RegleError
from budget.libelles import cle_marchand, normaliser_libelle
from budget.models import MOTIF_MOIS, Nature, Source, Transaction
from budget.montants import Montant, formater_montant

ICS_PAYPAL = "LU96ZZZ0000000000000000058"  # RG-14
TYPES_CHEQUE = frozenset({"CHEQUE EMIS", "REMISE DE CHEQUE"})  # RG-14, types normalisés
PREFIXES_AMAZON = ("AMAZON", "AMZN")  # RG-13 : clés marchand Amazon
SOURCES_CARTE = frozenset({Source.CA_CB, Source.TR})


class TypeRegle(StrEnum):
    VIREMENT = "virement"
    PRELEVEMENT = "prelevement"
    MARCHAND_CARTE = "marchand_carte"  # clé marchand d'un paiement carte -> sous-catégorie
    TOUJOURS_DEMANDER = "toujours_demander"


class Critere(StrEnum):
    ICS = "ics"
    LIBELLE_EXACT = "libelle_exact"
    LIBELLE_PREFIXE = "libelle_prefixe"


_RANG_CRITERE = {Critere.ICS: 3, Critere.LIBELLE_EXACT: 2, Critere.LIBELLE_PREFIXE: 1}
_NATURES_AVEC_SOUS_CATEGORIE = {Nature.DEPENSE, Nature.REMBOURSEMENT}


class Regle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None  # None avant insertion
    type: TypeRegle
    source: Source
    critere: Critere
    valeur: str = Field(min_length=1)  # ICS, libellé normalisé, ou clé marchand
    montant_min: Montant | None = None  # signés ; min == max pour un montant exact
    montant_max: Montant | None = None
    nature: Nature | None = None  # None pour toujours_demander et marchand_carte
    sous_categorie: SousCategorie | None = None
    detail: str | None = None
    active: bool = True
    creee_le: datetime | None = None  # renseignées à la lecture en base
    desactivee_le: datetime | None = None

    @model_validator(mode="after")
    def _coherence(self) -> Self:
        if (self.montant_min is None) != (self.montant_max is None):
            raise ValueError("montant_min et montant_max sont tous deux renseignés ou absents")
        if (
            self.montant_min is not None
            and self.montant_max is not None
            and self.montant_min > self.montant_max
        ):
            raise ValueError(f"plage de montant inversée : {self.montant_min} > {self.montant_max}")
        if self.type is TypeRegle.VIREMENT and self.montant_min is None:
            raise ValueError("une règle de virement a obligatoirement une condition de montant")
        if self.type is TypeRegle.MARCHAND_CARTE:
            return self._coherence_marchand()
        if self.type is TypeRegle.TOUJOURS_DEMANDER:
            if self.nature is not None or self.sous_categorie is not None:
                raise ValueError("une règle « toujours demander » ne porte aucun résultat")
            return self
        if self.nature is None:
            raise ValueError("la nature est obligatoire")
        if self.nature is Nature.LIEN_DEPENSE:
            raise ValueError("un lien à une dépense est un cas unique : jamais une règle (RG-07)")
        if self.nature in _NATURES_AVEC_SOUS_CATEGORIE and self.sous_categorie is None:
            raise ValueError(f"la sous-catégorie est obligatoire pour la nature {self.nature}")
        return self

    def _coherence_marchand(self) -> Self:
        """Règle marchand : paiement carte, sous-catégorie seule, jamais Amazon (RG-13)."""
        if self.source not in SOURCES_CARTE:
            raise ValueError("une règle marchand porte sur les sources ca_cb ou tr")
        if self.critere is Critere.ICS:
            raise ValueError("une règle marchand ne porte pas sur un ICS")
        if self.nature is not None:
            raise ValueError("une règle marchand ne fixe pas la nature, qui vient du sens")
        if self.sous_categorie is None:
            raise ValueError("la sous-catégorie est obligatoire pour une règle marchand")
        if self.valeur.startswith(PREFIXES_AMAZON):
            raise ValueError("RG-13 : un marchand Amazon n'est jamais couvert par une règle")
        return self

    def s_applique(self, transaction: Transaction) -> bool:
        if not self.active or transaction.source is not self.source:
            return False
        # Une règle marchand compare sa valeur à la clé marchand, pas au libellé complet
        libelle = (
            cle_marchand(transaction.libelle_normalise)
            if self.type is TypeRegle.MARCHAND_CARTE
            else transaction.libelle_normalise
        )
        if self.critere is Critere.ICS:
            correspond = transaction.ics == self.valeur
        elif self.critere is Critere.LIBELLE_EXACT:
            correspond = libelle == self.valeur
        else:
            correspond = libelle.startswith(self.valeur)
        if not correspond:
            return False
        if self.montant_min is None or self.montant_max is None:
            return True
        return self.montant_min <= transaction.montant <= self.montant_max

    @property
    def specificite(self) -> tuple[int, int, int]:
        """Critère, puis condition de montant (exact > plage > aucune), puis longueur."""
        if self.montant_min is None:
            montant = 0
        else:
            montant = 2 if self.montant_min == self.montant_max else 1
        return (_RANG_CRITERE[self.critere], montant, len(self.valeur))

    @property
    def resultat(self) -> tuple[Nature | None, SousCategorie | None, str | None]:
        return (self.nature, self.sous_categorie, self.detail)

    def recouvre(self, autre: "Regle") -> bool:
        """Conditions de montant qui se recouvrent ; l'absence de condition recouvre tout."""
        if self.montant_min is None or autre.montant_min is None:
            return True
        assert self.montant_max is not None and autre.montant_max is not None
        return self.montant_min <= autre.montant_max and autre.montant_min <= self.montant_max

    def decrire(self) -> str:
        """Description sans libellé complet : type, source, critère, condition, résultat."""
        if self.montant_min is None or self.montant_max is None:
            condition = "sans condition de montant"
        elif self.montant_min == self.montant_max:
            condition = f"montant {formater_montant(self.montant_min)}"
        else:
            condition = (
                f"montant de {formater_montant(self.montant_min)} "
                f"à {formater_montant(self.montant_max)}"
            )
        cible = self.sous_categorie or self.detail or self.nature or "toujours demander"
        numero = f"règle n° {self.id}" if self.id is not None else "règle"
        return f"{numero} ({self.type}, {self.source}, {self.critere}, {condition}) -> {cible}"


class Decision(BaseModel):
    """Réponse à une question, rattachée à une seule transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str
    question_id: int | None = None  # None avant insertion
    nature: Nature
    sous_categorie: SousCategorie | None = None
    detail: str | None = None
    lien_id: str | None = None  # RG-07
    mois_paie: str | None = Field(default=None, pattern=MOTIF_MOIS)  # RG-04
    regle_id: int | None = None  # règle créée à partir de cette réponse


@dataclass(frozen=True)
class Memoire:
    """Décisions et règles apprises disponibles pour le classement d'un mois."""

    decisions: Mapping[str, Decision] = field(default_factory=dict)
    regles: Sequence[Regle] = ()
    exceptionnels: Mapping[str, bool] = field(default_factory=dict)  # RG-15 : réponses oui/non


def est_rg14(transaction: Transaction) -> bool:
    """RG-14 : PayPal et chèques déclenchent toujours une question."""
    type_operation = normaliser_libelle(transaction.type_operation or "")
    return transaction.ics == ICS_PAYPAL or type_operation in TYPES_CHEQUE


def regles_applicables(regles: Iterable[Regle], transaction: Transaction) -> list[Regle]:
    """Règles actives qui correspondent ; les règles marchand carte attendent l'étape 7."""
    return [
        r for r in regles if r.type is not TypeRegle.MARCHAND_CARTE and r.s_applique(transaction)
    ]


def toujours_demander(regles: Iterable[Regle], transaction: Transaction) -> bool:
    """RG-14, ou règle « toujours demander » applicable."""
    return est_rg14(transaction) or any(
        r.type is TypeRegle.TOUJOURS_DEMANDER for r in regles_applicables(regles, transaction)
    )


def regle_retenue(regles: Iterable[Regle], transaction: Transaction) -> Regle | None:
    """La règle la plus spécifique, ou `None` si aucune ou s'il faut toujours demander.

    Deux règles de même spécificité et de résultats différents arrêtent le classement : la base
    a été modifiée à la main.
    """
    if est_rg14(transaction):
        return None
    applicables = regles_applicables(regles, transaction)
    if not applicables or any(r.type is TypeRegle.TOUJOURS_DEMANDER for r in applicables):
        return None
    return _plus_specifique(applicables, transaction)


def regle_marchand_retenue(regles: Iterable[Regle], transaction: Transaction) -> Regle | None:
    """Règle `marchand_carte` la plus spécifique pour un paiement carte (même priorité)."""
    applicables = [
        r for r in regles if r.type is TypeRegle.MARCHAND_CARTE and r.s_applique(transaction)
    ]
    return _plus_specifique(applicables, transaction) if applicables else None


def _plus_specifique(applicables: Sequence[Regle], transaction: Transaction) -> Regle:
    meilleure = max(r.specificite for r in applicables)
    retenues = [r for r in applicables if r.specificite == meilleure]
    if len({r.resultat for r in retenues}) > 1:
        raise ControleError(
            f"règles {', '.join(f'n° {r.id}' for r in retenues)} : même spécificité et résultats "
            f"différents pour l'opération du {transaction.date_operation:%d/%m/%Y} "
            f"({formater_montant(transaction.montant)}, {transaction.fichier}, position "
            f"{transaction.position}) ; désactiver l'une d'elles"
        )
    return retenues[0]


def verifier_nouvelle_regle(nouvelle: Regle, existantes: Iterable[Regle]) -> None:
    """Refuse une règle en conflit avec une règle active, ou déjà couverte (cadrage §7)."""
    for existante in existantes:
        if not (
            existante.active
            and existante.type is nouvelle.type
            and existante.source is nouvelle.source
            and existante.critere is nouvelle.critere
            and existante.valeur == nouvelle.valeur
            and existante.specificite == nouvelle.specificite
            and existante.recouvre(nouvelle)
        ):
            continue
        if existante.resultat == nouvelle.resultat:
            raise RegleError(f"règle refusée : déjà couverte par la {existante.decrire()}")
        raise RegleError(
            f"règle refusée : conflit avec la {existante.decrire()} (même critère, même "
            "spécificité, montants qui se recouvrent, résultat différent)"
        )
