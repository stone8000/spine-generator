"""Precise loop period (FFT), sine-fit amplitude, harmonic content, and phase lag of every part vs a reference part."""
import sys, os, json
import numpy as np

d, win, t0, t1 = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
PARTS = {"L": ["chest", "abs", "shoulder_L", "head", "hair", "fist_hi", "fist_lo"],
         "R": ["chest", "elbow", "head", "hair", "fist_back", "fist_front"]}
out = {}
for reg, parts in PARTS.items():
    z = np.load(os.path.join(d, f"track_w{win}_{reg}.npz"))
    t = z["times"]
    m = (t >= t0) & (t <= t1)
    tt = t[m]
    fps = 1 / np.median(np.diff(tt))
    # global period from head dy via zero-padded FFT
    curves = {}
    for p in parts:
        a = np.load(os.path.join(d, f"bone_w{win}_{reg}_{p}.npy"))[m]
        for j, lab in enumerate(["dx", "dy", "rot"]):
            y = a[:, j]
            if np.isnan(y).mean() > 0.2:
                continue
            y = np.interp(tt, tt[~np.isnan(y)], y[~np.isnan(y)])
            curves[f"{p}.{lab}"] = y - y.mean()
    ref = curves.get("head.dy")
    N = 1 << 16
    spec = np.abs(np.fft.rfft(ref * np.hanning(len(ref)), N))
    freqs = np.fft.rfftfreq(N, 1 / fps)
    band = (freqs > 0.2) & (freqs < 3)
    f0 = freqs[band][np.argmax(spec[band])]
    T = 1 / f0
    res = {"period_s": round(T, 3), "fps": round(fps, 2), "parts": {}}
    w = 2 * np.pi * f0
    basis = np.stack([np.sin(w * tt), np.cos(w * tt), np.sin(2 * w * tt), np.cos(2 * w * tt),
                      np.sin(3 * w * tt), np.cos(3 * w * tt), np.ones_like(tt)], 1)
    for k, y in curves.items():
        coef, *_ = np.linalg.lstsq(basis, y, rcond=None)
        a1 = np.hypot(coef[0], coef[1]); a2 = np.hypot(coef[2], coef[3]); a3 = np.hypot(coef[4], coef[5])
        ph = np.arctan2(coef[1], coef[0])  # phase of fundamental
        fit = basis @ coef
        r2 = 1 - np.var(y - fit) / np.var(y)
        res["parts"][k] = {"amp1": round(float(a1), 2), "h2/h1": round(float(a2 / a1), 2), "h3/h1": round(float(a3 / a1), 2),
                           "phase_deg": float(np.degrees(ph)), "r2": round(float(r2), 3)}
    # express phase relative to chest.dy (or first available) as time lag in seconds
    base = res["parts"].get("chest.dy") or next(iter(res["parts"].values()))
    for k, v in res["parts"].items():
        dph = (base["phase_deg"] - v["phase_deg"] + 180) % 360 - 180
        v["lag_vs_chest_dy_s"] = round(dph / 360 * T, 3)
        v["phase_deg"] = round(v["phase_deg"], 1)
    out[reg] = res
print(json.dumps(out, indent=1))
