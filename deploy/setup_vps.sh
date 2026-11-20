#!/usr/bin/env bash
# Installation / mise a jour sur le VPS (Ubuntu). A lancer sur le VPS depuis /home/ubuntu/cliping :
#   bash deploy/setup_vps.sh
# Puis, depuis le PC : deploy/push_vps.bat (interface web + modele YAMNet), et une seule fois :
#   .venv/bin/python tools/app.py --set-password
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get install -y -q ffmpeg python3-venv >/dev/null
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q numpy onnxruntime streamlink yt-dlp

mkdir -p data input work output models rec

sudo cp deploy/clips-app.service deploy/clips-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clips-app clips-watcher
sudo systemctl restart clips-app clips-watcher

sudo cp deploy/clips.caddy /etc/caddy/sites/clips.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
sudo systemctl reload caddy

systemctl --no-pager --lines=0 status clips-app clips-watcher | grep -E "●|Active"
[ -f data/auth.json ] || echo ">>> Choisir le mot de passe : .venv/bin/python tools/app.py --set-password"
