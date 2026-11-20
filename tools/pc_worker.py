"""Worker du PC : fait sur le GPU les taches lourdes demandees par l'appli du VPS (transcription Whisper, rendus).

Usage :
    python tools/pc_worker.py                  # tourne en continu (lance a l'ouverture de session, voir lancer_worker.bat)
    python tools/pc_worker.py --once           # une seule tache puis s'arrete
Config : data/worker.json  {"url": "https://clips.dossierminute.fr", "token": "..."}
         (jeton : sur le VPS, « python tools/app.py --worker-token »)

Taches :
  analyze  : recupere la video courte du live (input/<projet>.mp4) + son chat, ou telecharge la VOD Twitch,
             lance analyze.py, renvoie candidates.txt / transcript / frame.jpg au VPS.
  render   : recupere clips/<projet>.json, lance render.py + check_sync.py sur un clip, renvoie le mp4 au VPS.
Les videos sources restent sur le PC (input/) pour les re-rendus.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "data", "worker.json")
PY = sys.executable
NAME = platform.node() or "PC"


class Api:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def req(self, method, path, body=None, ctype="application/json", timeout=60):
        data = json.dumps(body).encode() if ctype == "application/json" and body is not None else body
        r = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "X-Worker-Name": NAME, "Content-Type": ctype,
            "User-Agent": "clips-worker"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read()

    def json(self, method, path, body=None):
        return json.loads(self.req(method, path, body) or b"{}")

    def stat(self, rel):
        return self.json("GET", "/api/worker/stat?path=" + urllib.parse.quote(rel))

    def download(self, rel, dest=None):
        dest = dest or os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        r = urllib.request.Request(self.url + "/api/worker/file?path=" + urllib.parse.quote(rel),
                                   headers={"Authorization": "Bearer " + self.token, "X-Worker-Name": NAME,
                                            "User-Agent": "clips-worker"})
        tmp = dest + ".dl"
        with urllib.request.urlopen(r, timeout=120) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if total and got != total:
            os.remove(tmp)
            raise IOError(f"telechargement incomplet : {rel}")
        os.replace(tmp, dest)
        return dest

    def upload(self, rel):
        path = os.path.join(ROOT, rel)
        size = os.path.getsize(path)
        r = urllib.request.Request(self.url + "/api/worker/file?path=" + urllib.parse.quote(rel), method="POST",
                                   data=open(path, "rb"), headers={
                                       "Authorization": "Bearer " + self.token, "X-Worker-Name": NAME,
                                       "Content-Type": "application/octet-stream", "Content-Length": str(size),
                                       "User-Agent": "clips-worker"})
        with urllib.request.urlopen(r, timeout=600) as resp:
            resp.read()


class Task:
    """Log envoye au VPS par paquets (toutes les 3 s) pour suivre la tache depuis l'appli."""

    def __init__(self, api, t):
        self.api, self.t, self.buf, self.lock = api, t, "", threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self.flusher, daemon=True).start()

    def log(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()
        with self.lock:
            self.buf += text

    def flush(self, **extra):
        with self.lock:
            log, self.buf = self.buf, ""
        for attempt in range(5):
            try:
                self.api.json("POST", "/api/worker/update", {"id": self.t["id"], "log": log, **extra})
                return
            except (urllib.error.URLError, OSError):
                time.sleep(3 * (attempt + 1))

    def flusher(self):
        while not self.stop.wait(3):
            self.flush()

    def finish(self, ok, **extra):
        self.stop.set()
        self.flush(status="fini" if ok else "erreur", **extra)

    def run(self, cmd):
        self.log("\n$ " + " ".join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)) + "\n")
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
        bad = False
        for line in p.stdout:
            self.log(line)
            bad = bad or line.startswith("ECHEC")
        p.wait()
        if p.returncode != 0 or bad:
            raise RuntimeError(f"echec de {os.path.basename(cmd[1])}")


def need(api, task, rel, optional=False):
    """Copie locale a jour d'un fichier du VPS (meme taille = deja la)."""
    st = api.stat(rel)
    local = os.path.join(ROOT, rel)
    if not st.get("exists"):
        if optional or os.path.exists(local):
            return os.path.exists(local)
        raise FileNotFoundError(f"{rel} absent du VPS et du PC")
    if os.path.exists(local) and os.path.getsize(local) == st["size"]:
        return True
    task.log(f"Telechargement de {rel} ({st['size'] / 1e6:.0f} Mo)...\n")
    api.download(rel)
    return True


def send(api, task, rels):
    for rel in rels:
        if os.path.exists(os.path.join(ROOT, rel)):
            api.upload(rel)
    task.log(f"Envoye au VPS : {', '.join(os.path.basename(r) for r in rels if os.path.exists(os.path.join(ROOT, r)))}\n")


def do_analyze(api, task, t):
    p = t["project"]
    video = f"input/{p}.mp4"
    have = need(api, task, video, optional=True)
    need(api, task, f"input/{p}.meta.json", optional=True)
    chat_local = need(api, task, f"work/{p}/chat.json", optional=True)
    if not have:
        if not t.get("vod"):
            raise FileNotFoundError("ni video courte sur le VPS, ni lien de VOD")
        cmd = [PY, "tools/twitch_vod.py", t["vod"], video]
        if t.get("start"):
            cmd += ["--start", t["start"]]
        if t.get("end"):
            cmd += ["--end", t["end"]]
        task.run(cmd)
    cmd = [PY, "tools/analyze.py", video]
    if t.get("vod") and not chat_local:
        cmd += ["--chat", t["vod"]]
    task.run(cmd)
    send(api, task, [f"work/{p}/{f}" for f in ("candidates.txt", "transcript.txt", "events.txt", "frame.jpg",
                                               "chat.json", "facecam.json")])


def do_render(api, task, t):
    cfg_rel = f"clips/{t['config']}"
    api.download(cfg_rel)  # toujours la derniere version (hook / temps modifies dans l'appli)
    with open(os.path.join(ROOT, cfg_rel), encoding="utf-8") as f:
        cfg = json.load(f)
    sub = os.path.splitext(os.path.basename(cfg["source"]))[0]
    need(api, task, cfg["source"])
    caps = cfg.get("captions")
    if caps and not os.path.exists(os.path.join(ROOT, caps)):
        task.log("Transcription absente sur ce PC : analyse d'abord\n")
        task.run([PY, "tools/analyze.py", cfg["source"]])
    name = t["clip"]
    task.run([PY, "tools/render.py", cfg_rel, "--only", name])
    task.run([PY, "tools/check_sync.py", cfg_rel, "--only", name])
    sync = {}
    try:
        with open(os.path.join(ROOT, "work", "_render", "sync.json"), encoding="utf-8") as f:
            allsync = json.load(f)
        for k in (f"{sub}/{name}", name):
            if k in allsync:
                sync[k] = allsync[k]
    except (OSError, ValueError):
        pass
    send(api, task, [f"output/{sub}/{name}.mp4", f"work/_render/{sub}/{name}_sync.png",
                     f"work/{sub}/facecam.json", f"work/{sub}/facecam_check.jpg"])
    return {"sync": sync}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    try:
        with open(CONF, encoding="utf-8") as f:
            conf = json.load(f)
    except (OSError, ValueError):
        raise SystemExit(f"Config manquante : {CONF}\n"
                         '{"url": "https://clips.dossierminute.fr", "token": "<python tools/app.py --worker-token sur le VPS>"}')
    api = Api(conf["url"], conf["token"])
    print(f"Worker {NAME} -> {api.url}  (Ctrl+C pour arreter)", flush=True)
    idle_msg = 0
    while True:
        try:
            t = api.json("GET", "/api/worker/next").get("task")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SystemExit("Jeton refuse par le VPS : verifier data/worker.json")
            print(f"VPS : erreur {e.code}", flush=True)
            time.sleep(30)
            continue
        except (urllib.error.URLError, OSError) as e:
            if time.time() - idle_msg > 600:
                print(f"VPS injoignable ({e}), nouvel essai...", flush=True)
                idle_msg = time.time()
            time.sleep(30)
            continue
        if not t:
            if args.once:
                return
            time.sleep(15)
            continue
        print(f"\n=== {t['kind']} {t['project']} {t.get('clip', '')} ===", flush=True)
        task = Task(api, t)
        try:
            extra = (do_analyze if t["kind"] == "analyze" else do_render)(api, task, t) or {}
            task.finish(True, **extra)
        except Exception as e:  # noqa: BLE001 - l'erreur part dans le log de la tache, le worker continue
            task.log(f"\nERREUR : {e}\n")
            task.finish(False)
        if args.once:
            return


if __name__ == "__main__":
    main()
