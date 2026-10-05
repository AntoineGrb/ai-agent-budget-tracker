# Spec 02 — Parsers Crédit Agricole et contrôle du débit différé

| Champ | Valeur |
| --- | --- |
| Étape du plan | 2 / 10 |
| Type | Code (Claude Code) |
| Complexité | Moyenne |
| Estimation | 1 à 2 sessions |
| Prérequis | Spec 01 terminée (fixtures juin et juillet disponibles) |
| Skill à charger | `parser-releve-bancaire` |

## Objectif

Lire les deux exports CSV du Crédit Agricole (cartes à débit différé, compte joint) et les convertir en `OperationBrute`, puis vérifier au centime que le détail des cartes correspond aux lignes de débit différé du joint (RG-03).

Aucun classement à cette étape : les lignes de débit différé sont conservées telles quelles, leur neutralisation se fait à l'étape 5.

## Références cadrage

- §3 Sources de données (formats, pièges du Crédit Agricole)
- §11 Contrôles bloquants
- RG-01 (date de débit des cartes), RG-03 (débit différé), RG-17 (cartes inconnues : on extrait l'information ici, l'alerte arrive à l'étape 5)

## Périmètre

**Inclus** : `parsers/credit_agricole.py`, `controles.py` (fonction RG-03), modèles `ReleveCarte`, `ReleveCartes`, `LigneDebitDiffere`, `ReleveJoint`, tests sur fixtures et sur données réelles.

**Exclus** : normalisation des libellés et clé de dédoublonnage (étape 4), neutralisation et classement (étape 5), alerte RG-17 (étape 5).

## Format du fichier cartes (`CA_CB_AAAAMM.csv`)

Constaté sur juin 2026 : encodage cp1252 (`€` = octet `0x80`), fins de ligne LF, séparateur `;` avec un `;` final sur les lignes de données. Un bloc d'en-tête, puis une section par carte.

```
(ligne vide)
Téléchargement du 23/09/2026;
(lignes vides)
TITULAIRE 1 OU            TITULAIRE 2
Compte de Dépôt n° 00000000000;
Solde au 23/09/2026 3 178,30 €
(ligne vide)
TITULAIRE 1 - Titulaire;
 carte n° 5137 81XX XXXX 1091
(lignes vides)
Encours débité le 30 juin 2026;-3 116.50 €
(ligne vide)
Date;Libellé;Débit euros;Crédit euros;
17/06/2026;MILIBOO CHAVANOD;258,99;;
...
04/06/2026;MANOMANO PARIS;;199,99;
...
(lignes vides)
TITULAIRE 2 - Titulaire;
 carte n° 5137 81XX XXXX 1481
...
```

Points d'attention :

- Le montant de l'en-tête « Encours débité » utilise un **point** décimal et un signe négatif (`-3 116.50 €`), alors que les lignes utilisent une **virgule** (`258,99`).
- La date de débit est en toutes lettres (`30 juin 2026`) : c'est la `date_debit` de toutes les opérations de la section (RG-01).
- Une ligne de données a exactement un montant, soit en débit, soit en crédit (avoir marchand, ex. MANOMANO).

### Algorithme

Machine à états ligne par ligne :

1. `carte n° … dddd` (motif `carte n°\s*[\dX ]*?(\d{4})\s*$`) ouvre une section : `carte = "X" + dddd`.
2. `Encours débité le <date>;<montant>` fixe `date_debit` et `encours = abs(montant)`.
3. `Date;Libellé;Débit euros;Crédit euros;` ouvre la zone de données.
4. Chaque ligne `JJ/MM/AAAA;…` produit une `OperationBrute` (découpage avec le module `csv`, délimiteur `;`).
5. Une ligne vide ou une nouvelle section ferme la zone de données.

Toute ligne de données hors zone, une section sans ligne « Encours débité » ou sans en-tête de colonnes lève `ParseError` avec le numéro de ligne.

### Mapping vers `OperationBrute`

| Champ | Valeur |
| --- | --- |
| `source` / `compte` | `CA_CB` / `JOINT` |
| `date_operation` | Colonne Date |
| `date_debit` | Date de la ligne « Encours débité » de la section |
| `type_operation` | `None` |
| `libelle` / `libelle_brut` | Colonne Libellé, espaces de bord retirés (les espaces internes multiples sont conservés : `UBER   *EATS`) |
| `carte` | `X` + 4 derniers chiffres de la section |
| `montant` | `-débit` ou `+crédit` |
| `ics`, `solde_apres` | `None` |

### Modèles de sortie

```python
class ReleveCarte(BaseModel):          # frozen, extra="forbid"
    carte: str                         # "X1091"
    date_debit: date
    encours: Decimal                   # positif : montant prélevé sur le joint
    operations: tuple[OperationBrute, ...]

class ReleveCartes(BaseModel):
    fichier: str
    cartes: tuple[ReleveCarte, ...]
```

Fonction publique : `parser_ca_cartes(chemin: Path) -> ReleveCartes`.

### Contrôle interne au fichier (bloquant)

Pour chaque carte : `somme(débits) - somme(crédits) == encours`. En cas d'écart, `ControleError` citant la carte, les deux montants et l'écart.

## Format du fichier compte joint (`CA_CPTE_JOINT_AAAAMM.csv`)

Constaté sur juin 2026 : cp1252, **fins de ligne CRLF et LF mélangées**, 4 colonnes `;` (sans `;` final sur les lignes de données), libellés **multi-lignes entre guillemets**.

```
Téléchargement du 23/09/2026;;;
...
;Encours sur 2 carte(s) débité(s) en octobre;297,30 €;
...
Liste des opérations du compte entre le 01/06/2026 et le 30/06/2026;;;
;;;
Date;Libellé;Débit euros;Crédit euros
30/06/2026;"Prélèvement carte
DEPENSES CARTE X1091 AU 18/06/26



";3 116,50;
29/06/2026;"Prélèvement
<CRÉANCIER> - … COTISATION DU CONTRAT …

<RUM>
FR41ZZZ272230
<RÉFÉRENCE>";43,77;
29/06/2026;"Virement en votre faveur
<ÉMETTEUR> VIRT.APTS



VIRT.APTS";;2 729,05
...
(sections de fin par carte : « Pas d'opérations carte prélevées sur la période demandée »)
```

### Algorithme

- Ouvrir avec `open(chemin, encoding="cp1252", newline="")` et lire avec `csv.reader(delimiter=";")` : c'est ce qui gère correctement les libellés multi-lignes et le mélange CRLF/LF. Ne pas découper le fichier par lignes à la main.
- La ligne `Liste des opérations du compte entre le JJ/MM/AAAA et le JJ/MM/AAAA` donne la période. Elle doit couvrir exactement un mois civil (du 1er au dernier jour), sinon `ParseError` : RG-02 raisonne par mois complet.
- Les lignes de données sont celles dont la 1re cellule est une date `JJ/MM/AAAA` après l'en-tête `Date;Libellé;Débit euros;Crédit euros`. Tout le reste est ignoré (encours à venir, sections de fin).
- Exactement un montant par ligne (débit ou crédit), sinon `ParseError`.

### Découpage du libellé

`lignes = [l.strip() for l in cellule.split("\n")]`, puis :

| Élément | Règle | Exemple |
| --- | --- | --- |
| `type_operation` | `lignes[0]` | `Prélèvement`, `Virement émis`, `Virement en votre faveur`, `Prélèvement carte`, `Remboursement de prêt`, `Cotisation`, `Avoir`, `Règlement`, `Régul opé débitrices`, `Remise de chèque` |
| `libelle` | `lignes[1]` si non vide, sinon `lignes[0]` | `Free Telecom - Free - Free HautDebit …` |
| `libelle_brut` | cellule complète | — |
| `ics` | parmi `lignes[2:]`, l'unique ligne correspondant à `^[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}$` | `FR41ZZZ272230`, `DE56AGR00002197951`, `LU96ZZZ0000000000000000058` |
| `carte` | premier motif trouvé dans `libelle`, insensible à la casse, dans cet ordre : `CARTE X(\d{4})`, `Carte N° X ?(\d{4})`, `carte \d{6}X+(\d{4})` | `DEPENSES CARTE X1091 AU 18/06/26` → `X1091` ; `CARTE X9817 CREATE 15/06` → `X9817` |

Deux motifs ICS dans la même cellule lèvent `ParseError`. Aucun motif donne `ics = None`. Sur juin, les 8 prélèvements ont chacun exactement un ICS, et aucune autre opération n'en a.

### Lignes de débit différé

Une opération de type `Prélèvement carte` dont le libellé correspond à `DEPENSES CARTE X(\d{4}) AU (\d{2}/\d{2}/\d{2})` produit en plus une `LigneDebitDiffere`.

```python
class LigneDebitDiffere(BaseModel):    # frozen, extra="forbid"
    carte: str                         # "X1091"
    date_debit: date                   # date de l'opération sur le joint (30/06/2026)
    date_arrete: date                  # "AU 18/06/26"
    montant: Decimal                   # positif (3116.50)

class ReleveJoint(BaseModel):
    fichier: str
    periode_debut: date
    periode_fin: date
    operations: tuple[OperationBrute, ...]
    debits_differes: tuple[LigneDebitDiffere, ...]
```

L'`OperationBrute` correspondante reste dans `operations` (avec `carte` renseignée) : sa neutralisation relève de l'étape 5.

Fonction publique : `parser_ca_joint(chemin: Path) -> ReleveJoint`.

## Contrôle RG-03 : débit différé (bloquant)

Dans `controles.py` : `verifier_debit_differe(cartes: ReleveCartes, joint: ReleveJoint) -> None`, qui lève `ControleError` si l'une des conditions suivantes n'est pas remplie.

1. **Même mois** : chaque `ReleveCarte.date_debit` tombe dans la période du joint. Sinon, les fichiers ne correspondent pas au même mois.
2. **Appariement complet** : chaque `ReleveCarte` a exactement une `LigneDebitDiffere` de même carte et de même date de débit, et inversement.
3. **Montant exact** : `LigneDebitDiffere.montant == ReleveCarte.encours`, au centime.
4. **Cycle cohérent** : toutes les `date_operation` de la carte sont antérieures ou égales à `date_arrete`.

Exemple de message : `RG-03 : carte X1091, débit du 30/06/2026 : joint = 3 116,50 €, détail cartes = 3 110,00 € (écart 6,50 €).`

## Valeurs attendues sur les fixtures de juin 2026

Les montants sont inchangés par l'anonymisation ; ces valeurs servent de tests golden.

| Élément | Valeur |
| --- | --- |
| Cartes | 2 sections : `X1091` et `X1481`, `date_debit` 30/06/2026 |
| `X1091` | 39 opérations, débits 3 316,49 €, crédits 199,99 €, encours 3 116,50 € |
| `X1481` | 9 opérations, débits 410,67 €, crédits 0 €, encours 410,67 € |
| 1re opération `X1091` | 17/06/2026, `MILIBOO CHAVANOD`, −258,99 € |
| Avoir `X1091` | 04/06/2026, `MANOMANO PARIS`, +199,99 € |
| Joint : période | 01/06/2026 → 30/06/2026 |
| Joint : opérations | **37** (le cadrage §3 indique 38 : valeur erronée, 37 est vérifié) |
| Joint : totaux | débits 10 127,46 €, crédits 12 102,56 € |
| Joint : types | Virement en votre faveur 11, Virement émis 8, Prélèvement 8, Prélèvement carte 2, Cotisation 2, Remboursement de prêt 2, Avoir 1, Règlement 1, Régul opé débitrices 1, Remise de chèque 1 |
| Joint : ICS | 8 opérations avec ICS : `FR41ZZZ272230`, `FR48ZZZ829660`, `FR83ZZZ459654`, `DE56AGR00002197951`, `FR67ZZZ308137`, et 3 fois `LU96ZZZ0000000000000000058` |
| Joint : débits différés | `X1091` 3 116,50 € et `X1481` 410,67 €, arrêtés au 18/06/2026 |
| Joint : carte extraite hors débit différé | `X9817` (avoir de 81 € et régularisation de cotisation), `X1481` (cotisation carte) |
| RG-03 | Passe sans erreur |

Pour juillet, les valeurs sont relevées lors de la première exécution réussie, puis figées dans les tests.

## Dépendances autorisées

Aucune nouvelle dépendance : bibliothèque standard (`csv`, `re`, `pathlib`) et socle de l'étape 1.

## Critères d'acceptation

- [ ] `parser_ca_cartes` et `parser_ca_joint` renvoient les valeurs du tableau ci-dessus sur les fixtures de juin.
- [ ] Les fixtures de juillet sont parsées sans erreur et RG-03 passe ; leurs valeurs clés sont figées dans les tests.
- [ ] Chaque libellé multi-ligne du joint est restitué entier dans `libelle_brut` (test sur un prélèvement à 6 lignes).
- [ ] Le contrôle interne du fichier cartes détecte une ligne supprimée (test sur une copie modifiée en mémoire).
- [ ] RG-03 détecte chacun des quatre cas d'échec : mois différent, carte manquante d'un côté, écart de montant d'un centime, opération postérieure à la date d'arrêté.
- [ ] Un fichier joint dont la période n'est pas un mois civil complet lève `ParseError`.
- [ ] Une ligne à deux montants, ou sans montant, lève `ParseError` avec son numéro de ligne.
- [ ] Aucun message d'erreur ne contient de libellé complet (position, date et montant uniquement).
- [ ] Les tests `donnees_reelles` passent sur `data/raw/2026-06/` et `data/raw/2026-07/` quand ils sont présents.
- [ ] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_parser_ca_cartes.py` | Valeurs golden, contrôle interne, en-tête « Encours » au point décimal, section sans en-tête de colonnes |
| `test_parser_ca_joint.py` | Valeurs golden, découpage des libellés, ICS, extraction des cartes, période non mensuelle, montants invalides |
| `test_controle_debit_differe.py` | Cas nominal et les quatre cas d'échec (RG-03) |

Les cas d'échec sont construits en mémoire à partir des fixtures (texte modifié puis écrit dans `tmp_path`), sans ajouter de nouveaux fichiers de fixtures.

## Points ouverts

- Le format d'un export cartes dont l'encours n'est pas encore débité (cycle en cours) n'a pas été observé. Si l'en-tête diffère de « Encours débité le », lever une `ParseError` explicite et remonter l'exemple (anonymisé) avant d'adapter le parser.
- Les exports historiques (6 à 12 mois à l'étape 10) peuvent présenter des variantes. Toute variante rencontrée est ajoutée comme cas de test.

## Écarts constatés

_À compléter pendant l'implémentation._
