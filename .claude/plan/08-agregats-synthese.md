# Spec 08 — Agrégats et agent synthèse

| Champ | Valeur |
| --- | --- |
| Étape du plan | 8 / 10 |
| Type | Code (Claude Code) |
| Complexité | Simple à moyenne |
| Estimation | 1 session |
| Prérequis | Spec 07 terminée |

## Objectif

Calculer par le code, pour un mois, tous les indicateurs du cadrage §8 : vue joint, vue perso, catégories, épargne et historique sur 12 mois. Appliquer les contrôles bloquants qui précèdent toute publication (§11). Faire ensuite commenter ces chiffres par l'agent synthèse, qui ne calcule rien. Le résultat est enregistré pour la publication de l'étape 9.

À la fin de l'étape, sur les fixtures et après les scénarios des specs 06 et 07, `budget synthese --mois 2026-06` affiche un dépassement de 3 471,37 € de l'enveloppe joint et de 556,46 € de l'enveloppe perso, puis le commentaire structuré de l'agent.

## Références cadrage

- §1 Les trois questions auxquelles répondre chaque mois
- §4 Natures de flux : effet de chacune sur les indicateurs
- §5 Structure budgétaire (enveloppes, dotations, charges fixes)
- §8 Indicateurs, et colonnes de l'onglet Synthèse
- §9 Agent synthèse : agrégats JSON en entrée, commentaire structuré (points clés, alertes) en sortie, aucun calcul
- §10 Enveloppes, budgets par sous-catégorie, dépenses annuelles connues, mois particuliers, seuils
- §11 Contrôles bloquants avant publication, confidentialité
- RG-04 (mois de paie), RG-07 (lien à une dépense), RG-08 (provision), RG-09 (remboursements), RG-10 (hors budget), RG-11 (investissements TR), RG-15 (exceptionnels), RG-18 (charges fixes)

## Périmètre

**Inclus**

- `agregats/` : calcul pur des indicateurs d'un mois.
- Contrôles bloquants avant publication.
- Agent synthèse PydanticAI v2.
- Migration `0005` : table `syntheses`.
- Commande `budget synthese`.

**Exclus**

- Publication Google Sheets (étape 9), `budget run` (étape 10).
- Vue consolidée du foyer (hors périmètre du cadrage §2).
- Agent analyste doté d'outils de lecture du ledger (phase 2).

## Principe

Les agrégats d'un mois sont une fonction pure des transactions, des classements et du contexte. Aucun montant n'est calculé par le LLM : il reçoit les agrégats déjà calculés et les commente. Avant le calcul, `budget synthese` reclasse sans question les mois dont dépendent les agrégats, pour que la projection reflète le contexte et les règles en vigueur.

Convention des indicateurs : dépenses, dotations, investissements et épargne sont exprimés en **positif** (montants consommés). Un écart à l'enveloppe vaut `consommation − enveloppe` : positif, c'est un dépassement. Tous les montants restent des `Decimal`.

## Effet de chaque nature

Pour une opération de montant signé `m` (négatif = sortie) :

| Nature | Vue joint (`ca_joint`, `ca_cb`) | Vue perso (`tr`) |
| --- | --- | --- |
| `DEPENSE` | Charge fixe du contexte (détail `charges_fixes.*`) : charges fixes réelles. Sinon dépenses courantes : `−m` | Dépenses : `−m` |
| `REMBOURSEMENT` | Déduit de sa sous-catégorie et des dépenses : `−m` (RG-09) | Idem |
| `LIEN_DEPENSE` | Sous-catégorie de la dépense liée ; `−m` : ajouté s'il sort, déduit s'il entre (RG-07) | Idem |
| `REVENU` | Revenus du mois de paie `mois_rattachement` (RG-04) | Revenus financiers si détail `RG-11:*`, sinon autres revenus |
| `DOTATION` | Dotations : sortie planifiée, hors dépenses | Dotation reçue (information) |
| `PROVISION` | Exclu : reprises et paiements financés sont neutralisés ensemble (RG-08) | — |
| `EPARGNE` | Épargne ponctuelle ; désépargne si `m > 0` | Investissements : consomment l'enveloppe perso (RG-11) |
| `COMPLEMENT` | Sortant : complément versé, consomme l'enveloppe. Entrant : dépassement financé (information) | Idem |
| `HORS_BUDGET` | Listé à part (RG-10) | Idem |
| `NEUTRALISE` | Exclu (RG-03) | — |

Interprétation des compléments (cadrage §4 et §5) : un complément versé quitte le compte émetteur hors de toute dotation, il consomme donc son enveloppe ; le compte receveur l'affiche comme un dépassement financé, sans réduire sa propre consommation (« compléments reçus inclus »). Voir les points ouverts.

## Indicateurs

### Vue joint

| Indicateur | Calcul |
| --- | --- |
| `revenus` | Σ `m` des `REVENU` du joint dont `mois_rattachement` vaut M, quel que soit leur mois budgétaire (RG-04) |
| `revenus_complets` | Vrai si M+1 est importé et classé : un salaire de M reçu début M+1 est alors connu |
| `dotations` | Σ `−m` des `DOTATION` du joint, avec le détail par dotation |
| `charges_fixes_attendues`, `charges_fixes_reelles` | Sommes des rapprochements RG-18 du mois (spec 05), avec la liste des charges hors seuil ou absentes |
| `depenses_courantes` | Σ `−m` des `DEPENSE` hors charges fixes, des `REMBOURSEMENT` et des `LIEN_DEPENSE` du joint |
| `complements_verses`, `complements_recus` | Σ `−m` des `COMPLEMENT` sortants ; Σ `m` des entrants |
| `consommation` | `depenses_courantes + complements_verses` |
| `enveloppe` | `enveloppes.joint_courant` |
| `ecart` | `consommation − enveloppe` |
| `exceptionnels` | Σ `−m` des dépenses taggées exceptionnelles (RG-15), et des liens dont la dépense liée l'est |
| `hors_budget` | Nombre et Σ `m` des `HORS_BUDGET` (RG-10) |

### Vue perso

| Indicateur | Calcul |
| --- | --- |
| `depenses` | Σ `−m` des `DEPENSE`, `REMBOURSEMENT` et `LIEN_DEPENSE` de TR |
| `investissements` | Σ `−m` des `EPARGNE` de TR : exécutions d'ordres, versements PEA, épargne ponctuelle (RG-11) |
| `complements_verses`, `complements_recus` | Comme pour le joint |
| `consommation` | `depenses + investissements + complements_verses` |
| `enveloppe` | `enveloppes.perso` |
| `ecart` | `consommation − enveloppe` |
| `revenus_financiers`, `autres_revenus`, `dotation_recue`, `exceptionnels`, `hors_budget` | Voir le tableau des natures |

### Catégories, par vue

- Montant net d'une sous-catégorie : Σ `−m` de ses `DEPENSE`, `REMBOURSEMENT` et `LIEN_DEPENSE`. Un lien prend la sous-catégorie de sa dépense liée, éventuellement classée le mois précédent. Les charges fixes sont incluses.
- Pour chaque sous-catégorie : catégorie, nature Fixe ou Pilotable (`categories.nature_de`), montant, dont exceptionnel, nombre d'opérations, budget (`budgets_sous_categories`, s'il est renseigné), écart au budget, moyenne des trois mois précédents hors exceptionnels, écart à cette moyenne.
- Moyenne : sur les mois classés parmi M−1 à M−3, divisée par le nombre de mois disponibles (1 à 3), arrondie au centime (`ROUND_HALF_UP`) ; vide si aucun mois n'est disponible. Le nombre de mois utilisés est conservé.
- Pour chaque catégorie : sommes de ses sous-catégories ; budget égal à la somme des budgets renseignés, vide si aucun ne l'est.
- Classement des pilotables (cadrage §8, question 2) : catégories triées par montant pilotable décroissant (sous-catégories Pilotable uniquement), avec leurs écarts au budget et à la moyenne.

### Épargne (onglet Synthèse)

| Indicateur | Calcul |
| --- | --- |
| `dotation_provision` | Σ `−m` des `DOTATION` vers la provision : dotation du contexte nommée `provision` (`NOM_DOTATION_PROVISION`) |
| `investissements` | Ceux de la vue perso |
| `epargne_ponctuelle` | Σ `−m` des `EPARGNE` sortantes du joint |
| `desepargne` | Σ `m` des `EPARGNE` entrantes du joint |
| `nette` | `dotation_provision + investissements + epargne_ponctuelle − desepargne` |

Si aucune dotation du contexte ne s'appelle `provision`, `dotation_provision` vaut 0 et une alerte le signale.

### Historique sur 12 mois

Pour chaque mois classé de M−11 à M : dépenses courantes et consommation du joint, écart joint, consommation et écart perso, exceptionnels, et un indicateur `complet` (le mois passe les contrôles bloquants). S'y ajoutent le cumul et le nombre d'exceptionnels sur la période (cadrage §8, question 3). Un mois est un pic si l'un de ses deux écarts est positif.

### Contexte du mois

Dépenses annuelles connues dont `mois` contient M (nom, montant, sous-catégorie) ; mois particulier de M (sens, motif, montant approximatif) ; alertes du classement par code, celles de RG-18 détaillées par nom de charge.

## Contrôles bloquants avant publication (cadrage §11)

`controler_publication(...)` lève `ControleError` en listant **tous** les manquements : nombre, puis premières occurrences avec fichier, position, date et montant, jamais le libellé ; et la commande à lancer pour les résoudre.

1. Le mois est importé : les contrôles RG-02, RG-03 et Trade Republic sont donc passés.
2. Chaque opération a une nature.
3. Chaque `DEPENSE` et `REMBOURSEMENT` a une sous-catégorie ; chaque `LIEN_DEPENSE` pointe vers une dépense classée qui en a une.
4. Aucun salaire n'attend son mois de paie (RG-04).
5. Pour chaque vue, la somme des sous-catégories égale les dépenses de la vue, au centime : charges fixes réelles plus dépenses courantes pour le joint, dépenses pour le perso.

Le contrôle 5 vérifie la cohérence du code d'agrégation : s'il échoue, on corrige le calcul, jamais on n'ajoute de tolérance. `budget synthese` refuse de produire une synthèse quand un contrôle échoue ; la publication (étape 9) appelle les mêmes contrôles.

## Modèles (`agregats/modeles.py`)

Tous immuables, `extra="forbid"`, montants en `Montant` (le type `Decimal` du projet, qui refuse les `float`).

```python
class LigneSousCategorie(BaseModel):
    sous_categorie: SousCategorie
    nature: NatureDepense
    montant: Montant
    dont_exceptionnel: Montant
    nb_operations: int
    budget: Montant | None
    ecart_budget: Montant | None
    moyenne_3_mois: Montant | None         # hors exceptionnels
    mois_moyenne: int                      # 0 à 3
    ecart_moyenne: Montant | None


class Agregats(BaseModel):
    mois: str
    joint: VueJoint                        # indicateurs ci-dessus + categories + pilotables
    perso: VuePerso
    epargne: Epargne
    historique: tuple[PointMensuel, ...]
    exceptionnels_12_mois: Montant
    nb_exceptionnels_12_mois: int
    exceptionnels_detail: tuple[DepenseExceptionnelle, ...]   # date, compte, montant, sous-catégorie, marchand
    depenses_annuelles_du_mois: tuple[DepenseAnnuelle, ...]
    mois_particulier: MoisParticulier | None
    alertes: tuple[ResumeAlerte, ...]
```

`DepenseExceptionnelle.marchand` vaut la clé marchand masquée pour un paiement carte, `None` sinon.

`calculer_agregats(mois, transactions_par_mois, classements_par_mois, rapprochements, contexte) -> Agregats` ne lit ni base ni fichier.

## Agent synthèse (`agents/synthese.py`)

Consulter la documentation PydanticAI v2 comme pour la spec 07, et réutiliser ses conventions (validateur de sortie, relances, `FunctionModel` dans les tests, `ALLOW_MODEL_REQUESTS = False`).

**Entrée** : `EntreeSynthese` = les `Agregats` et un extrait du contexte (enveloppes, budgets renseignés, seuil exceptionnel, dépenses annuelles connues, mois particuliers), sérialisés en JSON, montants en chaînes décimales, jamais en `float`.

**Confidentialité** :

- aucun libellé d'opération ; seuls les marchands des dépenses exceptionnelles payées par carte, sous forme de clé marchand masquée (des libellés marchands de paiements carte, autorisés par le cadrage §11) ;
- aucun libellé du contexte (`libelle_contient`, `reprises_libelle_contient`, `periode_regex`) ni nom de dotation ou de salaire : les totaux suffisent ;
- `masquer()` (spec 07) appliqué au JSON entier, en défense en profondeur ;
- un test vérifie qu'aucun libellé de transaction hors carte, aucun libellé du contexte et aucun IBAN n'apparaît dans le prompt.

**Sortie**

```python
class CommentaireSynthese(BaseModel):          # frozen, extra="forbid"
    resume: str = Field(max_length=280)        # colonne « Commentaire » de l'onglet Synthèse
    points_cles: list[str] = Field(min_length=1, max_length=5)
    alertes: list[str] = Field(max_length=5)   # dépassements, charges RG-18 hors seuil, revenus incomplets
    explication_pic: str | None                # obligatoire si un écart est positif
```

**Instructions** (en français) : répondre pour le mois aux trois questions du cadrage §1 ; ne citer que des montants présents dans les données ; ne rien calculer ; expliquer un dépassement par les catégories, les dépenses exceptionnelles, les dépenses annuelles connues ou le mois particulier ; style factuel et bref.

**Validateur de sortie** :

- chaque montant en euros cité (`1 234,56 €`, `1 200 €`, `-50 €`) est lu avec `parse_montant`, et sa valeur absolue doit figurer parmi les valeurs absolues des montants de l'entrée. Sinon `ModelRetry` qui liste les montants inconnus et rappelle de citer uniquement les montants fournis. Les pourcentages ne sont pas contrôlés ;
- `explication_pic` est renseignée si et seulement si l'écart joint ou l'écart perso est positif.

Deux relances au plus. Au-delà, la synthèse est enregistrée sans commentaire, avec un avertissement : l'absence de commentaire ne bloque pas la publication.

**Modèle** : `contexte.llm.modele_synthese` (défaut `anthropic:claude-sonnet-5`). Ne pas fixer `temperature`.

## Persistance (migration 0005)

```sql
CREATE TABLE syntheses (
    mois          TEXT PRIMARY KEY,
    empreinte     TEXT NOT NULL,     -- sha256 de l'entrée de l'agent (JSON canonique)
    agregats      TEXT NOT NULL,     -- JSON des Agregats, montants en chaînes décimales
    commentaire   TEXT,              -- JSON du CommentaireSynthese ; NULL sans LLM ou après échec
    modele        TEXT,
    calculee_le   TEXT NOT NULL,
    commentee_le  TEXT
);
```

L'empreinte est le sha256 du JSON canonique (clés triées) de l'entrée, du nom du modèle et de `VERSION_CONSIGNES_SYNTHESE`. Si elle n'a pas changé et qu'un commentaire existe, il est réutilisé sans appel ; `--regenerer` force un nouvel appel. Les agrégats sont toujours recalculés et réécrits. La publication (étape 9) lit cette table.

## CLI

`budget synthese --mois AAAA-MM [--base …] [--contexte …] [--sans-llm] [--regenerer]` :

1. Reclasse sans question les mois importés de M−11 à M+1 (classement de la spec 07, sans interlocuteur ni LLM).
2. Contrôles bloquants sur M : en cas d'échec, message et code 1.
3. Agrégats ; dans l'historique, un mois qui échoue aux contrôles est marqué incomplet.
4. Commentaire, réutilisé si l'empreinte n'a pas changé, sauf `--sans-llm` ou clé absente (avertissement).
5. Enregistrement, puis affichage Rich : vue joint, vue perso, catégories pilotables, épargne, alertes, commentaire.

## Organisation du code

```
src/budget/
├── agregats/
│   ├── __init__.py
│   ├── modeles.py      # Agregats, VueJoint, VuePerso, LigneSousCategorie, Epargne, PointMensuel
│   ├── calcul.py       # calculer_agregats : fonction pure
│   └── controles.py    # controler_publication
├── agents/
│   └── synthese.py     # EntreeSynthese, CommentaireSynthese, agent, validateur
└── ledger/
    └── syntheses.py    # lecture et écriture de la table syntheses
```

Le `Ledger` expose aussi la lecture des classements rattachés à un mois de paie, tous mois budgétaires confondus (revenus RG-04).

## Valeurs attendues sur les fixtures de juin 2026

Avec `contexte.example.yaml`, après le scénario de la spec 06 et celui de la spec 07 (OliverStore taggé exceptionnel), juillet non importé. Ces valeurs ne dépendent pas des sous-catégories choisies dans le scénario.

**Vue joint**

| Indicateur | Valeur |
| --- | --- |
| Revenus (mois de paie juin) | 5 255,47 € : `GROUPAMA` 2 729,05 € et `PLUME 06/2026` 2 526,42 € ; `revenus_complets` faux. Les 2 303,31 € de `PLUME 05/2026` vont à mai |
| Dotations | 1 350,00 € : `perso` 500,00 €, `perso_conjoint` 550,00 €, `provision` 300,00 € |
| Charges fixes attendues / réelles | 2 797,25 € / 2 797,25 €, aucune hors seuil |
| Dépenses courantes | 3 771,37 € : 52 dépenses hors charges fixes (3 909,16 €), moins 3 remboursements (387,79 €), plus le lien « Velo » (250,00 €) |
| Compléments versés | 900,00 € (500,00 € et 400,00 €) |
| Consommation / enveloppe / écart | 4 671,37 € / 1 200,00 € / +3 471,37 € |
| Exceptionnels | 883,16 € |
| Hors budget | 9 opérations, +3 234,98 € net |
| Somme des sous-catégories | 6 568,62 € (charges fixes réelles + dépenses courantes) |

**Vue perso**

| Indicateur | Valeur |
| --- | --- |
| Dépenses | 994,96 € : 58 dépenses (1 373,86 €), moins 2 remboursements (128,90 €), moins le lien « Velo » reçu (250,00 €) |
| Investissements | 61,50 € : 6 exécutions d'ordre (46,30 €) et 1 versement PEA (15,20 €) |
| Consommation / enveloppe / écart | 1 056,46 € / 500,00 € / +556,46 € |
| Compléments reçus | 0,00 € |
| Revenus financiers / autres revenus | 11,91 € (bonus 8,54 €, intérêts 2,21 €, dividende 1,16 €) / 2,90 € |
| Dotation reçue | 500,00 € |
| Hors budget | 1 opération, +100,00 € |
| Somme des sous-catégories | 994,96 € |

**Épargne** : 300,00 € de dotation provision + 61,50 € d'investissements, sans épargne ponctuelle ni désépargne, soit 361,50 € nets.

**RG-07** : par rapport à un calcul sans le lien, la sous-catégorie de DECATHLON 409,97 € perd 250,00 € en vue perso (contribution nette du vélo : 159,97 €) et en gagne 250,00 € en vue joint.

**Contrôles** : tous passent après les scénarios ; sans le scénario de la spec 07, le contrôle 3 échoue et cite le nombre de paiements carte sans sous-catégorie.

Les valeurs de juillet (après les scénarios de juin et des réponses de juillet à figer) sont relevées à la première exécution, puis figées, dont la moyenne sur 3 mois calculée sur le seul mois de juin.

## Dépendances autorisées

Aucune nouvelle dépendance.

## Critères d'acceptation

- [ ] Valeurs de juin des tableaux ci-dessus, vérifiées par un test golden.
- [ ] Chaque ligne du tableau « Effet de chaque nature » est couverte par un test synthétique, dont RG-07 (lien sortant ajouté, lien entrant déduit, dépense liée classée le mois précédent), RG-08 (provision exclue), RG-09, RG-10 et RG-11.
- [ ] RG-04 : un salaire de juin reçu le 02/07 (cas synthétique, juillet importé) compte dans les revenus de juin, et `revenus_complets` devient vrai.
- [ ] Moyenne sur 3 mois : 0, 1 et 3 mois disponibles ; exceptionnels exclus ; arrondi au centime.
- [ ] Budgets : sous-catégorie avec et sans budget, budget de catégorie comme somme des budgets renseignés.
- [ ] Contrôles bloquants : chacun des cinq cas produit une `ControleError` qui cite tous les manquements, sans libellé.
- [ ] Agent synthèse : un `FunctionModel` qui cite un montant absent des agrégats provoque une relance ; qui omet `explication_pic` alors qu'un écart est positif aussi ; après épuisement des relances, synthèse enregistrée sans commentaire.
- [ ] Confidentialité : aucun libellé hors carte, aucun libellé du contexte, aucun nom de dotation ni IBAN dans le prompt (test sur les messages capturés).
- [ ] Empreinte : deux `budget synthese` successifs ne font qu'un appel ; `--regenerer` en force un second ; un changement d'agrégat en provoque un.
- [ ] `budget synthese` : code 1 si un contrôle échoue ; affichage complet sinon ; `--sans-llm` sans appel.
- [ ] Migration 0005 sur une base en version 4.
- [ ] Aucun `float` dans les agrégats ni dans le JSON envoyé (test récursif sur les types).
- [ ] Les tests `donnees_reelles` (sans LLM) calculent juin et juillet réels quand les données sont présentes et que les questions y ont été répondues ; sinon ils vérifient que les contrôles échouent avec un message exploitable.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_agregats_natures.py` | Effet de chaque nature, vue par vue, sur des cas synthétiques |
| `test_agregats_categories.py` | Sous-catégories, liens RG-07, budgets, moyenne sur 3 mois, classement des pilotables |
| `test_agregats_golden.py` | Valeurs de juin sur les fixtures après les scénarios des specs 06 et 07 |
| `test_controles_publication.py` | Les cinq contrôles bloquants |
| `test_agent_synthese.py` | `FunctionModel` : entrée, confidentialité, validateur, relances, échec |
| `test_syntheses.py` | Persistance, empreinte, réutilisation du commentaire |
| `test_ledger_migrations.py`, `test_cli.py` (ajouts) | Migration 0005 ; `budget synthese` en succès, en échec de contrôle, `--sans-llm` |

## Points ouverts

- **Compléments** : l'interprétation ci-dessus (un complément versé consomme l'enveloppe de l'émetteur, un complément reçu est un dépassement financé) est à confirmer au premier run réel. En juin, les deux virements de 500 € et 400 € vers le compte du conjoint, répondus « complément » dans le scénario de test, expliquent l'essentiel du dépassement joint.
- **Charges du compte perso** (Navigo, Orange) : comptées dans les dépenses perso, faute de charges fixes perso dans le contexte (spec 05, points ouverts).
- **Dépenses annuelles connues** : elles expliquent un pic dans le commentaire, mais ne lissent pas le budget. Un budget lissé (provision mensuelle des dépenses annuelles) serait une évolution du cadrage.
- **Modèle de synthèse** : le cadrage retient Claude Sonnet 5 (`anthropic:claude-sonnet-5`). Claude Sonnet 5.5 (`anthropic:claude-sonnet-5-5`) est plus récent, au même tarif (2 $ / 10 $ par million de tokens). Le choix reste dans `contexte.yaml`.
- **Noms du contexte** : les noms de charges fixes et de dépenses annuelles sont envoyés au LLM. Ils doivent rester neutres (`garde_enfant`, pas un prénom) ; `budget config verifier` pourrait le rappeler.

## Écarts constatés

À compléter pendant l'implémentation.
