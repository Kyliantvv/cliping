"use client";
// Envoi vers l'iPhone par le Wi-Fi : QR code -> lien temporaire servi par le PC (20 min).
// Mode « Direct Photos » : le QR lance le raccourci iPhone « Clip GTA » qui télécharge et enregistre dans Photos.
// Mode « Safari » : page de téléchargement classique.
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { api, type Clip } from "@/lib/api";
import { Icon } from "./ui";

type Share = { token: string; url: string; expires: number };
type Mode = "raccourci" | "safari";

export const SHORTCUT_NAME = "Clip GTA";
const MODE_KEY = "iphone-mode";

function readMode(): Mode {
  try { return localStorage.getItem(MODE_KEY) === "safari" ? "safari" : "raccourci"; } catch { return "raccourci"; }
}

export default function QrDialog({ clip, onClose, onIcloud, onDownloaded }: {
  clip: Clip;
  onClose: () => void;
  onIcloud: (c: Clip) => void;
  onDownloaded: () => void;
}) {
  const dlg = useRef<HTMLDialogElement>(null);
  const [share, setShare] = useState<Share | null>(null);
  const [mode, setMode] = useState<Mode>("raccourci");
  const [img, setImg] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  const [left, setLeft] = useState(0);
  const [help, setHelp] = useState(false);

  useEffect(() => {
    setMode(readMode());
    dlg.current?.showModal();
    api<Share>("/api/share", { key: clip.key }).then(setShare).catch((e) => setErr((e as Error).message));
  }, [clip.key]);

  // contenu du QR selon le mode
  useEffect(() => {
    if (!share) return;
    const target = mode === "raccourci"
      ? `shortcuts://run-shortcut?name=${encodeURIComponent(SHORTCUT_NAME)}&input=text&text=${encodeURIComponent(share.url + "/video.mp4?dl=1")}`
      : share.url;
    QRCode.toDataURL(target, { margin: 1, width: 560, errorCorrectionLevel: "L", color: { dark: "#0b0612", light: "#ffffff" } })
      .then(setImg);
    try { localStorage.setItem(MODE_KEY, mode); } catch { /* navigation privée */ }
  }, [share, mode]);

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

      <div className="seg full" style={{ marginBottom: 14 }}>
        <button className={mode === "raccourci" ? "on" : ""} onClick={() => setMode("raccourci")}>Direct dans Photos</button>
        <button className={mode === "safari" ? "on" : ""} onClick={() => setMode("safari")}>Par Safari</button>
      </div>

      {err ? (
        <div className="banner err">{err}</div>
      ) : (
        <div className={`qr-box${done ? " done" : ""}`}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {img ? <img src={img} alt="QR code du clip" /> : <span className="spinner" />}
          {done && (
            <div className="qr-done">
              <Icon name="check" size={34} />
              <b>{mode === "raccourci" ? "Envoyé dans Photos" : "Téléchargé sur le téléphone"}</b>
            </div>
          )}
        </div>
      )}

      {share && !err && (mode === "raccourci" ? (
        <>
          <ol className="qr-steps">
            <li>Vise le QR code avec l&apos;<b>appareil photo</b>, touche <b>Ouvrir dans Raccourcis</b>.</li>
            <li>La vidéo est téléchargée et <b>enregistrée dans Photos</b> toute seule.</li>
          </ol>
          <button className="link" onClick={() => setHelp(!help)}>
            {help ? "Masquer" : "Première fois ? Créer le raccourci « Clip GTA » (1 min, une seule fois)"}
          </button>
          {help && (
            <ol className="qr-steps howto">
              <li>Sur l&apos;iPhone, ouvre l&apos;app <b>Raccourcis</b> › <b>+</b> en haut à droite.</li>
              <li>Renomme-le exactement <b>{SHORTCUT_NAME}</b> (touche le nom en haut).</li>
              <li>Ajoute l&apos;action <b>Obtenir le contenu de l&apos;URL</b>, et mets <b>Entrée du raccourci</b> comme URL.</li>
              <li>Ajoute l&apos;action <b>Enregistrer dans l&apos;album photo</b> (album : Récents).</li>
              <li>Touche <b>OK</b>. Au premier envoi, autorise l&apos;accès au <b>réseau local</b> et à <b>Photos</b>.</li>
            </ol>
          )}
        </>
      ) : (
        <ol className="qr-steps">
          <li>Vise le QR code avec l&apos;<b>appareil photo</b>, touche le lien.</li>
          <li>Touche <b>Télécharger la vidéo</b>, puis ouvre-la dans les téléchargements de Safari.</li>
          <li><b>Partager</b> › <b>Enregistrer la vidéo</b> : elle arrive dans Photos.</li>
        </ol>
      ))}

      {share && !err && (
        <p className="muted small">
          iPhone sur le <b>même Wi-Fi</b> que le PC. Lien valable {Math.floor(left / 60)} min {String(left % 60).padStart(2, "0")} s.
        </p>
      )}

      <div className="dialog-actions">
        <button className="btn ghost" onClick={() => { onIcloud(clip); dlg.current?.close(); }}>Envoyer via iCloud</button>
        <button className="btn primary" onClick={() => dlg.current?.close()}>{done ? "Terminé" : "Fermer"}</button>
      </div>
    </dialog>
  );
}
