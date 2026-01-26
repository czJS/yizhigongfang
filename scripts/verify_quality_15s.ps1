param(
  [string]$BackendBase = "http://127.0.0.1:5175",
  [string]$Model = "qwen2.5:1.5b",
  [int]$ChunkSize = 1
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$uploads = Join-Path $repoRoot "outputs\\uploads"
if (!(Test-Path $uploads)) {
  New-Item -ItemType Directory -Force -Path $uploads | Out-Null
}

$clip = Join-Path $repoRoot "测试视频_15s.mp4"
if (!(Test-Path $clip)) {
  $src = (Get-ChildItem -LiteralPath $repoRoot | Where-Object { $_.Name -like "*.mp4" } | Select-Object -First 1).FullName
  if (-not $src) { throw "No mp4 found in repo root." }
  $tmp = Join-Path $repoRoot "tmp_test_video.mp4"
  Copy-Item -Force $src $tmp
  docker cp "$tmp" yizhi-backend-1:/tmp/test_src.mp4
  docker exec yizhi-backend-1 /app/bin/ffmpeg -y -ss 0 -t 15 -i /tmp/test_src.mp4 -c copy /tmp/test_15s.mp4
  docker cp yizhi-backend-1:/tmp/test_15s.mp4 "$clip"
  Remove-Item $tmp
}

$dest = Join-Path $uploads "15s.mp4"
Copy-Item -Force $clip $dest

try {
  Invoke-RestMethod -Uri "$BackendBase/api/health" -Method Get | Out-Null
} catch {
  throw "Backend not reachable at $BackendBase. Start backend first."
}

$payload = @{
  video = "/app/outputs/uploads/15s.mp4"
  mode = "quality"
  preset = "quality"
  params = @{
    llm_model = $Model
    llm_chunk_size = $ChunkSize
  }
}

$task = Invoke-RestMethod -Uri "$BackendBase/api/tasks/start" -Method Post -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 6)
$taskId = $task.task_id
if (-not $taskId) { throw "Failed to start task." }

Write-Host "Started task: $taskId"

while ($true) {
  Start-Sleep -Seconds 5
  $st = Invoke-RestMethod -Uri "$BackendBase/api/tasks/$taskId/status" -Method Get
  Write-Host ("[{0}] {1} {2}%" -f $st.state, ($st.stage_name | ForEach-Object { $_ }), $st.progress)
  if ($st.state -in @("completed", "failed", "cancelled", "paused")) { break }
}

$report = $null
try {
  $report = Invoke-RestMethod -Uri "$BackendBase/api/tasks/$taskId/quality_report" -Method Get
} catch {
  $report = $null
}

if ($st.state -ne "completed") {
  Write-Host "Task failed. State: $($st.state)"
  if ($report -and $report.errors) {
    Write-Host ("Quality errors: " + ($report.errors -join "; "))
  }
  exit 1
}

Write-Host "Task completed."
exit 0
