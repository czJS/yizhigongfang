param(
  [string]$RepoRoot = "D:\yizhigongfang-main\yizhigongfang-git",
  [string]$OllamaSrc = "D:\tools\ollama",
  [switch]$Force = $false
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

function Get-SevenZipPath {
  $cmd = Get-Command "7z" -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $candidates = @(
    "D:\7-Zip\7z.exe",
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe"
  )
  foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
  return $null
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
  throw "[ollama_pack] no free drive letter available for subst."
}

function Remove-SubstDrive([string]$Drive) {
  if (-not $Drive) { return }
  & cmd /c "subst $Drive /D" | Out-Null
}

$repo = (Resolve-Path $RepoRoot).Path
if (-not (Test-Path $OllamaSrc)) { throw "[ollama_pack] OllamaSrc not found: $OllamaSrc" }
if (-not (Test-Path (Join-Path $OllamaSrc "ollama.exe"))) { throw "[ollama_pack] ollama.exe not found in: $OllamaSrc" }

$distElectron = Join-Path $repo "frontend\dist_electron"
Ensure-Dir $distElectron

$zipPath = Join-Path $distElectron "ollama_pack.zip"
$keepDir = "D:\temp\yizhistudio\build_keep"
Ensure-Dir $keepDir

$latestSrc = Get-LatestWriteTime $OllamaSrc
if (-not $Force -and (Test-Path $zipPath) -and $latestSrc) {
  $zipTime = (Get-Item $zipPath).LastWriteTime
  if ($latestSrc -le $zipTime) {
    Write-Host "[ollama_pack] ollama_pack.zip is up to date, skipping."
    exit 0
  }
}

$sevenZipPath = Get-SevenZipPath
if (-not $sevenZipPath) { throw "[ollama_pack] 7z not found. Please install 7-Zip or set 7z.exe in PATH." }

# Build a stable layout: zip contains a single top-level folder "ollama\"
$tmpRoot = "D:\temp\yizhistudio\ollama_pack_tmp"
Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue
Ensure-Dir (Join-Path $tmpRoot "ollama")

Write-Host "[ollama_pack] copying ollama files..."
robocopy $OllamaSrc (Join-Path $tmpRoot "ollama") /E /NFL /NDL /NJH /NJS /NP | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) { throw "[ollama_pack] robocopy failed with exit code $rc" }

if (Test-Path $zipPath) {
  $bak = Join-Path $keepDir ("ollama_pack_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".zip")
  Copy-Item -Force -LiteralPath $zipPath -Destination $bak
  Write-Host ("[ollama_pack] preserved previous zip -> {0}" -f $bak)
}

$tmpZip = ($zipPath + ".tmp")
if (Test-Path $tmpZip) { Remove-Item -Force $tmpZip }

$drive = $null
try {
  $drive = New-SubstDrive -TargetPath $tmpRoot
  $sourceGlob = ($drive.TrimEnd("\") + "\*")
  Write-Host "[ollama_pack] zipping via 7z..."
  & $sevenZipPath a -tzip -spf2 -mmt=on $tmpZip $sourceGlob | Out-Null
  if ($LASTEXITCODE -ge 2) { throw "[ollama_pack] 7z zip failed with exit code $LASTEXITCODE" }
} finally {
  Remove-SubstDrive -Drive $drive
}

Write-Host "[ollama_pack] verifying zip..."
& $sevenZipPath t $tmpZip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "[ollama_pack] 7z test failed with exit code $LASTEXITCODE" }

if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Move-Item -Force -LiteralPath $tmpZip -Destination $zipPath
Write-Host ("[ollama_pack] OK -> {0}" -f $zipPath)

