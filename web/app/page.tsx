"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, plural, type Clip, type Watch } from "@/lib/api";
import { Icon, UIProvider } from "@/components/ui";
import ClipsView from "@/components/ClipsView";
import NewStreamView from "@/components/NewStreamView";
import IphoneDialog from "@/components/IphoneDialog";
import StreamersView from "@/components/StreamersView";

type View = "clips" | "new" | "streamers";

export default function Page() {
  return (
    <UIProvider>
      <App />
    </UIProvider>
  );
}

function App() {
  const [view, setView] = useState<View>("clips");
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [error, setError] = useState("");
  const [iphone, setIphone] = useState<{ retry?: Clip } | null>(null);
  const busy = useRef(false);
  const [watch, setWatch] = useState<Watch | null>(null);
  const remote = watch?.remote ?? false;

  useEffect(() => {
    const get = () => api<Watch>("/api/watch").then(setWatch).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, []);

  const load = useCallback(async () => {
    try {
      const j = await api<{ clips: Clip[] }>("/api/clips");
      setClips(j.clips);
      setError("");
    } catch {
      setError(remote ? "Serveur injoignable : réessaie dans un instant" : "Serveur injoignable : lance lancer_app.bat");
    }
  }, [remote]);

  useEffect(() => {
    load();
  }, [load]);

  // rafraîchit plus souvent pendant un rendu
  const rendering = clips?.some((c) => c.job) ?? false;
  useEffect(() => {
    if (view !== "clips") return;
    const t = setInterval(() => !busy.current && load(), rendering ? 3000 : 20000);
    return () => clearInterval(t);
  }, [view, rendering, load]);

  const ready = clips?.filter((c) => c.video).length ?? 0;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">VI</span>
          <span>
            <b>Clips GTA VI</b>
            <small>Studio TikTok</small>
          </span>
        </div>
        <nav className="nav">
          <button className={view === "clips" ? "on" : ""} onClick={() => setView("clips")}>
            <Icon name="film" /> <span>Mes clips</span>
            {clips && <em>{ready}</em>}
          </button>
          <button className={view === "new" ? "on" : ""} onClick={() => setView("new")}>
            <Icon name="plus" /> <span>Nouveau stream</span>
          </button>
          {remote && (
            <button className={view === "streamers" ? "on" : ""} onClick={() => setView("streamers")}>
              <Icon name="radio" /> <span>Streamers suivis</span>
              {watch && Object.values(watch.live.streamers || {}).some((s) => s.recording) && <em className="rec-badge">REC</em>}
            </button>
          )}
          {!remote && (
            <button onClick={() => setIphone({})}>
              <Icon name="phone" /> <span>iPhone</span>
            </button>
          )}
        </nav>
        <div className="sidebar-foot">
          {rendering ? (
            <span className="live-dot">{plural(clips!.filter((c) => c.job).length, "rendu")} en cours</span>
          ) : remote ? (
            <span className="foot-remote">
              <span className={watch?.worker.online ? "ok-txt" : "warn-txt"}>PC {watch?.worker.online ? "en ligne" : "hors ligne"}</span>
              <a href="/logout"><Icon name="logout" size={13} /> Déconnexion</a>
            </span>
          ) : (
            <span>127.0.0.1 · ce PC uniquement</span>
          )}
        </div>
      </aside>

      <main className="main">
        {error && <div className="banner err">{error}</div>}
        {view === "clips" ? (
          <ClipsView clips={clips} reload={load} busy={busy} onIphoneSetup={(c) => setIphone({ retry: c })} />
        ) : view === "streamers" ? (
          <StreamersView />
        ) : (
          <NewStreamView remote={remote} onCreated={() => { setView("clips"); load(); }} />
        )}
      </main>

      {iphone && <IphoneDialog retry={iphone.retry} onClose={() => setIphone(null)} onSent={load} />}
    </div>
  );
}
