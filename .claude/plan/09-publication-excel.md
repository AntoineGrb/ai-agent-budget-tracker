# Spec 09 — Publication Excel

| Champ | Valeur |
| --- | --- |
| Étape du plan | 9 / 10 |
| Type | Claude Code |
| Complexité | Moyenne |
| Estimation | 1 session |
| Prérequis | Spec 08 terminée |

> Spec réécrite le 16 octobre 2026. Une première version publiait dans Google Sheets (`gspread`,
> compte de service, plan de requêtes `batchUpdate`) ; elle a été codée, puis abandonnée le même
> jour avant toute mise en service : la préparation Google Cloud, le réseau et l'inspection TLS du
> poste pesaient trop pour un MVP. Le classeur Excel local la remplace. Google Sheets reste
> envisageable en phase 2, si le foyer veut consulter le suivi en ligne.

## Objectif

Écrire, pour chaque mois publié, un classeur Excel `data/exports/budget_AAAA-MM.xlsx` avec deux
types d'onglets :

- **Synthèse** : un tableau par vue (joint, puis perso en dessous), une ligne par mois synthétisé
  avec les dépenses de chaque catégorie, puis un graphique sur 12 mois et les commentaires.
- **Un onglet par mois** (`2026-06`) : le commentaire, le récapitulatif par catégorie et
  sous-catégorie de chaque vue, deux graphiques de répartition, puis les opérations suivies du mois
  avec un filtre.

Le classeur est une projection du ledger, construite en entier à chaque publication ; un bandeau
le rappelle. Un fichier par mois : publier août ne touche pas au classeur de juillet, et les
fichiers successifs forment un historique. À la fin de l'étape, `budget publish --mois 2026-07`
écrit `budget_2026-07.xlsx`, avec juin et juillet.

## Références cadrage

- §5 Deux vues séparées, jamais additionnées.
- §8 Restitution ; le classeur est une projection régénérable.
- §9 Stack : `openpyxl`.
- §11 Aucune publication tant qu'un contrôle échoue.

## État du code à l'entrée

- `restitution.synthese.produire_synthese` et la table `syntheses` (spec 08) : agrégats et
  commentaire de chaque mois synthétisé.
- `restitution/operations.py` : `lignes_operations(transactions, classements)` donne les lignes de
  l'export CSV (9 colonnes), une par opération suivie.
- `categories.Categorie` : ordre et libellés des 10 catégories.

## Périmètre

**Inclus** : contenu des deux types d'onglets, écriture du classeur, commande `budget publish`.

**Exclus** : Google Sheets et dépôt sur Google Drive (phase 2), `budget run` (étape 10).

## Mois publiés

Le classeur du mois M contient M et **chaque mois antérieur qui a déjà une synthèse** ; jamais un
mois postérieur, même synthétisé. Chacun repasse par la chaîne de la spec 08 : reclassement, contrôles bloquants,
agrégats, commentaire réutilisé si l'empreinte n'a pas changé. La synthèse et les opérations d'un
onglet reflètent donc toujours le même classement, et les règles en vigueur. Un mois en échec
(par exemple après la désactivation d'une règle) arrête la commande avant toute écriture.

## Cellules

- Montant : `Decimal` dans la cellule, format `#,##0.00 "€"` ; écarts
  `[Red]+#,##0.00 "€";[Color10]-#,##0.00 "€";0.00 "€"` (dépassement en rouge).
- Date : `datetime.date`, format `dd/mm/yyyy`.
- Texte : toujours une chaîne (`data_type = "s"`), y compris un libellé commençant par `=`, qui ne
  doit jamais devenir une formule.
- Cellule vide quand le budget ou la moyenne n'existent pas.

## Onglets

Chaque onglet commence par un bandeau en A1 : « Projection générée par budget le 16/10/2026 à
18:40 à partir du ledger. Toute modification manuelle sera écrasée à la prochaine publication. »
Ordre : Synthèse, puis les mois du plus récent au plus ancien.

### Synthèse

De haut en bas, colonne A figée :

1. **Tableau « Vue joint »** à partir de la ligne 3 : titre, en-têtes, une ligne par mois dans
   l'ordre chronologique. 12 colonnes : Mois (`AAAA-MM`), Total (en gras), puis une colonne par
   catégorie dans l'ordre de `Categorie`, 0,00 € si aucune dépense.
2. **Tableau « Vue perso »**, une ligne vide plus bas, mêmes colonnes.
3. **Graphique** en colonnes groupées, jamais empilées (les deux vues ne s'additionnent pas) : les
   12 derniers mois, séries « Joint : total » et « Perso : total ».
4. **Commentaires** : Mois, puis `commentaire.resume` ou « Commentaire à régénérer : budget
   synthese --mois AAAA-MM ».

### Onglet mensuel (`2026-07`)

De haut en bas :

1. **Commentaire**, à partir de A3 : le résumé (ou la mention « à régénérer »), puis un point clé
   par ligne, préfixé de « • ».
2. **Récapitulatifs**, une ligne vide plus bas : bloc « Vue joint » en colonne A, bloc « Vue
   perso » en colonne H. Titre, en-têtes (Catégorie, Montant, Budget, Écart budget, Moyenne 3
   mois, Écart moyenne), une ligne par catégorie des agrégats suivie de ses sous-catégories non
   nulles (par montant décroissant, en italique, plus petites, en retrait, montant seul), puis une
   ligne Total égale au total de la vue (contrôle 3 de la spec 08). Le budget cible de chaque
   catégorie (`budgets` de `contexte.yaml`) figure dans la colonne Budget, avec son écart.
3. **Deux graphiques en secteurs** (répartition par catégorie, joint puis perso), à droite des
   récapitulatifs (colonnes O et W). Une catégorie de montant négatif est exclue des secteurs,
   mais reste dans le récapitulatif.
4. **Opérations**, sous les graphiques : les 9 colonnes de l'export CSV, une ligne par opération
   suivie, dans l'ordre de `lignes_operations`, non comptées incluses ; filtre automatique sur
   l'en-tête et toutes les lignes.

Le filtre masque des lignes entières : aucune autre donnée ne partage les lignes des opérations.

## Commande `budget publish`

`budget publish --mois AAAA-MM [--base …] [--contexte …] [--sans-llm] [--sortie data/exports]` :

1. Synthèse des mois publiés (ci-dessus) ; échec d'un contrôle : code 1, aucun fichier écrit.
2. Classeur construit en mémoire (`construire_classeur`, sans fichier), puis écrit dans
   `budget_AAAA-MM.xlsx` par un fichier temporaire renommé : republier un mois ne remplace son
   classeur que par un classeur complet, et ne touche pas aux autres mois.
3. Affichage d'un tableau (mois, total joint, total perso, opérations, commentaire) et du chemin.

Fichier ouvert dans Excel (Windows refuse le remplacement) : `PublicationError` invitant à le
fermer et à relancer.

## Organisation du code

```
src/budget/publication/
├── __init__.py
├── contenu.py      # preparer_contenu : mois publiés, synthèses, lignes d'opérations
└── excel.py        # construire_classeur (pure), enregistrer
```

## Valeurs attendues sur les fixtures

Scénarios de référence de juin et juillet (`tests/scenarios/reference.py`),
`contexte.example.yaml`, juin synthétisé avec un `FunctionModel`, instant fixe, publication de
juillet.

| Élément | Valeur |
| --- | --- |
| Onglets | `Synthèse`, `2026-07`, `2026-06`, dans cet ordre ; le classeur de juin n'a que `Synthèse` et `2026-06` |
| Synthèse | 2 lignes par tableau : juin (joint 7 566,46, Logement 3 289,26, Divers 0,00 ; perso 1 322,86, Loisirs 685,69) et juillet (joint 9 404,27, Divers 618,45 ; perso 1 154,49) |
| Onglet `2026-07` | 169 lignes d'opérations ; totaux des récapitulatifs : joint 9 404,27 €, perso 1 154,49 € ; écart budget Courses joint +157,92 € ; moyenne 3 mois Logement joint 3 289,26 € ; Logement joint détaillé, dont Autre 2 659,16 € ; chaque catégorie égale à la somme de ses sous-catégories |
| Onglet `2026-06` | 137 lignes d'opérations ; totaux : joint 7 566,46 €, perso 1 322,86 € |
| Graphiques | 1 sur Synthèse, 2 par onglet mensuel |
| Seconde publication | Mêmes valeurs dans chaque onglet |

## Dépendances autorisées

| Groupe | Paquet |
| --- | --- |
| Runtime | `openpyxl>=3.1,<4` |

openpyxl n'a pas d'annotations de type : `[[tool.mypy.overrides]]` `ignore_missing_imports` ciblé
(la dépendance `types-openpyxl` n'a pas été ajoutée).

## Critères d'acceptation

- [x] Aucun `float` dans les cellules ; toute cellule de texte est une chaîne, même `=…` ; montants
  en `Decimal` avec le format monétaire.
- [x] Contrôles bloquants : un mois avec une opération en attente n'est pas publié (code 1, aucun
  fichier écrit), y compris un mois déjà synthétisé remis en attente.
- [x] Synthèse : tableau joint puis tableau perso, mois dans l'ordre chronologique, graphique en
  colonnes groupées, commentaires ou mention « à régénérer ».
- [x] Onglet mensuel : commentaire, récapitulatifs dont chaque total égale celui de la vue, avec
  le détail des sous-catégories, deux graphiques, 9 colonnes d'opérations filtrables, rien d'autre
  sur leurs lignes.
- [x] Un fichier par mois, sans les mois postérieurs.
- [x] Fichier relu par openpyxl ; deux publications successives donnent les mêmes valeurs ; fichier
  verrouillé : `PublicationError`.
- [ ] Recette manuelle dans Excel : bandeaux, formats français, filtre, graphiques lisibles (dont
  un secteur avec une catégorie négative exclue, si le cas se présente).
- [x] Vérification complète (ruff, format, mypy, pytest) au vert.

## Tests attendus

| Fichier | Contenu |
| --- | --- |
| `test_publication.py` | Onglets, valeurs attendues, formats, graphiques, texte `=…`, catégorie négative, relecture du fichier, republication, fichier verrouillé, contrôles bloquants, commande |

## Points ouverts

- **Taille du classeur** : 12 onglets mensuels par an, acceptable.
- **Libellés dans le classeur** : il contient les libellés complets ; il reste dans `data/`, hors
  git.
- **Google Sheets** : phase 2 si une consultation en ligne devient nécessaire ; `contenu.py` serait
  réutilisé tel quel.

## Écarts constatés

- **Bascule vers Excel** (16 octobre 2026) : voir l'encadré en tête. Supprimés avec Google Sheets :
  le plan de requêtes et son golden JSON, le classeur en mémoire, le client `gspread`, les options
  `--simulation` et `--tous-les-mois`, le marqueur `sheets_reel` et les variables
  `GOOGLE_APPLICATION_CREDENTIALS`, `BUDGET_SPREADSHEET_ID`, `BUDGET_SPREADSHEET_ID_TEST`.
- **`--tous-les-mois` supprimé** : le classeur étant construit en entier, chaque mois antérieur
  déjà synthétisé est toujours recalculé.
- **Retours de la première recette (16 octobre 2026)** : Synthèse en deux tableaux (joint, puis
  perso) au lieu d'une ligne de 24 colonnes, commentaires dans un bloc à part sous le graphique ;
  sous-catégories sous chaque catégorie du récapitulatif mensuel ; un classeur par mois
  (`budget_AAAA-MM.xlsx`) au lieu d'un `budget.xlsx` unique. Chaque classeur est construit à
  partir du ledger, pas du classeur précédent : le ledger reste la seule source, et un classeur
  abîmé ou modifié à la main n'en contamine aucun autre.
- **Sous-catégories** : montant seul ; ni budget ni moyenne, que les agrégats de la spec 08 ne
  calculent qu'au niveau de la catégorie.
- **Mise en page de l'onglet mensuel** : la première version plaçait le récapitulatif à droite
  des opérations, sur les mêmes lignes ; le filtre l'aurait masqué en partie. Récapitulatifs et
  graphiques passent au-dessus des opérations ; l'en-tête des opérations n'est donc pas figé.
- **Pas de golden** : un `.xlsx` (archive zip horodatée) n'est pas comparable octet à octet ; les
  tests relisent les cellules.
- **Nombres dans le fichier** : le code ne manipule que des `Decimal` ; openpyxl les sérialise en
  texte à 16 chiffres significatifs (`%.16g`), qu'Excel stocke en double comme tout nombre. Exact
  au centime pour tout montant du foyer.
- **Secteurs en référence d'union** : les sous-catégories séparent les lignes de catégories ;
  les secteurs lisent donc une union (`('2026-07'!$B$9,'2026-07'!$B$12)`), qui exclut aussi une
  catégorie négative. À vérifier dans Excel.
- **Ledger** : ajout de `Ledger.syntheses()` (lecture de toutes les synthèses), sans changement de
  schéma.
- **Spec 10** : `budget run --simulation` s'appuyait sur `publish --simulation`, supprimé ; à
  préciser à la reprise de l'étape 10.
