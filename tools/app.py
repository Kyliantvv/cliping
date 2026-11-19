"""Appli web locale pour gerer les clips TikTok (ce PC uniquement).

Usage :
    python tools/app.py            -> ouvre http://127.0.0.1:8765 dans le navigateur
    python tools/app.py --no-open  -> serveur seul

Ce qu'elle fait :
  - liste tous les clips des configs clips/*.json avec miniature, duree, hook, legende, verdict de synchro
  - lecteur video, bouton "copier la legende" (legende + source), ouvrir le dossier
  - suivi de publication : a poster / programme / poste, lien TikTok, vues, likes, notes
  - modifier le hook ou les bornes d'un clip et le re-rendre (+ verification de synchro) depuis l'appli
  - file d'attente de publication : le prochain clip a poster selon l'ordre conseille
Etat de suivi : data/state.json (pas de base de donnees).
"""
import argparse
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "tools", "app_web")
STATE = os.path.join(ROOT, "data", "state.json")
THUMBS = os.path.join(ROOT, "work", "_thumbs")
SYNC = os.path.join(ROOT, "work", "_render", "sync.json")
PY = sys.executable

_lock = threading.Lock()
_jobs = []          # [{id, key, kind, status, log, started, ended}]
_job_q = queue.Queue()
_dur_cache = {}


# ------------------------------------------------------------------ utilitaires
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def tsec(v):
    if isinstance(v, (int, float)):
        return float(v)
    p = [float(x) for x in str(v).split(":")]
    return sum(x * 60 ** i for i, x in enumerate(reversed(p)))


def duration(path):
    st = os.stat(path)
    k = (path, st.st_mtime)
    if k not in _dur_cache:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        try:
            _dur_cache[k] = round(float(r.stdout.strip()), 1)
        except ValueError:
            _dur_cache[k] = None
    return _dur_cache[k]


def configs():
    out = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "clips"))):
        if fn.endswith(".json") and fn != "exemple.json":
            cfg = load_json(os.path.join(ROOT, "clips", fn), None)
            if cfg and cfg.get("clips"):
                out.append((fn, cfg))
    return out


def src_name(cfg):
    return os.path.splitext(os.path.basename(cfg["source"]))[0]


def find_video(sub, name):
    for d in (os.path.join("output", sub), "output", os.path.join("output", "avant")):
        p = os.path.join(ROOT, d, name + ".mp4")
        if os.path.exists(p):
            return p
    return None


def parse_legendes(sub):
    """legendes.txt -> {nom: {caption, source}} + ordre conseille + nom du createur."""
    path = None
    for d in (os.path.join("output", sub), "output"):
        p = os.path.join(ROOT, d, "legendes.txt")
        if os.path.exists(p):
            path = p
            break
    res, order, creator = {}, [], ""
    if not path:
        return res, order, creator
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f]
    if lines:
        m = re.search(r"—\s*(?:source\s*:\s*)?([^—(]+?)\s*(?:\(|—|$)", lines[0])
        creator = m.group(1).strip() if m else ""
    for l in lines[:4]:
        m = re.match(r"Ordre[^:]*:\s*(.+)", l)
        if m:
            order = re.findall(r"\b(\d{2})\b", m.group(1))
    i = 0
    while i < len(lines):
        m = re.match(r"(\S+)\.mp4\b", lines[i])
        if m:
            cap = lines[i + 1] if i + 1 < len(lines) else ""
            srcl = lines[i + 2] if i + 2 < len(lines) and lines[i + 2].startswith("Source") else ""
            res[m.group(1)] = {"caption": cap, "source": srcl}
            i += 3
        else:
            i += 1
    return res, order, creator


def thumb(path, key):
    os.makedirs(THUMBS, exist_ok=True)
    out = os.path.join(THUMBS, key.replace("/", "__") + ".jpg")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        # image a ~40 % : apres le hook, en plein dans l'action
        d = duration(path) or 10
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{d * 0.4:.1f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=360:-2", "-q:v", "4", out])
    return out


def all_clips():
    state = load_json(STATE, {})
    sync = load_json(SYNC, {})
    jobs_by_key = {}
    with _lock:
        for j in _jobs:
            if j["status"] in ("en attente", "en cours"):
                jobs_by_key[j["key"]] = j["status"]
    out = []
    for fn, cfg in configs():
        sub = src_name(cfg)
        leg, order, creator = parse_legendes(sub)
        for idx, c in enumerate(cfg["clips"]):
            name = c["name"]
            key = f"{sub}/{name}"
            vid = find_video(sub, name)
            st = state.get(key, {})
            num = name[:2]
            out.append({
                "key": key, "config": fn, "source": sub, "name": name, "creator": creator or sub,
                "hook": c.get("hook", ""), "start": c.get("start"), "end": c.get("end"),
                "outro": c.get("outro", ""), "zooms": c.get("zooms", []),
                "video": bool(vid), "duration": duration(vid) if vid else None,
                "mtime": os.path.getmtime(vid) if vid else None,
                "caption": leg.get(name, {}).get("caption", ""), "credit": leg.get(name, {}).get("source", ""),
                "order": order.index(num) if num in order else 99 + idx,
                "sync": sync.get(key) or sync.get(name),
                "status": st.get("status", "a_poster"), "url": st.get("url", ""), "views": st.get("views"),
                "likes": st.get("likes"), "notes": st.get("notes", ""), "posted_at": st.get("posted_at"),
                "scheduled": st.get("scheduled", ""), "job": jobs_by_key.get(key),
            })
    return out


# ------------------------------------------------------------------ rendus en arriere-plan
def worker():
    while True:
        job = _job_q.get()
        job["status"], job["started"] = "en cours", time.time()
        cfg_path = os.path.join("clips", job["config"])
        name = job["key"].split("/", 1)[1]
        log = []
        ok = True
        for cmd in ([PY, "tools/render.py", cfg_path, "--only", name],
                    [PY, "tools/check_sync.py", cfg_path, "--only", name]):
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            log.append((p.stdout or "")[-1500:] + (p.stderr or "")[-1500:])
            if p.returncode != 0 or "ECHEC" in (p.stdout or ""):
                ok = False
                break
        job["log"] = "\n".join(log)
        job["status"] = "fini" if ok else "erreur"
        job["ended"] = time.time()


def queue_render(config, key):
    job = {"id": int(time.time() * 1000), "key": key, "config": config, "status": "en attente", "log": "",
           "started": None, "ended": None}
    with _lock:
        _jobs.append(job)
        del _jobs[:-30]
    _job_q.put(job)
    return job


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype=None):
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = size - int(m.group(2))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def clip_from_key(self, key):
        for c in all_clips():
            if c["key"] == key:
                return c
        return None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            base = os.path.join(ROOT, "assets") if rel.startswith("fonts/") else WEB
            p = os.path.normpath(os.path.join(base, rel))
            if p.startswith(base) and os.path.isfile(p):
                return self.send_file(p)
            return self.send_error(404)
        if path == "/api/clips":
            return self.send_json({"clips": all_clips()})
        if path == "/api/jobs":
            with _lock:
                return self.send_json({"jobs": list(reversed(_jobs))})
        m = re.match(r"^/(video|thumb)/([\w\-]+)/([\w\-]+)$", path)
        if m:
            kind, sub, name = m.groups()
            vid = find_video(sub, name)
            if not vid:
                return self.send_error(404)
            if kind == "video":
                return self.send_file(vid, "video/mp4")
            return self.send_file(thumb(vid, f"{sub}/{name}"), "image/jpeg")
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json({"error": "JSON invalide"}, 400)
        path = urllib.parse.urlparse(self.path).path
        key = data.get("key", "")
        if path == "/api/state":
            with _lock:
                state = load_json(STATE, {})
                st = state.setdefault(key, {})
                for k in ("status", "url", "views", "likes", "notes", "scheduled"):
                    if k in data:
                        st[k] = data[k]
                if data.get("status") == "poste" and not st.get("posted_at"):
                    st["posted_at"] = time.strftime("%Y-%m-%d %H:%M")
                if data.get("status") in ("a_poster", "programme"):
                    st.pop("posted_at", None)
                save_json(STATE, state)
            return self.send_json({"ok": True, "state": st})
        if path == "/api/edit":
            # modifie hook / debut / fin / ecran de fin dans la config, puis relance le rendu
            clip = self.clip_from_key(key)
            if not clip:
                return self.send_json({"error": "clip inconnu"}, 404)
            cpath = os.path.join(ROOT, "clips", clip["config"])
            with _lock:
                cfg = load_json(cpath, None)
                c = next(x for x in cfg["clips"] if x["name"] == clip["name"])
                new = dict(c)
                for k in ("hook", "start", "end", "outro"):
                    if k in data:
                        v = str(data[k]).strip()
                        if v:
                            new[k] = v
                        elif k in ("start", "end"):
                            return self.send_json({"error": f"{k} obligatoire"}, 400)
                        else:
                            new.pop(k, None)
                try:
                    a, b = tsec(new["start"]), tsec(new["end"])
                except (ValueError, KeyError):
                    return self.send_json({"error": "temps invalide (format h:mm:ss)"}, 400)
                if b <= a:
                    return self.send_json({"error": "la fin doit etre apres le debut"}, 400)
                if b - a > 300:
                    return self.send_json({"error": "clip de plus de 5 min : verifier les temps"}, 400)
                c.clear()
                c.update(new)
                save_json(cpath, cfg)
            job = queue_render(clip["config"], key) if data.get("render", True) else None
            return self.send_json({"ok": True, "job": job})
        if path == "/api/render":
            clip = self.clip_from_key(key)
            if not clip:
                return self.send_json({"error": "clip inconnu"}, 404)
            return self.send_json({"ok": True, "job": queue_render(clip["config"], key)})
        if path == "/api/open":
            clip = self.clip_from_key(key)
            vid = clip and find_video(clip["source"], clip["name"])
            if not vid:
                return self.send_json({"error": "video introuvable"}, 404)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(vid)])
            return self.send_json({"ok": True})
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # local seulement
    url = f"http://127.0.0.1:{args.port}"
    print(f"Gestion des clips : {url}  (Ctrl+C pour arreter)")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
