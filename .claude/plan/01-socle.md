# Spec 01 — Socle du projet

| Champ | Valeur |
| --- | --- |
| Étape du plan | 1 / 10 |
| Type | Code (Claude Code) |
| Complexité | Simple |
| Estimation | 1 session |
| Prérequis | Extractions réelles de juin et juillet 2026 déposées dans `data/raw/2026-06/` et `data/raw/2026-07/` ; `data/anonymisation.yaml` rempli par l'utilisateur |

## Objectif

Poser les fondations sur lesquelles toutes les étapes suivantes s'appuient : structure du repo, outillage, modèles de domaine, utilitaires de parsing (montants, dates), chargement validé du fichier de contexte, CLI minimale et jeux de test anonymisés.

À la fin de l'étape, `uv run budget config verifier` valide un `contexte.yaml`, et `tests/fixtures/` contient les extractions de juin et juillet anonymisées, garanties sans donnée personnelle par un test.

## Références cadrage

- §3 Sources de données (formats de montants rencontrés)
- §4 Modèle de classement (natures de flux, catégories à deux niveaux, colonne Fixe/Pilotable)
- §9 Architecture technique (stack, modèle de données)
- §10 Fichier de contexte (structure YAML)
- §11 Contrôles, tests et confidentialité

## Périmètre

**Inclus**

- Arborescence du repo, `pyproject.toml`, `.gitignore`, `.env.example`, `README.md` court.
- `errors.py`, `montants.py`, `dates.py`, `categories.py`, `models.py`, `config.py`, `cli.py`.
- `contexte.example.yaml` versionné (sans aucune donnée personnelle).
- `scripts/anonymiser.py` et les fixtures `tests/fixtures/2026-06/` et `tests/fixtures/2026-07/`.
- Tests unitaires de tout ce qui précède, et garde-fou d'anonymat des fixtures.

**Exclus**

- Parsers des relevés (étapes 2 et 3), ledger SQLite et modèle `Transaction` (étape 4), classement (étapes 5 et suivantes).

## Arborescence cible

```
budget-tracker/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .gitignore
├── .env.example
├── contexte.example.yaml
├── docs/
│   ├── cadrage/
│   └── specs/
├── scripts/
│   └── anonymiser.py
├── src/budget/
│   ├── __init__.py          # __version__
│   ├── cli.py
│   ├── errors.py
│   ├── montants.py
│   ├── dates.py
│   ├── categories.py
│   ├── models.py
│   ├── config.py
│   └── parsers/
│       └── __init__.py      # vide à cette étape
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── 2026-06/         # CA_CB_202606.csv, CA_CPTE_JOINT_202606.csv, TR_202606.pdf
│   │   └── 2026-07/
│   ├── test_montants.py
│   ├── test_dates.py
│   ├── test_categories.py
│   ├── test_config.py
│   ├── test_cli.py
│   └── test_fixtures_anonymes.py
└── data/                    # hors git
```

`.gitignore` contient au minimum : `data/`, `.env`, `*.sqlite`, `*.sqlite3`, `.venv/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`.

## Dépendances autorisées

| Groupe | Paquets |
| --- | --- |
| Runtime | `pydantic` (v2), `pyyaml`, `typer`, `rich` |
| Dev | `pytest`, `ruff`, `mypy`, `types-PyYAML`, `pymupdf` (script d'anonymisation PDF et garde-fou d'anonymat uniquement) |

`pdfplumber` arrive à l'étape 3, `pydantic-ai` à l'étape 7. Ne pas les ajouter maintenant.

Configuration attendue dans `pyproject.toml` :

- `[project.scripts] budget = "budget.cli:app"`
- `[tool.mypy] strict = true`, `plugins = ["pydantic.mypy"]`
- `[tool.ruff] line-length = 100`, règles `E`, `F`, `I`, `UP`, `B`, `SIM`
- `[tool.pytest.ini_options] markers = ["donnees_reelles: tests exécutés sur data/raw, ignorés si absent"]`

## Conception

### `errors.py`

```python
class BudgetError(Exception): ...
class ParseError(BudgetError): ...      # fichier illisible ou format inattendu
class ControleError(BudgetError): ...   # contrôle de cohérence en échec
class ConfigError(BudgetError): ...     # contexte.yaml invalide
```

`ParseError` accepte `fichier: str`, `position: str | None` (ex. `"ligne 42"`, `"page 3, ligne 7"`) et un message ; son `__str__` les combine.

### `montants.py`

- `parse_montant(texte: str) -> Decimal` : renvoie un `Decimal` quantifié à 2 décimales ; lève `ParseError` si le texte est vide ou invalide.
- `parse_montant_optionnel(texte: str) -> Decimal | None` : `None` si le texte est vide après nettoyage, sinon comme `parse_montant`.
- `formater_montant(montant: Decimal) -> str` : format français, espace simple (U+0020) comme séparateur de milliers, virgule décimale, suffixe ` €`. Exemple : `Decimal("-3116.5")` → `"-3 116,50 €"`.

Règles de `parse_montant` :

1. Retirer `€`, les espaces (U+0020), espaces insécables (U+00A0) et espaces fines insécables (U+202F).
2. Accepter un signe `-` initial facultatif.
3. Partie entière : chiffres uniquement (les espaces de milliers ont été retirés).
4. Séparateur décimal facultatif : `,` ou `.`, suivi de 1 ou 2 chiffres. Aucun autre point ni virgule n'est admis.
5. Tout autre format lève `ParseError` : ne jamais deviner.

| Entrée | Sortie |
| --- | --- |
| `"550"` | `Decimal("550.00")` |
| `"4,8"` | `Decimal("4.80")` |
| `"1 071,04"` | `Decimal("1071.04")` |
| `"3 116,50"` | `Decimal("3116.50")` |
| `"-3 116.50 €"` | `Decimal("-3116.50")` |
| `"1983,76 €"` | `Decimal("1983.76")` |
| `"€1238,73"` | `Decimal("1238.73")` |
| `"1\u00a0071,04"` | `Decimal("1071.04")` |
| `""` | `ParseError` (et `None` avec `parse_montant_optionnel`) |
| `"1.071,04"` | `ParseError` |
| `"12,5,0"` | `ParseError` |
| `"abc"` | `ParseError` |

### `dates.py`

- `parse_date_fr(texte: str) -> date` accepte `JJ/MM/AAAA`, `JJ/MM/AA` (siècle 2000), et `J[J] <mois> AAAA` avec le mois en toutes lettres, insensible à la casse et aux accents (`juin`, `août`/`aout`, `février`/`fevrier`, `décembre`/`decembre`…). Tout autre format lève `ParseError`.
- `mois_budgetaire(d: date) -> str` renvoie `"AAAA-MM"`.
- `dernier_jour_du_mois(annee: int, mois: int) -> date`.

### `categories.py`

Taxonomie fermée à deux niveaux (cadrage §4), portée par deux énumérations `StrEnum` et une table de métadonnées.

- `Categorie` : 11 valeurs (`logement`, `enfants`, `impots`, `abonnements`, `courses`, `restos`, `loisirs`, `achats`, `transport`, `sante`, `cadeaux`), chacune avec un libellé d'affichage (`"Restos & commandes"`, `"Cadeaux & dons"`…).
- `SousCategorie` : 38 valeurs au format `<categorie>.<sous_categorie>`. La catégorie parente se déduit du préfixe.
- `NatureDepense` : `FIXE` ou `PILOTABLE`.
- `categorie_de(sc: SousCategorie) -> Categorie`, `nature_de(sc: SousCategorie) -> NatureDepense`, `libelle_de(sc: SousCategorie) -> str`.

| Sous-catégorie | Nature |
| --- | --- |
| `logement.pret`, `logement.assurance_emprunteur`, `logement.copropriete`, `logement.assurance_habitation` | Fixe |
| `logement.travaux_bricolage` | Pilotable |
| `enfants.garde` | Fixe |
| `enfants.vetements_equipement`, `enfants.activites` | Pilotable |
| `impots.impot_revenu`, `impots.taxe_fonciere` | Fixe |
| `abonnements.energie`, `abonnements.internet_mobile`, `abonnements.streaming_musique`, `abonnements.logiciels_ia`, `abonnements.frais_bancaires` | Fixe |
| `courses.supermarche`, `courses.commerces_bouche` | Pilotable |
| `restos.restaurant`, `restos.livraison`, `restos.cafes_snacks` | Pilotable |
| `loisirs.sorties`, `loisirs.sport`, `loisirs.jeux_apps`, `loisirs.voyages` | Pilotable |
| `achats.vetements`, `achats.maison_deco`, `achats.high_tech`, `achats.divers` | Pilotable |
| `transport.transports_commun` | Fixe |
| `transport.carburant`, `transport.parking_peages`, `transport.entretien_vehicule`, `transport.train_avion` | Pilotable |
| `sante.pharmacie`, `sante.consultations`, `sante.optique` | Pilotable |
| `cadeaux.cadeaux`, `cadeaux.dons` | Pilotable |

La répartition des catégories « Mixte » du cadrage est une proposition : elle est centralisée dans cette table pour pouvoir être ajustée sans toucher au reste du code.

### `models.py`

Énumérations :

- `Source` : `CA_CB = "ca_cb"`, `CA_JOINT = "ca_joint"`, `TR = "tr"`.
- `Compte` : `JOINT = "joint"`, `PERSO = "perso"`.
- `Nature` : les 10 natures de flux du cadrage §4 (`depense`, `revenu`, `dotation`, `provision`, `epargne`, `complement`, `lien_depense`, `remboursement`, `hors_budget`, `neutralise`). Déclarée maintenant, utilisée à partir de l'étape 5.

Modèle `OperationBrute` : sortie commune des parsers, avant tout classement. Immuable, `extra="forbid"`.

| Champ | Type | Description |
| --- | --- | --- |
| `source` | `Source` | Fichier d'origine |
| `compte` | `Compte` | `JOINT` pour les deux CSV CA, `PERSO` pour TR |
| `fichier` | `str` | Nom du fichier source (sans chemin) |
| `position` | `int` | Rang de l'opération dans le fichier, à partir de 1 |
| `date_operation` | `date` | Date d'achat ou d'opération |
| `date_debit` | `date \| None` | Date de débit effectif (cartes à débit différé), sinon `None` |
| `type_operation` | `str \| None` | 1re ligne du libellé CA joint, colonne Type TR, `None` pour les cartes |
| `libelle` | `str` | Libellé principal sur une ligne (voir specs 02 et 03) |
| `libelle_brut` | `str` | Libellé complet tel que lu, retours à la ligne conservés |
| `ics` | `str \| None` | Identifiant créancier SEPA, pour les prélèvements |
| `carte` | `str \| None` | `"X"` suivi des 4 derniers chiffres, ex. `"X1091"` |
| `montant` | `Decimal` | Signé : négatif = sortie |
| `solde_apres` | `Decimal \| None` | Solde après opération (TR uniquement) |

Validateurs : `montant` non nul ; `carte` au format `^X\d{4}$` si présente.

### `config.py`

Modèles Pydantic reproduisant la structure du cadrage §10, tous en `extra="forbid"`, et `charger_contexte(chemin: Path) -> Contexte`.

Deux écarts volontaires par rapport au template du cadrage :

- Les clés nominatives sont neutralisées : `comptes.perso` (au lieu de la clé nominative du template) et `enveloppes.perso`.
- `llm.modele_synthese` vaut par défaut `"anthropic:claude-sonnet-5"`, conformément au §9 mis à jour (le template du §10 indiquait encore Haiku).

Exigences :

- **Aucun float** : le YAML est chargé avec un `SafeLoader` dérivé dont le constructeur de nombres décimaux produit un `Decimal` à partir du texte source. `1787.46` doit donner exactement `Decimal("1787.46")`, et `15.50` doit donner `Decimal("15.50")`.
- Les sous-catégories sont typées `SousCategorie` : une valeur inconnue est une erreur.
- Les cartes respectent `^X\d{4}$`.
- `periode_regex` doit compiler et contenir exactement un groupe capturant.
- Une charge fixe ou un paiement financé par la provision doit définir au moins un critère parmi `ics`, `libelle_contient`, `type_operation`.
- `fenetre_salaire.du_jour` et `au_jour_mois_suivant` sont entre 1 et 31.
- Toute erreur de validation est convertie en `ConfigError` listant chaque problème avec son chemin YAML (ex. `charges_fixes[3].sous_categorie`).

### `contexte.example.yaml`

Reprend intégralement la structure du cadrage §10 avec des valeurs fictives : aucun nom, prénom, IBAN, numéro de compte ou de prêt. Les libellés de virement utilisent des marqueurs (`"VIR INST vers PRENOM NOM - Mensuel"`). Les identifiants créanciers SEPA des entreprises et les montants peuvent être conservés. Ce fichier doit passer `budget config verifier`.

### `cli.py`

Application Typer `app` :

- `budget --version` affiche la version.
- `budget config verifier [--chemin PATH]` (défaut `data/contexte.yaml`) : en cas de succès, affiche un résumé avec Rich (nombre de salaires, dotations, charges fixes, total mensuel des charges fixes attendues) et renvoie le code 0. En cas d'échec, affiche la liste des erreurs et renvoie le code 1, sans trace Python.

### Jeux de test anonymisés

Procédure détaillée dans le skill `fixtures-anonymisees`. Exigences :

- `scripts/anonymiser.py --mois AAAA-MM` lit `data/raw/AAAA-MM/` et `data/anonymisation.yaml`, puis écrit `tests/fixtures/AAAA-MM/` avec les mêmes noms de fichiers.
- **CSV** : même encodage (cp1252), mêmes séparateurs et fins de ligne, mêmes montants et dates. Remplacements : table de correspondance exacte (insensible à la casse), IBAN français (`FR\d{12}[0-9A-Z]{11}\d{2}`) remplacés par un IBAN fictif de même format, numéros de compte à 11 chiffres, et séquences de 6 chiffres ou plus dans les lignes de référence des libellés du joint (lignes 3 et suivantes), remplacées de façon déterministe par des chiffres de même longueur.
- **Conservés tels quels** : libellés marchands, identifiants créanciers SEPA (ex. `FR41ZZZ272230`, qui ne correspond pas au motif d'IBAN), montants, dates, numéros de carte masqués (`5137 81XX XXXX 1091`, `X1091`).
- **PDF** : caviardage avec PyMuPDF (`page.search_for`, `page.add_redact_annot`, `page.apply_redactions`), métadonnées effacées, puis sauvegarde. Les montants et leur position ne doivent pas bouger.
- Le script affiche un rapport de revue : toutes les lignes de virement, chèque et remise après anonymisation, pour relecture humaine avant commit.

## Critères d'acceptation

- [x] `uv sync` puis `uv run budget --help` fonctionnent sur un clone vierge.
- [x] `parse_montant` et `parse_montant_optionnel` passent tous les cas du tableau ci-dessus.
- [x] `parse_date_fr` gère `17/06/2026`, `18/06/26`, `30 juin 2026`, `1 août 2026`, `01 aout 2026`, `12 Décembre 2026`, et rejette `2026-06-17`.
- [x] `categories.py` expose 11 catégories et 38 sous-catégories ; chaque sous-catégorie a une catégorie parente valide, une nature et un libellé.
- [x] `contexte.example.yaml` passe `budget config verifier` (code 0).
- [x] Un YAML avec une clé inconnue, une sous-catégorie inexistante, une carte `1091` sans `X` ou une regex à deux groupes échoue avec un message citant le chemin YAML (code 1).
- [x] Les montants YAML sont des `Decimal` exacts (test sur `1787.46` et `15.50`).
- [x] `tests/fixtures/2026-06/` et `tests/fixtures/2026-07/` contiennent chacun les trois fichiers anonymisés.
- [x] Sur les fixtures de juin, les totaux sont inchangés par rapport aux fichiers réels : 3 116,50 € et 410,67 € nets pour les deux cartes, 10 127,46 € de débits et 12 102,56 € de crédits sur le joint (vérifiés par un test léger, sans attendre les parsers : somme des colonnes CSV).
- [x] `test_fixtures_anonymes.py` passe : aucun IBAN français, aucune séquence de 11 chiffres, et, si `data/anonymisation.yaml` est présent localement, aucune des valeurs à remplacer, dans le texte des CSV (décodés en cp1252) et des PDF (texte extrait avec PyMuPDF).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` et `uv run pytest` passent.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_montants.py` | Tableau des cas, `formater_montant` (négatif, milliers, arrondi) |
| `test_dates.py` | Formats acceptés et rejetés, `mois_budgetaire`, `dernier_jour_du_mois` (février bissextile) |
| `test_categories.py` | Cardinalités, cohérence préfixe/catégorie, nature et libellé définis pour toutes les valeurs |
| `test_config.py` | Chargement de l'exemple, cas d'erreur, `Decimal` exacts |
| `test_cli.py` | `--version`, `config verifier` en succès et en échec (via `typer.testing.CliRunner`) |
| `test_fixtures_anonymes.py` | Garde-fou d'anonymat, totaux de juin conservés |

`conftest.py` fournit `chemin_fixtures(mois: str) -> Path` et `chemin_donnees_reelles(mois: str) -> Path`, ce dernier faisant `pytest.skip` si le dossier n'existe pas.

## Definition of Done

- Tous les critères d'acceptation sont cochés.
- `git status` ne montre aucun fichier de `data/`.
- Le rapport de revue de l'anonymisation a été relu par l'utilisateur avant le premier commit des fixtures.
- Cette spec est mise à jour si un écart a été constaté (section ci-dessous).

## Écarts constatés

- **Emplacements** : le cadrage est dans `docs/cadrage.md` et les specs dans `.claude/plan/` (et non `docs/cadrage/` et `docs/specs/`). Les skills cités (`implementer-spec`, `fixtures-anonymisees`, etc.) n'existent pas encore ; la procédure d'anonymisation est documentée dans la docstring de `scripts/anonymiser.py`.
- **Données d'entrée** : les extractions étaient déposées dans `fixtures/` à la racine. Elles ont été déplacées dans `data/raw/AAAA-MM/`, et `CA_CPTE_202607.csv` a été renommé `CA_CPTE_JOINT_202607.csv`.
- **Poste de développement** : uv a été installé avec `pip install --user uv`. Derrière l'inspection TLS du poste, les commandes qui téléchargent (`uv sync`, premier `uv run`) demandent `--system-certs`.
- **Contradiction « même longueur » / « aucune séquence de 11 chiffres »** (décision utilisateur) : une séquence remplacée de 11 chiffres ou plus est masquée en `X` de même longueur ; plus courte, elle devient une suite de chiffres déterministe dérivée d'une graine secrète (`graine` dans `data/anonymisation.yaml`). Conséquence : les deux numéros de prêt deviennent identiques (`XXXXXXXXXXX`) et se distinguent par leur montant.
- **Numéros en ligne 2 des libellés du joint** : prêts, contrat, références PayPal, Free, Direct Assurance, Pajemploi et DGFIP y figurent ; la règle « 6 chiffres ou plus en lignes 3 et suivantes » ne les couvrait pas. Les séquences de 10 chiffres ou plus sont donc remplacées partout, ainsi que la ligne 2 réduite à un numéro (chèque, remise).
- **ICS et garde-fou** : `LU96ZZZ0000000000000000058` contient 22 chiffres consécutifs. Les jetons au format ICS sont exclus du contrôle « 11 chiffres », dans le script comme dans le test.
- **IBAN fictif** : un IBAN fictif « de même format » serait détecté par le garde-fou. Les IBAN fictifs portent la clé `00` (`FR00…`), impossible pour un IBAN réel, et le test les ignore. Les CSV de juin et juillet ne contiennent d'ailleurs aucun IBAN.
- **PDF** : les jetons de 11 chiffres ou plus hors ICS sont aussi caviardés (une référence de terminal « INDIGO NE … » en juillet). Le script vérifie qu'aucun mot hors des zones caviardées n'a bougé, et un test `donnees_reelles` compare montants et positions avec les PDF réels.
- **Mois abrégés** : le relevé TR de juillet écrit `08 juil. 2026`. `parse_date_fr` accepte les abréviations usuelles (`janv.`, `févr.`, `avr.`, `juil.`, `sept.`, `oct.`, `nov.`, `déc.`), nécessaires à la spec 03.
- **Nombre d'opérations du joint de juin** : 37 (le cadrage §3 indique 38 ; la spec 02 confirme 37).
- **`ParseError`** : `fichier` et `position` sont des arguments nommés facultatifs, car `parse_montant` et `parse_date_fr` ne connaissent pas le fichier ; les parsers les renseigneront.
- **Ruff** : ruff 0.16 formate aussi les blocs de code des fichiers Markdown ; `*.md` est exclu (`extend-exclude`) pour protéger les specs.
- **Revue humaine en attente** : la ligne 2 DGFIP de juillet conserve `1P08700` et `ACI011/260276053` (moins de 10 chiffres) ; à confirmer lors de la relecture du rapport.
