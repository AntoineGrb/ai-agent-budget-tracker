"""Réponses de référence aux 25 questions de juin 2026 sur les fixtures (spec 06).

Menus : virement sortant 1. Dotation 2. Épargne 3. Complément 4. Lien 5. Dépense 6. Hors budget ;
virement entrant 1. Dotation 2. Désépargne 3. Complément 4. Lien 5. Remboursement 6. Revenu
7. Hors budget ; opération sortante 1. Dépense 2. Hors budget ; opération entrante
1. Remboursement 2. Revenu 3. Hors budget. Catégories dans l'ordre de `Categorie` (4. Abonnements,
8. Achats, 9. Transport, 10. Santé), puis sous-catégories dans l'ordre de `SousCategorie`.

Spec 07 : propositions du catégoriseur rejouées par un `FunctionModel`, réponses du tableau de
validation en lot (bloc 2) et du tag exceptionnel (bloc 3).
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from budget.agents.categoriseur import CategoriseurLLM
from budget.config import Contexte
from budget.ledger import Ledger
from budget.questions.dialogue import InterlocuteurScripte
from budget.questions.execution import RapportClassement, classer_et_questionner

REPONSES_JUIN: list[list[str]] = [
    ["3", "n"],  # 01/06 -500,00 vers PRENOM2 : complément
    ["5", "4", "3", "o", ""],  # 01/06 -7,00 Spotifion : streaming, règle au montant exact
    ["6", "n"],  # 01/06 +2,90 Etsy (TR) : revenu
    ["7", "n"],  # 02/06 +100,00 (TR) : hors budget
    ["7", "n"],  # 03/06 +1 806,00 dépôt de garantie : hors budget (RG-10)
    ["6", "n"],  # 04/06 -50,00 : hors budget
    ["7", "n"],  # 05/06 +140,00 Revolut : hors budget
    ["5", "10", "3", "n"],  # 09/06 +77,90 IGESTION-BCAC (TR) : remboursement optique (RG-09)
    ["7", "n"],  # 14/06 +10,00 Wero : hors budget
    ["4", "2"],  # 15/06 -250,00 « Velo » (paire) : lien à DECATHLON 409,97 € (RG-07)
    ["7", "n"],  # 15/06 +250,00 de PRENOM2 : hors budget
    ["7", "n"],  # 26/06 +10,87 clôture BoursoBank : hors budget
    ["3", "n"],  # 26/06 -400,00 vers PRENOM2 : complément
    ["7", "n"],  # 26/06 +473,16 clôture BNPP : hors budget
    ["7", "n"],  # 29/06 +9,95 depuis BoursoBank : hors budget
    ["1", "2", "o", "p", "500-600"],  # 30/06 -550,00 « Virement du mois » : dotation perso_conjoint
    ["3"],  # 02/06 +585,00 remise de chèque : hors budget (RG-14, pas de règle)
    ["1", "4", "5", "n"],  # 03/06 +106,80 cotisation X9817 remboursée : frais bancaires
    ["1", "9", "2", "o", "", ""],  # 03/06 -90,80 Navigo (TR) : transports en commun, libellé exact
    ["1", "8", "4"],  # 04/06 -25,00 PayPal : achats divers (RG-14)
    ["1", "8", "4", "n"],  # 16/06 +81,00 avoir X9817 : remboursement achats divers
    ["1", "8", "4"],  # 16/06 -1,20 PayPal (RG-14)
    ["1", "8", "4"],  # 22/06 -4,80 PayPal (RG-14)
    ["1", "4", "5", "n"],  # 24/06 -144,00 cotisation carte : frais bancaires
    # 25/06 -11,99 Orange (TR) : internet et mobile, règle sur un préfixe saisi
    ["1", "4", "2", "o", "x", "Sepa Direct Debit transfer to ORANGE", ""],
]


# Bloc 2 de juin (65 lignes : 62 groupes, puis 3 Amazon RG-13 en 63 à 65). Catégories :
# 2. Enfants, 7. Loisirs, 8. Achats ; sous-catégories dans l'ordre de `SousCategorie`.
REPONSES_LOT_JUIN: list[str] = [
    "22", "7", "1",  # RELAY PARIS : correction Achats › Divers -> Loisirs › Sorties
    "63", "8", "3",  # AMAZON PAYMENTS 24/05 -218,68 € : Achats › High-tech (RG-13)
    "64", "8", "4",  # AMAZON EU SARL 25/05 -89,99 € : Achats › Divers (RG-13)
    "65", "2", "2",  # AMAZON PAYMENTS 10/06 -78,38 € : Enfants › Vêtements & équipement
    "",  # Entrée : tout valider
]  # fmt: skip
# Bloc 3 de juin : OLIVERSTORE -883,16 € du 01/06 marquée exceptionnelle (RG-15)
REPONSES_EXCEPTIONNELS_JUIN: list[str] = ["o"]


def reponses(nombre: int | None = None) -> list[str]:
    """Réponses à plat des `nombre` premières questions (toutes par défaut)."""
    return [r for question in REPONSES_JUIN[:nombre] for r in question]


# Propositions rejouées par le FunctionModel : clé marchand masquée -> code de sous-catégorie. Elles
# couvrent les 62 clés de juin et les 71 de juillet. Réponses de test sur les fixtures, pas des
# décisions sur les données réelles.
PROPOSITIONS: dict[str, str] = {
    # Cartes du compte joint
    "AIRBNB LUXEMBOURG": "loisirs.voyages",
    "ALICE FRUITS CHOISY LE ROI": "courses.commerces_bouche",
    "ATELIER PAPILLE LE HAVRE": "restos.restaurant",
    "AUCHAN SUP": "courses.supermarche",
    "BOOKING.COM AMSTERDAM": "loisirs.voyages",
    "BOUCHERIE JM SAINT MAUR DES F": "courses.commerces_bouche",
    "BOURSORAMA BOULOGNE BILLA": "abonnements.frais_bancaires",
    "BUCHHALTUNG BERLIN": "abonnements.logiciels_ia",
    "CAFE COCOTTE LE HAVRE": "restos.cafes_snacks",
    "CARLA'S COFFEE ST MAUR DES FOSS": "restos.cafes_snacks",
    "CARREFOUR CITY SAINT MAUR D": "courses.supermarche",
    "CARREFOUR.FR MONDEVILLE": "courses.supermarche",
    "CARREFOURCITY LE HAVRE": "courses.supermarche",
    "CERTAS": "transport.carburant",
    "DECATHLON CRETEI": "loisirs.sport",
    "DYNEFF VIRONVAY": "transport.carburant",
    "EG*ABRITEL": "loisirs.voyages",
    "EMINZA ANNECY LE VIEUX": "achats.vetements",
    "ESSO31816ROC1 ROSNY": "transport.carburant",
    "ETSY.COM*MRYTLESTREETS DUBLIN 1": "cadeaux.cadeaux",
    "EURL A.L.M. SAVIGNY": "achats.divers",
    "FLOWBIRD ALF SAINT OUEN SUR": "transport.parking_peages",
    "GUOFA 2 ST MAUR DES FOSS": "restos.restaurant",
    "H M HENNES MA PARIS": "achats.vetements",
    "HORODATEURS 76 LE HAVRE": "transport.parking_peages",
    "HOTEL AT BOOKING.COM AMSTERDAM": "loisirs.voyages",
    "IKEA CSC E COMM PLAISIR": "achats.maison_deco",
    "IKEA VILLIERS VILLIERS-SUR-MAR": "achats.maison_deco",
    "INTERM CALAO 77 94 SAINT MAUR DE": "courses.supermarche",
    "INTERMARCHE SAINT MAUR DES F": "courses.supermarche",
    "KIABI PSC CRETEIL": "enfants.vetements_equipement",
    "LA FRENCHIE BULLE CAEN": "restos.restaurant",
    "LA MARQUE EN MOINS NEUILLY-SUR-S": "achats.vetements",
    "LA POSTE.FR PARIS CEDEX 15": "achats.divers",
    "LA REDOUTE CART ROUBAIX": "achats.vetements",
    "LA VIE CLAIRE ST MAUR DES FOSS": "courses.commerces_bouche",
    "LEROY MERLIN FR RONCHIN": "logement.travaux_bricolage",
    "LES CUISINES DU LA VARENNE ST": "restos.restaurant",
    "LES GAULOIS ST SAINT-MAUR-DES-F": "restos.restaurant",
    "LIBERA": "restos.cafes_snacks",
    "MANOMANO PARIS": "logement.travaux_bricolage",
    "MIDAS CHAMPIGNY SUR MA": "transport.entretien_vehicule",
    "MILIBOO CHAVANOD": "achats.maison_deco",
    "MONOPRIX": "courses.supermarche",
    "MP*CARREFOUR DAC VL SAINT MAUR DES F": "courses.supermarche",
    "MP*CARREFOUR SAINT MAUR DES F": "courses.supermarche",
    "NETFLIX.COM AMSTERDAM": "abonnements.streaming_musique",
    "NICOLAS": "courses.commerces_bouche",
    "OKAIDI": "enfants.vetements_equipement",
    "OLIVERSTORE VILLENEUVE-D'": "achats.maison_deco",
    "OUISTREHAM HORO 14 OUISTREHAM": "transport.parking_peages",
    "PEAGE AUTOROUTE RUEIL MALMAISON": "transport.parking_peages",
    "PHARMA MURIERS ST MAUR": "sante.pharmacie",
    "PHARMACIE DES HA LE HAVRE": "sante.pharmacie",
    "PHARMACIE DU THE ST MAUR DES": "sante.pharmacie",
    "PHARMAKAIROS CAEN": "sante.pharmacie",
    "PICARD SA NEMOURS": "courses.supermarche",
    "PONT DE NDIE LE HAVRE CEDEX": "transport.parking_peages",
    "PT TANCARVILLE LE HAVRE CEDEX": "transport.parking_peages",
    "RBCPJ PARIS": "enfants.activites",
    "RELAY PARIS": "achats.divers",
    "RITUALSCOSMETICS CRETEIL": "cadeaux.cadeaux",
    "SAPN 92 ISSY LES MOUL": "transport.parking_peages",
    "SARL HBS LE HAVRE": "restos.restaurant",
    "SC.MIDAS CHAMPIGNY SUR MA": "transport.entretien_vehicule",
    "STELLA ST MAUR ST MAUR DES FOSS": "restos.restaurant",
    "SUMUP *MES 4 MOMENTS SAINT MAUR DE": "restos.cafes_snacks",
    "SUNNY INCH ST JEAN DE VEDAS": "achats.high_tech",
    "UBER *EATS HELP.UBER.C HELP.UBER.COM": "restos.livraison",
    "UBER *EATS HELP.UBER.COM": "restos.livraison",
    "UEP*OCEANE BIS LE HAVRE": "restos.restaurant",
    "UEP*SUPER U CAEN": "courses.supermarche",
    "UEP*SUPER U COLOMBELLES": "courses.supermarche",
    "VARENNE CAFE ST MAUR DES FOSS": "restos.cafes_snacks",
    # Trade Republic
    "ANTHROPIC": "abonnements.logiciels_ia",
    "ANTHROPIC* CLAUDE SUB": "abonnements.logiciels_ia",
    "ARPIBA": "restos.restaurant",
    "BK": "restos.restaurant",
    "BK BONNEUIL": "restos.restaurant",
    "BKG*BOOKING.COM HOTEL": "loisirs.voyages",
    "BOULANGERIE MURIER": "courses.commerces_bouche",
    "BRUSSELS FOODIES": "restos.restaurant",
    "CAFE D'ALBERT": "restos.cafes_snacks",
    "CANVA*": "abonnements.logiciels_ia",
    "CARREFOUR CITY": "courses.supermarche",
    "CONFORT": "achats.maison_deco",
    "CULTURA LA DEFEN": "loisirs.sorties",
    "DECATHLON": "loisirs.sport",
    "DECATHLON FRANCE SAS": "loisirs.sport",
    "E.LECLERC": "courses.supermarche",
    "EPSILOON MAGAZINE": "abonnements.streaming_musique",
    "ESSO31816ROC1": "transport.carburant",
    "ETSY": "cadeaux.cadeaux",
    "GOOGLE*GOOGLE ONE": "abonnements.logiciels_ia",
    "GOOGLE*GOOGLE PLAY APP": "loisirs.jeux_apps",
    "GOOGLE*YOUTUBE": "abonnements.streaming_musique",
    "GUOFA 2": "restos.restaurant",
    "INDIGO": "transport.parking_peages",
    "INDIGO NE": "transport.parking_peages",
    "IVS FRANCE": "restos.cafes_snacks",
    "JADE HAIR": "achats.divers",
    "KINEPOLIS BRUSSEL": "loisirs.sorties",
    "KINEPOLIS MEGA": "loisirs.sorties",
    "L ECLECTIQUE": "restos.restaurant",
    "LE MOULIN DE SURESNE": "restos.restaurant",
    "LES PETITS PLATS": "restos.restaurant",
    "LUNETTESPRTOUS": "sante.optique",
    "MC DONALD S SC": "restos.restaurant",
    "MONOP": "courses.supermarche",
    "MORAINVILLIERS": "transport.parking_peages",
    "NAVIGO ANNUEL": "transport.transports_commun",
    "NOTION LABS, INC.": "abonnements.logiciels_ia",
    "NYX*AIRSERVFRANCE": "restos.cafes_snacks",
    "NYX*CARONSERVICE": "restos.cafes_snacks",
    "PLAYSTATION": "loisirs.jeux_apps",
    "RDV COOKIES": "restos.cafes_snacks",
    "RELAY": "achats.divers",
    "S A S WRAI": "restos.restaurant",
    "SELECTA": "restos.cafes_snacks",
    "SHELL BOUTIQUE": "transport.carburant",
    "SNCF-VOYAGEURS": "transport.train_avion",
    "SP MAISON DE LA SOIE": "achats.vetements",
    "STARBUCKS": "restos.cafes_snacks",
    "TBM-VENTE LIGNE": "transport.transports_commun",
    "TEN DANCE MEDIA GMBH": "abonnements.streaming_musique",
    "UBER *EATS": "restos.livraison",
    "UBER *TRIP": "transport.transports_commun",
    "UBER* EATS": "restos.livraison",
    "VALINSOPAY NL": "achats.divers",
    "VARENNE CAFE": "restos.cafes_snacks",
    "WWW.FOODLES.CO": "restos.livraison",
    "WWW.STOPPUB.FR": "achats.divers",
    "ZALANDO PAYMENTS": "achats.vetements",
}


@dataclass
class AppelsCategoriseur:
    """Ce que le FunctionModel a reçu : ce qui serait parti au LLM."""

    prompts: list[str] = field(default_factory=list)
    messages: list[list[ModelMessage]] = field(default_factory=list)


def modele_propositions(
    appels: AppelsCategoriseur, propositions: dict[str, str] | None = None
) -> FunctionModel:
    """FunctionModel qui répond à chaque libellé du prompt d'après `PROPOSITIONS`."""
    table = PROPOSITIONS if propositions is None else propositions

    def repondre(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        appels.messages.append(messages)
        prompt = next(
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        appels.prompts.append(prompt)
        reponse = [
            {"libelle": libelle, "sous_categorie": table[libelle]}
            for libelle in prompt.splitlines()
            if libelle in table
        ]
        outil = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=outil, args={"response": reponse})])

    return FunctionModel(repondre)


def juin_complet(
    ledger: Ledger, contexte: Contexte, appels: AppelsCategoriseur, journal: Path | None = None
) -> RapportClassement:
    """Scénarios de référence de juin sur un ledger où juin est importé : blocs 1, 2 et 3."""
    interlocuteur = InterlocuteurScripte(
        reponses() + REPONSES_LOT_JUIN + REPONSES_EXCEPTIONNELS_JUIN
    )
    rapport = classer_et_questionner(
        "2026-06",
        ledger,
        contexte,
        interlocuteur,
        CategoriseurLLM(modele_propositions(appels)),
        journal,
    )
    assert interlocuteur.restantes == 0, "réponses du scénario non consommées"
    return rapport
