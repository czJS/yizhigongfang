@echo off
setlocal

set "ROOT=D:\dubbing-gui\models"
set "SRC=%ROOT%\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\models_pack"

if not exist "%SRC%" (
  echo source not found: %SRC%
  exit /b 1
)

for %%D in (whisperx tts ollama_models) do (
  if exist "%SRC%\%%D" (
    if exist "%ROOT%\%%D" rmdir /s /q "%ROOT%\%%D"
    move "%SRC%\%%D" "%ROOT%\" >nul
    echo moved %%D
  ) else (
    echo missing %%D
  )
)

echo done.
exit /b 0
