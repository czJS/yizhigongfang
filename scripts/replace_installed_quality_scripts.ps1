param(
  [string]$RepoRoot = "D:\yizhigongfang-main\yizhigongfang-git",
  [string]$DriveRoot = "D:\"
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $RepoRoot).Path
$srcDir = Join-Path $repo "scripts"
$toCopy = @(
  "quality_pipeline.py",
  "asr_translate_tts.py"
)
foreach ($f in $toCopy) {
  $p = Join-Path $srcDir $f
  if (-not (Test-Path -LiteralPath $p)) { throw "source script not found: $p" }
}

Write-Host "[replace_scripts] searching installed backend_server.exe under $DriveRoot ..."
$backend = Get-ChildItem -LiteralPath $DriveRoot -Recurse -File -Filter "backend_server.exe" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "\\YizhiStudio\\resources\\backend_server\.exe$" } |
  Select-Object -First 1

if (-not $backend) {
  throw "Installed backend_server.exe not found under $DriveRoot"
}

$destDir = $backend.DirectoryName
$scriptsDir = Join-Path $destDir "scripts"
if (-not (Test-Path -LiteralPath $scriptsDir)) {
  throw "installed scripts dir not found: $scriptsDir"
}

$copied = @()
foreach ($f in $toCopy) {
  $src = Join-Path $srcDir $f
  $dst = Join-Path $scriptsDir $f

  Write-Host "[replace_scripts] copying:"
  Write-Host "  src=$src"
  Write-Host "  dst=$dst"

  Copy-Item -LiteralPath $src -Destination $dst -Force
  $copied += $dst
}

Write-Host "[replace_scripts] OK"
$copied | ForEach-Object { Write-Host $_ }

