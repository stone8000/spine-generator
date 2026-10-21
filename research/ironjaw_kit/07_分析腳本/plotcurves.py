"""Plot bone curves (cv2, no matplotlib) + dump compact CSV for the report."""
import sys, os
import numpy as np
import cv2

d, win, t_from, t_to = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
specs = [a.split(":") for a in sys.argv[5:]]  # region:part:col(0dx 1dy 2rot)

COL = [(80, 200, 255), (255, 160, 60), (120, 255, 120), (230, 90, 230), (90, 90, 255), (255, 255, 90)]
W, H, PAD = 1400, 520, 60
img = np.full((H, W, 3), 22, np.uint8)
lines = []
for i, (reg, part, c) in enumerate(specs):
    a = np.load(os.path.join(d, f"bone_w{win}_{reg}_{part}.npy"))
    z = np.load(os.path.join(d, f"track_w{win}_{reg}.npz"))
    t = z["times"]
    y = a[:, int(c)]
    m = (t >= t_from) & (t <= t_to)
    lines.append((t[m], y[m] - np.nanmean(y[m]), f"{reg}.{part}.{['dx','dy','rot'][int(c)]}", COL[i % len(COL)]))
ymax = max(np.nanmax(np.abs(l[1])) for l in lines) * 1.1
def px(t, v):
    return int(PAD + (t - t_from) / (t_to - t_from) * (W - 2 * PAD)), int(H / 2 - v / ymax * (H / 2 - PAD))
cv2.line(img, (PAD, H // 2), (W - PAD, H // 2), (70, 70, 70), 1)
s = np.ceil(t_from)
while s <= t_to:
    x, _ = px(s, 0)
    cv2.line(img, (x, PAD), (x, H - PAD), (50, 50, 50), 1)
    cv2.putText(img, f"{s:.0f}s", (x - 12, H - PAD + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1)
    s += 1
for k, (t, y, lab, col) in enumerate(lines):
    pts = [px(tt, vv) for tt, vv in zip(t, y) if not np.isnan(vv)]
    cv2.polylines(img, [np.array(pts, np.int32)], False, col, 2, cv2.LINE_AA)
    cv2.putText(img, lab, (PAD + 10 + k * 230, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
cv2.putText(img, f"+/-{ymax:.1f}", (5, PAD), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)
name = f"plot_w{win}_{t_from:.0f}_{t_to:.0f}_" + "_".join(p for _, p, _ in specs)[:60] + ".png"
ok, buf = cv2.imencode(".png", img)
open(os.path.join(d, name), "wb").write(buf.tobytes())
print(name)
