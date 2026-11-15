# Delete demo seed data from the SHC database.
# Usage:
#   .\scripts\delete-seed-data.ps1 [-Db PATH] [-Docker]
param(
    [string]$Db = "data/scientific_home_cluster.db",
    [switch]$Docker
)
$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir
if ($Docker) {
    python scripts/delete_seed_data.py --docker
} else {
    python scripts/delete_seed_data.py --db $Db
}