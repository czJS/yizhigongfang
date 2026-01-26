param(
  [string]$ResourcesDir = "",
  [switch]$SkipKillPort = $false
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Write-Host "[deprecated] scripts/verify_win_unpacked_smoke.ps1 has moved to packaging/scripts/windows/verify_win_unpacked_smoke.ps1"

$target = Join-Path $repoRoot "packaging\scripts\windows\verify_win_unpacked_smoke.ps1"
if (-not (Test-Path $target)) {
  throw "[smoke] target script not found: $target"
}

& $target @PSBoundParameters
exit $LASTEXITCODE

