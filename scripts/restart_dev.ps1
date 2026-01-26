$ErrorActionPreference = "Stop"

function Stop-ByPort([int]$Port) {
  try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $pids = @($conns | Select-Object -ExpandProperty OwningProcess -ErrorAction SilentlyContinue) | Where-Object { $_ } | Select-Object -Unique
    foreach ($pid in $pids) {
      try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
    }
    return
  } catch {
    # Fall back to netstat parsing below
  }

  try {
    $lines = & cmd /c "netstat -ano | findstr :$Port | findstr LISTENING"
    foreach ($ln in ($lines | Where-Object { $_ })) {
      $parts = ($ln -split "\s+") | Where-Object { $_ }
      $pid = $parts[-1]
      if ($pid -match "^\d+$") {
        try { Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue } catch {}
      }
    }
  } catch {}
}

function Get-UserDataRoot() {
  if ($env:YGF_USER_DATA) { return $env:YGF_USER_DATA }
  if (Test-Path "D:\") { return "D:\dubbing-gui" }
  return (Join-Path $env:APPDATA "dubbing-gui")
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontend = Join-Path $repoRoot "frontend"
$userData = Get-UserDataRoot
$logsDir = Join-Path $userData "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Write-Host "[restart] stopping ports 5173/5174/5175..."
Stop-ByPort 5173
Stop-ByPort 5174
Stop-ByPort 5175

Write-Host "[restart] starting backend (python -m backend.app)..."
$backendOut = Join-Path $logsDir "dev_backend.out.log"
$backendErr = Join-Path $logsDir "dev_backend.err.log"
Remove-Item -Force -ErrorAction SilentlyContinue $backendOut
Remove-Item -Force -ErrorAction SilentlyContinue $backendErr
$backend = Start-Process -FilePath "python" -ArgumentList "-m backend.app" -WorkingDirectory $repoRoot -PassThru `
  -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -WindowStyle Hidden

Write-Host "[restart] starting frontend dev (npm run dev)..."
$devOut = Join-Path $logsDir "dev_frontend.out.log"
$devErr = Join-Path $logsDir "dev_frontend.err.log"
Remove-Item -Force -ErrorAction SilentlyContinue $devOut
Remove-Item -Force -ErrorAction SilentlyContinue $devErr
$dev = Start-Process -FilePath "cmd" -ArgumentList "/c", "npm run dev" -WorkingDirectory $frontend -PassThru `
  -RedirectStandardOutput $devOut -RedirectStandardError $devErr -WindowStyle Hidden

Write-Host ("[restart] started backend pid={0}, dev pid={1}" -f $backend.Id, $dev.Id)
Write-Host ("[restart] logs: {0}" -f $logsDir)
