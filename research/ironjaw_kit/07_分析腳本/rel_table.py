"""Local (parent-relative) idle keys: what to key on each Spine bone so that, after inheritance, the screen motion matches.
args: an_dir out_csv"""
import sys, os, csv
import numpy as np
an, out_csv = sys.argv[1], sys.argv[2]
T0, T1, H, LOOP = 18.2, 35.8, 675.0, 60
# (bone label, part, parent part or None)
HIER = {"L": [("上身根骨（髖）", "chest", None), ("頭", "head", "chest"), ("頭髮", "hair", "head"),
              ("左肩", "shoulder_L", "chest"), ("上拳（前手）", "fist_hi", "chest"), ("下拳（後手）", "fist_lo", "chest")],
        "R": [("上身根骨（髖）", "chest", None), ("頭", "head", "chest"), ("頭髮", "hair", "head"), ("手肘", "elbow", "chest")]}
rows = []
for reg, hier in HIER.items():
    z = np.load(os.path.join(an, f"track_w1_{reg}.npz")); t = z["times"]; m = (t >= T0) & (t <= T1); tt = t[m]
    fps = 1 / np.median(np.diff(tt))
    def sig(part, j):
        y = np.load(os.path.join(an, f"bone_w1_{reg}_{part}.npy"))[m][:, j]
        ok = ~np.isnan(y); y = np.interp(tt, tt[ok], y[ok]); return y - y.mean()
    ref = sig("head", 1); N = 1 << 16
    spec = np.abs(np.fft.rfft(ref * np.hanning(len(ref)), N)); fr = np.fft.rfftfreq(N, 1 / fps)
    band = (fr > 0.2) & (fr < 3); w = 2 * np.pi * fr[band][np.argmax(spec[band])]; T = 2 * np.pi / w
    B = np.stack([np.sin(w * tt), np.cos(w * tt), np.ones_like(tt)], 1)
    def fit(y):
        c, *_ = np.linalg.lstsq(B, y, rcond=None); amp = np.hypot(c[0], c[1]); phi = np.arctan2(c[1], c[0])
        r2 = 1 - np.var(y - B @ c) / np.var(y); return amp, ((np.pi / 2 - phi) / w) % T, r2
    t_ref = fit(sig("chest", 1 if reg == "L" else 0))[1]
    for label, part, parent in hier:
        for j, prop in enumerate(["translate X", "translate Y", "rotate"]):
            y = sig(part, j) - (sig(parent, j) if parent else 0)
            amp, tmax, r2 = fit(y)
            if (j < 2 and amp < 1.0) or (j == 2 and amp < 0.5) or r2 < 0.8:
                continue
            fmax = int(round((tmax - t_ref) / T * LOOP)) % LOOP
            ext = {0: "+X（畫面右）", 1: "−Y（往下）", 2: "順時針（Spine 為負角度）"}[j]
            rows.append({"角色": "左·彈跳型" if reg == "L" else "右·閃身型", "骨骼": label,
                         "相對於": {None: "—", "chest": "上身根骨", "head": "頭"}[parent],
                         "Spine 屬性": prop, "振幅（±）": f"{amp:.1f}°" if j == 2 else f"{amp:.1f} px",
                         "佔畫布高": "" if j == 2 else f"±{amp / H * 100:.2f}%",
                         "此方向極值在第幾幀": fmax, "反向極值在第幾幀": (fmax + 30) % LOOP, "極值方向": ext,
                         "R²": f"{r2:.3f}"})
with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
    wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
for r in rows: print(" | ".join(str(v) for v in r.values()))
