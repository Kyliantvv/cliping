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
  - envoi d'un clip vers l'iPhone : copie dans le dossier iCloud Photos (iCloud pour Windows)
  - nouveau stream : lien Twitch -> telechargement + analyse en arriere-plan -> moments proposes,
    on coche, on ecrit le hook, on trace la facecam -> config + legendes + rendus
Etat : data/state.json, data/settings.json, data/projects.json (pas de base de donnees).
"""
import argparse
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web", "out")  # interface Next.js (export statique, voir web/)
STATE = os.path.join(ROOT, "data", "state.json")
THUMBS = os.path.join(ROOT, "work", "_thumbs")
SYNC = os.path.join(ROOT, "work", "_render", "sync.json")
SETTINGS = os.path.join(ROOT, "data", "settings.json")
PROJECTS = os.path.join(ROOT, "data", "projects.json")
PY = sys.executable

_lock = threading.Lock()
_jobs = []          # [{id, key, kind, status, log, started, ended}]
_job_q = queue.Queue()      # rendus
_import_q = queue.Queue()   # telechargement + analyse de streams
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
    for d in (os.path.join("output", sub),):
        p = os.path.join(ROOT, d, name + ".mp4")
        if os.path.exists(p):
            return p
    return None


def parse_legendes(sub):
    """legendes.txt -> {nom: {caption, source}} + ordre conseille + nom du createur."""
    path = None
    for d in (os.path.join("output", sub),):
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


def to_recycle_bin(paths):
    """Corbeille Windows plutot que suppression definitive (un seul PowerShell pour plusieurs fichiers)."""
    """Renvoie la liste des chemins qui n'ont pas pu partir (fichier ouvert ailleurs, ex. video en lecture)."""
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return []
    # chaque fichier est tente plusieurs fois : un mp4 encore lu par le navigateur se libere en 1 ou 2 s
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; $fs = [Microsoft.VisualBasic.FileIO.FileSystem]; "
          "foreach ($p in ($env:TRASH_PATHS -split \"`n\")) { if (-not $p) { continue }; $ok = $false; "
          "for ($i = 0; $i -lt 6 -and -not $ok; $i++) { try { "
          "if (-not (Test-Path -LiteralPath $p)) { $ok = $true } "
          "elseif (Test-Path -LiteralPath $p -PathType Container) { $fs::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin'); $ok = $true } "
          "else { $fs::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin'); $ok = $true } "
          "} catch { Start-Sleep -Milliseconds 700 } }; if (-not $ok) { Write-Output \"ECHEC|$p\" } }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "TRASH_PATHS": "\n".join(os.path.normpath(p) for p in paths)})
    return [l.split("|", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith("ECHEC|")]


def remove_legendes(sub, names):
    """Retire les blocs des clips supprimes de output/<sub>/legendes.txt et de la ligne 'Ordre conseille'."""
    path = os.path.join(ROOT, "output", sub, "legendes.txt")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, skip = [], False
    for l in lines:
        m = re.match(r"(\S+)\.mp4\b", l)
        if m and m.group(1) in names:
            skip = True
            continue
        if skip:
            if l.strip() == "":
                skip = False
            continue
        out.append(l)
    nums = {n[:2] for n in names}
    for i, l in enumerate(out[:4]):
        m = re.match(r"(Ordre[^:]*:\s*)(.*)", l)
        if m:
            keep = [x for x in re.findall(r"\b\d{2}\b", m.group(2)) if x not in nums]
            out[i] = m.group(1) + ", ".join(keep)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")


def delete_clips(clips):
    """Supprime vraiment des clips : mp4 a la corbeille, retires de clips/*.json, de legendes.txt et du suivi.
    Si un projet n'a plus aucun clip, son dossier output/<projet> et sa config partent aussi a la corbeille."""
    trash, by_cfg = [], {}
    for c in clips:
        by_cfg.setdefault(c["config"], []).append(c)
    emptied = []
    with _lock:
        for cfg_name, cs in by_cfg.items():
            cpath = os.path.join(ROOT, "clips", cfg_name)
            cfg = load_json(cpath, None)
            if not cfg:
                continue
            sub = src_name(cfg)
            names = {c["name"] for c in cs}
            for n in names:
                v = find_video(sub, n)
                if v:
                    trash.append(v)
                t = os.path.join(THUMBS, f"{sub}__{n}.jpg")
                if os.path.exists(t):
                    os.remove(t)  # simple cache
            cfg["clips"] = [x for x in cfg["clips"] if x["name"] not in names]
            if cfg["clips"]:
                save_json(cpath, cfg)
                remove_legendes(sub, names)
            else:
                emptied.append(sub)
                trash.append(cpath)
                odir = os.path.join(ROOT, "output", sub)
                if os.path.isdir(odir):
                    trash = [p for p in trash if not p.startswith(odir + os.sep)] + [odir]
        state = load_json(STATE, {})
        for c in clips:
            state.pop(c["key"], None)
        save_json(STATE, state)
    failed = to_recycle_bin(trash)
    return {"deleted": len(clips), "trashed": len(trash) - len(failed), "emptied": emptied,
            "locked": [os.path.relpath(p, ROOT) for p in failed]}


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
        cfg_mtime = os.path.getmtime(os.path.join(ROOT, "clips", fn))
        leg, order, creator = parse_legendes(sub)
        for idx, c in enumerate(cfg["clips"]):
            name = c["name"]
            key = f"{sub}/{name}"
            vid = find_video(sub, name)
            st = state.get(key, {})
            if st.get("hidden"):
                continue
            num = name[:2]
            out.append({
                "key": key, "config": fn, "source": sub, "name": name, "creator": creator or cfg.get("creator") or sub,
                "hook": c.get("hook", ""), "start": c.get("start"), "end": c.get("end"),
                "outro": c.get("outro", ""), "zooms": c.get("zooms", []),
                "video": bool(vid), "duration": duration(vid) if vid else None,
                "mtime": os.path.getmtime(vid) if vid else None,
                "date": os.path.getmtime(vid) if vid else cfg_mtime,  # tri du plus recent au plus ancien
                "caption": leg.get(name, {}).get("caption", ""), "credit": leg.get(name, {}).get("source", ""),
                "order": order.index(num) if num in order else 99 + idx,
                "sync": sync.get(key) or sync.get(name),
                "status": st.get("status", "a_poster"), "url": st.get("url", ""), "views": st.get("views"),
                "likes": st.get("likes"), "notes": st.get("notes", ""), "posted_at": st.get("posted_at"),
                "scheduled": st.get("scheduled", ""), "sent_at": st.get("sent_at"), "job": jobs_by_key.get(key),
            })
    return out


# ------------------------------------------------------------------ taches en arriere-plan
# deux files : les rendus (1 a 2 min) ne restent pas bloques derriere un import de stream (30 min et +)
def _log(job, text):
    job["log"] = (job["log"] + text)[-6000:]


def run_cmd(job, cmd):
    """Lance une commande, recopie sa sortie dans le log du job au fil de l'eau."""
    _log(job, "\n$ " + " ".join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)) + "\n")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
    out = []
    for line in p.stdout:
        out.append(line)
        _log(job, line)
    p.wait()
    if p.returncode != 0 or any(l.startswith("ECHEC") for l in out):
        raise RuntimeError(f"echec de {os.path.basename(cmd[1])}")


def worker(q):
    while True:
        job = q.get()
        job["status"], job["started"] = "en cours", time.time()
        try:
            for step in job.pop("_steps"):
                if callable(step):
                    step(job)
                else:
                    run_cmd(job, step)
            job["status"] = "fini"
        except Exception as e:  # noqa: BLE001 - toute erreur doit finir dans le log, pas tuer le worker
            _log(job, f"\nERREUR : {e}\n")
            job["status"] = "erreur"
        job["ended"] = time.time()


def add_job(q, kind, key, steps, **extra):
    job = {"id": int(time.time() * 1000), "kind": kind, "key": key, "status": "en attente", "log": "",
           "started": None, "ended": None, "_steps": steps, **extra}
    with _lock:
        _jobs.append(job)
        del _jobs[:-40]
    q.put(job)
    return public_job(job)


def public_job(job):
    return {k: v for k, v in job.items() if not k.startswith("_")}


def queue_render(config, key):
    cfg_path = os.path.join("clips", config)
    name = key.split("/", 1)[1]
    return add_job(_job_q, "render", key, [[PY, "tools/render.py", cfg_path, "--only", name],
                                           [PY, "tools/check_sync.py", cfg_path, "--only", name]], config=config)


# ------------------------------------------------------------------ envoi vers l'iPhone (iCloud Photos)
def settings():
    return load_json(SETTINGS, {})


def icloud_candidates():
    """Dossiers ou iCloud pour Windows envoie les fichiers vers Photos (selon la version)."""
    home = os.path.expanduser("~")
    c = [os.path.join(home, "Pictures", "iCloud Photos", "Photos"),  # iCloud pour Windows 14+ (synchro dans les 2 sens)
         os.path.join(home, "Pictures", "iCloud Photos", "Uploads"),
         os.path.join(home, "Pictures", "iCloud Photos"),
         os.path.join(home, "iCloud Photos", "Uploads"),
         os.path.join(home, "iCloudPhotos", "Uploads"),
         os.path.join(home, "iCloudPhotos")]
    return [p for p in c if os.path.isdir(p)]


def iphone_dir():
    d = settings().get("iphone_dir")
    if d and os.path.isdir(d):
        return d
    found = icloud_candidates()
    return found[0] if found else None


def send_to_iphone(key, vid):
    d = iphone_dir()
    if not d:
        raise FileNotFoundError("dossier iCloud Photos introuvable")
    sub, name = key.split("/", 1)
    dst = os.path.join(d, f"{sub}_{name}.mp4")
    # copie sans re-encodage + date de creation = maintenant : sans elle, Photos (iPhone) classe la video
    # a une date au hasard et on ne la trouve pas en haut de la galerie
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-map", "0", "-c", "copy",
                        "-metadata", f"creation_time={now}", "-movflags", "+faststart", "-f", "mp4", dst + ".part"],
                       capture_output=True, text=True)
    if r.returncode != 0:  # secours : simple copie
        shutil.copyfile(vid, dst + ".part")  # .part : iCloud n'envoie pas un fichier a moitie copie
    os.replace(dst + ".part", dst)
    with _lock:
        state = load_json(STATE, {})
        state.setdefault(key, {})["sent_at"] = time.strftime("%Y-%m-%d %H:%M")
        save_json(STATE, state)
    return dst


# ------------------------------------------------------------------ nouveaux streams : import, moments, creation
def slug(s, n=40):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:n].strip("_")


def fmt(t):
    t = max(0, int(round(t)))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def projects_meta():
    return load_json(PROJECTS, {})


def resolve_twitch(url):
    """Lien de chaine -> derniere VOD (live en cours compris). Lien de VOD -> tel quel."""
    url = url.strip()
    if re.search(r"twitch\.tv/videos/\d+", url):
        return url, None
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)/?$", url)
    if not m:
        raise ValueError("lien Twitch attendu : twitch.tv/videos/123... ou twitch.tv/chaine")
    login = m.group(1)
    r = subprocess.run([PY, "-m", "yt_dlp", "--flat-playlist", "--playlist-items", "1", "--print", "url",
                        f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time"],
                       capture_output=True, text=True, timeout=120)
    vod = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if "/videos/" not in vod:
        raise ValueError(f"aucune VOD publique trouvee pour {login}")
    return vod, login


def start_import(data):
    name = slug(data.get("name") or "", 50)
    creator = (data.get("creator") or "").strip()
    url = (data.get("url") or "").strip()
    if not name:
        raise ValueError("nom de projet manquant")
    if not creator:
        raise ValueError("nom du streamer manquant")
    if any(j["kind"] == "import" and j["key"] == name and j["status"] in ("en attente", "en cours") for j in _jobs):
        raise ValueError("import deja en cours pour ce projet")
    video = os.path.join("input", name + ".mp4")
    have_video = os.path.exists(os.path.join(ROOT, video))
    if not have_video and not url:
        raise ValueError("lien Twitch manquant (ou mettre input/" + name + ".mp4)")
    if url and not re.search(r"twitch\.tv/(videos/\d+|[A-Za-z0-9_]+/?$)", url):
        raise ValueError("lien Twitch attendu : twitch.tv/videos/123... ou twitch.tv/chaine")
    for k in ("start", "end"):
        if data.get(k):
            tsec(data[k])  # leve ValueError si invalide
    with _lock:
        meta = projects_meta()
        meta[name] = {**meta.get(name, {}), "creator": creator, "url": url, "created": time.strftime("%Y-%m-%d %H:%M")}
        save_json(PROJECTS, meta)

    def resolve(job):
        if not url:
            return
        vod, login = resolve_twitch(url)
        job["vod"] = vod
        _log(job, f"VOD : {vod}\n")
        with _lock:
            meta = projects_meta()
            meta[name]["vod"] = vod
            if login:
                meta[name]["channel"] = f"twitch.tv/{login}"
            save_json(PROJECTS, meta)

    def download(job):
        if have_video:
            _log(job, f"{video} deja present : pas de telechargement\n")
            return
        cmd = [PY, "tools/twitch_vod.py", job["vod"], video]
        for k in ("start", "end"):
            if data.get(k):
                cmd += [f"--{k}", str(data[k])]
        run_cmd(job, cmd)

    def analyze(job):
        cmd = [PY, "tools/analyze.py", video]
        if job.get("vod"):
            cmd += ["--chat", job["vod"]]
        run_cmd(job, cmd)

    return add_job(_import_q, "import", name, [resolve, download, analyze])


def parse_candidates(name):
    path = os.path.join(ROOT, "work", name, "candidates.txt")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"#(\d+)\s+pic (\S+)\s+fenetre (\S+) -> (\S+)\s+score ([\d.]+)\s+\[(.*)\]", l)
        if m:
            peak = tsec(m.group(2))
            text = lines[i + 1].strip() if i + 1 < len(lines) else ""
            why = [w.strip() for w in m.group(6).split("|") if w.strip() and w.strip() != "-"]
            # clip propose : ~65 s autour du pic (le rendu recale debut/fin sur des phrases completes)
            out.append({"rank": int(m.group(1)), "peak": fmt(peak), "score": float(m.group(5)), "why": why,
                        "text": text, "start": fmt(peak - 35), "end": fmt(peak + 30),
                        "hook": hook_suggestion(text)})
    return out


def hook_suggestion(text):
    """Premiere phrase courte du passage, en majuscules : point de depart a reecrire."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if len(p.strip()) > 12]
    s = (parts[0] if parts else text)[:60].rsplit(" ", 1)[0] if len(text) > 60 else (parts[0] if parts else text)
    return s.upper().strip(" .")


def project_info(name):
    meta = projects_meta().get(name, {})
    cfg = load_json(os.path.join(ROOT, "clips", name + ".json"), None)
    used = [[c["start"], c["end"], c["name"]] for c in (cfg or {}).get("clips", [])]
    fc = load_json(os.path.join(ROOT, "work", name, "facecam.json"), {})
    leg_creator = parse_legendes(name)[2]
    return {
        "name": name, "creator": meta.get("creator") or (cfg or {}).get("creator") or leg_creator or name,
        "url": meta.get("url", ""), "channel": meta.get("channel", ""), "vod": meta.get("vod", ""),
        "video": os.path.exists(os.path.join(ROOT, "input", name + ".mp4")),
        "analyzed": os.path.exists(os.path.join(ROOT, "work", name, "candidates.txt")),
        "frame": os.path.exists(os.path.join(ROOT, "work", name, "frame.jpg")),
        "facecam": (cfg or {}).get("facecam") if isinstance((cfg or {}).get("facecam"), list) else fc.get("rect"),
        "layout": (cfg or {}).get("layout", "split"), "used": used,
    }


def all_projects():
    names = set(projects_meta())
    wdir = os.path.join(ROOT, "work")
    for n in os.listdir(wdir) if os.path.isdir(wdir) else []:
        if os.path.exists(os.path.join(wdir, n, "candidates.txt")):
            names.add(n)
    hidden = settings().get("hidden_projects", [])
    return [project_info(n) for n in sorted(names) if n not in hidden]


def create_clips(data):
    name = slug(data.get("name") or "", 50)
    info = project_info(name)
    if not info["video"]:
        raise ValueError("video source absente")
    creator = (data.get("creator") or info["creator"]).strip()
    layout = data.get("layout") or "split"
    fc = data.get("facecam")
    if layout == "split":
        if not (isinstance(fc, list) and len(fc) == 4 and all(isinstance(v, (int, float)) for v in fc)
                and fc[2] >= 40 and fc[3] >= 40):
            raise ValueError("tracer le rectangle de la facecam (ou choisir 'pas de facecam')")
        fc = [int(round(v)) for v in fc]
    wanted = data.get("clips") or []
    if not wanted:
        raise ValueError("aucun moment choisi")
    clean = []
    for w in wanted:
        a, b = tsec(w["start"]), tsec(w["end"])
        hook = re.sub(r"\s+", " ", str(w.get("hook", ""))).strip().upper()
        if b <= a or b - a > 180:
            raise ValueError(f"temps invalides pour « {hook or w['start']} » (fin apres debut, 3 min max)")
        if not hook:
            raise ValueError(f"hook manquant pour le moment de {w['start']}")
        if len(hook) > 70:
            raise ValueError(f"hook trop long (70 caracteres max) : {hook[:30]}...")
        clean.append((fmt(a), fmt(b), hook, str(w.get("caption", "")).strip()))

    cpath = os.path.join(ROOT, "clips", name + ".json")
    with _lock:
        cfg = load_json(cpath, None) or {
            "source": f"input/{name}.mp4", "captions": f"work/{name}/captions.json",
            "cam_height": 640, "jumpcut": True, "fixes": {"GTA 6": "GTA VI"},
            "highlight": ["gta", "vice city"], "clips": []}
        cfg["creator"] = creator
        cfg["layout"] = layout
        if layout == "split":
            cfg["facecam"] = fc
        else:
            cfg.pop("facecam", None)
        nums = [int(c["name"][:2]) for c in cfg["clips"] if c["name"][:2].isdigit()]
        n = max(nums, default=0)
        new = []
        for a, b, hook, cap in clean:
            n += 1
            clip_name = f"{n:02d}_{slug(hook, 24) or 'clip'}"
            cfg["clips"].append({"name": clip_name, "start": a, "end": b, "hook": hook})
            new.append((clip_name, a, b, hook, cap))
        save_json(cpath, cfg)
        write_legendes(name, creator, info, new)
    return [queue_render(name + ".json", f"{name}/{c[0]}") for c in new]


def write_legendes(name, creator, info, new):
    """Ajoute les legendes des nouveaux clips a output/<nom>/legendes.txt (format lu par parse_legendes)."""
    d = os.path.join(ROOT, "output", name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "legendes.txt")
    where = info.get("channel") or info.get("url") or "Twitch"
    tag = slug(creator, 30).replace("_", "")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read().rstrip("\n") + "\n\n"
    else:
        text = (f"LÉGENDES TIKTOK — {creator} ({where}) — créditer {creator} dans chaque légende\n"
                f"Ordre conseillé : \n\n")
    for clip_name, a, b, hook, cap in new:
        cap = cap or f"{hook[0] + hook[1:].lower()} 😳 #{tag} #gta6 #gtavi #twitch #clip"
        text += f"{clip_name}.mp4  (~{int(tsec(b) - tsec(a))} s)\n{cap}\nSource : {creator} ({where})\n\n"
    nums = re.findall(r"^(\d{2})_\S+\.mp4", text, re.M)
    text = re.sub(r"^Ordre conseillé :.*$", "Ordre conseillé : " + ", ".join(nums), text, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


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
        if path.startswith("/static/fonts/"):
            base = os.path.join(ROOT, "assets", "fonts")
            p = os.path.normpath(os.path.join(base, path[len("/static/fonts/"):]))
            if p.startswith(base + os.sep) and os.path.isfile(p):
                return self.send_file(p)
            return self.send_error(404)
        if path == "/api/clips":
            return self.send_json({"clips": all_clips()})
        if path == "/api/jobs":
            with _lock:
                return self.send_json({"jobs": [public_job(j) for j in reversed(_jobs)]})
        if path == "/api/projects":
            return self.send_json({"projects": all_projects()})
        m = re.match(r"^/api/projects/([\w\-]+)/candidates$", path)
        if m:
            return self.send_json({"project": project_info(m.group(1)), "candidates": parse_candidates(m.group(1))})
        if path == "/api/settings":
            return self.send_json({"iphone_dir": iphone_dir(), "saved": settings().get("iphone_dir"),
                                   "detected": icloud_candidates()})
        m = re.match(r"^/(source|frame)/([\w\-]+)$", path)
        if m:
            kind, name = m.groups()
            p = (os.path.join(ROOT, "input", name + ".mp4") if kind == "source"
                 else os.path.join(ROOT, "work", name, "frame.jpg"))
            if not os.path.exists(p):
                return self.send_error(404)
            return self.send_file(p, "video/mp4" if kind == "source" else "image/jpeg")
        m = re.match(r"^/(video|thumb)/([\w\-]+)/([\w\-]+)$", path)
        if m:
            kind, sub, name = m.groups()
            vid = find_video(sub, name)
            if not vid:
                return self.send_error(404)
            if kind == "video":
                return self.send_file(vid, "video/mp4")
            return self.send_file(thumb(vid, f"{sub}/{name}"), "image/jpeg")
        # interface Next.js exportee (web/out)
        if not os.path.isdir(WEB):
            body = "Interface pas encore construite : lancer « cd web && npm install && npm run build »".encode()
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        rel = path.lstrip("/")
        for cand in (rel, rel + ".html", os.path.join(rel, "index.html")):
            p = os.path.normpath(os.path.join(WEB, cand))
            if (p == WEB or p.startswith(WEB + os.sep)) and os.path.isfile(p):
                ctype = "text/html; charset=utf-8" if p.endswith(".html") else None
                return self.send_file(p, ctype)
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
        if path == "/api/delete":
            # supprime pour de bon (fichiers a la corbeille Windows, donc recuperables)
            keys = data.get("keys") or ([key] if key else [])
            if not isinstance(keys, list) or not keys or len(keys) > 500:
                return self.send_json({"error": "aucun clip"}, 400)
            index = {c["key"]: c for c in all_clips()}
            clips = [index.get(k) for k in keys]
            if not all(clips):
                return self.send_json({"error": "clip inconnu"}, 404)
            return self.send_json({"ok": True, **delete_clips(clips)})
        if path == "/api/send":
            clip = self.clip_from_key(key)
            vid = clip and find_video(clip["source"], clip["name"])
            if not vid:
                return self.send_json({"error": "video introuvable"}, 404)
            try:
                dst = send_to_iphone(key, vid)
            except FileNotFoundError as e:
                return self.send_json({"error": str(e), "setup": True}, 409)
            return self.send_json({"ok": True, "path": dst})
        if path == "/api/settings":
            d = str(data.get("iphone_dir") or "").strip().strip('"')
            if d and not os.path.isdir(d):
                return self.send_json({"error": "dossier introuvable"}, 400)
            with _lock:
                s = settings()
                if d:
                    s["iphone_dir"] = d
                else:
                    s.pop("iphone_dir", None)
                save_json(SETTINGS, s)
            return self.send_json({"ok": True, "iphone_dir": iphone_dir()})
        if path == "/api/import":
            try:
                return self.send_json({"ok": True, "job": start_import(data)})
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if path == "/api/create":
            try:
                return self.send_json({"ok": True, "jobs": create_clips(data)})
            except (ValueError, KeyError) as e:
                return self.send_json({"error": str(e)}, 400)
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
    threading.Thread(target=worker, args=(_job_q,), daemon=True).start()
    threading.Thread(target=worker, args=(_import_q,), daemon=True).start()
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
