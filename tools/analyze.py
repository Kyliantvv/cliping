"""Analyse une video/stream : transcription Whisper + volume + moments forts.

Usage :
    python tools/analyze.py input/mon_stream.mp4 [--chat https://www.twitch.tv/videos/123] [--lang fr] [--top 25]

Signaux combines : volume, mots-cles, sons (rires/cris/explosions/tirs via YAMNet),
changements de plan (ffmpeg), pics du chat Twitch (si --chat ou work/<video>/chat.json).

Sortie dans work/<nom_video>/ :
    audio.wav         audio 16 kHz mono
    captions.json     segments courts (sous-titres), un JSON par ligne, temps en ms
    transcript.txt    transcription lisible avec timestamps
    loudness.json     volume (dB) par tranche de 0.5 s
    candidates.txt    meilleurs moments detectes, avec la raison (sons, chat, montage rapide...)
    events.txt        rires, cris, explosions, tirs, ecrans noirs avec le texte autour
    events.json / scenes.json / chat.json   donnees brutes des signaux
    frame.jpg         image du milieu de la video (pour placer la facecam)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL = os.path.join(ROOT, "models", "ggml-large-v3-turbo-q5_0.bin")


KEYWORDS = [
    "putain", "wesh", "quoi", "non", "nan", "oh", "wow", "waouh", "incroyable", "magnifique",
    "pas possible", "c'est pas vrai", "mais", "regarde", "regardez", "attends", "aide", "au secours",
    "mort", "tué", "police", "flic", "choqué", "dingue", "fou", "ouf", "zebi", "frère", "mdr", "ptdr",
    "rockstar", "gta", "vice city", "lucia", "jason", "voiture", "crash", "bug", "argent",
    "what", "bro", "no way", "oh my god", "insane", "crazy",
]

BIN = 0.5  # secondes

# phrases que Whisper invente sur les silences / la musique
HALLU = re.compile(r"radio-canada|sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|thanks for watching", re.I)


def fmt(t):
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def rel(p, base):
    """Chemin relatif pour ffmpeg (les ':' de C:\\ cassent les options de filtre)."""
    return os.path.relpath(p, base).replace("\\", "/")


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"Erreur commande : {' '.join(cmd[:4])}...")
    return r


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def clean_captions(path):
    """Corrige les debuts de segment apres un silence et vire les hallucinations repetees."""
    caps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = c.get("text", "").strip()
            if not text or text.startswith(("[", "(", "*")) or HALLU.search(text):
                continue
            c["text"] = text
            caps.append(c)
    out, last, rep = [], None, 0
    for c in caps:
        rep = rep + 1 if c["text"] == last else 0
        last = c["text"]
        if rep >= 2:
            continue
        # whisper colle le debut du segment au debut du silence : on le ramene
        max_ms = int(len(c["text"]) * 75 + 300)
        if c["end"] - c["start"] > max_ms:
            c["start"] = c["end"] - max_ms
        out.append(c)
    with open(path, "w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--lang", default="fr")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--skip-transcribe", action="store_true")
    ap.add_argument("--chat", help="lien VOD Twitch ou fichier JSON du chat")
    args = ap.parse_args()

    video = os.path.abspath(args.video)
    name = os.path.splitext(os.path.basename(video))[0]
    out = os.path.join(ROOT, "work", name)
    os.makedirs(out, exist_ok=True)
    dur = duration(video)
    print(f"Video : {name} ({fmt(dur)})")

    # 1. audio
    wav = os.path.join(out, "audio.wav")
    if not os.path.exists(wav):
        print("Extraction audio...")
        run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])

    # 2. image pour reperer la facecam
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(dur * 0.5), "-i", video, "-frames:v", "1", "-q:v", "3",
         os.path.join(out, "frame.jpg")])

    # 3. transcription (ffmpeg + whisper.cpp)
    caps_path = os.path.join(out, "captions.json")
    if not args.skip_transcribe or not os.path.exists(caps_path):
        print("Transcription Whisper (peut prendre du temps sur un long stream)...")
        if os.path.exists(caps_path):
            os.remove(caps_path)
        # pas de VAD : avec, le filtre est ~40x plus lent
        wf = (f"whisper=model={rel(args.model, out)}:language={args.lang}:queue=30:max_len=18"
              f":format=json:destination=captions.json")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", "audio.wav", "-af", wf, "-f", "null", "-"]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=out)
        if p.returncode != 0:
            raise SystemExit("Echec transcription")
        print(f"Transcription : {time.time() - t0:.0f} s")
        clean_captions(caps_path)

    caps = []
    with open(caps_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                caps.append(json.loads(line))
    print(f"{len(caps)} segments transcrits")

    # transcript lisible : regroupe par phrases (~6 s)
    lines, cur, cur_start = [], [], None
    for c in caps:
        if cur_start is None:
            cur_start = c["start"]
        cur.append(c["text"].strip())
        if c["end"] - cur_start > 6000 or c["text"].strip().endswith((".", "?", "!")):
            lines.append(f"[{fmt(cur_start / 1000)}] {' '.join(cur)}")
            cur, cur_start = [], None
    if cur:
        lines.append(f"[{fmt(cur_start / 1000)}] {' '.join(cur)}")
    with open(os.path.join(out, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 4. volume par tranche
    with wave.open(wav, "rb") as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(16000 * BIN)
    nb = len(raw) // n
    rms = np.sqrt(np.mean(raw[: nb * n].reshape(nb, n) ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(rms)
    with open(os.path.join(out, "loudness.json"), "w") as f:
        json.dump([round(float(x), 1) for x in db], f)

    # 5. signaux : sons (rires, cris, explosions...), changements de plan, chat Twitch
    print("Detection des sons (rires, cris, explosions, tirs)...")
    events = signals.audio_events(wav, out)
    merged = signals.merge_events(events)
    print(f"{len(merged)} evenements sonores")
    print("Detection des changements de plan...")
    sc = signals.scenes(video, out)
    print(f"{len(sc['cuts'])} changements de plan, {len(sc['black'])} passages noirs")
    msgs = []
    if args.chat:
        msgs = signals.chat(args.chat, out)
    elif os.path.exists(os.path.join(out, "chat.json")):
        with open(os.path.join(out, "chat.json"), encoding="utf-8") as f:
            msgs = json.load(f)

    # 6. score : pics de volume (vs moyenne locale) + mots-cles + debit de parole + signaux
    win = int(60 / BIN)
    kernel = np.ones(win) / win
    local = np.convolve(db, kernel, mode="same")
    excess = np.clip(db - local, 0, None)

    ev_score = np.zeros(nb)
    for e in events:
        i = int(e["t"] / BIN)
        if 0 <= i < nb:
            ev_score[i] += signals.EVENT_GROUPS[e["type"]][1] * e["p"]

    cut_density = np.zeros(nb)
    for t in sc["cuts"]:
        i = int(t / BIN)
        if 0 <= i < nb:
            cut_density[i] += 1
    cut_density = np.convolve(cut_density, np.ones(int(6 / BIN)), mode="same")  # coupes sur 6 s

    chat_z = np.zeros(nb)
    if msgs:
        act, laugh = signals.chat_curve(msgs, nb, BIN)
        act = np.convolve(act + 2 * laugh, np.ones(int(5 / BIN)), mode="same")
        base = np.convolve(act, np.ones(int(120 / BIN)) / int(120 / BIN), mode="same")
        chat_z = np.clip((act - base) / (np.sqrt(base) + 1), 0, None)
        print(f"Chat : {len(msgs)} messages pris en compte")

    speech = np.zeros(nb)
    kw = np.zeros(nb)
    pat = re.compile("|".join(re.escape(k) for k in KEYWORDS), re.I)
    for c in caps:
        a, b = int(c["start"] / 1000 / BIN), int(c["end"] / 1000 / BIN) + 1
        speech[a:b] += 1
        hits = len(pat.findall(c["text"]))
        if "!" in c["text"] or "?" in c["text"]:
            hits += 0.5
        kw[a:b] += hits
    score = (excess + 2.0 * kw + 0.5 * np.minimum(speech, 2) + ev_score
             + 0.4 * np.clip(cut_density - 2, 0, None) + 3.0 * chat_z)
    smooth = np.convolve(score, np.ones(int(6 / BIN)) / int(6 / BIN), mode="same")

    # liste lisible des evenements sonores (avec ce qui se dit autour)
    with open(os.path.join(out, "events.txt"), "w", encoding="utf-8") as f:
        for s, e, ty, p in merged:
            near = " ".join(c["text"] for c in caps if (s - 4) * 1000 <= c["start"] <= (e + 2) * 1000)
            f.write(f"[{fmt(s)}] {ty:11s} {p:.2f}  {near}\n")
        for a, b in sc["black"]:
            f.write(f"[{fmt(a)}] ecran noir  {b - a:.1f} s\n")

    # selection gloutonne de pics separes d'au moins 45 s
    order = np.argsort(-smooth)
    picked = []
    for i in order:
        t = i * BIN
        if t < 5 or t > dur - 5:
            continue
        if all(abs(t - p) > 45 for p in picked):
            picked.append(t)
        if len(picked) >= args.top:
            break

    with open(os.path.join(out, "candidates.txt"), "w", encoding="utf-8") as f:
        for rank, t in enumerate(picked, 1):
            s, e = max(0, t - 20), min(dur, t + 12)
            text = " ".join(c["text"].strip() for c in caps if s * 1000 <= c["start"] <= e * 1000)
            a, b = int(s / BIN), int(e / BIN)
            why = []
            types = sorted({ty for (es, ee, ty, p) in merged if s <= es <= e})
            if types:
                why.append("sons: " + ", ".join(types))
            if msgs and chat_z[a:b].max() > 1.5:
                why.append(f"chat x{chat_z[a:b].max():.1f}")
            if excess[a:b].max() > 8:
                why.append("pic de volume")
            if cut_density[a:b].max() >= 6:
                why.append("montage rapide")
            if kw[a:b].sum() >= 3:
                why.append("mots-cles")
            f.write(f"#{rank}  pic {fmt(t)}  fenetre {fmt(s)} -> {fmt(e)}  score {smooth[int(t / BIN)]:.1f}"
                    f"  [{' | '.join(why) or '-'}]\n")
            f.write(f"    {text}\n\n")
    print(f"OK -> {out}")
    print("Fichiers : transcript.txt, candidates.txt, events.txt, scenes.json, frame.jpg")


if __name__ == "__main__":
    main()
