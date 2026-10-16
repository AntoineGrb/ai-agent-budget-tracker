# Spec 08 — Dépenses par catégorie et agent synthèse

| Champ | Valeur |
| --- | --- |
| Étape du plan | 8 / 10 |
| Type | Code (Claude Code) |
| Complexité | Simple à moyenne |
| Estimation | 1 session |
| Prérequis | Recadrage d'octobre 2026 terminé (`docs/cadrage.md`, étape R) |

> Spec réécrite le 16 octobre 2026 après le recadrage. Elle remplace la version qui reposait sur les natures de flux, les enveloppes, les dotations et les charges fixes. Rien de cela n'existe plus dans le code.

## Objectif

Pour un mois classé, le code calcule les dépenses comptées par catégorie, séparément pour la vue joint et la vue perso. Il les compare au budget de chaque catégorie (s'il est renseigné) et à la moyenne des trois mois précédents. Il ajoute l'évolution sur 12 mois et les plus grosses dépenses du mois. Avant tout calcul, des contrôles bloquants vérifient que le mois est entièrement classé.

L'agent synthèse reçoit ces chiffres déjà calculés et rédige un court commentaire. Il ne calcule rien. Le résultat est enregistré en base pour la publication de l'étape 9.

À la fin de l'étape, sur les fixtures et après le scénario de référence (`tests/scenarios/reference.py`), `budget synthese --mois 2026-06` affiche 7 566,46 € de dépenses joint et 1 322,86 € de dépenses perso, la répartition par catégorie, puis le commentaire de l'agent.

## Références cadrage

- §1 Les trois questions du mois : combien en joint et en perso, quelle catégorie pèse le plus, comment les dépenses évoluent.
- §4 Imputation : une opération suivie est comptée dans une sous-catégorie, ou non comptée.
- §5 Deux vues séparées (joint, perso), jamais additionnées.
- §8 Restitution, §10 Fichier de contexte, §11 Contrôles bloquants et confidentialité.
- RG-09 (avoirs carte déduits), RG-10 (non compté), RG-19 (débits seulement).

## État du code à l'entrée

Ce qui existe et doit être réutilisé tel quel :

- `classement.classer_mois` et `questions.execution.classer(mois, ledger, contexte)` : classement sans question, enregistré en base.
- `ResultatClassement.en_attente` : nombre d'opérations suivies ni comptées ni non comptées.
- `restitution/operations.py` : `depenses_par_categorie(transactions, classements)`, qui renvoie les dépenses comptées en positif, avoirs déduits, par `(Categorie, Compte)`. C'est la base du calcul.
- `categories.py` : `Categorie`, `SousCategorie`, `categorie_de`, `libelle_complet`.
- `agents/categoriseur.py` : conventions de l'agent PydanticAI (validateur de sortie, `ModelRetry`, relances, `raison_echec`, `CategorisationError`) ; `agents/confidentialite.py` : `masquer`, `contient_iban`.
- `cli._categoriseur` : mode dégradé quand la clé API manque.
- `ledger/migrations.py` : une seule migration (n° 1). Cette étape ajoute la n° 2.

## Périmètre

**Inclus**

- `agregats/` : calcul pur des chiffres d'un mois et contrôles bloquants.
- Budgets par catégorie, facultatifs, dans `contexte.yaml`.
- Agent synthèse PydanticAI v2.
- Migration n° 2 : table `syntheses`.
- Commande `budget synthese`.

**Exclus**

- Publication Google Sheets (étape 9), `budget run` (étape 10).
- Vue consolidée joint + perso (hors périmètre du cadrage §2).
- Agent analyste doté d'outils (phase 2).

## Convention

Toutes les dépenses sont exprimées en **positif** : `−Σ montant` des opérations comptées. Un avoir carte (montant positif) les réduit (RG-09). Le total d'une catégorie peut donc être négatif si un remboursement dépasse les achats du mois. Les opérations non comptées et les crédits ignorés n'entrent dans aucun total. Tous les montants sont des `Decimal`, jamais des `float`.

Un écart vaut `dépense − référence` : positif, c'est un dépassement.

## Contexte : budgets et modèle de synthèse

Deux clés facultatives s'ajoutent à `contexte.yaml`, qui reste en `version: 2` (elles sont rétrocompatibles) :

```yaml
budgets:                  # facultatif : budget mensuel par vue et par catégorie
  joint: {courses: 600, restos: 150}
  perso: {restos: 200}

llm:
  modele_categorisation: "anthropic:claude-haiku-4-5"
  modele_synthese: "anthropic:claude-sonnet-5-5"   # défaut si absent
```

- Les clés de `budgets.joint` et `budgets.perso` sont des valeurs de `Categorie`. Une catégorie inconnue ou un montant négatif lève `ConfigError` avec le chemin YAML.
- `contexte.example.yaml` reçoit l'exemple ci-dessus : il sert aux tests golden.
- `budget config verifier` affiche le nombre de budgets renseignés par vue et le modèle de synthèse.

## Chiffres d'un mois (`agregats/`)

`calculer_agregats(mois, transactions_par_mois, classements_par_mois, contexte) -> Agregats` est une fonction pure : ni base, ni fichier. `transactions_par_mois` et `classements_par_mois` couvrent les mois importés de M−11 à M.

### Par vue (joint, puis perso)

| Champ | Calcul |
| --- | --- |
| `total` | Dépenses comptées de la vue sur le mois |
| `categories` | Une `LigneCategorie` par catégorie ayant au moins une opération comptée sur M, dans l'ordre de `Categorie` |
| `non_comptees` | Nombre et somme (en positif) des débits non comptés de la vue, pour information |
| `plus_grosses` | Les 5 dépenses comptées les plus élevées de la vue (montant absolu décroissant, puis date) |

`LigneCategorie` :

| Champ | Calcul |
| --- | --- |
| `categorie` | `Categorie` |
| `montant` | Dépenses comptées de la catégorie sur M |
| `nb_operations` | Opérations comptées de la catégorie sur M, avoirs compris |
| `sous_categories` | `(SousCategorie, montant)` non nuls, par montant décroissant |
| `budget`, `ecart_budget` | Budget de `contexte.budgets` pour cette vue et cette catégorie, et `montant − budget` ; vides sans budget |
| `moyenne_3_mois`, `mois_moyenne`, `ecart_moyenne` | Voir ci-dessous |

Une catégorie qui a un budget mais aucune dépense sur M apparaît aussi, avec un montant de 0,00 €.

**Moyenne sur 3 mois** : moyenne des montants de la catégorie sur les mois **classés et complets** (sans opération en attente) parmi M−1, M−2 et M−3. Un mois disponible sans dépense dans la catégorie compte pour 0. La somme est divisée par le nombre de mois disponibles (1 à 3), puis arrondie au centime (`ROUND_HALF_UP`). Sans aucun mois disponible, la moyenne et son écart sont vides, et `mois_moyenne` vaut 0.

**Plus grosses dépenses** : chaque élément porte la date d'opération, le montant (positif), la sous-catégorie et `marchand`. `marchand` vaut la clé marchand masquée (`masquer(cle_marchand(...))`) pour un paiement carte, `None` sinon : jamais de libellé de virement ou de prélèvement (cadrage §11).

### Historique sur 12 mois

`historique` : pour chaque mois importé de M−11 à M, dans l'ordre chronologique, le total joint, le total perso et `complet` (aucune opération en attente). Le mois M y figure.

### Modèle

Modèles Pydantic immuables (`frozen=True, extra="forbid"`), montants en `Montant` :

```python
class Agregats(BaseModel):
    mois: str
    joint: Vue
    perso: Vue
    historique: tuple[PointMensuel, ...]
```

## Contrôles bloquants (`agregats/controles.py`)

`controler_mois(...)` lève `ControleError` en listant **tous** les manquements. Chaque manquement donne le nombre d'opérations en cause, puis les premières (fichier, position, date, montant, jamais le libellé), et la commande à lancer.

1. Le mois est importé. Les contrôles de parsing (RG-02, RG-03, solde TR) sont donc passés.
2. Le mois est classé et aucune opération n'est en attente (`budget classify --mois AAAA-MM`).
3. Pour chaque vue, la somme des catégories est égale au total, au centime, et chaque catégorie est égale à la somme de ses sous-catégories.

Le contrôle 3 vérifie le code d'agrégation : s'il échoue, on corrige le calcul, jamais on n'ajoute de tolérance. `budget synthese` refuse de produire une synthèse si un contrôle échoue ; la publication (étape 9) appelle les mêmes contrôles.

## Agent synthèse (`agents/synthese.py`)

Consulter le skill `pydantic-ai-v2` et reprendre les conventions du catégoriseur : agent défini au niveau du module, `deps_type`, validateur de sortie, `retries={"output": 2}`, `pydantic_ai.BANNER_ENABLED = False`, `FunctionModel` dans les tests, `models.ALLOW_MODEL_REQUESTS = False` déjà posé par `conftest.py`.

**Entrée** : les `Agregats` et les budgets renseignés, sérialisés en JSON avec les montants en chaînes décimales (`"627.36"`), jamais en `float`. `masquer()` est appliqué au JSON entier en défense en profondeur.

**Confidentialité** : seules les clés marchand masquées des paiements carte figurent dans le prompt (autorisées par le cadrage §11). Aucun libellé de virement ou de prélèvement, aucun IBAN, aucun nom.

**Sortie** :

```python
class CommentaireSynthese(BaseModel):  # frozen, extra="forbid"
    resume: str = Field(max_length=280)                     # une à deux phrases
    points_cles: list[str] = Field(min_length=1, max_length=5)
```

**Instructions** (en français) : répondre pour le mois aux trois questions du cadrage §1, vue joint et vue perso séparées ; citer la catégorie qui pèse le plus et les dépassements de budget ; expliquer une forte hausse par rapport à la moyenne à l'aide des plus grosses dépenses ; ne citer que des montants présents dans les données ; ne rien calculer ; style factuel et bref.

**Validateur de sortie** : chaque montant en euros cité (`1 234,56 €`, `1 200 €`, `-50 €`) est lu avec `parse_montant`. Sa valeur absolue doit figurer parmi les valeurs absolues des montants de l'entrée. Sinon, `ModelRetry` liste les montants inconnus et rappelle de ne citer que les montants fournis. Les pourcentages ne sont pas contrôlés.

**Échec** : après deux relances, ou en cas d'erreur d'API, la synthèse est enregistrée sans commentaire, avec un avertissement qui reprend la cause, sur le modèle de `raison_echec`. L'absence de commentaire ne bloque pas la publication.

**Modèle** : `contexte.llm.modele_synthese`. Ne pas fixer `temperature`. `max_tokens` modeste (2 000).

## Persistance (migration n° 2)

```sql
CREATE TABLE syntheses (
    mois          TEXT PRIMARY KEY,
    empreinte     TEXT NOT NULL,     -- sha256 de l'entrée de l'agent, du modèle et de la version des consignes
    agregats      TEXT NOT NULL,     -- JSON des Agregats, montants en chaînes décimales
    commentaire   TEXT,              -- JSON du CommentaireSynthese ; NULL sans LLM ou après échec
    modele        TEXT,
    calculee_le   TEXT NOT NULL,
    commentee_le  TEXT
);
```

L'empreinte est le sha256 du JSON canonique (clés triées) de l'entrée, du nom du modèle et de `VERSION_CONSIGNES_SYNTHESE`. Si elle n'a pas changé et qu'un commentaire existe, il est réutilisé sans appel ; `--regenerer` force un nouvel appel. Les agrégats sont toujours recalculés et réécrits. Une migration publiée n'est jamais modifiée (CLAUDE.md).

## CLI

`budget synthese --mois AAAA-MM [--base …] [--contexte …] [--sans-llm] [--regenerer]` :

1. Reclasse sans question les mois importés de M−11 à M (`questions.execution.classer`, sans interlocuteur ni LLM), pour que les chiffres reflètent les règles en vigueur.
2. Contrôles bloquants sur M : en cas d'échec, message et code 1.
3. Agrégats.
4. Commentaire : réutilisé si l'empreinte n'a pas changé ; pas d'appel avec `--sans-llm` ou si la clé API manque (avertissement, comme pour `classify`).
5. Enregistrement, puis affichage Rich : par vue, tableau Catégorie · Montant · Budget · Écart budget · Moyenne 3 mois · Écart moyenne, avec une ligne Total ; les plus grosses dépenses ; l'historique ; le commentaire.

`budget classify` n'est pas modifié.

## Organisation du code

```
src/budget/
├── agregats/
│   ├── __init__.py
│   ├── modeles.py      # Agregats, Vue, LigneCategorie, PlusGrosseDepense, PointMensuel
│   ├── calcul.py       # calculer_agregats : fonction pure
│   └── controles.py    # controler_mois
├── agents/
│   └── synthese.py     # EntreeSynthese, CommentaireSynthese, agent, validateur
├── ledger/
│   └── syntheses.py    # lecture et écriture de la table syntheses
└── config.py           # + budgets, llm.modele_synthese
```

## Valeurs attendues sur les fixtures

Après le scénario de référence de juin puis celui de juillet (`tests/scenarios/reference.py`), avec `contexte.example.yaml` et les budgets de l'exemple ci-dessus. Les montants par catégorie sont ceux de `DEPENSES` dans `tests/test_bout_en_bout.py`.

**Juin 2026**

| Élément | Valeur |
| --- | --- |
| Total joint | 7 566,46 € sur 61 opérations ; catégorie la plus lourde : Logement, 3 289,26 € |
| Total perso | 1 322,86 € sur 59 opérations ; catégorie la plus lourde : Loisirs, 685,69 € |
| Budgets | Courses joint 627,36 € pour 600 € : +27,36 € ; Restos joint 97,36 € pour 150 € : −52,64 € ; Restos perso 206,05 € pour 200 € : +6,05 € |
| Moyenne 3 mois | Vide partout : aucun mois classé avant juin (`mois_moyenne` = 0) |
| Non comptées | Joint 10 opérations, 6 088,17 € ; perso 7 opérations, 61,50 € (17 opérations, 6 149,67 € au total) |
| Plus grosses, joint | 1 787,46 € Logement › Prêt (05/06) ; 1 071,04 € Logement › Charges & assurances (24/06) ; 883,16 € Achats › Maison, marchand `OLIVERSTORE VILLENEUVE-D'` (01/06) ; 668,24 € Enfants › Garde/scolaire (02/06) ; 393,08 € Achats › Tech, `SUNNY INCH ST JEAN DE VEDAS` (01/06) |
| Plus grosses, perso | 409,97 € Loisirs › Sport, `DECATHLON` (08/06) ; 90,80 € Transport › Transports en commun (03/06), sans marchand car c'est un prélèvement ; 89,99 € Loisirs › Autre, `GOOGLE*GOOGLE PLAY APP` ; 77,90 € Santé › Autre, `LUNETTESPRTOUS` ; 62,40 € Loisirs › Sorties, `KINEPOLIS MEGA` |
| Historique | Juin seul si juillet n'est pas importé ; juin puis juillet sinon |

**Juillet 2026**

| Élément | Valeur |
| --- | --- |
| Total joint / perso | 9 404,27 € sur 102 opérations / 1 154,49 € sur 53 opérations |
| Moyenne 3 mois | Calculée sur juin seul (`mois_moyenne` = 1) : Courses joint 627,36 € pour 757,92 € en juillet, soit +130,56 € ; Logement joint 3 289,26 € pour 4 416,17 €, soit +1 126,91 € |
| Budgets | Courses joint +157,92 € ; Restos joint +295,58 € ; Restos perso +62,17 € |
| Catégorie nouvelle | Divers joint 618,45 € (moyenne 0,00 € sur 1 mois, écart +618,45 €) |
| Plus grosse, joint | 2 659,16 € Logement › Autre (20/07), sans marchand (virement) |

Répartition des non comptées de juin, à retrouver : sur le joint, 2 débits différés (3 527,17 €), 7 virements (2 550,00 €) et la carte `BOURSORAMA BOULOGNE BILLA` (11,00 €) ; sur TR, les 7 investissements. En cas d'écart avec ces tableaux, s'arrêter et le noter dans « Écarts constatés ».

**Contrôles** : tous passent après les scénarios. Juin importé sans aucune réponse : après le reclassement de l'étape 1 de la CLI, le contrôle 2 cite 126 opérations en attente (137 suivies moins 11 classées par les règles fixes) et la commande `budget classify --mois 2026-06`.

## Dépendances autorisées

Aucune nouvelle dépendance.

## Critères d'acceptation

- [x] Valeurs de juin et de juillet des tableaux ci-dessus, vérifiées par des tests golden (ajoutés à `tests/test_bout_en_bout.py` ou dans `tests/test_agregats.py`).
- [x] Avoir carte déduit de sa catégorie (RG-09), non comptées exclues (RG-10), crédits absents (RG-19) : un cas synthétique chacun.
- [x] Moyenne sur 3 mois : 0, 1 et 3 mois disponibles, mois en attente exclu, catégorie absente d'un mois comptée pour 0, arrondi au centime.
- [x] Budgets : catégorie avec et sans budget, budget sans dépense (montant 0,00 €), catégorie inconnue dans `contexte.yaml` refusée.
- [x] Contrôles bloquants : chacun des trois produit une `ControleError` qui cite les manquements, sans libellé.
- [x] Agent synthèse : un `FunctionModel` qui cite un montant absent provoque une relance ; après épuisement des relances, synthèse enregistrée sans commentaire et avec un avertissement.
- [x] Confidentialité : aucun libellé de virement ou de prélèvement et aucun IBAN dans le prompt (test sur les messages capturés, sur juin des fixtures).
- [x] Empreinte : deux `budget synthese` successifs ne font qu'un appel ; `--regenerer` en force un second ; une nouvelle réponse qui change un total en provoque un.
- [x] `budget synthese` : code 1 si un contrôle échoue ; affichage complet sinon ; `--sans-llm` sans appel.
- [x] Migration n° 2 appliquée sur une base en version 1, sans perte.
- [x] Aucun `float` dans les agrégats ni dans le JSON envoyé (test récursif sur les types).
- [x] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

Rester sur le cœur du moteur : pas de test par fonction utilitaire.

| Fichier | Contenu |
| --- | --- |
| `test_agregats.py` | Cas synthétiques (RG-09, RG-10, RG-19, moyenne, budgets) et contrôles bloquants |
| `test_agent_synthese.py` | `FunctionModel` : confidentialité, validateur, relances, échec, empreinte |
| `test_bout_en_bout.py` (ajouts) | Valeurs de juin et juillet ; `budget synthese` en succès et en échec de contrôle (via la fonction appelée par la CLI) |

## Points ouverts

- **Modèle de synthèse** : `claude-sonnet-5-5` par défaut. Le choix reste dans `contexte.yaml` ; la clé API doit être rattachée à un workspace (constaté au premier run réel).
- **Budgets** : à renseigner dans `data/contexte.yaml` après quelques mois, à partir des moyennes affichées.

## Écarts constatés

**Valeurs attendues** : toutes retrouvées sur les fixtures, sans écart (totaux, budgets, moyennes, non comptées et leur répartition, plus grosses dépenses).

**Contradiction tranchée**

- Historique de juin : la ligne « Juin seul si juillet n'est pas importé ; juin puis juillet sinon » contredit la définition (mois importés de M−11 à M). La définition est appliquée : la synthèse de juin n'a que juin dans son historique, même si juillet est importé ; celle de juillet a juin puis juillet. À confirmer.

**Précisions d'implémentation**

- Plus grosses dépenses : seuls les débits sont retenus. Un avoir carte n'est pas une dépense, même s'il est élevé en valeur absolue.
- Modèles : `Vue` porte aussi `compte` et une propriété `nb_operations` (les « 61 opérations » des tableaux). `non_comptees` est un `NonComptees(nombre, montant)`. Les sous-catégories sont des `MontantSousCategorie(sous_categorie, montant)` ; à montant égal, l'ordre de `SousCategorie` départage.
- Une opération suivie sans classement en base compte comme en attente : le mois n'est alors ni complet (moyenne, historique) ni contrôlable (contrôle 2).
- `controler_mois(mois, transactions_par_mois, classements_par_mois, agregats)` : le contrôle 3 porte sur les agrégats, qui sont donc calculés avant les contrôles. Si le mois n'est pas importé, seul le contrôle 1 est signalé. Cinq opérations au plus sont citées, puis « … et N autre(s) ».
- L'orchestration de la CLI (reclassement, contrôles, agrégats, commentaire, enregistrement) est dans `restitution/synthese.py` (`produire_synthese`), absent de l'arborescence de la spec. Les tests l'appellent directement.
- Agent : protocole `Commentateur`, implémentations `CommentateurLLM` et `CommentateurIndisponible` (mode dégradé), sur le modèle du catégoriseur. Nouvelle exception `SyntheseError`. `raison_echec` prend le nom de l'agent en paramètre (« agent synthèse en échec (…) »). La CLI partage la détection du mode dégradé entre les deux agents.
- Empreinte : calculée avec le modèle configuré (`contexte.llm.modele_synthese`), y compris avec `--sans-llm`. Un commentaire déjà produit pour la même empreinte est donc réutilisé avec `--sans-llm`. La colonne `modele` est vide sans commentaire. `--regenerer --sans-llm` enregistre la synthèse sans commentaire.
- Validateur : il reconnaît les montants suivis de `€`, avec ou sans signe (`-`, `+`, `−`) et avec des milliers séparés par une espace, normale, insécable ou fine. Il compare les valeurs absolues au centime. Une relance liste les montants inconnus dans leur forme d'origine.
- Budgets : montant positif ou nul, deux décimales au plus, ramené au centime (`"600.00"` dans le JSON envoyé). Une catégorie inconnue produit « budgets.joint.voyages (clé) : valeur 'voyages' invalide, attendu : … » (message `enum` traduit dans `config._decrire`).
- Migration n° 2 : le commentaire SQL de la colonne `empreinte` a été raccourci pour tenir dans les 100 caractères de ruff. Le schéma est celui de la spec.
- Utilitaire `dates.decaler_mois(mois, decalage)` ajouté.
- Le skill `pydantic-ai-v2` n'existe pas dans le dépôt (`.claude/skills/` absent). Les conventions ont été reprises du catégoriseur et de `pydantic-ai-slim` 2.56.0 installé.
- Test « nouvelle réponse » : Spotifion a une décision, qui prime sur sa règle. Le test désactive donc la règle du prêt principal, puis classe le prêt de 121,89 € en Divers › Autre.
