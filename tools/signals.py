"""Signaux pour reperer les moments forts : sons (YAMNet), changements de plan (ffmpeg), chat Twitch.

Chaque fonction ecrit un fichier JSON dans le dossier work/<video>/ et le reutilise s'il existe deja.
Utilisable seul :
    python tools/signals.py events  work/<video>/audio.wav
    python tools/signals.py scenes  input/<video>.mp4 work/<video>
    python tools/signals.py chat    https://www.twitch.tv/videos/123456789 work/<video>
    python tools/signals.py chat    chemin/chat.json work/<video>      (export TwitchDownloader ou chat-downloader)
"""
import csv
import json
import os
import re
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YAMNET = os.path.join(ROOT, "models", "yamnet.onnx")
YAMNET_CLASSES = os.path.join(ROOT, "models", "yamnet_class_map.csv")

# groupes de sons utiles -> regex sur les noms de classes AudioSet, poids dans le score
EVENT_GROUPS = {
    "rire": (r"laugh|giggle|snicker|chuckle|chortle", 3.0),
    "cri": (r"^shout$|^yell$|screaming|children shouting|battle cry|whoop", 3.0),
    "acclamation": (r"cheering|applause|crowd", 2.0),
    "explosion": (r"explosion|^boom$|eruption", 2.0),
    "tir": (r"gunshot|machine gun|fusillade|artillery|cap gun", 1.5),
    "crash": (r"smash, crash|breaking|shatter|^bang$", 1.5),
    "sirene": (r"siren", 1.0),
    "pneus": (r"skidding|tire squeal", 1.0),
}
EVENT_THRESHOLD = 0.25
HOP = 0.48  # secondes entre deux trames YAMNet


def _ffmpeg(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- sons
def audio_events(wav_path, out_dir):
    """Detecte rires, cris, explosions, tirs... -> events.json : [{t, type, p}] (t en s)."""
    path = os.path.join(out_dir, "events.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    import onnxruntime as ort

    names = []
    with open(YAMNET_CLASSES, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.append(row["display_name"])
    groups = {g: [i for i, n in enumerate(names) if re.search(rx, n, re.I)] for g, (rx, _) in EVENT_GROUPS.items()}

    sess = ort.InferenceSession(YAMNET, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        total = w.getnframes()
        chunk = sr * 60  # 1 min par passe
        events, pos = [], 0
        while pos < total:
            data = np.frombuffer(w.readframes(chunk), dtype=np.int16).astype(np.float32) / 32768.0
            if len(data) < sr:
                break
            scores = sess.run(None, {inp: data})[0]  # [trames, 521]
            for fi, row in enumerate(scores):
                t = pos / sr + fi * HOP
                for g, idx in groups.items():
                    p = float(row[idx].max()) if idx else 0.0
                    if p >= EVENT_THRESHOLD:
                        events.append({"t": round(t, 2), "type": g, "p": round(p, 2)})
            pos += len(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f)
    return events


def merge_events(events, gap=1.5):
    """Regroupe les trames consecutives d'un meme type -> [(debut, fin, type, p_max)]."""
    out = []
    for e in sorted(events, key=lambda e: (e["type"], e["t"])):
        if out and out[-1][2] == e["type"] and e["t"] - out[-1][1] <= gap:
            s, _, ty, p = out[-1]
            out[-1] = (s, e["t"] + HOP, ty, max(p, e["p"]))
        else:
            out.append((e["t"], e["t"] + HOP, e["type"], e["p"]))
    return sorted(out)


# ---------------------------------------------------------------- plans
def scenes(video, out_dir, threshold=0.3):
    """Changements de plan + passages noirs -> scenes.json {cuts: [t], black: [[debut, fin]]}."""
    path = os.path.join(out_dir, "scenes.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    vf = f"fps=15,scale=320:-2,blackdetect=d=0.4:pix_th=0.08,select='gt(scene\\,{threshold})',showinfo"
    r = _ffmpeg(["ffmpeg", "-hide_banner", "-i", video, "-an", "-vf", vf, "-f", "null", "-"])
    cuts = [round(float(m), 2) for m in re.findall(r"showinfo.*?pts_time:([\d.]+)", r.stderr)]
    black = [[round(float(a), 2), round(float(b), 2)]
             for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]
    data = {"cuts": cuts, "black": black}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------- chat Twitch
LAUGH_RX = re.compile(r"kekw|omegalul|lul\b|lol\b|mdr|ptdr|xd\b|jpp|😂|🤣|hahaha|ahahah|icant|lmao", re.I)
HYPE_RX = re.compile(r"pog|poggers|\bw\b|wtf|omg|😱|🔥|gg\b|insane|dingue|ouf|\?\?\?|!!!", re.I)
CHAT_DELAY = 6.0  # le chat reagit ~6 s apres l'action


def _load_chat_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    msgs = []
    if isinstance(data, dict) and "comments" in data:  # TwitchDownloader
        for c in data["comments"]:
            body = c.get("message", {}).get("body", "")
            msgs.append((float(c.get("content_offset_seconds", 0)), body))
    elif isinstance(data, list):  # chat-downloader
        for c in data:
            if "time_in_seconds" in c:
                msgs.append((float(c["time_in_seconds"]), c.get("message", "")))
    return msgs


TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # client public du site twitch.tv


def _gql(query):
    import time
    import urllib.request
    req = urllib.request.Request(TWITCH_GQL, data=json.dumps({"query": query}).encode(),
                                 headers={"Client-Id": TWITCH_CLIENT_ID, "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(2 ** attempt)
    raise SystemExit("Twitch ne repond pas")


def _download_twitch_chat(url):
    """Replay du chat d'une VOD Twitch via l'API publique du site (pagination par curseur)."""
    m = re.search(r"videos/(\d+)", url) or re.fullmatch(r"(\d+)", url)
    if not m:
        raise SystemExit("Lien VOD Twitch attendu : https://www.twitch.tv/videos/123456789")
    vid = m.group(1)
    print("Telechargement du chat Twitch...")
    # la pagination par curseur est refusee sans jeton : on avance par decalage (secondes) et on dedoublonne
    msgs, seen, offset, last_print = [], set(), 0, 0
    while True:
        q = ('{ video(id:"%s") { comments(contentOffsetSeconds:%d) { edges { node { id contentOffsetSeconds '
             'message { fragments { text } } } } pageInfo { hasNextPage } } } }') % (vid, offset)
        data = (_gql(q).get("data") or {}).get("video")
        if not data or not data.get("comments"):
            break
        edges = data["comments"]["edges"]
        new = 0
        for e in edges:
            n = e["node"]
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            new += 1
            text = "".join(f.get("text", "") for f in (n.get("message") or {}).get("fragments", []))
            msgs.append((float(n["contentOffsetSeconds"]), text))
        if not edges or not data["comments"]["pageInfo"]["hasNextPage"]:
            break
        last = int(edges[-1]["node"]["contentOffsetSeconds"])
        offset = last if (new and last > offset) else offset + 1
        if msgs and msgs[-1][0] - last_print > 600:
            last_print = msgs[-1][0]
            print(f"  {int(last_print // 60)} min, {len(msgs)} messages")
    return msgs


def chat(source, out_dir):
    """Telecharge (URL) ou lit (fichier) le chat -> chat.json [[t, message]]."""
    path = os.path.join(out_dir, "chat.json")
    if os.path.exists(source):
        msgs = _load_chat_file(source)
    else:
        msgs = _download_twitch_chat(source)
    msgs.sort()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False)
    print(f"{len(msgs)} messages de chat")
    return msgs


def chat_curve(msgs, nbins, binsize):
    """Activite du chat par tranche (messages + bonus rires/hype), recalee du delai du chat."""
    act = np.zeros(nbins)
    laugh = np.zeros(nbins)
    for t, body in msgs:
        i = int((t - CHAT_DELAY) / binsize)
        if 0 <= i < nbins:
            act[i] += 1
            if LAUGH_RX.search(body):
                laugh[i] += 1
            elif HYPE_RX.search(body):
                act[i] += 0.5
    return act, laugh


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "events":
        ev = merge_events(audio_events(sys.argv[2], os.path.dirname(sys.argv[2])))
        for s, e, ty, p in ev:
            print(f"{s:8.1f}-{e:8.1f}  {ty:12s} {p:.2f}")
    elif what == "scenes":
        d = scenes(sys.argv[2], sys.argv[3])
        print(f"{len(d['cuts'])} changements de plan, {len(d['black'])} passages noirs")
    elif what == "chat":
        chat(sys.argv[2], sys.argv[3])
