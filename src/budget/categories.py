"""Taxonomie fermée des dépenses à deux niveaux, et imputation d'une opération (cadrage §4).

Une seule énumération de sous-catégories à plat : la catégorie parente se déduit du préfixe, ce
qui rend impossible un couple catégorie/sous-catégorie incohérent. Chaque catégorie a son « Autre ».
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

NON_COMPTE = "Non compté"


class Categorie(StrEnum):
    LOGEMENT = "logement"
    COURSES = "courses"
    RESTOS = "restos"
    LOISIRS = "loisirs"
    ACHATS = "achats"
    ENFANTS = "enfants"
    TRANSPORT = "transport"
    ABONNEMENTS = "abonnements"
    SANTE = "sante"
    DIVERS = "divers"

    @property
    def libelle(self) -> str:
        return _LIBELLES_CATEGORIES[self]


class SousCategorie(StrEnum):
    LOGEMENT_PRET = "logement.pret"
    LOGEMENT_CHARGES_ASSURANCES = "logement.charges_assurances"
    LOGEMENT_ENERGIE = "logement.energie"
    LOGEMENT_AUTRE = "logement.autre"
    COURSES_SUPERMARCHE = "courses.supermarche"
    COURSES_COMMERCES = "courses.commerces"
    COURSES_AUTRE = "courses.autre"
    RESTOS_RESTAURANT = "restos.restaurant"
    RESTOS_LIVRAISON = "restos.livraison"
    RESTOS_AUTRE = "restos.autre"
    LOISIRS_SORTIES = "loisirs.sorties"
    LOISIRS_SPORT = "loisirs.sport"
    LOISIRS_VACANCES = "loisirs.vacances"
    LOISIRS_AUTRE = "loisirs.autre"
    ACHATS_VETEMENTS = "achats.vetements"
    ACHATS_MAISON = "achats.maison"
    ACHATS_TECH = "achats.tech"
    ACHATS_CADEAUX = "achats.cadeaux"
    ACHATS_AUTRE = "achats.autre"
    ENFANTS_GARDE_SCOLAIRE = "enfants.garde_scolaire"
    ENFANTS_ACTIVITES = "enfants.activites"
    ENFANTS_AUTRE = "enfants.autre"
    TRANSPORT_VOITURE = "transport.voiture"
    TRANSPORT_TRANSPORTS_COMMUN = "transport.transports_commun"
    TRANSPORT_TRAIN_AVION = "transport.train_avion"
    TRANSPORT_AUTRE = "transport.autre"
    ABONNEMENTS_INTERNET = "abonnements.internet"
    ABONNEMENTS_STREAMING = "abonnements.streaming"
    ABONNEMENTS_LOGICIELS = "abonnements.logiciels"
    ABONNEMENTS_BANQUE = "abonnements.banque"
    ABONNEMENTS_AUTRE = "abonnements.autre"
    SANTE_PHARMACIE = "sante.pharmacie"
    SANTE_MEDECINS = "sante.medecins"
    SANTE_AUTRE = "sante.autre"
    DIVERS_IMPOTS = "divers.impots"
    DIVERS_AUTRE = "divers.autre"


_LIBELLES_CATEGORIES: dict[Categorie, str] = {
    Categorie.LOGEMENT: "Logement",
    Categorie.COURSES: "Courses",
    Categorie.RESTOS: "Restos",
    Categorie.LOISIRS: "Loisirs",
    Categorie.ACHATS: "Achats",
    Categorie.ENFANTS: "Enfants",
    Categorie.TRANSPORT: "Transport",
    Categorie.ABONNEMENTS: "Abonnements",
    Categorie.SANTE: "Santé",
    Categorie.DIVERS: "Divers",
}

_SC = SousCategorie

# Libellé d'affichage et exemples, repris dans les instructions du catégoriseur
_METADONNEES: dict[SousCategorie, tuple[str, str]] = {
    _SC.LOGEMENT_PRET: ("Prêt", "mensualités, assurance emprunteur"),
    _SC.LOGEMENT_CHARGES_ASSURANCES: ("Charges & assurances", "copropriété, assurance habitation"),
    _SC.LOGEMENT_ENERGIE: ("Énergie", "électricité, gaz, eau"),
    _SC.LOGEMENT_AUTRE: ("Autre", "travaux, bricolage"),
    _SC.COURSES_SUPERMARCHE: ("Supermarché", "grandes surfaces, supérettes, surgelés"),
    _SC.COURSES_COMMERCES: ("Commerces", "boulangerie, boucherie, primeur, caviste"),
    _SC.COURSES_AUTRE: ("Autre", "autres courses alimentaires"),
    _SC.RESTOS_RESTAURANT: ("Restaurant", "restaurants, cafés, snacks, fast-food"),
    _SC.RESTOS_LIVRAISON: ("Livraison", "repas livrés"),
    _SC.RESTOS_AUTRE: ("Autre", "distributeurs, cantines d'entreprise"),
    _SC.LOISIRS_SORTIES: ("Sorties", "cinéma, musées, spectacles, librairie"),
    _SC.LOISIRS_SPORT: ("Sport", "articles de sport, clubs, salles"),
    _SC.LOISIRS_VACANCES: ("Vacances", "hébergement, locations de vacances"),
    _SC.LOISIRS_AUTRE: ("Autre", "jeux, applications"),
    _SC.ACHATS_VETEMENTS: ("Vêtements", "vêtements, chaussures"),
    _SC.ACHATS_MAISON: ("Maison", "meubles, déco, équipement de la maison"),
    _SC.ACHATS_TECH: ("Tech", "informatique, téléphonie, électronique"),
    _SC.ACHATS_CADEAUX: ("Cadeaux", "cadeaux offerts, cosmétiques, fleurs"),
    _SC.ACHATS_AUTRE: ("Autre", "achats divers, coiffeur, presse"),
    _SC.ENFANTS_GARDE_SCOLAIRE: ("Garde/scolaire", "crèche, nounou, cantine, école"),
    _SC.ENFANTS_ACTIVITES: ("Activités", "activités et loisirs des enfants"),
    _SC.ENFANTS_AUTRE: ("Autre", "vêtements, équipement, jouets"),
    _SC.TRANSPORT_VOITURE: ("Voiture", "carburant, parking, péages, entretien, assurance auto"),
    _SC.TRANSPORT_TRANSPORTS_COMMUN: ("Transports en commun", "métro, bus, tram, abonnement"),
    _SC.TRANSPORT_TRAIN_AVION: ("Train/avion", "billets de train et d'avion"),
    _SC.TRANSPORT_AUTRE: ("Autre", "taxi, VTC, vélo"),
    _SC.ABONNEMENTS_INTERNET: ("Internet", "box, forfait mobile"),
    _SC.ABONNEMENTS_STREAMING: ("Streaming", "musique, vidéo, presse en ligne"),
    _SC.ABONNEMENTS_LOGICIELS: ("Logiciels", "logiciels, IA, stockage en ligne"),
    _SC.ABONNEMENTS_BANQUE: ("Banque", "offre bancaire, cotisation carte, frais"),
    _SC.ABONNEMENTS_AUTRE: ("Autre", "autres abonnements"),
    _SC.SANTE_PHARMACIE: ("Pharmacie", "pharmacie, parapharmacie"),
    _SC.SANTE_MEDECINS: ("Médecins", "consultations, analyses"),
    _SC.SANTE_AUTRE: ("Autre", "optique, dentaire"),
    _SC.DIVERS_IMPOTS: ("Impôts", "impôt sur le revenu, taxe foncière"),
    _SC.DIVERS_AUTRE: ("Autre", "tout le reste"),
}


def categorie_de(sc: SousCategorie) -> Categorie:
    """Catégorie parente, déduite du préfixe de la sous-catégorie."""
    return Categorie(sc.value.split(".", 1)[0])


def sous_categories_de(categorie: Categorie) -> list[SousCategorie]:
    """Sous-catégories d'une catégorie, dans l'ordre de l'énumération (« Autre » en dernier)."""
    return [sc for sc in SousCategorie if categorie_de(sc) is categorie]


def libelle_de(sc: SousCategorie) -> str:
    """Libellé d'affichage de la sous-catégorie."""
    return _METADONNEES[sc][0]


def exemples_de(sc: SousCategorie) -> str:
    """Exemples de dépenses de la sous-catégorie."""
    return _METADONNEES[sc][1]


def libelle_complet(sc: SousCategorie) -> str:
    """`Achats › Maison`."""
    return f"{categorie_de(sc).libelle} › {libelle_de(sc)}"


class Imputation(BaseModel):
    """Sous-catégorie où une opération est comptée, ou « non compté » (RG-10), jamais les deux.

    Ni l'un ni l'autre : l'opération attend une question ou la validation en lot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sous_categorie: SousCategorie | None = None
    non_comptee: bool = False

    @model_validator(mode="after")
    def _exclusives(self) -> Self:
        if self.non_comptee and self.sous_categorie is not None:
            raise ValueError("une opération non comptée n'a pas de sous-catégorie (RG-10)")
        return self

    @property
    def decidee(self) -> bool:
        return self.non_comptee or self.sous_categorie is not None

    def libelle_imputation(self) -> str:
        """`Courses › Supermarché`, `Non compté`, ou vide si l'opération est en attente."""
        if self.non_comptee:
            return NON_COMPTE
        return libelle_complet(self.sous_categorie) if self.sous_categorie is not None else ""
