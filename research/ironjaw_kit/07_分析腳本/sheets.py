"""Contact sheets of the game area only. args: src out t0 t1 step cols rows tag [tw]"""
import cv2, sys, os
import numpy as np

src, out = sys.argv[1], sys.argv[2]
t0, t1, step = float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
COLS, ROWS = int(sys.argv[6]), int(sys.argv[7])
tag = sys.argv[8]
TW = int(sys.argv[9]) if len(sys.argv) > 9 else 480
Y0, Y1, X0, X1 = 205, 880, 205, 1405
TH = int(TW * (Y1 - Y0) / (X1 - X0))

def save(path, img, q=82):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    with open(path, "wb") as f:
        f.write(buf.tobytes())

cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t0 * fps) - 1))
thumbs = []
nxt = t0
while True:
    pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
    ok, fr = cap.read()
    if not ok:
        break
    t = pos / fps
    if t > t1 + 1e-6:
        break
    if t >= nxt - 1e-6:
        th = cv2.resize(fr[Y0:Y1, X0:X1], (TW, TH), interpolation=cv2.INTER_AREA)
        cv2.rectangle(th, (0, 0), (92, 24), (0, 0, 0), -1)
        cv2.putText(th, f"{t:5.2f}", (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        thumbs.append(th)
        nxt += step

per = COLS * ROWS
for s in range(0, len(thumbs), per):
    chunk = thumbs[s:s + per]
    while len(chunk) < per:
        chunk.append(np.zeros_like(thumbs[0]))
    rows = [np.hstack(chunk[r * COLS:(r + 1) * COLS]) for r in range(ROWS)]
    save(os.path.join(out, f"{tag}_{s // per:02d}.jpg"), np.vstack(rows))
print(tag, len(thumbs))
