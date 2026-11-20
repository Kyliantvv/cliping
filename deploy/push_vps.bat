@echo off
rem Depuis le PC : construit l'interface et l'envoie sur le VPS (Node n'est pas installe sur le VPS),
rem avec le modele YAMNet (detection des rires/cris pendant les lives). Puis redemarre l'appli.
cd /d "%~dp0\.."
set VPS=ubuntu@213.32.16.246
set PORT=2222
pushd web
if not exist "node_modules" call npm install --no-audit --no-fund
call npm run build || exit /b 1
popd
ssh -p %PORT% %VPS% "rm -rf cliping/web/out && mkdir -p cliping/web cliping/models"
scp -P %PORT% -r web\out %VPS%:cliping/web/out || exit /b 1
if exist "models\yamnet.onnx" scp -P %PORT% models\yamnet.onnx models\yamnet_class_map.csv %VPS%:cliping/models/
ssh -p %PORT% %VPS% "cd cliping && git pull -q && sudo systemctl restart clips-app clips-watcher && echo OK"
