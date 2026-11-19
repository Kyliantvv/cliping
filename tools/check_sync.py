"""Verifie que les sous-titres d'un clip rendu sont synchro avec la voix.

Usage :
    python tools/check_sync.py clips/mon_stream.json [--only 01_nom]

Pour chaque clip deja rendu (output/<nom>.mp4 + work/_render/<video>/<nom>.ass) :
  - a chaque reprise de parole : ecart avec le mot qui s'allume (mediane, pire 10 %)
  - part du temps ou un mot est surligne alors que personne ne parle
  - decalage global estime (sous-titres trop tot / trop tard)
  - image work/_render/<video>/<nom>_sync.png : voix (gris) + mots (couleurs), 15 s par ligne, pour controler a l'oeil
Verdict OK / A VERIFIER par clip.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOP = 0.02
SR = 16000


def ass_t(s):
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def read_words(ass_path):
    """Mots surlignes (karaoke) -> [(debut, fin, mot, nouvelle_ligne)]."""
    out = []
    with open(ass_path, encoding="utf-8-sig") as f:
        for line in f:
            if not line.startswith("Dialogue:") or ",Cap," not in line:
                continue
            parts = line.rstrip("\n").split(",", 9)
            a, b, text = ass_t(parts[1]), ass_t(parts[2]), parts[9]
            m = re.search(r"\\fscx112\\fscy112}([^{]*)", text)
            word = m.group(1) if m else re.sub(r"{[^}]*}", "", text)
            out.append((a, b, word, text.startswith("{\\fscx85")))
    return out


def voice(mp4):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", mp4, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    hop = int(SR * HOP)
    n = len(x) // hop
    fr = x[: n * hop].reshape(n, hop)
    # energie dans la bande de la voix (250-3500 Hz) : la musique de fond et les basses comptent peu
    sp = np.abs(np.fft.rfft(fr * np.hanning(hop), axis=1)) ** 2
    f = np.fft.rfftfreq(hop, 1 / SR)
    db = 10 * np.log10(sp[:, (f > 250) & (f < 3500)].sum(axis=1) + 1e-9)
    thr = np.percentile(db, 90) - 20
    on = db >= thr
    for i in range(1, n - 1):  # micro-trous entre syllabes
        if not on[i] and on[max(0, i - 5):i].any() and on[i + 1:i + 6].any():
            on[i] = True
    return on, db


def onsets(on):
    """Debuts de prise de parole : voix presente apres >= 0.25 s de blanc."""
    out = []
    for i in range(13, len(on)):
        if on[i] and not on[i - 13:i].any():
            out.append(i * HOP)
    return np.array(out)


def draw(words, on, db, path, title):
    import shutil
    row_s, px = 15, 80
    dur = len(on) * HOP
    rows = int(np.ceil(dur / row_s))
    Wd, rh = row_s * px, 130
    img = np.zeros((rows * rh, Wd, 3), np.uint8)
    lvl = np.clip((db - (np.percentile(db, 90) - 40)) / 40, 0, 1)
    for i in range(len(on)):
        t = i * HOP
        r, x = int(t // row_s), int((t % row_s) * px)
        h = int(lvl[i] * 60)
        img[r * rh + 70 - h:r * rh + 70, x:x + 2] = (230, 230, 230) if on[i] else (90, 90, 90)
    cols = [(255, 79, 163), (25, 211, 197)]
    for k, (a, b, w, _) in enumerate(words):
        t = a
        while t < b:
            r = int(t // row_s)
            if r >= rows:
                break
            e = min(b, (r + 1) * row_s)
            x1, x2 = int((t % row_s) * px), max(int((t % row_s) * px) + 2, int(((e - 1e-6) % row_s) * px))
            img[r * rh + 78:r * rh + 108, x1:x2] = cols[k % 2]
            t = e
    for r in range(rows):
        for s in range(row_s):
            img[r * rh:r * rh + 112, s * px] = (60, 60, 60)
    raw = path + ".rgb"
    img.tofile(raw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{Wd}x{rows * rh}",
                    "-i", raw, path])
    os.remove(raw)


def check(name, sub=""):
    mp4 = os.path.join(ROOT, "output", sub, f"{name}.mp4")
    ass = os.path.join(ROOT, "work", "_render", sub, f"{name}.ass")
    if not os.path.exists(ass):  # rendus d'avant le rangement par sous-dossier
        ass = os.path.join(ROOT, "work", "_render", f"{name}.ass")
    if not (os.path.exists(mp4) and os.path.exists(ass)):
        print(f"{name}: pas encore rendu")
        return None
    words = read_words(ass)
    on, db = voice(mp4)
    if not words or not len(on):
        print(f"{name}: rien a verifier")
        return None
    ons = onsets(on)
    # 1. debuts de phrase vs debut reel de la voix
    # pour chaque reprise de parole, ecart avec le mot qui s'allume le plus pres (positif = mot en retard)
    starts = np.array([a for a, _, _, _ in words])
    errs = []
    for o in ons:
        d = starts - o
        near = d[np.abs(d) <= 1.5]
        if len(near):
            errs.append(near[np.argmin(np.abs(near))])
    errs = np.array(errs) if errs else np.zeros(1)
    # 2. mots surlignes pendant un blanc (tolerance 0.15 s)
    tot = sil = 0.0
    dil = on.copy()
    for k in range(1, 8):
        dil[k:] |= on[:-k]
        dil[:-k] |= on[k:]
    for a, b, _, _ in words:
        i, j = int(a / HOP), min(len(on), int(b / HOP))
        if j > i:
            tot += (j - i) * HOP
            sil += (~dil[i:j]).sum() * HOP
    sil_pct = 100 * sil / max(tot, 1e-6)
    # 3. decalage global = mediane signee des ecarts (robuste aux bruits du jeu pris pour de la voix)
    glob = float(np.median(errs))
    med, p90 = float(np.median(np.abs(errs))), float(np.percentile(np.abs(errs), 90))
    ok = med <= 0.2 and sil_pct <= 10 and abs(glob) <= 0.15  # p90 indicatif : voix du jeu non sous-titrees
    png = os.path.join(os.path.dirname(ass), f"{name}_sync.png")
    draw(words, on, db, png, name)
    print(f"{name}: {'OK' if ok else 'A VERIFIER'}  reprise de parole -> mot : ecart median {med:.2f} s, pire 10 % {p90:.2f} s | "
          f"mots sur un blanc : {sil_pct:.0f} % | decalage global {glob:+.2f} s "
          f"({'sous-titres en retard' if glob > 0 else 'sous-titres en avance'} si non nul)")
    save_result(f"{sub}/{name}" if sub else name, {"ok": bool(ok), "median": round(med, 2), "p90": round(p90, 2),
                                                     "silence_pct": round(sil_pct, 1), "offset": round(glob, 2)})
    return ok


def save_result(key, res):
    """Garde le dernier verdict de chaque clip (lu par l'appli de gestion tools/app.py)."""
    path = os.path.join(ROOT, "work", "_render", "sync.json")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    data[key] = res
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--only")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    sub = os.path.splitext(os.path.basename(cfg["source"]))[0]
    res = [check(c["name"], sub) for c in cfg["clips"] if not args.only or c["name"] == args.only]
    res = [r for r in res if r is not None]
    print(f"\n{sum(res)}/{len(res)} clips OK. Images de controle : work/_render/<video>/<nom>_sync.png "
          "(gris clair = voix, couleurs = mot surligne)")


if __name__ == "__main__":
    main()
