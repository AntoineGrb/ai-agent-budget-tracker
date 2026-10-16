# Spec 10 — Run complet, puis import de l'historique

| Champ | Valeur |
| --- | --- |
| Étape du plan | 10 / 10 |
| Type | Organisationnel, précédé d'une courte partie code (Claude Code) |
| Complexité | Simple |
| Estimation | 1 session de code, puis le temps des runs et des réponses |
| Prérequis | Specs 08 et 09 terminées ; Préparation de la spec 09 faite ; `.env` complet (`ANTHROPIC_API_KEY` rattachée à un workspace, `GOOGLE_APPLICATION_CREDENTIALS`, `BUDGET_SPREADSHEET_ID`) ; extractions réelles dans `data/raw/` |

> Spec réécrite le 16 octobre 2026 après le recadrage : plus de bloc « exceptionnels », plus de journal `decisions.md`, plus d'alertes de charges fixes ni de dotations.

## Objectif

Mettre l'outil en service : enchaîner toute la chaîne avec une seule commande, valider les résultats sur juin et juillet réels, puis importer l'historique disponible pour que l'onglet Synthèse réponde à la question 3 du cadrage (« comment les dépenses évoluent-elles d'un mois à l'autre ? »).

L'étape a deux parties, dans cet ordre :

- **A. Code** (Claude Code) : `budget run`, test de bout en bout étendu, contrôle de traçabilité des règles de gestion, mode d'emploi mensuel.
- **B. Organisationnel** (utilisateur, poste local) : runs réels, validation, récupération et import de l'historique.

À la fin de l'étape (jalon 2), le Sheet est publié avec au moins 7 mois, chacun avec son commentaire, et le nombre de questions par mois décroît.

## Références cadrage

- §6 Règles de gestion conservées : chacune est couverte par au moins un test.
- §7 Déroulé dans le terminal : questions, puis validation en lot.
- §9 Commandes ; §11 Test de bout en bout sans appel réseau, résultats figés.
- §12 Risques : trop de questions au démarrage, mois incomplet, historique trop court.
- §13 Points ouverts : historique à importer (au moins 6 mois, 12 si possible), budgets par catégorie.

## État du code à l'entrée

- Commandes existantes : `import`, `classify` (questions puis validation en lot), `export`, `rules list|disable`, `config verifier`, plus `synthese` (spec 08) et `publish` (spec 09).
- `tests/test_bout_en_bout.py` rejoue déjà juin puis juillet sur les fixtures (`tests/scenarios/reference.py`) et fige les dépenses par catégorie. Les specs 08 et 09 y ajoutent les agrégats et la publication.

## A. Partie code

### `budget run`

```
budget run --mois AAAA-MM [--dossier data/raw] [--base …] [--contexte …] [--sans-llm] [--simulation]
budget run --depuis AAAA-MM --jusqu-a AAAA-MM [mêmes options]
```

Pour chaque mois, dans l'ordre chronologique :

1. `import` : idempotent, un mois déjà importé à l'identique n'ajoute rien (RG-16).
2. `classify` avec les questions, puis la validation en lot, dans le terminal.
3. S'il reste des opérations en attente (question passée, marchand non validé) : arrêt avant la synthèse, avec le décompte et la commande à relancer. Code 1.
4. `synthese`, puis `publish` (ou `--simulation`).

Les fonctions existantes sont réutilisées telles quelles ; `budget run` n'ajoute que l'enchaînement et un récapitulatif final (mois traités, questions posées, règles créées, appels aux deux agents, publication). Avec `--depuis` et `--jusqu-a`, le premier mois en échec ou incomplet arrête la boucle ; relancer la même commande reprend là où elle s'est arrêtée, puisque chaque étape est idempotente. Sans terminal interactif, aucune question n'est posée : le run s'arrête à l'étape 3 dès qu'une opération reste en attente.

### Test de bout en bout (cadrage §11)

Étendre `tests/test_bout_en_bout.py`, sans réseau (`FunctionModel` pour les deux agents, `ClasseurMemoire`) :

- `budget run --depuis 2026-06 --jusqu-a 2026-07 --simulation` sur les fixtures, avec les réponses de `tests/scenarios/reference.py` ;
- comparaison avec des goldens sous `tests/golden/` : `export_2026-07.csv`, `agregats_2026-07.json`, `commentaire_2026-07.json`, `publication_2026-07.json` (ce dernier vient de la spec 09).

Les goldens se régénèrent par une variable d'environnement (`BUDGET_GOLDEN=regenerer uv run pytest tests/test_bout_en_bout.py`). Toute régénération est justifiée dans le message de commit, avec le diff attendu.

### Traçabilité des règles de gestion (cadrage §6)

`tests/test_tracabilite.py` vérifie que :

- chaque règle conservée (`RG-01`, `RG-02`, `RG-03`, `RG-06`, `RG-09`, `RG-10`, `RG-11`, `RG-12`, `RG-13`, `RG-14`, `RG-16`, `RG-19`) apparaît dans au moins un nom ou une docstring de test, et dans au moins un commentaire ou une docstring de `src/budget/` ;
- aucune règle retirée au recadrage (`RG-04`, `RG-05`, `RG-07`, `RG-08`, `RG-15`, `RG-17`, `RG-18`) n'apparaît encore dans `src/` ni dans `tests/` (hors ce test).

La liste des règles est lue dans le tableau du §6 de `docs/cadrage.md`, pour ne pas la dupliquer. Le message d'échec liste les règles non couvertes ou encore citées.

### Mode d'emploi

Le `README.md` reçoit une section « Chaque mois » :

1. À partir du 1er du mois suivant, déposer les trois extractions dans `data/raw/AAAA-MM/`.
2. Lancer `uv run budget run --mois AAAA-MM`.
3. Répondre aux questions, puis valider les cartes (Entrée valide tout, un numéro corrige une ligne).
4. Vérifier le Sheet.
5. Sauvegarder `data/`.

S'y ajoutent les commandes d'audit (`budget rules list`, `budget rules disable`), l'export CSV, la reprise après interruption (`q` conserve les réponses données ; relancer la même commande), et la mise au point des budgets dans `contexte.yaml`.

### Critères d'acceptation (partie A)

- [ ] `budget run --mois 2026-06 --simulation` enchaîne les quatre étapes sur les fixtures (interlocuteur scripté et `FunctionModel`).
- [ ] `budget run` s'arrête avant la synthèse s'il reste une opération en attente, avec code 1 et le décompte ; une seconde exécution reprend sans réimporter ni reposer les questions répondues.
- [ ] `budget run --depuis 2026-06 --jusqu-a 2026-07` traite les deux mois dans l'ordre et s'arrête au premier mois incomplet.
- [ ] Test de bout en bout : les goldens de juillet sont identiques ; aucun appel réseau.
- [ ] Traçabilité : règles conservées couvertes, règles retirées absentes.
- [ ] README à jour.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## B. Partie organisationnelle (poste local)

À faire par l'utilisateur, dans un terminal local : elle demande les données réelles de `data/`, les secrets de `.env` et des réponses interactives. Les constats sont reportés dans « Écarts constatés ».

### B1. Run complet sur juin et juillet réels

Juin a déjà été classé lors de la recette du recadrage : ses questions ne reviendront pas.

1. `uv run budget config verifier` sur `data/contexte.yaml` (ajouter `llm.modele_synthese` si un autre modèle que le défaut est voulu).
2. `uv run budget run --depuis 2026-06 --jusqu-a 2026-07`.
3. Juillet : répondre aux questions sur les débits hors carte et les Amazon d'au moins 50 €, puis relire les propositions du LLM et valider. Faire une règle des virements et prélèvements récurrents.
4. Noter le nombre de questions et de marchands, et le temps passé (risque n° 1 du cadrage §12).

### B2. Validation des résultats

- [ ] Le Sheet contient Synthèse, `2026-07` et `2026-06`, avec bandeaux, filtre et graphiques.
- [ ] Les totaux par catégorie sont plausibles. Le net des cartes correspond au prélèvement différé (RG-03), ce qui est contrôlé à l'import.
- [ ] Les opérations non comptées (colonne « Comptée » à `non`) sont bien celles qu'on voulait exclure : virements vers les comptes perso, provision, investissements TR, débits différés.
- [ ] Le commentaire de l'agent ne cite que des montants présents dans la synthèse et nomme les catégories qui pèsent le plus.
- [ ] Le choix des modèles est confirmé (Haiku pour la catégorisation, Sonnet pour la synthèse), et le coût des appels relevé.
- [ ] Budgets par catégorie éventuellement ajoutés dans `data/contexte.yaml` à partir des montants observés, puis `budget config verifier`.

### B3. Récupération de l'historique

Point ouvert du cadrage §13 : au moins 6 mois, 12 si possible.

1. Pour chaque mois, déposer dans `data/raw/AAAA-MM/` :
    - `CA_CB_AAAAMM.csv` : export des cartes débitées en fin de mois M (encours débité), avec les sections des cartes actives à cette date ;
    - `CA_CPTE_JOINT_AAAAMM.csv` : export du compte joint du 1er à la fin du mois M ;
    - `TR_AAAAMM.pdf` : relevé mensuel Trade Republic du mois M.
2. Vérifier que chaque dossier contient ses trois fichiers : un mois incomplet ne peut pas être importé (RG-02). Un mois dont un fichier est introuvable est sauté et noté.

### B4. Import chronologique

1. Du plus ancien au plus récent : `uv run budget run --depuis AAAA-MM --jusqu-a 2026-05`. Les règles apprises sur les premiers mois réduisent les questions des suivants.
2. À chaque arrêt (contrôle en échec, opération en attente), traiter la cause, puis relancer la même commande.
3. Relever par mois : questions posées, marchands soumis au LLM, règles créées. La charge doit décroître.
4. Une fois tous les mois importés : `uv run budget publish --mois 2026-07 --tous-les-mois`, pour que chaque onglet mensuel reflète les règles finales.

**Points de vigilance**

- **Contrôles bloquants** : un format d'export ancien différent (CA ou TR) arrête l'import. Corriger le parser dans Claude Code avec un test sur le cas rencontré ; ne jamais assouplir un contrôle.
- **Collision d'identifiant RG-16** entre deux cycles de cartes (spec 04, écarts) : un import refusé à tort avec « déjà importées pour le mois … » relève de ce cas. L'ouvrir comme sujet de code (clé de dédoublonnage et migration de données).
- **Remplacement d'un mois** : un mois déjà importé ne peut pas être remplacé par un export différent (spec 04, points ouverts). Si un historique doit être réimporté, ajouter l'option `--remplacer` dans une petite spec dédiée.
- **Anciennes cartes** : les paiements d'une carte remplacée (X9817) restent lus et catégorisés normalement ; il n'y a plus d'alerte de carte inconnue.

### B5. Clôture du jalon 2

- [ ] L'onglet Synthèse couvre au moins 7 mois, avec un commentaire par mois et le graphique sur 12 mois.
- [ ] Les mois de forte hausse ont une explication dans le commentaire ; la question 3 du cadrage trouve une première réponse.
- [ ] `data/` est sauvegardé sur Google Drive (ledger, contexte, extractions).
- [ ] Les points ouverts du cadrage §13 tranchés sont reportés dans `docs/cadrage.md`.
- [ ] Les constats (questions par mois, coûts, écarts de format) sont notés ci-dessous, et les sujets de phase 2 listés (publication automatique, surveillance du dossier Drive, agent analyste).

## Valeurs attendues

| Élément | Valeur |
| --- | --- |
| Fixtures (partie A) | Celles des specs 08 et 09, figées dans `tests/golden/` |
| Juin et juillet réels | Relevées au run réel, sans être versionnées (données personnelles) |
| Historique | Questions par mois relevées dans « Écarts constatés » ; tendance attendue : décroissante |

## Dépendances autorisées

Aucune nouvelle dépendance.

## Points ouverts

- **Profondeur d'historique** : 6 mois au minimum, 12 si les exports sont disponibles (cadrage §13).
- **Publication automatique et surveillance du dossier Drive** : phase 2.

## Écarts constatés

À compléter pendant l'exécution (partie A) et les runs réels (partie B).
