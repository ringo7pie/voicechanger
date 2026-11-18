@echo off
chcp 65001 > nul
rem VoiceChanger の GUI を開く。pythonw で起動するので、この窓はすぐ閉じる。
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv がありません。先に次を実行してください: pwsh -NoProfile -File tools\install_engine_deps.ps1
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m voicechanger.gui %*
