"""Read Spine 4.2 skeleton JSON (and 4.0 / 4.1, which differ only in a few
names) into the version-neutral IR (ir.py).

WHY HERE AND NOT IN THE IR
  Defaults are a property of a file format version, not of the rig: a
  missing physics `damping` means 1 in 4.2 and 0.85 in 4.3. The reader
  resolves every default the way SkeletonJson 4.2 documents them, so the IR
  never carries "whatever the runtime assumes".

READ AS 4.2.120 READS IT
  The IR must pose a file the way the judge (spine-core 4.2.120) poses it,
  so the reader takes exactly the keys SkeletonJson 4.2 takes. Older names
  that 4.2 IGNORES are ignored here too, each with a warning in
  ir["source"]["warnings"] (L1 VERSION flags the same keys):
    bone `transform` (4.1 name of `inherit`: the bone stays normal),
    linked mesh `deform` (old name of `timelines`: stays true),
    animation `deform` (old home of deform keys: none are read),
    animation `draworder` (old name of `drawOrder`: no draw-order keys),
    skins as a {name: attachments} dict (pre-4.0: no skins at all).
  read(..., upgrade_legacy=True) reads them as their 4.2 equivalents
  instead. That changes how the file plays, so each one is recorded as a
  "behaviour change" warning; reemit_check reports them.

  Constraint order is resolved the same way: 4.2 runs one constraint per
  order value 0..N-1 (ir.dead_by_order_42), so a constraint in a gap or
  sharing an order is stored with `runs: False` (and a warning); lbs skips
  it and the emitter writes it inert.

  A file that declares another version but uses 4.2-only sections (the
  monkey: "4.1.20" with top-level physics) is read with 4.2 rules and gets a
  warning — L1 VERSION is qa's to fail.
"""
import json
import os

from . import ir as irmod
from .lbs import bind_pose, to_world

# An influence within this distance (px, absolute) of its vertex counts as
# agreeing. Far above the emitter's rounding noise (~1e-6), far below the
# ~1e-3..1e-2 px disagreement of an editor file's 2-decimal local coordinates.
SAME_PX = 1e-4


BEHAVIOUR_CHANGE = "behaviour change: "


def read(src, name=None, upgrade_legacy=False):
    """Skeleton JSON (path, or an already-parsed dict) -> IR dict.

    upgrade_legacy reads pre-4.2 key names that 4.2 ignores as their 4.2
    meaning (see the module docstring); the default ignores them, as the
    runtime does."""
    if isinstance(src, (str, os.PathLike)):
        with open(src) as f:
            data = json.load(f)
        name = name or os.path.splitext(os.path.basename(str(src)))[0]
    else:
        data = src
    return _Reader(data, name or "rig", upgrade_legacy).run()


def behaviour_changes(ir):
    """The reader's warnings that mean the IR plays differently from the file
    (only with upgrade_legacy)."""
    return [w for w in (ir.get("source") or {}).get("warnings", []) if w.startswith(BEHAVIOUR_CHANGE)]


def _hex_color(s, n):
    s = (s or "").strip()
    vals = [int(s[i:i + 2], 16) / 255.0 for i in range(0, 2 * n, 2)] if len(s) >= 2 * n else None
    return vals


def _enum(v):
    return v[0].lower() + v[1:] if v else v


class _Reader:
    def __init__(self, data, name, upgrade_legacy=False):
        self.d = data
        self.ir = irmod.new_rig(name)
        self.warnings = []
        self.upgrade = upgrade_legacy

    def legacy(self, where, old, new, ignored_means):
        """Whether to read the pre-4.2 key `old` as `new`; warns either way."""
        if self.upgrade:
            self.warnings.append(f"{BEHAVIOUR_CHANGE}{where}: pre-4.2 '{old}' read as '{new}' "
                                 f"(spine-core 4.2 ignores it: {ignored_means})")
            return True
        self.warnings.append(f"{where}: pre-4.2 '{old}' ignored, as spine-core 4.2 ignores it ({ignored_means})")
        return False

    def run(self):
        d, ir = self.d, self.ir
        sk = d.get("skeleton", {})
        version = sk.get("spine")
        major_minor = ".".join(str(version).split(".")[:2]) if version else None
        if "constraints" in d:
            raise ValueError("4.3 'constraints[]' layout: use a 4.3 reader")
        if major_minor and major_minor not in ("4.0", "4.1", "4.2"):
            self.warnings.append(f"declared spine {version}; read with 4.2 rules")
        if major_minor in ("4.0", "4.1") and d.get("physics"):
            self.warnings.append(f"declares {version} but has top-level physics (a 4.2 feature)")
        ir["source"] = {"format": "spine-json", "spine": version, "warnings": self.warnings}
        ir["skeleton"] = {
            "hash": sk.get("hash"), "x": float(sk.get("x", 0)), "y": float(sk.get("y", 0)),
            "width": float(sk.get("width", 0)), "height": float(sk.get("height", 0)),
            "reference_scale": float(sk.get("referenceScale", 100)), "fps": sk.get("fps"),
            "images": sk.get("images"), "audio": sk.get("audio"),
        }
        self.bones()
        self.slots()
        self.constraints()
        self.bind = bind_pose(ir)
        self.skins()
        self.events()
        self.animations()
        return ir

    # ---- skeleton structure

    def bones(self):
        for b in self.d.get("bones", []):
            inherit = b.get("inherit", "normal")
            if "transform" in b:
                if "inherit" in b:
                    self.warnings.append(f"bone {b['name']}: pre-4.2 'transform' ignored ('inherit' is set)")
                elif self.legacy(f"bone {b['name']}", "transform", "inherit", "the bone inherits normally"):
                    inherit = b["transform"]
            inherit = _enum(inherit)
            if inherit not in irmod.INHERIT_MODES:
                raise ValueError(f"bone {b['name']}: unknown inherit {inherit!r}")
            self.ir["bones"].append(irmod.new_bone(
                b["name"], b.get("parent"), b.get("x", 0), b.get("y", 0), b.get("rotation", 0), b.get("length", 0),
                scale_x=float(b.get("scaleX", 1)), scale_y=float(b.get("scaleY", 1)),
                shear_x=float(b.get("shearX", 0)), shear_y=float(b.get("shearY", 0)), inherit=inherit,
                skin_required=bool(b.get("skin", False)), color=b.get("color"), icon=b.get("icon"),
                visible=b.get("visible")))

    def slots(self):
        for s in self.d.get("slots", []):
            self.ir["slots"].append(irmod.new_slot(
                s["name"], s["bone"], s.get("attachment"), color=s.get("color", "ffffffff"),
                dark=s.get("dark"), blend=s.get("blend", "normal"), visible=s.get("visible")))

    def constraints(self):
        d, ir = self.d, self.ir
        for c in d.get("ik", []):
            ir["ik"].append({
                "name": c["name"], "order": int(c.get("order", 0)), "skin_required": bool(c.get("skin", False)),
                "bones": list(c["bones"]), "target": c["target"], "mix": float(c.get("mix", 1)),
                "softness": float(c.get("softness", 0)), "bend_positive": bool(c.get("bendPositive", True)),
                "compress": bool(c.get("compress", False)), "stretch": bool(c.get("stretch", False)),
                "uniform": bool(c.get("uniform", False))})
        for c in d.get("transform", []):
            mix_x, mix_sx = float(c.get("mixX", 1)), float(c.get("mixScaleX", 1))
            ir["transform"].append({
                "name": c["name"], "order": int(c.get("order", 0)), "skin_required": bool(c.get("skin", False)),
                "kind": "transform", "bones": list(c["bones"]), "target": c["target"],
                "local": bool(c.get("local", False)), "relative": bool(c.get("relative", False)),
                "offset": {"rotation": float(c.get("rotation", 0)), "x": float(c.get("x", 0)),
                           "y": float(c.get("y", 0)), "scale_x": float(c.get("scaleX", 0)),
                           "scale_y": float(c.get("scaleY", 0)), "shear_y": float(c.get("shearY", 0))},
                "mix": {"rotate": float(c.get("mixRotate", 1)), "x": mix_x, "y": float(c.get("mixY", mix_x)),
                        "scale_x": mix_sx, "scale_y": float(c.get("mixScaleY", mix_sx)),
                        "shear_y": float(c.get("mixShearY", 1))}})
        for c in d.get("path", []):
            mix_x = float(c.get("mixX", 1))
            ir["path"].append({
                "name": c["name"], "order": int(c.get("order", 0)), "skin_required": bool(c.get("skin", False)),
                "bones": list(c["bones"]), "target": c["target"],
                "position_mode": _enum(c.get("positionMode", "percent")),
                "spacing_mode": _enum(c.get("spacingMode", "length")),
                "rotate_mode": _enum(c.get("rotateMode", "tangent")),
                "offset_rotation": float(c.get("rotation", 0)), "position": float(c.get("position", 0)),
                "spacing": float(c.get("spacing", 0)),
                "mix": {"rotate": float(c.get("mixRotate", 1)), "x": mix_x, "y": float(c.get("mixY", mix_x))}})
        for c in d.get("physics", []):
            ir["physics"].append({
                "name": c["name"], "order": int(c.get("order", 0)), "skin_required": bool(c.get("skin", False)),
                "bone": c["bone"], "x": float(c.get("x", 0)), "y": float(c.get("y", 0)),
                "rotate": float(c.get("rotate", 0)), "scale_x": float(c.get("scaleX", 0)),
                "shear_x": float(c.get("shearX", 0)), "limit": float(c.get("limit", 5000)),
                "fps": float(c.get("fps", 60)), "inertia": float(c.get("inertia", 1)),
                "strength": float(c.get("strength", 100)), "damping": float(c.get("damping", 1)),
                "mass": float(c.get("mass", 1)), "wind": float(c.get("wind", 0)),
                "gravity": float(c.get("gravity", 0)), "mix": float(c.get("mix", 1)),
                "global": {k: bool(c.get(k + "Global", False))
                           for k in ("inertia", "strength", "damping", "mass", "wind", "gravity", "mix")},
                "spring": None})
        dead = {id(c) for _, c in irmod.dead_by_order_42(ir)}
        everything = irmod.constraints(ir)
        for kind, c in everything:
            c["runs"] = id(c) not in dead
            if not c["runs"]:
                self.warnings.append(f"{kind} {c['name']}: order {c['order']} never runs in 4.2 (one constraint "
                                     f"runs per order 0..{len(everything) - 1}; this one is past the end of that "
                                     "sequence or repeats an order)")

    # ---- skins and attachments

    def skins(self):
        raw = self.d.get("skins", [])
        if isinstance(raw, dict):  # pre-4.0: {"default": {slot: {...}}}; 4.2 loops over a list only
            upgrade = self.legacy("skeleton", "skins {name: attachments}", "skins [{name, attachments}]",
                                  "the skeleton has no skins and no slot shows an attachment")
            raw = [{"name": k, "attachments": v} for k, v in raw.items()] if upgrade else []
        slot_bone = {s["name"]: s["bone"] for s in self.ir["slots"]}
        for s in raw:
            skin = {"name": s["name"], "bones": list(s.get("bones", [])), "ik": list(s.get("ik", [])),
                    "transform": list(s.get("transform", [])), "path": list(s.get("path", [])),
                    "physics": list(s.get("physics", [])), "color": s.get("color"), "attachments": {}}
            for slot, atts in s.get("attachments", {}).items():
                out = skin["attachments"][slot] = {}
                for key, a in atts.items():
                    out[key] = self.attachment(key, a, slot_bone[slot])
            self.ir["skins"].append(skin)

    def attachment(self, key, a, slot_bone):
        typ = a.get("type", "region")
        name = a.get("name", key)
        seq = a.get("sequence")
        seq = None if seq is None else {"count": int(seq["count"]), "start": int(seq.get("start", 1)),
                                         "digits": int(seq.get("digits", 0)), "setup": int(seq.get("setup", 0))}
        if typ == "region":
            return {"type": "region", "name": name, "path": a.get("path", name), "x": float(a.get("x", 0)),
                    "y": float(a.get("y", 0)), "rotation": float(a.get("rotation", 0)),
                    "scale_x": float(a.get("scaleX", 1)), "scale_y": float(a.get("scaleY", 1)),
                    "width": _opt_float(a.get("width")), "height": _opt_float(a.get("height")),
                    "color": a.get("color", "ffffffff"), "sequence": seq}
        if typ == "mesh":
            uvs = a["uvs"]
            n = len(uvs) // 2
            verts, weights = self.vertices(a["vertices"], n, slot_bone)
            tris = a["triangles"]
            return {"type": "mesh", "name": name, "path": a.get("path", name), "color": a.get("color", "ffffffff"),
                    "width": _opt_float(a.get("width")), "height": _opt_float(a.get("height")),
                    "uvs": [[float(uvs[2 * i]), float(uvs[2 * i + 1])] for i in range(n)],
                    "triangles": [[int(tris[i]), int(tris[i + 1]), int(tris[i + 2])] for i in range(0, len(tris), 3)],
                    "hull": int(a.get("hull", 0)), "edges": self.edges(a.get("edges"), name),
                    "vertices": verts, "weights": weights, "sequence": seq}
        if typ == "linkedmesh":
            timelines = a.get("timelines", True)
            if "deform" in a and "timelines" not in a and self.legacy(
                    f"linked mesh {name}", "deform", "timelines", "it follows its parent's deform keys"):
                timelines = a["deform"]
            return {"type": "linkedmesh", "name": name, "path": a.get("path", name),
                    "color": a.get("color", "ffffffff"), "width": _opt_float(a.get("width")),
                    "height": _opt_float(a.get("height")), "parent": a["parent"], "skin": a.get("skin"),
                    "timelines": bool(timelines), "sequence": seq}
        if typ in ("boundingbox", "clipping", "path"):
            verts, weights = self.vertices(a["vertices"], int(a["vertexCount"]), slot_bone)
            out = {"type": typ, "name": name, "color": a.get("color"), "vertices": verts, "weights": weights}
            if typ == "clipping":
                out["end"] = a.get("end")
            if typ == "path":
                out.update(closed=bool(a.get("closed", False)), constant_speed=bool(a.get("constantSpeed", True)),
                           lengths=[float(v) for v in a.get("lengths", [])])
            return out
        if typ == "point":
            return {"type": "point", "name": name, "x": float(a.get("x", 0)), "y": float(a.get("y", 0)),
                    "rotation": float(a.get("rotation", 0)), "color": a.get("color")}
        raise ValueError(f"attachment {name!r}: unknown type {typ!r}")

    def vertices(self, values, count, slot_bone):
        """File vertices -> (setup world [[x, y]], weights or None)."""
        bones = self.ir["bones"]
        if len(values) == count * 2:
            m = self.bind[slot_bone]
            return [list(to_world(m, values[2 * i], values[2 * i + 1])) for i in range(count)], None
        verts, weights, i = [], [], 0
        for _ in range(count):
            k = int(values[i])
            i += 1
            infl, wx, wy = [], 0.0, 0.0
            for _ in range(k):
                b, lx, ly, w = bones[int(values[i])]["name"], values[i + 1], values[i + 2], float(values[i + 3])
                i += 4
                qx, qy = to_world(self.bind[b], lx, ly)
                infl.append((b, w, qx, qy))
                wx += qx * w
                wy += qy * w
            # all or nothing: re-binding one influence to the blended position
            # would move the blend, and the vertex would drift on every pass
            agree = all(abs(qx - wx) <= SAME_PX and abs(qy - wy) <= SAME_PX for _, _, qx, qy in infl)
            row = [[b, w] if agree else [b, w, [qx, qy]] for b, w, qx, qy in infl]
            verts.append([wx, wy])
            weights.append(row)
        if i != len(values):
            raise ValueError("weighted vertex list does not match the vertex count")
        return verts, weights

    def edges(self, raw, name):
        if raw is None:
            return None
        if any(int(v) % 2 for v in raw):
            self.warnings.append(f"mesh {name}: odd edge index, edges kept as vertex indices")
            return [[int(raw[i]), int(raw[i + 1])] for i in range(0, len(raw) - 1, 2)]
        # Spine writes edge endpoints as vertex index * 2
        return [[int(raw[i]) // 2, int(raw[i + 1]) // 2] for i in range(0, len(raw) - 1, 2)]

    # ---- events

    def events(self):
        for name, e in self.d.get("events", {}).items():
            self.ir["events"].append({"name": name, "int": int(e.get("int", 0)), "float": float(e.get("float", 0)),
                                      "string": e.get("string", ""), "audio": e.get("audio"),
                                      "volume": float(e.get("volume", 1)), "balance": float(e.get("balance", 0))})

    # ---- animations

    def animations(self):
        for name, a in self.d.get("animations", {}).items():
            self.ir["animations"].append({"name": name, "timelines": self.timelines(name, a)})

    def timelines(self, anim, a):
        out = []
        for bone, tls in a.get("bones", {}).items():
            for prop, keys in tls.items():
                out.append(self.bone_timeline(bone, prop, keys))
        for slot, tls in a.get("slots", {}).items():
            for prop, keys in tls.items():
                out.append(self.slot_timeline(slot, prop, keys))
        for name, keys in a.get("ik", {}).items():
            out.append(_tl("ik", name, "ik", [dict(
                time=_t(k), mix=float(k.get("mix", 1)), softness=float(k.get("softness", 0)),
                bend_positive=bool(k.get("bendPositive", True)), compress=bool(k.get("compress", False)),
                stretch=bool(k.get("stretch", False)), curve=_curve(k)) for k in keys]))
        for name, keys in a.get("transform", {}).items():
            tk = []
            for k in keys:
                mx, msx = float(k.get("mixX", 1)), float(k.get("mixScaleX", 1))
                tk.append(dict(time=_t(k), mix_rotate=float(k.get("mixRotate", 1)), mix_x=mx,
                               mix_y=float(k.get("mixY", mx)), mix_scale_x=msx,
                               mix_scale_y=float(k.get("mixScaleY", msx)), mix_shear_y=float(k.get("mixShearY", 1)),
                               curve=_curve(k)))
            out.append(_tl("transform", name, "transform", tk))
        for name, tls in a.get("path", {}).items():
            for prop, keys in tls.items():
                if prop == "mix":
                    tk = []
                    for k in keys:
                        mx = float(k.get("mixX", 1))
                        tk.append(dict(time=_t(k), mix_rotate=float(k.get("mixRotate", 1)), mix_x=mx,
                                       mix_y=float(k.get("mixY", mx)), curve=_curve(k)))
                    out.append(_tl("path", name, "mix", tk))
                else:
                    out.append(_tl("path", name, prop, [dict(time=_t(k), value=float(k.get("value", 0)),
                                                              curve=_curve(k)) for k in keys]))
        for name, tls in a.get("physics", {}).items():
            for prop, keys in tls.items():
                if prop == "reset":
                    out.append(_tl("physics", name, "reset", [{"time": _t(k)} for k in keys]))
                else:
                    out.append(_tl("physics", name, prop, [dict(time=_t(k), value=float(k.get("value", 0)),
                                                                 curve=_curve(k)) for k in keys]))
        atts = a.get("attachments")
        if "deform" in a:  # the old home of deform keys
            if atts is not None:
                self.warnings.append(f"animation {anim}: pre-4.2 'deform' ignored ('attachments' is set)")
            elif self.legacy(f"animation {anim}", "deform", "attachments/*/deform", "none of its deform keys play"):
                atts = {skin: {slot: {att: {"deform": keys} for att, keys in m.items()} for slot, m in sm.items()}
                        for skin, sm in a["deform"].items()}
        for skin, sm in (atts or {}).items():
            for slot, am in sm.items():
                for att, tls in am.items():
                    target = {"skin": skin, "slot": slot, "attachment": att}
                    for prop, keys in tls.items():
                        if prop == "deform":
                            out.append(_tl("deform", target, "deform", self.deform_keys(skin, slot, att, keys)))
                        elif prop == "sequence":
                            out.append(_tl("sequence", target, "sequence", [dict(
                                time=_t(k), mode=_enum(k.get("mode", "hold")), index=int(k.get("index", 0)),
                                delay=float(k.get("delay", 0))) for k in keys]))
        dro = a.get("drawOrder")
        if "draworder" in a:
            if dro is not None:
                self.warnings.append(f"animation {anim}: pre-4.2 'draworder' ignored ('drawOrder' is set)")
            elif self.legacy(f"animation {anim}", "draworder", "drawOrder", "the draw order never changes"):
                dro = a["draworder"]
        if dro is not None:
            out.append(_tl("draw_order", None, "draw_order",
                           [{"time": _t(k), "order": self.draw_order(k.get("offsets"))} for k in dro]))
        if "events" in a:
            ev = irmod.by_name(self.ir["events"])
            keys = []
            for k in a["events"]:
                e = ev[k["name"]]
                keys.append({"time": _t(k), "name": k["name"], "int": int(k.get("int", e["int"])),
                             "float": float(k.get("float", e["float"])), "string": k.get("string", e["string"]),
                             "volume": float(k.get("volume", e["volume"])),
                             "balance": float(k.get("balance", e["balance"]))})
            out.append(_tl("event", None, "event", keys))
        return out

    def bone_timeline(self, bone, prop, keys):
        if prop == "inherit":
            return _tl("bone", bone, prop, [{"time": _t(k), "inherit": _enum(k.get("inherit", "normal"))}
                                           for k in keys])
        fields = irmod.CHANNELS.get(("bone", prop))
        if fields is None:
            raise ValueError(f"bone {bone}: unknown timeline {prop!r}")
        default = 1.0 if prop.startswith("scale") else 0.0
        return _tl("bone", bone, prop, [dict(time=_t(k), **{f: float(k.get(f, default)) for f in fields},
                                             curve=_curve(k)) for k in keys])

    def slot_timeline(self, slot, prop, keys):
        if prop == "attachment":
            return _tl("slot", slot, prop, [{"time": _t(k), "name": k.get("name")} for k in keys])
        out = []
        for k in keys:
            key = {"time": _t(k)}
            if prop == "alpha":
                key["value"] = float(k.get("value", 0))
            elif prop in ("rgba", "rgb"):
                n = 4 if prop == "rgba" else 3
                key.update(zip("rgba"[:n], _hex_color(k["color"], n)))
            elif prop in ("rgba2", "rgb2"):
                n = 4 if prop == "rgba2" else 3
                key.update(zip("rgba"[:n], _hex_color(k["light"], n)))
                key.update(zip(("r2", "g2", "b2"), _hex_color(k["dark"], 3)))
            else:
                raise ValueError(f"slot {slot}: unknown timeline {prop!r}")
            key["curve"] = _curve(k)
            out.append(key)
        return _tl("slot", slot, prop, out)

    def deform_keys(self, skin, slot, att_name, keys):
        att = irmod.resolve_attachment(self.ir, skin, slot, att_name)
        if att is None:
            raise ValueError(f"deform for missing attachment {skin}/{slot}/{att_name}")
        geo = irmod.geometry_of(self.ir, slot, att)
        w = geo.get("weights")
        size = 2 * (sum(len(r) for r in w) if w is not None else len(geo["vertices"]))
        out = []
        for k in keys:
            v = k.get("vertices")
            offsets = None
            if v is not None:
                offsets = [0.0] * size
                start = int(k.get("offset", 0))
                for j, x in enumerate(v):
                    offsets[start + j] = float(x)
            out.append({"time": _t(k), "offsets": offsets, "curve": _curve(k)})
        return out

    def draw_order(self, offsets):
        """Spine's sparse draw-order offsets -> the full slot order (names)."""
        if offsets is None:
            return None
        names = [s["name"] for s in self.ir["slots"]]
        index = {n: i for i, n in enumerate(names)}
        n = len(names)
        order = [-1] * n
        unchanged = []
        original = 0
        for e in offsets:
            si = index[e["slot"]]
            while original != si:
                unchanged.append(original)
                original += 1
            order[original + int(e["offset"])] = original
            original += 1
        unchanged += list(range(original, n))
        for i in range(n - 1, -1, -1):
            if order[i] == -1:
                order[i] = unchanged.pop()
        return [names[i] for i in order]


def _tl(typ, target, prop, keys):
    return {"type": typ, "target": target, "property": prop, "keys": keys}


def _t(k):
    return float(k.get("time", 0))


def _curve(k):
    c = k.get("curve")
    if c is None or c == "stepped":
        return c
    return [float(v) for v in c]


def _opt_float(v):
    return None if v is None else float(v)
