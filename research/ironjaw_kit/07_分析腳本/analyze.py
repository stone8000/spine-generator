"""One pass over the video: region motion series, per-reel sharpness, LK point tracks on both fighters, idle std heatmaps."""
import cv2, sys, os, json
import numpy as np

src, out = sys.argv[1], sys.argv[2]

# full-frame (1920x1032) coordinates: x0, y0, x1, y1
REG = {
    "L": (205, 210, 530, 770),
    "R": (1090, 355, 1405, 770),
    "mult": (710, 212, 900, 262),
    "logo": (1140, 240, 1350, 350),
    "bar": (495, 775, 1115, 860),
    "bg_top": (205, 205, 520, 300),
}
REEL_X0, REEL_W, REEL_Y0, REEL_Y1 = 548, 102.6, 262, 752
for i in range(5):
    REG[f"c{i}"] = (int(REEL_X0 + i * REEL_W) + 6, REEL_Y0, int(REEL_X0 + (i + 1) * REEL_W) - 6, REEL_Y1)

WINDOWS = [(7.3, 11.0), (13.0, 37.5), (43.5, 49.0), (54.2, 64.8), (64.8, 66.9), (69.9, 74.3)]
STD_WIN = (13.0, 37.5)

def save(path, img, q=90):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    with open(path, "wb") as f:
        f.write(buf.tobytes())

def crop(img, r):
    x0, y0, x1, y1 = r
    return img[y0:y1, x0:x1]

cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
series = {k: [] for k in REG}
sharp = {f"c{i}": [] for i in range(5)}
prev = None
tracks = {}  # (win_idx, region) -> dict(ref, pts_prev, alive, disp list)
acc = {k: None for k in ("L", "R")}
acc_n = 0
ref_rgb = {}
idx = 0
lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
while True:
    ok, fr = cap.read()
    if not ok:
        break
    t = idx / fps
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    for k, r in REG.items():
        if prev is None:
            series[k].append(0.0)
        else:
            series[k].append(float(np.mean(cv2.absdiff(crop(g, r), crop(prev, r)))))
    for i in range(5):
        c = crop(g, REG[f"c{i}"])
        # horizontal edges vanish under vertical motion blur -> vertical derivative energy
        sharp[f"c{i}"].append(float(np.mean(np.abs(cv2.Sobel(c, cv2.CV_32F, 0, 1, ksize=3)))))
    # point tracking
    for wi, (a, b) in enumerate(WINDOWS):
        if not (a <= t <= b):
            continue
        for k in ("L", "R"):
            key = (wi, k)
            cur = crop(g, REG[k])
            if key not in tracks:
                pts = cv2.goodFeaturesToTrack(cur, maxCorners=500, qualityLevel=0.01, minDistance=7, blockSize=7)
                pts = pts.reshape(-1, 2).astype(np.float32)
                tracks[key] = {"t0": t, "ref": pts.copy(), "prev_img": cur, "prev_pts": pts.copy(),
                               "alive": np.ones(len(pts), bool), "disp": [np.zeros_like(pts)], "times": [t]}
                if wi == 1:
                    ref_rgb[k] = crop(fr, REG[k]).copy()
                continue
            tr = tracks[key]
            nxt, st, err = cv2.calcOpticalFlowPyrLK(tr["prev_img"], cur, tr["prev_pts"].reshape(-1, 1, 2), None, **lk)
            nxt = nxt.reshape(-1, 2)
            # forward-backward check
            back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, tr["prev_img"], nxt.reshape(-1, 1, 2), None, **lk)
            fb = np.linalg.norm(back.reshape(-1, 2) - tr["prev_pts"], axis=1)
            good = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 0.5)
            tr["alive"] &= good
            tr["prev_pts"] = np.where(good[:, None], nxt, tr["prev_pts"])
            tr["prev_img"] = cur
            d = tr["prev_pts"] - tr["ref"]
            d[~tr["alive"]] = np.nan
            tr["disp"].append(d)
            tr["times"].append(t)
    if STD_WIN[0] <= t <= STD_WIN[1]:
        for k in ("L", "R"):
            c = crop(g, REG[k]).astype(np.float64)
            if acc[k] is None:
                acc[k] = [np.zeros_like(c), np.zeros_like(c)]
            acc[k][0] += c
            acc[k][1] += c * c
        acc_n += 1
    prev = g
    idx += 1

os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "series.json"), "w") as f:
    json.dump({"fps": fps, "n": idx, "series": series, "sharp": sharp, "regions": REG}, f)

for k in ("L", "R"):
    s, s2 = acc[k]
    std = np.sqrt(np.maximum(s2 / acc_n - (s / acc_n) ** 2, 0))
    np.save(os.path.join(out, f"std_{k}.npy"), std.astype(np.float32))
    norm = np.clip(std / max(np.percentile(std, 99.5), 1e-6) * 255, 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
    base = ref_rgb[k]
    over = cv2.addWeighted(base, 0.45, heat, 0.75, 0)
    save(os.path.join(out, f"heat_{k}.jpg"), np.hstack([base, over]))

for (wi, k), tr in tracks.items():
    np.savez_compressed(os.path.join(out, f"track_w{wi}_{k}.npz"), ref=tr["ref"], disp=np.stack(tr["disp"]),
                        times=np.array(tr["times"]), alive=tr["alive"])
print("done", idx, "frames; std frames", acc_n, "tracks", {f"{k}": int(v['alive'].sum()) for k, v in tracks.items()})
