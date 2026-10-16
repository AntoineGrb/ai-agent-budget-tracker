# Spec 09 — Publication Google Sheets

| Champ | Valeur |
| --- | --- |
| Étape du plan | 9 / 10 |
| Type | Mixte : créer le Sheet et le compte de service (section Préparation, hors Claude Code), puis la publication dans Claude Code |
| Complexité | Moyenne |
| Estimation | 1 à 2 sessions |
| Prérequis | Spec 08 terminée. Pour la recette réelle seulement : section Préparation faite |

> Spec réécrite le 16 octobre 2026 après le recadrage : l'onglet Synthèse présente les dépenses par catégorie au lieu des natures de flux, et l'onglet Règles est supprimé.

## Objectif

Publier un mois dans un Google Sheet créé à la main, avec deux types d'onglets :

- **Synthèse** : une ligne par mois, avec les dépenses de chaque catégorie en vue joint et en vue perso, le commentaire de l'agent, et un graphique sur 12 mois.
- **Un onglet par mois** (`2026-06`) : les opérations suivies du mois avec un filtre, le récapitulatif par catégorie de chaque vue, le commentaire et deux graphiques de répartition.

Le Sheet est une projection régénérable du ledger : chaque publication réécrit les onglets qu'elle gère, et un bandeau le rappelle. La publication est construite comme un **plan** pur (requêtes d'API calculées sans réseau), puis appliquée par un client mince autour de `gspread`. Le plan est testé par un fichier golden ; seul le client touche au réseau.

À la fin de l'étape, `budget publish --mois 2026-06 --simulation` écrit le plan de juin sans réseau, et `budget publish --mois 2026-06` met à jour le Sheet réel en trois appels d'API.

## Références cadrage

- §5 Deux vues séparées, jamais additionnées.
- §8 Restitution ; le Sheet est une projection régénérable.
- §9 Stack : `gspread` et requêtes `batchUpdate` brutes ; compte de service avec lequel le Sheet est partagé.
- §11 Aucune publication tant qu'un contrôle échoue ; secrets dans `.env`.

## État du code à l'entrée

- `agregats.controler_mois` et la table `syntheses` (spec 08) : agrégats et commentaire de chaque mois synthétisé.
- `restitution/operations.py` : `lignes_operations(transactions, classements)` donne les lignes de l'export CSV (9 colonnes : date d'opération, date de débit, compte, libellé, montant, catégorie, sous-catégorie, comptée, règle appliquée), une par opération suivie.
- `categories.Categorie` : ordre et libellés des 10 catégories.

## Préparation (hors Claude Code, avant l'implémentation)

À faire par l'utilisateur dans la console Google Cloud et dans Google Sheets. Rien de ceci ne passe par le dépôt.

1. **Projet** : sur `console.cloud.google.com`, créer un projet (par exemple `budget-tracker`).
2. **API** : dans « API et services » → « Bibliothèque », activer **Google Sheets API** et **Google Drive API** (recommandées ensemble par la documentation de gspread).
3. **Compte de service** : « IAM et administration » → « Comptes de service » → créer `budget-publication`, sans rôle sur le projet.
4. **Clé** : sur ce compte, « Clés » → « Ajouter une clé » → JSON. Enregistrer le fichier sous `data/google/compte-service.json` (hors git, avec le reste de `data/`).
5. **Sheet** : créer à la main un Google Sheet (par exemple « Budget du foyer »), puis le partager avec l'adresse `client_email` du JSON, en **Éditeur**, sans notification.
6. **`.env`** : `GOOGLE_APPLICATION_CREDENTIALS=data/google/compte-service.json` et `BUDGET_SPREADSHEET_ID=<identifiant>`, l'identifiant étant la partie de l'URL entre `/d/` et `/edit`.
7. **Facultatif** : un second Sheet de test, partagé de la même façon, dans `BUDGET_SPREADSHEET_ID_TEST`, pour le test d'intégration réel (marqueur `sheets_reel`).

Ne jamais déposer la clé JSON ailleurs que dans `data/` : ni dans le dépôt, ni dans une session cloud.

## Périmètre

**Inclus**

- Contenu des deux types d'onglets, à partir de la table `syntheses` et des lignes d'opérations.
- Plan de publication pur, client `gspread`, faux classeur en mémoire pour les tests.
- Commande `budget publish`.
- Variables `BUDGET_SPREADSHEET_ID` et `BUDGET_SPREADSHEET_ID_TEST` documentées dans le README (sans valeur) et marqueur pytest `sheets_reel`.

**Exclus**

- Onglet Règles (l'audit passe par `budget rules list`).
- Création du Sheet ou du compte de service par le code.
- Dépôt sur Google Drive et déclenchement automatique (phase 2), `budget run` (étape 10).

## Principe : un plan pur, un client mince

```python
def construire_plan(
    contenu: ContenuPublication,       # synthèses, lignes du mois, récapitulatifs
    etat: EtatClasseur,                # onglets existants : titre -> sheetId, graphiques, filtre
    maintenant: datetime,
) -> PlanPublication: ...


class Classeur(Protocol):
    def etat(self) -> EtatClasseur: ...
    def appliquer(self, plan: PlanPublication) -> None: ...
```

- `PlanPublication` contient les requêtes `batchUpdate` (dictionnaires JSON) et les plages de valeurs. Il se sérialise en JSON de façon déterministe : c'est lui que compare le test golden.
- `ClasseurGoogle` : `gspread.service_account(filename=…)`, `open_by_key(BUDGET_SPREADSHEET_ID)`, lecture des métadonnées (onglets, graphiques, filtres), un `batch_update` pour la structure et la mise en forme, puis un `values_batch_update` pour les valeurs. Vérifier les noms de méthodes dans la documentation de gspread 6.
- `ClasseurMemoire` (tests) : applique le plan à un modèle en mémoire (onglets, valeurs, graphiques, filtres), ce qui permet de tester l'idempotence sans réseau.
- `maintenant` est passé en paramètre (pas d'horloge figée globale) : les tests fournissent un instant fixe.

## Écriture des valeurs, sans `float`

L'API JSON n'accepte que des nombres JSON, et la règle du projet interdit tout `float`. Les valeurs sont donc envoyées comme des **chaînes saisies** (`valueInputOption=USER_ENTERED`), que Sheets interprète :

- La première requête du plan fixe les paramètres du classeur : `locale` `fr_FR` et fuseau `Europe/Paris` (`updateSpreadsheetProperties`).
- Montant : chaîne au format français, sans séparateur de milliers ni symbole, produite depuis le `Decimal` (`-3116,50`). La fonction `restitution.export_csv.montant_csv` fait déjà exactement cela : la réutiliser.
- Date : `JJ/MM/AAAA`.
- Texte : préfixé d'une apostrophe (`'`), qui force le texte et empêche qu'un libellé commençant par `=`, `+` ou `-` soit lu comme une formule.
- Formats d'affichage par `repeatCell` : montants `#,##0.00 "€"`, écarts `[Red]+#,##0.00 "€";[Color10]-#,##0.00 "€";0.00 "€"` (dépassement en rouge), dates `dd/mm/yyyy`. Le rendu des séparateurs selon la locale est à vérifier sur le Sheet réel.

Un test parcourt le plan entier et échoue s'il contient un `float`.

## Onglets

Chaque onglet géré commence par un bandeau en A1 : « Projection générée par budget le 16/10/2026 à 18:40 à partir du ledger. Toute modification manuelle sera écrasée à la prochaine publication. » La ligne 3 porte les en-têtes, gelés (`frozenRowCount = 3`) ; les données commencent en ligne 4.

### Synthèse

Une ligne par mois disposant d'une synthèse (table `syntheses`), dans l'ordre chronologique. 24 colonnes :

| Colonnes | Contenu |
| --- | --- |
| A | Mois (`AAAA-MM`) |
| B | Joint : total |
| C à L | Joint : une colonne par catégorie, dans l'ordre de `Categorie` (en-tête « Joint · Logement », etc.), 0,00 € si aucune dépense |
| M | Perso : total |
| N à W | Perso : une colonne par catégorie, même ordre |
| X | Commentaire : `commentaire.resume`, ou « Commentaire à régénérer : budget synthese --mois AAAA-MM » |

Les colonnes de catégories sont toujours présentes, même vides : leur position ne dépend pas des mois publiés, ce qui garde les graphiques et les formules éventuelles stables.

Graphique (`addChart`, `basicChart` en colonnes groupées, jamais empilées : les deux vues ne s'additionnent pas) : les 12 derniers mois, séries « Joint : total » et « Perso : total », domaine « Mois ». Ancré sous le tableau.

### Onglet mensuel (`2026-06`)

- **Opérations**, colonnes A à I : les 9 colonnes de l'export CSV, une ligne par opération suivie, dans l'ordre de `lignes_operations`. Les opérations non comptées sont incluses (colonne « Comptée » à `non`), et le filtre permet de les masquer.
- **Filtre de base** (`setBasicFilter`) sur l'en-tête et toutes les lignes d'opérations.
- **Récapitulatif**, à partir de la colonne K : bloc « Vue joint », puis, deux lignes plus bas, bloc « Vue perso ». Colonnes : Catégorie, Montant, Budget, Écart budget, Moyenne 3 mois, Écart moyenne. Une ligne par catégorie des agrégats, puis une ligne Total égale au total de la vue (contrôle 3 de la spec 08). Cellules vides quand le budget ou la moyenne n'existent pas.
- **Commentaire**, deux lignes sous le bloc perso : le résumé, puis un point clé par ligne.
- **Deux graphiques en secteurs** (répartition par catégorie, joint et perso), ancrés à droite du récapitulatif. Une catégorie de montant négatif (remboursement supérieur aux achats) est exclue des secteurs, mais reste dans le récapitulatif.

### Ordre et onglets étrangers

Ordre des onglets gérés : Synthèse, puis les mois du plus récent au plus ancien. Un onglet dont le titre n'est ni `Synthèse` ni au format `AAAA-MM` n'est jamais modifié ni supprimé.

## Idempotence

Pour chaque onglet à publier :

- **S'il existe** : il est vidé entièrement dans le même `batchUpdate` (`updateCells` avec `fields: "*"` sur toute la grille, `deleteEmbeddedObject` pour chacun de ses graphiques, `clearBasicFilter` s'il a un filtre), puis redimensionné et remis en forme. Son `sheetId` est conservé.
- **Sinon** : il est créé (`addSheet`) avec un `sheetId` déterministe : 1000 pour Synthèse, `AAAAMM` pour un mois (202606).

Les requêtes d'un `batchUpdate` s'appliquent dans l'ordre : paramètres du classeur, vidage ou création, formats, filtre, graphiques. Les valeurs suivent dans un second appel. Une publication se fait donc en trois appels (métadonnées, structure, valeurs), loin des quotas de l'API.

Publier deux fois de suite donne le même classeur (test avec `ClasseurMemoire`). Une valeur saisie à la main dans un onglet géré disparaît à la publication suivante, comme l'annonce le bandeau.

## Commande `budget publish`

`budget publish --mois AAAA-MM [--base …] [--contexte …] [--sans-llm] [--tous-les-mois] [--simulation]` :

1. Synthèse du mois par la chaîne de la spec 08 : reclassement, contrôles bloquants (échec : code 1, rien n'est publié), agrégats, commentaire réutilisé si l'empreinte n'a pas changé.
2. Contenu : toutes les synthèses enregistrées pour l'onglet Synthèse, et l'onglet du mois. Avec `--tous-les-mois`, la synthèse de chaque mois importé est recalculée (commentaires réutilisés si leur empreinte n'a pas changé) et chaque onglet mensuel est republié ; un mois qui échoue aux contrôles arrête la commande avant toute requête.
3. Plan, puis :
    - `--simulation` : aucun réseau ni identifiant requis ; le plan est écrit dans `data/publication/plan_AAAA-MM.json` et résumé (onglets, nombre de lignes, de graphiques, de requêtes) ;
    - sinon : application par `ClasseurGoogle`, puis affichage de l'URL du Sheet.

**Erreurs** : `PublicationError(BudgetError)`, avec des messages actionnables et sans contenu du Sheet :

| Cas | Message |
| --- | --- |
| Fichier de clé absent ou illisible | Chemin attendu, variable `GOOGLE_APPLICATION_CREDENTIALS` |
| `BUDGET_SPREADSHEET_ID` absent | Où trouver l'identifiant dans l'URL du Sheet |
| HTTP 403 | « Le Sheet n'est pas partagé en Éditeur avec <client_email> » |
| HTTP 404 | Identifiant de Sheet introuvable |
| HTTP 429 ou 5xx | Relances avec attente exponentielle (vérifier ce que propose gspread 6), puis message invitant à relancer `budget publish`, qui est rejouable |

## Organisation du code

```
src/budget/publication/
├── __init__.py
├── contenu.py      # ContenuPublication : lignes Synthèse, opérations du mois, récapitulatifs
├── plan.py         # construire_plan, PlanPublication : fonctions pures
├── cellules.py     # dates, texte préfixé, formats (montants : montant_csv)
├── classeur.py     # Classeur, EtatClasseur, ClasseurMemoire
└── google.py       # ClasseurGoogle : seul module qui importe gspread
```

## Valeurs attendues sur les fixtures

Après les scénarios de référence de juin et juillet (`tests/scenarios/reference.py`), `contexte.example.yaml`, synthèses de juin et juillet calculées avec un `FunctionModel`, instant fixe, classeur vide au départ, publication de juillet.

| Élément | Valeur |
| --- | --- |
| Onglets créés | `Synthèse` (1000), `2026-07` (202607), dans cet ordre ; `2026-06` (202606) en plus avec `--tous-les-mois`, après `2026-07` |
| Synthèse | 2 lignes : juin (Joint 7 566,46 ; Joint · Logement 3 289,26 ; Perso 1 322,86 ; Perso · Loisirs 685,69 ; Joint · Divers 0,00) et juillet (Joint 9 404,27 ; Perso 1 154,49 ; Joint · Divers 618,45) |
| Onglet `2026-07` | 169 lignes d'opérations ; totaux des récapitulatifs : joint 9 404,27 €, perso 1 154,49 € ; écart budget Courses joint +157,92 € ; moyenne 3 mois Logement joint 3 289,26 € |
| Onglet `2026-06` (`--tous-les-mois`) | 137 lignes d'opérations ; totaux : joint 7 566,46 €, perso 1 322,86 € |
| Graphiques | 1 sur Synthèse, 2 par onglet mensuel |
| Appels d'API | 3 (métadonnées, structure, valeurs) |
| Seconde publication | Classeur identique ; graphiques et filtre recréés, jamais dupliqués |

Le plan complet de juillet est figé dans `tests/golden/publication_2026-07.json`.

## Dépendances autorisées

| Groupe | Paquet |
| --- | --- |
| Runtime | `gspread>=6.1,<7` (installe `google-auth`) |

Si `gspread` n'expose pas d'annotations de type exploitables par mypy, ajouter un `[[tool.mypy.overrides]]` ciblé sur ce module, et le noter dans les écarts.

## Critères d'acceptation

- [ ] Section Préparation faite, et noms des variables documentés dans le README (sans valeur).
- [ ] Le plan de juillet est identique au golden ; aucun `float` dans le plan ; toute cellule de texte est préfixée d'une apostrophe ; tout montant est une chaîne au format `-3116,50`.
- [ ] Contrôles bloquants : un mois avec une opération en attente n'est pas publié (code 1, aucune requête émise).
- [ ] Idempotence : deux publications successives sur `ClasseurMemoire` donnent le même état ; un onglet étranger (`Notes`) reste intact ; les graphiques ne sont pas dupliqués.
- [ ] Onglet existant vidé (valeurs, formats, graphiques, filtre) avec son `sheetId` conservé ; onglet absent créé avec son `sheetId` déterministe.
- [ ] Synthèse : 24 colonnes, mois dans l'ordre chronologique, commentaire ou mention « à régénérer ».
- [ ] Onglet mensuel : 9 colonnes d'opérations, filtre, récapitulatifs dont chaque total égale celui de la vue, commentaire, deux graphiques.
- [ ] `--simulation` fonctionne sans identifiants ni réseau.
- [ ] Erreurs 403, 404, clé absente et identifiant absent : `PublicationError` avec message actionnable (tests sur un faux client).
- [ ] Test `sheets_reel` (ignoré sans `BUDGET_SPREADSHEET_ID_TEST`) : publication réelle sur le Sheet de test, deux fois de suite.
- [ ] Recette manuelle sur le vrai Sheet : bandeaux, formats français, filtre, graphiques lisibles.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

Rester sur le cœur : le plan, l'idempotence, les erreurs.

| Fichier | Contenu |
| --- | --- |
| `test_publication_plan.py` | Golden de juillet, absence de `float`, préfixe texte, format des montants, onglet existant ou absent |
| `test_publication_idempotence.py` | `ClasseurMemoire` : double publication, onglet étranger, graphiques, contrôle bloquant |
| `test_publication_erreurs.py` | Traduction des erreurs d'API, clé et identifiant absents |
| `test_publication_reelle.py` | Marqueur `sheets_reel` |

## Points ouverts

- **Taille du classeur** : 12 onglets mensuels par an, acceptable. Au-delà, passer à un onglet « Opérations » unique avec des vues filtrées.
- **Locale imposée** : la publication fixe la locale `fr_FR` du classeur. Si l'utilisateur la change, la publication suivante la rétablit.
- **Onglets d'anciens mois** : seul l'onglet du mois publié est réécrit (ou tous avec `--tous-les-mois`). Après un changement de règles, relancer avec `--tous-les-mois`.
- **Libellés dans Google Sheets** : le Sheet contient les libellés complets. Il doit rester partagé avec le seul foyer et le compte de service.

## Écarts constatés

À compléter pendant l'implémentation.
