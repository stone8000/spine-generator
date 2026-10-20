"""Mine the beat structure of reactions from Spine animations.

For every non-idle animation, take the limb (else any) rotate timeline that
moves the most, evaluate it exactly (bezier solved in time, the way the Spine
runtimes do), and cut it into the classic beats:

  windup   counter-movement before the strike (anticipation)
  strike   the fast part: |angular velocity| above 30% of its peak
  hold     after the strike, |velocity| under 10% of peak (moving hold)
  recover  from the end of the hold until the bone is back near its start

plus overshoot past the hold pose and the lag from the driver bone to its
animated child (follow-through). Prints per-category medians and quartiles so
the generator can use measured defaults instead of guessed ones.

    python research/beats.py <packs dir or skeleton files> [--json out.json]
"""
import argparse
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from analyze_motion import LIMB, TORSO, HEAD  # noqa: E402

CATEGORIES = [
    ("idle", re.compile(r"idle|loop|breath|stand|wait", re.I)),
    ("intro", re.compile(r"intro|enter|appear|spawn|start|show|in$", re.I)),
    ("outro", re.compile(r"outro|exit|leave|disappear|hide|out$", re.I)),
    ("big_win", re.compile(r"big|mega|epic|super|max|huge|jackpot|celebrat|cheer|dance", re.I)),
    ("win", re.compile(r"win|happy|joy|yes|laugh", re.I)),
    ("trigger", re.compile(r"trigger|scatter|bonus|feature|fs|free", re.I)),
    ("attack", re.compile(r"attack|punch|hit|strike|shoot|fire|throw|slash|smash|kick|swing|jab", re.I)),
    ("react", re.compile(r"react|reaction|surprise|shock|fear|anger|angry|sad|lose|tease|anticip|near", re.I)),
]
DT = 1 / 240


def category(name):
    for cat, rx in CATEGORIES:
        if rx.search(name):
            return cat
    return "other"


def curve_points(key, nxt, field_i, is3):
    """Return normalised (cx1, cy1, cx2, cy2) or 'linear'/'stepped'."""
    c = key.get("curve")
    if c is None:
        return "linear"
    if c == "stepped":
        return "stepped"
    if is3:
        return (c, key.get("c2", 0.0), key.get("c3", 1.0), key.get("c4", 1.0))
    return c[field_i * 4: field_i * 4 + 4]  # absolute, handled by caller


def bezier_at(t, t0, t1, v0, v1, cp, absolute):
    """Value of one segment at time t (cp absolute (t,v) pairs in 4.x, normalised in 3.8)."""
    if absolute:
        cx1, cy1, cx2, cy2 = cp
    else:
        cx1 = t0 + cp[0] * (t1 - t0)
        cy1 = v0 + cp[1] * (v1 - v0)
        cx2 = t0 + cp[2] * (t1 - t0)
        cy2 = v0 + cp[3] * (v1 - v0)
    lo, hi = 0.0, 1.0
    for _ in range(40):  # x(s) is monotone for valid Spine curves: bisection
        s = (lo + hi) / 2
        x = (1 - s) ** 3 * t0 + 3 * (1 - s) ** 2 * s * cx1 + 3 * (1 - s) * s * s * cx2 + s ** 3 * t1
        if x < t:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return (1 - s) ** 3 * v0 + 3 * (1 - s) ** 2 * s * cy1 + 3 * (1 - s) * s * s * cy2 + s ** 3 * v1


def evaluate(keys, field, t, is3, default=0.0):
    if not keys:
        return default
    times = [k.get("time", 0.0) for k in keys]
    if t <= times[0]:
        return keys[0].get(field, default)
    if t >= times[-1]:
        return keys[-1].get(field, default)
    i = max(j for j in range(len(times)) if times[j] <= t)
    a, b = keys[i], keys[i + 1]
    t0, t1 = times[i], times[i + 1]
    v0, v1 = a.get(field, default), b.get(field, default)
    cp = curve_points(a, b, 0, is3)
    if cp == "stepped":
        return v0
    if cp == "linear" or t1 - t0 < 1e-9:
        return v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1e-9)
    return bezier_at(t, t0, t1, v0, v1, cp, absolute=not is3)


def sample(keys, field, dur, is3):
    n = max(2, int(round(dur / DT)) + 1)
    return [evaluate(keys, field, i * DT, is3) for i in range(n)]


def beats_of(v):
    """Cut one sampled rotate curve into windup / strike / hold / recover."""
    n = len(v)
    w = [(v[i + 1] - v[i]) / DT for i in range(n - 1)]
    if not w:
        return None
    peak_i = max(range(len(w)), key=lambda i: abs(w[i]))
    peak = abs(w[peak_i])
    if peak < 20:  # deg/s: nothing really strikes
        return None
    s = 1 if w[peak_i] > 0 else -1
    a = peak_i
    while a > 0 and abs(w[a - 1]) > 0.3 * peak and (w[a - 1] > 0) == (s > 0):
        a -= 1
    b = peak_i
    while b < len(w) - 1 and abs(w[b + 1]) > 0.3 * peak and (w[b + 1] > 0) == (s > 0):
        b += 1
    strike_amp = abs(v[b + 1] - v[a])
    if strike_amp < 1:
        return None
    # windup: extreme counter-movement before the strike starts
    pre = [s * (x - v[0]) for x in v[: a + 1]]
    wi = min(range(len(pre)), key=lambda i: pre[i])
    windup_amp = max(0.0, -pre[wi])
    # hold: from strike end, while |w| < 10% of peak
    h0 = b + 1
    # skip settle wobble: first frame after strike where |w| drops under 10%
    while h0 < len(w) and abs(w[h0]) >= 0.1 * peak:
        h0 += 1
    h1 = h0
    while h1 < len(w) and abs(w[h1]) < 0.1 * peak:
        h1 += 1
    hold = (h1 - h0) * DT if h0 < len(w) else 0.0
    # overshoot: furthest point past the pose the hold settles into. The first
    # slow frame after the strike is usually the overshoot peak itself (the
    # settle back is slow), so measure against the END of the hold.
    hold_v = v[min(h1, n - 1)]
    window = v[a: min(n, h1 + 1)]
    over = max(0.0, max(s * (x - hold_v) for x in window)) if window else 0.0
    # recover: after the hold, until back within 10% of the strike amplitude of v[0]
    r = h1
    while r < n - 1 and abs(v[r] - v[0]) > 0.1 * strike_amp:
        r += 1
    return {
        "windup_t": wi * DT,
        "windup_ratio": windup_amp / strike_amp,
        "strike_start": a * DT,
        "strike_dur": (b + 1 - a) * DT,
        "strike_amp": strike_amp,
        "peak_vel": peak,
        "overshoot_ratio": over / strike_amp,
        "hold": hold,
        "recover": max(0.0, (r - h1) * DT),
        "peak_t": peak_i * DT,
    }


def analyze(path):
    d = json.load(open(path, encoding="utf-8"))
    version = (d.get("skeleton") or {}).get("spine") or d.get("source", {}).get("spine", "?")
    is3 = version.startswith("3")
    rfield = "angle" if is3 else "value"
    name = d.get("character") or path
    children = defaultdict(list)
    for b in d.get("bones", []):
        if b.get("parent"):
            children[b["parent"]].append(b["name"])
    out = []
    for an, anim in d.get("animations", {}).items():
        cat = category(an)
        if cat == "idle":
            continue
        dur = 0.0
        for tls in anim.get("bones", {}).values():
            for keys in tls.values():
                for k in keys:
                    dur = max(dur, k.get("time", 0.0))
        if dur < 0.2:
            continue
        rots = {b: tls["rotate"] for b, tls in anim.get("bones", {}).items() if len(tls.get("rotate", [])) > 1}
        if not rots:
            continue
        sampled = {b: sample(k, rfield, dur, is3) for b, k in rots.items()}
        span = {b: max(v) - min(v) for b, v in sampled.items()}
        limbs = [b for b in sampled if LIMB.search(b) and span[b] < 300]
        pool = limbs or [b for b in sampled if (TORSO.search(b) or HEAD.search(b)) and span[b] < 300]
        if not pool:
            continue
        driver = max(pool, key=lambda b: span[b])
        bt = beats_of(sampled[driver])
        if not bt:
            continue
        lag = None
        kids = [c for c in children[driver] if c in sampled and span[c] > 2]
        if kids:
            kb = beats_of(sampled[kids[0]])
            if kb:
                lag = kb["peak_t"] - bt["peak_t"]
        out.append({"character": name, "anim": an, "category": cat, "duration": dur, "driver": driver,
                     "driver_span": span[driver], "chain_lag": lag, **bt})
    return out


def q(vals, p):
    vals = sorted(vals)
    return vals[int(p * (len(vals) - 1))] if vals else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--json")
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        files += sorted(glob.glob(os.path.join(p, "**", "*.json"), recursive=True)) if os.path.isdir(p) else [p]
    rows = []
    for f in files:
        try:
            rows += analyze(f)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            print(f"skip {f}: {e!r}", file=sys.stderr)
    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cols = ["duration", "driver_span", "windup_t", "windup_ratio", "strike_start", "strike_dur", "peak_vel",
            "overshoot_ratio", "hold", "recover", "chain_lag"]
    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
        by["ALL"].append(r)
    print(f"{len(rows)} animations, {len({r['character'] for r in rows})} characters")
    for cat in ["ALL"] + sorted(k for k in by if k != "ALL"):
        rs = by[cat]
        print(f"\n## {cat}  (n={len(rs)})")
        print(f"  {'metric':16s} {'p25':>8s} {'median':>8s} {'p75':>8s}")
        for c in cols:
            vals = [r[c] for r in rs if r[c] is not None]
            if vals:
                print(f"  {c:16s} {q(vals, .25):8.3f} {q(vals, .5):8.3f} {q(vals, .75):8.3f}")
        lag = [r["chain_lag"] for r in rs if r["chain_lag"] is not None]
        if lag:
            print(f"  child later than driver: {100 * sum(1 for x in lag if x > 0.005) / len(lag):.0f}% of {len(lag)}")


if __name__ == "__main__":
    main()
