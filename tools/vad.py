"""Detection de la parole avec Silero VAD (models/silero_vad.onnx).

Contrairement a un seuil de volume, le modele distingue la voix de la musique et des bruits du jeu.
speech_prob(samples) -> probabilite de parole par trame de 32 ms (audio 16 kHz mono float32).
"""
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "models", "silero_vad.onnx")
SR, CHUNK, CTX = 16000, 512, 64
HOP = CHUNK / SR  # 0.032 s
_sess = None


def available():
    return os.path.exists(MODEL)


def speech_prob(x):
    global _sess
    if _sess is None:
        import onnxruntime as ort
        _sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    x = np.asarray(x, np.float32)
    n = len(x) // CHUNK
    state = np.zeros((2, 1, 128), np.float32)
    ctx = np.zeros(CTX, np.float32)
    sr = np.array(SR, np.int64)
    out = np.zeros(n, np.float32)
    for k in range(n):
        ch = x[k * CHUNK:(k + 1) * CHUNK]
        inp = np.concatenate([ctx, ch])[None, :]
        p, state = _sess.run(None, {"input": inp, "state": state, "sr": sr})
        out[k] = float(p.reshape(-1)[0])
        ctx = ch[-CTX:]
    return out
