"use client";
import { memo, useRef } from "react";
import { STATUS, fmtDur, fmtNum, relTime, syncState, thumbUrl, videoUrl, type Clip } from "@/lib/api";
import { Icon } from "./ui";

const SYNC = { ok: "Synchro OK", warn: "Synchro à vérifier", none: "Synchro non vérifiée" };

function ClipCard({ clip: c, selecting, selected, showCreator, onClick }: {
  clip: Clip;
  selecting: boolean;
  selected: boolean;
  showCreator: boolean;
  onClick: (shift: boolean) => void;
}) {
  const v = useRef<HTMLVideoElement>(null);
  const s = syncState(c);

  const enter = () => {
    const el = v.current;
    if (!el || selecting) return;
    if (!el.src) {
      el.src = videoUrl(c) + "#t=" + Math.round((c.duration || 10) * 0.3);
      el.oncanplay = () => el.classList.add("ready");
    }
    el.play().catch(() => {});
  };

  return (
    <button
      type="button"
      className={`card${selected ? " sel" : ""}${selecting ? " selecting" : ""}${c.video ? "" : " missing"}`}
      onClick={(e) => onClick(e.shiftKey)}
      onMouseEnter={c.video ? enter : undefined}
      onMouseLeave={() => v.current?.pause()}
      aria-pressed={selecting ? selected : undefined}
    >
      <div className="media">
        {c.video ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img loading="lazy" src={thumbUrl(c)} alt="" />
            <video ref={v} muted loop playsInline preload="none" />
          </>
        ) : (
          <div className="missing-msg">Fichier absent<br />Re-rendre pour le recréer</div>
        )}
        <div className="media-top">
          <span className={`dot ${s}`} title={SYNC[s]} />
          <span className="chips-r">
            {c.sent_at && <span className="pill-s violet" title={`Envoyé sur l'iPhone le ${c.sent_at}`}><Icon name="phone" size={12} /></span>}
            <span className="pill-s">{fmtDur(c.duration)}</span>
          </span>
        </div>
        <div className="media-bottom">
          <div className="hook">{c.hook || c.name}</div>
        </div>
        {c.job && <div className="job"><span className="spinner" /> Rendu {c.job}</div>}
        {selecting && <span className="check" aria-hidden="true">{selected && <Icon name="check" size={18} />}</span>}
      </div>
      <div className="card-foot">
        <span className={`status st-${c.status}`}>{STATUS[c.status]}</span>
        <span className="muted small truncate">
          {showCreator ? `${c.creator} · ` : ""}{relTime(c.date)}
        </span>
        {c.views ? <span className="muted small views"><Icon name="eye" size={12} /> {fmtNum(c.views)}</span> : null}
      </div>
    </button>
  );
}

export default memo(ClipCard);
