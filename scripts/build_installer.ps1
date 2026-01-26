param(
  [int]$TimeoutMinutes = 90,
  [switch]$SkipDist = $false,
  [switch]$ForceModelsPack = $false,
  [switch]$SkipModelsPack = $false,
  [switch]$SkipSmokeCheck = $false
)

$ErrorActionPreference = "Stop"
Write-Host "[deprecated] scripts/build_installer.ps1 has moved to packaging/scripts/windows/build_installer.ps1"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $repoRoot "packaging\scripts\windows\build_installer.ps1"
if (-not (Test-Path $target)) {
  throw "[pack] target script not found: $target"
}

& $target @PSBoundParameters
exit $LASTEXITCODE
