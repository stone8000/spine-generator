"""Per-reel spin start/stop from vertical-edge sharpness (motion blur kills horizontal edges)."""
import json, sys
import numpy as np

d = json.load(open(sys.argv[1]))
fps = d["fps"]
t0, t1 = float(sys.argv[2]), float(sys.argv[3])
S = np.array([d["sharp"][f"c{i}"] for i in range(5)])  # 5 x N
M = np.array([d["series"][f"c{i}"] for i in range(5)])
n = S.shape[1]
lo, hi = int(t0 * fps), min(int(t1 * fps), n - 1)
# normalise sharpness per reel by its running high level
events = []
for i in range(5):
    s = S[i, lo:hi]
    sharp_ref = np.percentile(s, 90)
    blurred = s < 0.55 * sharp_ref
    # debounce: require 3 consecutive frames
    state = False
    run = 0
    for k, b in enumerate(blurred):
        if b != state:
            run += 1
            if run >= 3:
                state = b
                kk = k - 2
                events.append((round((lo + kk) / fps, 3), i, "start" if state else "stop"))
                run = 0
        else:
            run = 0
events.sort()
# group into spins: a spin begins at first 'start' and ends at last 'stop' before next start
spins = []
cur = None
for t, i, e in events:
    if e == "start":
        if cur is None or (cur["stops"] and len(cur["stops"]) >= 5) or (cur["stops"] and t - max(cur["stops"].values()) > 0.15):
            if cur:
                spins.append(cur)
            cur = {"starts": {}, "stops": {}}
        cur["starts"].setdefault(i, t)
    elif cur is not None:
        cur["stops"][i] = t
if cur:
    spins.append(cur)
for sp in spins:
    st = [sp["starts"].get(i) for i in range(5)]
    so = [sp["stops"].get(i) for i in range(5)]
    first = min(v for v in st if v is not None) if any(v is not None for v in st) else None
    gaps = [round(so[i + 1] - so[i], 3) if so[i] is not None and so[i + 1] is not None else None for i in range(4)]
    print(f"spin@{first}: starts={st}")
    print(f"           stops ={so}  stop-gaps={gaps}  dur={round(max(v for v in so if v) - first, 3) if first and any(so) else None}")
