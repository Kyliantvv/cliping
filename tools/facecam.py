"""Detection automatique de la facecam d'un stream.

Principe : on detecte les visages (UltraFace, onnxruntime) sur ~24 images reparties dans la video, sur l'image
entiere et sur ses 4 quarts (pour les petites facecams). Un visage qui reste au MEME endroit dans la plupart
des images = la facecam (les visages du jeu, eux, bougent). On agrandit ensuite la boite du visage pour
obtenir le cadre de la facecam.

Usage :
    python tools/facecam.py input/mon_stream.mp4 [work/mon_stream]
Sortie : work/<video>/facecam.json {"rect": [x, y, w, h], ...} + facecam_check.jpg (cadre dessine, a verifier).
"""
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "models", "ultraface-RFB-320.onnx")
MW, MH = 320, 240


def _frame(video, t, w, h):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if len(r.stdout) != w * h * 3:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3)


def _resize(img, w, h):
    ys = (np.arange(h) * img.shape[0] / h).astype(int)
    xs = (np.arange(w) * img.shape[1] / w).astype(int)
    return img[ys][:, xs]


def _nms(boxes, scores, thr=0.3):
    order = np.argsort(-scores)
    keep = []
    while len(order):
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        iou = inter / (area(boxes[i]) + area(boxes[order[1:]]) - inter + 1e-9)
        order = order[1:][iou < thr]
    return keep


def _faces(sess, img, conf=0.75):
    x = (_resize(img, MW, MH).astype(np.float32) - 127.0) / 128.0
    scores, boxes = sess.run(None, {"input": x.transpose(2, 0, 1)[None]})
    s, b = scores[0, :, 1], boxes[0]
    m = s > conf
    s, b = s[m], b[m]
    if not len(s):
        return []
    return [(b[i], float(s[i])) for i in _nms(b, s)]


def _refine(mx, my, face, aw, ah):
    """Cherche les 4 bords fixes de la facecam autour du visage (contours qui persistent d'une image a l'autre)."""
    cx, cy, fw, fh = face
    ys = slice(int(max(0, cy - fh)), int(min(ah, cy + fh)))
    xs = slice(int(max(0, cx - fw)), int(min(aw, cx + fw)))

    def best(profile, lo, hi, is_low_side):
        lo, hi = int(max(0, lo)), int(min(len(profile), hi))
        if hi - lo < 3:
            return None, 0
        seg = profile[lo:hi]
        i = int(np.argmax(seg))
        strength = seg[i] / (np.median(profile) + 1e-6)
        return lo + i + (0 if is_low_side else 1), strength

    colx = mx[ys].mean(axis=0)  # force des contours verticaux par colonne (sur la hauteur du visage)
    rowy = my[:, xs].mean(axis=1)
    left, sl = best(colx, cx - 4 * fw, cx - 0.4 * fw, True)
    right, sr = best(colx, cx + 0.4 * fw, cx + 4 * fw, False)
    top, st = best(rowy, cy - 3.5 * fh, cy - 0.25 * fh, True)
    bottom, sb = best(rowy, cy + 0.25 * fh, cy + 4 * fh, False)
    # bord d'image = bord valable (facecam collee au coin)
    if left is None or sl < 2.5:
        left, sl = (0, 9) if cx - 4 * fw <= 0 else (None, 0)
    if right is None or sr < 2.5:
        right, sr = (aw, 9) if cx + 4 * fw >= aw else (None, 0)
    if top is None or st < 2.5:
        top, st = (0, 9) if cy - 3.5 * fh <= 0 else (None, 0)
    if bottom is None or sb < 2.5:
        bottom, sb = (ah, 9) if cy + 4 * fh >= ah else (None, 0)
    if None in (left, right, top, bottom):
        return None
    w, h = right - left, bottom - top
    if not (1.2 < w / fw < 9 and 1.2 < h / fh < 9 and 0.8 < w / h < 2.4):
        return None
    return left, top, right, bottom


def detect(video, out_dir=None, samples=24):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(MODEL, so, providers=["CPUExecutionProvider"])

    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", video], capture_output=True, text=True)
    info = json.loads(r.stdout)
    sw, sh = info["streams"][0]["width"], info["streams"][0]["height"]
    dur = float(info["format"]["duration"])
    aw, ah = 1280, int(1280 * sh / sw) // 2 * 2  # resolution d'analyse
    sx, sy = sw / aw, sh / ah

    found = []  # (frame_index, x1, y1, x2, y2) en pixels source
    used = 0
    gx = np.zeros((ah, aw - 1))  # moyenne des contours verticaux / horizontaux sur toutes les images
    gy = np.zeros((ah - 1, aw))
    for k in range(samples):
        t = dur * (0.05 + 0.9 * k / max(1, samples - 1))
        img = _frame(video, t, aw, ah)
        if img is None:
            continue
        used += 1
        g = img.astype(np.float32).mean(axis=2)
        gx += np.abs(np.diff(g, axis=1))
        gy += np.abs(np.diff(g, axis=0))
        tiles = [(0, 0, aw, ah)] + [(qx, qy, aw // 2, ah // 2) for qx in (0, aw // 2) for qy in (0, ah // 2)]
        boxes = []
        for (tx, ty, tw, th) in tiles:
            for b, s in _faces(sess, img[ty:ty + th, tx:tx + tw]):
                x1, y1, x2, y2 = tx + b[0] * tw, ty + b[1] * th, tx + b[2] * tw, ty + b[3] * th
                boxes.append([x1, y1, x2, y2, s])
        if boxes:
            arr = np.array(boxes)
            for i in _nms(arr[:, :4], arr[:, 4], 0.4):
                x1, y1, x2, y2 = arr[i, :4]
                found.append((k, x1 * sx, y1 * sy, x2 * sx, y2 * sy))

    result = {"rect": None, "frames_used": used, "faces": len(found)}
    if found:
        # regroupe les visages par position ; la facecam = groupe present dans le plus d'images, immobile
        pts = np.array([[(f[1] + f[3]) / 2, (f[2] + f[4]) / 2, f[3] - f[1], f[4] - f[2], f[0]] for f in found])
        best, best_n = None, 0
        for p in pts:
            near = pts[(np.abs(pts[:, 0] - p[0]) < sw * 0.04) & (np.abs(pts[:, 1] - p[1]) < sh * 0.05)]
            n = len(set(near[:, 4].astype(int)))
            if n > best_n:
                best, best_n = near, n
        result["presence"] = round(best_n / max(1, used), 2)
        if best_n >= max(3, 0.35 * used):
            cx, cy = np.median(best[:, 0]), np.median(best[:, 1])
            fw, fh = np.median(best[:, 2]), np.median(best[:, 3])
            # cadre facecam : ~3.2 largeurs de visage, ratio ~4:3, le visage un peu au-dessus du centre
            w = min(sw, fw * 3.4)
            h = min(sh, w * 0.75)
            x = int(np.clip(cx - w / 2, 0, sw - w))
            y = int(np.clip(cy - h * 0.45, 0, sh - h))
            rect = [x, y, int(w), int(h)]
            refined = _refine(gx / used, gy / used, (cx / sx, cy / sy, fw / sx, fh / sy), aw, ah)
            if refined:
                x1, y1, x2, y2 = refined
                rect = [int(x1 * sx), int(y1 * sy), int((x2 - x1) * sx), int((y2 - y1) * sy)]
                result["method"] = "bords"
            else:
                result["method"] = "estimation"
            result["rect"] = [rect[0], rect[1], rect[2] // 2 * 2, rect[3] // 2 * 2]
            result["face"] = [int(cx), int(cy), int(fw), int(fh)]

    out_dir = out_dir or os.path.join(ROOT, "work", os.path.splitext(os.path.basename(video))[0])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "facecam.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    if result["rect"]:
        x, y, w, h = result["rect"]
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * 0.5:.2f}", "-i", video, "-frames:v", "1",
                        "-vf", f"drawbox=x={x}:y={y}:w={w}:h={h}:color=magenta:t=6,scale=960:-2",
                        os.path.join(out_dir, "facecam_check.jpg")])
        print(f"Facecam detectee : x={x} y={y} {w}x{h} (presente sur {result['presence']:.0%} des images)")
    else:
        print("Pas de facecam fixe detectee (video sans facecam ?) -> utiliser layout blur ou full")
    return result


if __name__ == "__main__":
    detect(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
