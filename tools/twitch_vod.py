"""Telecharge une VOD Twitch (meme si le live est encore en cours) en parallele.

yt-dlp passe par ffmpeg en temps reel pour une VOD en cours (~1x, et il demarre au bord du live).
Ici on lit la playlist HLS, on telecharge tous les segments deja disponibles en parallele, puis on colle.

Usage :
    python tools/twitch_vod.py https://www.twitch.tv/videos/123 input/nom.mp4 [--quality 720p60] [--from-seg N]
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--quality", default="720p60")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    m3u8 = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", args.quality, "-g", args.url],
                          capture_output=True, text=True).stdout.strip().splitlines()[-1]
    base = m3u8.rsplit("/", 1)[0] + "/"
    lines = get(m3u8).decode().splitlines()
    segs = [l for l in lines if l and not l.startswith("#")]
    print(f"{len(segs)} segments (~{len(segs) * 10 / 60:.0f} min)")

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

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for i in range(len(segs)):
            f.write(f"file '{i:06d}.ts'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-c", "copy", "-bsf:a", "aac_adtstoasc", os.path.abspath(args.out)], cwd=tmp, check=True)
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)
    print("OK ->", args.out)


if __name__ == "__main__":
    main()
