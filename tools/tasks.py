"""File d'attente des taches lourdes confiees au PC (Whisper + rendu sur GPU), partagee par plusieurs processus.

Sur le VPS, l'appli (tools/app.py) et le watcher (tools/watcher.py) ajoutent des taches ; le PC les prend via
l'API /api/worker/* de l'appli (voir tools/pc_worker.py). Stockage : data/tasks.json, verrou par fichier.

Tache : {id, kind: "analyze"|"render", project, ..., status: "en attente"|"en cours"|"fini"|"erreur",
         log, created, started, ended, beat}
"""
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "tasks.json")
LOCK = PATH + ".lock"
STALE = 15 * 60  # une tache "en cours" sans nouvelles du PC depuis 15 min repart en attente
KEEP = 200


class _Lock:
    def __enter__(self):
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(LOCK) > 30:  # verrou laisse par un processus mort
                        os.remove(LOCK)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > 60:
                    raise TimeoutError("data/tasks.json verrouille")
                time.sleep(0.05)

    def __exit__(self, *a):
        os.close(self.fd)
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(tasks):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks[-KEEP:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def all_tasks():
    with _Lock():
        return _load()


def get(tid):
    return next((t for t in all_tasks() if t["id"] == tid), None)


def add(kind, project, **fields):
    """Ajoute une tache. Une tache identique deja en attente n'est pas dupliquee (on renvoie l'existante)."""
    with _Lock():
        tasks = _load()
        for t in tasks:
            if (t["status"] == "en attente" and t["kind"] == kind and t["project"] == project
                    and all(t.get(k) == v for k, v in fields.items())):
                return t
        t = {"id": int(time.time() * 1000), "kind": kind, "project": project, **fields, "status": "en attente",
             "log": "", "created": time.time(), "started": None, "ended": None, "beat": None}
        while any(x["id"] == t["id"] for x in tasks):
            t["id"] += 1
        tasks.append(t)
        _save(tasks)
        return t


def claim():
    """Prochaine tache pour le PC (la plus ancienne en attente), passee "en cours"."""
    now = time.time()
    with _Lock():
        tasks = _load()
        for t in tasks:
            if t["status"] == "en cours" and now - (t.get("beat") or t.get("started") or now) > STALE:
                t["status"] = "en attente"
                t["log"] += "\n(PC sans nouvelles : tache relancee)\n"
        # les analyses d'abord en attente depuis longtemps ne doivent pas bloquer les rendus demandes a la main
        pending = [t for t in tasks if t["status"] == "en attente"]
        pending.sort(key=lambda t: (t["kind"] != "render", t["created"]))
        if not pending:
            _save(tasks)
            return None
        t = pending[0]
        t.update(status="en cours", started=now, beat=now)
        _save(tasks)
        return t


def update(tid, log=None, status=None, **fields):
    with _Lock():
        tasks = _load()
        t = next((x for x in tasks if x["id"] == tid), None)
        if not t:
            return None
        if log:
            t["log"] = (t["log"] + log)[-8000:]
        t["beat"] = time.time()
        t.update(fields)
        if status:
            t["status"] = status
            if status in ("fini", "erreur"):
                t["ended"] = time.time()
        _save(tasks)
        return t


def wait(tid, on_update=None, poll=3):
    """Bloque jusqu'a la fin de la tache (utilise par les jobs de l'appli). Renvoie la tache finie."""
    while True:
        t = get(tid)
        if t is None:
            raise RuntimeError("tache disparue")
        if on_update:
            on_update(t)
        if t["status"] in ("fini", "erreur"):
            return t
        time.sleep(poll)
