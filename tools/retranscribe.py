"""Retranscrit les passages des clips avec la voix isolee de la musique, pour des sous-titres propres.

Usage :
    python tools/retranscribe.py clips/mon_stream.json [--only 01_nom]

Pour chaque clip : voix separee de la musique et des bruits du jeu (UVR MDX-Net, audio-separator), puis
faster-whisper avec l'heure exacte de chaque mot. Les dialogues du jeu en anglais sont retires (le jeu les
sous-titre deja). Resultat : work/<video>/captions_fw.json (les segments des clips remplacent ceux de Whisper.cpp),
et la config pointe dessus. render.py utilise alors l'heure de chaque mot au lieu de la deviner.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import ROOT, tsec  # noqa: E402

EN = set("the you i a to and is it of in that we what this be on are for with have do not so but got your gonna "
         "all go me my he she they them was just can know like right yeah okay oh hey let's let one here there "
         "out up get come baby fuck don't i'm it's you're want".split())
MAX_CHARS = 18


def english(text):
    w = re.findall(r"[a-zA-Z']+", text.lower())
    accents = re.search(r"[éèêàùçôîâ]", text.lower())
    return len(w) >= 2 and not accents and sum(x in EN for x in w) / len(w) >= 0.4


def spoken_english(model, x, seg):
    """Langue entendue (pas celle du texte : Whisper traduit parfois l'anglais du jeu en francais)."""
    clip = x[int(seg.start * 16000):int(seg.end * 16000)]
    if len(clip) < 16000 * 0.6:
        return False
    try:
        lang, prob, _ = model.detect_language(clip)
    except Exception:
        return False
    if lang != "en" or prob <= 0.5:
        return False
    # "non non non, c'est bon" crie par le streamer passe parfois pour de l'anglais : le texte tranche
    fr = set("c'est je j'ai tu non bon pas le la les de du des merci oui ouais mais quoi on moi toi il elle "
             "vas va allez putain wesh frere".split())
    w = re.findall(r"[\w']+", seg.text.lower())
    return not w or sum(x in fr for x in w) / len(w) < 0.3


def chunks(words):
    """Mots -> lignes courtes (~18 caracteres), coupees sur la ponctuation et les pauses."""
    out, cur = [], []
    for w in words:
        if cur:
            text = " ".join(x[0] for x in cur)
            if (len(text) + 1 + len(w[0]) > MAX_CHARS or w[1] - cur[-1][2] > 0.6
                    or re.search(r"[.!?]$", cur[-1][0])):
                out.append(cur)
                cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def vocals(src, a, b, tmp):
    cache = os.path.join(tmp, f"voc_{a:.1f}_{b:.1f}.f32")
    if os.path.exists(cache):
        return np.fromfile(cache, np.float32)
    from audio_separator.separator import Separator
    mix = os.path.join(tmp, "mix.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-vn",
                    "-ac", "2", "-ar", "44100", mix], check=True)
    sep = Separator(output_dir=tmp, model_file_dir=os.path.join(ROOT, "models", "uvr"),
                    output_single_stem="Vocals", log_level=40)
    sep.load_model("UVR-MDX-NET-Voc_FT.onnx")
    voc = os.path.join(tmp, sep.separate(mix)[0])
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", voc, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    for f in (mix, voc):
        os.remove(f)
    x = np.frombuffer(r.stdout, np.float32)
    x.tofile(cache)
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--only")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    src = os.path.join(ROOT, cfg["source"])
    work = os.path.join(ROOT, "work", os.path.splitext(os.path.basename(cfg["source"]))[0])
    tmp = os.path.join(work, "_sep")
    os.makedirs(tmp, exist_ok=True)
    base = os.path.join(work, "captions.json")
    out_path = os.path.join(work, "captions_fw.json")
    with open(out_path if os.path.exists(out_path) else base, encoding="utf-8") as f:
        caps = [json.loads(l) for l in f if l.strip()]

    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8",
                         download_root=os.path.join(ROOT, "models", "fw"))
    for clip in cfg["clips"]:
        if args.only and clip["name"] != args.only:
            continue
        a, b = max(0.0, tsec(clip["start"]) - 2), tsec(clip["end"]) + 2
        print(f">> {clip['name']}  {a:.1f}-{b:.1f}")
        x = vocals(src, a, b, tmp)
        # langue detectee par phrase : en forcant "fr", Whisper traduit les dialogues anglais du jeu au lieu de
        # les laisser en anglais (et on ne peut plus les retirer)
        segs, _ = model.transcribe(x, language=None, multilingual=True, word_timestamps=True,
                                   vad_filter=True, condition_on_previous_text=False)
        new, dropped = [], 0
        for s in segs:
            if english(s.text) or not s.words or not re.search(r"\w", s.text) or spoken_english(model, x, s):
                dropped += 1
                continue
            # Whisper coupe "d'abord" en "d" + "'abord" : un morceau sans espace devant se colle au mot d'avant
            words = []
            for w in s.words:
                if not w.word.strip():
                    continue
                if words and (not w.word.startswith(" ") or re.fullmatch(r"[!?.,:;…]+", w.word.strip())):
                    pw, ps, _ = words[-1]
                    tok = w.word.strip()
                    sep = " " if re.fullmatch(r"[!?:;]+", tok) else ""
                    words[-1] = (pw + sep + tok, ps, a + w.end)
                else:
                    words.append((w.word.strip().lstrip("-").strip() or w.word.strip(), a + w.start, a + w.end))
            # Whisper boucle parfois ("non non non non non non...") : 4 repetitions maxi
            kept = []
            for w in words:
                if len(kept) >= 4 and all(k[0].lower().strip(",.!?") == w[0].lower().strip(",.!?") for k in kept[-4:]):
                    continue
                kept.append(w)
            words = kept
            for ch in chunks(words):
                new.append({"start": int(ch[0][1] * 1000), "end": int(ch[-1][2] * 1000),
                            "text": " ".join(w for w, _, _ in ch),
                            "words": [[w, int(ws * 1000), int(we * 1000)] for w, ws, we in ch]})
        print(f"   {len(new)} lignes, {dropped} phrases du jeu en anglais retirees")
        caps = [c for c in caps if c["end"] <= a * 1000 or c["start"] >= b * 1000] + new
    caps.sort(key=lambda c: c["start"])
    with open(out_path, "w", encoding="utf-8") as f:
        for c in caps:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    cfg["captions"] = os.path.relpath(out_path, ROOT).replace("\\", "/")
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK -> {cfg['captions']}")


if __name__ == "__main__":
    main()
