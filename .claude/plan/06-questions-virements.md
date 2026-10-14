# Spec 06 — Questions sur les virements et liens à une dépense

| Champ | Valeur |
| --- | --- |
| Étape du plan | 6 / 10 |
| Type | Code (Claude Code) |
| Complexité | Moyenne |
| Estimation | 1 à 2 sessions |
| Prérequis | Spec 05 terminée |

## Objectif

Classer par une question posée dans le terminal, sans LLM, chaque opération hors paiement carte restée non classée après l'étape 5 : virements entre comptes (RG-06), liens à une dépense (RG-07), PayPal et chèques (RG-14), prélèvements et opérations diverses inconnus. Elle tranche aussi le mois de paie des salaires reçus hors fenêtre (RG-04). Chaque réponse est enregistrée, et peut devenir une règle réutilisable : **une question tranchée ne revient jamais**.

À la fin de l'étape, un premier `budget classify --mois 2026-06` pose 25 questions sur les fixtures ; un second n'en pose aucune ; et `budget classify --mois 2026-07` réutilise les règles créées en juin.

## Références cadrage

- §4 Natures de flux (dotation, épargne, complément, lien à une dépense, remboursement, hors budget)
- §7 Mémoire et interaction humaine (ordre de classement, types de règles, priorité, déroulé des questions)
- §9 Architecture (questions posées par du code, nœud D ; outils différés en phase 2)
- §11 Confidentialité (rien de ce qui est traité ici ne part au LLM)
- RG-04 (mois de paie à déterminer), RG-06 (virements), RG-07 (lien à une dépense), RG-09 (remboursements), RG-10 (hors budget), RG-14 (toujours demander)

## Périmètre

**Inclus**

- Migration `0003` : tables `regles`, `questions`, `decisions`.
- Moteur de règles apprises pour les types `virement`, `prelevement` et `toujours_demander` : correspondance, priorité, refus des conflits.
- Construction des questions, dialogue terminal, recherche des dépenses candidates (RG-07).
- Intégration dans `budget classify` : décisions et règles appliquées après le contexte, puis questions.

**Exclus**

- Paiements carte (fichier cartes et `Avoir` TR) : leur sous-catégorie vient de l'étape 7 (règles marchand, LLM, validation en lot).
- Règles de type `marchand_carte`, RG-13, tags exceptionnels (RG-15), journal `decisions.md`, commandes `budget rules list` et `budget rules disable` (étape 7).
- Calcul des montants nets induits par les liens (RG-07) dans les indicateurs (étape 8).
- Questions asynchrones via le Sheet (phase 2).

**Choix de périmètre** : le plan nomme cette étape « questions sur les virements ». Elle couvre aussi les autres opérations hors carte (PayPal, chèques, prélèvements inconnus, cotisations, régularisations), car le LLM ne reçoit que des libellés de paiements carte (cadrage §11) : ces opérations ne peuvent être classées que par règle ou par question, avec le même mécanisme.

## Ordre de classement complet

`classer_mois` (spec 05) devient, pour chaque transaction :

| Ordre | Source | Origine |
| --- | --- | --- |
| 1 | Contexte et règles fixes (spec 05) | `contexte`, `regle_fixe` |
| 2 | Décision enregistrée pour cette transaction | `decision` |
| 3 | Règle apprise applicable (hors cas RG-14) | `regle` |
| 4 | Question (si le mode interactif est actif) | `decision` après réponse |

Une décision prime sur une règle : elle porte sur une opération précise. Une règle créée plus tard ne modifie donc jamais une réponse déjà donnée.

Pour un virement interne apparié (spec 05), décisions et règles s'appliquent à la paire : la règle est cherchée sur le côté joint, et la nature retenue s'applique aux deux côtés.

## Opérations concernées par les questions

Après les étapes 1 à 3, une transaction non classée qui n'est pas un paiement carte produit une question :

| Type de question | Opérations | Motif |
| --- | --- | --- |
| `virement` | `ca_joint` de type `Virement émis` ou `Virement en votre faveur` ; `tr` de type `Virement` (hors versement PEA) | RG-06 |
| `operation` | Toutes les autres : prélèvements sans charge fixe ni règle, PayPal, chèques émis et remises de chèque, cotisations, avoirs et régularisations du joint, retraits, prélèvements TR | RG-14 si PayPal ou chèque, sinon `non_classe` |

Une paire de virements internes produit **une seule** question, portée par le côté joint.

S'y ajoute un troisième type, indépendant du classement en nature :

| Type de question | Opérations | Motif |
| --- | --- | --- |
| `salaire` | Salaires classés `REVENU` par le contexte, dont `mois_rattachement` est vide (spec 04, cas 5 de RG-04) | RG-04 |

La décision ne change pas la nature (`REVENU`, donnée par le contexte) : elle fournit seulement le mois de paie, appliqué à `mois_rattachement` lors du classement. Aucune règle n'est proposée, puisque le rattachement dépend de la date de réception de chaque versement.

## Moteur de règles apprises (`regles.py`)

### Modèle

```python
class Regle(BaseModel):                    # frozen, extra="forbid"
    id: int | None                         # None avant insertion
    type: TypeRegle                        # virement | prelevement | marchand_carte | toujours_demander
    source: Source
    critere: Critere                       # ics | libelle_exact | libelle_prefixe
    valeur: str                            # ICS, ou libellé normalisé
    montant_min: Decimal | None            # signés ; min == max pour un montant exact
    montant_max: Decimal | None
    nature: Nature | None                  # None pour toujours_demander
    sous_categorie: SousCategorie | None
    detail: str | None                     # ex. nom de la dotation : "dotations.perso_conjoint"
    active: bool = True
```

`marchand_carte` est déclaré dans l'énumération et dans le schéma, mais n'est ni créé ni appliqué avant l'étape 7.

Validateurs : `montant_min` et `montant_max` tous deux renseignés ou tous deux absents, et `min ≤ max` ; une règle `virement` a obligatoirement une condition de montant (cadrage §7 et §12 : les règles de virement sont liées au montant) ; `nature` obligatoire sauf pour `toujours_demander` ; `sous_categorie` obligatoire si `nature` vaut `DEPENSE` ou `REMBOURSEMENT` ; une `LIEN_DEPENSE` n'est jamais une règle (cas unique par définition).

### Correspondance

Une règle active s'applique à une transaction si :

- même `source` ;
- `ics` : `transaction.ics == valeur` ; `libelle_exact` : `libelle_normalise == valeur` ; `libelle_prefixe` : `libelle_normalise.startswith(valeur)` ;
- condition de montant, si présente : `montant_min ≤ montant ≤ montant_max` (montants signés : une règle sur une sortie ne s'applique pas à une entrée).

### Priorité (cadrage §7)

La règle la plus spécifique l'emporte. La spécificité est le triplet comparé dans l'ordre :

1. critère : `ics` (3) > `libelle_exact` (2) > `libelle_prefixe` (1) ;
2. condition de montant : montant exact (2) > plage (1) > aucune (0) ;
3. longueur de `valeur` (un préfixe plus long est plus spécifique).

Une règle `toujours_demander` applicable l'emporte sur toute autre règle : la transaction produit une question.

### Conflits

À la création, une règle est **refusée** (`RegleError`, message actionnable) si une règle active existe avec les mêmes `type`, `source`, `critere` et `valeur`, la même spécificité, des conditions de montant qui se recouvrent (l'absence de condition recouvre tout), et un résultat différent (`nature`, `sous_categorie` ou `detail`). Si le résultat est identique, elle est refusée comme doublon (« déjà couverte par la règle n° 4 »).

Si deux règles de même spécificité et de résultats différents s'appliquent malgré tout à une transaction (base modifiée à la main), `ControleError` : le classement s'arrête.

### RG-14 : toujours demander

Indépendamment des règles apprises, ces opérations produisent toujours une question, et aucune règle n'est proposée après la réponse :

- prélèvement PayPal : ICS `LU96ZZZ0000000000000000058` (constante `ICS_PAYPAL`) ;
- chèques : type d'opération `Chèque emis` ou `Remise de chèque`, comparés normalisés.

L'utilisateur peut ajouter d'autres cas en répondant `t` à la proposition de règle (règle `toujours_demander`), par exemple pour un virement sans motif explicite.

## Questions et dialogue

### Interlocuteur

Le dialogue passe par un protocole, pour être testable sans terminal et réutilisable en phase 2 (outils différés) :

```python
class Interlocuteur(Protocol):
    def afficher(self, contenu: RenderableType) -> None: ...
    def demander(self, invite: str, choix_valides: Sequence[str]) -> str: ...
```

- `InterlocuteurTerminal` : Rich, ressaisie tant que la réponse n'est pas dans `choix_valides`.
- `InterlocuteurScripte` (tests) : rejoue une liste de réponses et échoue si elle est épuisée ou si une réponse est invalide.

### Déroulé (cadrage §7, bloc 1)

Les questions sont posées une par une, virements d'abord, puis salaires, puis opérations, chacune dans l'ordre chronologique (joint avant TR à date égale). En-tête : « Question 11/25 ».

Affichage d'une question : compte, date, montant signé, type d'opération, libellé, et pour une paire les deux côtés. Le terminal est local : le libellé y est affiché, mais il n'apparaît jamais dans un log.

```
Question 11/25 — Virement (RG-06)
  Joint · 15/06/2026 · −250,00 € · Virement émis
  Perso · 15/06/2026 · +250,00 € · Virement (contrepartie)
  VIR INST vers PRENOM NOM - Velo

  1. Dotation            2. Épargne             3. Complément
  4. Lien à une dépense  5. Dépense             6. Hors budget
  s. Passer              q. Quitter
>
```

**Choix pour une question `virement`**

| Choix | Sortie (montant négatif) | Entrée (montant positif) | Suite |
| --- | --- | --- | --- |
| Dotation | `DOTATION` | `DOTATION` | Choisir la dotation du contexte (numérotées) |
| Épargne / Désépargne | `EPARGNE` | `EPARGNE` (désépargne) | — |
| Complément | `COMPLEMENT` | `COMPLEMENT` | — |
| Lien à une dépense | `LIEN_DEPENSE` | `LIEN_DEPENSE` | Choisir la dépense (RG-07) |
| Dépense / Remboursement | `DEPENSE` | `REMBOURSEMENT` (RG-09) | Choisir la sous-catégorie |
| Revenu | — | `REVENU` | — |
| Hors budget | `HORS_BUDGET` | `HORS_BUDGET` (RG-10) | — |

RG-06 liste dotation, épargne, désépargne, complément, lien et hors budget. Les choix Dépense, Remboursement et Revenu s'y ajoutent pour les virements vers ou depuis des tiers : paiement d'une entreprise par virement, remboursement de mutuelle (RG-09), remboursement d'impôt.

**Choix pour une question `operation`** : Dépense (sortie) ou Remboursement (entrée) avec sous-catégorie, Revenu (entrée), Hors budget.

**Choix pour une question `salaire`** : les `mois_possibles` calculés par RG-04 (mois précédant la réception, puis mois de réception), plus `s` et `q`.

```
Question 14/26 — Salaire (RG-04)
  Joint · 10/06/2026 · +2 729,05 € · salaire_1
  Reçu hors de la fenêtre du 25 au 5, sans période dans le libellé.
  Mois de paie ?   1. 2026-05   2. 2026-06   s. Passer   q. Quitter
>
```

**Sous-catégorie** : deux menus numérotés, catégorie puis sous-catégorie, construits depuis `categories.py`.

**Passer** (`s`) laisse la question ouverte : elle sera reposée au prochain `classify`. **Quitter** (`q`, ou Ctrl+C) arrête le dialogue ; les réponses déjà données sont conservées.

### RG-07 : dépenses candidates

Pour le choix « Lien à une dépense », l'agent propose les dépenses candidates des deux comptes suivis :

- transactions de montant négatif dont la nature est `DEPENSE` ou qui sont encore non classées, hors charges fixes (spec 05), hors virement en cours de traitement ;
- datées à moins de 31 jours de l'opération (`FENETRE_LIEN_JOURS = 31`), dans le mois classé ou le mois précédent s'il est importé ;
- de montant absolu au moins égal à la moitié du montant du virement (`RATIO_LIEN_MINIMUM = Decimal("0.5")`) ;
- non déjà liées à un autre virement ;
- triées par écart de date croissant, 10 par page (`p` pour la page suivante, `a` pour annuler et revenir au choix de nature).

Le choix enregistre `lien_id` sur l'opération questionnée et, pour une paire, sur ses deux côtés. La sous-catégorie n'est pas recopiée : le lien la rend dépendante de celle de la dépense, résolue à l'agrégation (étape 8). Une dépense liée encore non classée garde sa propre question.

Effet attendu à l'étape 8 (exemple du cadrage) : DECATHLON 409,97 € sur TR, 250 € reçus du joint et liés : 159,97 € net en vue perso, 250 € en vue joint dans la catégorie du vélo.

### Proposition de règle (cadrage §7)

Après chaque réponse, sauf `LIEN_DEPENSE` et cas RG-14 :

```
En faire une règle ?  o. Oui   n. Non (cette opération seulement)   t. Toujours demander
```

Sur `o`, le type et le critère sont déduits de l'opération :

| Opération | Type | Critère proposé | Condition de montant |
| --- | --- | --- | --- |
| Virement | `virement` | `libelle_exact` | Obligatoire : `e` montant exact (défaut) ou `p` plage saisie (`500-600`) |
| Avec ICS | `prelevement` | `ics` | Facultative : aucune (défaut), exact ou plage |
| Sans ICS | `prelevement` | `libelle_exact` ; `x` pour saisir un préfixe du libellé normalisé | Facultative |

Une règle refusée pour conflit affiche le motif et la règle en conflit, puis revient à la proposition (`n` reste possible). Sur `t`, une règle `toujours_demander` est créée avec le critère proposé et sans condition de montant.

### Persistance (migration 0003)

```sql
CREATE TABLE questions (
    id               INTEGER PRIMARY KEY,
    transaction_id   TEXT NOT NULL UNIQUE REFERENCES transactions (id),
    contrepartie_id  TEXT REFERENCES transactions (id),
    type             TEXT NOT NULL CHECK (type IN ('virement', 'salaire', 'operation')),
    motif            TEXT NOT NULL,                  -- 'RG-06', 'RG-14', 'non_classe'
    posee_le         TEXT NOT NULL,
    repondue_le      TEXT
);

CREATE TABLE regles (
    id                    INTEGER PRIMARY KEY,
    type                  TEXT NOT NULL
                          CHECK (type IN ('virement', 'prelevement', 'marchand_carte', 'toujours_demander')),
    source                TEXT NOT NULL,
    critere               TEXT NOT NULL CHECK (critere IN ('ics', 'libelle_exact', 'libelle_prefixe')),
    valeur                TEXT NOT NULL,
    montant_min_centimes  INTEGER,
    montant_max_centimes  INTEGER,
    nature                TEXT,
    sous_categorie        TEXT,
    detail                TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    creee_le              TEXT NOT NULL,
    question_id           INTEGER REFERENCES questions (id),
    CHECK ((montant_min_centimes IS NULL) = (montant_max_centimes IS NULL))
);

CREATE TABLE decisions (
    transaction_id  TEXT PRIMARY KEY REFERENCES transactions (id),
    question_id     INTEGER NOT NULL REFERENCES questions (id),
    nature          TEXT NOT NULL,
    sous_categorie  TEXT,
    detail          TEXT,
    lien_id         TEXT REFERENCES transactions (id),
    mois_paie       TEXT,                            -- question salaire (RG-04)
    regle_id        INTEGER REFERENCES regles (id),  -- règle créée à partir de cette réponse
    decidee_le      TEXT NOT NULL
);
```

- Chaque réponse est enregistrée **immédiatement**, dans sa propre transaction SQL (question, décision pour chaque côté, règle éventuelle) : quitter en cours de route ne perd rien.
- Une décision est toujours créée, même quand la réponse produit une règle : c'est l'historique dont le journal `decisions.md` de l'étape 7 aura besoin (date, question, réponse, règle créée).
- Les transactions ont des `id` stables (RG-16) : décisions et règles survivent à un réimport.

### Organisation du code

```
src/budget/
├── regles.py               # Regle, correspondance, priorité, conflits, RG-14
├── questions/
│   ├── __init__.py
│   ├── construction.py     # quelles questions, dans quel ordre (fonctions pures)
│   ├── candidats.py        # RG-07
│   ├── dialogue.py         # Interlocuteur, InterlocuteurTerminal, déroulé
│   └── reponses.py         # réponse -> décision et règle éventuelle
└── ledger/
    └── memoire.py          # lecture et écriture des règles, questions, décisions
```

Ajout dans `errors.py` : `RegleError(BudgetError)` (règle invalide ou en conflit).

### CLI

`budget classify --mois AAAA-MM [--base …] [--contexte …] [--sans-questions]` :

1. Classement (contexte, décisions, règles) et enregistrement, comme à l'étape 5.
2. Sauf avec `--sans-questions` : dialogue sur les questions à poser, puis nouveau classement pour intégrer les réponses.
3. Résumé : questions posées, répondues, passées ; règles créées ; opérations encore non classées, dont paiements carte en attente de l'étape 7.

Si l'entrée standard n'est pas un terminal, les questions ne sont pas posées (comme `--sans-questions`) et le résumé indique leur nombre. Code 0 dans tous les cas sans erreur : des questions en attente ne sont pas une erreur à cette étape. Le contrôle bloquant « chaque opération a une nature » intervient avant la publication (étape 8).

## Valeurs attendues sur les fixtures de juin 2026

Avec `contexte.example.yaml` et une base sans règle ni décision.

| Élément | Valeur |
| --- | --- |
| Questions au premier `classify` | 25 |
| Questions `virement` | 16 : 13 côté joint (dont la paire « Velo »), 3 côté TR non appariés (Etsy 2,90 €, 100,00 € du 02/06, IGESTION-BCAC 77,90 €) |
| Questions `operation` | 9 : 3 PayPal (RG-14), 1 remise de chèque (RG-14), 1 cotisation carte 144,00 €, 2 opérations X9817, 2 prélèvements TR (Navigo, Orange) |
| Paiements carte non questionnés | 47 + 1 (cartes), 56 + 1 (TR) : en attente de l'étape 7 |

**Scénario de test scripté** (`tests/scenarios/juin.py`, réponses de référence) :

| Opération | Réponse | Règle |
| --- | --- | --- |
| Paire « Velo » 250 € | Lien à une dépense : DECATHLON 409,97 € (TR, 08/06) | Aucune (cas unique) |
| Virement du mois 550 € | Dotation `perso_conjoint` | Virement, libellé exact, plage 500-600 € |
| Spotifion 7 € | Dépense `abonnements.streaming_musique` | Virement, libellé exact, montant exact |
| IGESTION-BCAC +77,90 € | Remboursement `sante.optique` (RG-09) | Aucune |
| Wero +10 €, Revolut +140 €, remise de chèque +585 €, dépôt de garantie +1 806 €, clôtures BNPP et BoursoBank | Hors budget (RG-10) | Aucune |
| Prélèvement Orange (TR) | Dépense `abonnements.internet_mobile` | Prélèvement, préfixe saisi |
| Prélèvement Navigo (TR) | Dépense `transport.transports_commun` | Prélèvement, libellé exact |
| PayPal × 3 | Dépense, sous-catégories au choix | Non proposée (RG-14) |
| Autres | Réponses figées dans le scénario | Selon le scénario |

Résultats attendus :

- Après le scénario, plus aucune opération hors carte n'est non classée en juin.
- Les deux côtés de « Velo » sont `LIEN_DEPENSE` avec `lien_id` vers DECATHLON 409,97 €.
- Un second `classify` de juin ne pose aucune question et produit des classements identiques.
- Sur juillet, après juin : le virement Spotifion du 01/07 (7 €) et le prélèvement Orange du 27/07 sont classés par règle sans question ; les 2 PayPal et le chèque émis de 18,45 € sont posés (RG-14). Le nombre de questions de juillet est relevé à la première exécution, puis figé.

## Dépendances autorisées

Aucune nouvelle dépendance : la saisie utilise Rich (déjà présent).

## Critères d'acceptation

- [ ] Juin : 25 questions au premier passage, réparties comme ci-dessus ; une seule question pour la paire « Velo ».
- [ ] RG-04 : un salaire synthétique reçu le 10/06 sans période produit une question `salaire` à deux choix (`2026-05`, `2026-06`) ; après réponse, `mois_rattachement` vaut le mois choisi, la nature reste `REVENU`, aucune règle n'est proposée, et la question ne revient pas.
- [ ] Le scénario scripté de juin s'exécute de bout en bout avec `InterlocuteurScripte` ; un second passage pose 0 question.
- [ ] Juillet après juin : règles réutilisées et questions RG-14 reposées, comme décrit.
- [ ] RG-06 : tout virement non couvert par le contexte, une décision ou une règle produit une question.
- [ ] RG-07 : les candidates respectent fenêtre, ratio, exclusions et tri ; DECATHLON 409,97 € figure dans la première page pour « Velo » ; le lien est enregistré sur les deux côtés de la paire ; aucune règle n'est proposée.
- [ ] RG-09 et RG-10 : un remboursement reçoit une sous-catégorie ; un hors budget n'en a pas.
- [ ] RG-14 : PayPal et chèques sont toujours posés, même si une règle correspondante existe en base, et aucune règle n'est proposée.
- [ ] Priorité : ICS > libellé exact > préfixe ; condition de montant > sans condition ; préfixe long > préfixe court (tests unitaires sur des règles synthétiques).
- [ ] Conflits : deux règles de même spécificité, à montants recouvrants et résultats différents, sont refusées à la création ; un doublon est refusé ; deux plages disjointes sur le même libellé sont acceptées.
- [ ] Une règle `virement` sans condition de montant et une règle de `LIEN_DEPENSE` sont refusées par le modèle.
- [ ] Une décision prime sur une règle créée ensuite et qui s'appliquerait à la même opération.
- [ ] Quitter après 3 réponses conserve ces 3 décisions ; le passage suivant ne repose que les questions restantes.
- [ ] `--sans-questions` et une entrée non interactive ne posent aucune question.
- [ ] Aucun libellé dans les logs ; aucun appel réseau ni import de `pydantic_ai` dans ce code.
- [ ] Les tests `donnees_reelles` (avec `--sans-questions`) passent sur juin et juillet quand les données sont présentes.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_regles.py` | Validation du modèle, correspondance, priorité, conflits, doublons, RG-14 |
| `test_questions_construction.py` | Sélection et ordre des questions, paire unique, question salaire (RG-04), exclusion des paiements carte |
| `test_candidats_lien.py` | RG-07 : fenêtre, ratio, exclusions, tri, pagination, mois précédent |
| `test_dialogue.py` | `InterlocuteurScripte` : choix par type, sous-catégorie à deux niveaux, passer, quitter, ressaisie, proposition de règle et conflit |
| `test_memoire.py` | Persistance immédiate, décision et règle liées, survie à un réimport |
| `test_classement_questions_golden.py` | Scénario de juin, second passage, juillet après juin |
| `test_ledger_migrations.py` (ajouts) | Migration 0003 sur une base en version 2 |
| `test_cli.py` (ajouts) | `classify --sans-questions`, entrée non interactive |

## Points ouverts

- **Virements sans motif explicite (RG-14)** : le cadrage les range dans « toujours demander », sans critère objectif pour les reconnaître. La spec laisse l'utilisateur le décider au cas par cas (`t` à la proposition de règle) ; à revoir après le premier run réel.
- **Vers le compte de Sarah** : trois virements en juin (500 €, 400 €, 550 €) pour une dotation attendue de 500 €. Le premier run tranchera lequel est la dotation (point ouvert §13) ; le contexte pourra ensuite être mis à jour.
- **Montant partiel d'un lien** : un virement de 250 € lié à une dépense de 409,97 € finance une partie de la dépense. Si un virement finance plusieurs dépenses, le modèle (un seul `lien_id`) ne suffit pas : à traiter si le cas se présente.
- **Correction d'une réponse** : pas de commande pour revenir sur une décision au MVP. En attendant, supprimer la décision en base relance la question. À couvrir avec `budget rules disable` à l'étape 7, ou par une commande dédiée.

## Écarts constatés

_À compléter pendant l'implémentation._
