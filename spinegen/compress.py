"""Re-key a Spine 4.x skeleton's bone timelines with the fewest bezier keys.

    python -m spinegen.compress in.json out.json [--rate 240] [--rot 0.15] [--px 0.25] [--scale 0.002]

Every bone timeline is sampled the way the runtime plays it, refitted with
spinegen.curves.fit, and checked again the way the runtime plays it. A timeline
is only replaced when the refit is within tolerance AND has fewer keys.
"""
import argparse
import json

from .curves import fit, max_error, sample_timeline

FIELDS = {
    "rotate": ["value"],
    "translate": ["x", "y"], "translatex": ["value"], "translatey": ["value"],
    "scale": ["x", "y"], "scalex": ["value"], "scaley": ["value"],
    "shear": ["x", "y"], "shearx": ["value"], "sheary": ["value"],
}
DEFAULTS = {"scale": 1.0, "scalex": 1.0, "scaley": 1.0}


def tolerance(tl, args):
    if tl == "rotate" or tl.startswith("shear"):
        return args.rot
    if tl.startswith("scale"):
        return args.scale
    return args.px


def compress_timeline(keys, tl, args, loop):
    if len(keys) < 3 or any(k.get("curve") == "stepped" for k in keys):
        return keys, None
    fields = FIELDS[tl]
    t0, t1 = keys[0].get("time", 0.0), keys[-1].get("time", 0.0)
    if t1 - t0 <= 0:
        return keys, None
    d = DEFAULTS.get(tl, 0.0)
    gaps = [b.get("time", 0.0) - a.get("time", 0.0) for a, b in zip(keys, keys[1:])]
    if len(keys) >= 8 and all("curve" not in k for k in keys) and max(gaps) <= 0.05:
        # A dense all-linear timeline is a smooth motion sampled at a frame
        # rate (Hard Time writes 30 fps). The straight lines between samples
        # are an artefact of that, not motion anyone designed: fit the
        # samples themselves.
        times = [k.get("time", 0.0) for k in keys]
        ch = {f: [k.get(f, d) for k in keys] for f in fields}
    else:
        times, ch = sample_timeline(keys, fields, t1, args.rate, {f: d for f in fields})
        first = next(i for i, tt in enumerate(times) if tt >= t0)
        times, ch = times[first:], {f: v[first:] for f, v in ch.items()}
    is_loop = loop and all(abs(ch[f][0] - ch[f][-1]) < 1e-6 for f in fields)
    new, _ = fit(times, ch, tolerance(tl, args), loop=is_loop)
    err = max_error(new, times, ch, fields)
    ok = all(err[f] <= tolerance(tl, args) * 1.0001 for f in fields)
    if ok and len(new) < len(keys):
        return new, err
    return keys, None


def compress(skel, args):
    stats = {"before": 0, "after": 0, "worst": {}}
    for name, anim in skel.get("animations", {}).items():
        loop = any(w in name.lower() for w in ("idle", "loop"))
        for bone, tls in anim.get("bones", {}).items():
            for tl, keys in list(tls.items()):
                if tl not in FIELDS:
                    continue
                stats["before"] += len(keys)
                new, err = compress_timeline(keys, tl, args, loop)
                tls[tl] = new
                stats["after"] += len(new)
                if err:
                    for f, e in err.items():
                        k = f"{tl}"
                        stats["worst"][k] = max(stats["worst"].get(k, 0.0), e)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--rate", type=float, default=240.0)
    ap.add_argument("--rot", type=float, default=0.15, help="deg")
    ap.add_argument("--px", type=float, default=0.25, help="skeleton units")
    ap.add_argument("--scale", type=float, default=0.002)
    args = ap.parse_args()
    skel = json.load(open(args.src, encoding="utf-8"))
    stats = compress(skel, args)
    json.dump(skel, open(args.dst, "w", encoding="utf-8"), separators=(",", ":"))
    worst = ", ".join(f"{k} {v:.3f}" for k, v in sorted(stats["worst"].items()))
    print(f"bone keys {stats['before']} -> {stats['after']} "
          f"({stats['before'] / max(1, stats['after']):.1f}x fewer); worst runtime error: {worst}")


if __name__ == "__main__":
    main()
