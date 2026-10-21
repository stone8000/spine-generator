"""Decode the whole video once: save contact sheets, a few full frames, and per-frame motion energy."""
import cv2, sys, os, json
import numpy as np

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)

def save(path, img, q=88):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    with open(path, "wb") as f:
        f.write(buf.tobytes())

cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS)
TW, TH = 480, 258
COLS, ROWS = 4, 3
SHEET_STEP = 1.0  # seconds between thumbnails

thumbs = []
energy = []  # per frame: global diff
prev_small = None
prev_gray = None
idx = 0
next_thumb_t = 0.0
full_times = [1, 5, 10, 20, 30, 40, 50, 60, 70]
full_done = set()
small_frames = []  # keep 160x86 gray of every frame for later region analysis
while True:
    ok, fr = cap.read()
    if not ok:
        break
    t = idx / fps
    small = cv2.resize(fr, (320, 172), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)
    if prev_gray is not None:
        energy.append(float(np.mean(np.abs(gray - prev_gray))))
    else:
        energy.append(0.0)
    prev_gray = gray
    small_frames.append(gray.astype(np.uint8))
    if t >= next_thumb_t - 1e-6:
        th = cv2.resize(fr, (TW, TH), interpolation=cv2.INTER_AREA)
        cv2.rectangle(th, (0, 0), (110, 30), (0, 0, 0), -1)
        cv2.putText(th, f"{t:5.1f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        thumbs.append(th)
        next_thumb_t += SHEET_STEP
    for ft in full_times:
        if ft not in full_done and t >= ft:
            save(os.path.join(out, f"full_{ft:03d}s.jpg"), fr)
            full_done.add(ft)
    idx += 1

per = COLS * ROWS
for s in range(0, len(thumbs), per):
    chunk = thumbs[s:s + per]
    while len(chunk) < per:
        chunk.append(np.zeros_like(thumbs[0]))
    rows = [np.hstack(chunk[r * COLS:(r + 1) * COLS]) for r in range(ROWS)]
    save(os.path.join(out, f"sheet_{s // per:02d}.jpg"), np.vstack(rows), 80)

np.save(os.path.join(out, "small_gray.npy"), np.stack(small_frames))
with open(os.path.join(out, "energy.json"), "w") as f:
    json.dump({"fps": fps, "energy": energy}, f)
print("frames", idx, "fps", fps, "thumbs", len(thumbs))
