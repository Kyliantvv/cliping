"""Telecharge une VOD Twitch (meme si le live est encore en cours) en parallele.

yt-dlp passe par ffmpeg en temps reel pour une VOD en cours (~1x, et il demarre au bord du live).
Ici on lit la playlist HLS, on telecharge tous les segments deja disponibles en parallele, puis on colle.

Usage :
    python tools/twitch_vod.py https://www.twitch.tv/videos/123 input/nom.mp4 [--quality 720p60] [--start 8:20:00] [--end 9:00:00]
--start/--end : ne prendre qu'une partie de la VOD (temps de la VOD). Le decalage est note dans input/nom.meta.json
pour que l'analyse recale le chat.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise


def secs(v):
    p = [float(x) for x in str(v).split(":")]
    return sum(x * 60 ** i for i, x in enumerate(reversed(p)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--quality", default="720p60")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--start", default="0", help="debut (s ou h:mm:ss) dans la VOD")
    ap.add_argument("--end", default=None, help="fin (s ou h:mm:ss) dans la VOD")
    args = ap.parse_args()

    m3u8, r = None, None
    for q in (args.quality, "best[height<=720]", "best"):
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", q, "-g", args.url], capture_output=True, text=True)
        if r.stdout.strip():
            m3u8 = r.stdout.strip().splitlines()[-1]
            break
        if "subscriber-only" in r.stderr:
            raise SystemExit("VOD reservee aux abonnes : impossible sans compte abonne. "
                             "Enregistrer le live en direct a la place (voir README)")
    if not m3u8:
        raise SystemExit("VOD introuvable : " + r.stderr.strip()[-300:])
    base = m3u8.rsplit("/", 1)[0] + "/"
    lines = get(m3u8).decode().splitlines()
    segs, starts, t, dur = [], [], 0.0, 10.0
    for l in lines:
        if l.startswith("#EXTINF:"):
            dur = float(l[8:].split(",")[0])
        elif l and not l.startswith("#"):
            segs.append(l)
            starts.append(t)
            t += dur
    a, b = secs(args.start), secs(args.end) if args.end else 1e12
    keep = [i for i, st in enumerate(starts) if st + 10 > a and st < b]
    segs, vod_start = [segs[i] for i in keep], starts[keep[0]]
    print(f"{len(segs)} segments (~{len(segs) * 10 / 60:.0f} min) a partir de {vod_start:.0f} s de VOD")

    tmp = args.out + ".parts"
    os.makedirs(tmp, exist_ok=True)

    def dl(i_s):
        i, s = i_s
        p = os.path.join(tmp, f"{i:06d}.ts")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return
        # segments "-muted" : Twitch renomme parfois, on essaie les deux
        try:
            data = get(base + s)
        except Exception:
            data = get(base + s.replace(".ts", "-muted.ts"))
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)

    done = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for _ in ex.map(dl, enumerate(segs)):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(segs)}", flush=True)

    # collage binaire des .ts : les horodatages Twitch sont continus d'un segment a l'autre, on les garde tels quels
    # (le demuxer concat de ffmpeg les recalcule segment par segment : trous de son, decalage de +-0.8 s)
    big = os.path.join(tmp, "all.ts")
    with open(big, "wb") as out:
        for i in range(len(segs)):
            with open(os.path.join(tmp, f"{i:06d}.ts"), "rb") as f:
                out.write(f.read())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", big, "-c", "copy", "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart", os.path.abspath(args.out)], check=True)
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)
    with open(os.path.splitext(args.out)[0] + ".meta.json", "w", encoding="utf-8") as f:
        json.dump({"vod": args.url, "vod_start": vod_start}, f)
    print("OK ->", args.out)


if __name__ == "__main__":
    main()
