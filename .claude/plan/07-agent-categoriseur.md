# Spec 07 — Agent catégoriseur, validation en lot, règles apprises et journal

| Champ | Valeur |
| --- | --- |
| Étape du plan | 7 / 10 |
| Type | Code (Claude Code) |
| Complexité | Moyenne |
| Estimation | 2 sessions |
| Prérequis | Spec 06 terminée. Pour la recette sur données réelles uniquement : `ANTHROPIC_API_KEY` renseignée dans `.env` (les tests n'en ont pas besoin) |

## Objectif

Donner une sous-catégorie à chaque paiement carte (fichier cartes et `Avoir` Trade Republic), dans l'ordre du cadrage §7 : règles apprises, puis proposition du LLM pour les marchands inconnus, puis validation humaine en lot, chaque validation créant une règle. C'est le cœur agentique du projet, et le seul endroit où un libellé part vers un modèle.

L'étape termine aussi ce que la spec 06 a laissé de côté : règles `marchand_carte`, Amazon (RG-13), tags exceptionnels (RG-15), journal `decisions.md`, commandes `budget rules list` et `budget rules disable`. Elle clôt le jalon 1 avec `budget export`.

À la fin de l'étape, après le scénario de la spec 06, `budget classify --mois 2026-06` fait un seul appel au catégoriseur (62 marchands), présente un tableau de 65 lignes que l'on valide avec Entrée, puis propose un tag exceptionnel. Un second passage ne fait ni appel ni question, et `budget export --mois 2026-06` écrit le CSV du mois entièrement classé.

## Références cadrage

- §4 Catégories (taxonomie fermée, nature Fixe ou Pilotable)
- §7 Mémoire et interaction humaine : ordre de classement, type de règle « marchand carte », priorité, pas de score de confiance, déroulé des blocs 2 et 3, stockage et journal
- §9 Agent catégoriseur : un appel par run, énumération fermée, vérification « une proposition par libellé » et relance
- §10 Seuils (`exceptionnel`, `amazon_auto`) et modèles (`llm.modele_categorisation`)
- §11 Confidentialité (seuls les libellés marchands des paiements carte partent au LLM ; masquage des IBAN et des séquences de 6 chiffres ou plus ; un test échoue si un IBAN apparaît dans un prompt) et tests sans appel réseau
- §12 Risque principal : trop de questions au premier run
- §14 Jalon 1
- RG-09 (avoirs marchands), RG-12 (sens d'une opération TR), RG-13 (Amazon), RG-15 (exceptionnels)

## Périmètre

**Inclus**

- Clé marchand (`libelles.cle_marchand`) et groupes marchands.
- Règles `marchand_carte` : validation, correspondance, priorité, création à la validation.
- RG-13 : Amazon sous le seuil classé sans question ; à partir du seuil, saisie dans le tableau de validation.
- Agent catégoriseur PydanticAI v2, masquage, garde-fous de confidentialité, chargement de `.env`.
- Bloc 2 (validation en lot) et bloc 3 (tags exceptionnels, RG-15) du dialogue.
- Migration `0004`.
- Journal `decisions.md`, régénéré à chaque `classify`.
- Commandes `budget rules list`, `budget rules disable` et `budget export` ; options `--sans-llm` et `--journal` de `budget classify`.
- Horloge unique et injectable pour tous les horodatages.

**Exclus**

- Agrégats, contrôles bloquants avant publication, agent synthèse (étape 8).
- Publication Google Sheets (étape 9), `budget run` (étape 10).
- Correction d'une décision déjà enregistrée, réactivation d'une règle, règle marchand saisie à la main avec condition de montant (points ouverts).
- Questions asynchrones par outils différés (phase 2).

## Ordre de classement d'un paiement carte

La règle 8 de la spec 05 donne la nature : `DEPENSE` en sortie, `REMBOURSEMENT` en entrée (RG-09, RG-12). La sous-catégorie est ensuite cherchée dans cet ordre, par une nouvelle étape de `classer_mois` exécutée après `appliquer_memoire` :

| Ordre | Source | Origine, détail |
| --- | --- | --- |
| 1 | Décision enregistrée pour l'opération (ligne RG-13 validée) | `decision`, `RG-13` |
| 2 | RG-13 : marchand Amazon, montant absolu strictement inférieur à `seuils.amazon_auto` | `regle_fixe`, `RG-13`, sous-catégorie `achats.divers` |
| 3 | Règle `marchand_carte` apprise la plus spécifique | `regle`, `regles.<n°>`, `regle_id` renseigné |
| 4 | Aucune : nature conservée, sous-catégorie vide | `regle_fixe`, `paiement_carte` ou `RG-09` (inchangés) |

Un paiement carte du cas 4 est « en attente de catégorisation » : il alimente le bloc 2. La nature n'est jamais modifiée par cette étape. Une règle marchand ne porte qu'une sous-catégorie : un avoir (montant positif) reçoit celle du marchand, ce qui le déduit de la catégorie d'origine (RG-09).

`classer_mois(mois, transactions, contexte, memoire)` reste une fonction pure : règles marchand et décisions arrivent par `Memoire`, les seuils par le contexte.

## Clé marchand (`libelles.py`)

`cle_marchand(libelle_normalise: str) -> str` extrait la partie stable d'un libellé carte, sans numéro de magasin, de terminal ni de transaction :

1. Découper en jetons sur les espaces.
2. Garder le premier jeton, puis les suivants jusqu'au premier jeton (exclu) contenant au moins trois chiffres consécutifs.
3. Retirer les `/` et espaces de fin.
4. Si le résultat est vide, renvoyer le libellé normalisé.

| Libellé normalisé | Clé |
| --- | --- |
| `AMAZON PAYMENTS PARIS2441535/` | `AMAZON PAYMENTS` |
| `AMAZON PAYMENTS 2441535` (TR) | `AMAZON PAYMENTS` |
| `MONOPRIX 1357 STMAU2042885/` | `MONOPRIX` |
| `DECATHLON CRETEI 0008 CRETEIL/` | `DECATHLON CRETEI` |
| `BK K0028 PUTEAU 4819855` | `BK` |
| `ESSO31816ROC1 2131616` | `ESSO31816ROC1` |
| `GUOFA 2 ST MAUR DES FOSS` | `GUOFA 2 ST MAUR DES FOSS` |
| `SUMUP *MES 4 MOMENTS SAINT MAUR DE` | `SUMUP *MES 4 MOMENTS SAINT MAUR DE` |
| `UBER *EATS HELP.UBER.COM` | `UBER *EATS HELP.UBER.COM` |

La ville n'est pas retirée : le format CA ne la sépare pas du nom de façon fiable (`CARREFOUR CITY SAINT MAUR D`). Conséquence assumée : une même enseigne peut donner plusieurs clés (`MONOPRIX` sur les cartes du joint, `MONOP` sur TR), chacune validée une fois.

Un **groupe marchand** réunit les paiements carte en attente d'un mois qui partagent `(source, cle_marchand)`. Les règles marchand sont propres à une source (`ca_cb` ou `tr`), comme les autres règles (spec 06).

## Règles `marchand_carte` (`regles.py`)

**Validation du modèle `Regle`** (ajouts pour ce type) :

- `source` vaut `ca_cb` ou `tr` ;
- `critere` vaut `libelle_exact` ou `libelle_prefixe` (pas d'ICS sur un paiement carte) ;
- `nature` est vide et `sous_categorie` obligatoire : la nature vient du sens de l'opération ;
- la condition de montant reste facultative (la validation en lot n'en crée pas) ;
- une valeur commençant par l'un des `PREFIXES_AMAZON` est refusée (RG-13).

**Correspondance** : comme en spec 06, mais la valeur est comparée à `cle_marchand(transaction.libelle_normalise)`, pas au libellé complet. `regles_applicables` continue d'exclure les règles marchand ; une fonction dédiée `regle_marchand_retenue(regles, transaction)` les évalue (même priorité, même `ControleError` en cas d'égalité contradictoire). Le moteur ne l'appelle que pour les paiements carte, ce qui évite un import circulaire entre `regles.py` et `classement/moteur.py`.

**Priorité et conflits** : inchangés (spec 06) : libellé exact > préfixe, condition de montant > sans condition, préfixe long > court ; refus à la création des conflits et des doublons.

**Création** : la validation d'un groupe crée `Regle(type=MARCHAND_CARTE, source, critere=LIBELLE_EXACT, valeur=cle, sous_categorie=…)`, sans condition de montant. `decrire()` affiche la sous-catégorie comme résultat.

## RG-13 : Amazon

- Marchand Amazon : clé marchand commençant par l'un des `PREFIXES_AMAZON = ("AMAZON", "AMZN")`.
- Montant absolu strictement inférieur à `seuils.amazon_auto` (50 €) : `achats.divers`, origine `regle_fixe`, détail `RG-13`, sans question. Les avoirs Amazon sous le seuil suivent la même règle.
- À partir du seuil : l'opération n'est jamais envoyée au LLM (un libellé Amazon ne dit rien de l'achat) et aucune règle ne la couvre. Elle apparaît dans le tableau du bloc 2 comme une ligne « à compléter », une ligne par opération. Sa validation crée une **décision** : question de type `operation` et de motif `RG-13` (aucune modification du schéma de `questions`), nature selon le sens, sous-catégorie saisie. Jamais une règle.

## Agent catégoriseur (`agents/categoriseur.py`)

**PydanticAI v2** : le skill `pydantic-ai-v2` cité par `CLAUDE.md` n'existe pas encore (spec 01, écarts). Avant d'écrire le code, consulter la documentation v2 (`https://pydantic.dev/docs/ai/`, dont le guide de migration V1 → V2 et la page Testing) et noter dans « Écarts constatés » tout écart avec cette spec. Points vérifiés à la rédaction (v2.0.0 du 23/06/2026) : `Agent`, `output_type`, `instructions`, `deps_type`, `run_sync(...).output`, `ModelRetry`, `TestModel`, `FunctionModel`, `Agent.override`, `models.ALLOW_MODEL_REQUESTS` et `capture_run_messages` existent ; un `Agent` non paramétré est typé `Agent[object, str]` ; `result.usage` devient une propriété ; un nom de modèle sans préfixe de fournisseur lève `UserError`.

**Contrat**

```python
class Proposition(BaseModel):              # frozen, extra="forbid"
    libelle: str                           # clé marchand masquée, recopiée à l'identique
    sous_categorie: SousCategorie          # énumération fermée : rien d'autre n'est possible


@dataclass(frozen=True)
class Soumission:                          # deps de l'agent
    libelles: tuple[str, ...]              # clés masquées, uniques, triées


class Categoriseur(Protocol):
    def proposer(
        self, groupes: Sequence[GroupeMarchand]
    ) -> dict[tuple[Source, str], SousCategorie]: ...
```

- `CategoriseurLLM(modele: str | Model)` implémente le protocole avec l'agent ; la CLI le construit avec `contexte.llm.modele_categorisation`, les tests avec un `FunctionModel`.
- Agent : `output_type=list[Proposition]`, `deps_type=Soumission`, instructions en français construites depuis `categories.py` (une ligne par sous-catégorie : code, catégorie, sous-catégorie, Fixe ou Pilotable) et la consigne « exactement une proposition par libellé fourni, libellé recopié à l'identique ».
- Prompt utilisateur : les libellés masqués, un par ligne. Rien d'autre : ni montant, ni date, ni compte.
- Validateur de sortie (`@agent.output_validator`) : l'ensemble des libellés renvoyés est exactement celui de `Soumission`, sans doublon. Sinon `ModelRetry` listant les libellés manquants, inconnus ou en double. Deux relances au plus (paramètre de relance de sortie de `Agent`, nom à vérifier en v2).
- Réglages du modèle : `max_tokens` suffisant pour un lot (8 000). Ne pas fixer `temperature` : les modèles récents (Haiku 5.5, Sonnet 5.x) refusent les valeurs autres que celle par défaut.
- Lots : un appel par run en régime normal ; au-delà de `TAILLE_LOT_CATEGORISEUR = 100` clés (import de l'historique), un appel par lot de 100.
- Pas de score de confiance (cadrage §7) : la sortie n'en comporte pas.

**Confidentialité (cadrage §11)**

- Le catégoriseur ne reçoit que des `GroupeMarchand`, construits à partir de paiements carte. Avant tout appel, chaque transaction des groupes est revérifiée avec `est_paiement_carte` ; sinon `ControleError` (défense en profondeur).
- `agents/confidentialite.py` : `masquer(texte) -> str` remplace tout IBAN (motif FR et motif international générique) par `[IBAN]`, et toute séquence de 6 chiffres ou plus par autant de `#`. Il est appliqué à chaque libellé avant l'envoi. Deux clés distinctes devenues identiques une fois masquées sont envoyées une seule fois, et la proposition s'applique aux deux.
- `tests/conftest.py` fixe `models.ALLOW_MODEL_REQUESTS = False` pour toute la session : aucun test ne peut appeler un vrai modèle.
- Les logs donnent le nombre de clés envoyées, le modèle, la durée, l'usage en tokens et le nombre de propositions ; jamais une clé.

**Mode dégradé** : sans clé API, avec `--sans-llm`, ou si l'appel échoue (réseau, authentification, sortie invalide après relances), le classement continue. Les groupes apparaissent dans le tableau sans proposition, et un avertissement donne la raison (classe d'erreur, sans contenu). L'utilisateur saisit alors la sous-catégorie lui-même.

**Propositions conservées** : chaque proposition reçue est enregistrée (table `propositions`) avant le dialogue. Un groupe qui a déjà une proposition en attente n'est pas renvoyé au LLM : quitter puis relancer `classify` ne coûte pas un second appel.

**Secrets** : `environnement.py` charge `.env` au démarrage de la CLI (lignes `CLE=valeur`, commentaires `#`, guillemets retirés), sans écraser une variable déjà définie. Pas de dépendance pour cela.

## Bloc 2 : validation en lot

Après les questions de la spec 06 (bloc 1), le tableau regroupe :

- les groupes marchands en attente, triés par compte (joint puis perso), puis par montant total absolu décroissant ;
- puis les opérations Amazon RG-13 à compléter, dans l'ordre chronologique.

```
Paiements carte à valider — 2026-06 (65 lignes)
  N°  Compte  Marchand                        Op.       Total  Proposition
   1  Joint   OLIVERSTORE VILLENEUVE-D'         2   -954,90 €  Achats › Maison & déco
   2  Joint   MP*CARREFOUR SAINT MAUR DES F     5   -400,12 €  Courses › Supermarché
  …
  63  Joint   AMAZON PAYMENTS · 24/05/2026      1   -218,68 €  à compléter (RG-13)
  …
Entrée : tout valider · numéro : corriger une ligne · q : quitter
>
```

- **Entrée** valide toutes les lignes qui ont une sous-catégorie, proposée ou corrigée. Pour chaque groupe : une règle `marchand_carte` et la proposition marquée validée, dans une seule transaction SQL. Pour chaque ligne RG-13 : une question (motif `RG-13`) et une décision. Les lignes sans sous-catégorie restent en attente, et leur nombre est affiché.
- **Numéro** : menus catégorie puis sous-catégorie (ceux de la spec 06, `a` pour annuler). La correction est enregistrée aussitôt dans `propositions` et marquée « corrigée » ; le tableau est réaffiché.
- **q** (ou Ctrl+C) : fin du dialogue. Les corrections déjà saisies sont conservées ; aucune règle n'est créée pour les lignes non validées.
- Une règle refusée (conflit, doublon, RG-13) laisse sa ligne en attente avec le motif affiché ; les autres lignes sont validées.

Le terminal est local : les clés marchand y sont affichées, mais jamais journalisées.

## Bloc 3 : tags exceptionnels (RG-15)

Candidates, dans le mois classé :

- nature `DEPENSE`, montant absolu au moins égal à `seuils.exceptionnel` (500 €) ;
- pas une charge fixe du contexte (détail `charges_fixes.*`) ;
- non récurrente : aucune opération de même clé de récurrence, de montant absolu au moins égal au seuil, dans les trois mois budgétaires précédents importés. Clé de récurrence : l'ICS s'il existe, sinon `(source, cle_marchand)` pour un paiement carte, sinon `(source, libelle_normalise)` ;
- sans réponse déjà enregistrée (oui ou non).

```
Exceptionnel 1/1 — Dépense d'au moins 500 € (RG-15)
  Joint · 01/06/2026 · -883,16 € · Achats › Maison & déco
  OLIVERSTORE VILLENEUVE-D'
  Marquer comme exceptionnelle ?  o. Oui   n. Non   s. Passer   q. Quitter
>
```

La réponse (oui ou non) est enregistrée aussitôt et ne revient jamais ; « passer » laisse la question ouverte. `Classement.exceptionnel` vaut vrai pour une réponse oui. Un lien à une dépense (RG-07) hérite du tag de la dépense liée lors des agrégats (étape 8).

## Déroulé de `budget classify`

`classer_et_questionner(mois, ledger, contexte, interlocuteur, categoriseur=None, journal=None)` (paramètres ajoutés avec une valeur par défaut : les appels de la spec 06 restent valides) :

1. Classement et enregistrement : contexte, décisions, règles, sous-catégories carte.
2. Bloc 1 : questions de la spec 06, puis reclassement.
3. Bloc 2 : groupes en attente ; appel au catégoriseur pour ceux sans proposition en attente ; tableau ; reclassement.
4. Bloc 3 : tags exceptionnels ; reclassement.
5. Régénération du journal `decisions.md`, y compris sans interlocuteur.

`q` dans un bloc arrête tout le dialogue ; les réponses données sont conservées. Sans interlocuteur (`--sans-questions` ou entrée non interactive), aucun appel au LLM n'a lieu, puisque rien ne serait validé.

CLI : `budget classify --mois AAAA-MM [--base …] [--contexte …] [--sans-questions] [--sans-llm] [--journal data/decisions.md]`. Le résumé ajoute : marchands proposés, validés, corrigés et en attente ; règles créées ; tags exceptionnels ; paiements carte sans sous-catégorie. Code 0 dans tous les cas sans erreur : l'attente ne devient bloquante qu'avant la publication (étape 8).

## Persistance (migration 0004)

```sql
ALTER TABLE regles ADD COLUMN desactivee_le TEXT;
ALTER TABLE classements ADD COLUMN regle_id INTEGER REFERENCES regles (id);

CREATE TABLE propositions (
    id                       INTEGER PRIMARY KEY,
    mois                     TEXT NOT NULL,
    source                   TEXT NOT NULL CHECK (source IN ('ca_cb', 'tr')),
    cle_marchand             TEXT NOT NULL,
    sous_categorie_proposee  TEXT,           -- NULL : aucune proposition (mode dégradé)
    modele                   TEXT,           -- ex. anthropic:claude-haiku-4-5
    sous_categorie_corrigee  TEXT,           -- saisie de l'utilisateur, prime sur la proposition
    regle_id                 INTEGER REFERENCES regles (id),
    proposee_le              TEXT NOT NULL,
    validee_le               TEXT
);

CREATE INDEX propositions_cle ON propositions (source, cle_marchand);

CREATE TABLE exceptionnels (
    transaction_id  TEXT PRIMARY KEY REFERENCES transactions (id),
    exceptionnel    INTEGER NOT NULL CHECK (exceptionnel IN (0, 1)),
    decide_le       TEXT NOT NULL
);
```

- `classements.regle_id` : numéro de la règle apprise appliquée (spec 06 ou marchand). Il alimente la colonne « Règle appliquée » de l'export et du Sheet, et les compteurs de `budget rules list`. `Classement` reçoit le champ `regle_id: int | None = None`, renseigné aussi pour les règles de la spec 06.
- `Memoire` reçoit `exceptionnels: Mapping[str, bool]`.
- Au plus une proposition en attente (`validee_le` vide) par `(source, cle_marchand)` : garanti par le code, vérifié par un test.
- `horloge.maintenant()` remplace `memoire.horodatage()` partout ; les tests la figent pour rendre journal et goldens déterministes.

## Journal `decisions.md`

Régénéré entièrement à la fin de chaque `classify` (défaut `data/decisions.md`, hors git) à partir de la base, qui reste la source de vérité (cadrage §7). C'est un fichier local de lecture humaine : il contient les libellés, comme le terminal, et n'est envoyé nulle part.

```markdown
# Journal des décisions

Régénéré le 14/10/2026 à 18:32 depuis data/budget.sqlite. Ne pas modifier : la base fait foi.

## Règles (66 actives, 0 désactivée)

| N° | Type | Source | Critère | Valeur | Condition | Résultat | Créée le | Désactivée le |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | virement | ca_joint | libelle_exact | … | montant -7,00 € | abonnements.streaming_musique | 14/10/2026 | |

## 2026-06

### Questions (28)

| Décidée le | Opération | Question | Réponse | Règle créée |
| --- | --- | --- | --- | --- |
| 14/10/2026 18:20 | Joint · 01/06/2026 · -7,00 € · Virement émis · … | Virement (RG-06) | Dépense › Abonnements › Streaming & musique | n° 1 |

### Marchands validés (62)

| Validé le | Compte | Marchand | Op. | Proposition | Retenue | Règle |
| --- | --- | --- | --- | --- | --- | --- |

### Tags exceptionnels (1)

| Décidé le | Opération | Exceptionnel |
| --- | --- | --- |
```

Mois du plus récent au plus ancien ; lignes par date de décision. Contenu déterministe à horloge figée (test golden).

## Commandes d'audit et export

**`budget rules list [--toutes] [--type TYPE]`** : tableau Rich des règles actives (toutes avec `--toutes`) : n°, type, source, critère, valeur, condition de montant, résultat, opérations couvertes (classements ayant ce `regle_id`, tous mois confondus), créée le, désactivée le.

**`budget rules disable N`** : désactive la règle (`active = 0`, `desactivee_le`), affiche sa description et les mois dont des opérations étaient classées par elle, avec la commande à relancer (`budget classify --mois …`). Règle inconnue ou déjà inactive : message et code 1. Les décisions issues de questions ne changent pas, puisqu'une décision prime sur une règle ; les opérations classées par la règle redeviennent en attente au `classify` suivant.

**`budget export --mois AAAA-MM [--sortie data/exports]`** : écrit `budget_AAAA-MM.csv` à partir du classement enregistré. UTF-8 avec BOM, séparateur `;`, montants `-3116,50` sans séparateur de milliers ni symbole, dates `JJ/MM/AAAA`. Une ligne par transaction du mois, triées par compte (joint puis perso), date, source et position. Colonnes du cadrage §8, reprises telles quelles par l'onglet mensuel à l'étape 9 :

| Colonne | Contenu |
| --- | --- |
| Date d'opération | `JJ/MM/AAAA` |
| Date de débit | Cartes uniquement |
| Compte | `Joint` ou `Perso` |
| Libellé | Libellé sur une ligne |
| Montant | Signé |
| Nature | Libellé français (`Dépense`, `Lien à une dépense`…) ; `Non classée` si vide |
| Catégorie, Sous-catégorie | Libellés ; pour un lien, ceux de la dépense liée (RG-07) |
| Exceptionnel | `oui` ou vide |
| Lien | Pour un lien : compte, date et montant de la dépense liée |
| Règle appliquée | `Règle n° 12`, `Décision`, `Contexte : charges_fixes.energie`, `RG-03`, `RG-13`… |

Les lignes sont construites par `restitution/operations.py` (`lignes_operations(...) -> list[LigneOperation]`), réutilisé tel quel par la publication.

## Organisation du code

```
src/budget/
├── horloge.py                # maintenant() : seule source des horodatages
├── environnement.py          # chargement de .env
├── libelles.py               # + cle_marchand
├── regles.py                 # + marchand_carte, regle_marchand_retenue, PREFIXES_AMAZON
├── agents/
│   ├── __init__.py
│   ├── confidentialite.py    # masquer()
│   └── categoriseur.py       # Proposition, Soumission, agent, CategoriseurLLM
├── classement/
│   └── cartes.py             # sous-catégorie des paiements carte, RG-13, groupes marchands
├── questions/
│   ├── validation_lot.py     # bloc 2
│   └── exceptionnels.py      # bloc 3, candidates RG-15
├── restitution/
│   ├── __init__.py
│   ├── operations.py         # LigneOperation (export, puis Sheet)
│   ├── export_csv.py
│   └── journal.py            # decisions.md
└── ledger/
    └── memoire.py            # + propositions, exceptionnels, désactivation
```

## Valeurs attendues sur les fixtures

**Juin 2026**, après le scénario de la spec 06, avec `contexte.example.yaml` :

| Élément | Valeur |
| --- | --- |
| Paiements carte | 105 : 48 du fichier cartes, 57 `Avoir` TR |
| RG-13 automatique | 2 : 49,72 € et 19,99 € (`AMAZON PAYMENTS`), `achats.divers` |
| RG-13 à compléter | 3 : 78,38 € et 218,68 € (`AMAZON PAYMENTS`), 89,99 € (`AMAZON EU SARL`) |
| Groupes marchands en attente | 62 (23 joint, 39 perso), couvrant 100 opérations |
| Appels au catégoriseur | 1, avec 62 libellés |
| Lignes du tableau | 65 |
| Après validation (Entrée) | 62 règles `marchand_carte`, 66 règles au total ; aucun paiement carte sans sous-catégorie |
| RG-09 | `MANOMANO PARIS` (−199,99 € et +199,99 €) et `ZALANDO PAYMENTS` (TR, −51,00 € et +51,00 €) : même sous-catégorie, net nul |
| RG-15 | 1 candidate : `OLIVERSTORE` −883,16 € du 01/06 (l'achat de −71,74 € du même marchand est sous le seuil) |
| Second `classify` | 0 appel, 0 ligne, 0 question |

Exemples de groupes : `CARREFOUR CITY SAINT MAUR D` (joint, 8 opérations, −128,62 €), `RBCPJ PARIS` (joint, 6 opérations, −188,00 €), `GOOGLE*GOOGLE PLAY APP` (perso, 5 opérations, −124,37 €), `UBER *EATS` (perso, 4 opérations, −71,20 €).

**Juillet 2026**, après juin complet :

| Élément | Valeur |
| --- | --- |
| Paiements carte | 138 |
| Classés par une règle marchand de juin | 38 (20 groupes), dont `MILIBOO CHAVANOD` +258,99 € : avoir de l'achat de juin, même sous-catégorie (RG-09) |
| RG-13 automatique | 8, dont les avoirs +9,00 € et +39,99 € |
| RG-13 à compléter | 2 : 57,80 € (cartes) et 51,98 € (TR) |
| Groupes en attente | 71 (51 joint, 20 perso), soit 71 libellés envoyés |
| RG-15 | `MIDAS CHAMPIGNY SUR MA` −1 173,86 € ; les candidates hors carte dépendent des réponses de juillet, relevées puis figées |

**Scénario de test** : `tests/scenarios/juin.py` reçoit les propositions du `FunctionModel` (un dictionnaire clé → sous-catégorie couvrant les 62 clés de juin et les 71 de juillet), au moins une correction de ligne, les trois saisies RG-13 et la réponse « oui » pour OliverStore. Ce sont des réponses de test sur les fixtures, pas des décisions sur les données réelles. Les blocs sont exposés séparément pour que le test de juillet puisse les dérouler sans répondre aux questions du bloc 1.

## Dépendances autorisées

| Groupe | Paquet |
| --- | --- |
| Runtime | `pydantic-ai-slim[anthropic]>=2,<3` |

`pydantic-ai-slim` avec le seul extra `anthropic` évite d'installer les SDK des autres fournisseurs, que le paquet `pydantic-ai` complet embarque en v2. Aucune autre dépendance : `.env` est lu avec la bibliothèque standard.

## Critères d'acceptation

- [x] `cle_marchand` produit les clés du tableau ; groupes de juin et de juillet conformes aux valeurs attendues.
- [x] Ordre de classement d'un paiement carte : décision > RG-13 > règle marchand > en attente (tests synthétiques) ; la nature n'est jamais modifiée.
- [x] Règles marchand : validations du modèle (source, critère, nature vide, sous-catégorie obligatoire, refus Amazon) ; priorité exact > préfixe ; conflit et doublon refusés.
- [x] RG-09 : les avoirs reçoivent la sous-catégorie du marchand (MANOMANO et Zalando en juin, MILIBOO en juillet).
- [x] RG-13 : 49,72 € classé sans question ; 78,38 € présenté à compléter, validé en décision, jamais envoyé au LLM, sans règle.
- [x] Catégoriseur : un seul appel pour juin, avec exactement les 62 libellés masqués ; aucun montant, date ni compte dans les messages (inspectés via le `FunctionModel` ou `capture_run_messages`).
- [x] Confidentialité : un groupe synthétique dont la clé contient un IBAN ou 6 chiffres consécutifs est masqué dans le prompt, et le test échoue si un IBAN y apparaît ; une transaction hors carte dans un groupe lève `ControleError` avant l'appel.
- [x] Validateur : une sortie avec un libellé manquant, inconnu ou en double provoque une relance ; après épuisement des relances, mode dégradé sans proposition et sans erreur.
- [x] Mode dégradé : `--sans-llm` ou une clé absente présente les groupes sans proposition ; la saisie manuelle crée la règle.
- [x] Validation en lot : Entrée crée une règle par groupe ; une correction prime sur la proposition et survit à un `q` ; une proposition en attente n'est pas redemandée au LLM au passage suivant.
- [x] RG-15 : une seule candidate en juin ; la réponse ne revient jamais ; une dépense récurrente (même ICS au-dessus du seuil le mois précédent, cas synthétique) et une charge fixe ne sont pas proposées.
- [x] Second `classify` de juin : aucun appel, aucune question, classements identiques.
- [x] `--sans-questions` et entrée non interactive : aucun appel au LLM.
- [x] `budget rules list` et `budget rules disable` : affichage, compteurs, désactivation, opérations remises en attente au `classify` suivant, code 1 sur une règle inconnue.
- [x] `decisions.md` régénéré à chaque `classify`, identique au golden à horloge figée.
- [x] `budget export --mois 2026-06` : 159 lignes, colonnes et formats ci-dessus, aucune ligne `Non classée` après les scénarios.
- [x] Migration 0004 sur une base en version 3 contenant règles et décisions.
- [x] `models.ALLOW_MODEL_REQUESTS = False` pendant les tests ; aucune clé marchand dans les logs.
- [x] Les tests `donnees_reelles` (sans questions ni LLM) passent sur juin et juillet quand les données sont présentes.
- [x] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_libelles.py` (ajouts) | `cle_marchand` |
| `test_regles.py` (ajouts) | Règles marchand : validation, correspondance sur la clé, priorité, conflits, refus Amazon |
| `test_classement_cartes.py` | Ordre décision > RG-13 > règle > attente, RG-09, groupes marchands |
| `test_confidentialite.py` | `masquer`, IBAN dans un prompt, garde-fou paiement carte |
| `test_categoriseur.py` | `FunctionModel` : prompt, validateur et relances, lots, mode dégradé |
| `test_validation_lot.py` | Bloc 2 avec `InterlocuteurScripte` : Entrée, correction, quitter, RG-13, conflit |
| `test_exceptionnels.py` | RG-15 : candidates, récurrence, réponses |
| `test_journal.py` | `decisions.md` golden à horloge figée |
| `test_export_csv.py` | Colonnes, formats, tri, lien RG-07 |
| `test_classement_cartes_golden.py` | Juin puis juillet sur les fixtures |
| `test_ledger_migrations.py`, `test_cli.py` (ajouts) | Migration 0004 ; `rules list`, `rules disable`, `export`, `classify --sans-llm` |

## Points ouverts

- **Modèle de catégorisation** : le cadrage retient Claude Haiku 4.5 (`anthropic:claude-haiku-4-5`, 1 $ / 5 $ par million de tokens en entrée / sortie). Claude Haiku 5.5 (`anthropic:claude-haiku-5-5`, 0,10 $ / 0,50 $) est plus récent et dix fois moins cher. Le choix reste dans `contexte.yaml` (`llm.modele_categorisation`) et ne demande aucun changement de code ; à comparer au premier run réel. Haiku 5.5 réfléchit par défaut (effort `medium`) : abaisser l'effort par les réglages du modèle si la latence gêne.
- **Plusieurs clés pour un même marchand** (`UBER *EATS HELP.UBER.COM` et `UBER *EATS HELP.UBER.C HELP.UBER.COM`) : chacune est validée une fois. Si la charge reste forte, ajouter une saisie de préfixe dans le tableau (comme `x` en spec 06).
- **Paiement carte qui n'est pas une dépense** (recharge d'un autre compte) : une règle marchand ne change pas la nature. À traiter si le cas apparaît.
- **Marchand à usages multiples** (Decathlon : sport ou vélo) : une règle par clé ; une opération atypique ne peut pas être reclassée seule au MVP.
- **Correction d'une décision et réactivation d'une règle** : pas de commande. `budget rules disable` suivi d'une nouvelle validation couvre le cas des règles.

## Écarts constatés

**PydanticAI v2** (version installée : `pydantic-ai-slim` 2.56.0, API vérifiée dans le code source installé et par un essai avec `FunctionModel`)

- Relances de sortie : paramètre `retries={"output": 2}` d'`Agent` (dictionnaire `AgentRetries`). Après épuisement, `UnexpectedModelBehavior` (« Exceeded maximum output retries »), converti en mode dégradé.
- Confirmés : `output_type`, `deps_type`, `instructions`, `@agent.output_validator`, `ModelRetry`, `run_sync(..., model=, deps=, model_settings=)`, `result.usage` (propriété : `requests`, `input_tokens`, `output_tokens`), `models.ALLOW_MODEL_REQUESTS`. Le modèle est passé à `run_sync` : l'agent est défini une fois, sans modèle, au niveau du module.
- Sans clé, un modèle `anthropic:` lève `UserError` à l'appel ; un nom sans préfixe lève `UserError` (« Unknown model »). La CLI détecte la clé absente avant tout appel.
- Le premier appel d'un processus affiche une bannière : désactivée par `pydantic_ai.BANNER_ENABLED = False` dans `agents/categoriseur.py`.
- `pydantic_graph` appelle `asyncio.get_event_loop()` hors boucle (`DeprecationWarning` sous Python 3.12) : avertissement filtré dans `pyproject.toml`, sans effet sur le fonctionnement.

**Organisation et contrats**

- `Categoriseur` expose aussi `nom_modele` (renseigne `propositions.modele`). `CategoriseurIndisponible(raison)` porte le mode dégradé (`--sans-llm`, clé absente, aucun catégoriseur) ; toute défaillance lève `CategorisationError` (nouvelle exception) avec les propositions des lots déjà reçus, qui sont conservées.
- `est_paiement_carte` déménage dans `classement/cartes.py` (le moteur le réimporte) pour éviter un cycle entre `cartes.py` et `moteur.py`.
- `classer_et_questionner` est découpée en `classer`, `bloc_questions`, `bloc_validation`, `bloc_exceptionnels`. Les tests de la spec 06 appellent désormais `bloc_questions` pour le bloc 1 seul, puisque `classer_et_questionner` enchaîne les trois blocs.
- `Ledger.lire_sql` : lecture brute (SELECT seulement), réservée au journal.
- Détail d'un paiement carte classé par règle : `regles.<n°>`, avec `regle_id`.

**Comportements précisés**

- Journal : par défaut `decisions.md` à côté de la base (`data/decisions.md` avec la base par défaut), pour que les tests qui passent `--base` n'écrivent jamais dans `data/`.
- RG-13 : les saisies d'une ligne Amazon restent en mémoire jusqu'à Entrée ; un `q` les perd (les corrections de groupes, elles, sont enregistrées aussitôt comme prévu). Le compteur « corrigés » inclut ces saisies.
- Groupes à égalité de total : départagés par la clé marchand.
- Résumé de `classify` : une ligne par bloc, puis la liste de ce qui reste en attente (questions, marchands, Amazon, tags).
- `budget rules list` est large : lisible à partir d'environ 140 colonnes.

**Vérifié sur les fixtures** : toutes les valeurs de juin et de juillet du tableau « Valeurs attendues » (65 lignes, 62 groupes dont 23 joint, 100 opérations, 1 appel à 62 libellés, 66 règles, 38 opérations de juillet classées par 20 règles de juin, MILIBOO, 8 RG-13 automatiques dont les avoirs, 2 à compléter, 71 groupes dont 51 joint, MIDAS en RG-15). En juillet, sans réponse au bloc 1, MIDAS est la seule candidate RG-15.

**Scénario de référence** (`tests/scenarios/juin.py`) : propositions du `FunctionModel` pour les 133 clés ; correction de RELAY PARIS (Achats › Divers vers Loisirs › Sorties) ; saisies RG-13 : 218,68 € High-tech, 89,99 € Divers, 78,38 € Enfants › Vêtements & équipement ; OLIVERSTORE marquée exceptionnelle. Le journal de référence est `tests/golden/decisions_2026-06.md` (libellés anonymisés des fixtures).

**Recette réelle** : non faite. Aucun `.env` n'existe encore (seulement `.env.example`) ; sans clé, `classify` passe en mode dégradé.
