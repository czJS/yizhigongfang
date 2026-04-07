param(
  [switch]$Force = $false,
  [switch]$IncludeTempNsis = $true,
  [switch]$IncludeBuildCaches = $true,
  [int]$MinAgeMinutes = 10
)

$ErrorActionPreference = "Stop"

function Get-SizeBytes([string]$Path) {
  if (-not (Test-Path $Path)) { return 0 }
  $sum = 0
  try {
    Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object { $sum += $_.Length }
  } catch {}
  return $sum
}

function Pretty([long]$Bytes) {
  if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
  if ($Bytes -ge 1MB) { return ("{0:N1} MB" -f ($Bytes / 1MB)) }
  if ($Bytes -ge 1KB) { return ("{0:N0} KB" -f ($Bytes / 1KB)) }
  return ("{0} B" -f $Bytes)
}

function Get-DirSizeBytes([string]$Path) {
  if (-not (Test-Path $Path)) { return 0 }
  try {
    return (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
  } catch {
    return 0
  }
}

$now = Get-Date

$targets = @(
  (Join-Path $env:APPDATA "dubbing-gui"),
  (Join-Path $env:LOCALAPPDATA "dubbing-gui"),
  (Join-Path $env:APPDATA "YizhiStudio"),
  (Join-Path $env:LOCALAPPDATA "YizhiStudio")
) | Select-Object -Unique

Write-Host "[clean] candidates on C: (user profile):"
foreach ($t in $targets) {
  $size = Get-SizeBytes $t
  if (Test-Path $t) {
    Write-Host ("  - {0}  ({1})" -f $t, (Pretty $size))
  } else {
    Write-Host ("  - {0}  (missing)" -f $t)
  }
}

if ($IncludeBuildCaches) {
  Write-Host ""
  Write-Host "[clean] candidates on C: (build caches):"
  $cacheTargets = @(
    (Join-Path $env:LOCALAPPDATA "electron-builder\Cache"),
    (Join-Path $env:LOCALAPPDATA "electron\Cache"),
    (Join-Path $env:LOCALAPPDATA "npm-cache"),
    (Join-Path $env:LOCALAPPDATA "pyinstaller")
  ) | Select-Object -Unique
  foreach ($t in $cacheTargets) {
    if (Test-Path $t) {
      $size = Get-DirSizeBytes $t
      Write-Host ("  - {0}  ({1})" -f $t, (Pretty $size))
    } else {
      Write-Host ("  - {0}  (missing)" -f $t)
    }
  }
}

if ($IncludeTempNsis) {
  Write-Host ""
  Write-Host ("[clean] candidates on C: (installer temp under TEMP, ns*.tmp, older than {0} min):" -f $MinAgeMinutes)
  $tempRoot = $env:TEMP
  $nsisTemps = @()
  try {
    if (Test-Path $tempRoot) {
      $nsisTemps = Get-ChildItem -LiteralPath $tempRoot -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^ns.*\.tmp$' } |
        Where-Object { ($now - $_.LastWriteTime).TotalMinutes -ge $MinAgeMinutes } |
        Sort-Object LastWriteTime -Descending
    }
  } catch { $nsisTemps = @() }

  if ($nsisTemps.Count -eq 0) {
    Write-Host "  - (none)"
  } else {
    foreach ($d in ($nsisTemps | Select-Object -First 30)) {
      $size = Get-DirSizeBytes $d.FullName
      Write-Host ("  - {0}  ({1})  lastWrite={2}" -f $d.FullName, (Pretty $size), $d.LastWriteTime)
    }
    if ($nsisTemps.Count -gt 30) {
      Write-Host ("  ... and {0} more" -f ($nsisTemps.Count - 30))
    }
  }
}

if (-not $Force) {
  Write-Host ""
  Write-Host "[clean] dry-run only. Re-run with -Force to delete the existing folders above."
  Write-Host "        tips: you can disable parts via -IncludeTempNsis:\$false or -IncludeBuildCaches:\$false"
  exit 0
}

Write-Host ""
Write-Host "[clean] deleting..."
foreach ($t in $targets) {
  if (Test-Path $t) {
    try {
      Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction Stop
      Write-Host ("  deleted: {0}" -f $t)
    } catch {
      Write-Host ("  failed:  {0} ({1})" -f $t, $_.Exception.Message)
    }
  }
}

if ($IncludeBuildCaches) {
  $cacheTargets = @(
    (Join-Path $env:LOCALAPPDATA "electron-builder\Cache"),
    (Join-Path $env:LOCALAPPDATA "electron\Cache"),
    (Join-Path $env:LOCALAPPDATA "npm-cache"),
    (Join-Path $env:LOCALAPPDATA "pyinstaller")
  ) | Select-Object -Unique
  foreach ($t in $cacheTargets) {
    if (Test-Path $t) {
      try {
        Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction Stop
        Write-Host ("  deleted: {0}" -f $t)
      } catch {
        Write-Host ("  failed:  {0} ({1})" -f $t, $_.Exception.Message)
      }
    }
  }
}

if ($IncludeTempNsis) {
  $tempRoot = $env:TEMP
  try {
    if (Test-Path $tempRoot) {
      $nsisTemps = Get-ChildItem -LiteralPath $tempRoot -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^ns.*\.tmp$' } |
        Where-Object { ($now - $_.LastWriteTime).TotalMinutes -ge $MinAgeMinutes }
      foreach ($d in $nsisTemps) {
        try {
          Remove-Item -LiteralPath $d.FullName -Recurse -Force -ErrorAction Stop
          Write-Host ("  deleted: {0}" -f $d.FullName)
        } catch {
          Write-Host ("  failed:  {0} ({1})" -f $d.FullName, $_.Exception.Message)
        }
      }
    }
  } catch {}
}

Write-Host "[clean] done."

