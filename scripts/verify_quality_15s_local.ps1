param(
  [string]$BackendBase = "http://127.0.0.1:5175",
  [string]$LlmEndpoint = "http://127.0.0.1:11434/v1",
  [string]$Model = "qwen2.5:7b",
  [int]$ChunkSize = 1,
  [int]$WaitLlmSeconds = 120,
  [switch]$SkipLlmCheck
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

# Generate a tiny 15s test video using the project's ffmpeg.exe (no external assets needed).
$ffmpeg = Join-Path $repoRoot "bin\\ffmpeg.exe"
if (!(Test-Path $ffmpeg)) { throw "ffmpeg.exe not found at $ffmpeg" }

# Use ASCII filename to avoid console/codepage issues on some Windows setups.
$clip = Join-Path $repoRoot "test_15s.mp4"
if (!(Test-Path $clip)) {
  Write-Host "[test] generating 15s test clip..."
  & $ffmpeg -y -f lavfi -i "testsrc=size=1280x720:rate=30" -f lavfi -i "sine=frequency=440:sample_rate=48000" `
    -t 15 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest $clip | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed to generate clip (exit $LASTEXITCODE)" }
}

# Ensure backend is reachable
try {
  Invoke-RestMethod -Uri "$BackendBase/api/health" -Method Get | Out-Null
} catch {
  throw "Backend not reachable at $BackendBase. Start backend first."
}

function Resolve-LlmModelsUrl([string]$Endpoint) {
  $e = ""
  if ($null -ne $Endpoint) { $e = [string]$Endpoint }
  $e = $e.Trim()
  if (-not $e) { return "" }
  $e = $e.TrimEnd("/")
  if ($e -match "/models$") { return $e }
  if ($e -match "/v1$") { return "$e/models" }
  # If user provided base without /v1, assume OpenAI-compatible path under /v1
  return "$e/v1/models"
}

if (-not $SkipLlmCheck) {
  $modelsUrl = Resolve-LlmModelsUrl $LlmEndpoint
  if (-not $modelsUrl) { throw "Invalid LlmEndpoint: '$LlmEndpoint'" }

  Write-Host "[test] checking LLM endpoint (manual Ollama load flow)..."
  Write-Host "  endpoint=$LlmEndpoint"
  Write-Host "  probe=$modelsUrl"

  $deadline = (Get-Date).AddSeconds([math]::Max(0, [int]$WaitLlmSeconds))
  while ($true) {
    try {
      $resp = Invoke-RestMethod -Uri $modelsUrl -Method Get -TimeoutSec 2
      # OpenAI-style: { data: [{id:...}, ...] }
      if ($resp -and $resp.data) {
        Write-Host "[test] LLM endpoint is reachable."
        break
      }
      # If it responds but empty, still consider it up.
      Write-Host "[test] LLM endpoint responded (no model list). Continuing."
      break
    } catch {
      if ((Get-Date) -ge $deadline) {
        throw @"
LLM not reachable yet.
This project uses a standalone app flow where Ollama is loaded/started manually.
Please open the app and start the Ollama service + load the model, then retry (or re-run this script).

Quick probe:
  curl.exe $modelsUrl

If you want to test the pipeline WITHOUT LLM for now, re-run with:
  -SkipLlmCheck
"@
      }
      Start-Sleep -Seconds 2
    }
  }
}

$payload = @{
  video = $clip
  mode = "quality"
  preset = "quality"
  params = @{
    llm_endpoint = $LlmEndpoint
    llm_model = $Model
    llm_chunk_size = $ChunkSize
  }
}

Write-Host "[test] starting task..."
$task = Invoke-RestMethod -Uri "$BackendBase/api/tasks/start" -Method Post -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 6)
$taskId = $task.task_id
if (-not $taskId) { throw "Failed to start task." }

Write-Host "[test] started task: $taskId"

while ($true) {
  Start-Sleep -Seconds 5
  $st = Invoke-RestMethod -Uri "$BackendBase/api/tasks/$taskId/status" -Method Get
  $stage = ($st.stage_name | ForEach-Object { $_ })
  Write-Host ("[test] [{0}] {1} {2}%" -f $st.state, $stage, $st.progress)
  if ($st.state -in @("completed", "failed", "cancelled", "paused")) { break }
}

if ($st.state -ne "completed") {
  Write-Host "[test] task failed."
  try {
    $log = Invoke-RestMethod -Uri "$BackendBase/api/tasks/$taskId/log?offset=0" -Method Get
    if ($log.content) {
      $tail = ($log.content -split "`n") | Select-Object -Last 120
      Write-Host "--- log tail ---"
      $tail | ForEach-Object { Write-Host $_ }
    }
  } catch {}
  exit 1
}

Write-Host "[test] task completed."
exit 0

