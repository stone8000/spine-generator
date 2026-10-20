"""Emit the IR as Spine 4.2 skeleton JSON.

    python -m spinegen.emit.spine42 build/rig.json out/name.json [--spine 4.2.43]

EMITTER RULES (SOTA §2, §3.1)
  * Every field is written explicitly, above all where 4.2 and 4.3 defaults
    differ (physics inertia/strength/damping/mass/limit/fps; the six
    transform-constraint mixes): a file must mean the same thing whichever
    runtime reads it.
  * Constraint `order` is renumbered 0..N-1. 4.2 silently skips a constraint
    whose order is past the end of that sequence (a gap pushes it there, E4)
    and runs only the first of two that share one; renumbering in
    evaluation order keeps every constraint that used to run running in the
    same order. A constraint that never ran (IR `runs: False`) would start
    running once renumbered, so it is written INERT instead: every mix 0,
    its mix no longer global, and the timelines that key it dropped. The
    file then plays as before; report["inert"] and report["warnings"] say
    which ones.
  * `inherit`, never 4.1's `transform`; `skeleton.referenceScale` always;
    `"spine": "4.2.43"` unless told otherwise (Hard Time's version; the
    runtime does not check the string, L1 does).
  * Meshes: the outline comes first and `hull` counts it (meshtopo.py);
    every vertex list, UV list, triangle, edge and deform key is permuted to
    match.
  * Vertices: IR setup-world positions become each bone's setup-local
    coordinates through lbs.bind_pose, the exact inverse of the reader.
  * Semantic constraints expand here: `half_angle` becomes a local,
    absolute, rotate-only transform constraint; a physics `spring` (f, zeta)
    becomes strength and damping (ir.spring_to_spine).
"""
import argparse
import bisect
import hashlib
import json

from .. import ir as irmod
from ..lbs import bind_pose, to_local
from ..meshtopo import boundary_loops, hull_permutation, outside_outline, permute_offsets, reorder_mesh

DEFAULT_VERSION = "4.2.43"
DIGITS = 6  # 1e-6 px / deg / s: far below what any gate can see, files stay readable


def emit(ir, version=DEFAULT_VERSION, report=None):
    """IR -> Spine 4.2 JSON dict. `report`, if a dict, receives what changed."""
    errs = irmod.validate(ir)
    if errs:
        raise ValueError("invalid IR:\n  " + "\n  ".join(errs[:20]))
    return _Emitter(prepare(ir, report), version).run()


def prepare(ir, report=None):
    """The IR as the emitted file will read back (a normalised copy).

    Everything the emitter changes about a rig happens here, at the IR
    level, so `read(emit(ir))` equals `prepare(ir)` up to rounding and tests
    can say so directly: constraint orders renumbered, semantic constraints
    expanded, mesh outlines moved first (deform keys follow their vertices).
    """
    report = report if report is not None else {}
    report.setdefault("orders", {})
    report.setdefault("hull", [])
    report.setdefault("warnings", [])
    report.setdefault("inert", [])
    out = irmod.clone(ir)
    for kind, c in irmod.constraints(out):
        if not c.get("runs", True):
            _make_inert(out, kind, c, report)
    for i, (_, c) in enumerate(irmod.constraints(out)):
        if c.get("order") != i:
            report["orders"][c["name"]] = (c.get("order"), i)
        c["order"] = i
        c["runs"] = True  # contiguous orders: everything runs (the inert ones as no-ops)
    out["transform"] = [_expand_half_angle(c) for c in out["transform"]]
    for c in out["physics"]:
        if c.get("spring"):
            c["strength"], c["damping"] = irmod.spring_to_spine(c["spring"]["f"], c["spring"]["zeta"], c["mass"])
            c["spring"] = None
    plans = {}
    for skin, slot, key, att in irmod.iter_attachments(out):
        if att["type"] != "mesh":
            continue
        loops = boundary_loops(att["triangles"])
        perm, hull = hull_permutation(att["triangles"], len(att["vertices"]), att["uvs"], att["hull"], loops)
        if perm is None:
            report["warnings"].append(f"{skin}/{slot}/{key}: boundary does not close; hull kept")
            continue
        if len(loops) > 1:
            # Spine's hull is one polygon: a hole's rim is interior to it, an island is not
            outer = {perm[i] for i in range(hull)}
            outer_loop = next((lp for lp in loops if set(lp) == outer), None)
            stray = outside_outline(att["uvs"], loops, outer_loop) if outer_loop else []
            rim = len(set().union(*loops)) - hull - len(stray)
            what = []
            if rim:
                what.append(f"{rim} vertices rim a hole inside the hull (legal; the editor fills the hole "
                            "if the mesh is re-triangulated)")
            if stray:
                what.append(f"{len(stray)} vertices form an island outside it, which no one-polygon hull "
                            "can hold (L8 flags them)")
            report["warnings"].append(f"{skin}/{slot}/{key}: {len(loops)} outlines; the hull is the outer one; "
                                      + "; ".join(what))
        identity = perm == list(range(len(perm)))
        if identity and hull == att["hull"]:
            continue
        report["hull"].append({"skin": skin, "slot": slot, "attachment": key, "hull": (att["hull"], hull),
                               "reordered": not identity, "perm": perm})
        w = att.get("weights")
        sizes = [2 * len(r) for r in w] if w is not None else [2] * len(att["vertices"])
        plans[id(att)] = (att, perm, hull, sizes, identity)
    for anim in out.get("animations", []):
        for tl in anim["timelines"]:
            if tl["type"] != "deform":
                continue
            t = tl["target"]
            geo = irmod.geometry_of(out, t["slot"], irmod.resolve_attachment(out, t["skin"], t["slot"], t["attachment"]))
            plan = plans.get(id(geo))
            if plan and not plan[4]:
                for k in tl["keys"]:
                    if k["offsets"] is not None:
                        k["offsets"] = permute_offsets(k["offsets"], plan[1], plan[3])
    for att, perm, hull, _, _ in plans.values():
        reorder_mesh(att, perm, hull)
    return out


def _make_inert(ir, kind, c, report):
    """A constraint that never ran, made a no-op that renumbering cannot wake:
    every mix 0 (each constraint returns at once on mix 0), a physics mix
    no longer driven by the global mix timeline, and no timeline that keys
    it (a mix key would turn it on)."""
    if kind == "ik" or c.get("kind") == "half_angle":
        c["mix"] = 0.0
    elif kind == "physics":
        c["mix"] = 0.0
        c["global"] = dict(c["global"], mix=False)
    else:
        c["mix"] = {k: 0.0 for k in c["mix"]}
    dropped = 0
    for anim in ir.get("animations", []):
        keep = [tl for tl in anim["timelines"] if not (tl["type"] == kind and tl["target"] == c["name"])]
        dropped += len(anim["timelines"]) - len(keep)
        anim["timelines"] = keep
    report["inert"].append({"kind": kind, "name": c["name"], "order": c.get("order"), "timelines_dropped": dropped})
    report["warnings"].append(f"{kind} {c['name']}: never ran in the source (order {c.get('order')}); written "
                              f"inert (mix 0, {dropped} timeline(s) dropped) so the file plays the same")


def _expand_half_angle(c):
    """half_angle: the helper turns by `mix` (default half) of the angle
    between itself and its target, in local space — a local, absolute,
    rotate-only transform constraint. The runtime does not wrap that angle,
    so past 180 deg the helper keeps following the elbow the same way round
    (lbs models exactly this)."""
    if c.get("kind") != "half_angle":
        return c
    zero = {"x": 0.0, "y": 0.0, "scale_x": 0.0, "scale_y": 0.0, "shear_y": 0.0}
    return {"name": c["name"], "order": c["order"], "runs": c.get("runs", True),
            "skin_required": c.get("skin_required", False),
            "kind": "transform", "bones": [c["bone"]], "target": c["target"], "local": True, "relative": False,
            "offset": {"rotation": 0.0, **zero}, "mix": {"rotate": float(c.get("mix", 0.5)), **zero}}


def write(ir, path, version=DEFAULT_VERSION, report=None):
    skel = emit(ir, version, report)
    with open(path, "w") as f:
        f.write(dumps(skel))
    return skel


def dumps(skel):
    return json.dumps(skel, indent=1, ensure_ascii=False) + "\n"


def _n(x):
    """A number as written: rounded, integral values as ints, no -0."""
    v = round(float(x), DIGITS)
    if v == int(v) and abs(v) < 1e15:
        return int(v)
    return v


def _hex(vals):
    return "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in vals)


def _curve(c):
    if c is None or c == "stepped":
        return c
    return [_n(v) for v in c]


def _with_curve(out, key):
    c = _curve(key.get("curve"))
    if c is not None:
        out["curve"] = c
    return out


class _Emitter:
    """Mechanical serialisation of a prepare()d IR."""

    def __init__(self, ir, version):
        self.ir = ir
        self.version = version
        self.bind = bind_pose(ir)
        self.bidx = irmod.bone_index(ir)
        self.slot_bone = {s["name"]: s["bone"] for s in ir["slots"]}

    def run(self):
        ir = self.ir
        out = {"skeleton": self.skeleton()}
        out["bones"] = [self.bone(b) for b in ir["bones"]]
        out["slots"] = [self.slot(s) for s in ir["slots"]]
        for kind, fn in (("ik", self.ik), ("transform", self.transform), ("path", self.path),
                         ("physics", self.physics)):
            if ir[kind]:
                out[kind] = [fn(c, c["order"]) for c in ir[kind]]
        out["skins"] = [self.skin(s) for s in ir["skins"]]
        if ir["events"]:
            out["events"] = {e["name"]: self.event(e) for e in ir["events"]}
        if ir.get("animations"):
            out["animations"] = {a["name"]: self.animation(a) for a in ir["animations"]}
        return out

    # ---- skeleton

    def skeleton(self):
        sk = self.ir["skeleton"]
        h = sk.get("hash") or hashlib.sha256(
            json.dumps(self.ir, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:22]
        out = {"hash": h, "spine": self.version, "x": _n(sk["x"]), "y": _n(sk["y"]),
               "width": _n(sk["width"]), "height": _n(sk["height"]),
               "referenceScale": _n(sk["reference_scale"])}
        for k in ("fps", "images", "audio"):
            if sk.get(k) is not None:
                out[k] = sk[k]
        return out

    def bone(self, b):
        out = {"name": b["name"]}
        if b["parent"] is not None:
            out["parent"] = b["parent"]
        out.update(length=_n(b["length"]), rotation=_n(b["rotation"]), x=_n(b["x"]), y=_n(b["y"]),
                   scaleX=_n(b["scale_x"]), scaleY=_n(b["scale_y"]), shearX=_n(b["shear_x"]),
                   shearY=_n(b["shear_y"]), inherit=b["inherit"])
        if b["skin_required"]:
            out["skin"] = True
        for k in ("color", "icon", "visible"):
            if b.get(k) is not None:
                out[k] = b[k]
        return out

    def slot(self, s):
        out = {"name": s["name"], "bone": s["bone"], "color": s["color"]}
        if s.get("dark") is not None:
            out["dark"] = s["dark"]
        if s["attachment"] is not None:
            out["attachment"] = s["attachment"]
        out["blend"] = s["blend"]
        if s.get("visible") is not None:
            out["visible"] = s["visible"]
        return out

    # ---- constraints

    def _head(self, c, order):
        out = {"name": c["name"], "order": order}
        if c.get("skin_required"):
            out["skin"] = True
        return out

    def ik(self, c, order):
        out = self._head(c, order)
        out.update(bones=list(c["bones"]), target=c["target"], mix=_n(c["mix"]), softness=_n(c["softness"]),
                   bendPositive=bool(c["bend_positive"]), compress=bool(c["compress"]),
                   stretch=bool(c["stretch"]), uniform=bool(c["uniform"]))
        return out

    def transform(self, c, order):
        o, m = c["offset"], c["mix"]
        out = self._head(c, order)
        out.update(bones=list(c["bones"]), target=c["target"], local=bool(c["local"]),
                   relative=bool(c["relative"]), rotation=_n(o["rotation"]), x=_n(o["x"]), y=_n(o["y"]),
                   scaleX=_n(o["scale_x"]), scaleY=_n(o["scale_y"]), shearY=_n(o["shear_y"]),
                   mixRotate=_n(m["rotate"]), mixX=_n(m["x"]), mixY=_n(m["y"]), mixScaleX=_n(m["scale_x"]),
                   mixScaleY=_n(m["scale_y"]), mixShearY=_n(m["shear_y"]))
        return out

    def path(self, c, order):
        m = c["mix"]
        out = self._head(c, order)
        out.update(bones=list(c["bones"]), target=c["target"], positionMode=c["position_mode"],
                   spacingMode=c["spacing_mode"], rotateMode=c["rotate_mode"], rotation=_n(c["offset_rotation"]),
                   position=_n(c["position"]), spacing=_n(c["spacing"]), mixRotate=_n(m["rotate"]),
                   mixX=_n(m["x"]), mixY=_n(m["y"]))
        return out

    def physics(self, c, order):
        strength, damping = c["strength"], c["damping"]
        out = self._head(c, order)
        out.update(bone=c["bone"], x=_n(c["x"]), y=_n(c["y"]), rotate=_n(c["rotate"]), scaleX=_n(c["scale_x"]),
                   shearX=_n(c["shear_x"]), limit=_n(c["limit"]), fps=_n(c["fps"]), inertia=_n(c["inertia"]),
                   strength=_n(strength), damping=_n(damping), mass=_n(c["mass"]), wind=_n(c["wind"]),
                   gravity=_n(c["gravity"]), mix=_n(c["mix"]))
        for k, v in c["global"].items():
            if v:
                out[k + "Global"] = True
        return out

    # ---- meshes

    def vertex_values(self, slot, att):
        """Spine's vertex array: bone-local values from IR setup-world ones."""
        verts, weights = att["vertices"], att.get("weights")
        out = []
        if weights is None:
            m = self.bind[self.slot_bone[slot]]
            for x, y in verts:
                lx, ly = to_local(m, x, y)
                out += [_n(lx), _n(ly)]
            return out
        for i, row in enumerate(weights):
            out.append(len(row))
            for inf in row:
                px, py = inf[2] if len(inf) > 2 else verts[i]
                lx, ly = to_local(self.bind[inf[0]], px, py)
                out += [self.bidx[inf[0]], _n(lx), _n(ly), _n(inf[1])]
        return out

    def skin(self, s):
        out = {"name": s["name"]}
        for k in ("bones", "ik", "transform", "path", "physics"):
            if s.get(k):
                out[k] = list(s[k])
        if s.get("color") is not None:
            out["color"] = s["color"]
        out["attachments"] = {slot: {key: self.attachment(slot, key, a) for key, a in atts.items()}
                              for slot, atts in s["attachments"].items()}
        return out

    def attachment(self, slot, key, a):
        typ = a["type"]
        out = {"type": typ}
        if a["name"] != key:
            out["name"] = a["name"]
        if typ in ("region", "mesh", "linkedmesh") and a.get("path") not in (None, a["name"]):
            out["path"] = a["path"]
        if typ == "region":
            out.update(x=_n(a["x"]), y=_n(a["y"]), rotation=_n(a["rotation"]), scaleX=_n(a["scale_x"]),
                       scaleY=_n(a["scale_y"]))
            for k in ("width", "height"):
                if a.get(k) is not None:
                    out[k] = _n(a[k])
            out["color"] = a["color"]
        elif typ == "mesh":
            out["color"] = a["color"]
            out["uvs"] = [_n(c) for uv in a["uvs"] for c in uv]
            out["triangles"] = [v for t in a["triangles"] for v in t]
            out["vertices"] = self.vertex_values(slot, a)
            out["hull"] = a["hull"]
            if a.get("edges") is not None:
                out["edges"] = [2 * v for e in a["edges"] for v in e]  # Spine writes vertex index * 2
            for k in ("width", "height"):
                if a.get(k) is not None:
                    out[k] = _n(a[k])
        elif typ == "linkedmesh":
            out["color"] = a["color"]
            out["parent"] = a["parent"]
            if a.get("skin") is not None:
                out["skin"] = a["skin"]
            out["timelines"] = bool(a["timelines"])
            for k in ("width", "height"):
                if a.get(k) is not None:
                    out[k] = _n(a[k])
        elif typ in ("boundingbox", "clipping", "path"):
            if typ == "clipping" and a.get("end") is not None:
                out["end"] = a["end"]
            if typ == "path":
                out.update(closed=bool(a["closed"]), constantSpeed=bool(a["constant_speed"]),
                           lengths=[_n(v) for v in a["lengths"]])
            out["vertexCount"] = len(a["vertices"])
            out["vertices"] = self.vertex_values(slot, a)
        elif typ == "point":
            out.update(x=_n(a["x"]), y=_n(a["y"]), rotation=_n(a["rotation"]))
        else:
            raise ValueError(f"attachment {a['name']!r}: unknown type {typ!r}")
        if typ in ("boundingbox", "clipping", "path", "point") and a.get("color") is not None:
            out["color"] = a["color"]
        if a.get("sequence") is not None:
            out["sequence"] = dict(a["sequence"])
        return out

    # ---- events and animations

    def event(self, e):
        out = {"int": e["int"], "float": _n(e["float"]), "string": e["string"]}
        if e.get("audio") is not None:
            out.update(audio=e["audio"], volume=_n(e["volume"]), balance=_n(e["balance"]))
        return out

    def animation(self, a):
        out = {}
        for tl in sorted(a["timelines"], key=lambda t: irmod.SECTION_ORDER.index(t["type"])):
            typ, tgt, prop, keys = tl["type"], tl["target"], tl["property"], tl["keys"]
            if typ == "bone":
                out.setdefault("bones", {}).setdefault(tgt, {})[prop] = [self.bone_key(prop, k) for k in keys]
            elif typ == "slot":
                out.setdefault("slots", {}).setdefault(tgt, {})[prop] = [self.slot_key(prop, k) for k in keys]
            elif typ == "ik":
                out.setdefault("ik", {})[tgt] = [_with_curve(
                    {"time": _n(k["time"]), "mix": _n(k["mix"]), "softness": _n(k["softness"]),
                     "bendPositive": bool(k["bend_positive"]), "compress": bool(k["compress"]),
                     "stretch": bool(k["stretch"])}, k) for k in keys]
            elif typ == "transform":
                out.setdefault("transform", {})[tgt] = [_with_curve(
                    {"time": _n(k["time"]), "mixRotate": _n(k["mix_rotate"]), "mixX": _n(k["mix_x"]),
                     "mixY": _n(k["mix_y"]), "mixScaleX": _n(k["mix_scale_x"]), "mixScaleY": _n(k["mix_scale_y"]),
                     "mixShearY": _n(k["mix_shear_y"])}, k) for k in keys]
            elif typ == "path":
                if prop == "mix":
                    ks = [_with_curve({"time": _n(k["time"]), "mixRotate": _n(k["mix_rotate"]),
                                       "mixX": _n(k["mix_x"]), "mixY": _n(k["mix_y"])}, k) for k in keys]
                else:
                    ks = [_with_curve({"time": _n(k["time"]), "value": _n(k["value"])}, k) for k in keys]
                out.setdefault("path", {}).setdefault(tgt, {})[prop] = ks
            elif typ == "physics":
                if prop == "reset":
                    ks = [{"time": _n(k["time"])} for k in keys]
                else:
                    ks = [_with_curve({"time": _n(k["time"]), "value": _n(k["value"])}, k) for k in keys]
                out.setdefault("physics", {}).setdefault(tgt, {})[prop] = ks
            elif typ in ("deform", "sequence"):
                slot = out.setdefault("attachments", {}).setdefault(tgt["skin"], {}).setdefault(tgt["slot"], {})
                entry = slot.setdefault(tgt["attachment"], {})
                if typ == "deform":
                    entry["deform"] = self.deform_keys(keys)
                else:
                    entry["sequence"] = [{"time": _n(k["time"]), "mode": k["mode"], "index": k["index"],
                                          "delay": _n(k["delay"])} for k in keys]
            elif typ == "draw_order":
                out["drawOrder"] = [self.draw_order_key(k) for k in keys]
            elif typ == "event":
                out["events"] = [{"time": _n(k["time"]), "name": k["name"], "int": k["int"],
                                  "float": _n(k["float"]), "string": k["string"], "volume": _n(k["volume"]),
                                  "balance": _n(k["balance"])} for k in keys]
            else:
                raise ValueError(f"unknown timeline type {typ!r}")
        return out

    def bone_key(self, prop, k):
        if prop == "inherit":
            return {"time": _n(k["time"]), "inherit": k["inherit"]}
        out = {"time": _n(k["time"])}
        for f in irmod.CHANNELS[("bone", prop)]:
            out[f] = _n(k[f])
        return _with_curve(out, k)

    def slot_key(self, prop, k):
        out = {"time": _n(k["time"])}
        if prop == "attachment":
            out["name"] = k["name"]
            return out
        if prop == "alpha":
            out["value"] = _n(k["value"])
        elif prop in ("rgba", "rgb"):
            out["color"] = _hex([k[c] for c in "rgba"[:4 if prop == "rgba" else 3]])
        else:
            out["light"] = _hex([k[c] for c in "rgba"[:4 if prop == "rgba2" else 3]])
            out["dark"] = _hex([k["r2"], k["g2"], k["b2"]])
        return _with_curve(out, k)

    def deform_keys(self, keys):
        out = []
        for k in keys:
            key = {"time": _n(k["time"])}
            off = k["offsets"]
            if off is not None:
                nz = [i for i, v in enumerate(off) if _n(v) != 0]
                if nz:  # Spine stores the span between the first and last non-zero value
                    key["offset"] = nz[0]
                    key["vertices"] = [_n(v) for v in off[nz[0]:nz[-1] + 1]]
            out.append(_with_curve(key, k))
        return out

    def draw_order_key(self, k):
        out = {"time": _n(k["time"])}
        if k["order"] is None:
            return out
        names = [s["name"] for s in self.ir["slots"]]
        index = {n: i for i, n in enumerate(names)}
        target = [index[n] for n in k["order"]]
        keep = set(_longest_increasing(target))
        moved = sorted((orig, pos) for pos, orig in enumerate(target) if orig not in keep)
        out["offsets"] = [{"slot": names[orig], "offset": pos - orig} for orig, pos in moved]
        return out


def _longest_increasing(seq):
    """One longest strictly increasing subsequence of `seq` (values)."""
    tails, tails_i, prev = [], [], [-1] * len(seq)
    for i, v in enumerate(seq):
        j = bisect.bisect_left(tails, v)
        if j == len(tails):
            tails.append(v)
            tails_i.append(i)
        else:
            tails[j] = v
            tails_i[j] = i
        prev[i] = tails_i[j - 1] if j else -1
    out, i = [], tails_i[-1] if tails_i else -1
    while i >= 0:
        out.append(seq[i])
        i = prev[i]
    return out[::-1]


def main(argv=None):
    from ..ir import load
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig")
    ap.add_argument("out")
    ap.add_argument("--spine", default=DEFAULT_VERSION)
    args = ap.parse_args(argv)
    report = {}
    write(load(args.rig), args.out, args.spine, report)
    for w in report["warnings"]:
        print("warning:", w)
    print(f"wrote {args.out}: {len(report['hull'])} meshes re-hulled, {len(report['orders'])} constraints renumbered")


if __name__ == "__main__":
    main()
