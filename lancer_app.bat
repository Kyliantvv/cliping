@echo off
cd /d "%~dp0"
rem Premiere fois (ou apres une mise a jour de l'interface) : construit l'interface Next.js dans web\out
if not exist "web\out\index.html" (
  echo Construction de l'interface...
  pushd web
  if not exist "node_modules" call npm install --no-audit --no-fund
  call npm run build
  popd
)
python tools\app.py
pause
