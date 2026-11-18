"""Genere des bruitages et une boucle musicale 100 % originaux (synthese numpy) -> aucun probleme de droits.

Usage : python tools/make_assets.py
Sortie : assets/sfx/whoosh.wav, assets/sfx/impact.wav, assets/sfx/pop.wav, assets/music/synthwave_loop.wav
"""
import os
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 48000
rng = np.random.default_rng(7)


def save(path, x, stereo=True):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1 and stereo:
        x = np.stack([x, x], axis=1)
    peak = np.max(np.abs(x)) or 1.0
    x = x / peak * 0.89
    data = (x * 32767).astype(np.int16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print("->", os.path.relpath(path, ROOT))


def fft_filter(x, lo=None, hi=None):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= 1 / (1 + (lo / np.maximum(f, 1)) ** 4)
    if hi:
        g *= 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(X * g, len(x))


def whoosh(dur=0.5):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    # passe-bande qui monte puis redescend (filtre d'etat variable, frequence variable)
    fc = 400 + 3200 * np.sin(np.pi * t / dur) ** 2
    y = np.zeros(n)
    low = band = 0.0
    q = 0.5
    for i in range(n):
        f = 2 * np.sin(np.pi * fc[i] / SR)
        low += f * band
        high = noise[i] - low - q * band
        band += f * high
        y[i] = band
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.5
    left = y * env * (0.6 + 0.4 * t / dur)
    right = y * env * (1.0 - 0.4 * t / dur)  # leger panoramique gauche -> droite
    return np.stack([left, right], axis=1)


def impact(dur=0.9):
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 38 + 50 * np.exp(-t * 18)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 5.5)
    click = fft_filter(rng.standard_normal(n), lo=1500) * np.exp(-t * 90) * 0.5
    x = np.tanh(2.2 * (body + click))
    return x


def pop(dur=0.12):
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 900 - 500 * t / dur
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 40)


def note_freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def saw(freq, t):
    return 2 * ((freq * t) % 1.0) - 1


def music(bpm=100, bars=16):
    """Boucle synthwave douce : nappe + basse + arpege, progression la min - fa - do - sol."""
    beat = 60 / bpm
    bar = 4 * beat
    total = bars * bar
    n = int(SR * total)
    t = np.arange(n) / SR
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G
    pad = np.zeros(n)
    bass = np.zeros(n)
    arp = np.zeros(n)
    for b in range(bars):
        ch = chords[b % 4]
        s, e = int(b * bar * SR), int((b + 1) * bar * SR)
        tt = t[s:e] - b * bar
        env = np.minimum(1, tt / 0.4) * np.minimum(1, (bar - tt) / 0.3)
        for m in ch:
            f = note_freq(m)
            pad[s:e] += (saw(f * 0.997, tt) + saw(f * 1.003, tt)) * env * 0.18
        # basse en croches
        root = note_freq(ch[0] - 24)
        for k in range(8):
            ks, ke = s + int(k * beat / 2 * SR), s + int((k + 1) * beat / 2 * SR)
            tk = np.arange(ke - ks) / SR
            bass[ks:ke] += np.sin(2 * np.pi * root * tk) * np.exp(-tk * 6) * 0.9
        # arpege en doubles croches, une octave au-dessus
        seq = ch + [ch[0] + 12]
        for k in range(16):
            ks, ke = s + int(k * beat / 4 * SR), s + int((k + 1) * beat / 4 * SR)
            tk = np.arange(ke - ks) / SR
            f = note_freq(seq[k % 4] + 12)
            arp[ks:ke] += np.sign(np.sin(2 * np.pi * f * tk)) * np.exp(-tk * 14) * 0.12
    pad = fft_filter(pad, hi=1800)
    arp = fft_filter(arp, hi=3500)
    mix = pad + bass * 0.8 + arp
    # echo stereo leger sur l'arpege
    d = int(beat * 0.75 * SR)
    right = mix.copy()
    right[d:] += 0.35 * arp[:-d]
    left = mix.copy()
    left[d // 2:] += 0.25 * arp[:-(d // 2)]
    # fondu de raccord pour une boucle sans clic
    f = int(0.02 * SR)
    for ch in (left, right):
        ch[:f] *= np.linspace(0, 1, f)
        ch[-f:] *= np.linspace(1, 0, f)
    return np.stack([left, right], axis=1)


if __name__ == "__main__":
    save(os.path.join(ROOT, "assets", "sfx", "whoosh.wav"), whoosh())
    save(os.path.join(ROOT, "assets", "sfx", "impact.wav"), impact())
    save(os.path.join(ROOT, "assets", "sfx", "pop.wav"), pop())
    save(os.path.join(ROOT, "assets", "music", "synthwave_loop.wav"), music())
