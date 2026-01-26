param(
  [string]$RepoRoot = "D:\yizhigongfang-main\yizhigongfang-git",
  [string]$VenvPath = "D:\tools\venvs\yizhi-quality"
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

$repo = (Resolve-Path $RepoRoot).Path
$dTemp = "D:\temp\yizhistudio\quality_worker"
$pipCache = "D:\cache\pip"
Ensure-Dir $dTemp
Ensure-Dir $pipCache

$env:TEMP = $dTemp
$env:TMP = $dTemp
$env:PIP_CACHE_DIR = $pipCache

Write-Host "[quality_worker] repo=$repo"
Write-Host "[quality_worker] venv=$VenvPath"
Write-Host "[quality_worker] TEMP=$env:TEMP"
Write-Host "[quality_worker] PIP_CACHE_DIR=$env:PIP_CACHE_DIR"

# Create venv if missing (use system python)
if (-not (Test-Path $VenvPath)) {
  Write-Host "[quality_worker] creating venv..."
  python -m venv $VenvPath
}

$py = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $py)) { throw "[quality_worker] python not found in venv: $py" }

Write-Host "[quality_worker] upgrading pip + installing pyinstaller..."
& $py -m pip install -U pip pyinstaller

# Clean up incompatible leftovers from previous runs (gruut pulls numpy<2.0 and conflicts with WhisperX).
& $py -m pip uninstall -y gruut gruut-ipa gruut_lang_en gruut_lang_de gruut_lang_es gruut_lang_fr | Out-Null

$req = Join-Path $repo "backend\\requirements_quality.txt"
if (-not (Test-Path $req)) { throw "[quality_worker] requirements not found: $req" }

Write-Host "[quality_worker] installing quality requirements (this may take a while)..."
& $py -m pip install -r $req

# Coqui TTS (pip package name: TTS) conflicts with WhisperX's pandas requirement in pip resolver:
# - whisperx requires pandas>=2.2.3,<2.3.0
# - TTS declares pandas>=1.4,<2.0
# In practice, TTS works fine without strictly enforcing the pandas upper bound for our usage.
# Install TTS without dependency resolution, and install its common runtime deps explicitly (excluding pandas).
Write-Host "[quality_worker] installing Coqui TTS (resolver workaround)..."
& $py -m pip install --no-deps "TTS==0.21.3"
& $py -m pip install cython librosa numba inflect anyascii flask pysbd umap-learn
# Provide Intel TBB runtime for numba on Windows (avoids missing tbb12.dll at runtime).
& $py -m pip install tbb
# Coqui TTS may import `gruut` for text normalization/phonemization. Installing `gruut` normally
# would downgrade numpy (<2.0). Install it WITHOUT deps and add its lightweight deps explicitly.
& $py -m pip install --no-deps "gruut==2.2.3" "gruut-ipa==0.13.0"
# Language data package(s) for gruut (do NOT pull deps to avoid resolver surprises).
& $py -m pip install --no-deps gruut_lang_en
& $py -m pip install babel dateparser jsonlines python-crfsuite tzlocal
# Install additional TTS runtime deps that are normally pulled by pip.
# We intentionally avoid installing pandas<2.0 to keep WhisperX compatible.
# NOTE: We also avoid installing `gruut*` here because it pulls numpy<2.0, which conflicts with WhisperX.
& $py -m pip install bangla bnnumerizer bnunicodenormalizer coqpit encodec g2pkk hangul_romanize jamo jieba num2words pypinyin trainer unidecode

$spec = Join-Path $repo "quality_worker.spec"
if (-not (Test-Path $spec)) { throw "[quality_worker] spec not found: $spec" }

# NOTE:
# PyInstaller modifies the output EXE's Windows resources (manifest/icon) in-place.
# Windows Defender / file indexers can temporarily lock freshly-created EXEs, causing:
#   RuntimeError: Execution of 'remove_all_resources' failed - no more attempts left!
# To reduce interference, build into our dedicated TEMP folder (already set above),
# then copy the final exe into repo\dist at the end.
$distDirFinal = Join-Path $repo "dist"
$distDirBuild = Join-Path $dTemp "dist"
$workDir = Join-Path $dTemp "build_pyinstaller_quality"
Ensure-Dir $distDirFinal
Ensure-Dir $distDirBuild
Ensure-Dir $workDir

Write-Host "[quality_worker] building quality_worker.exe ..."
Push-Location $repo
try {
  # Remove previous build output in temp build dir
  $oldExeBuild = Join-Path $distDirBuild "quality_worker.exe"
  if (Test-Path $oldExeBuild) { Remove-Item -Force -LiteralPath $oldExeBuild }

  & $py -m PyInstaller --noconfirm --clean --distpath $distDirBuild --workpath $workDir $spec
  if ($LASTEXITCODE -ne 0) { throw "[quality_worker] pyinstaller failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}

$exeBuild = Join-Path $distDirBuild "quality_worker.exe"
if (-not (Test-Path $exeBuild)) { throw "[quality_worker] build finished but exe missing: $exeBuild" }

# Copy into repo\dist (overwrite)
$exeFinal = Join-Path $distDirFinal "quality_worker.exe"
Copy-Item -LiteralPath $exeBuild -Destination $exeFinal -Force

Write-Host "[quality_worker] OK -> $exeFinal"

