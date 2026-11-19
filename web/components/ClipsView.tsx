"use client";
import { useCallback, useEffect, useMemo, useState, type MutableRefObject } from "react";
import {
  api, copyText, dayLabel, fmtDur, fmtNum, fullCaption, plural, slugify, syncState, thumbUrl, type Clip, type Status,
} from "@/lib/api";
import { Icon, useUI } from "./ui";
import ClipCard from "./ClipCard";
import ClipModal from "./ClipModal";

type Filter = "tout" | Status | "verifier";
type Sort = "recent" | "order" | "views" | "duration";

const FILTERS: [Filter, string][] = [
  ["tout", "Tous"], ["a_poster", "À poster"], ["programme", "Programmés"], ["poste", "Postés"], ["verifier", "À vérifier"],
];

export default function ClipsView({ clips, reload, busy, onIphoneSetup }: {
  clips: Clip[] | null;
  reload: () => Promise<void>;
  busy: MutableRefObject<boolean>;
  onIphoneSetup: (c: Clip) => void;
}) {
  const { toast, confirm } = useUI();
  const [filter, setFilter] = useState<Filter>("tout");
  const [creator, setCreator] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("recent");
  const [sel, setSel] = useState<Set<string> | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const all = clips ?? [];
  busy.current = !!openKey || deleting;

  const visible = useMemo(() => {
    const s = q.trim().toLowerCase();
    const list = all.filter((c) => {
      if (creator && c.source !== creator) return false;
      if (filter === "verifier" ? syncState(c) !== "warn" : filter !== "tout" && c.status !== filter) return false;
      if (s && !`${c.hook} ${c.caption} ${c.creator} ${c.name}`.toLowerCase().includes(s)) return false;
      return true;
    });
    const by: Record<Sort, (a: Clip, b: Clip) => number> = {
      recent: (a, b) => b.date - a.date || a.order - b.order,
      order: (a, b) => a.source.localeCompare(b.source) || a.order - b.order,
      views: (a, b) => (b.views || 0) - (a.views || 0),
      duration: (a, b) => (b.duration || 0) - (a.duration || 0),
    };
    return list.sort(by[sort]);
  }, [all, filter, creator, q, sort]);

  // sections : par jour (plus récents) ou par streamer (ordre conseillé)
  const sections = useMemo(() => {
    const m = new Map<string, Clip[]>();
    for (const c of visible) {
      const k = sort === "recent" ? dayLabel(c.date) : sort === "order" ? c.creator : "";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(c);
    }
    return [...m.entries()];
  }, [visible, sort]);

  const creators = useMemo(() => {
    const m = new Map<string, { name: string; n: number; date: number }>();
    for (const c of all) {
      const e = m.get(c.source) ?? { name: c.creator, n: 0, date: 0 };
      e.n++;
      e.date = Math.max(e.date, c.date);
      m.set(c.source, e);
    }
    const list = [...m.entries()].sort((a, b) => b[1].date - a[1].date);
    // même streamer sur plusieurs projets : précise lequel (ex. « JLTomy · vod »)
    return list.map(([src, e]) => {
      const twin = list.some(([s2, e2]) => s2 !== src && e2.name === e.name);
      const suffix = src.replace(slugify(e.name) + "_", "").replace(/^gta6_?/, "").replace(/_/g, " ");
      return [src, { ...e, label: twin && suffix ? `${e.name} · ${suffix}` : e.name }] as const;
    });
  }, [all]);

  // projet filtré supprimé : revient à « Tous »
  useEffect(() => {
    if (creator && clips && !clips.some((c) => c.source === creator)) setCreator("");
  }, [clips, creator]);

  // garde la sélection propre quand des clips disparaissent
  useEffect(() => {
    if (!sel) return;
    const keys = new Set(all.map((c) => c.key));
    if ([...sel].some((k) => !keys.has(k))) setSel(new Set([...sel].filter((k) => keys.has(k))));
  }, [all, sel]);

  const sendToIphone = useCallback(async (c: Clip) => {
    try {
      toast("Copie vers iCloud Photos…");
      await api("/api/send", { key: c.key });
      toast("Envoyé : il arrive dans Photos d'ici 1 à 2 min");
      reload();
    } catch (e) {
      const msg = (e as Error).message;
      if (/iCloud/i.test(msg)) onIphoneSetup(c);
      else toast("Erreur : " + msg, "err");
    }
  }, [toast, reload, onIphoneSetup]);

  const copyCaption = useCallback(async (c: Clip) => {
    const t = fullCaption(c);
    if (!t) return toast("Pas de légende pour ce clip", "err");
    await copyText(t);
    toast("Légende copiée");
  }, [toast]);

  const toggle = (key: string, range: boolean) => {
    if (!sel) return;
    const next = new Set(sel);
    const order = visible.map((c) => c.key);
    if (range && last && order.includes(last)) {
      const [a, b] = [order.indexOf(last), order.indexOf(key)].sort((x, y) => x - y);
      order.slice(a, b + 1).forEach((k) => next.add(k));
    } else if (next.has(key)) next.delete(key);
    else next.add(key);
    setSel(next);
    setLast(key);
  };

  /** Supprime des clips : fichiers à la corbeille, retirés des configs et de l'appli. */
  const deleteClips = useCallback(async (keys: string[]) => {
    const chosen = all.filter((c) => keys.includes(c.key));
    if (!chosen.length) return false;
    // projets vidés entièrement : leur dossier part aussi
    const emptied = [...new Set(chosen.map((c) => c.source))].filter(
      (s) => all.filter((c) => c.source === s).every((c) => keys.includes(c.key)),
    );
    const ok = await confirm({
      title: `Supprimer ${plural(chosen.length, "clip")} ?`,
      danger: true,
      ok: "Supprimer",
      body: (
        <>
          <ul className="del-list">
            {chosen.slice(0, 8).map((c) => <li key={c.key}>{c.hook || c.name}</li>)}
            {chosen.length > 8 && <li className="muted">… et {chosen.length - 8} autres</li>}
          </ul>
          <p>
            Les mp4 sont effacés du PC (corbeille Windows) et les clips retirés de l&apos;appli et de leurs légendes.
            {emptied.length > 0 && (
              <> Le dossier <b>{emptied.map((s) => `output/${s}`).join(", ")}</b> part aussi, il n&apos;aura plus de clip.</>
            )}
          </p>
        </>
      ),
    });
    if (!ok) return false;
    setDeleting(true);
    try {
      const r = await api<{ deleted: number; emptied: string[] }>("/api/delete", { keys });
      toast(`${plural(r.deleted, "clip supprimé", "clips supprimés")}` + (r.emptied.length ? " · dossier supprimé" : ""));
      await reload();
      return true;
    } catch (e) {
      toast("Erreur : " + (e as Error).message, "err");
      return false;
    } finally {
      setDeleting(false);
    }
  }, [all, confirm, toast, reload]);

  // raccourcis clavier
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const typing = !!el && /INPUT|TEXTAREA|SELECT/.test(el.tagName);
      if (document.querySelector("dialog[open]")) return;
      if (e.key === "Escape") {
        if (typing) el!.blur();
        else if (sel) setSel(null);
        return;
      }
      if (typing || openKey) return;
      if (e.key === "/") { e.preventDefault(); document.getElementById("q")?.focus(); }
      if (sel && (e.key === "Delete" || e.key === "Backspace") && sel.size) {
        e.preventDefault();
        deleteClips([...sel]).then((ok) => ok && setSel(new Set()));
      }
      if (sel && e.key.toLowerCase() === "a" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setSel(new Set(visible.map((c) => c.key)));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, openKey, visible, deleteClips]);

  // stats
  const n = (f: (c: Clip) => boolean) => all.filter(f).length;
  const views = all.reduce((s, c) => s + (Number(c.views) || 0), 0);
  const stats: [string | number, string][] = [
    [n((c) => c.status === "a_poster" && c.video), "à poster"],
    [n((c) => c.status === "programme"), "programmés"],
    [n((c) => c.status === "poste"), "postés"],
    [fmtNum(views), "vues"],
  ];

  // prochain à poster : le stream le plus récent, dans son ordre conseillé
  const todo = all.filter((c) => c.status === "a_poster" && c.video).sort((a, b) => b.date - a.date);
  const next = todo.filter((c) => c.source === todo[0]?.source).sort((a, b) => a.order - b.order)[0];

  const openClip = openKey ? all.find((c) => c.key === openKey) : undefined;
  const allOn = visible.length > 0 && !!sel && visible.every((c) => sel.has(c.key));

  return (
    <div className={sel ? "with-selbar" : ""}>
      <header className="page-head">
        <div>
          <h1>Mes clips</h1>
          <p className="muted">
            {clips ? `${plural(all.length, "clip")} · ${plural(creators.length, "stream")} · du plus récent au plus ancien` : "Chargement…"}
          </p>
        </div>
        <div className="stats">
          {stats.map(([v, l]) => (
            <div className="stat" key={l}><b>{v}</b><span>{l}</span></div>
          ))}
        </div>
      </header>

      {next && !sel && (
        <section className="next">
          <img src={thumbUrl(next)} alt="" />
          <div className="next-body">
            <span className="kicker">Prochain à poster</span>
            <h2>{next.hook}</h2>
            <p className="muted clamp2">{next.caption || "Pas de légende"}</p>
            <span className="muted small">{next.creator} · {fmtDur(next.duration)}</span>
          </div>
          <div className="next-actions">
            <button className="btn primary" onClick={() => copyCaption(next)}><Icon name="copy" size={16} /> Copier la légende</button>
            <button className="btn" onClick={() => sendToIphone(next)}><Icon name="send" size={16} /> {next.sent_at ? "Renvoyer" : "Envoyer"} sur l&apos;iPhone</button>
            <div className="row-btns">
              <button className="btn ghost" onClick={async () => { await api("/api/state", { key: next.key, status: "poste" }); toast("Marqué posté"); reload(); }}>Marquer posté</button>
              <button className="btn ghost" onClick={() => setOpenKey(next.key)}>Ouvrir</button>
            </div>
          </div>
        </section>
      )}

      <div className="toolbar">
        <div className="seg">
          {FILTERS.map(([k, l]) => (
            <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
        <label className="search">
          <Icon name="search" size={16} />
          <input id="q" type="search" placeholder="Rechercher…  ( / )" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Rechercher un clip" />
        </label>
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Trier">
          <option value="recent">Plus récents</option>
          <option value="order">Ordre conseillé</option>
          <option value="views">Plus de vues</option>
          <option value="duration">Plus longs</option>
        </select>
        <button className={`btn ${sel ? "primary" : ""}`} onClick={() => { setSel(sel ? null : new Set()); setLast(null); }}>
          <Icon name="select" size={16} /> {sel ? "Terminer" : "Sélectionner"}
        </button>
      </div>

      {creators.length > 1 && (
        <div className="creators">
          <button className={!creator ? "on" : ""} onClick={() => setCreator("")}>Tous</button>
          {creators.map(([src, e]) => (
            <button key={src} className={creator === src ? "on" : ""} onClick={() => setCreator(creator === src ? "" : src)}>
              <span className="avatar">{e.name.slice(0, 1).toUpperCase()}</span>
              {e.label}
              <em>{e.n}</em>
            </button>
          ))}
        </div>
      )}

      {clips === null ? (
        <div className="grid">{Array.from({ length: 8 }, (_, i) => <div key={i} className="card skeleton" />)}</div>
      ) : !visible.length ? (
        <div className="empty">
          <b>Aucun clip ici</b>
          <span className="muted">{all.length ? "Change de filtre ou de recherche." : "Ajoute un stream dans « Nouveau stream »."}</span>
        </div>
      ) : (
        sections.map(([title, cs]) => (
          <section key={title || "all"} className="section">
            {title && (
              <h3 className="section-title">
                {title}
                <span>{plural(cs.length, "clip")}</span>
                {sel && (
                  <button className="link" onClick={() => {
                    const on = cs.every((c) => sel.has(c.key));
                    const s = new Set(sel);
                    cs.forEach((c) => (on ? s.delete(c.key) : s.add(c.key)));
                    setSel(s);
                  }}>
                    {cs.every((c) => sel.has(c.key)) ? "Tout désélectionner" : "Tout sélectionner"}
                  </button>
                )}
              </h3>
            )}
            <div className="grid">
              {cs.map((c) => (
                <ClipCard key={c.key} clip={c} selecting={!!sel} selected={!!sel?.has(c.key)}
                  showCreator={sort !== "order"}
                  onClick={(shift) => (sel ? toggle(c.key, shift) : setOpenKey(c.key))} />
              ))}
            </div>
          </section>
        ))
      )}

      {sel && (
        <div className="selbar" role="toolbar" aria-label="Sélection">
          <b>{sel.size ? plural(sel.size, "sélectionné") : "Clique sur des clips"}</b>
          <button className="btn ghost" onClick={() => setSel(allOn ? new Set() : new Set(visible.map((c) => c.key)))}>
            {allOn ? "Tout désélectionner" : `Tout sélectionner (${visible.length})`}
          </button>
          <button className="btn danger" disabled={!sel.size || deleting}
            onClick={() => deleteClips([...sel]).then((ok) => ok && setSel(new Set()))}>
            <Icon name="trash" size={16} /> {deleting ? "Suppression…" : `Supprimer${sel.size ? ` (${sel.size})` : ""}`}
          </button>
          <button className="btn ghost icon" aria-label="Fermer la sélection" onClick={() => setSel(null)}><Icon name="x" size={16} /></button>
        </div>
      )}

      {openClip && (
        <ClipModal
          clip={openClip}
          list={visible}
          onNavigate={setOpenKey}
          onClose={() => setOpenKey(null)}
          reload={reload}
          copyCaption={copyCaption}
          sendToIphone={sendToIphone}
          onDelete={async (c) => {
            const i = visible.findIndex((x) => x.key === c.key);
            const nb = visible[i + 1] || visible[i - 1];
            if (await deleteClips([c.key])) setOpenKey(nb && nb.key !== c.key ? nb.key : null);
          }}
        />
      )}
    </div>
  );
}
