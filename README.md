# Budget Tracker agentique

Chaque mois, transforme les extractions bancaires du foyer (Crédit Agricole cartes et compte joint
en CSV, Trade Republic en PDF) en un suivi des dépenses publié dans Google Sheets. Le traitement est
essentiellement de l'ETL déterministe ; le LLM sert uniquement à catégoriser les marchands inconnus
et à commenter des chiffres déjà calculés.

Cadrage : `docs/cadrage.md`. Spécifications par étape : `.claude/plan/`.

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
