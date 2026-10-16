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
```

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
