"""Rend des clips TikTok 1080x1920 a partir d'un fichier de config JSON.

Usage :
    python tools/render.py clips/mon_stream.json [--only 01_nom] [--cpu]

Config (exemple, les valeurs indiquees sont les valeurs par defaut) :
{
  "source": "input/mon_stream.mp4",
  "captions": "work/mon_stream/captions.json",
  "layout": "split",            // split = facecam en haut + gameplay en bas
                                // blur  = video 16:9 au centre, fond flou
                                // full  = recadrage 9:16 plein ecran
  "facecam": "auto",            // ou [x, y, largeur, hauteur] dans la source (split) ; auto = detection
  "cam_height": 640,            // hauteur de la facecam dans le 1080x1920 (split)
  "game_offset": 0.5,           // 0 = gauche, 0.5 = centre, 1 = droite (split/full)
  "fg_zoom": 1.4,               // agrandissement de la video centrale (blur)
  "karaoke": true,              // sous-titres mot par mot
  "jumpcut": false,             // true ou {"min_silence": 0.4, "keep": 0.12} : retire les blancs
  "music": "assets/music/synthwave_loop.wav",   // null = pas de musique
  "music_volume": -26,          // dB, la musique baisse encore toute seule quand ca parle
  "sfx": true,                  // whoosh sur les zooms, impact sur le hook / "impacts"
  "highlight": ["gta", "vice city"],
  "fixes": {"mauvais mot": "bon mot"},
  "clips": [
    {"name": "01_vol_voiture", "start": "00:12:03.5", "end": "00:12:31",
     "hook": "IL SE FAIT VOLER SA VOITURE EN 3 SECONDES",
     "zooms": ["00:12:20"],                      // zoom + whoosh (temps source)
     "impacts": ["00:12:25"],                    // bruit d'impact sur une info choc (temps source)
     "highlight": ["voiture"],
     "cuts": [["00:12:10", "00:12:12.5"]],       // passages a retirer (temps source)
     "outro": "Abonne-toi pour la suite"}        // ecran de fin 2.5 s (optionnel)
  ]
}
Direction artistique : voir PREFERENCES.md (Montserrat, degrade neon rose/orange/violet/turquoise).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vad  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS = 1080, 1920, 30
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")
SFX_DIR = os.path.join(ROOT, "assets", "sfx")
DEFAULT_MUSIC = "assets/music/synthwave_loop.wav"

# couleurs DA (ASS = &HAABBGGRR)
PINK = "&H00A34FFF"       # #FF4FA3
TURQUOISE = "&H00C5D319"  # #19D3C5
VIOLET = "&H00FF3C7A"     # #7A3CFF
DARK = "&H002E0B1A"       # #1A0B2E
WHITE = "&H00FFFFFF"
GRAD_A = (255, 79, 163)   # rose
GRAD_B = (255, 154, 60)   # orange

DEFAULT_HL = ["putain", "quoi", "non", "wow", "incroyable", "magnifique", "pas possible", "mort",
              "police", "choqué", "dingue", "ouf", "gta", "vice city", "rockstar"]

# erreurs frequentes de Whisper sur le vocabulaire GTA
FIXES = {
    r"\bV-?City\b": "Vice City", r"\bvice-city\b": "Vice City", r"\bG\.?T\.?A\.? ?(6|six|VI)\b": "GTA VI",
    r"\bGTA ?6\b": "GTA VI", r"\bLéonida\b": "Leonida", r"\bLucía\b": "Lucia",
}

END_PUNCT = (".", "!", "?", "…")
HOOK_SIZE = 60  # reduit le 19/11 (bandeau trop gros)
HOOK_CHARS = 22  # caracteres max par ligne du hook


# ------------------------------------------------------------------ utilitaires
def tsec(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = 0.0
    for p in str(v).split(":"):
        s = s * 60 + float(p)
    return s


def ass_time(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def esc(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def rel(p, base):
    """Chemin relatif pour ffmpeg (les ':' de C:\\ cassent les options de filtre)."""
    return os.path.relpath(p, base).replace("\\", "/")


def has_nvenc():
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1", "-c:v", "h264_nvenc",
                        "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def load_caps(path):
    caps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    caps.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    # segments avec l'heure de chaque mot (tools/retranscribe.py) : deja propres, on ne les retouche pas
    precise = [c for c in caps if c.get("words")]
    return sorted(clean_caps([c for c in caps if not c.get("words")]) + precise, key=lambda c: c["start"])


def clean_caps(caps):
    """Retire les doublons et les bouts de phrase sans duree que sort Whisper, avant l'affichage.

    - mots avec * (hallucination sur la musique) ; ponctuation seule ou en debut de segment recollee a la ligne d'avant ;
    - meme texte et meme duree qu'un des 2 segments d'avant, a moins de 8 s (repetition inventee) ;
    - bout sans duree colle au segment d'avant : fin de phrase repetee (raccord entre deux blocs) retiree,
      ponctuation seule recollee au segment d'avant, sinon le temps du segment d'avant est partage entre les deux
      (sinon les mots s'empilent au meme instant et deux lignes s'affichent en meme temps).
    """
    out = []
    for c in caps:
        text = " ".join(w for w in c.get("text", "").split() if "*" not in w)
        # ponctuation en debut de segment (« ? Il y a un chien ») : recollee a la fin du segment d'avant
        lead = re.match(r"^([!?.,…]+)\s*", text)
        if lead and out:
            out[-1]["text"] += " " + lead.group(1)
            text = text[lead.end():]
        if not text:
            continue
        if not _words(text):  # ponctuation seule
            if out:
                out[-1]["text"] += " " + text
            continue
        c = dict(c, text=text)
        dur = c["end"] - c["start"]
        low = text.lower()
        if any(low == q["text"].lower() and abs((q["end"] - q["start"]) - dur) <= 30 and c["start"] - q["end"] < 8000
               for q in out[-2:]):
            continue
        if out and dur < 250 and c["start"] - out[-1]["end"] < 300:
            p = out[-1]
            pw, cw = _words(p["text"]), _words(text)
            if len(cw) <= len(pw) and pw[-len(cw):] == cw:
                continue
            # partage le temps des segments colles d'avant (jusqu'a 4) pour que chacun ait ~0.3 s par mot
            grp = [c]
            while len(grp) < 5 and len(out) >= len(grp):
                q = out[-len(grp)]
                grp.insert(0, q)
                span = max(x["end"] for x in grp) - grp[0]["start"]
                n = sum(len(_words(x["text"])) for x in grp)
                prev = out[-len(grp)] if len(out) >= len(grp) else None
                if span >= 300 * n or prev is None or q["start"] - prev["end"] >= 300:
                    break
            a, b = grp[0]["start"], max(x["end"] for x in grp)
            tot = sum(len(x["text"]) for x in grp)
            t = a
            for x in grp:
                x["start"], t = int(t), t + (b - a) * len(x["text"]) / tot
                x["end"] = int(t)
        out.append(c)
    # bout sans duree isole : on lui donne du temps sur le blanc qui suit (sans deborder sur le segment suivant)
    for i in range(len(out) - 1, -1, -1):
        c = out[i]
        if c["end"] - c["start"] < 250:
            lim = out[i + 1]["start"] if i + 1 < len(out) else c["start"] + 10000
            c["end"] = max(c["end"], min(lim, c["start"] + max(400, 70 * len(c["text"]))))
    # bout sans duree en debut de phrase : partage le temps du segment suivant
    for i in range(len(out) - 1):
        c, n = out[i], out[i + 1]
        if c["end"] - c["start"] < 250 and n["start"] - c["end"] < 300 and n["end"] - c["start"] >= 250:
            a, b = c["start"], n["end"]
            cut = a + int((b - a) * len(c["text"]) / (len(c["text"]) + len(n["text"])))
            c["end"], n["start"] = cut, cut
    return out


def _words(text):
    return re.findall(r"[\w']+", text.lower())


def fix_text(text, cfg):
    for bad, good in {**FIXES, **{re.escape(k): v for k, v in cfg.get("fixes", {}).items()}}.items():
        text = re.sub(bad, good, text, flags=re.I)
    return text


# ------------------------------------------------------------------ calage et coupes
def snap(caps, start, end, cuts=()):
    """Cale le debut sur un debut de phrase et la fin sur une fin de phrase (jamais couper en plein milieu).
    Si un changement de plan tombe juste avant le debut / juste apres la fin (dans un silence), on cale dessus."""
    if not caps:
        return start, end
    best, prev_end = start, 0.0
    for i, c in enumerate(caps):
        s = c["start"] / 1000
        if s > start + 0.5:
            break
        prev_done = i == 0 or caps[i - 1]["text"].rstrip().endswith(END_PUNCT)
        if prev_done and s >= start - 8:
            best = s
            prev_end = caps[i - 1]["end"] / 1000 if i > 0 else 0.0
    start = max(0.0, prev_end, best - 0.15)  # jamais la fin du mot precedent
    ok = [c for c in cuts if max(prev_end + 0.05, best - 0.8) <= c <= best]
    if ok:
        start = ok[-1]
    for i, c in enumerate(caps):
        e = c["end"] / 1000
        if e < end - 0.5:
            continue
        if e > end + 15:
            break
        if c["text"].rstrip().endswith(END_PUNCT):
            nxt = caps[i + 1]["start"] / 1000 if i + 1 < len(caps) else e + 1
            end = max(e, min(e + 0.6, nxt - 0.1))
            ok = [k for k in cuts if e + 0.1 <= k <= min(e + 0.9, nxt - 0.1)]
            if ok:
                end = ok[0]
            break
    return start, end


def find_silences(wav_path, start, end, min_silence=0.4, keep=0.12):
    """Blancs et respirations dans [start, end] -> coupes (jump cuts). Seuil relatif au niveau de la voix."""
    if not os.path.exists(wav_path):
        return []
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        w.setpos(int(start * sr))
        data = np.frombuffer(w.readframes(int((end - start) * sr)), dtype=np.int16).astype(np.float32) / 32768
    hop = int(sr * 0.02)
    nf = len(data) // hop
    if nf < 10:
        return []
    db = 20 * np.log10(np.sqrt(np.mean(data[: nf * hop].reshape(nf, hop) ** 2, axis=1)) + 1e-9)
    thr = max(-50.0, min(-30.0, np.percentile(db, 90) - 22))
    quiet = db < thr
    cuts, i = [], 0
    while i < nf:
        if quiet[i]:
            j = i
            while j < nf and quiet[j]:
                j += 1
            a, b = i * 0.02, j * 0.02
            if b - a >= min_silence and a > 0.2 and b < (end - start) - 0.2:
                cuts.append((start + a + keep, start + b - keep))
            i = j
        else:
            i += 1
    return cuts


class Timeline:
    """Passage [start, end] de la source moins des coupes internes -> temps dans le clip final."""

    def __init__(self, start, end, cuts):
        merged = []
        for a, b in sorted((max(a, start), min(b, end)) for a, b in cuts if b > start and a < end and b > a):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        self.start, self.end, self.cuts = start, end, merged
        self.duration = (end - start) - sum(b - a for a, b in merged)

    def map(self, t):
        """Temps source -> temps clip (None si dans une coupe ou hors clip)."""
        if t < self.start or t > self.end:
            return None
        removed = 0.0
        for a, b in self.cuts:
            if t >= b:
                removed += b - a
            elif t > a:
                return None
        return t - self.start - removed

    def map_range(self, s, e):
        """Intervalle source -> intervalle clip (rogne sur les coupes), None si entierement coupe."""
        s, e = max(s, self.start), min(e, self.end)
        for a, b in self.cuts:
            if a <= s < b:
                s = b
            if a < e <= b:
                e = a
        if e <= s:
            return None
        ms, me = self.map(s), self.map(e)
        return (ms, me) if ms is not None and me is not None else None

    def select_expr(self):
        if not self.cuts:
            return None
        parts = "+".join(f"between(t,{a - self.start:.3f},{b - self.start:.3f})" for a, b in self.cuts)
        return f"not({parts})"


# ------------------------------------------------------------------ habillage (ASS + bandeau)
def wrap(text, max_chars):
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def hook_center_y(cfg):
    layout = cfg.get("layout", "blur")
    if layout == "split":
        return int(cfg.get("cam_height", 640))
    return 330


def caption_margin(layout, cam_h):
    if layout == "split":
        return H - cam_h - 260
    if layout == "blur":
        return 400
    return 560


def make_banner(lines, path):
    """Bandeau degrade rose -> orange a coins arrondis, taille adaptee au texte du hook."""
    longest = max(len(l) for l in lines)
    bw = min(W - 60, max(420, int(longest * HOOK_SIZE * 0.6) + 80))
    bh = len(lines) * int(HOOK_SIZE * 1.2) + 40
    r = 30
    (r1, g1, b1), (r2, g2, b2) = GRAD_A, GRAD_B
    alpha = (f"255*lte(pow(max(max({r}-X\\,X-(W-1-{r}))\\,0)\\,2)+pow(max(max({r}-Y\\,Y-(H-1-{r}))\\,0)\\,2)\\,{r * r})")
    geq = (f"geq=r='{r1}+({r2}-{r1})*X/W':g='{g1}+({g2}-{g1})*X/W':b='{b1}+({b2}-{b1})*X/W':a='{alpha}'")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={bw}x{bh}",
                    "-vf", f"format=rgba,{geq}", "-frames:v", "1", path], check=True)
    return bw, bh


class VoiceMask:
    """Activite vocale (trames de 20 ms) sur [start, end] de la source, meme seuil que les jump cuts."""

    HOP = 0.02

    def __init__(self, wav_path, start, end):
        self.t0, self.on = start, np.zeros(0, bool)
        if not os.path.exists(wav_path):
            return
        data, sr = self.isolated(os.path.dirname(wav_path), start, end)
        if data is None:
            with wave.open(wav_path, "rb") as w:
                sr = w.getframerate()
                w.setpos(int(max(0, start) * sr))
                data = np.frombuffer(w.readframes(int((end - start) * sr)), dtype=np.int16).astype(np.float32) / 32768
        hop = int(sr * self.HOP)
        nf = len(data) // hop
        if nf < 10:
            return
        if sr == vad.SR and vad.available():
            # modele de parole : ignore la musique et les bruits du jeu (un seuil de volume les prend pour de la voix)
            p = vad.speech_prob(data)
            if len(p):
                idx = np.minimum(len(p) - 1, (np.arange(nf) * self.HOP / vad.HOP).astype(int))
                self.on = p[idx] >= 0.35
            return
        db = 20 * np.log10(np.sqrt(np.mean(data[: nf * hop].reshape(nf, hop) ** 2, axis=1)) + 1e-9)
        thr = max(-50.0, min(-30.0, np.percentile(db, 90) - 22))
        on = db >= thr
        # bouche les micro-trous (< 100 ms) entre deux syllabes
        k = 5
        for i in range(1, len(on) - 1):
            if not on[i] and on[max(0, i - k):i].any() and on[i + 1:i + 1 + k].any():
                on[i] = True
        self.on = on

    @staticmethod
    def isolated(work, start, end):
        """Voix isolee de la musique par tools/retranscribe.py (work/<video>/_sep/voc_<debut>_<fin>.f32), si elle couvre le passage."""
        sep = os.path.join(work, "_sep")
        if not os.path.isdir(sep):
            return None, None
        for f in os.listdir(sep):
            m = re.fullmatch(r"voc_([\d.]+)_([\d.]+)\.f32", f)
            if m and float(m.group(1)) <= max(0, start) + 0.05 and float(m.group(2)) >= end - 0.05:
                x = np.fromfile(os.path.join(sep, f), np.float32)
                i = int((max(0, start) - float(m.group(1))) * 16000)
                return x[i:i + int((end - max(0, start)) * 16000)], 16000
        return None, None

    def frames(self, a, b):
        i = max(0, int((a - self.t0) / self.HOP))
        j = min(len(self.on), int((b - self.t0) / self.HOP))
        return i, j


def recale_runs(caps, vm, max_shift=1.5):
    """Whisper decale parfois toute une suite de segments (jusqu'a ~1.5 s) : le recalage mot par mot (+-0.3 s)
    ne suffit pas, et les mots tombent dans un blanc (supprime ensuite par les jump cuts).
    Pour chaque suite de segments colles, on cherche le decalage qui met le plus de texte sur de la voix."""
    if vm is None or not len(vm.on):
        return caps
    out = [dict(c) for c in caps]
    runs, cur = [], []
    for k, c in enumerate(out):
        if cur and c["start"] - out[cur[-1]]["end"] > 500:
            runs.append(cur)
            cur = []
        cur.append(k)
    if cur:
        runs.append(cur)

    def cover(run, d):
        tot = on = 0
        for k in run:
            i, j = vm.frames(out[k]["start"] / 1000 + d, out[k]["end"] / 1000 + d)
            if j > i:
                tot += j - i
                on += int(vm.on[i:j].sum())
        return on / tot if tot else 0.0

    for r, run in enumerate(runs):
        lo = (out[runs[r - 1][-1]]["end"] - out[run[0]]["start"]) / 1000 if r else -max_shift
        hi = (out[runs[r + 1][0]]["start"] - out[run[-1]]["end"]) / 1000 if r + 1 < len(runs) else max_shift
        shifts = [d / 20 for d in range(int(-max_shift * 20), int(max_shift * 20) + 1) if lo <= d / 20 <= hi]
        if not shifts:
            continue
        base = cover(run, 0.0)
        best = max(shifts, key=lambda d: (round(cover(run, d), 2), -abs(d)))
        if cover(run, best) - base >= 0.1:
            for k in run:
                out[k]["start"] += int(best * 1000)
                out[k]["end"] += int(best * 1000)
    return out


def align_words(text, s, e, vm, prev_end=-1e9, next_start=1e9):
    """Mots du segment [s, e] places sur les moments ou la voix est vraiment presente.

    Whisper donne des segments approximatifs (debut/fin souvent dans un blanc) : on resserre le segment
    sur la voix detectee, puis on repartit les mots sur le temps parle uniquement (pas sur les pauses).
    """
    words = text.split()
    if vm is None or not len(vm.on) or not words:
        return word_times(text, s, e)
    lo, hi = max(s - 0.3, prev_end), min(e + 0.3, next_start)
    i, j = vm.frames(lo, hi)
    idx = np.nonzero(vm.on[i:j])[0]
    if len(idx) < 3:
        return word_times(text, s, e)
    ts = vm.t0 + (i + idx) * vm.HOP  # instants voises
    # resserre sur la voix mais garde le segment ancre (pas plus de 0.3 s de deplacement par bord sans voix)
    ts = ts[(ts >= s - 0.3) & (ts <= e + 0.3)]
    if len(ts) < 3:
        return word_times(text, s, e)
    weights = np.array([len(w) + 1 for w in words], float)
    cum = np.concatenate([[0], np.cumsum(weights)]) / weights.sum() * (len(ts) - 1)
    out = []
    for k, w in enumerate(words):
        a = ts[int(round(cum[k]))]
        b = ts[min(len(ts) - 1, int(round(cum[k + 1])))] + vm.HOP
        out.append((w, a, max(b, a + 0.08)))
    return out


def word_times(text, s, e):
    """Decoupe un segment en mots avec des temps proportionnels a leur longueur."""
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    out, t = [], s
    for w, k in zip(words, weights):
        d = (e - s) * k / total
        out.append((w, t, t + d))
        t += d
    return out


def build_ass(clip, caps, tl, cfg, path, hook_lines, vm=None):
    layout = cfg.get("layout", "blur")
    cam_h = cfg.get("cam_height", 640)
    hl = [h.lower() for h in DEFAULT_HL + cfg.get("highlight", []) + clip.get("highlight", [])]
    hl_re = re.compile(r"(" + "|".join(re.escape(h) for h in sorted(hl, key=len, reverse=True)) + r")", re.I)
    mv = clip.get("caption_margin", caption_margin(layout, cam_h))
    font = "Montserrat Black"

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},82,{WHITE},{WHITE},{DARK},&H80000000,0,0,0,0,100,100,1,0,1,7,4,2,60,60,{mv},1
Style: Hook,{font},{HOOK_SIZE},{WHITE},{WHITE},{DARK},&H00000000,0,0,0,0,100,100,0,0,1,0,3,5,40,40,0,1
Style: Outro,{font},70,{WHITE},{WHITE},{VIOLET},{VIOLET},0,0,0,0,100,100,0,0,3,26,0,5,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    hd = 0.0
    if clip.get("hook"):
        # assez longtemps pour etre lu (PREFERENCES.md) : 6 s mini, plus si le texte est long
        hd = min(tl.duration, float(clip.get("hook_duration", max(6.0, 2.0 + len(clip["hook"]) / 12))))
        yc = hook_center_y(cfg)
        txt = r"\N".join(esc(l) for l in hook_lines)
        ev.append(f"Dialogue: 2,{ass_time(0)},{ass_time(hd)},Hook,,0,0,0,,"
                  rf"{{\pos({W // 2},{yc})\fscx70\fscy70\t(0,160,\fscx100\fscy100)}}" + txt)
    if clip.get("outro"):
        od = float(clip.get("outro_duration", 2.5))
        ev.append(f"Dialogue: 3,{ass_time(tl.duration)},{ass_time(tl.duration + od)},Outro,,0,0,0,,"
                  r"{\fscx60\fscy60\t(0,150,\fscx100\fscy100)}" + esc(clip["outro"].upper()))

    karaoke = clip.get("karaoke", cfg.get("karaoke", True))
    cap_ev = []
    floor = -1e9
    caps = [c for c in caps if c["end"] / 1000.0 > tl.start and c["start"] / 1000.0 < tl.end]
    if not any(c.get("words") for c in caps):
        caps = recale_runs(caps, vm)
    for ci, c in enumerate(caps):
        s, e = c["start"] / 1000.0, c["end"] / 1000.0
        if any(d.lower() in c["text"].lower() for d in cfg.get("drop", []) + clip.get("drop", [])):
            continue  # ligne a retirer (dialogue du jeu traduit, mot invente par Whisper...)
        text = esc(fix_text(c["text"].strip(), cfg).upper())
        # "GAGNE !" reste un seul mot (sinon le "!" s'allume tout seul et decale les temps des mots)
        text = re.sub(r" ([!?:;]+)", "\1", text)
        if not text:
            continue
        prev_e = caps[ci - 1]["end"] / 1000.0 if ci else -1e9
        next_s = caps[ci + 1]["start"] / 1000.0 if ci + 1 < len(caps) else 1e9
        if c.get("words") and len(text.split()) == len(c["words"]):
            words = [(w, ws / 1000.0, we / 1000.0) for w, (_, ws, we) in zip(text.split(), c["words"])]
            # recale sur la voix isolee (Whisper peut se tromper de quelques dixiemes sur un cri)
            al = align_words(text, s, e, vm, prev_e - 0.05, next_s + 0.05)
            if vm is not None and len(vm.on) and al != word_times(text, s, e):
                words = [(w, (a1 + a2) / 2, (b1 + b2) / 2) for (w, a1, b1), (_, a2, b2) in zip(words, al)]
            # Whisper place souvent le 1er mot d'une phrase un peu tard : on le cale sur le debut de la voix
            if vm is not None and len(vm.on):
                i, j = vm.frames(max(words[0][1] - 0.8, prev_e), words[0][1] + 0.05)
                seg = vm.on[i:j]
                starts = [k for k in range(1, len(seg)) if seg[k] and not seg[k - 1]
                          and not vm.on[max(0, i + k - 13):i + k].any()]
                if starts:
                    t0 = vm.t0 + (i + starts[-1]) * vm.HOP
                    w0, _, b0 = words[0]
                    words[0] = (w0, t0, max(b0, t0 + 0.08))
        elif c.get("words"):
            words = word_times(text, s, e)
        else:
            words = align_words(text, s, e, vm, prev_e - 0.1, next_s + 0.1)
        # les mots d'une ligne viennent toujours apres ceux de la ligne d'avant : sinon, quand Whisper coupe une
        # phrase en deux segments presque simultanes, les deux lignes alternent a l'ecran
        fixed, t = [], floor
        for w, a, b in words:
            a = max(a, t)
            fixed.append((w, a, max(b, a + 0.08)))
            t = a + 0.06
        words = fixed
        floor = words[-1][1] + 0.1
        # chaque mot reste affiche jusqu'au suivant (pas de clignotement), la ligne jusqu'a la fin de la voix + 0.3 s
        words = [(w, a, max(b, words[k + 1][1]) if k + 1 < len(words) and words[k + 1][1] - b < 0.6 else b)
                 for k, (w, a, b) in enumerate(words)]
        s, e = words[0][1], min(words[-1][2] + 0.3, next_s)
        # mots-cles en turquoise
        base = [hl_re.sub(lambda m: f"{{\\c{TURQUOISE}&}}" + m.group(0) + f"{{\\c{WHITE}&}}", w) for w, _, _ in words]
        if not karaoke:
            rng = tl.map_range(s, e)
            if rng:
                cap_ev.append([rng[0], rng[1], r"{\fscx85\fscy85\t(0,90,\fscx105\fscy105)\t(90,160,\fscx100\fscy100)}"
                               + " ".join(base)])
            continue
        first = True
        for i, (w, ws, we) in enumerate(words):
            if i == len(words) - 1:
                we = max(we, e)
            rng = tl.map_range(ws, we)
            if not rng:
                continue
            # mot en cours en rose, legerement grossi
            parts = [f"{{\\c{PINK}&\\fscx112\\fscy112}}{esc(w)}{{\\c{WHITE}&\\fscx100\\fscy100}}" if j == i else b
                     for j, b in enumerate(base)]
            anim = r"{\fscx85\fscy85\t(0,80,\fscx100\fscy100)}" if first else ""
            first = False
            cap_ev.append([rng[0], rng[1], anim + " ".join(parts)])
    # jamais deux lignes de sous-titres en meme temps (libass les empile : texte en double a l'ecran)
    cap_ev.sort(key=lambda x: x[0])
    for k, (a, b, txt) in enumerate(cap_ev):
        if k + 1 < len(cap_ev):
            b = min(b, cap_ev[k + 1][0])
        if b - a >= 0.02:
            ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{txt.replace(chr(7), ' ')}")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(head + "\n".join(ev) + "\n")
    return hd


# ------------------------------------------------------------------ video
def resolve_facecam(cfg):
    fc = cfg.get("facecam", "auto")
    if fc != "auto":
        return fc
    work = os.path.join(ROOT, os.path.dirname(cfg["captions"]))
    path = os.path.join(work, "facecam.json")
    if not os.path.exists(path):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import facecam
        facecam.detect(os.path.join(ROOT, cfg["source"]), work)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("rect"):
        raise SystemExit("Facecam non detectee : mettre \"facecam\": [x, y, largeur, hauteur] ou \"layout\": \"blur\"")
    return data["rect"]


def video_chain(cfg, src_label):
    layout = cfg.get("layout", "blur")
    off = float(cfg.get("game_offset", 0.5))
    if layout == "split":
        x, y, cw, ch = resolve_facecam(cfg)
        cam_h = int(cfg.get("cam_height", 640))
        game_h = H - cam_h
        return (f"{src_label}split[a][b];"
                f"[a]crop={cw}:{ch}:{x}:{y},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
                f"[b]scale=-2:{game_h},crop={W}:{game_h}:(iw-{W})*{off}:0[game];"
                f"[cam][game]vstack,setsar=1[v0]")
    if layout == "full":
        return f"{src_label}scale=-2:{H},crop={W}:{H}:(iw-{W})*{off}:0,setsar=1[v0]"
    fz = max(1.0, float(cfg.get("fg_zoom", 1.4)))
    return (f"{src_label}split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.12[bg];"
            f"[b]scale={int(W * fz) // 2 * 2}:-2,crop={W}:ih[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")


def zoom_expr(zooms):
    # zoom rapide (0.15 s), maintien ~1.2 s, retour (0.3 s)
    terms = [f"0.16*clip((it-{z - 0.15:.3f})/0.15,0,1)*clip(({z + 1.5:.3f}-it)/0.3,0,1)" for z in zooms]
    return "1+" + "+".join(terms)


# ------------------------------------------------------------------ rendu
def render(cfg, clip, enc):
    # reglages video surchargeables par clip (la facecam bouge parfois pendant un stream)
    cfg = {**cfg, **{k: clip[k] for k in ("facecam", "layout", "cam_height", "game_offset", "fg_zoom") if k in clip}}
    src = os.path.join(ROOT, cfg["source"])
    caps = load_caps(os.path.join(ROOT, cfg["captions"])) if cfg.get("captions") else []
    work = os.path.join(ROOT, os.path.dirname(cfg["captions"])) if cfg.get("captions") else None
    scene_cuts = []
    if work and os.path.exists(os.path.join(work, "scenes.json")):
        with open(os.path.join(work, "scenes.json"), encoding="utf-8") as f:
            scene_cuts = json.load(f).get("cuts", [])
    start, end = tsec(clip["start"]), tsec(clip["end"])
    if clip.get("snap", cfg.get("snap", True)):
        start, end = snap(caps, start, end, scene_cuts)

    cuts = [(tsec(a), tsec(b)) for a, b in clip.get("cuts", [])]
    jc = clip.get("jumpcut", cfg.get("jumpcut", False))
    n_jc = 0
    if jc and work:
        opts = jc if isinstance(jc, dict) else {}
        sil = find_silences(os.path.join(work, "audio.wav"), start, end,
                            opts.get("min_silence", 0.4), opts.get("keep", 0.12))
        n_jc = len(sil)
        cuts += sil
    tl = Timeline(start, end, cuts)

    name = clip["name"]
    # un sous-dossier par video source : evite que deux streamers avec un clip "01_decompte" s'ecrasent
    sub = os.path.splitext(os.path.basename(cfg["source"]))[0]
    tmp = os.path.join(ROOT, "work", "_render", sub)
    os.makedirs(tmp, exist_ok=True)
    out_dir = os.path.join(ROOT, "output", sub)
    os.makedirs(out_dir, exist_ok=True)

    hook_lines = wrap(clip["hook"].upper(), HOOK_CHARS) if clip.get("hook") else []
    vm = VoiceMask(os.path.join(ROOT, os.path.dirname(cfg["captions"]), "audio.wav"), max(0, start - 2), end + 2)         if cfg.get("captions") else None
    hd = build_ass(clip, caps, tl, cfg, os.path.join(tmp, f"{name}.ass"), hook_lines, vm)
    od = float(clip.get("outro_duration", 2.5)) if clip.get("outro") else 0.0
    total = tl.duration + od

    inputs = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", src]
    n_in = 1

    def add_input(args):
        nonlocal n_in
        inputs.extend(args)
        n_in += 1
        return n_in - 1

    # ---- video
    sel = tl.select_expr()
    fc = ""
    vsrc, asrc = "[0:v]", "[0:a]"
    if sel:
        # fps d'abord : les enregistrements de live (.ts remuxes) annoncent 30 i/s mais en contiennent 60,
        # et setpts=N/FRAME_RATE mettait alors la video au ralenti (2x plus lente que le son)
        fc += (f"[0:v]fps={FPS},select='{sel}',setpts=N/{FPS}/TB[vin];"
               f"[0:a]aselect='{sel}',asetpts=N/SR/TB[ain];")
        vsrc, asrc = "[vin]", "[ain]"
    fc += video_chain(cfg, vsrc)
    chain = f";[v0]fps={FPS}"
    zooms = [z for z in (tl.map(tsec(z)) for z in clip.get("zooms", [])) if z is not None]
    if zooms:
        chain += (f",zoompan=z='{zoom_expr(zooms)}':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
                  f":s={W}x{H}:fps={FPS}")
    if od:
        chain += f",tpad=stop_mode=clone:stop_duration={od}"
    chain += "[vz]"
    fc += chain
    vlast = "[vz]"
    if hook_lines and hd > 0:
        banner = os.path.join(tmp, f"{name}_banner.png")
        bw, bh = make_banner(hook_lines, banner)
        bi = add_input(["-loop", "1", "-i", banner])
        yc = hook_center_y(cfg)
        fc += (f";[{bi}:v]format=rgba[bn];{vlast}[bn]overlay=x={(W - bw) // 2}:y={yc - bh // 2}"
               f":enable='lte(t,{hd:.2f})':shortest=1[vb]")
        vlast = "[vb]"
    fonts = rel(FONTS_DIR, tmp)
    fc += f";{vlast}ass={name}.ass:fontsdir={fonts}[v]"

    # ---- audio : voix normalisee + bruitages + musique qui s'efface sous la voix
    fc += f";{asrc}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if od:
        fc += f",apad=pad_dur={od}"
    music = clip.get("music", cfg.get("music", DEFAULT_MUSIC))
    fc += "[voice0]"
    mix = []
    if music:
        fc += ";[voice0]asplit=2[voice][sc]"
    else:
        fc += ";[voice0]anull[voice]"
    mix.append("[voice]")

    if clip.get("sfx", cfg.get("sfx", True)):
        hits = []
        whoosh = [max(0.0, z - 0.25) for z in zooms]
        if whoosh:
            hits.append(("whoosh.wav", whoosh, 0.55))
        impacts = [0.0] if hook_lines else []
        impacts += [z for z in (tl.map(tsec(t)) for t in clip.get("impacts", [])) if z is not None]
        if od:
            impacts.append(tl.duration)
        if impacts:
            hits.append(("impact.wav", impacts, 0.5))
        for fname, times, vol in hits:
            si = add_input(["-i", os.path.join(SFX_DIR, fname)])
            labels = [f"[s{si}_{k}]" for k in range(len(times))]
            fc += f";[{si}:a]aresample=48000,volume={vol},asplit={len(times)}" + "".join(labels)
            for k, t in enumerate(times):
                ms = int(t * 1000)
                fc += f";{labels[k]}adelay={ms}|{ms}[d{si}_{k}]"
                mix.append(f"[d{si}_{k}]")

    if music:
        mi = add_input(["-stream_loop", "-1", "-i", os.path.join(ROOT, music)])
        vol = float(clip.get("music_volume", cfg.get("music_volume", -26)))
        fc += (f";[{mi}:a]aresample=48000,volume={vol}dB,atrim=0:{total:.3f},"
               f"afade=t=in:d=0.6,afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[mus]"
               f";[mus][sc]sidechaincompress=threshold=0.05:ratio=5:attack=15:release=350[mduck]")
        mix.append("[mduck]")
    fc += f";{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[a]"

    if enc == "nvenc":
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-maxrate", "16M"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]
    out = os.path.join(out_dir, f"{name}.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
           *vcodec, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    last = [c["text"] for c in caps if start * 1000 <= c["start"] < end * 1000][-2:]
    extra = []
    if tl.cuts:
        extra.append(f"{len(tl.cuts)} coupes ({n_jc} blancs, -{(end - start) - tl.duration:.1f} s)")
    print(f"\n>> {name}  {start:.1f}-{end:.1f}  ({total:.1f} s)  {' '.join(extra)}  fin : ...{' '.join(last)}")
    if total < 60:
        print("   ATTENTION : moins de 60 s")
    r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"ECHEC {name}\n{r.stderr[-1500:]}", file=sys.stderr)
        return False
    print(f"   -> {out}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--cpu", action="store_true", help="encode avec libx264 au lieu de NVENC")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    enc = "x264" if args.cpu or not has_nvenc() else "nvenc"
    ok = 0
    for clip in cfg["clips"]:
        if args.only and clip["name"] not in args.only:
            continue
        ok += render(cfg, clip, enc)
    print(f"\n{ok} clip(s) rendu(s) dans output/")


if __name__ == "__main__":
    main()
