param(
  [string]$RepoRoot = "",
  [string]$VenvPath = "D:\tools\venvs\yizhi-backend"
)

$ErrorActionPreference = "Stop"
Write-Host "[deprecated] scripts/rebuild_backend_server_exe.ps1 has moved to packaging/scripts/windows/rebuild_backend_server_exe.ps1"

$repo = if ($RepoRoot) { (Resolve-Path $RepoRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$target = Join-Path $repo "packaging\scripts\windows\rebuild_backend_server_exe.ps1"
if (-not (Test-Path $target)) { throw "[backend_exe] target script not found: $target" }

& $target -RepoRoot $repo -VenvPath $VenvPath
exit $LASTEXITCODE

