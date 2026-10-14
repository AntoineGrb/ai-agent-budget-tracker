"""Réponses de référence aux 25 questions de juin 2026 sur les fixtures (spec 06).

Menus : virement sortant 1. Dotation 2. Épargne 3. Complément 4. Lien 5. Dépense 6. Hors budget ;
virement entrant 1. Dotation 2. Désépargne 3. Complément 4. Lien 5. Remboursement 6. Revenu
7. Hors budget ; opération sortante 1. Dépense 2. Hors budget ; opération entrante
1. Remboursement 2. Revenu 3. Hors budget. Catégories dans l'ordre de `Categorie` (4. Abonnements,
8. Achats, 9. Transport, 10. Santé), puis sous-catégories dans l'ordre de `SousCategorie`.
"""

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


def reponses(nombre: int | None = None) -> list[str]:
    """Réponses à plat des `nombre` premières questions (toutes par défaut)."""
    return [r for question in REPONSES_JUIN[:nombre] for r in question]
