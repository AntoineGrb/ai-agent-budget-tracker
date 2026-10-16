# Budget Tracker agentique

Chaque mois, transforme les extractions bancaires du foyer (Crédit Agricole cartes et compte joint
en CSV, Trade Republic en PDF) en un suivi des dépenses par catégorie, en vue joint et en vue perso.
Seuls les débits sont suivis ; chacun reçoit une sous-catégorie ou est déclaré « non compté ». Le
traitement est essentiellement de l'ETL déterministe ; le LLM sert uniquement à catégoriser les
marchands carte inconnus.

```sh
uv run budget import --mois 2026-06     # contrôle et importe les trois extractions
uv run budget classify --mois 2026-06   # questions dans le terminal, puis validation des cartes
uv run budget export --mois 2026-06     # CSV du mois dans data/exports/
uv run budget synthese --mois 2026-06   # dépenses par catégorie, commentées par le LLM
uv run budget publish --mois 2026-06    # classeur Excel data/exports/budget_2026-06.xlsx
```

Cadrage : `docs/cadrage.md`. Spécifications par étape : `.claude/plan/`.

Déploiement sur un VPS (alias `trun`, `tpush`, `tpull`, `thelp`…) : `docs/deploiement.md`.

## Démarrage

```sh
uv sync
cp contexte.example.yaml data/contexte.yaml   # puis l'adapter
uv run budget config verifier
```

Vérification complète :

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

## Données

`data/` (extractions réelles, contexte, table d'anonymisation) est hors git. Les fixtures de
`tests/fixtures/` sont générées par `uv run python scripts/anonymiser.py --mois AAAA-MM` et
vérifiées par `tests/test_fixtures_anonymes.py`.

## Publication Excel

`budget publish --mois AAAA-MM` écrit `data/exports/budget_AAAA-MM.xlsx` (dossier : option
`--sortie`) : un onglet Synthèse (tableaux joint et perso, une ligne par mois, graphique sur 12
mois, commentaires) et un onglet par mois synthétisé jusqu'à AAAA-MM (commentaire, récapitulatifs
par catégorie et sous-catégorie, graphiques, puis opérations filtrables). Un fichier par mois,
construit à partir du ledger : les classeurs des mois précédents ne sont jamais touchés.
Republier un mois remplace son classeur ; le fermer dans Excel avant.

## Chaque mois

1. À partir du 1er du mois suivant, déposer les trois extractions dans `data/raw/AAAA-MM/`
   (`CA_CB_AAAAMM.csv`, `CA_CPTE_JOINT_AAAAMM.csv`, `TR_AAAAMM.pdf`).
2. Lancer `uv run budget run --mois AAAA-MM` : import, questions, validation des cartes,
   synthèse, puis classeur.
3. Répondre aux questions (débits hors carte, Amazon d'au moins 50 €), puis valider les cartes :
   Entrée valide tout, un numéro corrige une ligne.
4. Vérifier le classeur `data/exports/budget_AAAA-MM.xlsx` dans Excel.
5. Sauvegarder `data/`.

`budget run --depuis AAAA-MM --jusqu-a AAAA-MM` traite plusieurs mois, du plus ancien au plus
récent, et s'arrête au premier mois en erreur ou incomplet (code 1, avec le décompte). Corriger la
cause, puis relancer la même commande : chaque étape est idempotente, donc les mois terminés sont
repris sans rien ajouter et les questions déjà tranchées ne reviennent pas. Sans terminal
interactif, aucune question n'est posée : le run s'arrête dès qu'une opération reste en attente.

- **Interruption** : `q` dans un dialogue conserve les réponses déjà données ; relancer la même
  commande.
- **Sans LLM** : `--sans-llm` saisit les catégories à la main et publie sans commentaire.
- **Simulation** : `--simulation` va jusqu'à la préparation du classeur (contrôles et commentaire
  compris) sans écrire le fichier `.xlsx`.
- **Audit des règles** : `uv run budget rules list`, puis `uv run budget rules disable N` ;
  relancer ensuite `budget classify --mois AAAA-MM` pour les mois indiqués.
- **Export CSV** : `uv run budget export --mois AAAA-MM`.
- **Budgets** : à ajuster dans `data/contexte.yaml` d'après les montants observés, puis
  `uv run budget config verifier`.
