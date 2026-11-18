"""Planches d'images pour verifier chaque clip avant le rendu (1 image toutes les 2 s, heure source affichee).

Usage :
    python tools/preview.py clips/ma_video.json [--only 01_nom] [--every 2]

Sortie : work/<video>/preview/<clip>.jpg (+ resume dans le terminal : ecrans noirs, changements de plan,
sons detectes dans le clip). On regarde les planches pour reperer ecrans noirs, spoilers, logos, plans faibles
(-> "cuts" dans la config) et les moments visuels forts (-> "zooms").
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402

ROOT = render.ROOT
# chemin relatif (les ':' de C:\ cassent les options de filtre ffmpeg)
FONT = os.path.relpath(os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialbd.ttf"), ROOT).replace("\\", "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--every", type=float, default=2.0)
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    src = os.path.join(ROOT, cfg["source"])
    work = os.path.join(ROOT, os.path.dirname(cfg["captions"]))
    caps = render.load_caps(os.path.join(ROOT, cfg["captions"]))
    sig = {}
    for k in ("scenes", "events"):
        p = os.path.join(work, f"{k}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                sig[k] = json.load(f)
    cuts = sig.get("scenes", {}).get("cuts", [])
    black = sig.get("scenes", {}).get("black", [])
    out_dir = os.path.join(work, "preview")
    os.makedirs(out_dir, exist_ok=True)

    for clip in cfg["clips"]:
        if args.only and clip["name"] not in args.only:
            continue
        start, end = render.tsec(clip["start"]), render.tsec(clip["end"])
        if clip.get("snap", cfg.get("snap", True)):
            start, end = render.snap(caps, start, end, cuts)
        dur = end - start
        n = int(dur / args.every) + 1
        cols = 6
        rows = (n + cols - 1) // cols
        out = os.path.join(out_dir, f"{clip['name']}.jpg")
        vf = (f"fps=1/{args.every},scale=320:-2,"
              f"drawtext=fontfile={FONT}:text='%{{pts\\:hms\\:{start:.2f}}}':x=6:y=6:fontsize=20:fontcolor=white:"
              f"box=1:boxcolor=black@0.6:boxborderw=4,tile={cols}x{rows}:padding=4:margin=4")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
                            "-vf", vf, "-frames:v", "1", "-q:v", "4", out], capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            print(f"ECHEC {clip['name']} : {r.stderr[-300:]}")
            continue
        nc = sum(1 for c in cuts if start <= c <= end)
        bl = [(a, b) for a, b in black if b > start and a < end]
        evs = {}
        for e in sig.get("events", []):
            if start <= e["t"] <= end:
                evs[e["type"]] = evs.get(e["type"], 0) + 1
        print(f"{clip['name']}: {start:.1f}-{end:.1f} ({dur:.0f} s)  {n} images  "
              f"{nc} changements de plan ({nc / max(dur, 1) * 10:.1f}/10 s)"
              + (f"  ECRANS NOIRS: {', '.join(f'{a:.1f}-{b:.1f}' for a, b in bl)}" if bl else "")
              + (f"  sons: {evs}" if evs else ""))
        print(f"   -> {out}")


if __name__ == "__main__":
    main()
