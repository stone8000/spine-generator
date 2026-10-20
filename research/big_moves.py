"""How do reference characters get away with BIG limb moves?

For every animation in which a limb bone rotates at least --min degrees
peak-to-peak, report which of the known techniques hold the art together
during that move:

  skinned   a weighted mesh spans the joint (vertices weighted to the bone AND
            its parent), so the elbow/shoulder bends instead of hinging a card
  swap      an attachment under that limb is swapped during the animation
            (a different drawing for the raised / foreshortened pose)
  deform    a mesh under that limb has deform (per-vertex) keys
  ik        the bone is driven by an IK constraint
  xform     the bone is driven by a transform constraint (fake perspective)
  order     the animation reorders draw order (limb passes in front/behind)
  lag       the child bone peaks later than this bone (follow-through), in s

    python research/big_moves.py <packs dir or files> [--min 60] [--json out]
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from analyze_motion import LIMB, bezier_extent, field_names, segments  # noqa: E402


def weighted_bones(geom, bone_names):
    """Set of bone names each weighted mesh is bound to."""
    v = geom.get("vertices")
    uvs = geom.get("uvs")
    n = geom.get("vertexCount") or (len(uvs) // 2 if uvs else None)
    if not isinstance(v, list) or n is None or len(v) <= 2 * n:
        return set()
    out, i = set(), 0
    for _ in range(n):
        if i >= len(v):
            break
        c = int(v[i])
        i += 1
        for _ in range(c):
            b = int(v[i])
            if 0 <= b < len(bone_names):
                out.add(bone_names[b])
            i += 4
    return out


def sampled_peak_time(keys, field, version, default):
    """Time of the largest |value - first value| along a rotate timeline."""
    if not keys:
        return None
    base = keys[0].get(field, default)
    best_t, best = keys[0].get("time", 0.0), 0.0
    for t0, t1, f, v0, v1, kind, n in segments(keys, [field], version, default):
        for v, t in ((v0, t0), (v1, t1)):
            if abs(v - base) > best:
                best, best_t = abs(v - base), t
        if kind == "bezier":
            lo, hi = bezier_extent(v0, v1, n)
            for v in (lo, hi):
                if abs(v - base) > best:
                    best, best_t = abs(v - base), (t0 + t1) / 2
    return best_t


def analyze(path, min_deg):
    d = json.load(open(path, encoding="utf-8"))
    version = (d.get("skeleton") or {}).get("spine") or d.get("source", {}).get("spine", "?")
    name = d.get("character") or path
    bones = d.get("bones", [])
    bone_names = [b["name"] for b in bones]
    parent = {b["name"]: b.get("parent") for b in bones}
    children = defaultdict(list)
    for b in bones:
        if b.get("parent"):
            children[b["parent"]].append(b["name"])

    def descendants(b):
        out, stack = {b}, [b]
        while stack:
            for c in children[stack.pop()]:
                out.add(c)
                stack.append(c)
        return out

    slot_bone = {s["name"]: s["bone"] for s in d.get("slots", [])}
    ik_bones = {b for c in d.get("ik", []) for b in c.get("bones", [])}
    xf_bones = {b for c in d.get("transform", []) for b in c.get("bones", [])}

    # meshes per slot and the bones they are skinned to
    skinned_slots = {}
    geo = d.get("skin_geometry")
    if geo is None:
        geo = {}
        skins = d.get("skins", [])
        for skin in skins if isinstance(skins, list) else []:
            for slot, atts in skin.get("attachments", {}).items():
                geo.setdefault(slot, {}).update(atts)
    for slot, atts in geo.items():
        bs = set()
        for a in atts.values():
            if a.get("type", "region") == "mesh":
                bs |= weighted_bones(a, bone_names)
        skinned_slots[slot] = bs

    rows = []
    for an, anim in d.get("animations", {}).items():
        swapped = set()
        for slot, tls in anim.get("slots", {}).items():
            names = [k.get("name") for k in tls.get("attachment", [])]
            if len(set(names)) > 1:
                swapped.add(slot)
        deformed = set()
        for tree in (anim.get("deform", {}), anim.get("attachments", {})):
            for skin in tree.values():
                for slot, atts in skin.items():
                    if atts:
                        deformed.add(slot)
        reorder = len(anim.get("drawOrder", anim.get("draworder", []))) > 0
        rot = {}
        for bone, tls in anim.get("bones", {}).items():
            keys = tls.get("rotate")
            if not keys:
                continue
            f = field_names("rotate", keys[0], version)[0]
            lo = min(k.get(f, 0.0) for k in keys)
            hi = max(k.get(f, 0.0) for k in keys)
            for t0, t1, ff, v0, v1, kind, n in segments(keys, [f], version, 0.0):
                if kind == "bezier":
                    a, b = bezier_extent(v0, v1, n)
                    lo, hi = min(lo, a), max(hi, b)
            rot[bone] = (hi - lo, keys, f)
        for bone, (p2p, keys, f) in rot.items():
            if p2p < min_deg or p2p > 300 or not LIMB.search(bone):
                continue
            sub = descendants(bone)
            slots_here = {s for s, b in slot_bone.items() if b in sub}
            skinned = any(bone in bs and (parent.get(bone) in bs or len(bs & sub) > 1)
                          for bs in skinned_slots.values())
            kids = [c for c in children[bone] if c in rot]
            lag = None
            if kids:
                tp = sampled_peak_time(keys, f, version, 0.0)
                tc = sampled_peak_time(rot[kids[0]][1], rot[kids[0]][2], version, 0.0)
                if tp is not None and tc is not None:
                    lag = round(tc - tp, 3)
            rows.append({
                "character": name, "anim": an, "bone": bone, "p2p": round(p2p, 1),
                "keys": len(keys),
                "skinned": skinned,
                "swap": bool(slots_here & swapped),
                "deform": bool(slots_here & deformed),
                "ik": bone in ik_bones or bool(sub & ik_bones),
                "xform": bone in xf_bones,
                "order": reorder,
                "lag": lag,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--min", type=float, default=60.0)
    ap.add_argument("--json")
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        files += sorted(glob.glob(os.path.join(p, "**", "*.json"), recursive=True)) if os.path.isdir(p) else [p]
    rows = []
    for f in files:
        rows += analyze(f, args.min)
    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = len(rows)
    print(f"{n} big limb moves (>= {args.min:.0f} deg) in {len({r['character'] for r in rows})} characters")
    if not n:
        return
    for k in ("skinned", "swap", "deform", "ik", "xform", "order"):
        print(f"  {k:8s} {100 * sum(r[k] for r in rows) / n:5.1f}%")
    none = sum(not (r["skinned"] or r["swap"] or r["deform"] or r["ik"] or r["xform"]) for r in rows)
    print(f"  {'none':8s} {100 * none / n:5.1f}%   (plain rigid card rotated that far)")
    lags = [r["lag"] for r in rows if r["lag"] is not None]
    if lags:
        lags.sort()
        pos = sum(1 for x in lags if x > 0.01)
        print(f"  child peaks later than parent: {100 * pos / len(lags):.0f}% of {len(lags)}, median lag {lags[len(lags) // 2]:.3f}s")
    keys = sorted(r["keys"] for r in rows)
    print(f"  keys on the big timeline: median {keys[n // 2]}, p25 {keys[n // 4]}, p75 {keys[3 * n // 4]}")
    combo = Counter("+".join(k for k in ("skinned", "swap", "deform", "ik", "xform") if r[k]) or "none" for r in rows)
    print("  technique combos:")
    for c, k in combo.most_common(10):
        print(f"    {k:4d}  {c}")


if __name__ == "__main__":
    main()
