"""Surveille des chaines Twitch et enregistre leurs lives GTA VI, puis prepare les moments forts (VPS, sans GPU).

Usage :
    python tools/watcher.py            # tourne en continu (service systemd sur le VPS)
    python tools/watcher.py --once     # un seul tour : affiche qui est en live et dans quelle categorie

Liste des chaines : data/watchlist.json (modifiable dans l'appli, onglet « Streamers suivis ») :
    {"streamers": ["jltomy", "squeezie"], "categories": ["Grand Theft Auto VI"]}
Relue a chaque tour : pas besoin de redemarrer quoi que ce soit.

Fonctionnement :
  1. Toutes les 60 s : qui est en live, dans quelle categorie (API publique Twitch, sans compte).
  2. Live dans une categorie suivie -> enregistrement 720p (streamlink, pubs retirees) par morceaux de 30 min,
     + chat en direct (IRC anonyme). Arret apres 5 min hors ligne ou hors categorie.
  3. Chaque morceau fini : volume + sons (YAMNet) + pics du chat -> meilleurs moments -> fenetres de ~3 min
     decoupees sans re-encodage. Le morceau est ensuite supprime (disque du VPS limite).
  4. Toutes les 3 h de live (ou a la fin) : les fenetres sont collees en une video courte input/<projet>.mp4,
     avec son chat recale (work/<projet>/chat.json), et une tache "analyse" part au PC (Whisper sur GPU).
Etat en direct : data/live.json (lu par l'appli). Journal : stdout (journalctl -u clips-watcher).
"""
import argparse
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals  # noqa: E402
import tasks  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WATCHLIST = os.path.join(DATA, "watchlist.json")
LIVE = os.path.join(DATA, "live.json")
PROJECTS = os.path.join(DATA, "projects.json")
REC = os.path.join(ROOT, "rec")
PY = sys.executable

DEFAULTS = {
    "streamers": ["jltomy", "squeezie"],
    "categories": ["Grand Theft Auto VI"],
    "quality": "720p60,720p,720p30,best",
    "chunk_minutes": 30,         # taille des morceaux d'enregistrement
    "windows_per_chunk": 3,      # moments gardes par morceau de 30 min
    "window_before": 100,        # secondes gardees avant le pic
    "window_after": 80,          # et apres
    "batch_hours": 3,            # une video courte (= un projet) toutes les 3 h de live
    "grace_minutes": 5,          # hors ligne / hors categorie depuis 5 min -> fin de l'enregistrement
    "min_free_gb": 4,            # en dessous : on arrete d'enregistrer
    "keep_days": 3,              # videos courtes deja analysees par le PC : supprimees du VPS apres 3 j
}
POLL = 60
BIN = 0.5
SEP = 180  # ecart mini entre deux moments gardes (s)


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


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


def config():
    cfg = {**DEFAULTS, **load_json(WATCHLIST, {})}
    cfg["streamers"] = [s.strip().lower() for s in cfg["streamers"] if re.fullmatch(r"[A-Za-z0-9_]{2,25}", s.strip())]
    return cfg


def free_gb():
    return shutil.disk_usage(ROOT).free / 1e9


# ------------------------------------------------------------------ Twitch : qui est en live ?
def live_status(logins):
    """{login: {display, live, game, title, stream_id, started}} pour toutes les chaines en une requete."""
    if not logins:
        return {}
    q = " ".join(f'u{i}: user(login:"{l}") {{ login displayName stream {{ id title createdAt type game {{ name }} }} }}'
                 for i, l in enumerate(logins))
    data = (signals._gql("{ " + q + " }") or {}).get("data") or {}
    out = {}
    for i, l in enumerate(logins):
        u = data.get(f"u{i}")
        s = (u or {}).get("stream")
        out[l] = {"display": (u or {}).get("displayName") or l, "exists": bool(u), "live": bool(s and s.get("type") == "live"),
                  "game": ((s or {}).get("game") or {}).get("name"), "title": (s or {}).get("title"),
                  "stream_id": (s or {}).get("id"), "started": (s or {}).get("createdAt")}
    return out


# ------------------------------------------------------------------ chat en direct (IRC anonyme)
class Chat(threading.Thread):
    """Ecrit chaque message dans chat.jsonl : [heure_murale, texte]."""

    def __init__(self, login, path):
        super().__init__(daemon=True)
        self.login, self.path, self.stop = login, path, threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.login}\r\n".encode())
                buf = b""
                with open(self.path, "a", encoding="utf-8") as f:
                    while not self.stop.is_set():
                        try:
                            data = s.recv(65536)
                        except socket.timeout:
                            s.sendall(b"PING :tmi.twitch.tv\r\n")
                            continue
                        if not data:
                            break
                        buf += data
                        *lines, buf = buf.split(b"\r\n")
                        now = round(time.time(), 2)
                        for raw in lines:
                            line = raw.decode("utf-8", "replace")
                            if line.startswith("PING"):
                                s.sendall(("PONG" + line[4:] + "\r\n").encode())
                            elif " PRIVMSG #" in line:
                                f.write(json.dumps([now, line.split(" :", 1)[-1][:300]], ensure_ascii=False) + "\n")
                        f.flush()
                s.close()
            except OSError as e:
                log(f"[{self.login}] chat : {e}, reconnexion")
            self.stop.wait(5)


def read_chat(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ enregistrement d'une session
class Session:
    """Un live suivi : enregistrement par parties (une partie = un lancement de streamlink), morceaux de 30 min.

    Temps : chaque partie a sa propre horloge (secondes dans la partie). sync.json relie l'heure murale a cette
    horloge (les pubs retirees par streamlink font avancer l'heure murale sans avancer la video), ce qui sert a
    recaler le chat."""

    def __init__(self, login, info, cfg):
        self.login, self.display, self.cfg = login, info["display"], cfg
        self.title = info.get("title") or ""
        self.started = time.time()
        self.dir = os.path.join(REC, f"{login}_{time.strftime('%Y%m%d_%H%M')}")
        os.makedirs(self.dir, exist_ok=True)
        self.chat_path = os.path.join(self.dir, "chat.jsonl")
        self.chat = Chat(login, self.chat_path)
        self.chat.start()
        self.state = load_json(os.path.join(self.dir, "state.json"), {"parts": [], "windows": [], "done": {},
                                                                       "batch_start": time.time()})
        self.active = True
        self.offline_since = None
        self.proc = None
        self.lock = threading.Lock()
        self.rec_thread = threading.Thread(target=self.record_loop, daemon=True)
        self.rec_thread.start()
        self.proc_thread = threading.Thread(target=self.process_loop, daemon=True)
        self.proc_thread.start()
        log(f"[{login}] debut de l'enregistrement : {self.title}")

    def save(self):
        with self.lock:
            save_json(os.path.join(self.dir, "state.json"), self.state)

    # ---------------------------------------------------------- enregistrement (streamlink -> ffmpeg segment)
    def record_loop(self):
        fails = 0
        while self.active:
            n = len(self.state["parts"])
            pdir = os.path.join(self.dir, f"part{n:02d}")
            os.makedirs(pdir, exist_ok=True)
            part = {"dir": pdir, "ended": False, "wall_start": time.time()}
            with self.lock:
                self.state["parts"].append(part)
            self.save()
            t0 = time.time()
            self.run_part(pdir)
            part["ended"] = True
            self.save()
            if time.time() - t0 < 60:
                fails += 1
            else:
                fails = 0
            if self.active:
                self.wait_s(min(300, 10 * 2 ** min(fails, 5)))  # flux coupe : on relance (attente croissante)

    def wait_s(self, s):
        end = time.time() + s
        while self.active and time.time() < end:
            time.sleep(1)

    def run_part(self, pdir):
        seg = int(self.cfg["chunk_minutes"] * 60)
        sl = subprocess.Popen([PY, "-m", "streamlink", "--twitch-disable-ads", "--stdout", "--loglevel", "warning",
                               "--retry-open", "3", f"https://www.twitch.tv/{self.login}", self.cfg["quality"]],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                               "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                               "-f", "segment", "-segment_time", str(seg), "-segment_format", "mpegts",
                               "-segment_list", "list.csv", "-segment_list_type", "csv",
                               "-progress", "pipe:1", "-nostats", "chunk_%03d.ts"],
                              cwd=pdir, stdin=sl.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        sl.stdout.close()
        self.proc = (sl, ff)
        sync, last = [], 0.0
        for line in ff.stdout:
            if line.startswith("out_time_us="):
                try:
                    t = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                now = time.time()
                if now - last >= 5:
                    sync.append([round(now, 2), round(t, 2)])
                    last = now
                    if len(sync) % 12 == 1:
                        save_json(os.path.join(pdir, "sync.json"), sync)
            if not self.active:
                break
        for p in (sl, ff):
            if p.poll() is None:
                p.terminate()
        for p in (sl, ff):
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        save_json(os.path.join(pdir, "sync.json"), sync)
        self.proc = None

    def stop(self):
        log(f"[{self.login}] fin de l'enregistrement")
        self.active = False
        if self.proc:
            for p in self.proc:
                if p.poll() is None:
                    p.terminate()
        self.chat.stop.set()

    # ---------------------------------------------------------- traitement des morceaux finis
    @staticmethod
    def chunks(pdir):
        out = []
        try:
            with open(os.path.join(pdir, "list.csv"), encoding="utf-8") as f:
                for line in f:
                    p = line.strip().split(",")
                    if len(p) >= 3:
                        out.append({"file": p[0], "start": float(p[1]), "end": float(p[2])})
        except OSError:
            pass
        return out

    def process_loop(self):
        while True:
            busy = False
            for pi, part in enumerate(list(self.state["parts"])):
                ch = self.chunks(part["dir"])
                for k, c in enumerate(ch):
                    key = f"{pi}:{k}"
                    if key in self.state["done"]:
                        continue
                    # on attend le morceau suivant (ou la fin de la partie) : une fenetre peut deborder dessus
                    if k + 1 >= len(ch) and not part["ended"]:
                        continue
                    try:
                        self.process_chunk(pi, part, ch, k)
                    except Exception as e:  # noqa: BLE001 - un morceau rate ne doit pas arreter la session
                        log(f"[{self.login}] morceau {key} : erreur {e}")
                    with self.lock:
                        self.state["done"][key] = True
                    self.save()
                    if k >= 1:
                        self.rm(os.path.join(part["dir"], ch[k - 1]["file"]))
                    busy = True
                if part["ended"] and all(f"{pi}:{k}" in self.state["done"] for k in range(len(ch))):
                    for c in ch:
                        self.rm(os.path.join(part["dir"], c["file"]))
            span = time.time() - self.state["batch_start"]
            finished = not self.active and not self.rec_thread.is_alive() and not busy and self.all_done()
            if self.state["windows"] and (finished or span > self.cfg["batch_hours"] * 3600):
                self.build_project()
            if finished:
                shutil.rmtree(self.dir, ignore_errors=True)
                log(f"[{self.login}] session terminee")
                return
            if not busy:
                time.sleep(20)

    def all_done(self):
        return all(p["ended"] and all(f"{pi}:{k}" in self.state["done"] for k in range(len(self.chunks(p["dir"]))))
                   for pi, p in enumerate(self.state["parts"]))

    @staticmethod
    def rm(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def part_chat(self, part, a, b):
        """Messages du chat recales sur l'horloge de la partie, entre a et b (s)."""
        sync = load_json(os.path.join(part["dir"], "sync.json"), [])
        if len(sync) < 2:
            return []
        w = np.array([s[0] for s in sync])
        t = np.array([s[1] for s in sync])
        out = []
        for wall, msg in read_chat(self.chat_path):
            if wall < w[0] - 60 or wall > w[-1] + 60:
                continue
            pt = float(np.interp(wall, w, t)) if w[0] <= wall <= w[-1] else (t[0] + wall - w[0] if wall < w[0] else t[-1] + wall - w[-1])
            if a <= pt <= b:
                out.append((pt, msg))
        return out

    def process_chunk(self, pi, part, ch, k):
        c = ch[k]
        src = os.path.join(part["dir"], c["file"])
        tmp = os.path.join(part["dir"], f"_an{k:03d}")
        os.makedirs(tmp, exist_ok=True)
        wav = os.path.join(tmp, "audio.wav")
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", wav] if os.name != "nt" else
                       ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
                       check=True)
        with wave.open(wav, "rb") as wf:
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        n = int(16000 * BIN)
        nb = len(raw) // n
        if nb < 120:
            shutil.rmtree(tmp, ignore_errors=True)
            return
        rms = np.sqrt(np.mean(raw[: nb * n].reshape(nb, n) ** 2, axis=1) + 1e-10)
        db = 20 * np.log10(rms)
        win = min(int(60 / BIN), nb)
        excess = np.clip(db - np.convolve(db, np.ones(win) / win, mode="same"), 0, None)

        ev = np.zeros(nb)
        types = {}
        if os.path.exists(signals.YAMNET):
            for e in signals.audio_events(wav, tmp):
                i = int(e["t"] / BIN)
                if 0 <= i < nb:
                    ev[i] += signals.EVENT_GROUPS[e["type"]][1] * e["p"]
                    types.setdefault(i, set()).add(e["type"])

        msgs = [(t - c["start"], m) for t, m in self.part_chat(part, c["start"] - 130, c["end"] + 10)]
        chat_z = np.zeros(nb)
        if msgs:
            act, laugh = signals.chat_curve(msgs, nb, BIN)
            act = np.convolve(act + 2 * laugh, np.ones(int(5 / BIN)), mode="same")
            base = np.convolve(act, np.ones(int(120 / BIN)) / int(120 / BIN), mode="same")
            chat_z = np.clip((act - base) / (np.sqrt(base) + 1), 0, None)
        score = excess + ev + 3.0 * chat_z
        smooth = np.convolve(score, np.ones(int(6 / BIN)) / int(6 / BIN), mode="same")
        shutil.rmtree(tmp, ignore_errors=True)

        taken = [w["peak"] for w in self.state["windows"] if w["part"] == pi]
        picked = []
        for i in np.argsort(-smooth):
            if smooth[i] < 0.3 or len(picked) >= self.cfg["windows_per_chunk"]:
                break
            t = c["start"] + i * BIN
            if all(abs(t - p) >= SEP for p in taken + [p for p, _ in picked]):
                picked.append((t, i))
        for t, i in sorted(picked):
            a = max(ch[0]["start"], t - self.cfg["window_before"])
            b = min(ch[-1]["end"], t + self.cfg["window_after"])
            seg = slice(max(0, i - 40), i + 25)
            why = sorted(set().union(*[types.get(j, set()) for j in range(seg.start, min(nb, seg.stop))]))
            if chat_z[seg].max() > 1.5:
                why.append(f"chat x{chat_z[seg].max():.1f}")
            if excess[seg].max() > 8:
                why.append("pic de volume")
            out = os.path.join(self.dir, f"win_{pi:02d}_{int(a):06d}.ts")
            if self.cut(part, ch, a, b, out):
                with self.lock:
                    self.state["windows"].append({"part": pi, "peak": round(t, 1), "a": round(a, 1), "b": round(b, 1),
                                                  "file": os.path.basename(out), "score": round(float(smooth[i]), 1),
                                                  "why": why, "wall": part["wall_start"] + a})
                log(f"[{self.login}] moment garde : partie {pi} a {int(t // 60)} min ({', '.join(why) or 'volume'})")

    @staticmethod
    def cut(part, ch, a, b, out):
        """Fenetre [a, b] (temps de la partie) sans re-encodage, en collant les morceaux voisins si besoin."""
        use = [c for c in ch if c["end"] > a and c["start"] < b and os.path.exists(os.path.join(part["dir"], c["file"]))]
        if not use:
            return False
        src = "concat:" + "|".join(os.path.join(part["dir"], c["file"]) for c in use)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a - use[0]['start']:.2f}", "-i", src,
                            "-t", f"{b - a:.2f}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                            "-f", "mpegts", out], capture_output=True, text=True)
        return r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 100_000

    # ---------------------------------------------------------- video courte + projet pour l'appli
    def build_project(self):
        with self.lock:
            wins = sorted(self.state["windows"], key=lambda w: (w["part"], w["a"]))
            self.state["windows"] = []
            batch_start = self.state["batch_start"]
            self.state["batch_start"] = time.time()
        self.save()
        wins = [w for w in wins if os.path.exists(os.path.join(self.dir, w["file"]))]
        if not wins:
            return
        name = f"{self.login}_live_{time.strftime('%Y%m%d_%H%M', time.localtime(wins[0]['wall']))}"
        while os.path.exists(os.path.join(ROOT, "input", name + ".mp4")):
            name += "b"
        os.makedirs(os.path.join(ROOT, "input"), exist_ok=True)
        work = os.path.join(ROOT, "work", name)
        os.makedirs(work, exist_ok=True)
        lst = os.path.join(self.dir, "concat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for w in wins:
                f.write(f"file '{w['file']}'\n")
        video = os.path.join(ROOT, "input", name + ".mp4")
        # video copiee telle quelle, son re-encode (cale sur l'horloge video : pas de trou aux raccords)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                            "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", video + ".part.mp4"],
                           cwd=self.dir, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"[{self.login}] collage rate : {r.stderr[-500:]}")
            return
        os.replace(video + ".part.mp4", video)

        chat, mapping, pos = [], [], 0.0
        for w in wins:
            d = probe(os.path.join(self.dir, w["file"])) or (w["b"] - w["a"])
            part = self.state["parts"][w["part"]]
            for t, m in self.part_chat(part, w["a"] - 10, w["a"] + d):
                chat.append([round(pos + t - w["a"], 2), m])
            mapping.append({**{k: w[k] for k in ("part", "a", "b", "peak", "score", "why")}, "at": round(pos, 2),
                            "dur": round(d, 2), "wall": round(w["wall"], 1)})
            pos += d
        chat.sort()
        save_json(os.path.join(work, "chat.json"), chat)
        save_json(os.path.join(ROOT, "input", name + ".meta.json"),
                  {"live": True, "login": self.login, "title": self.title, "windows": mapping,
                   "batch_start": batch_start, "built": time.time()})
        for w in wins:
            self.rm(os.path.join(self.dir, w["file"]))
        with tasks._Lock():
            meta = load_json(PROJECTS, {})
            meta[name] = {"creator": self.display, "url": f"https://www.twitch.tv/{self.login}",
                          "channel": f"twitch.tv/{self.login}", "created": time.strftime("%Y-%m-%d %H:%M"),
                          "auto": True, "title": self.title, "moments": len(wins)}
            save_json(PROJECTS, meta)
        tasks.add("analyze", name)
        log(f"[{self.login}] projet {name} : {len(wins)} moments, {pos / 60:.0f} min -> analyse demandee au PC")


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def salvage(sdir):
    """Session coupee par un redemarrage du VPS/service : les fenetres deja decoupees deviennent un projet."""
    st = load_json(os.path.join(sdir, "state.json"), None)
    try:
        if st and st.get("windows"):
            ses = Session.__new__(Session)
            ses.dir, ses.state, ses.lock = sdir, st, threading.Lock()
            ses.login = os.path.basename(sdir).rsplit("_", 2)[0]
            ses.display, ses.title = ses.login, ""
            ses.chat_path = os.path.join(sdir, "chat.jsonl")
            ses.build_project()
    except Exception as e:  # noqa: BLE001
        log(f"recuperation de {sdir} ratee : {e}")
    shutil.rmtree(sdir, ignore_errors=True)


# ------------------------------------------------------------------ menage du disque
def cleanup(cfg):
    """Supprime du VPS les videos courtes deja analysees par le PC depuis plus de keep_days jours (le PC les garde)."""
    done = {t["project"]: t.get("ended") or 0 for t in tasks.all_tasks() if t["kind"] == "analyze" and t["status"] == "fini"}
    idir = os.path.join(ROOT, "input")
    if not os.path.isdir(idir):
        return
    for fn in os.listdir(idir):
        name, ext = os.path.splitext(fn)
        if ext == ".mp4" and name in done and time.time() - done[name] > cfg["keep_days"] * 86400:
            os.remove(os.path.join(idir, fn))
            log(f"menage : input/{fn} supprime (analyse finie, le PC a la video)")


# ------------------------------------------------------------------ boucle principale
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        cfg = config()
        for l, s in live_status(cfg["streamers"]).items():
            ok = s["live"] and s["game"] in cfg["categories"]
            print(f"{l:20s} {'LIVE' if s['live'] else '-':5s} {s['game'] or '':28s} {'-> enregistre' if ok else ''}")
        return
    if not os.path.exists(WATCHLIST):
        save_json(WATCHLIST, {"streamers": DEFAULTS["streamers"], "categories": DEFAULTS["categories"]})
    os.makedirs(REC, exist_ok=True)
    for d in os.listdir(REC):  # sessions d'avant un redemarrage : on sauve les moments deja decoupes
        salvage(os.path.join(REC, d))
    sessions, last_clean = {}, 0
    log("watcher demarre")
    while True:
        cfg = config()
        try:
            st = live_status(cfg["streamers"])
        except (Exception, SystemExit) as e:  # noqa: BLE001 - signals._gql leve SystemExit si Twitch ne repond pas
            log(f"Twitch injoignable : {e}")
            st = None
        low = free_gb() < cfg["min_free_gb"]
        now = time.time()
        if st is not None:
            for login, s in st.items():
                ok = s["live"] and s["game"] in cfg["categories"]
                ses = sessions.get(login)
                if ses and ses.active:
                    if ok:
                        ses.offline_since = None
                    elif ses.offline_since is None:
                        ses.offline_since = now
                    elif now - ses.offline_since > cfg["grace_minutes"] * 60:
                        ses.stop()
                elif ok and not low:
                    sessions[login] = Session(login, s, cfg)
            for login, ses in list(sessions.items()):
                if login not in st and ses.active:  # retire de la liste : on arrete
                    ses.stop()
        if low:
            for ses in sessions.values():
                if ses.active:
                    log(f"disque presque plein ({free_gb():.1f} Go libres) : arret de {ses.login}")
                    ses.stop()
        for login, ses in list(sessions.items()):
            if not ses.active and not ses.proc_thread.is_alive():
                del sessions[login]
        status = {"updated": now, "free_gb": round(free_gb(), 1), "low_disk": low, "categories": cfg["categories"],
                  "streamers": {}}
        for login in cfg["streamers"]:
            s = (st or {}).get(login, {})
            ses = sessions.get(login)
            status["streamers"][login] = {
                "display": s.get("display", login), "exists": s.get("exists", True), "live": s.get("live", False),
                "game": s.get("game"), "title": s.get("title"),
                "recording": bool(ses and ses.active), "since": ses.started if ses else None,
                "moments": len(ses.state["windows"]) if ses else 0,
                "processing": bool(ses and not ses.active)}
        save_json(LIVE, status)
        if now - last_clean > 3600:
            last_clean = now
            try:
                cleanup(cfg)
            except OSError as e:
                log(f"menage : {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
