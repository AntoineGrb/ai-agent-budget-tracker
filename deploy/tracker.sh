# Alias du Budget Tracker, à charger sur le VPS (voir docs/deploiement.md).
#   echo 'source /opt/tracker/deploy/tracker.sh' >> ~/.bashrc
# Convention : « t » + nom de la commande. `thelp` liste tout.
# Variables : TRACKER_HOME (défaut /opt/tracker), TRACKER_BACKUP (défaut ~/backups).

export TRACKER_HOME="${TRACKER_HOME:-/opt/tracker}"

# Exécute une commande dans le dossier du projet, d'où que l'on soit (les chemins data/... sont relatifs).
_tracker() { (cd "$TRACKER_HOME" && "$@"); }

# --- Traitement mensuel -------------------------------------------------------------------------
trun()      { _tracker uv run budget run "$@"; }         # tout enchaîner : --mois M | --depuis M --jusqu-a M
timport()   { _tracker uv run budget import "$@"; }      # contrôler et importer : --mois M
tclassify() { _tracker uv run budget classify "$@"; }    # questions + validation des cartes : --mois M
tsynthese() { _tracker uv run budget synthese "$@"; }    # synthèse commentée : --mois M
tpublish()  { _tracker uv run budget publish "$@"; }     # classeur Excel : --mois M
texport()   { _tracker uv run budget export "$@"; }      # CSV du mois : --mois M

# --- Règles apprises et configuration -----------------------------------------------------------
trules()    { _tracker uv run budget rules list "$@"; }      # lister (--toutes, --type T)
tdisable()  { _tracker uv run budget rules disable "$@"; }   # désactiver la règle N
tconfig()   { _tracker uv run budget config verifier "$@"; } # valider data/contexte.yaml
tversion()  { _tracker uv run budget --version; }
tbudget()   { _tracker uv run budget "$@"; }                 # accès direct : tbudget --help

# --- État, sauvegarde ---------------------------------------------------------------------------
tstatus() {
  echo "== Extractions déposées (data/raw) =="; ls -1 "$TRACKER_HOME/data/raw" 2>/dev/null || echo "(aucune)"
  echo "== Classeurs publiés (data/exports) =="; ls -1 "$TRACKER_HOME/data/exports" 2>/dev/null || echo "(aucun)"
  echo "== Base =="; ls -lh "$TRACKER_HOME/data/budget.sqlite" 2>/dev/null || echo "(pas encore créée)"
}
tbackup() {  # archive datée de data/ dans $TRACKER_BACKUP, conserve les 12 plus récentes
  local dest="${TRACKER_BACKUP:-$HOME/backups}"; mkdir -p "$dest"
  local f="$dest/tracker-data-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar -czf "$f" -C "$TRACKER_HOME" data && chmod 600 "$f" && echo "Sauvegarde : $f"
  ls -1t "$dest"/tracker-data-*.tar.gz | tail -n +13 | xargs -r rm --
}
tdbcopy() {  # copie cohérente de la base (utilisée par `tpull -Base` depuis le PC)
  _tracker python3 -c "import sqlite3; s=sqlite3.connect('data/budget.sqlite'); d=sqlite3.connect('data/budget.snapshot.sqlite'); s.backup(d); d.close(); s.close()" \
    && echo "$TRACKER_HOME/data/budget.snapshot.sqlite"
}

# --- Maintenance du code ------------------------------------------------------------------------
tsync()     { _tracker uv sync "$@"; }                                     # installer les dépendances
tupdate()   { _tracker git pull --ff-only && _tracker uv sync --frozen; }  # récupérer la dernière version
ttest()     { _tracker uv run pytest -m "not donnees_reelles" "$@"; }
tlint()     { _tracker uv run ruff check . "$@"; }
tformat()   { _tracker uv run ruff format "${@:-.}"; }
ttypes()    { _tracker uv run mypy src; }
tcheck()    { _tracker bash -c 'uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest'; }

# --- Aide ---------------------------------------------------------------------------------------
thelp() {
cat <<'AIDE'
Budget Tracker : alias VPS (dossier : $TRACKER_HOME, défaut /opt/tracker)

TRAITEMENT MENSUEL                  (M = AAAA-MM)
  trun --mois M                     tout enchaîner : import, questions, cartes, synthèse, classeur
  trun --depuis M --jusqu-a M       plusieurs mois, du plus ancien au plus récent
        options : --sans-llm (saisie manuelle, sans commentaire) · --simulation (sans écrire le .xlsx)
  timport --mois M                  contrôler et importer les trois extractions
  tclassify --mois M                questions + validation des cartes   (--sans-questions, --sans-llm)
  tsynthese --mois M                synthèse commentée                  (--sans-llm, --regenerer)
  tpublish --mois M                 classeur data/exports/budget_M.xlsx (--sortie DOSSIER)
  texport --mois M                  CSV du mois dans data/exports/

RÈGLES ET CONFIGURATION
  trules [--toutes] [--type T]      lister les règles apprises
  tdisable N                        désactiver la règle N (puis tclassify --mois M)
  tconfig                           valider data/contexte.yaml
  tversion                          version installée
  tbudget ...                       n'importe quelle commande : tbudget --help

ÉTAT ET SAUVEGARDE
  tstatus                           extractions déposées, classeurs publiés, base
  tbackup                           archive datée de data/ dans ~/backups (12 conservées)
  tdbcopy                           copie cohérente de la base (pour tpull -Base côté PC)

MAINTENANCE DU CODE
  tsync                             uv sync
  tupdate                           git pull + uv sync --frozen
  ttest · tlint · tformat · ttypes  tests (hors données réelles), ruff, format, mypy
  tcheck                            vérification complète (lint, format, typage, tests)

Les alias côté PC (tpush, tpull, tvps, tssh) s'obtiennent avec `thelp` dans PowerShell.
AIDE
}
