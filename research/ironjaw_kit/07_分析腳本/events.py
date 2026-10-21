import json, sys, numpy as np
d = json.load(open(sys.argv[1])); fps = d["fps"]; s = d["series"]
t0, t1 = float(sys.argv[2]), float(sys.argv[3])
keys = ["mult", "L", "R", "c0", "c1", "c2", "c3", "c4", "bar"]
A = np.array([s[k] for k in keys])
lo, hi = int(t0*fps), int(t1*fps)
# print a compact 0.1s-binned activity table
step = max(1, int(round(fps*0.1)))
print("t     " + " ".join(f"{k:>5}" for k in keys))
for i in range(lo, hi, step):
    seg = A[:, i:i+step].mean(1)
    print(f"{i/fps:6.2f} " + " ".join(f"{v:5.1f}" for v in seg))
