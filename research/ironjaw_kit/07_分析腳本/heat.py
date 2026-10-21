"""Temporal std-dev heat map of each fighter over a clean idle window."""
import cv2, sys, os
import numpy as np
src, out, t0, t1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
REG = {"L": (205, 210, 530, 770), "R": (1090, 355, 1405, 770)}
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
f = int(t0 * fps)
cap.set(cv2.CAP_PROP_POS_FRAMES, f)
acc = {k: None for k in REG}
first = {}
n = 0
while f / fps <= t1:
    ok, fr = cap.read()
    if not ok:
        break
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    for k, (x0, y0, x1, y1) in REG.items():
        c = g[y0:y1, x0:x1]
        if acc[k] is None:
            acc[k] = [np.zeros_like(c), np.zeros_like(c)]
            first[k] = fr[y0:y1, x0:x1].copy()
        acc[k][0] += c
        acc[k][1] += c * c
    n += 1
    f += 1
for k in REG:
    s, s2 = acc[k]
    std = np.sqrt(np.maximum(s2 / n - (s / n) ** 2, 0))
    norm = np.clip(std / np.percentile(std, 99.5) * 255, 0, 255).astype(np.uint8)
    over = cv2.addWeighted(first[k], 0.45, cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO), 0.75, 0)
    ok, buf = cv2.imencode(".jpg", np.hstack([first[k], over]), [cv2.IMWRITE_JPEG_QUALITY, 90])
    open(os.path.join(out, f"heat_{k}.jpg"), "wb").write(buf.tobytes())
print("frames", n)
