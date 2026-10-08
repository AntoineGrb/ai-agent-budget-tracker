# Spec 04 — Ledger SQLite, dédoublonnage, mois budgétaire et rattachement des salaires

| Champ | Valeur |
| --- | --- |
| Étape du plan | 4 / 10 |
| Type | Code (Claude Code) |
| Complexité | Simple |
| Estimation | 1 session |
| Prérequis | Specs 02 et 03 terminées (les trois parsers et leurs contrôles bloquants) |

## Objectif

Importer un mois complet (les trois extractions) dans un ledger SQLite unique, de façon atomique et idempotente : un réimport n'a aucun effet (RG-16). Chaque opération y devient une `Transaction` portant un identifiant stable et son mois budgétaire (RG-01). Fournir la fonction de rattachement des salaires à leur mois de paie (RG-04), utilisée par le classement de l'étape 5.

À la fin de l'étape, `uv run budget import --mois 2026-06` charge les 159 opérations de juin dans `data/budget.sqlite`, et une seconde exécution n'ajoute rien.

## Références cadrage

- §3 Sources de données (complétude : un mois M n'est traitable qu'à partir du 1er de M+1)
- §9 Architecture technique (stockage SQLite, modèle `Transaction`, commande `import`)
- §11 Contrôles bloquants
- RG-01 (mois budgétaire = mois de débit), RG-02 (trois extractions et contrôles passés), RG-04 (mois de paie des salaires), RG-16 (clé de dédoublonnage)

## Périmètre

**Inclus**

- `libelles.py` : normalisation typographique des libellés.
- `montants.py` : conversion exacte `Decimal` ↔ centimes pour le stockage.
- `models.py` : modèle `Transaction`.
- `ledger/` : ouverture de la base, mécanisme de migrations, migration `0001`, écriture et lecture des transactions.
- `importation.py` : orchestration de l'import d'un mois (RG-02, parsers, contrôles, RG-01, RG-16).
- `salaires.py` : fonction pure RG-04.
- Commande `budget import`.

**Exclus**

- Natures de flux, neutralisations, classement (étape 5). Les lignes de débit différé sont importées comme les autres.
- Persistance du mois de paie : il dépend du fichier de contexte, qui peut évoluer après l'import. Il est recalculé à chaque classement et stocké avec lui (étape 5).
- Règles, questions, décisions (étapes 6 et 7). Commande `budget run` (étape 10).

## Principe : le ledger ne contient que des faits

La table `transactions` ne stocke que ce qui est lu dans les fichiers, plus deux valeurs dérivées sans dépendance au contexte : l'identifiant (RG-16) et le mois budgétaire (RG-01). Tout ce qui dépend de `contexte.yaml`, des règles apprises ou des réponses de l'utilisateur est une projection recalculée par `budget classify` (étapes 5 et suivantes).

Conséquence : modifier le contexte ne demande jamais de réimporter un mois.

## Conception

### `libelles.py`

`normaliser_libelle(texte: str) -> str`, purement typographique :

1. Décomposition Unicode (NFKD) et suppression des diacritiques.
2. Passage en majuscules.
3. Remplacement de tout blanc (espace, U+00A0, U+202F, tabulation, retour à la ligne) par une espace, puis réduction des espaces multiples à une seule.
4. Suppression des espaces de bord.

La ponctuation et les chiffres sont conservés.

| Entrée | Sortie |
| --- | --- |
| `"UBER   *EATS HELP.UBER.COM"` | `"UBER *EATS HELP.UBER.COM"` |
| `"Prélèvement carte"` | `"PRELEVEMENT CARTE"` |
| `"MONOPRIX  1357 STMAU2042885/"` | `"MONOPRIX 1357 STMAU2042885/"` |
| `" Cotisation carte "` | `"COTISATION CARTE"` |

L'extraction d'une clé marchand (suppression des numéros de magasin, des villes) n'est pas l'objet de cette fonction : elle relève de l'étape 7.

### `montants.py` (ajouts)

- `en_centimes(montant: Decimal) -> int` : `Decimal("-3116.50")` → `-311650`. Lève `ValueError` si le montant a plus de deux décimales significatives : jamais d'arrondi silencieux.
- `depuis_centimes(centimes: int) -> Decimal` : renvoie un `Decimal` quantifié à 2 décimales.

Les montants sont stockés en **entiers de centimes** : aucun `float` ne peut apparaître, y compris dans un `SUM` SQL.

### Modèle `Transaction` (`models.py`)

Immuable, `extra="forbid"`. Construit à partir d'une `OperationBrute`.

| Champ | Type | Description |
| --- | --- | --- |
| `id` | `str` | Clé de dédoublonnage (RG-16), 16 caractères hexadécimaux |
| `source`, `compte`, `fichier`, `position` | — | Repris de l'`OperationBrute` |
| `rang` | `int` (≥ 1) | Rang d'occurrence parmi les opérations identiques du même fichier (RG-16) |
| `date_operation` | `date` | Date d'achat ou d'opération (conservée, RG-01) |
| `date_debit` | `date \| None` | Date de débit des cartes |
| `mois_budgetaire` | `str` (`^\d{4}-\d{2}$`) | RG-01 |
| `type_operation`, `libelle`, `libelle_brut`, `ics`, `carte` | — | Repris de l'`OperationBrute` |
| `libelle_normalise` | `str` | `normaliser_libelle(libelle)` |
| `montant` | `Decimal` | Signé, négatif = sortie |
| `solde_apres` | `Decimal \| None` | TR uniquement |

Les champs de classement du modèle du cadrage §9 (`nature`, `sous_categorie`, `exceptionnel`, `lien_id`) n'y figurent pas : ils appartiennent à la projection `classements` de l'étape 5 (voir le principe ci-dessus).

### RG-01 : mois budgétaire

- Source `CA_CB` : mois de `date_debit`. Un achat du 23/05/2026 débité le 30/06/2026 compte pour `2026-06`.
- Sources `CA_JOINT` et `TR` : mois de `date_operation`.

### RG-16 : identifiant et rang

Pour les opérations d'un même fichier, dans l'ordre de `position` :

1. `cle = (source, date_operation, montant, libelle_normalise)`.
2. `rang` = nombre d'opérations déjà rencontrées avec la même `cle`, plus 1.
3. `id = sha256(f"{source}|{date_operation:%Y-%m-%d}|{montant}|{libelle_normalise}|{rang}")`, 16 premiers caractères hexadécimaux. `montant` est écrit avec deux décimales et son signe (`-6.80`).

La carte ne fait pas partie de la clé (cadrage RG-16) : deux achats identiques sur deux cartes du même fichier reçoivent les rangs 1 et 2, ce qui reste unique.

### Ledger (`ledger/`)

```
src/budget/ledger/
├── __init__.py      # expose Ledger
├── migrations.py    # liste ordonnée des migrations, application
└── depot.py         # classe Ledger : import, lecture
```

**Connexion** : module `sqlite3` de la bibliothèque standard, `PRAGMA foreign_keys = ON`. Aucun adaptateur implicite (`detect_types` non utilisé) : les dates sont écrites et relues explicitement en ISO `AAAA-MM-JJ`, les montants via `en_centimes` / `depuis_centimes`.

**Migrations** : `migrations.py` déclare un tuple de `Migration(numero: int, description: str, sql: str)`. La version du schéma est `PRAGMA user_version`. À l'ouverture, les migrations de numéro supérieur sont appliquées dans l'ordre, chacune dans sa propre transaction avec la mise à jour de `user_version`. Une base dont `user_version` dépasse la dernière migration connue lève `LedgerError` (base créée par une version plus récente du code). À partir de cette étape, **toute évolution du schéma passe par une nouvelle migration** ; une migration publiée n'est jamais modifiée.

**Migration 0001** :

```sql
CREATE TABLE imports (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source IN ('ca_cb', 'ca_joint', 'tr')),
    mois            TEXT NOT NULL,            -- AAAA-MM
    fichier         TEXT NOT NULL,
    empreinte       TEXT NOT NULL,            -- sha256 du fichier, informatif
    nb_operations   INTEGER NOT NULL,
    importe_le      TEXT NOT NULL,            -- horodatage ISO 8601
    UNIQUE (source, mois)
);

CREATE TABLE transactions (
    id                    TEXT PRIMARY KEY,   -- RG-16
    import_id             INTEGER NOT NULL REFERENCES imports (id),
    source                TEXT NOT NULL,
    compte                TEXT NOT NULL CHECK (compte IN ('joint', 'perso')),
    fichier               TEXT NOT NULL,
    position              INTEGER NOT NULL,
    rang                  INTEGER NOT NULL CHECK (rang >= 1),
    date_operation        TEXT NOT NULL,
    date_debit            TEXT,
    mois_budgetaire       TEXT NOT NULL,      -- RG-01
    type_operation        TEXT,
    libelle               TEXT NOT NULL,
    libelle_brut          TEXT NOT NULL,
    libelle_normalise     TEXT NOT NULL,
    ics                   TEXT,
    carte                 TEXT,
    montant_centimes      INTEGER NOT NULL CHECK (montant_centimes <> 0),
    solde_apres_centimes  INTEGER
);

CREATE INDEX transactions_mois ON transactions (mois_budgetaire);
```

**API** :

- `Ledger(chemin: Path)` : ouvre (ou crée) la base et applique les migrations. Utilisable comme gestionnaire de contexte (`with Ledger(...) as ledger:`).
- `ledger.enregistrer_import(mois, fichiers: Sequence[FichierImporte]) -> RapportImport` : écrit les imports et transactions d'un mois **dans une seule transaction SQL** (tout ou rien).
- `ledger.transactions(mois: str | None = None, source: Source | None = None) -> list[Transaction]`, triées par source puis position.
- `ledger.mois_importes() -> list[str]`.

`FichierImporte` regroupe `source`, `fichier`, `empreinte` et la liste de `Transaction`. `RapportImport` donne, par source, le nombre d'opérations lues, ajoutées et déjà présentes.

### Idempotence (RG-16)

Pour chaque source du mois à importer :

| Situation dans le ledger | Effet |
| --- | --- |
| Aucun import `(source, mois)` | Insertion de l'import et de ses transactions |
| Import existant, ensemble des `id` identique | Aucun effet ; rapporté comme « déjà importé ». L'empreinte peut différer : un export retéléchargé change sa ligne « Téléchargement du … » sans changer ses opérations |
| Import existant, ensemble des `id` différent | `ControleError` : nombre d'opérations ajoutées et disparues, date du premier import. Rien n'est écrit |
| Un `id` du fichier existe déjà sous un autre mois | `ControleError` : le fichier a probablement été déposé dans le mauvais dossier |

Remplacer un mois déjà importé par un contenu différent n'est pas prévu au MVP (voir points ouverts).

### Import d'un mois (`importation.py`)

`importer_mois(mois: str, dossier: Path, ledger: Ledger, aujourd_hui: date) -> RapportImport`, dans cet ordre :

1. **Complétude (cadrage §3)** : si `aujourd_hui` est antérieur au 1er du mois suivant, `ControleError` (« le mois 2026-06 ne pourra être importé qu'à partir du 01/07/2026 »).
2. **Présence (RG-02)** : `dossier/AAAA-MM/` doit contenir `CA_CB_AAAAMM.csv`, `CA_CPTE_JOINT_AAAAMM.csv` et `TR_AAAAMM.pdf`. Sinon, `ControleError` listant **tous** les fichiers manquants.
3. **Parsing** des trois fichiers (contrôles internes inclus : spec 02, spec 03).
4. **Cohérence de période** : la période du joint et celle du relevé TR sont exactement le mois demandé ; chaque `date_debit` de carte tombe dans ce mois. Sinon, `ControleError` citant le fichier et la période trouvée.
5. **RG-03** : `verifier_debit_differe(cartes, joint)`.
6. **Construction** des `Transaction` (RG-01, RG-16), fichier par fichier.
7. **Écriture** atomique dans le ledger, avec les règles d'idempotence ci-dessus.

Une erreur à n'importe quelle étape laisse la base inchangée. `aujourd_hui` est un paramètre pour rendre les tests déterministes ; la CLI passe `date.today()`.

### RG-04 : mois de paie (`salaires.py`)

```python
class RattachementSalaire(BaseModel):   # frozen, extra="forbid"
    nom: str                             # nom du salaire dans le contexte
    mois_paie: str                       # "AAAA-MM"

def rattacher_salaire(transaction: Transaction, contexte: Contexte) -> RattachementSalaire | None: ...
```

1. Seules les transactions `CA_JOINT` de montant positif sont candidates.
2. Un salaire du contexte correspond si `normaliser_libelle(libelle_contient)` est contenu dans `libelle_normalise`. Plus d'un salaire correspondant : `ControleError`. Aucun : `None`.
3. Si le salaire a un `periode_regex`, il est appliqué sur `libelle` (non normalisé, puisque l'utilisateur écrit l'expression d'après le libellé de la banque). Le groupe capturé `MM/AAAA` donne le mois de paie. Une correspondance dont le mois est invalide (`13/2026`) lève `ControleError`. Pas de correspondance : on passe à l'étape 4.
4. Sinon, fenêtre `fenetre_salaire` : reçu entre le `du_jour` et la fin du mois M, mois de paie M ; reçu entre le 1er et le `au_jour_mois_suivant` du mois M+1, mois de paie M.
5. Reçu hors fenêtre et sans période lisible : `ControleError` citant la date, le montant et le nom du salaire. Ne jamais deviner (voir points ouverts).

La fonction est pure et testée ici ; son résultat est stocké dans la projection de classement à l'étape 5.

### Erreurs

Ajout dans `errors.py` : `LedgerError(BudgetError)` pour les anomalies de la base (version de schéma inconnue, base illisible). Les messages respectent les règles du projet : position, date et montant, jamais de libellé complet.

### CLI

`budget import --mois AAAA-MM [--dossier data/raw] [--base data/budget.sqlite]` :

- Succès : tableau Rich par source (fichier, opérations lues, ajoutées, déjà présentes), puis le total du mois. Code 0.
- Mois déjà importé à l'identique : même tableau, avec 0 ajout. Code 0.
- `BudgetError` : message en rouge, sans trace Python. Code 1.

`--mois` est validé au format `AAAA-MM`.

## Valeurs attendues sur les fixtures

| Élément | Juin 2026 | Juillet 2026 |
| --- | --- | --- |
| Transactions `ca_cb` | 48 | 86 |
| Transactions `ca_joint` | 37 | 28 |
| Transactions `tr` | 74 | Valeur de la spec 03, figée à sa première exécution |
| Total juin | 159 | — |
| RG-01 | `GUOFA 2 ST MAUR DES FOSS`, achat du 23/05/2026, `mois_budgetaire = "2026-06"` | `PICARD SA NEMOURS`, achat du 15/06/2026, `mois_budgetaire = "2026-07"` |
| RG-16, rang 2 | `VARENNE CAFE` (TR), 24/06/2026, −6,80 € : rangs 1 et 2 ; celui du 25/06 a le rang 1 | — |
| RG-16, rang 2 | `RBCPJ PARIS` (carte X1091), 30/05/2026, −24,00 € : rangs 1 et 2 | — |
| Transactions de rang 2 | 2 | — |
| Réimport | 0 ajout | 0 ajout |

**RG-04 sur les fixtures** (avec `contexte.example.yaml`) :

| Transaction | Mode | `mois_paie` |
| --- | --- | --- |
| Juin, `PLUME … - 05/2026 NOM2S`, reçu le 01/06, +2 303,31 € | Période du libellé | `2026-05` |
| Juin, `PLUME … - 06/2026 NOM2S`, reçu le 29/06, +2 526,42 € | Période du libellé | `2026-06` |
| Juin, `GROUPAMA GAN VIE VIRT.APTS`, reçu le 29/06, +2 729,05 € | Fenêtre | `2026-06` |
| Juillet, `GROUPAMA GAN VIE VIRT.APTS`, reçu le 28/07, +2 726,58 € | Fenêtre | `2026-07` |

Soit 3 salaires en juin, dont 5 255,47 € rattachés au mois de paie de juin, et 1 en juillet. Le salaire `PLUME` de juillet n'est pas dans l'extraction de juillet : il arrivera début août avec la période `07/2026`.

## Dépendances autorisées

Aucune nouvelle dépendance : `sqlite3`, `hashlib`, `unicodedata` (bibliothèque standard) et le socle des étapes précédentes.

## Critères d'acceptation

- [ ] `budget import --mois 2026-06 --dossier tests/fixtures` (avec une base dans `tmp_path`) importe 159 transactions ; une seconde exécution en ajoute 0.
- [ ] Le même import dans deux bases distinctes produit exactement les mêmes `id`.
- [ ] RG-16 : les cas `VARENNE CAFE` et `RBCPJ PARIS` reçoivent les rangs 1 et 2 ; la base n'a aucun `id` en double.
- [ ] RG-16 : un réimport d'un fichier modifié (une ligne retirée, en mémoire puis dans `tmp_path`) lève `ControleError` et laisse la base inchangée.
- [ ] RG-01 : le mois budgétaire des achats carte est celui du débit (cas de juin et de juillet ci-dessus).
- [ ] RG-02 : un dossier sans le PDF TR lève `ControleError` citant le fichier manquant ; un dossier vide les cite tous les trois.
- [ ] Complétude : importer `2026-06` avec `aujourd_hui = 2026-06-30` lève `ControleError` ; avec `2026-07-01`, l'import passe.
- [ ] Un fichier joint d'un autre mois déposé dans le dossier lève `ControleError` sur la période ; rien n'est écrit.
- [ ] Un échec de parsing ou de contrôle sur l'un des trois fichiers laisse les tables `imports` et `transactions` vides (atomicité).
- [ ] Migrations : une base neuve est en version 1 ; une réouverture n'applique rien ; une base en version 99 lève `LedgerError`.
- [ ] Aucun `float` en base : les colonnes de montant sont de type `INTEGER` (test sur `typeof()` SQLite), et `en_centimes` refuse `Decimal("1.005")`.
- [ ] RG-04 : les quatre cas du tableau ci-dessus, plus les cas synthétiques : salaire reçu le 03/07 sans période (mois de paie juin), reçu le 15/06 sans période (`ControleError`), période `13/2026` (`ControleError`), libellé correspondant à deux salaires (`ControleError`).
- [ ] Aucun message d'erreur ni log ne contient de libellé complet.
- [ ] Les tests `donnees_reelles` importent `data/raw/2026-06/` et `data/raw/2026-07/` sans erreur quand ils sont présents.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_libelles.py` | Tableau de normalisation |
| `test_montants.py` (ajouts) | `en_centimes`, `depuis_centimes`, refus de plus de deux décimales |
| `test_ledger_migrations.py` | Base neuve, réouverture, version inconnue |
| `test_dedoublonnage.py` | RG-16 : rangs, stabilité des `id`, réimport identique, réimport modifié, `id` présent sous un autre mois |
| `test_importation.py` | RG-01, RG-02, complétude, cohérence de période, atomicité, valeurs golden de juin et juillet |
| `test_salaires.py` | RG-04 : cas des fixtures et cas synthétiques |
| `test_cli.py` (ajouts) | `budget import` en succès, en réimport et en échec |

`conftest.py` fournit une fixture `ledger_vide(tmp_path) -> Ledger`. Les cas d'erreur sont construits en copiant les fixtures dans `tmp_path` puis en les modifiant, sans ajouter de fichiers de fixtures.

## Points ouverts

- **Salaire hors fenêtre sans période** : le cadrage ne dit pas quoi faire d'un salaire reçu le 10 du mois. La spec choisit l'arrêt (`ControleError`) plutôt qu'un rattachement deviné. Si le cas se produit sur des données réelles, trancher entre une question à l'étape 6 et un rattachement au mois de réception.
- **Remplacement d'un mois** : un export corrigé par la banque ou retéléchargé avec des opérations en plus est refusé. Si le besoin apparaît (historique de l'étape 10), ajouter une option `--remplacer`, interdite dès qu'une décision humaine porte sur une transaction du mois.
- **Historique (étape 10)** : un mois de l'historique dont un fichier manque ne peut pas être importé (RG-02). À réévaluer quand l'historique sera récupéré.

## Écarts constatés

_À compléter pendant l'implémentation._
