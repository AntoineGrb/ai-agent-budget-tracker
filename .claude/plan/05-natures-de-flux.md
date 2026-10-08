# Spec 05 — Natures de flux : dotations, provision, charges fixes, neutralisations

| Champ | Valeur |
| --- | --- |
| Étape du plan | 5 / 10 |
| Type | Mixte : remplir `data/contexte.yaml` dans le projet Claude.ai, puis le moteur dans Claude Code |
| Complexité | Moyenne |
| Estimation | 2 sessions |
| Prérequis | Spec 04 terminée ; `data/contexte.yaml` rempli et validé par `budget config verifier` (section Préparation) |

## Objectif

Attribuer une nature de flux à toutes les opérations qu'on peut classer **sans question et sans LLM**, à partir du fichier de contexte et de règles fixes : neutralisation des débits différés, salaires, dotations, provision, charges fixes, types Trade Republic, paiements carte. Rapprocher chaque charge fixe de son montant attendu et signaler les anomalies.

Les opérations restantes sont marquées « non classées » : elles alimenteront les questions de l'étape 6. Les paiements carte reçoivent leur nature ici, mais leur sous-catégorie à l'étape 7.

À la fin de l'étape, `uv run budget classify --mois 2026-06` affiche la répartition par nature, les 26 opérations non classées de juin, le rapprochement des charges fixes et les alertes.

## Références cadrage

- §4 Modèle de classement (natures de flux, effets sur les indicateurs)
- §5 Structure budgétaire du foyer (dotations, provision, charges fixes)
- §7 Mémoire et interaction humaine (ordre de classement : le contexte d'abord)
- §10 Fichier de contexte
- RG-03 (neutralisation), RG-04 (mois de paie), RG-05 (dotations), RG-08 (provision), RG-09 (avoirs marchands : nature uniquement), RG-11 (types TR), RG-12 (sens TR), RG-17 (cartes non déclarées), RG-18 (charges fixes)

## Préparation (projet Claude.ai, avant Claude Code)

Copier `contexte.example.yaml` en `data/contexte.yaml`, y reporter les vraies valeurs, puis trancher les points suivants. Chacun est constaté sur les extractions de juin et juillet.

1. **Dotations (RG-05)** : vérifier les libellés exacts et les montants des trois dotations (`Mensuel` 500 €, `Virement du mois` 500 €, `Charges trim` 300 €). En juin, `Virement du mois` porte 550 € : il ne sera pas reconnu et deviendra une question à l'étape 6.
2. **Dotations de juillet** : aucune des trois dotations n'apparaît sous son libellé habituel. On trouve à la place, vers le compte perso, 300 € « Charges persos » et 1 000 € sans motif, et vers le compte de provision, 250 € « Virement pour charges » et 1 500 € « Impots ». Décider s'il s'agit d'un changement durable (mettre le contexte à jour, éventuellement avec plusieurs libellés) ou d'un mois exceptionnel (laisser les questions de l'étape 6 trancher).
3. **Provision (RG-08)** : seuls les paiements de copropriété (`FR48ZZZ829660`) sont financés par la provision. Point ouvert §13 tranché : les impôts n'en font pas partie ; ils seront une dépense annuelle financée par un virement exceptionnel sur le joint.
4. **Charges fixes (RG-18)** : en juillet, les deux prêts passent de 1 787,46 € et 121,89 € à 1 471,00 € et 105,84 €. Mettre à jour les montants attendus si le changement est durable, sinon les alertes RG-18 le signaleront chaque mois.
5. **Nouvelle charge en juillet** : prélèvement Direct Assurance (ICS `FR64ZZZ395200`, 33,13 €). La taxonomie n'a pas de sous-catégorie « assurance véhicule ». Décider entre l'ajout d'une sous-catégorie (modification de `categories.py`, à faire dans cette étape) ou le rattachement à une sous-catégorie existante, puis l'ajouter aux charges fixes.
6. **Charges du compte perso** : le prélèvement Navigo (90,80 €) et l'abonnement Orange (11,99 €) passent par Trade Republic. Le cadrage ne définit des charges fixes que pour le joint : ils restent non classés ici et seront traités par question et règle à l'étape 6.
7. Valider avec `uv run budget config verifier`, puis exporter l'état du dépôt (`npx repomix`) si besoin.

Toute modification du schéma de `contexte.yaml` décidée ici (nouveau champ) est reportée dans `config.py`, `contexte.example.yaml` et `test_config.py`, et notée dans « Écarts constatés ».

## Périmètre

**Inclus**

- `classement/` : moteur de classement par le contexte et règles fixes, appariement des virements internes.
- Modèles `Classement`, `Alerte`, `RapprochementCharge`.
- Migration `0002` : tables de projection `classements`, `alertes`, `charges_fixes_mois`.
- Commande `budget classify --mois`.

**Exclus**

- Questions, règles apprises, décisions, liens à une dépense (étape 6).
- Sous-catégorie des paiements carte, RG-13 (Amazon), RG-15 (exceptionnels), journal des décisions (étape 7).
- Agrégats et indicateurs, contrôle « chaque opération a une nature » avant publication (étape 8).

## Principe : le classement est une projection

`budget classify --mois M` recalcule entièrement le classement des transactions du mois M à partir du ledger, du contexte et, à partir des étapes 6 et 7, des règles apprises et des décisions. Dans une seule transaction SQL, les lignes du mois dans `classements`, `alertes` et `charges_fixes_mois` sont supprimées puis réécrites. Ce qui doit persister (réponses de l'utilisateur, règles) vit dans d'autres tables, introduites à l'étape 6.

Le classement d'un mois ne dépend que des transactions de ce mois.

## Conception

### Modèles

```python
class Classement(BaseModel):               # frozen, extra="forbid"
    transaction_id: str
    nature: Nature | None                  # None = non classée
    sous_categorie: SousCategorie | None
    mois_rattachement: str                 # mois_budgetaire, sauf salaires : mois de paie (RG-04)
    origine: OrigineClassement             # contexte | regle_fixe | regle | decision | aucune
    detail: str | None                     # "RG-03", "dotations.perso", "charges_fixes.energie"…
    contrepartie_id: str | None            # virement interne apparié
    lien_id: str | None = None             # RG-07, rempli à l'étape 6
    exceptionnel: bool = False             # RG-15, rempli à l'étape 7

class Alerte(BaseModel):                   # frozen, extra="forbid"
    code: str                              # "RG-05", "RG-08", "RG-17", "RG-18", "TR-TYPE"
    transaction_id: str | None
    message: str                           # sans libellé : position, date, montant

class RapprochementCharge(BaseModel):      # frozen, extra="forbid"
    nom: str
    attendu: Decimal                       # positif
    reel: Decimal | None                   # positif ; None si absente
    transaction_ids: tuple[str, ...]
    ecart: Decimal | None                  # reel - attendu
    ecart_pct: Decimal | None              # arrondi au centième
    seuil_pct: Decimal                     # tolerance_pct de la charge, sinon seuils.ecart_charge_fixe_pct
    hors_seuil: bool
```

`OrigineClassement` est un `StrEnum`. `regle_fixe` désigne les règles codées en dur et indépendantes du contexte (RG-03, RG-11, paiements carte).

`ResultatClassement` regroupe, pour un mois, la liste des `Classement` (une par transaction, y compris les non classées), des `Alerte` et des `RapprochementCharge`.

### Correspondance d'un critère du contexte

Fonction commune `correspond(transaction, critere) -> bool` pour les éléments `_AvecCritere` du contexte (charges fixes, paiements financés) :

- `ics` : égalité stricte avec `transaction.ics`.
- `libelle_contient` : `normaliser_libelle(valeur)` contenu dans `libelle_normalise`.
- `type_operation` : égalité après normalisation des deux côtés.
- Tous les critères renseignés doivent correspondre (ET logique).

Dotations, salaires et reprises de provision n'ont qu'un critère de libellé, comparé de la même façon.

### Ordre d'application

Chaque transaction reçoit le **premier** classement applicable dans l'ordre ci-dessous. Une transaction déjà classée n'est plus examinée par les étapes suivantes.

| Ordre | Règle | Périmètre | Résultat |
| --- | --- | --- | --- |
| 1 | RG-03 : débit différé | `ca_joint`, type `Prélèvement carte`, libellé conforme au motif `DEPENSES CARTE Xdddd AU JJ/MM/AA` (constante exposée par le parser CA) | `NEUTRALISE`, détail `RG-03` |
| 2 | RG-04 : salaire | `rattacher_salaire` (spec 04) non nul | `REVENU`, `mois_rattachement` = mois de paie, détail `salaires.<nom>` |
| 3 | RG-05 : dotation | `ca_joint`, montant négatif, libellé contenant `libelle_contient` **et** `abs(montant) == montant` | `DOTATION`, détail `dotations.<nom>` |
| 4 | RG-08 : reprise de provision | `ca_joint`, montant positif, libellé contenant `reprises_libelle_contient` | `PROVISION`, détail `provision.reprise` |
| 5 | RG-08 : paiement financé | `ca_joint`, montant négatif, `correspond` à un `paiements_finances` | `PROVISION`, détail `provision.<nom>` |
| 6 | RG-18 : charge fixe | `ca_joint`, montant négatif, voir l'affectation ci-dessous | `DEPENSE`, sous-catégorie de la charge, détail `charges_fixes.<nom>` |
| 7 | RG-11 : types TR | `tr`, voir le tableau RG-11 | `EPARGNE` ou `REVENU`, détail `RG-11` |
| 8 | Paiement carte | `ca_cb` (toutes), `tr` de type `Avoir` | `DEPENSE` si montant négatif, `REMBOURSEMENT` s'il est positif (RG-09 ; sous-catégorie à l'étape 7) |
| 9 | Contrepartie de dotation | Virement interne apparié dont l'autre côté est une `DOTATION` | `DOTATION`, même détail suffixé `:contrepartie` |
| — | Aucune | Tout le reste | `nature = None`, origine `aucune` |

Un salaire, une dotation ou une charge fixe dont le libellé correspond à plusieurs éléments du contexte de même type lève `ControleError` : le contexte est ambigu, il faut le corriger.

Pour une opération de type `Avoir` sur le joint (`CARTE X9817 CREATE`), la règle 8 ne s'applique pas : seules les opérations des fichiers cartes et les `Avoir` TR sont des paiements carte. Elle reste non classée.

### RG-11 : types Trade Republic

Comparaison sur le type et la description normalisés.

| Type TR | Condition | Nature |
| --- | --- | --- |
| `Exécution d'ordre` | — | `EPARGNE` (montant négatif : investissement, imputé sur l'enveloppe perso à l'étape 8) |
| `Virement` | Description `VERSEMENT PEA` | `EPARGNE` |
| `Intérêts` | — | `REVENU` (revenu financier) |
| `Rendement` | — | `REVENU` (dividende) |
| `Bonus` | — | `REVENU` (bonus Saveback) |
| `Avoir` | — | Paiement carte (règle 8) |
| `Virement` (autre), `Prelevement bancaire` | — | Non classé (étape 6) |
| Tout autre type | — | Non classé, et alerte `TR-TYPE` (« type TR inconnu, page/position, date, montant ») |

Le détail précise la sous-nature : `RG-11:ordre`, `RG-11:pea`, `RG-11:interets`, `RG-11:dividende`, `RG-11:bonus`. L'étape 8 s'en sert pour distinguer revenus financiers et salaires.

### RG-18 : affectation des charges fixes

1. Pour chaque charge fixe, les candidates sont les transactions `ca_joint` négatives, non encore classées, pour lesquelles `correspond` est vrai.
2. **Charges à critères identiques** (les deux prêts : seul `type_operation` est renseigné) : les charges du groupe et leurs candidates sont triées par montant absolu croissant, puis appariées dans cet ordre. Si le nombre de candidates diffère du nombre de charges, l'appariement se fait au plus proche montant attendu, et une alerte RG-18 signale l'écart de nombre. Les candidates en trop restent non classées.
3. **Charge à critère unique** : toutes ses candidates lui sont affectées ; le réel est leur somme.
4. Pour chaque charge du contexte, un `RapprochementCharge` est produit : `ecart = reel - attendu`, `ecart_pct = ecart / attendu × 100` arrondi au centième, `hors_seuil = abs(ecart_pct) > seuil_pct` (inégalité stricte).
5. Une charge hors seuil produit une alerte RG-18 (« charge pret_principal : 1 471,00 € au lieu de 1 787,46 € (−17,70 %, seuil 10 %) »). Une charge sans opération dans le mois produit une alerte RG-18 « charge attendue absente ».

### RG-17 : cartes non déclarées

Pour toute transaction dont `carte` est renseignée (sur le joint comme dans le fichier cartes) :

- carte de `cartes_inactives` : alerte « carte inactive » ;
- carte absente de `cartes_actives` et de `cartes_inactives` : alerte « carte inconnue ».

L'alerte n'empêche pas le classement : l'opération suit l'ordre d'application normal.

### Autres alertes

- **RG-05, dotation non trouvée** : une dotation du contexte sans opération correspondante dans le mois. Si une opération a le bon libellé mais un autre montant, le message le précise (« dotation perso_conjoint non trouvée : libellé présent le 30/06/2026 avec 550,00 € au lieu de 500,00 € »).
- **RG-08, provision non appariée** : une reprise de provision sans paiement financé dans le mois, ou l'inverse.

Les alertes sont informatives : elles ne bloquent ni le classement ni la suite. Les contrôles bloquants restent ceux de l'import (spec 04).

### Virements internes (`classement/virements_internes.py`)

L'argent qui va du joint vers le compte perso apparaît deux fois : en sortie sur le joint, en entrée sur Trade Republic. Les deux côtés sont appariés pour être classés une seule fois.

- Côté joint : type `Virement émis` (montant négatif) ou `Virement en votre faveur` (positif).
- Côté TR : type `Virement`, hors `Versement PEA`, de signe opposé.
- Même valeur absolue, et date TR comprise entre la date joint et la date joint + 3 jours (`DELAI_VIREMENT_INTERNE_JOURS = 3`).
- L'appariement n'a lieu que si chaque côté a **exactement une** candidate de l'autre côté. Sinon, aucun appariement : les deux côtés seront traités séparément à l'étape 6.
- Seules les transactions du mois classé sont examinées.

Les deux côtés reçoivent `contrepartie_id` l'un vers l'autre. Si le côté joint est une `DOTATION`, le côté TR le devient aussi (règle 9). Sinon, les deux côtés restent non classés et feront l'objet d'une seule question à l'étape 6.

### Persistance (migration 0002)

```sql
CREATE TABLE classements (
    transaction_id     TEXT PRIMARY KEY REFERENCES transactions (id),
    mois_budgetaire    TEXT NOT NULL,
    nature             TEXT,                 -- NULL = non classée
    sous_categorie     TEXT,
    mois_rattachement  TEXT NOT NULL,
    origine            TEXT NOT NULL,
    detail             TEXT,
    contrepartie_id    TEXT REFERENCES transactions (id),
    lien_id            TEXT REFERENCES transactions (id),
    exceptionnel       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE alertes (
    id              INTEGER PRIMARY KEY,
    mois            TEXT NOT NULL,
    code            TEXT NOT NULL,
    transaction_id  TEXT REFERENCES transactions (id),
    message         TEXT NOT NULL
);

CREATE TABLE charges_fixes_mois (
    mois             TEXT NOT NULL,
    nom              TEXT NOT NULL,
    attendu_centimes INTEGER NOT NULL,
    reel_centimes    INTEGER,
    seuil_pct        TEXT NOT NULL,          -- Decimal en texte, jamais REAL
    hors_seuil       INTEGER NOT NULL,
    PRIMARY KEY (mois, nom)
);

CREATE TABLE charges_fixes_transactions (
    mois            TEXT NOT NULL,
    nom             TEXT NOT NULL,
    transaction_id  TEXT NOT NULL REFERENCES transactions (id),
    PRIMARY KEY (mois, nom, transaction_id),
    FOREIGN KEY (mois, nom) REFERENCES charges_fixes_mois (mois, nom)
);
```

`lien_id` et `exceptionnel` sont créés dès maintenant pour ne pas multiplier les migrations ; ils restent vides jusqu'aux étapes 6 et 7.

API ajoutée au `Ledger` : `enregistrer_classement(mois, resultat: ResultatClassement)` (remplacement atomique du mois) et `classements(mois) -> list[Classement]`.

### Organisation du code

```
src/budget/classement/
├── __init__.py
├── modeles.py              # Classement, Alerte, RapprochementCharge, ResultatClassement
├── criteres.py             # correspond()
├── contexte.py             # règles 2 à 6, RG-05, RG-08, RG-18, RG-17
├── trade_republic.py       # RG-11
├── virements_internes.py   # appariement
└── moteur.py               # classer_mois(transactions, contexte) -> ResultatClassement
```

`classer_mois` est une fonction pure (aucune lecture de base ni de fichier) : c'est elle qui porte les tests.

### CLI

`budget classify --mois AAAA-MM [--base data/budget.sqlite] [--contexte data/contexte.yaml]` :

1. Charge le contexte et les transactions du mois (mois non importé : erreur, code 1).
2. Calcule et enregistre le classement.
3. Affiche avec Rich :
    - par compte, le nombre d'opérations et le total par nature, plus la ligne « non classées » ;
    - le rapprochement des charges fixes (attendu, réel, écart, écart %, statut) ;
    - les alertes, groupées par code ;
    - le nombre de virements internes appariés.

Code 0 même s'il reste des opérations non classées : elles sont attendues à cette étape.

## Valeurs attendues sur les fixtures de juin 2026

Avec `contexte.example.yaml`.

**Compte joint (37 opérations)**

| Nature | Nombre | Total | Opérations |
| --- | --- | --- | --- |
| `NEUTRALISE` | 2 | −3 527,17 € | Débits différés X1091 (3 116,50 €) et X1481 (410,67 €) |
| `REVENU` | 3 | +7 558,78 € | Deux `PLUME`, un `GROUPAMA` ; 5 255,47 € rattachés à `2026-06` et 2 303,31 € à `2026-05` |
| `DOTATION` | 2 | −800,00 € | `Mensuel` 500 € (`dotations.perso`), `Charges trim` 300 € (`dotations.provision`) |
| `PROVISION` | 2 | −0,04 € | Reprise +1 071,00 € le 19/06, SOPAGI −1 071,04 € le 24/06 (`provision.copropriete`) |
| `DEPENSE` | 8 | −2 797,25 € | Les 8 charges fixes du contexte, toutes au montant attendu |
| Non classées | 20 | — | 13 virements, 3 PayPal, 1 remise de chèque, 1 cotisation carte, 2 opérations X9817 |

**Fichier cartes (48 opérations)** : 47 `DEPENSE` (−3 727,16 €), 1 `REMBOURSEMENT` (MANOMANO, +199,99 €). Le net (−3 527,17 €) est égal aux deux lignes neutralisées du joint.

**Trade Republic (74 opérations)**

| Nature | Nombre | Total | Opérations |
| --- | --- | --- | --- |
| `DEPENSE` | 56 | −1 271,07 € | Paiements carte (`Avoir` négatifs), dont DECATHLON 409,97 € |
| `REMBOURSEMENT` | 1 | +51,00 € | Zalando, `Avoir` en entrée (RG-12) |
| `EPARGNE` | 7 | −61,50 € | 6 exécutions d'ordre (46,30 €), 1 versement PEA (15,20 €) |
| `REVENU` | 3 | +11,91 € | Bonus 8,54 €, intérêts 2,21 €, dividende 1,16 € |
| `DOTATION` | 1 | +500,00 € | Contrepartie de `Mensuel`, le 29/06 |
| Non classées | 6 | — | 4 virements entrants (dont 250 € apparié à « Velo »), 2 prélèvements (Navigo, Orange) |

**Synthèse**

| Élément | Valeur |
| --- | --- |
| Non classées | 26 (20 joint, 6 TR) |
| Virements internes appariés | 2 : `Mensuel` 500 € (29/06 ↔ 29/06), `Velo` 250 € (15/06 ↔ 15/06) |
| Charges fixes | 8 sur 8 trouvées, réel = attendu = 2 797,25 €, aucune hors seuil |
| Alertes RG-17 | 2 : carte X9817 inactive (03/06, +106,80 € ; 16/06, +81,00 €) |
| Alertes RG-05 | 1 : `perso_conjoint`, libellé présent avec 550,00 € au lieu de 500,00 € |
| Alertes RG-08, RG-18, TR-TYPE | 0 |

## Valeurs attendues sur les fixtures de juillet 2026

| Élément | Valeur |
| --- | --- |
| Salaires | 1 : `GROUPAMA`, 2 726,58 €, rattaché à `2026-07` |
| Charges fixes | 8 sur 8 trouvées |
| RG-18 hors seuil | 2 : `pret_principal` 1 471,00 € contre 1 787,46 € (−316,46 €, −17,70 %) ; `pret_secondaire` 105,84 € contre 121,89 € (−16,05 €, −13,17 %) |
| RG-18 dans le seuil | `garde_enfant` 735,65 € contre 668,24 € (+67,41 €, +10,09 %, tolérance 25 %) |
| Alertes RG-05 | 3 : les trois dotations sont absentes |
| Provision | Aucune reprise ni paiement financé |
| Virements internes appariés | 2 : 1 000 € le 24/07, 300 € « Charges persos » le 28/07 (non classés : aucun n'est une dotation déclarée) |
| Direct Assurance (33,13 €) | Non classée avec `contexte.example.yaml` |

Les autres comptes de juillet sont relevés lors de la première exécution réussie, puis figés dans les tests.

## Dépendances autorisées

Aucune nouvelle dépendance.

## Critères d'acceptation

- [ ] `contexte.yaml` réel validé par `budget config verifier` ; les décisions de la section Préparation sont notées dans « Écarts constatés ».
- [ ] `classer_mois` renvoie les valeurs des tableaux de juin sur les fixtures (nombres et totaux par nature et par compte, détails).
- [ ] Les valeurs de juillet ci-dessus sont vérifiées par les tests, et les autres sont figées après la première exécution.
- [ ] Chaque transaction du mois a exactement une ligne dans `classements`, y compris les non classées.
- [ ] RG-03 : les deux lignes de débit différé sont `NEUTRALISE`, et le net des cartes égale leur somme.
- [ ] RG-05 : une dotation au bon libellé mais au mauvais montant n'est pas classée et produit l'alerte décrite.
- [ ] RG-08 : reprise et paiement financé sont `PROVISION` ; une reprise seule produit une alerte (cas synthétique).
- [ ] RG-11 : chaque type TR du tableau est couvert, et un type inconnu produit l'alerte `TR-TYPE`.
- [ ] RG-12 : l'`Avoir` Zalando en entrée est un `REMBOURSEMENT`, l'`Avoir` DECATHLON en sortie une `DEPENSE`.
- [ ] RG-17 : alerte pour une carte inactive et pour une carte inconnue (cas synthétique), sans effet sur le classement.
- [ ] RG-18 : appariement des deux prêts par montant (juin et juillet), écarts et seuils (y compris la tolérance propre de `garde_enfant`), charge absente (cas synthétique), nombre de candidates différent du nombre de charges (cas synthétique).
- [ ] Virements internes : appariement unique, absence d'appariement en cas d'ambiguïté (deux virements de même montant dans la fenêtre), contrepartie de dotation classée `DOTATION`.
- [ ] Un contexte ambigu (libellé correspondant à deux dotations) lève `ControleError`.
- [ ] `budget classify` est rejouable : deux exécutions successives produisent des tables identiques.
- [ ] Aucun message d'alerte ou d'erreur ne contient de libellé complet.
- [ ] Les tests `donnees_reelles` classent `data/raw/2026-06/` et `2026-07/` avec `data/contexte.yaml` sans erreur quand ils sont présents.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_classement_criteres.py` | `correspond` : ICS, libellé normalisé, type, ET logique |
| `test_classement_contexte.py` | RG-03, RG-04, RG-05, RG-08, RG-17, ordre d'application, contexte ambigu |
| `test_charges_fixes.py` | RG-18 : affectation, appariement par montant, écarts, seuils, charge absente |
| `test_classement_tr.py` | RG-11, RG-12, type inconnu |
| `test_virements_internes.py` | Appariement, ambiguïté, fenêtre de 3 jours, contrepartie de dotation |
| `test_classement_golden.py` | Valeurs de juin et juillet sur les fixtures, rejouabilité |
| `test_ledger_migrations.py` (ajouts) | Migration 0002 sur une base en version 1 contenant déjà des transactions |
| `test_cli.py` (ajouts) | `budget classify` en succès et sur un mois non importé |

Les cas synthétiques sont construits avec une fabrique de `Transaction` dans `conftest.py`, sans fichier de fixture supplémentaire.

## Points ouverts

- **Virement interne à cheval sur deux mois** (envoyé le 30, reçu le 1er) : il ne sera pas apparié, puisque le classement d'un mois ne lit que ce mois. Les deux côtés seront traités séparément à l'étape 6. À revoir si le cas se produit.
- **Charges fixes du compte perso** (Navigo, Orange) : non prévues par le cadrage. Si l'utilisateur souhaite les suivre comme charges fixes de la vue perso, ajouter un champ `compte` aux charges fixes du contexte (évolution de schéma de `contexte.yaml`).
- **Dépenses annuelles connues** (`depenses_annuelles_connues`) : elles n'ont pas de critère de correspondance et ne participent pas au classement. Leur usage (budget lissé, explication des pics) relève de l'étape 8.
- **Écart de centimes de la provision** (1 071,00 € reçus, 1 071,04 € payés) : les deux opérations sont neutralisées ensemble, les 0,04 € disparaissent des indicateurs. C'est le comportement voulu par RG-08.

## Écarts constatés

_À compléter pendant l'implémentation._
