"use client";
import { useCallback, useEffect, useState } from "react";
import { api, enc, plural, slugify, type Job, type Project } from "@/lib/api";
import { Icon, useUI } from "./ui";
import MomentsPicker from "./MomentsPicker";

const today = () => new Date().toISOString().slice(0, 10).replace(/-/g, "");

export default function NewStreamView({ onCreated, remote = false }: { onCreated: () => void; remote?: boolean }) {
  const { toast } = useUI();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [f, setF] = useState({ url: "", creator: "", name: "", start: "", end: "" });
  const [nameTouched, setNameTouched] = useState(false);
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    const [p, j] = await Promise.all([api<{ projects: Project[] }>("/api/projects"), api<{ jobs: Job[] }>("/api/jobs")]);
    setProjects(p.projects);
    setJobs(j.jobs.filter((x) => x.kind === "import"));
  }, []);

  useEffect(() => { load(); }, [load]);
  const active = jobs.some((j) => j.status === "en cours" || j.status === "en attente");
  useEffect(() => {
    if (!active || open) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, open, load]);

  const setCreator = (creator: string) =>
    setF((x) => ({ ...x, creator, name: nameTouched ? x.name : creator ? `${slugify(creator)}_gta6_${today()}` : "" }));

  const go = async () => {
    setSending(true);
    try {
      await api("/api/import", f);
      toast("Import lancé");
      setF({ url: "", creator: "", name: "", start: "", end: "" });
      setNameTouched(false);
      load();
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); }
    finally { setSending(false); }
  };

  if (open) return <MomentsPicker name={open} onBack={() => { setOpen(null); load(); }} onCreated={onCreated} />;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Nouveau stream</h1>
          <p className="muted">Lien Twitch, téléchargement, transcription, chat : les meilleurs moments sont repérés tout seuls.</p>
        </div>
      </header>

      <section className="panel">
        <div className="form-grid">
          <label className="field span2">
            <span className="field-label">Lien Twitch</span>
            <input placeholder="twitch.tv/videos/… ou twitch.tv/chaine" value={f.url} onChange={(e) => {
              const url = e.target.value;
              setF((x) => ({ ...x, url }));
              const m = url.match(/twitch\.tv\/(?!videos)([A-Za-z0-9_]+)/);
              if (m && !f.creator) setCreator(m[1]);
            }} />
          </label>
          <label className="field">
            <span className="field-label">Streamer</span>
            <input placeholder="Ex. Squeezie" value={f.creator} onChange={(e) => setCreator(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Nom du projet</span>
            <input placeholder={`streamer_gta6_${today()}`} value={f.name} onChange={(e) => { setNameTouched(true); setF({ ...f, name: e.target.value }); }} />
          </label>
          <label className="field">
            <span className="field-label">À partir de</span>
            <input placeholder="0:00:00" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Jusqu&apos;à</span>
            <input placeholder="(fin)" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} />
          </label>
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={sending} onClick={go}><Icon name="download" size={16} /> Télécharger et analyser</button>
          <span className="muted small">
            {remote
              ? "Le PC télécharge la VOD et fait l'analyse (il doit être allumé). Les lives des streamers suivis arrivent ici tout seuls."
              : "Compte 20 à 40 min pour 2 h de stream. Ça continue même si tu fermes la page. VOD réservée aux abonnés : la télécharger toi-même dans input/."}
          </span>
        </div>
      </section>

      {jobs.slice(0, 4).map((j) => (
        <section className="panel" key={j.id}>
          <div className="job-head">
            <span className={`jobstate ${j.status === "fini" ? "ok" : j.status === "erreur" ? "err" : "run"}`}>
              {(j.status === "en cours" || j.status === "en attente") && <span className="spinner" />} {j.status}
            </span>
            <b>{j.key}</b>
            {(j as Job & { auto?: boolean }).auto && <span className="pill-s violet">live auto</span>}
            {j.status === "fini" && <button className="btn primary" style={{ marginLeft: "auto" }} onClick={() => setOpen(j.key)}>Choisir les moments</button>}
          </div>
          <pre className="joblog">{j.log.trim().split("\n").slice(-12).join("\n") || "En attente…"}</pre>
        </section>
      ))}

      <h3 className="section-title">Streams analysés <span>{projects ? plural(projects.length, "stream") : ""}</span></h3>
      {projects && !projects.length && <div className="empty"><b>Aucun stream analysé</b><span className="muted">Colle un lien au-dessus.</span></div>}
      <div className="projects">
        {projects?.map((p) => (
          <article key={p.name} className="proj">
            {p.frame
              ? /* eslint-disable-next-line @next/next/no-img-element */ <img loading="lazy" src={`/frame/${enc(p.name)}`} alt="" />
              : <div className="proj-ph" />}
            <div className="proj-body">
              <b>{p.creator}</b>
              <span className="muted small truncate">{p.auto && "Live auto · "}{p.name} · {plural(p.used.length, "clip créé", "clips créés")}</span>
            </div>
            {p.analyzed && p.video
              ? <button className="btn" onClick={() => setOpen(p.name)}>Choisir des moments</button>
              : <span className="muted small">{p.video ? "Analyse pas finie" : "Vidéo source absente"}</span>}
          </article>
        ))}
      </div>
    </div>
  );
}
