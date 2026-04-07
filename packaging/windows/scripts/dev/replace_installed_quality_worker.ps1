param(
  [string]$SrcExe = "D:\yizhigongfang-main\yizhigongfang-git\dist\quality_worker.exe",
  [string]$DriveRoot = "D:\"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SrcExe)) {
  throw "Source exe not found: $SrcExe"
}

Write-Host "[replace] searching installed backend_server.exe under $DriveRoot ..."
$backend = Get-ChildItem -LiteralPath $DriveRoot -Recurse -File -Filter "backend_server.exe" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "\\YizhiStudio\\resources\\backend_server\.exe$" } |
  Select-Object -First 1

if (-not $backend) {
  throw "Installed backend_server.exe not found under $DriveRoot"
}

$destDir = $backend.DirectoryName
$destExe = Join-Path $destDir "quality_worker.exe"

Write-Host "[replace] copying:"
Write-Host "  src=$SrcExe"
Write-Host "  dst=$destExe"

Copy-Item -LiteralPath $SrcExe -Destination $destExe -Force

Write-Host "[replace] OK"
Write-Host $destDir

