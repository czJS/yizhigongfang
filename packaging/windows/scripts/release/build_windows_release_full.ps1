param(
  [string]$RepoRoot = "D:\yizhigongfang-main\yizhigongfang-git",
  [string]$Model = "qwen3:8b",
  [string]$PhraseModel = "qwen3:4b",
  [switch]$SkipDist = $false,
  [switch]$ForcePullModel = $false,
  [switch]$SkipPullPhraseModel = $false
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

function Get-LatestWriteTime([string]$Path) {
  if (-not (Test-Path $Path)) { return $null }
  $latest = Get-ChildItem -Path $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property LastWriteTime -Maximum
  if ($latest.Count -eq 0) { return $null }
  return $latest.Maximum
}

function Test-OllamaModelPresent([string]$ModelsRoot, [string]$ModelId) {
  # Best-effort heuristic: avoid touching timestamps by pulling again when it is already present.
  try {
    $manifests = Join-Path $ModelsRoot "manifests"
    if (-not (Test-Path $manifests)) { return $false }
    $base = ($ModelId.Split(":")[0] | ForEach-Object { $_.Trim() })[0]
    if (-not $base) { return $false }
    $hit = Get-ChildItem -Path $manifests -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match [regex]::Escape($base) } |
      Select-Object -First 1
    return $null -ne $hit
  } catch {
    return $false
  }
}

$repo = (Resolve-Path $RepoRoot).Path
$logsDir = Join-Path $repo "outputs\\logs"
$outLog = Join-Path $logsDir "build_windows_release_full.out.log"
$errLog = Join-Path $logsDir "build_windows_release_full.err.log"

Ensure-Dir $logsDir
Remove-Item -Force -ErrorAction SilentlyContinue $outLog, $errLog

try {
  Start-Transcript -Path $outLog -Append | Out-Null
} catch {
  # If transcript fails, continue; logs may still be captured by caller.
}

try {

# Keep writes on D: as much as possible
$dTemp = "D:\temp\yizhistudio"
$npmCache = "D:\cache\npm"
$electronCache = "D:\cache\electron"
$electronBuilderCache = "D:\cache\electron-builder"
$ollamaModels = "D:\tools\ollama_models"

Ensure-Dir $dTemp
Ensure-Dir $npmCache
Ensure-Dir $electronCache
Ensure-Dir $electronBuilderCache
Ensure-Dir $ollamaModels

$env:TEMP = $dTemp
$env:TMP = $dTemp
$env:NPM_CONFIG_CACHE = $npmCache
$env:ELECTRON_CACHE = $electronCache
$env:ELECTRON_BUILDER_CACHE = $electronBuilderCache
$env:OLLAMA_MODELS = $ollamaModels

Write-Host "[full] repo=$repo"
Write-Host "[full] TEMP=$env:TEMP"
Write-Host "[full] NPM_CONFIG_CACHE=$env:NPM_CONFIG_CACHE"
Write-Host "[full] ELECTRON_CACHE=$env:ELECTRON_CACHE"
Write-Host "[full] ELECTRON_BUILDER_CACHE=$env:ELECTRON_BUILDER_CACHE"
Write-Host "[full] OLLAMA_MODELS=$env:OLLAMA_MODELS"
Write-Host "[full] model=$Model"
Write-Host "[full] phrase_model=$PhraseModel"

# Ensure assets/models is populated (build_installer zips from assets/models)
$assetsModels = Join-Path $repo "assets\\models"
$existingPack = Join-Path $repo "apps\\desktop\\dist_electron\\models_pack"
if ((Test-Path $existingPack) -and (Test-Path $assetsModels)) {
  $needRestore = $true
  if (Test-Path (Join-Path $assetsModels "whisperx")) { $needRestore = $false }
  if ($needRestore) {
    Write-Host "[full] restoring assets/models from existing dist_electron/models_pack ..."
    robocopy $existingPack $assetsModels /MIR /XO /XN /XC /FFT /MT:16 /NFL /NDL /NJH /NJS /NP | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { throw "[full] robocopy restore failed: exit $rc" }
  }
}

# Pull Ollama model into D:\tools\ollama_models
$ollamaExe = Join-Path $repo "pack\\ollama\\ollama.exe"
if (-not (Test-Path $ollamaExe)) { throw "[full] ollama.exe not found: $ollamaExe" }

$hasModel = Test-OllamaModelPresent -ModelsRoot $ollamaModels -ModelId $Model
if ($ForcePullModel -or (-not $hasModel)) {
  Write-Host "[full] pulling ollama model (may take long)..."
  & $ollamaExe pull $Model
  if ($LASTEXITCODE -ne 0) { throw "[full] ollama pull failed: exit $LASTEXITCODE" }
} else {
  Write-Host "[full] ollama model appears present; skipping pull (use -ForcePullModel to override)."
}

if (-not $SkipPullPhraseModel) {
  $hasPhrase = Test-OllamaModelPresent -ModelsRoot $ollamaModels -ModelId $PhraseModel
  if ($ForcePullModel -or (-not $hasPhrase)) {
    Write-Host "[full] pulling phrase model for zh phrase extraction (may take long)..."
    & $ollamaExe pull $PhraseModel
    if ($LASTEXITCODE -ne 0) { throw "[full] ollama pull (phrase model) failed: exit $LASTEXITCODE" }
  } else {
    Write-Host "[full] phrase model appears present; skipping pull (use -ForcePullModel to override)."
  }
} else {
  Write-Host "[full] SkipPullPhraseModel=true; skipping phrase model pull."
}

# Run packaging script
$buildInstaller = Join-Path $repo "packaging\\windows\\scripts\\build_installer.ps1"
if (-not (Test-Path $buildInstaller)) { throw "[full] build_installer.ps1 not found: $buildInstaller" }

Write-Host "[full] running packaging/windows/scripts/build_installer.ps1 ..."
# Decide whether to rebuild models_pack.zip. If unchanged, do NOT force rebuild.
$zipPath = Join-Path $repo "apps\\desktop\\dist_electron\\models_pack.zip"
$latestModels = Get-LatestWriteTime $assetsModels
$latestOllama = Get-LatestWriteTime $ollamaModels
$latestSource = $latestModels
if ($latestOllama -and ($latestSource -eq $null -or $latestOllama -gt $latestSource)) {
  $latestSource = $latestOllama
}
$forceModelsPack = $true
if ((Test-Path $zipPath) -and $latestSource) {
  $zipTime = (Get-Item $zipPath).LastWriteTime
  if ($latestSource -le $zipTime) {
    $forceModelsPack = $false
    Write-Host "[full] models_pack.zip is up to date; skipping models pack rebuild."
  } else {
    Write-Host "[full] models_pack source changed; will rebuild models pack."
  }
} else {
  Write-Host "[full] models_pack.zip missing or cannot determine source time; will rebuild models pack."
}

if ($SkipDist) {
  if ($forceModelsPack) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildInstaller -SkipDist -ForceModelsPack
  } else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildInstaller -SkipDist
  }
} else {
  if ($forceModelsPack) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildInstaller -ForceModelsPack
  } else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildInstaller
  }
}
if ($LASTEXITCODE -ne 0) { throw "[full] build_installer.ps1 failed: exit $LASTEXITCODE" }

Write-Host "[full] done."
Write-Host "[full] outputs:"
Write-Host ("  installer: {0}" -f (Join-Path $repo "apps\\desktop\\dist_electron\\YizhiStudio-0.1.0-win.exe"))
Write-Host ("  modelpack:  {0}" -f (Join-Path $repo "apps\\desktop\\dist_electron\\models_pack.zip"))

} catch {
  try {
    ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), ($_ | Out-String)) | Out-File -FilePath $errLog -Append -Encoding utf8
  } catch {}
  throw
} finally {
  try { Stop-Transcript | Out-Null } catch {}
}

