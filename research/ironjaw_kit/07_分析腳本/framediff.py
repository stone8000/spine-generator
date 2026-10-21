import json, sys, numpy as np
d = json.load(open(sys.argv[1]))
fps = d["fps"]; s = d["series"]
t0, t1 = float(sys.argv[2]), float(sys.argv[3])
keys = sys.argv[4].split(",")
for i in range(int(t0*fps), int(t1*fps)+1):
    print(f"{i:5d} {i/fps:7.3f} " + " ".join(f"{k}={s[k][i]:6.2f}" for k in keys))
