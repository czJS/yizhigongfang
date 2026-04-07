param(
  [string]$RepoRoot = "",
  [string]$VenvPath = "",
  [string]$TempRoot = "",
  [string]$PipCacheDir = ""
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

function Resolve-ConfiguredPath([string]$Primary, [string]$Fallback) {
  if ($Primary) { return $Primary }
  return $Fallback
}

if (-not $RepoRoot) {
  # repo/packaging/windows/scripts -> repo root
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
}

$repo = (Resolve-Path $RepoRoot).Path
$VenvPath = Resolve-ConfiguredPath $VenvPath ($env:YGF_BACKEND_VENV)
if (-not $VenvPath) { $VenvPath = "D:\tools\venvs\yizhi-backend" }
$TempRoot = Resolve-ConfiguredPath $TempRoot ($env:YGF_TEMP_ROOT)
if (-not $TempRoot) { $TempRoot = "D:\temp\yizhistudio" }
$PipCacheDir = Resolve-ConfiguredPath $PipCacheDir ($env:YGF_PIP_CACHE_DIR)
if (-not $PipCacheDir) { $PipCacheDir = "D:\cache\pip" }

$dTemp = Join-Path $TempRoot "backend_server"
$pipCache = $PipCacheDir
Ensure-Dir $dTemp
Ensure-Dir $pipCache

$env:TEMP = $dTemp
$env:TMP = $dTemp
$env:PIP_CACHE_DIR = $pipCache

Write-Host "[backend_exe] repo=$repo"
Write-Host "[backend_exe] venv=$VenvPath"
Write-Host "[backend_exe] TEMP=$env:TEMP"
Write-Host "[backend_exe] PIP_CACHE_DIR=$env:PIP_CACHE_DIR"

# Create venv if missing (use system python)
if (-not (Test-Path $VenvPath)) {
  Write-Host "[backend_exe] creating venv..."
  python -m venv $VenvPath
}

$py = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $py)) { throw "[backend_exe] python not found in venv: $py" }

Write-Host "[backend_exe] upgrading pip + installing pyinstaller..."
& $py -m pip install -U pip pyinstaller

# Install backend runtime deps into this isolated venv so PyInstaller can bundle them.
$reqCandidates = @(
  (Join-Path $repo "apps\backend\requirements.txt"),
  (Join-Path $repo "backend\requirements.txt")
)
$req = $null
foreach ($p in $reqCandidates) {
  if (Test-Path $p) { $req = $p; break }
}
if ($req) {
  Write-Host "[backend_exe] installing backend requirements..."
  & $py -m pip install -r $req
} else {
  Write-Host "[warn] backend requirements not found under apps/backend or backend."
}

$spec = Join-Path $repo "packaging\windows\pyinstaller\backend_server.spec"
if (-not (Test-Path $spec)) { throw "[backend_exe] canonical spec not found: $spec" }

$distDir = Join-Path $repo "dist"
$workDir = Join-Path $repo "build\pyinstaller"
Ensure-Dir $distDir
Ensure-Dir $workDir

Write-Host "[backend_exe] building backend_server.exe ..."
Push-Location $repo
try {
  # Remove any old artifact first so we don't get a false "OK" on build failure.
  $oldExe = Join-Path $distDir "backend_server.exe"
  if (Test-Path $oldExe) { Remove-Item -Force -LiteralPath $oldExe }

  & $py -m PyInstaller --noconfirm --clean --distpath $distDir --workpath $workDir $spec
  if ($LASTEXITCODE -ne 0) { throw "[backend_exe] pyinstaller failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}

$exe = Join-Path $distDir "backend_server.exe"
if (-not (Test-Path $exe)) { throw "[backend_exe] build finished but exe missing: $exe" }
Write-Host "[backend_exe] OK -> $exe"

