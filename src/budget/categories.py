"""Taxonomie fermée des dépenses à deux niveaux (cadrage §4).

Une seule énumération de sous-catégories à plat : la catégorie parente se déduit du préfixe, ce
qui rend impossible un couple catégorie/sous-catégorie incohérent.
"""

from enum import StrEnum


class NatureDepense(StrEnum):
    FIXE = "fixe"
    PILOTABLE = "pilotable"


class Categorie(StrEnum):
    LOGEMENT = "logement"
    ENFANTS = "enfants"
    IMPOTS = "impots"
    ABONNEMENTS = "abonnements"
    COURSES = "courses"
    RESTOS = "restos"
    LOISIRS = "loisirs"
    ACHATS = "achats"
    TRANSPORT = "transport"
    SANTE = "sante"
    CADEAUX = "cadeaux"

    @property
    def libelle(self) -> str:
        return _LIBELLES_CATEGORIES[self]


class SousCategorie(StrEnum):
    LOGEMENT_PRET = "logement.pret"
    LOGEMENT_ASSURANCE_EMPRUNTEUR = "logement.assurance_emprunteur"
    LOGEMENT_COPROPRIETE = "logement.copropriete"
    LOGEMENT_ASSURANCE_HABITATION = "logement.assurance_habitation"
    LOGEMENT_TRAVAUX_BRICOLAGE = "logement.travaux_bricolage"
    ENFANTS_GARDE = "enfants.garde"
    ENFANTS_VETEMENTS_EQUIPEMENT = "enfants.vetements_equipement"
    ENFANTS_ACTIVITES = "enfants.activites"
    IMPOTS_IMPOT_REVENU = "impots.impot_revenu"
    IMPOTS_TAXE_FONCIERE = "impots.taxe_fonciere"
    ABONNEMENTS_ENERGIE = "abonnements.energie"
    ABONNEMENTS_INTERNET_MOBILE = "abonnements.internet_mobile"
    ABONNEMENTS_STREAMING_MUSIQUE = "abonnements.streaming_musique"
    ABONNEMENTS_LOGICIELS_IA = "abonnements.logiciels_ia"
    ABONNEMENTS_FRAIS_BANCAIRES = "abonnements.frais_bancaires"
    COURSES_SUPERMARCHE = "courses.supermarche"
    COURSES_COMMERCES_BOUCHE = "courses.commerces_bouche"
    RESTOS_RESTAURANT = "restos.restaurant"
    RESTOS_LIVRAISON = "restos.livraison"
    RESTOS_CAFES_SNACKS = "restos.cafes_snacks"
    LOISIRS_SORTIES = "loisirs.sorties"
    LOISIRS_SPORT = "loisirs.sport"
    LOISIRS_JEUX_APPS = "loisirs.jeux_apps"
    LOISIRS_VOYAGES = "loisirs.voyages"
    ACHATS_VETEMENTS = "achats.vetements"
    ACHATS_MAISON_DECO = "achats.maison_deco"
    ACHATS_HIGH_TECH = "achats.high_tech"
    ACHATS_DIVERS = "achats.divers"
    TRANSPORT_CARBURANT = "transport.carburant"
    TRANSPORT_TRANSPORTS_COMMUN = "transport.transports_commun"
    TRANSPORT_PARKING_PEAGES = "transport.parking_peages"
    TRANSPORT_ENTRETIEN_VEHICULE = "transport.entretien_vehicule"
    TRANSPORT_TRAIN_AVION = "transport.train_avion"
    SANTE_PHARMACIE = "sante.pharmacie"
    SANTE_CONSULTATIONS = "sante.consultations"
    SANTE_OPTIQUE = "sante.optique"
    CADEAUX_CADEAUX = "cadeaux.cadeaux"
    CADEAUX_DONS = "cadeaux.dons"


_LIBELLES_CATEGORIES: dict[Categorie, str] = {
    Categorie.LOGEMENT: "Logement",
    Categorie.ENFANTS: "Enfants",
    Categorie.IMPOTS: "Impôts",
    Categorie.ABONNEMENTS: "Abonnements",
    Categorie.COURSES: "Courses",
    Categorie.RESTOS: "Restos & commandes",
    Categorie.LOISIRS: "Loisirs",
    Categorie.ACHATS: "Achats",
    Categorie.TRANSPORT: "Transport",
    Categorie.SANTE: "Santé",
    Categorie.CADEAUX: "Cadeaux & dons",
}

_FIXE = NatureDepense.FIXE
_PILOTABLE = NatureDepense.PILOTABLE

# Répartition Fixe/Pilotable des catégories « Mixte » du cadrage : proposition centralisée ici
# pour pouvoir être ajustée sans toucher au reste du code.
_METADONNEES: dict[SousCategorie, tuple[NatureDepense, str]] = {
    SousCategorie.LOGEMENT_PRET: (_FIXE, "Prêt"),
    SousCategorie.LOGEMENT_ASSURANCE_EMPRUNTEUR: (_FIXE, "Assurance emprunteur"),
    SousCategorie.LOGEMENT_COPROPRIETE: (_FIXE, "Charges copropriété"),
    SousCategorie.LOGEMENT_ASSURANCE_HABITATION: (_FIXE, "Assurance habitation"),
    SousCategorie.LOGEMENT_TRAVAUX_BRICOLAGE: (_PILOTABLE, "Travaux & bricolage"),
    SousCategorie.ENFANTS_GARDE: (_FIXE, "Garde"),
    SousCategorie.ENFANTS_VETEMENTS_EQUIPEMENT: (_PILOTABLE, "Vêtements & équipement"),
    SousCategorie.ENFANTS_ACTIVITES: (_PILOTABLE, "Activités"),
    SousCategorie.IMPOTS_IMPOT_REVENU: (_FIXE, "Impôt sur le revenu"),
    SousCategorie.IMPOTS_TAXE_FONCIERE: (_FIXE, "Taxe foncière"),
    SousCategorie.ABONNEMENTS_ENERGIE: (_FIXE, "Énergie"),
    SousCategorie.ABONNEMENTS_INTERNET_MOBILE: (_FIXE, "Internet & mobile"),
    SousCategorie.ABONNEMENTS_STREAMING_MUSIQUE: (_FIXE, "Streaming & musique"),
    SousCategorie.ABONNEMENTS_LOGICIELS_IA: (_FIXE, "Logiciels & IA"),
    SousCategorie.ABONNEMENTS_FRAIS_BANCAIRES: (_FIXE, "Frais bancaires"),
    SousCategorie.COURSES_SUPERMARCHE: (_PILOTABLE, "Supermarché"),
    SousCategorie.COURSES_COMMERCES_BOUCHE: (_PILOTABLE, "Commerces de bouche"),
    SousCategorie.RESTOS_RESTAURANT: (_PILOTABLE, "Restaurant"),
    SousCategorie.RESTOS_LIVRAISON: (_PILOTABLE, "Livraison"),
    SousCategorie.RESTOS_CAFES_SNACKS: (_PILOTABLE, "Cafés & snacks"),
    SousCategorie.LOISIRS_SORTIES: (_PILOTABLE, "Sorties"),
    SousCategorie.LOISIRS_SPORT: (_PILOTABLE, "Sport"),
    SousCategorie.LOISIRS_JEUX_APPS: (_PILOTABLE, "Jeux & apps"),
    SousCategorie.LOISIRS_VOYAGES: (_PILOTABLE, "Voyages"),
    SousCategorie.ACHATS_VETEMENTS: (_PILOTABLE, "Vêtements"),
    SousCategorie.ACHATS_MAISON_DECO: (_PILOTABLE, "Maison & déco"),
    SousCategorie.ACHATS_HIGH_TECH: (_PILOTABLE, "High-tech"),
    SousCategorie.ACHATS_DIVERS: (_PILOTABLE, "Divers"),
    SousCategorie.TRANSPORT_CARBURANT: (_PILOTABLE, "Carburant"),
    SousCategorie.TRANSPORT_TRANSPORTS_COMMUN: (_FIXE, "Transports en commun"),
    SousCategorie.TRANSPORT_PARKING_PEAGES: (_PILOTABLE, "Parking & péages"),
    SousCategorie.TRANSPORT_ENTRETIEN_VEHICULE: (_PILOTABLE, "Entretien véhicule"),
    SousCategorie.TRANSPORT_TRAIN_AVION: (_PILOTABLE, "Train & avion"),
    SousCategorie.SANTE_PHARMACIE: (_PILOTABLE, "Pharmacie"),
    SousCategorie.SANTE_CONSULTATIONS: (_PILOTABLE, "Consultations"),
    SousCategorie.SANTE_OPTIQUE: (_PILOTABLE, "Optique"),
    SousCategorie.CADEAUX_CADEAUX: (_PILOTABLE, "Cadeaux"),
    SousCategorie.CADEAUX_DONS: (_PILOTABLE, "Dons"),
}


def categorie_de(sc: SousCategorie) -> Categorie:
    """Catégorie parente, déduite du préfixe de la sous-catégorie."""
    return Categorie(sc.value.split(".", 1)[0])


def nature_de(sc: SousCategorie) -> NatureDepense:
    """Nature Fixe ou Pilotable de la sous-catégorie."""
    return _METADONNEES[sc][0]


def libelle_de(sc: SousCategorie) -> str:
    """Libellé d'affichage de la sous-catégorie."""
    return _METADONNEES[sc][1]
