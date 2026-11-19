"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, plural, type Clip } from "@/lib/api";
import { Icon, UIProvider } from "@/components/ui";
import ClipsView from "@/components/ClipsView";
import NewStreamView from "@/components/NewStreamView";
import IphoneDialog from "@/components/IphoneDialog";

type View = "clips" | "new";

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

  const load = useCallback(async () => {
    try {
      const j = await api<{ clips: Clip[] }>("/api/clips");
      setClips(j.clips);
      setError("");
    } catch {
      setError("Serveur injoignable : lance lancer_app.bat");
    }
  }, []);

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
          <button onClick={() => setIphone({})}>
            <Icon name="phone" /> <span>iPhone</span>
          </button>
        </nav>
        <div className="sidebar-foot">
          {rendering ? (
            <span className="live-dot">{plural(clips!.filter((c) => c.job).length, "rendu")} en cours</span>
          ) : (
            <span>127.0.0.1 · ce PC uniquement</span>
          )}
        </div>
      </aside>

      <main className="main">
        {error && <div className="banner err">{error}</div>}
        {view === "clips" ? (
          <ClipsView clips={clips} reload={load} busy={busy} onIphoneSetup={(c) => setIphone({ retry: c })} />
        ) : (
          <NewStreamView onCreated={() => { setView("clips"); load(); }} />
        )}
      </main>

      {iphone && <IphoneDialog retry={iphone.retry} onClose={() => setIphone(null)} onSent={load} />}
    </div>
  );
}
