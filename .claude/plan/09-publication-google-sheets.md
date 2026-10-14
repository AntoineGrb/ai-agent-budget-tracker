# Spec 09 — Publication Google Sheets : valeurs, filtres, graphiques

| Champ | Valeur |
| --- | --- |
| Étape du plan | 9 / 10 |
| Type | Mixte : créer le Sheet et le compte de service (section Préparation, hors Claude Code), puis la publication dans Claude Code |
| Complexité | Moyenne |
| Estimation | 2 sessions |
| Prérequis | Spec 08 terminée. Pour la recette réelle uniquement : section Préparation faite |

## Objectif

Publier un mois dans un Google Sheet créé à la main : l'onglet Synthèse (une ligne par mois, graphique sur 12 mois), un onglet par mois (toutes les opérations des deux vues, filtre actif, récapitulatif par catégorie, graphiques de répartition) et l'onglet Règles. Le Sheet est une projection régénérable du ledger : chaque publication écrase les onglets qu'elle gère, et un bandeau le rappelle.

La publication est construite comme un **plan** pur (requêtes d'API calculées sans réseau), puis appliquée par un client mince autour de `gspread`. Le plan est testé par des fichiers golden ; seul le client touche au réseau.

À la fin de l'étape, `budget publish --mois 2026-06 --simulation` écrit le plan de juin sans réseau, et `budget publish --mois 2026-06` met à jour le Sheet réel en trois appels d'API.

## Références cadrage

- §8 Structure du Google Sheet : onglets Synthèse, mensuel et Règles, colonnes, graphiques, bandeau, projection régénérable
- §9 Stack : `gspread` et requêtes `batchUpdate` brutes ; compte de service avec lequel le Sheet est partagé
- §11 Aucune publication tant qu'un contrôle échoue ; secrets dans `.env`
- §12 Risques : modification manuelle du Sheet ; authentification ou quota Google (publication idempotente et rejouable seule)

## Préparation (hors Claude Code, avant l'implémentation)

À faire par l'utilisateur dans la console Google Cloud et dans Google Sheets. Rien de ceci ne passe par le dépôt.

1. **Projet** : sur `console.cloud.google.com`, créer un projet (par exemple `budget-tracker`).
2. **API** : dans « API et services » → « Bibliothèque », activer **Google Sheets API** et **Google Drive API** (recommandées ensemble par la documentation de gspread).
3. **Compte de service** : « IAM et administration » → « Comptes de service » → créer `budget-publication`, sans rôle sur le projet.
4. **Clé** : sur ce compte, « Clés » → « Ajouter une clé » → JSON. Enregistrer le fichier sous `data/google/compte-service.json` (hors git, avec le reste de `data/`).
5. **Sheet** : créer à la main un Google Sheet (par exemple « Budget du foyer »), puis le partager avec l'adresse `client_email` du JSON, en **Éditeur**, sans notification.
6. **`.env`** : `GOOGLE_APPLICATION_CREDENTIALS=data/google/compte-service.json` et `BUDGET_SPREADSHEET_ID=<identifiant>`, l'identifiant étant la partie de l'URL entre `/d/` et `/edit`.
7. **Facultatif** : un second Sheet de test, partagé de la même façon, dans `BUDGET_SPREADSHEET_ID_TEST`, pour les tests d'intégration réels (marqueur `sheets_reel`).

Ne jamais déposer la clé JSON ailleurs que dans `data/` : ni dans le dépôt, ni dans une session cloud.

## Périmètre

**Inclus**

- Contenu des trois types d'onglets, à partir des synthèses (étape 8), des lignes d'opérations (`restitution/operations.py`, étape 7) et des règles.
- Plan de publication pur, client `gspread`, faux classeur en mémoire pour les tests.
- Commande `budget publish`.
- Ajouts à `.env.example` (`BUDGET_SPREADSHEET_ID`, `BUDGET_SPREADSHEET_ID_TEST`) et marqueur pytest `sheets_reel`.

**Exclus**

- Création du Sheet ou du compte de service par le code (le Sheet est créé à la main, cadrage §9).
- Dépôt sur Google Drive et déclenchement automatique (phase 2).
- `budget run` (étape 10).

## Principe : un plan pur, un client mince

```python
def construire_plan(
    contenu: ContenuPublication,       # synthèses, lignes du mois, récapitulatifs, règles
    etat: EtatClasseur,                # onglets existants : titre -> sheetId, graphiques, filtre
    maintenant: datetime,
) -> PlanPublication: ...


class Classeur(Protocol):
    def etat(self) -> EtatClasseur: ...
    def appliquer(self, plan: PlanPublication) -> None: ...
```

- `PlanPublication` contient les requêtes `batchUpdate` (dictionnaires JSON) et les plages de valeurs. Il se sérialise en JSON de façon déterministe : c'est lui que comparent les tests golden.
- `ClasseurGoogle` : `gspread.service_account(filename=…)`, `open_by_key(BUDGET_SPREADSHEET_ID)`, lecture des métadonnées (onglets, graphiques, filtres), un `batch_update` pour la structure et la mise en forme, puis un `values_batch_update` pour les valeurs. Vérifier les noms de méthodes dans la documentation de gspread 6.
- `ClasseurMemoire` (tests) : applique le plan à un modèle en mémoire (onglets, valeurs, graphiques, filtres), ce qui permet de tester l'idempotence sans réseau.

## Écriture des valeurs, sans `float`

L'API JSON n'accepte que des nombres JSON, et la règle du projet interdit tout `float`. Les valeurs sont donc envoyées comme des **chaînes saisies** (`valueInputOption=USER_ENTERED`) que Sheets interprète :

- La première requête du plan fixe les paramètres du classeur : `locale` `fr_FR` et fuseau `Europe/Paris` (`updateSpreadsheetProperties`). La publication modifie donc ce réglage, ce qui est documenté.
- Montant : chaîne au format français sans séparateur de milliers ni symbole, produite depuis le `Decimal` (`-3116,50`). Fonction dédiée `montant_pour_sheets`.
- Date : `JJ/MM/AAAA`.
- Texte : préfixé d'une apostrophe (`'`), qui force le texte et empêche qu'un libellé commençant par `=`, `+` ou `-` soit lu comme une formule.
- Formats d'affichage par `repeatCell` : montants `#,##0.00 "€"`, écarts `[Red]+#,##0.00 "€";[Color10]-#,##0.00 "€";0.00 "€"` (dépassement en rouge), dates `dd/mm/yyyy`. Le rendu des séparateurs selon la locale est à vérifier sur le Sheet réel.

Un test parcourt le plan entier et échoue s'il contient un `float`.

## Onglets

Chaque onglet géré commence par un bandeau en A1 : « Projection générée par budget le 14/10/2026 à 18:40 à partir du ledger. Toute modification manuelle sera écrasée au prochain run. » La ligne 3 porte les en-têtes, gelés (`frozenRowCount = 3`) ; les données commencent en ligne 4.

### Synthèse

Une ligne par mois disposant d'une synthèse (étape 8), dans l'ordre chronologique :

| Colonne | Source (agrégats de l'étape 8) |
| --- | --- |
| Mois | `AAAA-MM` |
| Revenus joint | `joint.revenus` |
| Dotations | `joint.dotations` |
| Charges fixes réelles | `joint.charges_fixes_reelles` |
| Charges fixes attendues | `joint.charges_fixes_attendues` |
| Dépenses courantes joint | `joint.depenses_courantes` |
| Compléments versés | `joint.complements_verses` |
| Écart joint | `joint.ecart` |
| Dépenses perso (investissements inclus) | `perso.consommation` |
| Écart perso | `perso.ecart` |
| Épargne nette | `epargne.nette` |
| Dont exceptionnels | `joint.exceptionnels + perso.exceptionnels` |
| Commentaire | `commentaire.resume`, ou « Commentaire à régénérer : budget synthese --mois AAAA-MM » |

Les colonnes reprennent celles du cadrage §8, avec deux précisions : « Compléments versés » rend l'écart joint vérifiable (dépenses courantes + compléments − enveloppe), et « Dépenses perso » désigne la consommation qui sert à l'écart perso.

Graphique (`addChart`, `basicChart` en colonnes) : les 12 derniers mois, séries « Dépenses courantes joint », « Dépenses perso (investissements inclus) » et « Dont exceptionnels », domaine « Mois ». Il est ancré à droite du tableau.

### Onglet mensuel (`2026-06`)

- Colonnes A à K : les 11 colonnes de l'export CSV (spec 07), une ligne par transaction du mois, dans le même ordre. Les opérations neutralisées sont incluses (audit), et le filtre permet de les masquer.
- Filtre de base (`setBasicFilter`) sur l'en-tête et toutes les lignes d'opérations.
- Récapitulatif à partir de la colonne M : bloc « Vue joint » puis, deux lignes plus bas, bloc « Vue perso ». Colonnes : Catégorie, Montant, Budget, Écart budget, Moyenne 3 mois, Écart moyenne. Une ligne par catégorie de montant non nul, puis une ligne Total égale aux dépenses de la vue (contrôle 5 de l'étape 8).
- Deux graphiques en secteurs (répartition par catégorie, joint et perso), ancrés à droite du récapitulatif.

### Règles (lecture seule)

Une ligne par règle apprise, active ou non : N°, Type, Source, Critère, Valeur, Condition de montant, Résultat, Active, Opérations couvertes, Créée le, Désactivée le (mêmes données que `budget rules list --toutes`).

### Ordre et onglets étrangers

Ordre des onglets gérés : Synthèse, les mois du plus récent au plus ancien, puis Règles. Un onglet dont le titre n'est ni `Synthèse`, ni `Règles`, ni au format `AAAA-MM` n'est jamais modifié ni supprimé.

## Idempotence

Pour chaque onglet à publier :

- **S'il existe** : il est vidé entièrement dans le même `batchUpdate` (`updateCells` avec `fields: "*"` sur toute la grille, `deleteEmbeddedObject` pour chacun de ses graphiques, `clearBasicFilter` s'il a un filtre), puis redimensionné et remis en forme. Son `sheetId` est conservé.
- **Sinon** : il est créé (`addSheet`) avec un `sheetId` déterministe : 1000 pour Synthèse, 1001 pour Règles, `AAAAMM` pour un mois (202606).

Les requêtes d'un `batchUpdate` s'appliquent dans l'ordre : paramètres du classeur, vidage ou création, formats, filtre, graphiques. Les valeurs suivent dans un second appel. Une publication se fait donc en trois appels (lecture des métadonnées, structure, valeurs), loin des quotas de l'API.

Publier deux fois de suite donne le même classeur (test avec `ClasseurMemoire`). Une valeur saisie à la main dans un onglet géré disparaît à la publication suivante, comme l'annonce le bandeau (cadrage §12).

## Commande `budget publish`

`budget publish --mois AAAA-MM [--base …] [--contexte …] [--sans-llm] [--tous-les-mois] [--simulation]` :

1. Synthèse du mois par la chaîne de l'étape 8 : reclassement, contrôles bloquants (échec : code 1, rien n'est publié), agrégats, commentaire réutilisé si l'empreinte n'a pas changé.
2. Contenu : toutes les synthèses enregistrées pour l'onglet Synthèse, telles qu'enregistrées par leur dernier `budget synthese` ; l'onglet du mois ; les règles. Avec `--tous-les-mois`, la synthèse de chaque mois est recalculée (commentaires réutilisés si leur empreinte n'a pas changé) et chaque onglet mensuel est republié.
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
| HTTP 429 ou 5xx | Relances avec attente exponentielle (client de gspread prévu pour cela, à vérifier en v6), puis message invitant à relancer `budget publish`, qui est rejouable |

## Organisation du code

```
src/budget/publication/
├── __init__.py
├── contenu.py      # ContenuPublication : synthèses, lignes du mois, récapitulatifs, règles
├── plan.py         # construire_plan, PlanPublication : fonctions pures
├── cellules.py     # montant_pour_sheets, dates, texte préfixé, formats
├── classeur.py     # Classeur, EtatClasseur, ClasseurMemoire
└── google.py       # ClasseurGoogle (seul module qui importe gspread)
```

## Valeurs attendues sur les fixtures de juin 2026

Après les scénarios des specs 06 et 07, `contexte.example.yaml`, horloge figée, classeur vide au départ.

| Élément | Valeur |
| --- | --- |
| Onglets créés | `Synthèse` (1000), `2026-06` (202606), `Règles` (1001), dans cet ordre |
| Ligne de juin de l'onglet Synthèse | Revenus joint 5 255,47 ; Dotations 1 350,00 ; Charges fixes réelles et attendues 2 797,25 ; Dépenses courantes joint 3 771,37 ; Compléments versés 900,00 ; Écart joint +3 471,37 ; Dépenses perso 1 056,46 ; Écart perso +556,46 ; Épargne nette 361,50 ; Dont exceptionnels 883,16 |
| Onglet `2026-06` | 159 lignes d'opérations ; totaux des récapitulatifs : joint 6 568,62 €, perso 994,96 € |
| Onglet Règles | 66 lignes |
| Graphiques | 3 : un sur Synthèse, deux sur l'onglet mensuel |
| Appels d'API | 3 (métadonnées, structure, valeurs) |
| Seconde publication | Classeur identique ; graphiques et filtre recréés, jamais dupliqués |

Le plan complet est figé dans `tests/golden/publication_2026-06.json`.

## Dépendances autorisées

| Groupe | Paquet |
| --- | --- |
| Runtime | `gspread>=6.1,<7` (installe `google-auth`) |

Si `gspread` n'expose pas d'annotations de type exploitables par mypy, ajouter un `[[tool.mypy.overrides]]` ciblé sur ce module, et le noter dans les écarts.

## Critères d'acceptation

- [ ] Section Préparation faite, et noms des variables reportés dans `.env.example` (sans valeur).
- [ ] Le plan de juin est identique au golden ; aucun `float` dans le plan ; toute cellule de texte est préfixée d'une apostrophe ; tout montant est une chaîne au format `-3116,50`.
- [ ] Contrôles bloquants : un mois avec une opération non classée n'est pas publié (code 1, aucune requête émise).
- [ ] Idempotence : deux publications successives sur `ClasseurMemoire` donnent le même état ; un onglet étranger (`Notes`) est intact ; les graphiques ne sont pas dupliqués.
- [ ] Onglet existant vidé (valeurs, formats, graphiques, filtre) et `sheetId` conservé ; onglet absent créé avec son `sheetId` déterministe.
- [ ] Onglet Synthèse : colonnes ci-dessus, mois dans l'ordre, commentaire ou mention « à régénérer ».
- [ ] Onglet mensuel : 11 colonnes de l'export, filtre de base, récapitulatifs dont le total égale les dépenses de chaque vue, deux graphiques.
- [ ] `--simulation` fonctionne sans identifiants ni réseau.
- [ ] Erreurs 403, 404, clé absente et identifiant absent : `PublicationError` avec message actionnable (tests sur un faux client).
- [ ] Test `sheets_reel` (ignoré sans `BUDGET_SPREADSHEET_ID_TEST`) : publication réelle de juin sur le Sheet de test, deux fois de suite.
- [ ] Recette manuelle sur le vrai Sheet : bandeaux, formats français, filtre, trois graphiques lisibles.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_publication_cellules.py` | `montant_pour_sheets`, dates, préfixe texte, formats |
| `test_publication_contenu.py` | Lignes Synthèse, onglet mensuel, récapitulatifs, Règles |
| `test_publication_plan.py` | Golden de juin, absence de `float`, ordre des requêtes, onglet existant ou absent |
| `test_publication_idempotence.py` | `ClasseurMemoire` : double publication, onglet étranger, graphiques |
| `test_publication_erreurs.py` | Traduction des erreurs d'API, clé et identifiant absents |
| `test_publication_reelle.py` | Marqueur `sheets_reel` |
| `test_cli.py` (ajouts) | `budget publish --simulation`, échec sur contrôle bloquant |

## Points ouverts

- **Taille du classeur** : 12 onglets mensuels par an, acceptable au MVP (cadrage §8). Au-delà, passer à un onglet « Opérations » unique avec des vues filtrées.
- **Locale imposée** : la publication fixe la locale `fr_FR` du classeur. Si l'utilisateur la change, la publication suivante la rétablit.
- **Onglets d'anciens mois** : seuls l'onglet du mois publié (ou tous avec `--tous-les-mois`) sont réécrits ; un onglet mensuel peut donc refléter des règles antérieures. Relancer avec `--tous-les-mois` après un changement de règles.
- **Libellés dans Google Sheets** : le Sheet contient les libellés complets, voulu par le cadrage §8. Il doit rester partagé avec le seul foyer et le compte de service.

## Écarts constatés

À compléter pendant l'implémentation.
