# Préférences de montage

Règles à appliquer à tous les prochains clips.

1. **Durée : au moins 1 minute par TikTok.** Plus long est OK si le passage tient (60 à 120 s).
2. **Ne jamais couper en pleine phrase.** Le clip commence au début d'une phrase et finit à la fin d'une phrase.
   `tools/render.py` cale automatiquement le début et la fin sur la ponctuation de la transcription (`"snap": false` pour désactiver).
   Relire quand même la dernière phrase de chaque clip.
3. **Le titre (hook) reste affiché assez longtemps pour être lu** : 6 s minimum, plus pour un titre long
   (calcul auto dans `render.py`, ou `"hook_duration": 8` sur un clip).
4. **Direction artistique dans l'esprit GTA VI** (Vice City, Miami, coucher de soleil néon) :
   - Police : la typo officielle de GTA VI appartient à Rockstar et n'est pas en libre accès.
     Les copies « GTA 6 font » qui circulent ne sont pas sous licence : ne pas les utiliser.
     À la place, une police gratuite (licence OFL) au rendu proche : grasse, géométrique, en majuscules.
     Choix par défaut : **Montserrat Black**. Alternative : **Anton** pour un style plus condensé.
   - Couleurs (dégradé coucher de soleil) :
     - rose néon `#FF4FA3`
     - orange `#FF9A3C`
     - violet `#7A3CFF`
     - turquoise `#19D3C5`
     - texte blanc, contour sombre `#1A0B2E`
   - Hook : texte blanc sur un bandeau en dégradé rose → orange (à la place de l'encadré blanc).
   - Mots-clés des sous-titres : rose néon ou turquoise, à la place du jaune.
   - Écran de fin : fond violet, texte blanc.
   - Ne jamais reprendre le logo officiel GTA VI ni l'habillage Rockstar : le compte ne doit pas avoir l'air officiel.
   - **Appliqué automatiquement par `render.py`** (police dans `assets/fonts/`).
5. **Sous-titres karaoké** : chaque mot s'allume en rose quand il est prononcé, mots-clés en turquoise.
   Toujours lancer `python tools/check_sync.py clips/<video>.json` après le rendu : pas de publication si A VERIFIER sans avoir regardé.
6. **Jump cuts sur les streams** : `"jumpcut": true` retire les blancs et respirations de plus de 0,4 s.
   Ça n'a aucun effet sur les vidéos avec une musique de fond continue, et c'est voulu : couper par-dessus la musique s'entendrait.
   Penser aux 60 s : les jump cuts raccourcissent le clip.
7. **Bruitages et musique** : un whoosh sur chaque zoom, un impact sur le hook, sur l'écran de fin et sur les `impacts`.
   Musique synthwave originale très basse, qui baisse encore sous la voix.
   Tous les sons sont générés par `tools/make_assets.py`, donc aucun problème de droits.
   `"music": null` pour publier sans musique et ajouter un son tendance dans TikTok à la place.
8. **Facecam** : `"facecam": "auto"` la détecte toute seule. Vérifier `work/<video>/facecam_check.jpg`.
