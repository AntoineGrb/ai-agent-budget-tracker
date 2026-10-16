# Pont PC <-> VPS du Budget Tracker (PowerShell). À charger depuis $PROFILE :
#   . "C:\Users\admin\Documents\programmation\ai-agent-budget-tracker\deploy\tracker.ps1"
# Prérequis : OpenSSH client (ssh, scp) et une clé SSH autorisée sur le VPS.
# Variables d'environnement :
#   TRACKER_VPS      obligatoire, ex. deploy@mon-vps (ou un Host de ~/.ssh/config)
#   TRACKER_VPS_DIR  facultatif, défaut /opt/tracker
#   TRACKER_LOCAL    facultatif, défaut : racine du projet contenant ce script

$script:TrackerLocal = if ($env:TRACKER_LOCAL) { $env:TRACKER_LOCAL } else { Split-Path -Parent $PSScriptRoot }

function Get-TrackerCible {
    if (-not $env:TRACKER_VPS) { throw "Définir TRACKER_VPS (ex. `$env:TRACKER_VPS = 'deploy@mon-vps')." }
    $dir = if ($env:TRACKER_VPS_DIR) { $env:TRACKER_VPS_DIR } else { '/opt/tracker' }
    [pscustomobject]@{ Hote = $env:TRACKER_VPS; Dir = $dir }
}

function Test-TrackerMois([string]$Mois) {
    if ($Mois -notmatch '^\d{4}-(0[1-9]|1[0-2])$') { throw "Mois attendu au format AAAA-MM, reçu : '$Mois'." }
}

function tpush {
    <# Envoie les extractions du mois (data\raw\AAAA-MM) vers le VPS. -Contexte envoie aussi data\contexte.yaml. #>
    param([Parameter(Mandatory)][string]$Mois, [switch]$Contexte)
    $c = Get-TrackerCible; Test-TrackerMois $Mois
    $src = Join-Path $script:TrackerLocal "data\raw\$Mois"
    if (-not (Test-Path $src)) { throw "Dossier introuvable : $src" }
    ssh $c.Hote "mkdir -p '$($c.Dir)/data/raw/$Mois' && chmod 700 '$($c.Dir)/data' '$($c.Dir)/data/raw'"
    if ($LASTEXITCODE) { throw "Connexion SSH impossible." }
    scp -r "$src\*" "$($c.Hote):$($c.Dir)/data/raw/$Mois/"
    if ($LASTEXITCODE) { throw "Échec de l'envoi des extractions." }
    if ($Contexte) {
        scp (Join-Path $script:TrackerLocal 'data\contexte.yaml') "$($c.Hote):$($c.Dir)/data/contexte.yaml"
        if ($LASTEXITCODE) { throw "Échec de l'envoi de contexte.yaml." }
    }
    Write-Host "OK : $Mois envoyé. Ensuite : tvps trun --mois $Mois"
}

function tpull {
    <# Rapatrie les classeurs Excel (un mois, ou tous) ; -Base rapatrie aussi une copie de la base. #>
    param([string]$Mois, [switch]$Base)
    $c = Get-TrackerCible
    $dest = Join-Path $script:TrackerLocal 'data\exports'
    New-Item -ItemType Directory -Force $dest | Out-Null
    $motif = '*.xlsx'
    if ($Mois) { Test-TrackerMois $Mois; $motif = "budget_$Mois.xlsx" }
    scp "$($c.Hote):$($c.Dir)/data/exports/$motif" "$dest\"
    if ($LASTEXITCODE) { throw "Échec du rapatriement des classeurs." }
    if ($Base) {
        ssh $c.Hote "source '$($c.Dir)/deploy/tracker.sh' && tdbcopy >/dev/null"
        if ($LASTEXITCODE) { throw "Échec de la copie de la base sur le VPS." }
        scp "$($c.Hote):$($c.Dir)/data/budget.snapshot.sqlite" (Join-Path $script:TrackerLocal 'data\budget.vps.sqlite')
        if ($LASTEXITCODE) { throw "Échec du rapatriement de la base." }
        Write-Host "Base copiée dans data\budget.vps.sqlite (data\budget.sqlite n'est pas touchée)."
    }
    Write-Host "OK : classeurs dans $dest"
}

function tvps {
    <# Lance un alias du VPS avec terminal interactif : tvps trun --mois 2026-06 #>
    param([Parameter(Mandatory, ValueFromRemainingArguments)][string[]]$Commande)
    $c = Get-TrackerCible
    ssh -t $c.Hote "source '$($c.Dir)/deploy/tracker.sh' && $($Commande -join ' ')"
}

function tssh { $c = Get-TrackerCible; ssh -t $c.Hote "cd '$($c.Dir)'; exec bash -l" }

function thelp {
@'
Budget Tracker : alias côté PC (PowerShell)

  tpush AAAA-MM [-Contexte]   envoie data\raw\AAAA-MM vers le VPS (+ data\contexte.yaml avec -Contexte)
  tpull [AAAA-MM] [-Base]     rapatrie les classeurs Excel (un mois, ou tous) ; -Base : copie de la
                              base dans data\budget.vps.sqlite
  tvps <commande>             exécute un alias du VPS, avec terminal : tvps trun --mois 2026-06
  tssh                        session SSH ouverte dans /opt/tracker
  thelp                       cette aide

Configuration : $env:TRACKER_VPS = 'utilisateur@hote' (obligatoire), TRACKER_VPS_DIR (défaut /opt/tracker).
Alias du VPS (trun, timport, tclassify, tsynthese, tpublish, texport, trules, tdisable, tconfig,
tstatus, tbackup, tupdate, tcheck...) : `tvps thelp`, ou docs/deploiement.md.

Flux mensuel :  tpush 2026-06  ->  tvps trun --mois 2026-06  ->  tpull 2026-06
'@ | Write-Host
}
