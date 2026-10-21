"""Every-Nth-frame sheet of an arbitrary full-frame crop. args: src out t0 t1 every cols tag x0 y0 x1 y1 tw"""
import cv2, sys, os
import numpy as np
src, out, t0, t1, every, cols, tag = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), sys.argv[7]
x0, y0, x1, y1, tw = map(int, sys.argv[8:13])
th = int(tw * (y1 - y0) / (x1 - x0))
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
f0 = int(t0 * fps)
cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
thumbs = []
f = f0
while f / fps <= t1:
    ok, fr = cap.read()
    if not ok:
        break
    if (f - f0) % every == 0:
        im = cv2.resize(fr[y0:y1, x0:x1], (tw, th), interpolation=cv2.INTER_AREA)
        cv2.rectangle(im, (0, 0), (120, 22), (0, 0, 0), -1)
        cv2.putText(im, f"{f / fps:.3f} #{f}", (3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        thumbs.append(im)
    f += 1
rows = []
for r in range(0, len(thumbs), cols):
    ch = thumbs[r:r + cols]
    while len(ch) < cols:
        ch.append(np.zeros_like(thumbs[0]))
    rows.append(np.hstack(ch))
ok, buf = cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
open(os.path.join(out, f"{tag}.jpg"), "wb").write(buf.tobytes())
print(tag, len(thumbs))
