# Clipping TikTok GTA VI

Tout tourne en local : ffmpeg (transcription Whisper sur GPU), Python, YAMNet (détection de sons), UltraFace (visages).
Règles de montage : voir [PREFERENCES.md](PREFERENCES.md).

## Dossiers
- `input/` : vidéos/streams sources (.mp4)
- `work/<video>/` : transcription, signaux, planches d'aperçu
- `clips/<video>.json` : liste des clips (début, fin, hook, zooms, coupes)
- `output/` : clips TikTok finis 1080x1920 + `legendes.txt`
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
   Verdict OK / A VERIFIER par clip + image `work/_render/<clip>_sync.png` (gris = voix, couleurs = mots).

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
- ffmpeg 8+ build « full » (filtre whisper), Python 3 avec `numpy onnxruntime yt-dlp`.
- Mettre dans `models/` : `ggml-large-v3-turbo-q5_0.bin` (whisper.cpp), `yamnet.onnx` + `yamnet_class_map.csv`
  (zeropointnine/yamnet-onnx), `ultraface-RFB-320.onnx` (Linzaer/Ultra-Light-Fast-Generic-Face-Detector).
- Les dossiers `input/`, `work/`, `models/` et les mp4 de `output/` ne sont pas versionnés.
- VOD Twitch (même en live) : `python tools/twitch_vod.py https://www.twitch.tv/videos/ID input/nom.mp4`
