param(
  [int]$TimeoutMinutes = 90,
  [switch]$SkipDist = $false,
  [switch]$ForceModelsPack = $false,
  [switch]$SkipModelsPack = $false,
  [switch]$SkipSmokeCheck = $false
)

$ErrorActionPreference = "Stop"
$nodeDir = "D:\tools\node-v20.11.1-win-x64"
$env:Path = $nodeDir + ";" + $env:Path

# Keep build writes off C: as much as possible (caches + temp)
$dTemp = "D:\temp\yizhistudio\build_installer"
$npmCache = "D:\cache\npm"
$electronCache = "D:\cache\electron"
$electronBuilderCache = "D:\cache\electron-builder"
New-Item -ItemType Directory -Force -Path $dTemp, $npmCache, $electronCache, $electronBuilderCache | Out-Null
$env:TEMP = $dTemp
$env:TMP = $dTemp
$env:NPM_CONFIG_CACHE = $npmCache
$env:ELECTRON_CACHE = $electronCache
$env:ELECTRON_BUILDER_CACHE = $electronBuilderCache
# Timestamp used by electron-builder artifactName to avoid overwriting a running installer
$env:BUILD_TS = (Get-Date -Format "yyyyMMdd_HHmmss")
Write-Host "[pack] TEMP=$env:TEMP"
Write-Host "[pack] NPM_CONFIG_CACHE=$env:NPM_CONFIG_CACHE"
Write-Host "[pack] ELECTRON_CACHE=$env:ELECTRON_CACHE"
Write-Host "[pack] ELECTRON_BUILDER_CACHE=$env:ELECTRON_BUILDER_CACHE"
Write-Host "[pack] BUILD_TS=$env:BUILD_TS"

$distElectron = "D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron"
$modelsZipPath = Join-Path $distElectron "models_pack.zip"
$keptModelsZip = ""
$ollamaZipPath = Join-Path $distElectron "ollama_pack.zip"
$keptOllamaZip = ""
$keepDir = "D:\temp\yizhistudio\build_keep"

# Preflight: ensure required backend executables exist before electron-builder runs.
$repoRoot = "D:\yizhigongfang-main\yizhigongfang-git"
$backendExe = Join-Path $repoRoot "dist\backend_server.exe"
$qualityWorkerExe = Join-Path $repoRoot "dist\quality_worker.exe"
if (-not (Test-Path $backendExe)) {
  throw "[pack] missing dist/backend_server.exe. Please run scripts/rebuild_backend_server_exe.ps1 first."
}
if (-not (Test-Path $qualityWorkerExe)) {
  throw "[pack] missing dist/quality_worker.exe. Please run scripts/rebuild_quality_worker_exe.ps1 first."
}

Write-Host "[pack] stopping electron/node to avoid file locks..."
Get-Process -Name YizhiStudio -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name electron -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force

if (-not $SkipDist) {
  Write-Host "[pack] cleaning dist_electron..."
  # Preserve an existing models_pack.zip so "up-to-date" detection can still work after cleaning.
  # Otherwise, we delete the zip first and then we cannot skip rebuilding even when sources are unchanged.
  if (Test-Path $modelsZipPath) {
    try {
      New-Item -ItemType Directory -Force -Path $keepDir | Out-Null
      $keptModelsZip = Join-Path $keepDir ("models_pack_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".zip")
      Move-Item -Force -LiteralPath $modelsZipPath -Destination $keptModelsZip
      Write-Host ("[pack] preserved models_pack.zip -> {0}" -f $keptModelsZip)
    } catch {
      Write-Host ("[warn] failed to preserve models_pack.zip: {0}" -f $_.Exception.Message)
      $keptModelsZip = ""
    }
  }
  # Preserve an existing ollama_pack.zip to avoid losing the separately distributed package
  # when rebuilding the installer (the installer itself does NOT bundle Ollama).
  if (Test-Path $ollamaZipPath) {
    try {
      New-Item -ItemType Directory -Force -Path $keepDir | Out-Null
      $keptOllamaZip = Join-Path $keepDir ("ollama_pack_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".zip")
      Move-Item -Force -LiteralPath $ollamaZipPath -Destination $keptOllamaZip
      Write-Host ("[pack] preserved ollama_pack.zip -> {0}" -f $keptOllamaZip)
    } catch {
      Write-Host ("[warn] failed to preserve ollama_pack.zip: {0}" -f $_.Exception.Message)
      $keptOllamaZip = ""
    }
  }
  Remove-Item -Recurse -Force $distElectron -ErrorAction SilentlyContinue
} else {
  Write-Host "[pack] skipping dist_electron clean (SkipDist=true)"
}

# NOTE:
# Ollama is no longer bundled into the installer (to avoid multi-GB payload causing slow installs).
# Use scripts/build_ollama_pack.ps1 to produce a separate ollama_pack.zip for user import.

if (-not $SkipDist) {
  Write-Host "[pack] npm run dist (timeout ${TimeoutMinutes}m)..."
  Push-Location "D:\yizhigongfang-main\yizhigongfang-git\frontend"
  $proc = Start-Process -FilePath "npm" -ArgumentList "run dist" -NoNewWindow -PassThru
  $timeoutSec = $TimeoutMinutes * 60
  $done = $proc | Wait-Process -Timeout $timeoutSec -ErrorAction SilentlyContinue
  if (-not $done -and -not $proc.HasExited) {
    Write-Host "[pack] timeout reached, killing build process..."
    Stop-Process -Id $proc.Id -Force
    Pop-Location
    exit 2
  }
  Pop-Location
  if ($proc.HasExited -and $proc.ExitCode -ne 0) {
    Write-Host "[pack] build failed with exit code $($proc.ExitCode)"
    exit $proc.ExitCode
  }
} else {
  Write-Host "[pack] skipping npm run dist (SkipDist=true)"
}

# Fast smoke check against win-unpacked/resources (no installer run required).
if (-not $SkipSmokeCheck) {
  try {
    Write-Host "[pack] smoke check (win-unpacked/resources)..."
    & (Join-Path $repoRoot "scripts\verify_win_unpacked_smoke.ps1")
    if ($LASTEXITCODE -ne 0) {
      throw "[pack] smoke check failed (exit $LASTEXITCODE)"
    }
  } catch {
    Write-Host ("[pack] smoke check failed: {0}" -f $_.Exception.Message)
    exit 3
  }
} else {
  Write-Host "[pack] SkipSmokeCheck=true, skipping smoke check."
}

# Restore preserved models_pack.zip before model-pack stage so the script can skip rebuilding when unchanged.
if ($keptModelsZip -and (Test-Path $keptModelsZip) -and -not (Test-Path $modelsZipPath)) {
  try {
    New-Item -ItemType Directory -Force -Path $distElectron | Out-Null
    Move-Item -Force -LiteralPath $keptModelsZip -Destination $modelsZipPath
    Write-Host "[pack] restored preserved models_pack.zip."
  } catch {
    Write-Host ("[warn] failed to restore preserved models_pack.zip: {0}" -f $_.Exception.Message)
  }
}

# Restore preserved ollama_pack.zip (installer rebuild should not delete the separately distributed package).
if ($keptOllamaZip -and (Test-Path $keptOllamaZip) -and -not (Test-Path $ollamaZipPath)) {
  try {
    New-Item -ItemType Directory -Force -Path $distElectron | Out-Null
    Move-Item -Force -LiteralPath $keptOllamaZip -Destination $ollamaZipPath
    Write-Host "[pack] restored preserved ollama_pack.zip."
  } catch {
    Write-Host ("[warn] failed to restore preserved ollama_pack.zip: {0}" -f $_.Exception.Message)
  }
}

# Extra safety: if the zip is missing (e.g., previous run skipped building) but we have backups, restore the newest.
if (-not (Test-Path $modelsZipPath) -and (Test-Path $keepDir)) {
  try {
    $latestBackup = Get-ChildItem -LiteralPath $keepDir -File -Filter "models_pack_*.zip" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($latestBackup) {
      Copy-Item -Force -LiteralPath $latestBackup.FullName -Destination $modelsZipPath
      Write-Host ("[pack] restored models_pack.zip from latest backup -> {0}" -f $latestBackup.FullName)
    }
  } catch {
    Write-Host ("[warn] failed to restore models_pack.zip from backup: {0}" -f $_.Exception.Message)
  }
}

# Extra safety: restore ollama_pack.zip from latest backup if missing.
if (-not (Test-Path $ollamaZipPath) -and (Test-Path $keepDir)) {
  try {
    $latestOllamaBackup = Get-ChildItem -LiteralPath $keepDir -File -Filter "ollama_pack_*.zip" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($latestOllamaBackup) {
      Copy-Item -Force -LiteralPath $latestOllamaBackup.FullName -Destination $ollamaZipPath
      Write-Host ("[pack] restored ollama_pack.zip from latest backup -> {0}" -f $latestOllamaBackup.FullName)
    }
  } catch {
    Write-Host ("[warn] failed to restore ollama_pack.zip from backup: {0}" -f $_.Exception.Message)
  }
}

# Prepare separate model pack (optional manual copy after install)
function Get-LatestWriteTime([string]$Path) {
  if (-not (Test-Path $Path)) { return $null }
  $latest = Get-ChildItem -Path $Path -Recurse -File -Force |
    Measure-Object -Property LastWriteTime -Maximum
  if ($latest.Count -eq 0) { return $null }
  return $latest.Maximum
}

function Get-SevenZipPath {
  $cmd = Get-Command "7z" -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  $candidates = @(
    "D:\7-Zip\7z.exe",
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe"
  )
  foreach ($path in $candidates) {
    if (Test-Path $path) { return $path }
  }
  return $null
}

function Get-LongPath([string]$Path) {
  if ($Path -like "\\?\*") { return $Path }
  return "\\?\$Path"
}

function New-SubstDrive([string]$TargetPath) {
  $letters = @("Z","Y","X","W","V","U","T","S","R","Q","P")
  $existing = (Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Name)
  foreach ($l in $letters) {
    if ($existing -notcontains $l) {
      $drive = "${l}:"
      & cmd /c "subst $drive `"$TargetPath`"" | Out-Null
      return $drive
    }
  }
  throw "[pack] no free drive letter available for subst."
}

function Remove-SubstDrive([string]$Drive) {
  if (-not $Drive) { return }
  & cmd /c "subst $Drive /D" | Out-Null
}

function Get-ReparseTargetPath([string]$Path) {
  # Decode target from "fsutil reparsepoint query" hex dump (Windows may not print a friendly target line).
  # Expected to be a relative path like ../../blobs/<hash> (HF cache style).
  try {
    $out = fsutil reparsepoint query $Path 2>$null
    if (-not $out) { return "" }
    $bytes = New-Object System.Collections.Generic.List[byte]
    foreach ($line in $out) {
      $s = [string]$line
      if ($s -notmatch '^\s*[0-9A-Fa-f]{4}:\s+') { continue }
      $hexPart = ($s -split ":\s+", 2)[1]
      foreach ($tok in ($hexPart -split "\s+")) {
        if ($tok -match '^[0-9A-Fa-f]{2}$') {
          $bytes.Add([Convert]::ToByte($tok, 16)) | Out-Null
        }
      }
    }
    if ($bytes.Count -eq 0) { return "" }
    $raw = [System.Text.Encoding]::UTF8.GetString($bytes.ToArray())
    $raw = ($raw -replace "\u0000", "").Trim()
    if (-not $raw) { return "" }
    $raw = $raw -replace "/", "\"
    # Convert to absolute path (relative to link parent)
    if ([System.IO.Path]::IsPathRooted($raw)) { return $raw }
    $parent = Split-Path -Parent $Path
    return (Resolve-Path (Join-Path $parent $raw) -ErrorAction SilentlyContinue).Path
  } catch {
    return ""
  }
}

function Materialize-ReparseFiles([string]$RootDir) {
  if (-not (Test-Path $RootDir)) { return 0 }
  $count = 0
  $items = Get-ChildItem -Path $RootDir -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -and ($_.Length -eq 0) }
  foreach ($it in $items) {
    $src = Get-ReparseTargetPath -Path $it.FullName
    if (-not $src -or -not (Test-Path -LiteralPath $src)) {
      Write-Host ("[warn] cannot resolve reparse target: {0}" -f $it.FullName)
      continue
    }
    try {
      $tmp = ($it.FullName + ".materialized")
      Copy-Item -LiteralPath $src -Destination $tmp -Force
      Remove-Item -LiteralPath $it.FullName -Force
      Move-Item -LiteralPath $tmp -Destination $it.FullName -Force
      $count++
    } catch {
      Write-Host ("[warn] failed to materialize: {0} ({1})" -f $it.FullName, $_.Exception.Message)
    }
  }
  return $count
}

function New-ModelsZip([string]$SourceDir, [string]$ZipPath) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Host "[pack] zipping models_pack..."
  # Build to a temp file first to avoid losing an existing zip on failure.
  $tmpZip = ($ZipPath + ".tmp")
  if (Test-Path $tmpZip) { Remove-Item -Force $tmpZip }

  $sevenZipPath = Get-SevenZipPath
  if (-not $sevenZipPath) {
    throw "[pack] 7z not found. Please install 7-Zip or set 7z.exe in PATH."
  }
  $drive = $null
  try {
    # Use subst to shorten paths (avoid MAX_PATH issues in 3rd-party tools)
    $drive = New-SubstDrive -TargetPath $SourceDir
    $sourceGlob = ($drive.TrimEnd("\") + "\*")
    Write-Host "[pack] zipping models_pack via 7z (subst $drive -> $SourceDir)..."
    & $sevenZipPath a -tzip -spf2 -mmt=on $tmpZip $sourceGlob | Out-Null
    # 7z exit codes:
    # - 0: no error
    # - 1: warning (e.g., some files could not be accessed)
    # - >=2: error/fatal
    if ($LASTEXITCODE -ge 2) {
      throw "[pack] 7z zip failed with exit code $LASTEXITCODE"
    }
    if ($LASTEXITCODE -eq 1) {
      Write-Host "[warn] 7z reported warnings (exit 1). Continuing to integrity test..."
    }
  } finally {
    Remove-SubstDrive -Drive $drive
  }

  Write-Host "[pack] verifying models_pack.zip..."
  & $sevenZipPath t $tmpZip | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "[pack] 7z test failed with exit code $LASTEXITCODE"
  }

  # Promote tmp zip to final destination (atomic-ish on same volume)
  try {
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Move-Item -Force -LiteralPath $tmpZip -Destination $ZipPath
  } catch {
    throw ("[pack] failed to move models_pack zip into place: {0}" -f $_.Exception.Message)
  }

  $sw.Stop()
  Write-Host "[pack] models_pack.zip done in $([int]$sw.Elapsed.TotalMinutes)m"
}

$modelsSrc = "D:\yizhigongfang-main\yizhigongfang-git\assets\models"
$modelsDst = "D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\models_pack"
if ($SkipModelsPack) {
  Write-Host "[pack] SkipModelsPack=true, skipping models_pack."
} elseif (-not (Test-Path $modelsSrc)) {
  Write-Host "[pack] models source not found, skipping models_pack."
} else {
  $zipPath = "D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\models_pack.zip"
  $ollamaModels = "D:\tools\ollama_models"
  $latestModels = Get-LatestWriteTime $modelsSrc
  $latestOllama = Get-LatestWriteTime $ollamaModels
  $latestSource = $latestModels
  if ($latestOllama -and ($latestSource -eq $null -or $latestOllama -gt $latestSource)) {
    $latestSource = $latestOllama
  }
  $shouldBuild = $true
  if (-not $ForceModelsPack -and (Test-Path $zipPath) -and $latestSource) {
    $zipTime = (Get-Item $zipPath).LastWriteTime
    if ($latestSource -le $zipTime) {
      Write-Host "[pack] models_pack.zip is up to date, skipping."
      $shouldBuild = $false
    }
  }
  if ($shouldBuild) {
    Write-Host "[pack] preparing models_pack (separate from installer)..."
    New-Item -ItemType Directory -Force -Path $modelsDst | Out-Null
    $copyLog = "D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\models_pack_robocopy.log"
    robocopy $modelsSrc $modelsDst /MIR /XO /XN /XC /FFT /MT:16 /NFL /NDL /NJH /NJS /TEE /LOG+:$copyLog | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
      throw "[pack] robocopy models failed with exit code $rc"
    }
    if (Test-Path $ollamaModels) {
      Write-Host "[pack] adding ollama_models into models_pack..."
      robocopy $ollamaModels "$modelsDst\\ollama_models" /MIR /XO /XN /XC /FFT /MT:16 /NFL /NDL /NJH /NJS /TEE /LOG+:$copyLog | Out-Null
      $rc = $LASTEXITCODE
      if ($rc -ge 8) {
        throw "[pack] robocopy ollama_models failed with exit code $rc"
      }
    } else {
      Write-Host "[pack] ollama_models not found, skipping."
    }
    # HuggingFace caches may contain reparse points (symlinks) that are unreadable on some Windows setups,
    # resulting in empty 0-byte files and 7z warnings. Materialize them into real files before zipping.
    $whisperx = Join-Path $modelsDst "whisperx"
    if (Test-Path $whisperx) {
      Write-Host "[pack] materializing whisperx reparse-point files..."
      $n = Materialize-ReparseFiles -RootDir $whisperx
      Write-Host ("[pack] materialized {0} files." -f $n)
      $stillBroken = Get-ChildItem -Path $whisperx -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -and ($_.Length -eq 0) } |
        Select-Object -First 1
      if ($stillBroken) {
        Write-Host "[warn] whisperx contains unresolved reparse-point files (0 bytes). Skipping models_pack.zip build."
        Write-Host "       If you need models_pack.zip, rebuild whisperx cache without symlinks (Developer Mode) and re-run with -ForceModelsPack."
        $shouldBuild = $false
      }
    }
    if ($shouldBuild) {
      # Use a short temp path to avoid MAX_PATH issues, then zip with a stable tool
      New-ModelsZip -SourceDir $modelsDst -ZipPath $zipPath
    }
  }
}

Write-Host "[pack] done."

# Copy installer to a timestamped filename to avoid "output file is locked" issues
# when users run the installer directly from dist_electron and then rebuild.
try {
  $releaseDir = Join-Path $distElectron "releases"
  New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
  $latest = Get-ChildItem -LiteralPath $distElectron -File -Filter "YizhiStudio-0.1.0-win-*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($latest) {
    $dstExe = Join-Path $releaseDir $latest.Name
    Copy-Item -Force -LiteralPath $latest.FullName -Destination $dstExe

    $block = ($latest.FullName + ".blockmap")
    if (Test-Path $block) {
      Copy-Item -Force -LiteralPath $block -Destination ($dstExe + ".blockmap")
    }
    Write-Host ("[pack] copied installer -> {0}" -f $dstExe)
  } else {
    Write-Host "[warn] installer exe not found for copying."
  }
} catch {
  Write-Host ("[warn] failed to copy installer to timestamped file: {0}" -f $_.Exception.Message)
}
exit 0
