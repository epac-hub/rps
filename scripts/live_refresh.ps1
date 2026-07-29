$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

if (-not $env:SKYTRACKIT_USER -or -not $env:SKYTRACKIT_PASSWORD) {
    throw "Missing SKYTRACKIT_USER or SKYTRACKIT_PASSWORD environment variables."
}

if (-not $env:SKYTRACKIT_LOOKBACK_DAYS) {
    $env:SKYTRACKIT_LOOKBACK_DAYS = "5"
}

python scripts/fetch_skytrackit.py
python scripts/build_dashboard.py

git add index.html dashboard_data.json dashboard_interactivo_resumen.csv dashboard_interactivo_rutas.csv

$changes = git diff --cached --name-only
if (-not $changes) {
    Write-Output "No dashboard changes to publish."
    exit 0
}

git config user.name "rps-dashboard-bot"
git config user.email "dashboard@rpsmedical.com"
git commit -m "Refresh fleet dashboard data"
git push origin main
