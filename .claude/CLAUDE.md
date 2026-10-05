# Directives du Projet

## Vision & Contexte
- Projet : Budget Tracker agentique (projet d'apprentissage n°2, framework PydanticAI v2)
- Objectif : chaque mois, transformer trois extractions bancaires (Crédit Agricole cartes et compte joint en CSV, Trade Republic en PDF) en un suivi des dépenses du foyer publié dans Google Sheets, avec une vue compte joint et une vue compte perso. Environ 80 % du projet est de l'ETL déterministe ; le LLM sert uniquement à catégoriser les marchands inconnus et à commenter des chiffres déjà calculés par le code.
- Cadrage global : voir `docs/cadrage/` (règles de gestion RG-01 à RG-18, natures de flux, catégories, contrôles, plan en 10 étapes).
- Étape en cours : voir la spec la plus récente non terminée dans `docs/specs/`.

## Commandes de Développement
- Installation : `uv sync`
- CLI : `uv run budget --help`
- Tests : `uv run pytest`
- Tests sans données réelles : `uv run pytest -m "not donnees_reelles"`
- Linter : `uv run ruff check .` — Format : `uv run ruff format .`
- Typage : `uv run mypy src`
- Vérification complète avant de valider une tâche : `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
- Export pour le projet Claude.ai : `npx repomix` (respecte `.gitignore` : `data/` et `.env` restent exclus)

## Style de Code & Conventions
- **Langage** : Python 3.12, typage strict (`mypy --strict` avec le plugin `pydantic.mypy`).
- **Dépendances** : uv et `pyproject.toml`. Layout `src/budget/`, tests dans `tests/`, outils de dev dans `scripts/`.
- **Modèles** : Pydantic v2. Objets de domaine immuables : `model_config = ConfigDict(frozen=True, extra="forbid")`.
- **Montants** : `decimal.Decimal` exclusivement, jamais `float`. Parsing via `budget.montants.parse_montant`, affichage via `budget.montants.formater_montant`. Convention de signe : négatif = sortie d'argent.
- **Dates** : `datetime.date`. Parsing via `budget.dates` (formats `JJ/MM/AAAA`, `JJ/MM/AA`, `30 juin 2026`).
- **Nommage** : vocabulaire métier en français sans accents dans les identifiants (`libelle`, `montant`, `mois_budgetaire`, `OperationBrute`). Messages utilisateur en français.
- **Erreurs** : exceptions dédiées héritant de `BudgetError` (`ParseError`, `ControleError`, `ConfigError`). Chaque message est actionnable : fichier, page ou ligne, valeur attendue contre valeur trouvée, règle concernée (`RG-03`).
- **Logs** : module `logging`, pas de `print` hors CLI. Jamais de libellé bancaire complet, d'IBAN ou de nom dans un log ou un message d'erreur : on cite la position (page, ligne), la date et le montant.
- **Traçabilité** : toute règle de gestion implémentée cite son identifiant en commentaire (`# RG-03`) et dans le nom ou la docstring du test qui la couvre.
- **Tests** : pytest. Les critères d'acceptation d'une spec deviennent des tests avant l'implémentation. Les tests sur fichiers réels portent le marqueur `donnees_reelles` et sont ignorés si `data/raw/` est absent.

## Règles Non-Négociables (Garde-fous)
- Ne JAMAIS committer de données réelles. Tout ce qui est dans `data/` est hors git. Les fixtures de `tests/fixtures/` sont anonymisées (skill `fixtures-anonymisees`) et vérifiées par `tests/test_fixtures_anonymes.py`.
- Ne JAMAIS utiliser `float` pour un montant, y compris en lecture YAML ou en calcul intermédiaire.
- Un contrôle de cohérence qui échoue arrête le traitement. Ne jamais le transformer en avertissement, l'assouplir ou ajouter une tolérance pour faire passer un test : corriger le parser.
- Ne rien envoyer au LLM d'autre que des libellés marchands de paiements carte (cadrage §11).
- Ne pas introduire de nouvelle dépendance sans demander au préalable. Les dépendances autorisées sont listées dans la spec de l'étape en cours.
- Ne pas modifier le schéma SQLite sans script de migration (à partir de l'étape 4).
- PydanticAI : v2 uniquement. Consulter le skill `pydantic-ai-v2` avant d'écrire ou de modifier du code d'agent.
- Exécuter la vérification complète (lint, format, typage, tests) avant de déclarer une tâche terminée.
- Une spec à la fois. Si la spec est ambiguë ou contredite par les données, s'arrêter et poser la question plutôt que d'inventer ; noter l'écart dans la section « Écarts constatés » de la spec.

## Structure de la Documentation
- `docs/cadrage/` : Cadrage général du projet (export Markdown du document de cadrage)
- `docs/specs/` : Spécifications techniques par étape (`01-socle.md`, `02-parsers-credit-agricole.md`, `03-parser-trade-republic.md`, …)
- `.claude/skills/` : Savoir-faire réutilisables (`implementer-spec`, `parser-releve-bancaire`, `fixtures-anonymisees`, `pydantic-ai-v2`)

## Données locales (hors git)
- `data/raw/AAAA-MM/` : extractions réelles (`CA_CB_AAAAMM.csv`, `CA_CPTE_JOINT_AAAAMM.csv`, `TR_AAAAMM.pdf`)
- `data/contexte.yaml` : référence du foyer (modèle versionné : `contexte.example.yaml`)
- `data/anonymisation.yaml` : table de correspondance utilisée pour générer les fixtures
