# Spec 10 — Run complet sur juin, puis import de l'historique

| Champ | Valeur |
| --- | --- |
| Étape du plan | 10 / 10 |
| Type | Organisationnel, précédé d'une courte partie code (Claude Code) |
| Complexité | Simple |
| Estimation | 1 session de code, puis le temps des runs et des réponses |
| Prérequis | Specs 07 à 09 terminées ; Préparation de la spec 09 faite ; `.env` complet (`ANTHROPIC_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `BUDGET_SPREADSHEET_ID`) ; extractions réelles de juin dans `data/raw/2026-06/` |

## Objectif

Mettre l'outil en service : enchaîner toute la chaîne sur juin réel avec une seule commande, valider les résultats, puis importer l'historique disponible pour que l'onglet Synthèse puisse répondre à la question 3 du cadrage (« à quels moments de l'année le budget explose-t-il, et pourquoi ? »).

L'étape a deux parties, dans cet ordre :

- **A. Code** (Claude Code) : `budget run`, test de bout en bout figé sur juin, contrôle de traçabilité des règles de gestion, mode d'emploi mensuel.
- **B. Organisationnel** (utilisateur, poste local) : run réel de juin, validation, récupération et import de l'historique.

À la fin de l'étape (jalon 2), le Sheet est publié avec au moins 7 mois (6 mois d'historique plus juin), chacun avec son commentaire, et le nombre de questions par mois décroît.

## Références cadrage

- §9 Commandes : `budget run --mois 2026-06` enchaîne `import`, `classify` (avec les questions) et `publish`, chacune lançable seule
- §6 Chaque règle de gestion est couverte par au moins un test
- §11 Test de bout en bout sur juin avec un modèle de test PydanticAI, sans appel réseau, et résultats figés (golden files)
- §12 Risques : trop de questions au démarrage, mois incomplet, historique trop court
- §13 Points ouverts : historique à importer (au moins 6 mois, 12 si possible), dotation du conjoint, budgets par sous-catégorie, dépenses annuelles
- §14 Jalon 2

## A. Partie code

### `budget run`

```
budget run --mois AAAA-MM [--dossier data/raw] [--base …] [--contexte …] [--journal …] [--sans-llm] [--simulation]
budget run --depuis AAAA-MM --jusqu-a AAAA-MM [mêmes options]
```

Pour chaque mois, dans l'ordre chronologique :

1. `import` : idempotent, un mois déjà importé à l'identique n'ajoute rien (RG-16).
2. `classify` avec les questions (blocs 1 à 3), dans le terminal.
3. Si des éléments restent en attente (questions passées, paiements carte sans sous-catégorie, salaire sans mois de paie) : arrêt avant publication, avec le décompte et la commande à relancer. Code 1.
4. `synthese`, puis `publish` (ou `--simulation`).

Les fonctions existantes sont réutilisées telles quelles ; `budget run` n'ajoute que l'enchaînement et le récapitulatif final (mois traités, questions posées, règles créées, appels aux deux agents, publication). Avec `--depuis` et `--jusqu-a`, le premier mois en échec ou incomplet arrête la boucle ; relancer la même commande reprend là où elle s'est arrêtée, puisque chaque étape est idempotente. Sans terminal interactif, aucune question n'est posée : le run s'arrête à l'étape 3 dès qu'il reste une question.

### Test de bout en bout figé (cadrage §11)

`tests/test_bout_en_bout.py`, sur les fixtures de juin, horloge figée, sans réseau :

- import, puis classement avec les réponses scriptées des specs 06 et 07 (`InterlocuteurScripte`), le catégoriseur et l'agent synthèse branchés sur des `FunctionModel` déterministes ;
- synthèse, puis publication sur `ClasseurMemoire` ;
- comparaison avec `tests/golden/2026-06/` : `export.csv`, `decisions.md`, `agregats.json`, `commentaire.json`, `publication.json`.

Les goldens se régénèrent par une variable d'environnement (`BUDGET_GOLDEN=regenerer uv run pytest tests/test_bout_en_bout.py`). Toute régénération est justifiée dans le message de commit, avec le diff attendu. Les goldens des étapes 7 à 9 peuvent être déplacés sous `tests/golden/` à cette occasion.

### Traçabilité des règles de gestion (cadrage §6)

`tests/test_tracabilite.py` vérifie que chaque identifiant `RG-01` à `RG-18` apparaît :

- dans au moins un nom ou une docstring de test ;
- dans au moins un commentaire ou une docstring de `src/budget/`.

Le message d'échec liste les règles non couvertes.

### Mode d'emploi

Le `README.md` reçoit une section « Chaque mois » : déposer les trois extractions dans `data/raw/AAAA-MM/` à partir du 1er du mois suivant, lancer `budget run --mois AAAA-MM`, répondre aux questions, vérifier le Sheet, sauvegarder `data/`. Plus les commandes d'audit (`budget rules list`, `budget rules disable`), l'export CSV et la reprise après interruption.

### Critères d'acceptation (partie A)

- [ ] `budget run --mois 2026-06 --simulation` enchaîne les quatre étapes sur les fixtures (test avec interlocuteur scripté et `FunctionModel`).
- [ ] `budget run` s'arrête avant la publication s'il reste une question passée, avec code 1 et le décompte ; une seconde exécution reprend sans réimporter ni reposer les questions répondues.
- [ ] `budget run --depuis 2026-06 --jusqu-a 2026-07` traite les deux mois dans l'ordre, et s'arrête au premier mois incomplet.
- [ ] Test de bout en bout : les cinq goldens de juin sont identiques ; aucun appel réseau (`ALLOW_MODEL_REQUESTS = False`, classeur en mémoire).
- [ ] Traçabilité : RG-01 à RG-18 couvertes dans les tests et dans le code.
- [ ] README à jour.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## B. Partie organisationnelle (poste local)

À faire par l'utilisateur, dans un terminal local : elle demande les données réelles de `data/`, les secrets de `.env` et des réponses interactives. Les constats sont reportés dans « Écarts constatés ».

### B1. Run complet sur juin réel

1. `uv run budget config verifier` sur `data/contexte.yaml`.
2. `uv run budget run --mois 2026-06`.
3. Répondre aux questions :
    - **Virements** (bloc 1) : en particulier les trois virements vers le compte du conjoint (500 €, 400 €, 550 €) : lequel est la dotation (cadrage §13) ? En faire une règle si le libellé est stable.
    - **Paiements carte** (bloc 2) : relire les propositions du LLM, corriger par numéro, valider avec Entrée (environ 60 marchands au premier run).
    - **Exceptionnels** (bloc 3).
4. Noter le nombre de questions par bloc et le temps passé (risque n° 1 du cadrage §12).

### B2. Validation des résultats

- [ ] Le Sheet contient Synthèse, `2026-06` et Règles, avec bandeaux, filtre et trois graphiques.
- [ ] Les totaux de l'onglet `2026-06` se rapprochent des relevés : net de chaque carte égal au prélèvement différé (RG-03), solde Trade Republic (spec 03).
- [ ] Les alertes attendues sur juin réel sont présentes et comprises : RG-18 sur les deux prêts (+21,51 % et +15,16 %, montants de juillet retenus dans le contexte), assurance auto attendue absente, carte X9817 inactive (spec 05, écarts).
- [ ] Les écarts joint et perso sont plausibles ; l'interprétation des compléments (spec 08, points ouverts) est confirmée ou corrigée.
- [ ] Le commentaire de l'agent ne cite que des montants présents dans la synthèse et explique les dépassements.
- [ ] `data/decisions.md` est lisible et reflète les réponses.
- [ ] Le choix des modèles est confirmé (Haiku 4.5 ou 5.5 pour la catégorisation, Sonnet 5 ou 5.5 pour la synthèse : specs 07 et 08, points ouverts), et le coût des deux appels relevé.
- [ ] `data/contexte.yaml` mis à jour : dotation du conjoint, budgets par sous-catégorie si souhaités, dépenses annuelles connues (taxe foncière, impôts, assurance auto, vacances), puis `budget config verifier`.

### B3. Récupération de l'historique

Point ouvert du cadrage §13 : au moins 6 mois, 12 si possible.

1. Pour chaque mois, déposer dans `data/raw/AAAA-MM/` :
    - `CA_CB_AAAAMM.csv` : export des cartes débitées en fin de mois M (encours débité), un fichier contenant les sections des cartes actives à cette date ;
    - `CA_CPTE_JOINT_AAAAMM.csv` : export du compte joint du 1er à la fin du mois M ;
    - `TR_AAAAMM.pdf` : relevé mensuel Trade Republic du mois M.
2. Vérifier que chaque dossier contient ses trois fichiers : un mois incomplet ne peut pas être importé (RG-02). Un mois dont un fichier est introuvable est sauté et noté.
3. Vérifier les cartes des mois anciens : une carte alors active mais absente de `cartes_actives` (X9817 avant son remplacement) produit des alertes RG-17. L'ajouter dans `cartes_inactives` suffit pour les comprendre ; elles ne bloquent rien.

### B4. Import chronologique

1. Du plus ancien au plus récent : `uv run budget run --depuis AAAA-MM --jusqu-a 2026-05`. Les règles apprises sur les premiers mois réduisent les questions des suivants.
2. À chaque arrêt (contrôle en échec, question passée), traiter la cause puis relancer la même commande.
3. Relever par mois : questions des blocs 1 à 3, marchands soumis au LLM, règles créées, alertes. La charge doit décroître.
4. Une fois tous les mois importés : `uv run budget publish --mois 2026-06 --tous-les-mois`, pour que chaque onglet mensuel reflète les règles finales.

**Points de vigilance**

- **Contrôles bloquants** : un format d'export ancien différent (CA ou TR) arrête l'import. Corriger le parser dans Claude Code avec un test sur le cas rencontré ; ne jamais assouplir un contrôle.
- **Collision d'identifiant RG-16** entre deux cycles de cartes (spec 04, écarts) : un import refusé à tort avec « déjà importées pour le mois … » relève de ce cas. L'ouvrir comme sujet de code (clé de dédoublonnage et migration de données).
- **Contexte daté** : `contexte.yaml` ne porte que les valeurs actuelles. Les mois anciens produiront des alertes RG-18 (montants de prêts antérieurs) et des questions sur des dotations d'un autre montant. C'est attendu ; si le bruit gêne, envisager un contexte daté (évolution de schéma, nouvelle spec).
- **Remplacement d'un mois** : un mois déjà importé ne peut pas être remplacé par un export différent (spec 04, points ouverts). Si un historique doit être réimporté, ajouter l'option `--remplacer` dans une petite spec dédiée.

### B5. Clôture du jalon 2

- [ ] L'onglet Synthèse couvre au moins 7 mois, avec un commentaire par mois et le graphique sur 12 mois.
- [ ] Les mois en dépassement ont une explication ; la question 3 du cadrage trouve une première réponse.
- [ ] `data/` est sauvegardé sur Google Drive (ledger, règles, journal, contexte, extractions).
- [ ] Les points ouverts du cadrage §13 tranchés sont reportés dans `docs/cadrage.md`.
- [ ] Les constats (questions par mois, coûts, écarts de format) sont notés ci-dessous, et les sujets de phase 2 listés (publication automatique, surveillance du dossier Drive, agent analyste).

## Valeurs attendues

| Élément | Valeur |
| --- | --- |
| Fixtures de juin (partie A) | Celles des specs 07 à 09, figées dans `tests/golden/2026-06/` |
| Juin réel | Relevées au premier run réel, sans être versionnées (données personnelles) |
| Historique | Questions par mois relevées dans « Écarts constatés » ; tendance attendue : décroissante |

## Dépendances autorisées

Aucune nouvelle dépendance.

## Points ouverts

- **Profondeur d'historique** : 6 mois au minimum, 12 si les exports sont disponibles (cadrage §13).
- **Contexte daté** : à décider après l'import de l'historique, selon le bruit des alertes RG-05 et RG-18.
- **Publication automatique et surveillance du dossier Drive** : phase 2.

## Écarts constatés

À compléter pendant l'exécution (partie A) et les runs réels (partie B).
