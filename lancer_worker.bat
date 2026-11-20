@echo off
rem Worker du PC : fait les transcriptions Whisper et les rendus demandes par l'appli du VPS (GPU).
rem Lance automatiquement a l'ouverture de session (voir README, « Installer le worker »).
cd /d "%~dp0"
title Clips GTA VI - worker PC
:loop
python tools\pc_worker.py
echo Worker arrete, redemarrage dans 30 s (Ctrl+C pour quitter)...
timeout /t 30 /nobreak >nul
goto loop
