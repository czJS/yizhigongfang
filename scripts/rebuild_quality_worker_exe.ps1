param(
  [string]$RepoRoot = "",
  [string]$VenvPath = "D:\tools\venvs\yizhi-quality"
)

$ErrorActionPreference = "Stop"
Write-Host "[deprecated] scripts/rebuild_quality_worker_exe.ps1 has moved to packaging/scripts/windows/rebuild_quality_worker_exe.ps1"

$repo = if ($RepoRoot) { (Resolve-Path $RepoRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$target = Join-Path $repo "packaging\scripts\windows\rebuild_quality_worker_exe.ps1"
if (-not (Test-Path $target)) { throw "[quality_worker] target script not found: $target" }

& $target -RepoRoot $repo -VenvPath $VenvPath
exit $LASTEXITCODE

