"""Réponses de référence sur les fixtures de juin et juillet 2026 (cadrage §11).

Menu d'imputation : catégories dans l'ordre de `Categorie` (1. Logement, 2. Courses, 3. Restos,
4. Loisirs, 5. Achats, 6. Enfants, 7. Transport, 8. Abonnements, 9. Santé, 10. Divers), puis
11. Non compté ; ensuite la sous-catégorie, dans l'ordre de `SousCategorie`. Après une réponse :
« En faire une règle ? » (o, n, t), puis le critère et la condition de montant.

Les propositions du catégoriseur sont rejouées par un `FunctionModel` : ce sont des réponses de
test sur les fixtures, pas des décisions sur les données réelles.
"""

from dataclasses import dataclass, field

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

NON_COMPTE = "11"

# Bloc 1 de juin : 26 questions, dont 3 Amazon (RG-13) et 2 couvertes par une règle créée plus tôt
QUESTIONS_JUIN: list[list[str]] = [
    ["5", "3"],  # 24/05 -218,68 Amazon : Achats › Tech (RG-13, pas de règle)
    ["5", "6"],  # 25/05 -89,99 Amazon : Achats › Autre (RG-13)
    [NON_COMPTE, "o", "p", "1-1000"],  # 01/06 -500,00 vers PRENOM2 : règle de 1 à 1 000 €
    ["8", "2", "o", ""],  # 01/06 -7,00 Spotifion : Abonnements › Streaming, montant exact
    ["6", "1", "o", ""],  # 02/06 -668,24 Pajemploi : Enfants › Garde/scolaire, règle sur l'ICS
    # 03/06 -67,48 assurance emprunteur : Logement › Prêt, règle sur un préfixe saisi
    ["1", "1", "o", "x", "ASSU. CAAE PRET HABITAT", ""],
    ["7", "2", "o", "", ""],  # 03/06 -90,80 Navigo (TR) : Transports en commun, libellé exact
    [NON_COMPTE, "n"],  # 04/06 -50,00 virement : non compté, sans règle
    ["1", "3", "o", ""],  # 04/06 -68,92 Octopus Energy : Logement › Énergie, ICS
    ["5", "6"],  # 04/06 -25,00 PayPal : Achats › Autre (RG-14, pas de règle)
    ["8", "1", "o", ""],  # 05/06 -23,99 Free : Abonnements › Internet, ICS
    # 05/06 -1 787,46 prêt : Logement › Prêt, préfixe ; couvre aussi le prêt de 121,89 €
    ["1", "1", "o", "x", "XXXXXXXXXXX ECHEANCE", ""],
    ["6", "3"],  # 10/06 -78,38 Amazon : Enfants › Autre (RG-13)
    ["8", "4", "o", "", ""],  # 12/06 -15,50 Offre Premium : Abonnements › Banque
    [NON_COMPTE, "n"],  # 15/06 -250,00 « Velo » : le vélo est compté sur TR, où il est payé
    ["5", "6"],  # 16/06 -1,20 PayPal (RG-14)
    ["4", "4"],  # 22/06 -4,80 PayPal : Loisirs › Autre (RG-14)
    ["1", "2", "o", ""],  # 24/06 -1 071,04 copropriété : Logement › Charges & assurances, ICS
    ["8", "4", "n"],  # 24/06 -144,00 cotisation carte : Abonnements › Banque, sans règle
    # 25/06 -11,99 Orange (TR) : Abonnements › Internet, préfixe saisi
    ["8", "1", "o", "x", "Sepa Direct Debit transfer to ORANGE", ""],
    # 26/06 -400,00 vers PRENOM2 : couverte par la règle du 01/06, pas de question
    [NON_COMPTE, "o", ""],  # 29/06 -300,00 vers la provision : non compté, montant exact
    [NON_COMPTE, "o", ""],  # 29/06 -500,00 « Mensuel » vers TR : non compté, montant exact
    ["1", "2", "o", ""],  # 29/06 -43,77 assurance habitation : Charges & assurances, ICS
    [NON_COMPTE, "o", "p", "500-600"],  # 30/06 -550,00 « Virement du mois » : plage
]

# Bloc 2 de juin : 62 groupes marchands
LOT_JUIN: list[str] = [
    "20", NON_COMPTE,  # BOURSORAMA BOULOGNE BILLA : non compté
    "22", "4", "1",  # RELAY PARIS : Achats › Autre corrigé en Loisirs › Sorties
    "",  # Entrée : tout valider
]  # fmt: skip

# Bloc 1 de juillet : 13 questions, dont 2 Amazon ; prélèvements, prêts, Spotifion et Orange sont
# classés par les règles de juin
QUESTIONS_JUILLET: list[list[str]] = [
    ["5", "2"],  # 27/06 -57,80 Amazon : Achats › Maison (RG-13)
    ["10", "2", "n"],  # 02/07 -300,00 retrait : Divers › Autre, sans règle
    ["10", "2", "n"],  # 02/07 -300,00 second retrait
    ["7", "1", "o", ""],  # 07/07 -33,13 Direct Assurance : Transport › Voiture, ICS
    ["5", "6"],  # 15/07 -1,20 PayPal (RG-14)
    ["10", "2"],  # 16/07 -18,45 chèque émis (RG-14)
    ["1", "4", "n"],  # 20/07 -2 659,16 acompte climatisation : Logement › Autre
    ["4", "4"],  # 21/07 -4,80 PayPal (RG-14)
    [NON_COMPTE, "n"],  # 24/07 -1 000,00 vers PRENOM : non compté
    [NON_COMPTE, "n"],  # 24/07 -1 500,00 « Impots » vers la provision : non compté
    ["5", "6"],  # 25/07 -51,98 Amazon (TR) : Achats › Autre (RG-13)
    [NON_COMPTE, "o", ""],  # 28/07 -300,00 « Charges persos » : non compté, montant exact
    [NON_COMPTE, "n"],  # 28/07 -250,00 « Virement pour charges » : non compté
]

# Bloc 2 de juillet : 71 groupes marchands
LOT_JUILLET: list[str] = [""]  # Entrée : tout valider

REPONSES: dict[str, list[str]] = {
    "2026-06": [r for q in QUESTIONS_JUIN for r in q] + LOT_JUIN,
    "2026-07": [r for q in QUESTIONS_JUILLET for r in q] + LOT_JUILLET,
}

PROPOSITIONS: dict[str, str] = {
    # Cartes du compte joint
    "AIRBNB LUXEMBOURG": "loisirs.vacances",
    "ALICE FRUITS CHOISY LE ROI": "courses.commerces",
    "ATELIER PAPILLE LE HAVRE": "restos.restaurant",
    "AUCHAN SUP": "courses.supermarche",
    "BOOKING.COM AMSTERDAM": "loisirs.vacances",
    "BOUCHERIE JM SAINT MAUR DES F": "courses.commerces",
    "BOURSORAMA BOULOGNE BILLA": "abonnements.banque",
    "BUCHHALTUNG BERLIN": "abonnements.logiciels",
    "CAFE COCOTTE LE HAVRE": "restos.restaurant",
    "CARLA'S COFFEE ST MAUR DES FOSS": "restos.restaurant",
    "CARREFOUR CITY SAINT MAUR D": "courses.supermarche",
    "CARREFOUR.FR MONDEVILLE": "courses.supermarche",
    "CARREFOURCITY LE HAVRE": "courses.supermarche",
    "CERTAS": "transport.voiture",
    "DECATHLON CRETEI": "loisirs.sport",
    "DYNEFF VIRONVAY": "transport.voiture",
    "EG*ABRITEL": "loisirs.vacances",
    "EMINZA ANNECY LE VIEUX": "achats.vetements",
    "ESSO31816ROC1 ROSNY": "transport.voiture",
    "ETSY.COM*MRYTLESTREETS DUBLIN 1": "achats.cadeaux",
    "EURL A.L.M. SAVIGNY": "achats.autre",
    "FLOWBIRD ALF SAINT OUEN SUR": "transport.voiture",
    "GUOFA 2 ST MAUR DES FOSS": "restos.restaurant",
    "H M HENNES MA PARIS": "achats.vetements",
    "HORODATEURS 76 LE HAVRE": "transport.voiture",
    "HOTEL AT BOOKING.COM AMSTERDAM": "loisirs.vacances",
    "IKEA CSC E COMM PLAISIR": "achats.maison",
    "IKEA VILLIERS VILLIERS-SUR-MAR": "achats.maison",
    "INTERM CALAO 77 94 SAINT MAUR DE": "courses.supermarche",
    "INTERMARCHE SAINT MAUR DES F": "courses.supermarche",
    "KIABI PSC CRETEIL": "enfants.autre",
    "LA FRENCHIE BULLE CAEN": "restos.restaurant",
    "LA MARQUE EN MOINS NEUILLY-SUR-S": "achats.vetements",
    "LA POSTE.FR PARIS CEDEX 15": "achats.autre",
    "LA REDOUTE CART ROUBAIX": "achats.vetements",
    "LA VIE CLAIRE ST MAUR DES FOSS": "courses.commerces",
    "LEROY MERLIN FR RONCHIN": "logement.autre",
    "LES CUISINES DU LA VARENNE ST": "restos.restaurant",
    "LES GAULOIS ST SAINT-MAUR-DES-F": "restos.restaurant",
    "LIBERA": "restos.restaurant",
    "MANOMANO PARIS": "logement.autre",
    "MIDAS CHAMPIGNY SUR MA": "transport.voiture",
    "MILIBOO CHAVANOD": "achats.maison",
    "MONOPRIX": "courses.supermarche",
    "MP*CARREFOUR DAC VL SAINT MAUR DES F": "courses.supermarche",
    "MP*CARREFOUR SAINT MAUR DES F": "courses.supermarche",
    "NETFLIX.COM AMSTERDAM": "abonnements.streaming",
    "NICOLAS": "courses.commerces",
    "OKAIDI": "enfants.autre",
    "OLIVERSTORE VILLENEUVE-D'": "achats.maison",
    "OUISTREHAM HORO 14 OUISTREHAM": "transport.voiture",
    "PEAGE AUTOROUTE RUEIL MALMAISON": "transport.voiture",
    "PHARMA MURIERS ST MAUR": "sante.pharmacie",
    "PHARMACIE DES HA LE HAVRE": "sante.pharmacie",
    "PHARMACIE DU THE ST MAUR DES": "sante.pharmacie",
    "PHARMAKAIROS CAEN": "sante.pharmacie",
    "PICARD SA NEMOURS": "courses.supermarche",
    "PONT DE NDIE LE HAVRE CEDEX": "transport.voiture",
    "PT TANCARVILLE LE HAVRE CEDEX": "transport.voiture",
    "RBCPJ PARIS": "enfants.activites",
    "RELAY PARIS": "achats.autre",
    "RITUALSCOSMETICS CRETEIL": "achats.cadeaux",
    "SAPN 92 ISSY LES MOUL": "transport.voiture",
    "SARL HBS LE HAVRE": "restos.restaurant",
    "SC.MIDAS CHAMPIGNY SUR MA": "transport.voiture",
    "STELLA ST MAUR ST MAUR DES FOSS": "restos.restaurant",
    "SUMUP *MES 4 MOMENTS SAINT MAUR DE": "restos.restaurant",
    "SUNNY INCH ST JEAN DE VEDAS": "achats.tech",
    "UBER *EATS HELP.UBER.C HELP.UBER.COM": "restos.livraison",
    "UBER *EATS HELP.UBER.COM": "restos.livraison",
    "UEP*OCEANE BIS LE HAVRE": "restos.restaurant",
    "UEP*SUPER U CAEN": "courses.supermarche",
    "UEP*SUPER U COLOMBELLES": "courses.supermarche",
    "VARENNE CAFE ST MAUR DES FOSS": "restos.restaurant",
    # Trade Republic
    "ANTHROPIC": "abonnements.logiciels",
    "ANTHROPIC* CLAUDE SUB": "abonnements.logiciels",
    "ARPIBA": "restos.restaurant",
    "BK": "restos.restaurant",
    "BK BONNEUIL": "restos.restaurant",
    "BKG*BOOKING.COM HOTEL": "loisirs.vacances",
    "BOULANGERIE MURIER": "courses.commerces",
    "BRUSSELS FOODIES": "restos.restaurant",
    "CAFE D'ALBERT": "restos.restaurant",
    "CANVA*": "abonnements.logiciels",
    "CARREFOUR CITY": "courses.supermarche",
    "CONFORT": "achats.maison",
    "CULTURA LA DEFEN": "loisirs.sorties",
    "DECATHLON": "loisirs.sport",
    "DECATHLON FRANCE SAS": "loisirs.sport",
    "E.LECLERC": "courses.supermarche",
    "EPSILOON MAGAZINE": "abonnements.streaming",
    "ESSO31816ROC1": "transport.voiture",
    "ETSY": "achats.cadeaux",
    "GOOGLE*GOOGLE ONE": "abonnements.logiciels",
    "GOOGLE*GOOGLE PLAY APP": "loisirs.autre",
    "GOOGLE*YOUTUBE": "abonnements.streaming",
    "GUOFA 2": "restos.restaurant",
    "INDIGO": "transport.voiture",
    "INDIGO NE": "transport.voiture",
    "IVS FRANCE": "restos.restaurant",
    "JADE HAIR": "achats.autre",
    "KINEPOLIS BRUSSEL": "loisirs.sorties",
    "KINEPOLIS MEGA": "loisirs.sorties",
    "L ECLECTIQUE": "restos.restaurant",
    "LE MOULIN DE SURESNE": "restos.restaurant",
    "LES PETITS PLATS": "restos.restaurant",
    "LUNETTESPRTOUS": "sante.autre",
    "MC DONALD S SC": "restos.restaurant",
    "MONOP": "courses.supermarche",
    "MORAINVILLIERS": "transport.voiture",
    "NAVIGO ANNUEL": "transport.transports_commun",
    "NOTION LABS, INC.": "abonnements.logiciels",
    "NYX*AIRSERVFRANCE": "restos.restaurant",
    "NYX*CARONSERVICE": "restos.restaurant",
    "PLAYSTATION": "loisirs.autre",
    "RDV COOKIES": "restos.restaurant",
    "RELAY": "achats.autre",
    "S A S WRAI": "restos.restaurant",
    "SELECTA": "restos.restaurant",
    "SHELL BOUTIQUE": "transport.voiture",
    "SNCF-VOYAGEURS": "transport.train_avion",
    "SP MAISON DE LA SOIE": "achats.vetements",
    "STARBUCKS": "restos.restaurant",
    "TBM-VENTE LIGNE": "transport.transports_commun",
    "TEN DANCE MEDIA GMBH": "abonnements.streaming",
    "UBER *EATS": "restos.livraison",
    "UBER *TRIP": "transport.autre",
    "UBER* EATS": "restos.livraison",
    "VALINSOPAY NL": "achats.autre",
    "VARENNE CAFE": "restos.restaurant",
    "WWW.FOODLES.CO": "restos.livraison",
    "WWW.STOPPUB.FR": "achats.autre",
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


def derouler(
    mois: str, ledger: Ledger, contexte: Contexte, appels: AppelsCategoriseur
) -> RapportClassement:
    """Rejoue les réponses de référence du mois : questions, puis validation en lot."""
    interlocuteur = InterlocuteurScripte(REPONSES[mois])
    rapport = classer_et_questionner(
        mois, ledger, contexte, interlocuteur, CategoriseurLLM(modele_propositions(appels))
    )
    assert interlocuteur.restantes == 0, "réponses du scénario non consommées"
    return rapport
