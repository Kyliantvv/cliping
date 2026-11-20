"use client";
// Petits outils d'interface partagés : toasts et boîte de confirmation (remplace window.confirm)
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

type ConfirmOpts = { title: string; body?: ReactNode; ok?: string; danger?: boolean };
type Ctx = { toast: (msg: string, kind?: "ok" | "err") => void; confirm: (o: ConfirmOpts) => Promise<boolean> };

const UICtx = createContext<Ctx>({ toast: () => {}, confirm: async () => false });
export const useUI = () => useContext(UICtx);

export function UIProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: "ok" | "err" }[]>([]);
  const [ask, setAsk] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const dlg = useRef<HTMLDialogElement>(null);

  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 4500 : 2200);
  }, []);

  const confirm = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setAsk({ ...o, resolve })), []);

  useEffect(() => {
    if (ask && dlg.current && !dlg.current.open) dlg.current.showModal();
  }, [ask]);

  const answer = (v: boolean) => {
    ask?.resolve(v);
    dlg.current?.close();
    setAsk(null);
  };

  return (
    <UICtx.Provider value={{ toast, confirm }}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.msg}
          </div>
        ))}
      </div>
      <dialog ref={dlg} className="dialog" onCancel={(e) => { e.preventDefault(); answer(false); }}>
        {ask && (
          <>
            <h3>{ask.title}</h3>
            {ask.body && <div className="dialog-body">{ask.body}</div>}
            <div className="dialog-actions">
              <button className="btn ghost" onClick={() => answer(false)}>
                Annuler
              </button>
              <button className={`btn ${ask.danger ? "danger-solid" : "primary"}`} autoFocus onClick={() => answer(true)}>
                {ask.ok || "Confirmer"}
              </button>
            </div>
          </>
        )}
      </dialog>
    </UICtx.Provider>
  );
}

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

const PATHS = {
  film: (<><rect x="2" y="3" width="20" height="18" rx="3" /><path d="M7 3v18M17 3v18M2 8h5M2 16h5M17 8h5M17 16h5" /></>),
  plus: <path d="M12 5v14M5 12h14" />,
  phone: (<><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M11 18h2" /></>),
  search: (<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
  check: <path d="M20 6 9 17l-5-5" />,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  trash: (<><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" /><path d="M10 11v6M14 11v6" /></>),
  copy: (<><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>),
  folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  download: <path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16" />,
  left: <path d="m15 18-6-6 6-6" />,
  right: <path d="m9 18 6-6-6-6" />,
  play: <path d="M7 4v16l13-8z" />,
  refresh: <path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  eye: (<><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>),
  heart: <path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />,
  select: (<><rect x="3" y="3" width="18" height="18" rx="4" /><path d="m8 12 3 3 5-6" /></>),
  send: <path d="m22 2-7 20-4-9-9-4zM22 2 11 13" />,
  clock: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  radio: (<><circle cx="12" cy="12" r="2" /><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5" /></>),
  logout: <path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3" />,
} as const;
