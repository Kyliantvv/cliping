"use client";
// Envoi vers l'iPhone par le Wi-Fi : QR code -> page de téléchargement servie par le PC (lien 20 min)
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { api, type Clip } from "@/lib/api";
import { Icon } from "./ui";

type Share = { token: string; url: string; expires: number };

export default function QrDialog({ clip, onClose, onIcloud, onDownloaded }: {
  clip: Clip;
  onClose: () => void;
  onIcloud: (c: Clip) => void;
  onDownloaded: () => void;
}) {
  const dlg = useRef<HTMLDialogElement>(null);
  const [share, setShare] = useState<Share | null>(null);
  const [img, setImg] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  const [left, setLeft] = useState(0);

  useEffect(() => {
    dlg.current?.showModal();
    api<Share>("/api/share", { key: clip.key })
      .then(async (s) => {
        setShare(s);
        setImg(await QRCode.toDataURL(s.url, { margin: 1, width: 560, color: { dark: "#0b0612", light: "#ffffff" } }));
      })
      .catch((e) => setErr((e as Error).message));
  }, [clip.key]);

  // compte à rebours + détection du téléchargement sur le téléphone
  useEffect(() => {
    if (!share) return;
    const t = setInterval(async () => {
      setLeft(Math.max(0, share.expires - Math.floor(Date.now() / 1000)));
      if (done) return;
      const r = await api<{ downloads: number }>("/api/share/status", { token: share.token }).catch(() => null);
      if (r && r.downloads > 0) { setDone(true); onDownloaded(); }
    }, 1500);
    return () => clearInterval(t);
  }, [share, done, onDownloaded]);

  return (
    <dialog ref={dlg} className="dialog qr-dialog" onClose={onClose}>
      <div className="qr-head">
        <h3>Sur l&apos;iPhone</h3>
        <button className="btn ghost icon" aria-label="Fermer" onClick={() => dlg.current?.close()}><Icon name="x" /></button>
      </div>
      <p className="muted small qr-hook">{clip.hook || clip.name}</p>

      {err ? (
        <div className="banner err">{err}</div>
      ) : (
        <div className={`qr-box${done ? " done" : ""}`}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {img ? <img src={img} alt="QR code du clip" /> : <span className="spinner" />}
          {done && <div className="qr-done"><Icon name="check" size={34} /><b>Téléchargé sur le téléphone</b></div>}
        </div>
      )}

      {share && !err && (
        <>
          <ol className="qr-steps">
            <li>Ouvre l&apos;<b>appareil photo</b> de l&apos;iPhone et vise le QR code, puis touche le lien.</li>
            <li>Touche <b>Télécharger la vidéo</b>, puis ouvre-la dans les téléchargements de Safari.</li>
            <li><b>Partager</b> › <b>Enregistrer la vidéo</b> : elle arrive dans Photos.</li>
          </ol>
          <p className="muted small">
            iPhone sur le <b>même Wi-Fi</b> que le PC. Lien valable {Math.floor(left / 60)} min {String(left % 60).padStart(2, "0")} s.
            Si la page ne s&apos;ouvre pas : Windows a peut-être bloqué Python, accepte la fenêtre « Pare-feu » (réseau privé).
          </p>
          <p className="muted small qr-url">{share.url}</p>
        </>
      )}

      <div className="dialog-actions">
        <button className="btn ghost" onClick={() => { onIcloud(clip); dlg.current?.close(); }}>Envoyer via iCloud</button>
        <button className="btn primary" onClick={() => dlg.current?.close()}>{done ? "Terminé" : "Fermer"}</button>
      </div>
    </dialog>
  );
}
