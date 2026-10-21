"""Fit per-'bone' similarity transforms from LK tracks: translation / rotation / scale curves, amplitude, period."""
import sys, os, json
import numpy as np
import cv2

d = sys.argv[1]
win = int(sys.argv[2]) if len(sys.argv) > 2 else 1

# sub-regions in region-crop coordinates (x0, y0, x1, y1)
PARTS = {
    "L": {"hair": (90, 140, 260, 205), "head": (110, 190, 240, 300), "fist_hi": (200, 330, 300, 430),
          "fist_lo": (75, 425, 190, 505), "chest": (110, 300, 230, 420), "abs": (110, 420, 230, 560),
          "shoulder_L": (0, 260, 100, 420)},
    "R": {"hair": (20, 15, 170, 75), "head": (40, 60, 180, 170), "fist_front": (85, 210, 190, 300),
          "fist_back": (0, 170, 80, 280), "chest": (180, 150, 315, 300), "belly": (180, 300, 315, 415),
          "elbow": (230, 230, 315, 340)},
}

def fit(ref, cur):
    ok = ~np.isnan(cur).any(1)
    if ok.sum() < 4:
        return None
    M, inl = cv2.estimateAffinePartial2D(ref[ok].astype(np.float32), cur[ok].astype(np.float32),
                                         method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if M is None:
        return None
    a, b = M[0, 0], M[1, 0]
    s = float(np.hypot(a, b))
    rot = float(np.degrees(np.arctan2(b, a)))
    c = ref[ok].mean(0)
    moved = M[:, :2] @ c + M[:, 2]
    return moved[0] - c[0], moved[1] - c[1], rot, s, int(ok.sum())

def period(sig, fps):
    sig = np.asarray(sig, float)
    sig = sig[~np.isnan(sig)]
    if len(sig) < 30:
        return None
    sig = sig - sig.mean()
    ac = np.correlate(sig, sig, "full")[len(sig) - 1:]
    ac /= ac[0] + 1e-9
    lo = int(0.6 * fps)
    if len(ac) <= lo + 2:
        return None
    k = lo + int(np.argmax(ac[lo:]))
    return k / fps, float(ac[k])

res = {}
for k in ("L", "R"):
    z = np.load(os.path.join(d, f"track_w{win}_{k}.npz"))
    ref, disp, times = z["ref"], z["disp"], z["times"]
    fps = 1 / np.median(np.diff(times))
    res[k] = {}
    for name, (x0, y0, x1, y1) in PARTS[k].items():
        sel = (ref[:, 0] >= x0) & (ref[:, 0] < x1) & (ref[:, 1] >= y0) & (ref[:, 1] < y1)
        if sel.sum() < 4:
            res[k][name] = {"n": int(sel.sum())}
            continue
        rows = []
        for f in range(len(times)):
            r = fit(ref[sel], ref[sel] + disp[f][sel])
            rows.append(r if r else (np.nan,) * 5)
        a = np.array(rows, float)
        info = {"n": int(sel.sum()), "alive_end": int(a[-1, 4]) if not np.isnan(a[-1, 4]) else 0}
        for j, lab in enumerate(["dx", "dy", "rot", "scale"]):
            col = a[:, j]
            v = col[~np.isnan(col)]
            if len(v) == 0:
                continue
            info[lab + "_p2p"] = round(float(np.percentile(v, 98) - np.percentile(v, 2)), 3 if lab == "scale" else 2)
            p = period(col, fps)
            if p:
                info[lab + "_period"] = round(p[0], 2)
                info[lab + "_acf"] = round(p[1], 2)
        res[k][name] = info
        np.save(os.path.join(d, f"bone_w{win}_{k}_{name}.npy"), a)
print(json.dumps(res, indent=1, ensure_ascii=False))
