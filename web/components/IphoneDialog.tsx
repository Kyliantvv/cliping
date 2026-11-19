"use client";
import { useEffect, useRef, useState } from "react";
import { api, type Clip, type IphoneSettings } from "@/lib/api";
import { useUI } from "./ui";

export default function IphoneDialog({ retry, onClose, onSent }: { retry?: Clip; onClose: () => void; onSent: () => void }) {
  const { toast } = useUI();
  const dlg = useRef<HTMLDialogElement>(null);
  const [st, setSt] = useState<IphoneSettings | null>(null);
  const [dir, setDir] = useState("");

  useEffect(() => {
    dlg.current?.showModal();
    api<IphoneSettings>("/api/settings").then((s) => { setSt(s); setDir(s.saved || ""); }).catch(() => {});
  }, []);

  const save = async () => {
    try {
      const r = await api<{ iphone_dir: string | null }>("/api/settings", { iphone_dir: dir });
      if (!r.iphone_dir) return toast("Toujours aucun dossier iCloud : active Photos iCloud d'abord", "err");
      toast("Dossier enregistré");
      if (retry) {
        await api("/api/send", { key: retry.key });
        toast("Envoyé : il arrive dans Photos d'ici 1 à 2 min");
        onSent();
      }
      dlg.current?.close();
    } catch (e) { toast("Erreur : " + (e as Error).message, "err"); }
  };

  return (
    <dialog ref={dlg} className="dialog" onClose={onClose}>
      <h3>Envoyer les clips sur l&apos;iPhone</h3>
      <div className="dialog-body">
        {st && (st.iphone_dir
          ? <p>Dossier utilisé : <b className="ok">{st.iphone_dir}</b></p>
          : <p><b className="warn">Aucun dossier iCloud Photos trouvé sur ce PC.</b></p>)}
        <ol>
          <li>Dans <b>iCloud pour Windows</b>, ouvre Photos et active « Téléchargez et stockez la photothèque de votre PC sur iCloud ».</li>
          <li>Sur l&apos;iPhone : Réglages › ton nom › iCloud › Photos activé.</li>
          <li>Le dossier <code>Images\iCloud Photos\Photos</code> est alors détecté tout seul. Sinon, colle son chemin ci-dessous.</li>
        </ol>
        {st && st.detected.length > 0 && <p className="muted small">Détectés : {st.detected.join(" · ")}</p>}
        <label className="field">
          <span className="field-label">Dossier (optionnel)</span>
          <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="C:\Users\…\Pictures\iCloud Photos\Photos" />
        </label>
      </div>
      <div className="dialog-actions">
        <button className="btn ghost" onClick={() => dlg.current?.close()}>Fermer</button>
        <button className="btn primary" onClick={save}>{retry ? "Enregistrer et envoyer" : "Enregistrer"}</button>
      </div>
    </dialog>
  );
}
