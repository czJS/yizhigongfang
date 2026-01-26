param(
  [string]$ResourcesDir = "",
  [switch]$SkipKillPort = $false
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

if (-not $ResourcesDir) {
  $desktopDir = Join-Path $repoRoot "apps\desktop"
  if (-not (Test-Path $desktopDir)) { $desktopDir = Join-Path $repoRoot "frontend" } # transition fallback
  $ResourcesDir = Join-Path $desktopDir "dist_electron\win-unpacked\resources"
}
$ResourcesDir = (Resolve-Path $ResourcesDir).Path

Write-Host ("[smoke] resources={0}" -f $ResourcesDir)

function Kill-PortWin([int]$Port) {
  try {
    $lines = (netstat -ano | Select-String (":$Port\s") | ForEach-Object { $_.Line })
  } catch { $lines = @() }
  foreach ($l in ($lines | ForEach-Object { "$_" })) {
    # last token is PID
    $parts = ($l -split "\s+") | Where-Object { $_ }
    if ($parts.Count -lt 5) { continue }
    # NOTE: PowerShell has a built-in read-only automatic variable `$PID` (case-insensitive).
    # Do NOT assign to `$pid` here; use a different name.
    $procId = [int]$parts[-1]
    if ($procId -le 0) { continue }
    try {
      $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
      $name = if ($p) { $p.ProcessName } else { "" }
      if ($name -match "backend_server") {
        Write-Host ("[smoke] killing pid={0} name={1} (port {2})" -f $procId, $name, $Port)
        taskkill /PID $procId /T /F | Out-Null
      }
    } catch {}
  }
}

if (-not $SkipKillPort) {
  Kill-PortWin -Port 5175
}

$backendExe = Join-Path $ResourcesDir "backend_server.exe"
$workerExe = Join-Path $ResourcesDir "quality_worker.exe"

if (!(Test-Path $backendExe)) { throw "[smoke] missing backend_server.exe in resources" }
if (!(Test-Path $workerExe)) { throw "[smoke] missing quality_worker.exe in resources" }

# Ensure these env vars mimic the installed app behavior (Electron main process).
$env:YGF_APP_ROOT = $ResourcesDir
$cfg = Join-Path $ResourcesDir "configs\quality.yaml"
if (-not (Test-Path $cfg)) { $cfg = Join-Path $ResourcesDir "config\quality.yaml" } # legacy
if (Test-Path $cfg) { $env:CONFIG_PATH = $cfg }

$tmpOut = Join-Path $repoRoot "tmp_smoke_outputs"
New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null
$env:YGF_OUTPUTS_ROOT = $tmpOut

Push-Location $ResourcesDir
try {
  Write-Host "[smoke] backend_server.exe --self-check"
  & $backendExe --self-check
  $code1 = $LASTEXITCODE
  if ($code1 -ne 0) { throw "[smoke] backend self-check failed (exit $code1)" }

  Write-Host "[smoke] quality_worker.exe --self-check"
  & $workerExe --self-check
  $code2 = $LASTEXITCODE
  if ($code2 -ne 0) { throw "[smoke] worker self-check failed (exit $code2)" }
} finally {
  Pop-Location
}

Write-Host "[smoke] OK"
exit 0

