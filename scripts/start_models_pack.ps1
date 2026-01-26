$ErrorActionPreference = "Stop"

$scriptPath = "D:\yizhigongfang-main\yizhigongfang-git\scripts\build_installer.ps1"
$logPathOut = "D:\yizhigongfang-main\yizhigongfang-git\scripts\models_pack_run.out.log"
$logPathErr = "D:\yizhigongfang-main\yizhigongfang-git\scripts\models_pack_run.err.log"

Remove-Item -Force -ErrorAction SilentlyContinue $logPathOut
Remove-Item -Force -ErrorAction SilentlyContinue $logPathErr

Write-Host "[models_pack] starting build_installer.ps1 (SkipDist, ForceModelsPack)..."
$proc = Start-Process -FilePath "powershell" `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -SkipDist -ForceModelsPack" `
  -NoNewWindow -PassThru `
  -RedirectStandardOutput $logPathOut `
  -RedirectStandardError $logPathErr

Write-Host ("[models_pack] started pid {0}" -f $proc.Id)
