"""Vertical offset of one reel column vs a reference still frame -> wind-up before start, overshoot/bounce at stop."""
import cv2, sys
import numpy as np
src, col, t0, t1, tref = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
X0 = int(548 + col * 102.6) + 8
X1 = int(548 + (col + 1) * 102.6) - 8
Y0, Y1 = 262, 752
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)

def grab(t):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
    ok, fr = cap.read()
    return cv2.cvtColor(fr[Y0:Y1, X0:X1], cv2.COLOR_BGR2GRAY).astype(np.float32)

ref = grab(tref)
win = cv2.createHanningWindow(ref.shape[::-1], cv2.CV_32F)
f = int(round(t0 * fps))
cap.set(cv2.CAP_PROP_POS_FRAMES, f)
while f / fps <= t1:
    ok, fr = cap.read()
    if not ok:
        break
    g = cv2.cvtColor(fr[Y0:Y1, X0:X1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), resp = cv2.phaseCorrelate(ref, g, win)
    sharp = float(np.mean(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))))
    print(f"{f:5d} {f / fps:7.3f}  dy={dy:7.2f}  resp={resp:4.2f}  sharp={sharp:5.1f}")
    f += 1
