"""Export report assets: chart data JSON + key-frame strips."""
import cv2, sys, os, json
import numpy as np

src, an, out = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(out, exist_ok=True)

def save(name, img, q=84):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    open(os.path.join(out, name), "wb").write(buf.tobytes())

# ---------- idle curves ----------
T0, T1 = 18.3, 24.3
series = {}
for reg, part, col, key in [("L", "head", 1, "L_head_dy"), ("L", "fist_hi", 1, "L_fistA_dy"),
                            ("L", "fist_lo", 1, "L_fistB_dy"), ("R", "head", 0, "R_head_dx")]:
    a = np.load(os.path.join(an, f"bone_w1_{reg}_{part}.npy"))
    t = np.load(os.path.join(an, f"track_w1_{reg}.npz"))["times"]
    m = (t >= T0) & (t <= T1)
    y = a[m, col]
    tt = t[m]
    ok = ~np.isnan(y)
    y = np.interp(tt, tt[ok], y[ok])
    y = y - y.mean()
    series[key] = [[round(float(a_ - T0), 3), round(float(b_), 2)] for a_, b_ in zip(tt, y)]

# ---------- reel start/stop offsets (from reelshift runs) ----------
start = [(0, 0.00), (1, -8.04), (2, -14.20), (3, -16.75), (4, -9.10), (5, 27.22), (6, 80.83)]
stop = [(0, -223.73), (1, -135.95), (2, -59.09), (3, -12.30), (4, 4.33), (5, 10.93), (6, 7.20),
        (7, 3.34), (8, 0.84), (9, -0.23), (10, -0.28), (11, 0.10)]
json.dump({"idle": series, "reelStart": start, "reelStop": stop}, open(os.path.join(out, "data.json"), "w"))

# ---------- frame strips ----------
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
def frame(t):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
    ok, fr = cap.read()
    return fr

def strip(name, times, box, w, labels=None, gap=6):
    x0, y0, x1, y1 = box
    h = int(w * (y1 - y0) / (x1 - x0))
    tiles = []
    for i, t in enumerate(times):
        im = cv2.resize(frame(t)[y0:y1, x0:x1], (w, h), interpolation=cv2.INTER_AREA)
        tiles.append(im)
        if i < len(times) - 1:
            tiles.append(np.full((h, gap, 3), 0, np.uint8))
    save(name, np.hstack(tiles))
    print(name, len(times), "x", w, h)

strip("punch_R.jpg", [17.080, 17.180, 17.281, 17.449, 17.549, 17.818, 18.054], (1060, 330, 1405, 880), 220)
strip("punch_L.jpg", [64.819, 64.886, 64.953, 65.088, 65.289, 65.491], (205, 300, 560, 880), 220)
strip("bigwin.jpg", [66.970, 67.071, 67.680, 68.248, 68.920, 69.630], (330, 205, 1280, 880), 300)
strip("bonus.jpg", [56.75, 57.76, 60.80, 62.77, 64.52, 64.72, 65.59], (530, 205, 1080, 770), 230)
strip("intro.jpg", [5.65, 6.05, 6.32, 6.52, 6.72, 7.13], (205, 205, 1405, 880), 300)
strip("reelstop.jpg", [21.719, 21.786, 21.853, 21.886, 21.954, 22.021], (548, 262, 651, 752), 120)

# heat maps (already computed), resized
for k in ("L", "R"):
    im = cv2.imdecode(np.fromfile(os.path.join(an, f"heat_{k}.jpg"), np.uint8), cv2.IMREAD_COLOR)
    h, w = im.shape[:2]
    s = 520 / w
    save(f"heat_{k}.jpg", cv2.resize(im, (520, int(h * s)), interpolation=cv2.INTER_AREA))
print("ok")
