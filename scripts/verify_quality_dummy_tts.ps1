param(
  [string]$VideoRel = "outputs/uploads/15s.mp4",
  [string]$OutputName = "dummy_tts_check"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$outDir = Join-Path $repoRoot ("outputs\\" + $OutputName)
if (!(Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }

$videoPath = Join-Path $repoRoot $VideoRel
if (!(Test-Path $videoPath)) { throw "Video not found: $videoPath" }

# Minimal one-line segments to skip ASR/MT and go straight to TTS/mux.
$audioJson = Join-Path $outDir "audio.json"
$chsSrt = Join-Path $outDir "chs.srt"
$engOverride = Join-Path $outDir "eng_override.srt"

$zh = [System.Text.RegularExpressions.Regex]::Unescape('\u4f60\u597d\uff0c\u8fd9\u662f\u4e00\u53e5\u6d4b\u8bd5\u3002')
$seg = @(
  @{
    start = 0.0;
    end = 2.0;
    text = $zh;
    translation = 'Hello, this is a test.';
  }
)
$json = $seg | ConvertTo-Json -Depth 4
if (-not $json.TrimStart().StartsWith("[")) {
  $json = "[`n$json`n]"
}
[System.IO.File]::WriteAllText($audioJson, $json, (New-Object System.Text.UTF8Encoding($false)))

$chsLines = @(
  "1",
  "00:00:00,000 --> 00:00:02,000",
  $zh,
  ""
)
$chsLines | Set-Content -Path $chsSrt -Encoding UTF8

$engLines = @(
  "1",
  "00:00:00,000 --> 00:00:02,000",
  "Hello, this is a test.",
  ""
)
$engLines | Set-Content -Path $engOverride -Encoding UTF8

$containerOut = "/app/outputs/$OutputName"
$containerVideo = "/app/$VideoRel".Replace("\\", "/")
$cmd = @(
  "python",
  "/app/scripts/quality_pipeline.py",
  "--video", $containerVideo,
  "--output-dir", $containerOut,
  "--resume-from", "tts",
  "--eng-override-srt", "$containerOut/eng_override.srt"
) -join " "

Write-Host "Running: $cmd"
docker exec yizhi-backend-1 bash -lc $cmd

$outVideo = Join-Path $outDir "output_en_sub.mp4"
if (Test-Path $outVideo) {
  Write-Host "OK: output_en_sub.mp4 generated"
  exit 0
}

Write-Host "FAILED: output_en_sub.mp4 missing. Check run.log in $outDir"
exit 1
