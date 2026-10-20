"""Measure what makes a Spine character's motion big and fluid.

Reads Spine skeleton JSON (3.8 / 4.x) or hacksaw-character-motion motion packs
(*.motion.json, same animation format plus skin_geometry) and prints one row of
rig + animation metrics per character, so the Go Bananas monkey can be put next
to the reference cast on the same scale.

    python research/analyze_motion.py <file-or-dir> [...] [--csv out.csv] [--anims]

Every number is computed from the keyframes themselves, with bezier segments
sampled (not just the key values), so overshoot authored into a curve handle is
counted as motion.
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import sys

LIMB = re.compile(r"arm|hand|fore|elbow|shoulder|wrist|leg|thigh|calf|knee|foot|wing|finger|fist|claw|paw", re.I)
TORSO = re.compile(r"spine|torso|body|chest|hip|pelvis|waist|belly|trunk", re.I)
HEAD = re.compile(r"head|neck|face|jaw", re.I)

BONE_TL = {
    "rotate": ("angle", "value"),
    "translate": ("x", "y"),
    "translatex": ("value",),
    "translatey": ("value",),
    "scale": ("x", "y"),
    "scalex": ("value",),
    "scaley": ("value",),
    "shear": ("x", "y"),
    "shearx": ("value",),
    "sheary": ("value",),
}
DEFAULT = {"scale": 1.0, "scalex": 1.0, "scaley": 1.0}


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    name = d.get("character") or os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
    version = (d.get("skeleton") or {}).get("spine") or (d.get("source") or {}).get("spine", "?")
    height = (d.get("skeleton") or {}).get("height") or 1000.0
    return name, version, float(height), d


def attachments(d):
    """Yield attachment dicts from either a real skeleton or a motion pack."""
    if "skin_geometry" in d:
        for slot in d["skin_geometry"].values():
            yield from slot.values()
        # regions are not stored in skin_geometry; layers lists every attachment
        meshes = sum(len(s) for s in d["skin_geometry"].values())
        for _ in range(max(0, len(d.get("layers", {})) - meshes)):
            yield {"type": "region"}
        return
    skins = d.get("skins", [])
    if isinstance(skins, dict):  # 3.7 style
        skins = [{"name": k, "attachments": v} for k, v in skins.items()]
    for skin in skins:
        for slot in skin.get("attachments", {}).values():
            yield from slot.values()


def field_names(tl, key, version):
    names = BONE_TL[tl]
    if tl == "rotate":
        return ["angle"] if version.startswith("3") else ["value"]
    return list(names)


def segments(keys, fields, version, default):
    """Yield (t0, t1, field, v0, v1, kind, (cy1, cy2) or None) per segment."""
    is3 = version.startswith("3")
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        t0, t1 = a.get("time", 0.0), b.get("time", 0.0)
        c = a.get("curve")
        for fi, f in enumerate(fields):
            v0, v1 = a.get(f, default), b.get(f, default)
            if c == "stepped":
                yield t0, t1, f, v0, v1, "stepped", None
            elif c is None:
                yield t0, t1, f, v0, v1, "linear", None
            elif is3:
                # 3.8: curve=cx1, c2=cy1, c3=cx2, c4=cy2, normalised 0..1
                cx1, cy1 = c, a.get("c2", 0.0)
                cx2, cy2 = a.get("c3", 1.0), a.get("c4", 1.0)
                yield t0, t1, f, v0, v1, "bezier", (cx1, cy1, cx2, cy2)
            else:
                # 4.x: absolute (time, value) control points, 4 numbers per field
                cx1, cy1, cx2, cy2 = c[fi * 4: fi * 4 + 4]
                dt = (t1 - t0) or 1e-9
                dv = v1 - v0
                ny1 = (cy1 - v0) / dv if abs(dv) > 1e-9 else (0.0 if abs(cy1 - v0) < 1e-6 else math.copysign(9, cy1 - v0))
                ny2 = (cy2 - v0) / dv if abs(dv) > 1e-9 else (1.0 if abs(cy2 - v0) < 1e-6 else math.copysign(9, cy2 - v0))
                yield t0, t1, f, v0, v1, "bezier", ((cx1 - t0) / dt, ny1, (cx2 - t0) / dt, ny2)


def bezier_extent(v0, v1, n):
    """min/max of a normalised bezier segment mapped onto v0..v1 (sampled)."""
    cx1, cy1, cx2, cy2 = n
    lo = hi = v0
    for i in range(1, 17):
        s = i / 16
        y = 3 * (1 - s) ** 2 * s * cy1 + 3 * (1 - s) * s * s * cy2 + s ** 3
        v = v0 + (v1 - v0) * y
        lo, hi = min(lo, v), max(hi, v)
    return lo, hi


def analyze_animation(name, anim, version, height, bone_names):
    m = {
        "anim": name,
        "duration": 0.0,
        "keys": 0,
        "timelines": 0,
        "linear": 0,
        "stepped": 0,
        "bezier": 0,
        "overshoot": 0,
        "flat_ease": 0,  # bezier whose handles leave/arrive at zero velocity
        "limb_rot_p2p": 0.0,
        "torso_rot_p2p": 0.0,
        "head_rot_p2p": 0.0,
        "any_rot_p2p": 0.0,
        "trans_p2p_h": 0.0,
        "scale_min": 1.0,
        "scale_max": 1.0,
        "deform_tl": 0,
        "attach_swaps": 0,
        "draworder_keys": 0,
        "ik_tl": 0,
        "transform_tl": 0,
        "bones_animated": 0,
        "key_times": set(),
    }
    animated = set()
    for bone, tls in anim.get("bones", {}).items():
        for tl, keys in tls.items():
            if tl not in BONE_TL or not keys:
                continue
            fields = field_names(tl, keys[0], version)
            default = DEFAULT.get(tl, 0.0)
            m["timelines"] += 1
            m["keys"] += len(keys)
            for k in keys:
                m["key_times"].add(round(k.get("time", 0.0), 3))
                m["duration"] = max(m["duration"], k.get("time", 0.0))
            lo = {f: min(k.get(f, default) for k in keys) for f in fields}
            hi = {f: max(k.get(f, default) for k in keys) for f in fields}
            for t0, t1, f, v0, v1, kind, n in segments(keys, fields, version, default):
                m[kind] += 1
                if kind == "bezier":
                    a, b = bezier_extent(v0, v1, n)
                    span = abs(v1 - v0)
                    if a < min(v0, v1) - max(0.02 * span, 1e-3) or b > max(v0, v1) + max(0.02 * span, 1e-3):
                        m["overshoot"] += 1
                    lo[f], hi[f] = min(lo[f], a), max(hi[f], b)
                    cx1, cy1, cx2, cy2 = n
                    if abs(cy1) < 0.02 and abs(cy2 - 1) < 0.02:
                        m["flat_ease"] += 1
            moved = any(hi[f] - lo[f] > 1e-3 for f in fields)
            if moved:
                animated.add(bone)
            if tl == "rotate":
                p2p = hi[fields[0]] - lo[fields[0]]
                m["any_rot_p2p"] = max(m["any_rot_p2p"], p2p)
                if LIMB.search(bone):
                    m["limb_rot_p2p"] = max(m["limb_rot_p2p"], p2p)
                elif HEAD.search(bone):
                    m["head_rot_p2p"] = max(m["head_rot_p2p"], p2p)
                elif TORSO.search(bone):
                    m["torso_rot_p2p"] = max(m["torso_rot_p2p"], p2p)
            elif tl.startswith("translate"):
                p2p = math.hypot(*[hi[f] - lo[f] for f in fields])
                m["trans_p2p_h"] = max(m["trans_p2p_h"], p2p / height)
            elif tl.startswith("scale"):
                m["scale_min"] = min(m["scale_min"], *lo.values())
                m["scale_max"] = max(m["scale_max"], *hi.values())
    m["bones_animated"] = len(animated)

    for slot, tls in anim.get("slots", {}).items():
        att = tls.get("attachment", [])
        names = [k.get("name") for k in att]
        m["attach_swaps"] += sum(1 for a, b in zip(names, names[1:]) if a != b)
        for k in att:
            m["duration"] = max(m["duration"], k.get("time", 0.0))
    m["draworder_keys"] = len(anim.get("drawOrder", anim.get("draworder", [])))
    m["ik_tl"] = len(anim.get("ik", {}))
    m["transform_tl"] = len(anim.get("transform", {}))

    def count_deform(tree):
        n = 0
        for skin in tree.values():
            for slot in skin.values():
                for att in slot.values():
                    if isinstance(att, dict):  # 4.1+: {"deform": [...], "sequence": [...]}
                        n += 1 if att.get("deform") else 0
                    elif att:
                        n += 1
        return n

    m["deform_tl"] = count_deform(anim.get("deform", {})) + count_deform(anim.get("attachments", {}))
    return m


def analyze(path):
    name, version, height, d = load(path)
    atts = list(attachments(d))
    meshes = [a for a in atts if a.get("type") in ("mesh", "linkedmesh")]
    weighted = [a for a in meshes if a.get("type") == "mesh" and isinstance(a.get("vertices"), list)
                and "uvs" in a and len(a["vertices"]) > len(a["uvs"])]
    if "skin_geometry" in d:
        weighted = [l for l in d.get("layers", {}).values() if l.get("weighted")]
    bones = d.get("bones", [])
    row = {
        "character": name,
        "spine": version,
        "height": height,
        "bones": len(bones),
        "limb_bones": sum(1 for b in bones if LIMB.search(b["name"])),
        "meshes": len(meshes),
        "weighted_meshes": len(weighted),
        "regions": len(atts) - len(meshes),
        "ik": len(d.get("ik", [])),
        "transform": len(d.get("transform", [])),
        "path": len(d.get("path", [])),
        "physics": len(d.get("physics", [])),
        "animations": len(d.get("animations", {})),
    }
    anims = [analyze_animation(n, a, version, height, {b["name"] for b in bones})
             for n, a in d.get("animations", {}).items()]
    keyed_anims = [a for a in anims if a["duration"] > 0]
    tot = lambda k: sum(a[k] for a in anims)
    segs = tot("linear") + tot("stepped") + tot("bezier")
    dur = sum(a["duration"] for a in keyed_anims) or 1e-9
    row.update({
        "max_limb_rot": round(max((a["limb_rot_p2p"] for a in anims), default=0), 1),
        "max_torso_rot": round(max((a["torso_rot_p2p"] for a in anims), default=0), 1),
        "max_head_rot": round(max((a["head_rot_p2p"] for a in anims), default=0), 1),
        "max_any_rot": round(max((a["any_rot_p2p"] for a in anims), default=0), 1),
        "max_trans_h": round(max((a["trans_p2p_h"] for a in anims), default=0), 3),
        "scale_min": round(min((a["scale_min"] for a in anims), default=1), 3),
        "scale_max": round(max((a["scale_max"] for a in anims), default=1), 3),
        "keys_per_s": round(tot("keys") / dur, 1),
        "keys_per_tl": round(tot("keys") / max(1, tot("timelines")), 1),
        "pct_bezier": round(100 * tot("bezier") / max(1, segs), 1),
        "pct_linear": round(100 * tot("linear") / max(1, segs), 1),
        "pct_stepped": round(100 * tot("stepped") / max(1, segs), 1),
        "pct_overshoot": round(100 * tot("overshoot") / max(1, tot("bezier")), 1),
        "pct_flat_ease": round(100 * tot("flat_ease") / max(1, tot("bezier")), 1),
        "deform_tl": tot("deform_tl"),
        "attach_swaps": tot("attach_swaps"),
        "draworder_keys": tot("draworder_keys"),
        "ik_tl": tot("ik_tl"),
        "transform_tl": tot("transform_tl"),
        "distinct_key_times_per_s": round(sum(len(a["key_times"]) for a in keyed_anims) / dur, 1),
    })
    return row, anims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--csv")
    ap.add_argument("--anims", action="store_true", help="print per-animation rows too")
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "**", "*.json"), recursive=True))
        else:
            files.append(p)
    rows = []
    for f in files:
        try:
            row, anims = analyze(f)
        except (KeyError, ValueError, TypeError) as e:
            print(f"skip {f}: {e!r}", file=sys.stderr)
            continue
        rows.append(row)
        if args.anims:
            for a in anims:
                print(f"  {row['character']:40s} {a['anim']:32s} dur={a['duration']:.2f} "
                      f"limb={a['limb_rot_p2p']:.0f} torso={a['torso_rot_p2p']:.0f} head={a['head_rot_p2p']:.0f} "
                      f"trans/h={a['trans_p2p_h']:.3f} keys={a['keys']} deform={a['deform_tl']} swaps={a['attach_swaps']}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    if not args.anims:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
