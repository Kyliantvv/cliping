"use client";
import { useCallback, useEffect, useState } from "react";
import { api, plural, relTime, type Watch } from "@/lib/api";
import { Icon, useUI } from "./ui";

// Streamers suivis par le VPS (tools/watcher.py) : en live dans une catégorie suivie -> enregistrement automatique
export default function StreamersView() {
  const { toast, confirm } = useUI();
  const [w, setW] = useState<Watch | null>(null);
  const [add, setAdd] = useState("");
  const [cat, setCat] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try { setW(await api<Watch>("/api/watch")); } catch { /* bandeau d'erreur géré par la page */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const save = async (streamers: string[], categories: string[]) => {
    setSaving(true);
    try {
      setW(await api<Watch>("/api/watch", { streamers, categories }));
      return true;
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); return false; }
    finally { setSaving(false); }
  };

  if (!w) return <div className="empty"><span className="spinner" /></div>;

  const addStreamer = async () => {
    const names = add.split(/[\s,;]+/).map((s) => s.replace(/^(https?:\/\/)?(www\.)?twitch\.tv\//i, "").replace(/\/.*$/, "").toLowerCase()).filter(Boolean);
    if (!names.length) return;
    if (await save([...w.streamers, ...names.filter((n) => !w.streamers.includes(n))], w.categories)) {
      setAdd("");
      toast(names.length > 1 ? `${names.length} streamers ajoutés` : `${names[0]} ajouté`);
    }
  };
  const remove = async (s: string) => {
    const rec = w.live.streamers?.[s]?.recording;
    if (rec && !(await confirm({ title: `Retirer ${s} ?`, body: "Son live est en cours d'enregistrement : l'enregistrement s'arrête, les moments déjà repérés sont gardés.", ok: "Retirer", danger: true }))) return;
    if (await save(w.streamers.filter((x) => x !== s), w.categories)) toast(`${s} retiré`);
  };
  const addCat = async () => {
    const c = cat.trim();
    if (!c || w.categories.includes(c)) return;
    if (await save(w.streamers, [...w.categories, c])) setCat("");
  };

  const recording = Object.values(w.live.streamers || {}).filter((s) => s.recording).length;

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Streamers suivis</h1>
          <p className="muted">Dès qu&apos;un streamer est en live dans une catégorie suivie, le VPS enregistre et repère les moments forts. Toutes les 3 h de live, ou à la fin, ils arrivent dans « Nouveau stream ».</p>
        </div>
      </header>

      <section className="panel status-grid">
        <div>
          <span className="field-label">Surveillance (VPS)</span>
          {w.watcher_ok
            ? <b className="ok-txt">Active{recording ? ` · ${plural(recording, "enregistrement")}` : ""}</b>
            : <b className="err-txt">Arrêtée</b>}
          {w.live.updated && <span className="muted small">Vérifié {relTime(w.live.updated)}</span>}
        </div>
        <div>
          <span className="field-label">PC (Whisper, rendus)</span>
          {w.worker.online ? <b className="ok-txt">En ligne</b> : <b className="warn-txt">Hors ligne</b>}
          <span className="muted small">
            {w.worker.online ? w.worker.name : w.worker.seen ? `Vu ${relTime(w.worker.seen)}` : "Jamais connecté"}
            {w.tasks_pending ? ` · ${plural(w.tasks_pending, "tâche")} en attente` : ""}
          </span>
        </div>
        <div>
          <span className="field-label">Disque du VPS</span>
          <b className={w.live.low_disk ? "err-txt" : ""}>{w.live.free_gb != null ? `${w.live.free_gb} Go libres` : "–"}</b>
          {w.live.low_disk && <span className="muted small">Enregistrements arrêtés</span>}
        </div>
      </section>

      <section className="panel">
        <h3 className="panel-title">Chaînes <span className="muted small">{plural(w.streamers.length, "chaîne")}</span></h3>
        <ul className="streamers">
          {w.streamers.map((s) => {
            const st = w.live.streamers?.[s];
            const followed = st?.game && w.categories.includes(st.game);
            return (
              <li key={s}>
                <a className="st-name" href={`https://www.twitch.tv/${s}`} target="_blank" rel="noreferrer">{st?.display || s}</a>
                <span className="st-state">
                  {!st ? <span className="muted small">En attente de vérification</span>
                    : st.exists === false ? <span className="err-txt small">Pseudo introuvable sur Twitch</span>
                    : st.recording ? <span className="live-dot rec">Enregistre · {st.game}{st.moments ? ` · ${plural(st.moments, "moment")}` : ""}</span>
                    : st.processing ? <span className="warn-txt small">Préparation des moments…</span>
                    : st.live ? <span className="small">En live · {st.game || "sans catégorie"}{followed ? "" : " (pas suivie)"}</span>
                    : <span className="muted small">Hors ligne</span>}
                  {st?.live && st.title && <span className="muted small truncate">{st.title}</span>}
                </span>
                <button className="btn ghost icon" aria-label={`Retirer ${s}`} title={`Retirer ${s}`} disabled={saving} onClick={() => remove(s)}>
                  <Icon name="x" size={16} />
                </button>
              </li>
            );
          })}
          {!w.streamers.length && <li className="muted">Aucune chaîne suivie.</li>}
        </ul>
        <form className="inline-add" onSubmit={(e) => { e.preventDefault(); addStreamer(); }}>
          <label className="field">
            <span className="field-label">Ajouter une chaîne</span>
            <input placeholder="Pseudo Twitch ou lien twitch.tv/…" value={add} onChange={(e) => setAdd(e.target.value)} />
          </label>
          <button className="btn primary" type="submit" disabled={saving || !add.trim()}><Icon name="plus" size={16} /> Ajouter</button>
        </form>
      </section>

      <section className="panel">
        <h3 className="panel-title">Catégories suivies</h3>
        <div className="chips">
          {w.categories.map((c) => (
            <span key={c} className="chip">
              {c}
              {w.categories.length > 1 && (
                <button aria-label={`Retirer ${c}`} title={`Retirer ${c}`} onClick={() => save(w.streamers, w.categories.filter((x) => x !== c))}>
                  <Icon name="x" size={13} />
                </button>
              )}
            </span>
          ))}
        </div>
        <form className="inline-add" onSubmit={(e) => { e.preventDefault(); addCat(); }}>
          <label className="field">
            <span className="field-label">Ajouter une catégorie <em>nom exact sur Twitch</em></span>
            <input placeholder="Grand Theft Auto VI" value={cat} onChange={(e) => setCat(e.target.value)} />
          </label>
          <button className="btn" type="submit" disabled={saving || !cat.trim()}><Icon name="plus" size={16} /> Ajouter</button>
        </form>
      </section>
    </div>
  );
}
