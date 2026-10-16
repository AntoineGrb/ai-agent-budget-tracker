# Déploiement sur le VPS

Le code et les données vivent sur le VPS dans `/opt/tracker`. Les extractions bancaires sont
téléchargées sur le PC, puis poussées vers le VPS ; les classeurs Excel font le chemin inverse.
Tout est hors git : `data/` ne transite que par `scp`.

```
PC (extractions)  --tpush-->  VPS /opt/tracker/data/raw/AAAA-MM
                              VPS : trun --mois AAAA-MM   (questions dans le terminal SSH)
PC (Excel)        <--tpull--  VPS /opt/tracker/data/exports/budget_AAAA-MM.xlsx
```

## Alias

Convention : `t` + nom de la commande. Deux jeux, chacun avec son `thelp`.

**Sur le VPS** (`deploy/tracker.sh`, utilisables depuis n'importe quel dossier)

| Alias | Commande | Rôle |
|---|---|---|
| `trun --mois M` | `budget run` | import, questions, cartes, synthèse, classeur |
| `trun --depuis M --jusqu-a M` | `budget run` | plusieurs mois ; `--sans-llm`, `--simulation` |
| `timport --mois M` | `budget import` | contrôler et importer les extractions |
| `tclassify --mois M` | `budget classify` | questions + validation des cartes |
| `tsynthese --mois M` | `budget synthese` | synthèse commentée (`--sans-llm`, `--regenerer`) |
| `tpublish --mois M` | `budget publish` | classeur `data/exports/budget_M.xlsx` |
| `texport --mois M` | `budget export` | CSV du mois |
| `trules` | `budget rules list` | règles apprises (`--toutes`, `--type`) |
| `tdisable N` | `budget rules disable` | désactiver la règle N |
| `tconfig` | `budget config verifier` | valider `data/contexte.yaml` |
| `tversion` / `tbudget …` | `budget` | version / accès direct à la CLI |
| `tstatus` | | extractions, classeurs, base |
| `tbackup` | | archive datée de `data/` dans `~/backups` (12 conservées) |
| `tdbcopy` | | copie cohérente de la base (utilisée par `tpull -Base`) |
| `tsync`, `tupdate` | `uv sync`, `git pull` + `uv sync --frozen` | dépendances, mise à jour |
| `ttest`, `tlint`, `tformat`, `ttypes`, `tcheck` | | tests, ruff, format, mypy, vérification complète |
| `thelp` | | aide |

**Sur le PC** (`deploy/tracker.ps1`, PowerShell)

| Alias | Rôle |
|---|---|
| `tpush AAAA-MM [-Contexte]` | envoie `data\raw\AAAA-MM` (et `data\contexte.yaml` avec `-Contexte`) |
| `tpull [AAAA-MM] [-Base]` | rapatrie les classeurs (un mois ou tous) ; `-Base` : copie de la base dans `data\budget.vps.sqlite`, sans toucher la base locale |
| `tvps <alias>` | lance un alias du VPS avec terminal, ex. `tvps trun --mois 2026-06` |
| `tssh` | session SSH ouverte dans `/opt/tracker` |
| `thelp` | aide |

## Publier le projet sur le VPS (une seule fois)

1. **Accès SSH par clé** depuis le PC (si ce n'est pas fait) :
   `ssh-keygen -t ed25519`, puis `ssh-copy-id utilisateur@vps` (ou ajouter `~/.ssh/id_ed25519.pub`
   dans `~/.ssh/authorized_keys` du VPS). Désactiver ensuite la connexion SSH par mot de passe.
2. **Prérequis sur le VPS** : `git` et `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
   Inutile d'installer Python : `uv sync` récupère la version 3.12 demandée par le projet.
3. **Dossier** (utilisateur non root) :
   `sudo mkdir -p /opt/tracker && sudo chown $USER: /opt/tracker`
4. **Cloner** : `git clone <url-du-depot> /opt/tracker`. Dépôt privé : ajouter une *deploy key*
   en lecture seule (`ssh-keygen -t ed25519 -f ~/.ssh/tracker_deploy`, clé publique dans
   GitHub > Settings > Deploy keys, et `Host github.com` + `IdentityFile` dans `~/.ssh/config`).
5. **Installer** : `cd /opt/tracker && uv sync --frozen`
6. **Clé API** (le LLM ne catégorise que les marchands carte inconnus) :
   `cp .env.example .env && chmod 600 .env`, puis renseigner `ANTHROPIC_API_KEY=`.
7. **Dossier de données protégé** : `mkdir -p data/raw data/exports && chmod 700 data`
8. **Alias VPS** : `echo 'source /opt/tracker/deploy/tracker.sh' >> ~/.bashrc && source ~/.bashrc`,
   puis `thelp`.
9. **Alias PC** : dans PowerShell, `notepad $PROFILE` et ajouter
   ```powershell
   $env:TRACKER_VPS = 'utilisateur@vps'      # ou le nom d'un Host de ~/.ssh/config
   . "C:\Users\admin\Documents\programmation\ai-agent-budget-tracker\deploy\tracker.ps1"
   ```
   Rouvrir PowerShell, puis `thelp`.
10. **Contexte du foyer et historique** : depuis le PC, `tpush AAAA-MM -Contexte` pour le premier
    mois (puis `tpush` pour chaque mois historique à reprendre). Sur le VPS : `tconfig`.
    Si l'historique est déjà traité en local, `scp data/budget.sqlite` vers
    `/opt/tracker/data/` (une fois, avant tout `trun` sur le VPS) évite de refaire les questions.
11. **Vérifier** : `tvps tcheck` (tests hors données réelles inclus) puis `tvps tstatus`.
12. **Sauvegarde automatique** (facultatif) : `crontab -e`, puis
    `0 3 * * 0 bash -c 'source /opt/tracker/deploy/tracker.sh && tbackup'`.
    Récupérer de temps en temps une archive sur le PC : `tpull -Base`.

## Chaque mois

1. Télécharger les trois extractions dans `data\raw\AAAA-MM\` sur le PC.
2. `tpush AAAA-MM`
3. `tvps trun --mois AAAA-MM` : répondre aux questions, valider les cartes.
4. `tpull AAAA-MM` puis ouvrir `data\exports\budget_AAAA-MM.xlsx`.
5. Facultatif : `tvps tbackup`.

## Mettre à jour le code

Sur le PC : `git push`. Puis `tvps tupdate` (`git pull --ff-only` + `uv sync --frozen`).

## Points d'attention

- **Une seule base de référence** : le VPS. Ne pas traiter le même mois à la fois en local et sur
  le VPS ; `tpull -Base` ne sert qu'à consulter ou sauvegarder.
- **Terminal obligatoire** pour les questions : `tvps` / `tssh` allouent un TTY. Un `ssh` sans `-t`
  (ou un cron) arrête le run dès qu'une opération reste en attente.
- **Données sensibles** : `data/` en `700`, `.env` en `600`, jamais de données dans git. Seuls les
  libellés marchands de paiements carte partent vers le LLM (cadrage §11).
