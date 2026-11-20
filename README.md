# Clipping TikTok GTA VI

Tout tourne en local : ffmpeg (transcription Whisper sur GPU), Python, YAMNet (détection de sons), UltraFace (visages).
Règles de montage : voir [PREFERENCES.md](PREFERENCES.md).

## Dossiers
- `input/` : vidéos/streams sources (.mp4)
- `work/<video>/` : transcription, signaux, planches d'aperçu
- `clips/<video>.json` : liste des clips (début, fin, hook, zooms, coupes)
- `output/<video>/` : clips TikTok finis 1080x1920 + `legendes.txt` (un dossier par vidéo source)
- `models/` : Whisper, YAMNet

## Étapes
1. Mettre la vidéo dans `input/` (nom simple, sans emoji).
2. Analyse :
   ```
   python tools/analyze.py input/ma_video.mp4
   python tools/analyze.py input/mon_stream.mp4 --chat https://www.twitch.tv/videos/123456789
   ```
   Fichiers produits dans `work/ma_video/` :
   - `transcript.txt` : transcription complète
   - `candidates.txt` : meilleurs moments, avec la raison (sons, chat, montage rapide, mots-clés)
   - `events.txt` : rires, cris, explosions, tirs, sirènes, écrans noirs, avec le texte autour
3. Écrire `clips/ma_video.json` (voir l'en-tête de `tools/render.py`).
4. Vérification visuelle (1 image toutes les 2 s, avec l'heure de la source) :
   ```
   python tools/preview.py clips/ma_video.json
   ```
   Planches dans `work/ma_video/preview/`. Corriger la config : `cuts` pour retirer un passage
   (écran noir, carton titre, spoiler), `zooms` sur les moments visuels forts.
5. Rendu :
   ```
   python tools/render.py clips/ma_video.json
   python tools/render.py clips/ma_video.json --only 03_crash
   ```
6. Vérification de la synchro sous-titres / voix (sur les mp4 rendus) :
   ```
   python tools/check_sync.py clips/ma_video.json
   ```
   Verdict OK / A VERIFIER par clip + image `work/_render/<video>/<clip>_sync.png` (gris = voix, couleurs = mots).

## Appli de gestion des clips
Double-clic sur `lancer_app.bat` (ou `python tools/app.py`) : ouvre http://127.0.0.1:8765 (accessible seulement depuis ce PC).
Interface en Next.js (`web/`, export statique dans `web/out` servi par `tools/app.py`) ; le serveur Python garde
toute la logique (rendus, corbeille, iCloud, imports). `lancer_app.bat` construit l'interface au premier lancement.
Après une modification de `web/` : `cd web && npm run build`. Pour développer : `npm run dev` (port 3000, appels
relayés vers le serveur Python, qui doit tourner).
- Tous les clips de `output/`, du plus récent au plus ancien (sections Aujourd'hui / Hier / date), filtre par
  streamer, aperçu au survol, pastille synchro (dernier `check_sync`).
- « Prochain à poster » : clip conseillé (ordre de `legendes.txt`) + bouton « Copier la légende » (légende + crédit).
- Suivi : À poster / Programmé (date) / Posté, lien TikTok, vues, likes, notes. Stocké dans `data/state.json` (pas sur GitHub).
- Modifier hook, début, fin, texte de fin puis « Enregistrer et re-rendre » : met à jour `clips/*.json`, relance
  `render.py` puis `check_sync.py` en arrière-plan.
- Raccourcis : `/` chercher, `←` `→` clip suivant, `C` copier la légende, `Échap` fermer.
- « Sélectionner » : cocher plusieurs clips (Maj+clic pour un intervalle, Ctrl+A pour tous les clips affichés)
  puis « Supprimer » (ou touche Suppr). Suppression complète : le mp4 part à la corbeille Windows (récupérable),
  le clip est retiré de `clips/*.json`, de `legendes.txt` et du suivi. Si un projet n'a plus de clip, son dossier
  `output/<projet>` et sa config partent aussi à la corbeille.
- « Envoyer sur l'iPhone » : affiche un QR code. L'iPhone (même Wi-Fi) l'ouvre avec l'appareil photo et télécharge
  le clip, puis Partager › Enregistrer la vidéo. Le lien passe par un 2e serveur (port 8766) ouvert sur le réseau local
  seulement après le premier QR : il ne sert qu'un clip par jeton aléatoire, valable 20 min (l'API reste sur 127.0.0.1).
  Secours : « Envoyer via iCloud » copie le mp4 (avec la date du jour) dans `Images\iCloud Photos\Photos`.
- Onglet « + Nouveau stream » : lien d'une VOD Twitch ou d'une chaîne (dernière VOD) + nom du streamer.
  L'appli enchaîne `twitch_vod.py` puis `analyze.py --chat` en arrière-plan (journal en direct), puis liste les moments
  repérés : cocher, écrire le hook, ajuster début/fin, tracer la facecam sur une image du stream (ou « pas de facecam »).
  « Créer » écrit `clips/<projet>.json`, ajoute les légendes dans `output/<projet>/legendes.txt` et lance les rendus.
  Les projets sont notés dans `data/projects.json` ; un stream déjà dans `input/` peut aussi être analysé sans lien.

## Sur le VPS : lives enregistrés tout seuls (https://clips.dossierminute.fr)
Le VPS (sans GPU) surveille, enregistre et repère ; le PC (GPU) transcrit et rend. L'appli est sur le VPS,
protégée par mot de passe, jamais indexée (noindex + robots.txt).
- **Streamers suivis** : onglet « Streamers suivis » de l'appli (ou `data/watchlist.json` sur le VPS), relu chaque
  minute. Catégorie suivie par défaut : `Grand Theft Auto VI` (nom exact sur Twitch).
- `tools/watcher.py` (service `clips-watcher`) : toutes les 60 s, qui est en live et dans quelle catégorie.
  Live dans une catégorie suivie : enregistrement 720p (streamlink, pubs retirées) par morceaux de 30 min, plus le chat
  en direct. Chaque morceau : volume + sons (YAMNet) + pics du chat, 3 meilleurs moments gardés (fenêtres de ~3 min
  sans ré-encodage), puis le morceau est supprimé. Toutes les 3 h de live (ou à la fin) : les moments sont collés en
  une vidéo courte `input/<chaine>_live_<date>_<heure>.mp4` + `work/<projet>/chat.json`, et une analyse part au PC.
  Arrêt après 5 min hors ligne ou hors catégorie. Disque : enregistrement coupé sous 4 Go libres, vidéos courtes déjà
  analysées supprimées du VPS après 3 jours (le PC les garde). Réglages : clés de `DEFAULTS` dans `watcher.py`,
  surchargeables dans `data/watchlist.json`.
- `tools/pc_worker.py` sur le PC (lancé à l'ouverture de session par `lancer_worker.bat`, raccourci dans le dossier
  Démarrage) : prend les tâches du VPS (`data/tasks.json`), fait `analyze.py` / `render.py` / `check_sync.py` sur le
  GPU, renvoie les résultats. PC éteint : les tâches attendent. Config : `data/worker.json` (url + jeton).
- Ensuite comme avant : « Nouveau stream » › choisir les moments › hook › « Créer » (rendu fait par le PC).
  « Envoyer sur l'iPhone » donne un lien HTTPS qui marche partout (20 min).
- Logs : `journalctl -u clips-watcher -f`, `journalctl -u clips-app -f`. État en direct : `data/live.json`.

Installation / mise à jour :
```
# sur le VPS (ssh -p 2222 ubuntu@213.32.16.246), dans ~/cliping (branche vps)
git pull && bash deploy/setup_vps.sh
.venv/bin/python tools/app.py --set-password     # une fois (ou pour changer le mot de passe)
.venv/bin/python tools/app.py --worker-token     # jeton à mettre dans data/worker.json sur le PC
# sur le PC : interface + modèle YAMNet vers le VPS, puis redémarrage des services
deploy\push_vps.bat
```

## Signaux utilisés pour trouver les moments
| Signal | Outil | Pour quoi |
|---|---|---|
| Transcription + mots-clés | Whisper (ffmpeg) | infos fortes, réactions verbales |
| Pics de volume | numpy | cris, moments intenses |
| Rires, cris, explosions, tirs, sirènes, crashs | YAMNet (onnxruntime) | réactions de streamers, action en jeu |
| Changements de plan | ffmpeg `scene` | passages d'action, calage propre début/fin |
| Écrans noirs | ffmpeg `blackdetect` | passages à couper |
| Pics du chat Twitch | API publique Twitch | LE meilleur signal sur les streams (KEKW, LUL, MDR, spam) |

## Rendu automatique
- Début et fin calés sur des phrases complètes, puis sur un changement de plan proche s'il tombe dans un silence.
- `cuts` : passages retirés à l'intérieur d'un clip ; les sous-titres et les zooms sont recalés.
- `jumpcut` : retire les blancs et les respirations (pour les streams).
- Sous-titres karaoké mot par mot, chaque mot calé sur la voix réellement détectée, DA néon façon Vice City (Montserrat, bandeau dégradé), voir PREFERENCES.md.
- Bruitages (whoosh sur les zooms, impact sur le hook et sur `impacts`) et musique synthwave originale qui baisse sous la voix.
- `"facecam": "auto"` : détection de la facecam (visage fixe + bords du cadre), contrôle dans `facecam_check.jpg`.
- Hook affiché au moins 6 s, écran de fin optionnel (`outro`), son à -14 LUFS, encodage NVENC.

## Sons et police
- `python tools/make_assets.py` régénère les bruitages et la musique dans `assets/`. Ils sont synthétisés, donc 100 % originaux.
- Police Montserrat (licence OFL, libre) dans `assets/fonts/`.

## Notes
- Pas d'emoji dans les hooks gravés : ils s'affichent mal. Les mettre dans la légende TikTok.
- Le VAD de Whisper est désactivé exprès : avec, la transcription est environ 40 fois plus lente.
- faster-whisper est bloqué par Smart App Control (Windows). On passe donc par le filtre whisper de ffmpeg.
- Le chat Twitch est téléchargé via l'API publique du site, sans compte. Ça peut casser si Twitch change son API.
  Solution de secours : un export JSON de TwitchDownloader, avec `--chat chemin/chat.json`.

## Installation (nouveau PC)
- ffmpeg 8+ build « full » (filtre whisper), Python 3 avec `numpy onnxruntime yt-dlp`. Node.js 20+ (interface de l'appli).
- Mettre dans `models/` : `ggml-large-v3-turbo-q5_0.bin` (whisper.cpp), `yamnet.onnx` + `yamnet_class_map.csv`
  (zeropointnine/yamnet-onnx), `ultraface-RFB-320.onnx` (Linzaer/Ultra-Light-Fast-Generic-Face-Detector).
- Les dossiers `input/`, `work/`, `models/` et les mp4 de `output/` ne sont pas versionnés.
- VOD Twitch (même en live) : `python tools/twitch_vod.py https://www.twitch.tv/videos/ID input/nom.mp4 [--start 8:15:00]`
  (`--start` : seulement la fin d'un long stream ; le chat est recalé via `input/nom.meta.json`).
- VOD réservée aux abonnés : enregistrer le live public avec yt-dlp (`python -m yt_dlp --hls-use-mpegts -o input/nom.ts https://www.twitch.tv/chaine`),
  puis remuxer en mp4 et écrire `input/nom.meta.json` avec `vod_start` (secondes de VOD au début de l'enregistrement).
