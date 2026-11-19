"use client";
import { useEffect, useRef, useState } from "react";
import { api, enc, plural, slugify, tsec, type Candidate, type Project } from "@/lib/api";
import { Icon, useUI } from "./ui";

export default function MomentsPicker({ name, onBack, onCreated }: { name: string; onBack: () => void; onCreated: () => void }) {
  const { toast } = useUI();
  const [p, setP] = useState<Project | null>(null);
  const [cands, setCands] = useState<Candidate[]>([]);
  const [pick, setPick] = useState<Set<number>>(new Set());
  const [layout, setLayout] = useState<"split" | "full">("split");
  const [fc, setFc] = useState<number[] | null>(null);
  const [creating, setCreating] = useState(false);
  const player = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    api<{ project: Project; candidates: Candidate[] }>(`/api/projects/${enc(name)}/candidates`).then((r) => {
      setP(r.project);
      setCands(r.candidates);
      setFc(r.project.facecam ? [...r.project.facecam] : null);
      setLayout(r.project.layout === "full" ? "full" : "split");
    });
  }, [name]);

  if (!p) return <div className="empty">Chargement…</div>;

  const used = (c: Candidate) => p.used.find(([a, b]) => tsec(c.start) < tsec(b) && tsec(c.end) > tsec(a));
  const patch = (rank: number, k: keyof Candidate, v: string) => setCands((cs) => cs.map((c) => (c.rank === rank ? { ...c, [k]: v } : c)));
  const toggle = (rank: number) => setPick((s) => { const n = new Set(s); if (n.has(rank)) n.delete(rank); else n.add(rank); return n; });

  const create = async () => {
    setCreating(true);
    try {
      const clips = cands.filter((c) => pick.has(c.rank)).map((c) => ({ start: c.start, end: c.end, hook: c.hook, caption: c.caption || "" }));
      const r = await api<{ jobs: unknown[] }>("/api/create", { name: p.name, creator: p.creator, layout, facecam: fc, clips });
      toast(`${plural(r.jobs.length, "clip")} en cours de rendu`);
      onCreated();
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); }
    finally { setCreating(false); }
  };

  const tag = slugify(p.creator).replace(/_/g, "");

  return (
    <div className="with-selbar">
      <button className="btn ghost" onClick={onBack}><Icon name="left" size={16} /> Streams</button>
      <header className="page-head">
        <div>
          <h1>{p.creator}</h1>
          <p className="muted">Moments classés par score (chat, cris, rires, mots-clés). Coche, écris un hook, ajuste début et fin. Visé : 60 s minimum.</p>
        </div>
      </header>

      <div className="moments">
        <div className="stick">
          <video ref={player} controls preload="metadata" src={`/source/${enc(p.name)}`} />
          <section className="panel">
            <h3 className="panel-title">Facecam</h3>
            <div className="seg full">
              <button className={layout === "split" ? "on" : ""} onClick={() => setLayout("split")}>Facecam en haut</button>
              <button className={layout === "full" ? "on" : ""} onClick={() => setLayout("full")}>Pas de facecam</button>
            </div>
            {layout === "split"
              ? <><p className="muted small">Trace un rectangle autour de la caméra du streamer.</p><FacecamDraw name={p.name} rect={fc} onChange={setFc} /></>
              : <p className="muted small">Le jeu sera recadré plein écran.</p>}
          </section>
        </div>

        <div>
          {!cands.length && <div className="empty">Aucun moment trouvé.</div>}
          {cands.map((c) => {
            const on = pick.has(c.rank), u = used(c), d = tsec(c.end) - tsec(c.start);
            return (
              <article key={c.rank} className={`cand${on ? " on" : ""}${u && !on ? " used" : ""}`}>
                <input type="checkbox" checked={on} onChange={() => toggle(c.rank)} aria-label={`Choisir le moment ${c.rank}`} />
                <div className="cand-body">
                  <div className="cand-top">
                    <b>#{c.rank}</b>
                    <span className="muted small">pic {c.peak}</span>
                    {c.why.map((w) => <span key={w} className="why">{w}</span>)}
                    {u && <span className="pill-s">déjà clippé : {u[2]}</span>}
                    <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => {
                      const v = player.current;
                      if (v) { v.currentTime = tsec(c.start); v.play(); }
                    }}><Icon name="play" size={14} /> Voir</button>
                  </div>
                  <Expandable text={c.text} />
                  {on && (
                    <div className="cand-edit">
                      <label className="field"><span className="field-label">Début</span>
                        <input value={c.start} onChange={(e) => patch(c.rank, "start", e.target.value)} /></label>
                      <label className="field"><span className="field-label">Fin <em className={d >= 60 ? "" : "warn"}>{isFinite(d) ? `${d} s` : "?"}</em></span>
                        <input value={c.end} onChange={(e) => patch(c.rank, "end", e.target.value)} /></label>
                      <label className="field span2"><span className="field-label">Hook (70 car. max)</span>
                        <input maxLength={70} value={c.hook} onChange={(e) => patch(c.rank, "hook", e.target.value)} /></label>
                      <label className="field span-all"><span className="field-label">Légende TikTok (optionnel)</span>
                        <input value={c.caption || ""} placeholder={`Auto : hook + #${tag} #gta6 #gtavi #twitch #clip`}
                          onChange={(e) => patch(c.rank, "caption", e.target.value)} /></label>
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </div>

      <div className="selbar">
        <b>{pick.size ? plural(pick.size, "moment choisi", "moments choisis") : "Coche des moments"}</b>
        <button className="btn primary" disabled={!pick.size || creating} onClick={create}>
          {creating ? "Création…" : `Créer ${pick.size ? plural(pick.size, "clip") : "les clips"}`}
        </button>
      </div>
    </div>
  );
}

function Expandable({ text }: { text: string }) {
  const [full, setFull] = useState(false);
  return <p className={`cand-txt${full ? " full" : ""}`} onClick={() => setFull(!full)} title="Cliquer pour tout lire">{text}</p>;
}

function FacecamDraw({ name, rect, onChange }: { name: string; rect: number[] | null; onChange: (r: number[]) => void }) {
  const img = useRef<HTMLImageElement>(null);
  const start = useRef<number[] | null>(null);
  const [, force] = useState(0);

  useEffect(() => {
    const r = () => force((n) => n + 1);
    window.addEventListener("resize", r);
    return () => window.removeEventListener("resize", r);
  }, []);

  const pt = (e: React.PointerEvent) => {
    const el = img.current!, b = el.getBoundingClientRect(), k = el.naturalWidth / b.width;
    return [Math.max(0, Math.min(b.width, e.clientX - b.left)) * k, Math.max(0, Math.min(b.height, e.clientY - b.top)) * k];
  };

  const el = img.current;
  const k = el && el.naturalWidth ? el.clientWidth / el.naturalWidth : 0;

  return (
    <div className="fcwrap"
      onPointerDown={(e) => { start.current = pt(e); (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); }}
      onPointerMove={(e) => {
        const a = start.current;
        if (!a) return;
        const b = pt(e);
        onChange([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])].map(Math.round));
      }}
      onPointerUp={() => { start.current = null; }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img ref={img} src={`/frame/${enc(name)}`} alt="Image du stream" draggable={false} onLoad={() => force((n) => n + 1)} />
      {rect && k > 0 && (
        <div className="fcrect" style={{ left: rect[0] * k, top: rect[1] * k, width: rect[2] * k, height: rect[3] * k }} />
      )}
    </div>
  );
}
