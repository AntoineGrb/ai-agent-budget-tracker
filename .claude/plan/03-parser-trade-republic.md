# Spec 03 — Parser Trade Republic et contrôle de solde

| Champ | Valeur |
| --- | --- |
| Étape du plan | 3 / 10 |
| Type | Code (Claude Code) |
| Complexité | Moyenne à complexe |
| Estimation | 2 sessions |
| Prérequis | Spec 01 terminée ; la spec 02 n'est pas un prérequis |
| Skill à charger | `parser-releve-bancaire` |

## Objectif

Extraire les opérations du **compte courant** du relevé PDF mensuel de Trade Republic en `OperationBrute`, en lisant la position des mots sur la page. Garantir par des contrôles bloquants que chaque opération a été lue, avec le bon sens et le bon montant.

## Références cadrage

- §3 Sources de données (pièges de Trade Republic, prototype 73/74)
- §11 Contrôles bloquants
- RG-12 (le sens se lit dans la colonne, jamais dans le type)

## Périmètre

**Inclus** : `parsers/trade_republic.py`, modèle `ReleveTR`, contrôles de solde, tests sur fixtures et données réelles.

**Exclus** : classement des types TR en natures (épargne, revenus financiers : étape 5), sections PEA du PDF.

## Structure du PDF (constatée sur juin 2026)

- Format A4 (595 × 842 pt), 8 pages contenant **trois relevés** successifs : compte courant (pages 1 à 6), puis deux relevés « Compte PEA » (pages 7 et 8). On ne parse que le compte courant.
- Un relevé commence sur une page contenant `SYNTHÈSE DU RELEVÉ DE COMPTE`. La ligne produit donne quatre montants : `Compte courant 1975,22 € 993,71 € 1435,36 € 1533,57 €` (solde de début, entrées, sorties, solde de fin). Les pages suivantes sans synthèse appartiennent au même relevé.
- La période figure en en-tête : `DATE 01 juin 2026 - 30 juin 2026`.
- Le tableau des transactions a pour colonnes `DATE`, `TYPE`, `DESCRIPTION`, `ENTRÉE D'ARGENT`, `SORTIE D'ARGENT`, `SOLDE`, et son en-tête est répété en haut de chaque page de continuation.
- Chaque page porte un bandeau haut (`TRADE REPUBLIC …`) et un pied de page (bloc `Trade Republic Bank GmbH, Branch France …` puis `Généré le …`, à partir de y ≈ 759 pt). La dernière page de transactions se termine par `APERÇU DU SOLDE`, suivi d'une page `REMARQUES SUR LE RELEVÉ DE COMPTE`.

### Géométrie mesurée (indicative : les bornes doivent être recalculées sur chaque page)

| Élément | Position |
| --- | --- |
| En-têtes DATE / TYPE / DESCRIPTION | x0 = 74 / 102 / 151 |
| En-tête ENTRÉE | x0 = 420 ; montants d'entrée alignés à gauche sur x0 = 420 |
| En-tête SORTIE | x0 = 453 ; montants de sortie alignés à gauche sur x0 = 453 |
| En-tête SOLDE | x0 = 502, x1 = 519 ; soldes alignés à droite (x1 = 513, suivi du mot `€` jusqu'à 519) |
| Ligne de transaction | Jour (`01`) et mois (`juin`) sur une ligne, année (`2026`) ~8 pt plus bas ; montants centrés verticalement, jamais au-dessus du jour |

## Pièges constatés et exigences associées

1. **En-têtes sur deux lignes** : avec les réglages par défaut, `extract_words()` restitue `ENTRÉE D'ARGENT` et `SORTIE D'ARGENT` caractère par caractère, entrelacés. Avec `extract_words(y_tolerance=1)`, on obtient bien `ENTRÉE`, `SORTIE` et `SOLDE` : utiliser ce réglage pour repérer les en-têtes.
2. **Symbole € collé** : le solde de la ligne DECATHLON du 08/06 est extrait sous la forme `€1238,73` (le `€` du montant précédent est collé). Normaliser chaque mot candidat en retirant les `€` en tête et en fin avant d'appliquer le motif `^-?\d+,\d{2}$`. C'est ce cas qui faisait échouer le prototype (73/74).
3. **Parsing texte interdit** : `extract_text()` colle les numéros de magasin aux montants (`CARREFOUR CITY 2466078 6,45 €` lu 24 660 786,45 €). On lit des mots positionnés, jamais des lignes de texte.
4. **Type « Avoir »** : il désigne tout paiement carte, dans les deux sens (remboursement Zalando de 51 € en entrée). Le sens vient de la colonne (RG-12).
5. **Lignes sur deux niveaux** : date (jour mois / année), type (`Exécution` / `d'ordre`, `Prelevement` / `bancaire`) et description peuvent occuper deux lignes visuelles.
6. **Soldes négatifs** : observés dans les relevés PEA (`-15,20 €`) ; le motif de montant accepte le signe `-`.

## Algorithme

1. **Découper les relevés** : parcourir les pages, ouvrir un relevé à chaque `SYNTHÈSE DU RELEVÉ DE COMPTE`, lire son produit et ses quatre montants. Exiger exactement un relevé `Compte courant`, sinon `ParseError`.
2. **Bornes de colonnes par page** : sur chaque page du relevé, repérer le mot `DESCRIPTION` du tableau (le mot n'apparaît que là), puis, dans une bande de ±8 pt autour de son `top` et avec `y_tolerance=1`, les mots `ENTRÉE`, `SORTIE` et `SOLDE`. Leurs abscisses servent de référence pour cette page.
3. **Zone utile** : les mots situés sous l'en-tête du tableau et au-dessus du premier des repères suivants : `APERÇU`, `REMARQUES`, ou le pied de page (ligne commençant par `Trade Republic Bank GmbH` dans le quart bas de la page).
4. **Début de ligne** : un mot `^\d{2}$` dans la colonne DATE (x0 < x0 de TYPE), suivi sur la même ligne d'un nom de mois. Une transaction occupe la bande verticale `[top_jour - 2 pt, top_jour_suivant - 2 pt[`, la dernière bande allant jusqu'à la limite de la zone utile.
5. **Contenu d'une bande** :
    - Date : jour et mois de la 1re ligne, année (`^\d{4}$`) dans la colonne DATE de la bande.
    - Type : mots de la colonne TYPE (x0 entre TYPE et DESCRIPTION), joints par un espace dans l'ordre de lecture.
    - Montants : mots dont la forme normalisée correspond au motif de montant et dont x0 est au-delà de la bordure gauche de la colonne ENTRÉE (x0 d'ENTRÉE − 5 pt).
    - Description : tous les autres mots à partir de la colonne DESCRIPTION, joints par un espace dans l'ordre de lecture (ligne, puis abscisse).
6. **Affectation des montants** : chaque bande doit contenir exactement deux montants. Le plus à droite est le solde (son x1 doit se trouver à moins de 8 pt du x1 de l'en-tête SOLDE). L'autre est le mouvement : entrée s'il est plus proche du x0 d'ENTRÉE, sortie s'il est plus proche du x0 de SORTIE. Toute autre configuration lève `ParseError` (page, rang de la ligne).
7. **Cohérence du sens** : le signe du mouvement déduit de sa colonne doit correspondre au signe de `solde - solde_précédent`. Un désaccord lève `ParseError` : c'est le signe d'une colonne mal attribuée.

## Mapping vers `OperationBrute`

| Champ | Valeur |
| --- | --- |
| `source` / `compte` | `TR` / `PERSO` |
| `date_operation` | Date de la ligne |
| `date_debit` | `None` |
| `type_operation` | Colonne Type, sur une ligne (`Avoir`, `Virement`, `Exécution d'ordre`, `Prelevement bancaire`, `Bonus`, `Intérêts`, `Rendement`…) |
| `libelle` / `libelle_brut` | Description sur une ligne (identiques pour TR) |
| `montant` | `+entrée` ou `−sortie` |
| `solde_apres` | Solde de la ligne |
| `ics`, `carte` | `None` |

## Modèle de sortie

```python
class ReleveTR(BaseModel):               # frozen, extra="forbid"
    fichier: str
    produit: str                          # "Compte courant"
    periode_debut: date
    periode_fin: date
    solde_debut: Decimal
    total_entrees: Decimal
    total_sorties: Decimal                # positif
    solde_fin: Decimal
    operations: tuple[OperationBrute, ...]
```

Fonction publique : `parser_trade_republic(chemin: Path) -> ReleveTR`.

## Contrôles (bloquants, dans le parser)

| ID | Contrôle |
| --- | --- |
| C1 | Pour chaque opération : `solde_précédent + montant == solde_apres`, en partant de `solde_debut` |
| C2 | Somme des entrées == `total_entrees` et somme des sorties == `total_sorties` |
| C3 | Dernier `solde_apres` == `solde_fin` |
| C4 | Toutes les dates sont dans la période ; la période couvre exactement un mois civil |
| C5 | Au moins une opération, sauf si entrées et sorties valent 0 |

Chaque échec lève `ControleError` indiquant la page, le rang, la date et les montants concernés, **sans la description** : elle peut contenir des noms ou des IBAN.

## Valeurs attendues sur les fixtures de juin 2026

| Élément | Valeur |
| --- | --- |
| Relevés dans le PDF | 3 (compte courant, 2 × PEA) ; seul le compte courant est retenu |
| Période | 01/06/2026 → 30/06/2026 |
| Opérations | 74 |
| Soldes | début 1 975,22 € ; fin 1 533,57 € |
| Totaux | entrées 993,71 € ; sorties 1 435,36 € |
| 1re opération | 01/06/2026, `Bonus`, `Cash reward allocation`, +8,54 €, solde 1 983,76 € |
| Cas du `€` collé | 08/06/2026, `Avoir`, `DECATHLON 0008`, −409,97 €, solde 1 238,73 € |
| Avoir en entrée (RG-12) | 11/06/2026, `Avoir`, `Zalando Payments`, +51,00 €, solde 1 333,69 € |
| Type et description sur deux lignes | 02/06/2026, `Exécution d'ordre`, description se terminant par `quantity: 0.032138`, −10,00 €, solde 1 875,37 € |
| Dernière opération | 30/06/2026, `Avoir`, `NYX*AIRSERVFRANCE`, −1,50 €, solde 1 533,57 € |

Les descriptions des virements sont modifiées par l'anonymisation : les tests golden sur ces lignes portent sur la date, le type et les montants uniquement. Pour juillet, les valeurs sont relevées lors de la première exécution réussie, puis figées.

## Dépendances autorisées

| Groupe | Paquet | Raison |
| --- | --- | --- |
| Runtime | `pdfplumber` | Coordonnées des mots (`extract_words`), approche validée sur juin |

## Critères d'acceptation

- [x] `parser_trade_republic` renvoie les valeurs du tableau ci-dessus sur la fixture de juin, avec les 74 opérations et C1 à C5 au vert.
- [x] La fixture de juillet est parsée sans erreur ; ses valeurs clés sont figées dans les tests.
- [x] Les bornes de colonnes sont calculées page par page à partir des en-têtes : aucune abscisse codée en dur hors tolérances nommées (constantes `TOLERANCE_*` documentées).
- [x] Le cas `€1238,73` est couvert par un test unitaire de normalisation des mots.
- [x] Les sections PEA sont ignorées ; un PDF sans relevé « Compte courant » lève `ParseError`.
- [x] C1 à C3 détectent une opération manquante (test : suppression d'une bande dans la liste intermédiaire, ou montant altéré), et le contrôle de sens (étape 7 de l'algorithme) détecte une inversion entrée/sortie.
- [x] Aucun message d'erreur ne contient de description.
- [x] Les tests `donnees_reelles` passent sur `data/raw/2026-06/` et `data/raw/2026-07/` quand ils sont présents.
- [x] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_parser_tr.py` | Valeurs golden, découpage des relevés, lignes sur deux niveaux, PEA ignorés |
| `test_parser_tr_mots.py` | Fonctions pures : normalisation des montants (`€` collé, signe), affectation aux colonnes à partir de mots synthétiques (dictionnaires `x0`, `x1`, `top`, `text`) |
| `test_parser_tr_controles.py` | C1 à C5 et contrôle de sens, sur des listes de bandes construites en mémoire |

Structurer le code pour que la lecture PDF (`pdfplumber`) soit isolée d'une couche de fonctions pures travaillant sur des mots positionnés : c'est ce qui rend les cas d'erreur testables sans fabriquer de PDF.

## Points ouverts

- Les montants TR observés n'ont pas de séparateur de milliers (`1983,76`). Si un montant ≥ 10 000 € apparaît avec un espace (`12 345,67`), deux mots se retrouveront dans la même colonne : les concaténer avant le parsing, et ajouter le cas en test.
- Les relevés historiques (étape 10) peuvent différer légèrement (libellés d'en-tête, langue). Toute variante devient un cas de test.

## Écarts constatés

- **Dépendance** : `pdfplumber` 0.11.10 ajouté (`uv add --system-certs pdfplumber`), avec ses dépendances transitives (`pdfminer-six`, `pypdfium2`, `pillow`…).
- **Architecture** : `lire_pages` est la seule fonction qui touche à `pdfplumber` ; tout le reste (`decouper_releves`, `reperer_colonnes`, `decouper_bandes`, `affecter_montants`, `controler_releve`…) travaille sur des `Mot` (`text`, `x0`, `x1`, `top`). Les structures intermédiaires (`Page`, `Bande`, `LigneTR`, `SyntheseTR`) sont des dataclasses figées ; seul `ReleveTR` est un modèle Pydantic.
- **`position`** : rang de l'opération dans le relevé (1 à 74 en juin). Les messages d'erreur citent la page et le rang sur la page, plus le rang global pour les contrôles C1 et C4.
- **Ordre des contrôles** : C4 (période, puis dates), C5, puis ligne à ligne le contrôle de sens et C1, enfin C2 et C3. La première anomalie arrête tout : contrairement à RG-03, une anomalie de solde se propage aux lignes suivantes, les regrouper n'apporterait que du bruit.
- **Contrôle de sens** : il reste une `ParseError` comme le prévoit la spec, mais une opération supprimée peut aussi inverser le sens de la variation de solde ; le message cite donc « colonne ENTRÉE/SORTIE mal attribuée ou opération manquante ».
- **Contrôles de format ajoutés** (`ParseError` avec page et rang) : un seul en-tête `DESCRIPTION` par page, mots du tableau hors d'une transaction, mot non montant dans les colonnes de montants, année absente, colonne TYPE vide, mouvement à plus de `TOLERANCE_MOUVEMENT` de ENTRÉE et de SORTIE, mouvement négatif dans sa colonne, PDF illisible.
- **Séparateur de milliers** (point ouvert) : traité par anticipation (`_fusionner_milliers`, `TOLERANCE_MILLIERS`) et couvert par un test synthétique ; aucun cas réel observé.
- **Pages sans tableau** : une page du relevé sans en-tête `DESCRIPTION` (page `REMARQUES`) ne produit aucune opération.
- **Valeurs de juillet figées** : 01/07 → 31/07/2026, 65 opérations, solde 1 533,57 € → 1 442,72 €, entrées 1 353,61 €, sorties 1 444,46 €. Les mois sont abrégés (`juil.`). Le 2e relevé PEA de juillet a des mouvements (205,15 € / 237,15 €), bien ignorés. Un avoir Amazon en entrée (+39,99 € le 27/07) confirme RG-12.
- **Descriptions des virements** : l'anonymisation laisse des parenthèses vides (`Incoming transfer from MME ET MR ( )`).
- **Données réelles** : synthèse, dates, types, montants et soldes identiques aux fixtures en juin et en juillet ; un test `donnees_reelles` le vérifie.
- **Test « sans compte courant »** : réalisé en remplaçant `lire_pages` par `monkeypatch` (pages PEA seules, ou compte courant en double), sans nouvelle fixture.
