// Types et appels vers le serveur Python (tools/app.py)

export type Status = "a_poster" | "programme" | "poste";

export type Sync = { ok: boolean; median: number; silence_pct: number; offset: number };

export type Clip = {
  key: string;
  config: string;
  source: string;
  name: string;
  creator: string;
  hook: string;
  start: string;
  end: string;
  outro: string;
  video: boolean;
  duration: number | null;
  mtime: number | null;
  date: number;
  caption: string;
  credit: string;
  order: number;
  sync: Sync | null;
  status: Status;
  url: string;
  views: number | null;
  likes: number | null;
  notes: string;
  posted_at: string | null;
  scheduled: string;
  sent_at: string | null;
  job: string | null;
};

export type Job = {
  id: number;
  kind: "render" | "import";
  key: string;
  status: "en attente" | "en cours" | "fini" | "erreur";
  log: string;
  started: number | null;
  ended: number | null;
};

export type Project = {
  name: string;
  creator: string;
  url: string;
  channel: string;
  vod: string;
  video: boolean;
  analyzed: boolean;
  frame: boolean;
  facecam: number[] | null;
  layout: string;
  used: [string, string, string][];
  auto?: boolean;
  title?: string;
  source_here?: boolean;
};

export type Candidate = {
  rank: number;
  peak: string;
  score: number;
  why: string[];
  text: string;
  start: string;
  end: string;
  hook: string;
  caption?: string;
};

export type LiveStreamer = {
  display: string; exists: boolean; live: boolean; game: string | null; title: string | null;
  recording: boolean; since: number | null; moments: number; processing: boolean;
};

export type Watch = {
  remote: boolean;
  streamers: string[];
  categories: string[];
  live: { updated?: number; free_gb?: number; low_disk?: boolean; streamers?: Record<string, LiveStreamer> };
  watcher_ok: boolean;
  worker: { seen: number | null; online: boolean; name: string };
  tasks_pending: number;
};

export type IphoneSettings = { iphone_dir: string | null; saved: string | null; detected: string[] };

export async function api<T = Record<string, unknown>>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(
    path,
    body === undefined
      ? { cache: "no-store" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  if (r.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login"; // session expirée (appli sur le VPS)
    throw new Error("connexion requise");
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { error?: string }).error || r.statusText);
  return j as T;
}

export const STATUS: Record<Status, string> = { a_poster: "À poster", programme: "Programmé", poste: "Posté" };

export const enc = encodeURIComponent;
export const videoUrl = (c: Clip) => `/video/${enc(c.source)}/${enc(c.name)}?t=${c.mtime || 0}`;
export const thumbUrl = (c: Clip) => `/thumb/${enc(c.source)}/${enc(c.name)}?t=${c.mtime || 0}`;
export const syncState = (c: Clip): "ok" | "warn" | "none" => (!c.sync ? "none" : c.sync.ok ? "ok" : "warn");

export function fmtDur(d: number | null) {
  if (d == null) return "–";
  const s = Math.round(d);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtNum(n: number | null | undefined) {
  if (n == null) return "–";
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(".0", "") + " M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(".0", "") + " k";
  return String(n);
}

const DAY = 86400000;
function startOfDay(t: number) {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** "Aujourd'hui", "Hier", "lundi 16 novembre" */
export function dayLabel(sec: number) {
  const t = sec * 1000;
  const diff = Math.round((startOfDay(Date.now()) - startOfDay(t)) / DAY);
  if (diff <= 0) return "Aujourd'hui";
  if (diff === 1) return "Hier";
  const s = new Date(t).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "il y a 3 min", "il y a 2 h", "hier à 14:05", "16 nov." */
export function relTime(sec: number) {
  const t = sec * 1000;
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "à l'instant";
  if (m < 60) return `il y a ${m} min`;
  if (m < 60 * 6) return `il y a ${Math.round(m / 60)} h`;
  const hm = new Date(t).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const diff = Math.round((startOfDay(Date.now()) - startOfDay(t)) / DAY);
  if (diff <= 0) return `aujourd'hui à ${hm}`;
  if (diff === 1) return `hier à ${hm}`;
  return new Date(t).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

export const tsec = (v: string) => String(v).split(":").map(Number).reduce((s, x) => s * 60 + x, 0);

export function slugify(s: string) {
  return s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");
}

export const fullCaption = (c: Clip) => [c.caption, c.credit].filter(Boolean).join("\n");

export async function copyText(txt: string) {
  try {
    await navigator.clipboard.writeText(txt);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = txt;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

export const plural = (n: number, word: string, many = word + "s") => `${n} ${n > 1 ? many : word}`;
