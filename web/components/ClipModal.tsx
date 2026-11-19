"use client";
import { useEffect, useRef, useState } from "react";
import { STATUS, api, fmtDur, fullCaption, relTime, videoUrl, type Clip, type Job, type Status } from "@/lib/api";
import { Icon, useUI } from "./ui";

type Tab = "publier" | "modifier" | "infos";

export default function ClipModal({ clip: c, list, onNavigate, onClose, reload, copyCaption, sendToIphone, onDelete }: {
  clip: Clip;
  list: Clip[];
  onNavigate: (key: string) => void;
  onClose: () => void;
  reload: () => Promise<void>;
  copyCaption: (c: Clip) => void;
  sendToIphone: (c: Clip) => void;
  onDelete: (c: Clip) => void;
}) {
  const { toast } = useUI();
  const dlg = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>("publier");
  const [form, setForm] = useState(() => pubForm(c));
  const [edit, setEdit] = useState(() => editForm(c));
  const [job, setJob] = useState<Job | null>(null);

  // ouvre le dialog une fois, puis suit le clip courant
  useEffect(() => {
    dlg.current?.showModal();
  }, []);
  useEffect(() => {
    setForm(pubForm(c));
    setEdit(editForm(c));
    setJob(null);
  }, [c.key]); // eslint-disable-line react-hooks/exhaustive-deps

  const i = list.findIndex((x) => x.key === c.key);
  const go = (d: number) => {
    if (i < 0 || list.length < 2) return;
    onNavigate(list[(i + d + list.length) % list.length].key);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      if (el && /INPUT|TEXTAREA|SELECT/.test(el.tagName)) return;
      if (e.key === "ArrowRight") go(1);
      if (e.key === "ArrowLeft") go(-1);
      if (e.key.toLowerCase() === "c" && !e.ctrlKey && !e.metaKey) copyCaption(c);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // suivi du rendu en cours
  useEffect(() => {
    if (!c.job && !job) return;
    let stop = false;
    const tick = async () => {
      const { jobs } = await api<{ jobs: Job[] }>("/api/jobs");
      const j = jobs.find((x) => x.key === c.key && x.kind === "render");
      if (stop || !j) return;
      setJob(j);
      if (j.status === "en attente" || j.status === "en cours") setTimeout(tick, 2500);
      else {
        toast(j.status === "fini" ? "Rendu terminé" : "Échec du rendu", j.status === "fini" ? "ok" : "err");
        reload();
      }
    };
    tick();
    return () => { stop = true; };
  }, [c.key, c.job, job?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const setStatus = async (s: Status) => {
    await api("/api/state", { key: c.key, status: s });
    toast(STATUS[s]);
    reload();
  };

  const savePub = async () => {
    try {
      await api("/api/state", {
        key: c.key, url: form.url.trim(), notes: form.notes, scheduled: form.scheduled,
        views: form.views === "" ? null : Number(form.views), likes: form.likes === "" ? null : Number(form.likes),
        ...(form.scheduled && c.status === "a_poster" ? { status: "programme" } : {}),
      });
      toast("Enregistré");
      reload();
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); }
  };

  const render = async (withEdits: boolean) => {
    try {
      const r = withEdits
        ? await api<{ job: Job }>("/api/edit", { key: c.key, ...edit })
        : await api<{ job: Job }>("/api/render", { key: c.key });
      setJob(r.job);
      toast("Rendu lancé (1 à 2 min)");
      reload();
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); }
  };

  const running = job && (job.status === "en attente" || job.status === "en cours");
  const s = c.sync;

  return (
    <dialog ref={dlg} className="modal" onClose={onClose} onClick={(e) => e.target === dlg.current && dlg.current?.close()}>
      <div className="modal-inner">
        <div className="player">
          {c.video ? (
            <video key={c.key + c.mtime} src={videoUrl(c)} controls autoPlay playsInline />
          ) : (
            <div className="missing-msg">Fichier absent : re-rendre le clip</div>
          )}
        </div>

        <div className="side">
          <div className="side-head">
            <div className="navbtns">
              <button className="btn ghost icon" onClick={() => go(-1)} aria-label="Clip précédent"><Icon name="left" /></button>
              <span className="muted small">{i + 1} / {list.length}</span>
              <button className="btn ghost icon" onClick={() => go(1)} aria-label="Clip suivant"><Icon name="right" /></button>
            </div>
            <button className="btn ghost icon" onClick={() => dlg.current?.close()} aria-label="Fermer"><Icon name="x" /></button>
          </div>

          <span className="kicker">{c.creator}</span>
          <h2 className="modal-title">{c.hook || c.name}</h2>
          <p className="muted small">{c.name} · {fmtDur(c.duration)} · rendu {relTime(c.date)}</p>

          <div className="tabs" role="tablist">
            {(["publier", "modifier", "infos"] as Tab[]).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                {{ publier: "Publier", modifier: "Modifier", infos: "Infos" }[t]}
              </button>
            ))}
          </div>

          {tab === "publier" && (
            <div className="tab-body">
              <div className="capbox">{fullCaption(c) || <i className="muted">Pas de légende dans legendes.txt</i>}</div>
              <div className="btn-row">
                <button className="btn primary" onClick={() => copyCaption(c)}><Icon name="copy" size={16} /> Copier <kbd>C</kbd></button>
                {c.video && <button className="btn" onClick={() => sendToIphone(c)}><Icon name="send" size={16} /> {c.sent_at ? "Renvoyer" : "iPhone"}</button>}
                {c.video && <a className="btn ghost" href={videoUrl(c)} download={`${c.name}.mp4`}><Icon name="download" size={16} /> mp4</a>}
                {c.video && <button className="btn ghost" onClick={() => api("/api/open", { key: c.key })}><Icon name="folder" size={16} /> Dossier</button>}
              </div>
              {c.sent_at && <p className="muted small">Envoyé sur l&apos;iPhone le {c.sent_at}</p>}

              <div className="field-label">Statut</div>
              <div className="seg full">
                {(Object.keys(STATUS) as Status[]).map((k) => (
                  <button key={k} className={c.status === k ? "on" : ""} onClick={() => setStatus(k)}>{STATUS[k]}</button>
                ))}
              </div>
              <div className="grid2">
                <Field label="Programmé pour"><input type="datetime-local" value={form.scheduled} onChange={(e) => setForm({ ...form, scheduled: e.target.value })} /></Field>
                <Field label="Lien TikTok"><input placeholder="https://www.tiktok.com/@…" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
                <Field label="Vues"><input type="number" min={0} value={form.views} onChange={(e) => setForm({ ...form, views: e.target.value })} /></Field>
                <Field label="Likes"><input type="number" min={0} value={form.likes} onChange={(e) => setForm({ ...form, likes: e.target.value })} /></Field>
              </div>
              <Field label="Notes"><textarea rows={2} placeholder="Hook alternatif, retours du public…" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
              <div className="btn-row">
                <button className="btn" onClick={savePub}>Enregistrer</button>
                {c.url && <a className="btn ghost" href={c.url} target="_blank" rel="noopener noreferrer"><Icon name="external" size={16} /> TikTok</a>}
                {c.posted_at && <span className="muted small">Posté le {c.posted_at}</span>}
              </div>
            </div>
          )}

          {tab === "modifier" && (
            <div className="tab-body">
              <Field label="Hook (titre affiché)"><input maxLength={80} value={edit.hook} onChange={(e) => setEdit({ ...edit, hook: e.target.value })} /></Field>
              <div className="grid3">
                <Field label="Début (source)"><input value={edit.start} onChange={(e) => setEdit({ ...edit, start: e.target.value })} /></Field>
                <Field label="Fin (source)"><input value={edit.end} onChange={(e) => setEdit({ ...edit, end: e.target.value })} /></Field>
                <Field label="Texte de fin"><input placeholder="(aucun)" value={edit.outro} onChange={(e) => setEdit({ ...edit, outro: e.target.value })} /></Field>
              </div>
              <div className="btn-row">
                <button className="btn primary" disabled={!!running} onClick={() => render(true)}><Icon name="refresh" size={16} /> Enregistrer et re-rendre</button>
                <button className="btn ghost" disabled={!!running} onClick={() => render(false)}>Re-rendre tel quel</button>
              </div>
              {job && (
                <div className="joblog">
                  <b className={`jobstate ${job.status === "fini" ? "ok" : job.status === "erreur" ? "err" : "run"}`}>Rendu : {job.status}</b>
                  {"\n" + job.log.trim().split("\n").slice(-8).join("\n")}
                </div>
              )}
            </div>
          )}

          {tab === "infos" && (
            <div className="tab-body">
              <div className="info-row"><span>Synchro sous-titres</span>
                {s ? <b className={s.ok ? "ok" : "warn"}>{s.ok ? "OK" : "À vérifier"}</b> : <b className="muted">non vérifiée</b>}
              </div>
              {s && <p className="muted small">Écart médian {s.median} s · mots sur un blanc {s.silence_pct} % · décalage {s.offset > 0 ? "+" : ""}{s.offset} s</p>}
              <div className="info-row"><span>Passage source</span><b>{c.start} → {c.end}</b></div>
              <div className="info-row"><span>Projet</span><b>{c.source}</b></div>
              <div className="danger-zone">
                <div>
                  <b>Supprimer ce clip</b>
                  <p className="muted small">Efface le mp4 du PC (corbeille Windows), le retire de l&apos;appli et de legendes.txt.</p>
                </div>
                <button className="btn danger" onClick={() => onDelete(c)}><Icon name="trash" size={16} /> Supprimer</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

const pubForm = (c: Clip) => ({
  url: c.url || "", notes: c.notes || "", scheduled: c.scheduled || "",
  views: c.views == null ? "" : String(c.views), likes: c.likes == null ? "" : String(c.likes),
});
const editForm = (c: Clip) => ({ hook: c.hook || "", start: String(c.start || ""), end: String(c.end || ""), outro: c.outro || "" });
