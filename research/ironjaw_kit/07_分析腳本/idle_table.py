"""Turn the sine fits into an animator's key table: amplitude (px @675 canvas, % of canvas height, degrees) and the frame
of the extreme within a 60-frame loop, with Spine's conventions (y up, rotation counter-clockwise positive).
args: an_dir out_csv out_json"""
import sys, os, json, csv
import numpy as np

an, out_csv, out_json = sys.argv[1], sys.argv[2], sys.argv[3]
T0, T1 = 18.2, 35.8
CANVAS_H = 675.0
LOOP_FRAMES = 60
PARTS = {"L": ["chest", "shoulder_L", "head", "hair", "fist_hi", "fist_lo"],
         "R": ["chest", "elbow", "head", "hair", "fist_back"]}
NAMES = {"chest": "胸口", "shoulder_L": "左肩（畫面左）", "head": "頭", "hair": "頭髮", "fist_hi": "上拳（前手）",
         "fist_lo": "下拳（後手）", "elbow": "手肘", "fist_back": "後拳"}
rows, js = [], {}
for reg, parts in PARTS.items():
    z = np.load(os.path.join(an, f"track_w1_{reg}.npz"))
    t = z["times"]
    m = (t >= T0) & (t <= T1)
    tt = t[m]
    fits = {}
    # common period from head dy
    ref = np.load(os.path.join(an, f"bone_w1_{reg}_head.npy"))[m][:, 1]
    ok = ~np.isnan(ref)
    ref = np.interp(tt, tt[ok], ref[ok]) - np.nanmean(ref)
    fps = 1 / np.median(np.diff(tt))
    N = 1 << 16
    spec = np.abs(np.fft.rfft(ref * np.hanning(len(ref)), N))
    fr = np.fft.rfftfreq(N, 1 / fps)
    band = (fr > 0.2) & (fr < 3)
    f0 = fr[band][np.argmax(spec[band])]
    w = 2 * np.pi * f0
    B = np.stack([np.sin(w * tt), np.cos(w * tt), np.ones_like(tt)], 1)
    for p in parts:
        a = np.load(os.path.join(an, f"bone_w1_{reg}_{p}.npy"))[m]
        for j, lab in enumerate(["dx", "dy", "rot"]):
            y = a[:, j]
            if np.isnan(y).mean() > 0.2:
                continue
            ok = ~np.isnan(y)
            y = np.interp(tt, tt[ok], y[ok])
            c, *_ = np.linalg.lstsq(B, y - y.mean(), rcond=None)
            fit = B @ c
            r2 = 1 - np.var(y - y.mean() - fit) / np.var(y)
            amp = float(np.hypot(c[0], c[1]))
            # time (abs) of the maximum of the fitted sine: A*sin(w t + phi) max at w t + phi = pi/2
            phi = np.arctan2(c[1], c[0])
            t_max = ((np.pi / 2 - phi) / w) % (2 * np.pi / w)
            fits[(p, lab)] = dict(amp=amp, t_max=t_max, r2=float(r2))
    T = 2 * np.pi / w
    # reference: frame 0 = lowest point of the chest (chest dy maximum; image y grows downward)
    t_ref = fits[("chest", "dy")]["t_max"] if reg == "L" else fits[("chest", "dx")]["t_max"]
    for (p, lab), v in fits.items():
        if v["amp"] < 1.0 and lab != "rot":
            continue  # under 1 px: not worth keying
        if v["r2"] < 0.8:
            continue  # not a clean loop (contaminated by the reaction or by occlusion)
        frame_of_max = ((v["t_max"] - t_ref) / T * LOOP_FRAMES) % LOOP_FRAMES
        if lab == "dx":
            prop, extreme, unit_amp = "translate X", "最右（畫面右）", v["amp"]
        elif lab == "dy":
            prop, extreme, unit_amp = "translate Y", "最低", v["amp"]
        else:
            prop, extreme, unit_amp = "rotate", "順時針最大", v["amp"]
        row = {
            "角色": "左·彈跳型" if reg == "L" else "右·閃身型",
            "部位": NAMES[p],
            "Spine 屬性": prop,
            "振幅（±）": f"{unit_amp:.1f}°" if lab == "rot" else f"{unit_amp:.1f} px",
            "佔畫布高": "" if lab == "rot" else f"±{unit_amp / CANVAS_H * 100:.2f}%",
            "極值": extreme,
            "極值所在幀（60 幀循環）": int(round(frame_of_max)) % LOOP_FRAMES,
            "反向極值所在幀": int(round(frame_of_max + 30)) % LOOP_FRAMES,
            "正弦吻合度 R²": f"{v['r2']:.3f}",
        }
        rows.append(row)
    js[reg] = {"period_s": round(T, 3), "frame0": "胸口最低點" if reg == "L" else "胸口最右點（離轉輪最遠）"}
with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
    wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    wr.writeheader()
    wr.writerows(rows)
json.dump({"meta": js, "rows": rows}, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for r in rows:
    print(" | ".join(str(v) for v in r.values()))
print(js)
