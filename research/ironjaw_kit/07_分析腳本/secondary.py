"""(1) hair motion left over after removing the head's rigid transform -> secondary/physics layer?
(2) full-affine strain inside torso patches -> mesh deformation (breathing / squash) vs rigid parts."""
import sys, os, json
import numpy as np
import cv2

d, win, t0, t1 = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
HEAD = {"L": (110, 190, 240, 300), "R": (40, 60, 180, 170)}
HAIR = {"L": [(90, 140, 175, 200), (175, 140, 260, 205)], "R": [(20, 15, 95, 75), (95, 15, 170, 75)]}
PATCH = {"L": {"chest": (110, 300, 230, 420), "shoulder_L": (0, 260, 100, 420), "head": (110, 190, 240, 300)},
         "R": {"chest": (180, 150, 315, 300), "head": (40, 60, 180, 170), "elbow": (230, 230, 315, 340)}}

def sel(ref, r):
    x0, y0, x1, y1 = r
    return (ref[:, 0] >= x0) & (ref[:, 0] < x1) & (ref[:, 1] >= y0) & (ref[:, 1] < y1)

def sine_fit(tt, y, T):
    w = 2 * np.pi / T
    B = np.stack([np.sin(w * tt), np.cos(w * tt), np.ones_like(tt)], 1)
    c, *_ = np.linalg.lstsq(B, y, rcond=None)
    fit = B @ c
    return float(np.hypot(c[0], c[1])), float(np.degrees(np.arctan2(c[1], c[0]))), float(1 - np.var(y - fit) / max(np.var(y), 1e-9))

out = {}
for reg in ("L", "R"):
    z = np.load(os.path.join(d, f"track_w{win}_{reg}.npz"))
    ref, disp, times = z["ref"], z["disp"], z["times"]
    m = (times >= t0) & (times <= t1)
    tt = times[m]
    D = disp[m]
    T = 1.99
    res = {"hair_residual": {}, "strain": {}}
    hs = sel(ref, HEAD[reg])
    for hi, hr in enumerate(HAIR[reg]):
        s = sel(ref, hr)
        rx, ry = [], []
        for f in range(len(tt)):
            cur_h = ref[hs] + D[f][hs]
            ok = ~np.isnan(cur_h).any(1)
            cur_s = ref[s] + D[f][s]
            ok2 = ~np.isnan(cur_s).any(1)
            if ok.sum() < 5 or ok2.sum() < 3:
                rx.append(np.nan); ry.append(np.nan); continue
            M, _ = cv2.estimateAffinePartial2D(ref[hs][ok].astype(np.float32), cur_h[ok].astype(np.float32), method=cv2.RANSAC, ransacReprojThreshold=1.0)
            pred = (M[:, :2] @ ref[s][ok2].T).T + M[:, 2]
            r = (cur_s[ok2] - pred).mean(0)
            rx.append(r[0]); ry.append(r[1])
        rx, ry = np.array(rx), np.array(ry)
        k = ~np.isnan(rx)
        if k.sum() > 30:
            ax, px, r2x = sine_fit(tt[k], rx[k], T)
            ay, py, r2y = sine_fit(tt[k], ry[k], T)
            res["hair_residual"][f"tuft{hi}"] = {"n": int(s.sum()), "res_dx_amp": round(ax, 2), "res_dx_phase": round(px, 1), "r2x": round(r2x, 2),
                                                 "res_dy_amp": round(ay, 2), "res_dy_phase": round(py, 1), "r2y": round(r2y, 2),
                                                 "res_rms": round(float(np.sqrt(np.nanmean(rx ** 2 + ry ** 2))), 2)}
    for name, r in PATCH[reg].items():
        s = sel(ref, r)
        sx, sy, sh, rms = [], [], [], []
        for f in range(len(tt)):
            cur = ref[s] + D[f][s]
            ok = ~np.isnan(cur).any(1)
            if ok.sum() < 6:
                sx.append(np.nan); sy.append(np.nan); sh.append(np.nan); rms.append(np.nan); continue
            A, _ = cv2.estimateAffine2D(ref[s][ok].astype(np.float32), cur[ok].astype(np.float32), method=cv2.RANSAC, ransacReprojThreshold=1.0)
            L = A[:, :2]
            # polar-ish decomposition: rotation removed, keep symmetric stretch
            U, S, Vt = np.linalg.svd(L)
            R = U @ Vt
            P = R.T @ L
            sx.append(P[0, 0]); sy.append(P[1, 1]); sh.append(P[0, 1])
            Ms, _ = cv2.estimateAffinePartial2D(ref[s][ok].astype(np.float32), cur[ok].astype(np.float32), method=cv2.RANSAC, ransacReprojThreshold=1.0)
            pred = (Ms[:, :2] @ ref[s][ok].T).T + Ms[:, 2]
            rms.append(float(np.sqrt(np.mean(np.sum((cur[ok] - pred) ** 2, 1)))))
        sx, sy, sh = map(np.array, (sx, sy, sh))
        k = ~np.isnan(sx)
        res["strain"][name] = {
            "scaleX_p2p_%": round(float((np.percentile(sx[k], 98) - np.percentile(sx[k], 2)) * 100), 2),
            "scaleY_p2p_%": round(float((np.percentile(sy[k], 98) - np.percentile(sy[k], 2)) * 100), 2),
            "shear_p2p": round(float(np.percentile(sh[k], 98) - np.percentile(sh[k], 2)), 4),
            "scaleY_sine_r2": round(sine_fit(tt[k], sy[k], T)[2], 2),
            "nonrigid_rms_px": round(float(np.nanmean(rms)), 2)}
    out[reg] = res
print(json.dumps(out, indent=1))
