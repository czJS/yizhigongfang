$ErrorActionPreference = "Stop"

$scriptPath = "D:\yizhigongfang-main\yizhigongfang-git\scripts\build_installer.ps1"
$logPathOut = "D:\yizhigongfang-main\yizhigongfang-git\scripts\build_installer_run.out.log"
$logPathErr = "D:\yizhigongfang-main\yizhigongfang-git\scripts\build_installer_run.err.log"

Remove-Item -Force -ErrorAction SilentlyContinue $logPathOut
Remove-Item -Force -ErrorAction SilentlyContinue $logPathErr

Write-Host "[runner] starting build_installer.ps1..."
$proc = Start-Process -FilePath "powershell" `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
  -NoNewWindow -PassThru `
  -RedirectStandardOutput $logPathOut `
  -RedirectStandardError $logPathErr

$start = Get-Date
while (-not $proc.HasExited) {
  Start-Sleep -Seconds 180
  $elapsed = New-TimeSpan -Start $start -End (Get-Date)
  Write-Host ("[progress] still running, elapsed {0:hh\:mm\:ss}" -f $elapsed)
  if (Test-Path $logPathOut) {
    Write-Host "[progress] stdout (tail 20)"
    Get-Content -Path $logPathOut -Tail 20
  }
  if (Test-Path $logPathErr) {
    Write-Host "[progress] stderr (tail 20)"
    Get-Content -Path $logPathErr -Tail 20
  }
}

Write-Host ("[progress] exited with code {0}" -f $proc.ExitCode)
if (Test-Path $logPathOut) {
  Write-Host "[progress] stdout (tail 200)"
  Get-Content -Path $logPathOut -Tail 200
}
if (Test-Path $logPathErr) {
  Write-Host "[progress] stderr (tail 200)"
  Get-Content -Path $logPathErr -Tail 200
}
