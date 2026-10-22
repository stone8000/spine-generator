"""Emit the IR as Spine 4.3 skeleton JSON.

    python -m spinegen.emit.spine43 build/rig.json out/name.json [--spine 4.3.13]

WHY A SECOND EMITTER (Spine 4.3 is the primary target; 4.2 stays for old games)
  4.3 reads constraints only from one `constraints[]` list, runs them in that
  list's order (there is no `order` key), and spells several things
  differently. A 4.2 file on a 4.3 player loses every constraint without a
  word, or throws when an animation names one ([E5], qa43/load.mjs). This
  module is the only place the 4.3 format exists; everything upstream works
  on the IR.

SAME PREPARATION AS 4.2
  spine42.prepare() normalises the IR first (mesh outlines first, a
  constraint that never ran written inert, constraints numbered 0..N-1 in
  evaluation order, physics springs expanded), so both emitters write the
  same rig and the same vertex order, and a 4.2 and a 4.3 file of one IR
  can be compared vertex for vertex (spinegen.equiv43). `constraints[]` is
  that evaluation order: the 4.2 order rules ("contiguous": one constraint
  per order 0..N-1) are resolved before 4.3 sees the list, so a constraint
  4.2 never ran stays a no-op instead of waking up in 4.3.

WHAT 4.3 SPELLS DIFFERENTLY (SkeletonJson 4.3.13)
  constraints  one list, each entry has `type` ik | transform | path | physics.
  ik           `scaleY: "uniform" | "none"` replaces 4.2's `uniform` bool.
  transform    `source` (4.2 `target`), `localSource` + `localTarget`
               (4.2 `local`), `additive` (4.2 `relative`), `clamp`, and a
               `properties` map {from: {to: {to-property: {offset, scale}}}}.
               A 4.2 constraint is the identity map rotate->rotate, x->x,
               y->y, scaleX->scaleX, scaleY->scaleY, shearY->shearY; all six
               are written with all six mixes, because in 4.3 a mix for a
               property that is not in `properties` does nothing (a 4.2 mix
               timeline keying mixX on a constraint written without `x`
               would be lost).
  half_angle   the form verified exact in 4.3.13 (motionkit/scripts/trial/
               probe-half.ts, research/trials/README.md):
                 source <lower>, localSource, localTarget, mixRotate 1,
                 properties.rotate.to.rotate {scale: mix, offset: (1-mix)*R}
               with R the helper's setup rotation. That is exactly what the
               IR means (the 4.2 expansion's absolute local blend
               r + mix*(r_lower - r) with r = R): the helper turns by `mix` of
               the lower bone's change from setup. When something keys the
               helper's rotation or the constraint's mix, r is not R, and the
               direct form (scale 1, mixRotate = mix) is written instead, which
               is the blend itself.
  path         `slot` (4.2 `target`).
  physics      the same fields, but different DEFAULTS (inertia 0.5 vs 1,
               damping 0.85 vs 1): every field is written; where the IR is
               silent the 4.3 default is filled in and report["defaults"]
               says which.
  linkedmesh   `source` (4.2 `parent`).
  animations   the same layout as 4.2; timelines name constraints, not
               indices, so they need no change.

WHERE 4.2 AND 4.3 PLAY THE SAME FILE DIFFERENTLY (report["warnings"]; the
bones that can differ, with everything downstream of them, in report["differs"])
  Physics: 4.3 interpolates physics between its fixed steps (xLag,
  rotateLag, scaleLag); 4.2 shows the last step. Same parameters, different
  follow-through at every frame rate whose frames do not land on physics
  steps; spinegen.equiv43 measures it. Transform constraints, three 4.2.120
  quirks 4.3 does not reproduce, each flagged per constraint:
    * absolute local scale: 4.2 divides the blend by the bone's own scale
      (lbs.py keeps it); identical only while that bone's scale is 1;
    * world rotate offset outside (-180, 180]: the runtimes wrap the angle
      differently, so the result can be one turn apart (x mix);
    * world shearY offset: 4.2 adds it after wrapping and mirrors it with the
      target, 4.3 before and unmirrored;
    * world relative shearY: 4.2 wraps the target's shear angle (y axis
      minus x axis) into (-180, 180] before blending, 4.3 does not; the two
      part whenever the target's x axis points into (90, 180] deg.
  Order: a constraint that writes a bone's world transform (world transform
  constraint, path, physics) followed by one that moves an ancestor of that
  bone: 4.2 re-poses the bone from its applied local transform, 4.3
  differently; equal only on normal, uniform, unsheared chains. spinegen's
  rigs order IK first, half-angle helpers (local) next, physics last on
  bones nothing later moves, so they have none of these.
"""
import argparse

from .. import ir as irmod
from . import spine42
from .spine42 import _n

DEFAULT_VERSION = "4.3.13"

# SkeletonJson 4.3.13 physics defaults (4.2.120: inertia 1, damping 1).
PHYSICS_DEFAULTS = {"x": 0.0, "y": 0.0, "rotate": 0.0, "scale_x": 0.0, "shear_x": 0.0, "limit": 5000.0,
                    "fps": 60.0, "inertia": 0.5, "strength": 100.0, "damping": 0.85, "mass": 1.0, "wind": 0.0,
                    "gravity": 0.0, "mix": 1.0}
GLOBAL_KEYS = ("inertia", "strength", "damping", "mass", "wind", "gravity", "mix")

# 4.2 mix name (IR) -> 4.3 property name (from and to are the same for a 4.2 constraint)
TC_PROPS = (("rotate", "rotate"), ("x", "x"), ("y", "y"), ("scale_x", "scaleX"), ("scale_y", "scaleY"),
            ("shear_y", "shearY"))


def emit(ir, version=DEFAULT_VERSION, report=None):
    """IR -> Spine 4.3 JSON dict. `report`, if a dict, receives what changed
    (spine42's keys plus "defaults", "half_angle")."""
    errs = irmod.validate(ir)
    if errs:
        raise ValueError("invalid IR:\n  " + "\n  ".join(errs[:20]))
    return _Emitter43(prepare(ir, report), version).run()


def prepare(ir, report=None):
    """spine42.prepare plus the 4.3 parts: physics defaults filled in (4.3's,
    recorded), half_angle constraints remembered so they can be written in
    the 4.3 form, and the 4.2-only transform behaviours flagged."""
    report = report if report is not None else {}
    report.setdefault("defaults", [])
    report.setdefault("half_angle", {})
    src = irmod.clone(ir)
    for c in src["physics"]:
        filled = [k for k in PHYSICS_DEFAULTS if c.get(k) is None]
        for k in filled:
            c[k] = PHYSICS_DEFAULTS[k]
        g = c.get("global") or {}
        c["global"] = {k: bool(g.get(k, False)) for k in GLOBAL_KEYS}
        c.setdefault("spring", None)
        if filled:
            report["defaults"].append({"physics": c["name"], "filled": filled})
    keyed = _keyed(src)
    halves = {}
    for c in src["transform"]:
        if c.get("kind") != "half_angle":
            continue
        setup = next(b for b in src["bones"] if b["name"] == c["bone"])["rotation"]
        direct = (("bone", c["bone"], "rotate") in keyed) or (("transform", c["name"], "transform") in keyed)
        halves[c["name"]] = {"setup_rotation": float(setup), "form": "direct" if direct else "scale"}
    report["half_angle"] = halves
    out = spine42.prepare(src, report)
    found = []
    for c in out["transform"]:
        if c["name"] in halves:
            c["_half"] = halves[c["name"]]
        else:
            found += _quirks(out, c, keyed)
    found += _order_quirks(out)
    report["warnings"] += [w for w, _ in found]
    report["differs"] = sorted(_downstream(out, {b for _, bones in found for b in bones}))
    return out


def _keyed(ir):
    """{(type, target, property)} of every timeline in every animation."""
    out = set()
    for anim in ir.get("animations", []):
        for tl in anim["timelines"]:
            t = tl["target"]
            if isinstance(t, dict):
                continue
            out.add((tl["type"], t, tl["property"]))
    return out


def _moved(kind, c):
    """The bones a constraint writes."""
    if kind == "physics":
        return [c["bone"]]
    return list(c["bones"]) if "bones" in c else [c["bone"]]


def _order_quirks(ir):
    """A constraint that writes a bone's WORLD transform (world transform
    constraint, path, physics), followed by a constraint that moves one of
    that bone's ancestors: 4.2 keeps the world change as the bone's applied
    local transform and re-poses it under the moved ancestor, 4.3 does not
    do it the same way. Exact in both only when the chain inherits normally
    with uniform, unsheared scale (measured: up to 1.7 in matrix entries
    otherwise); the other order (ancestor first) always agrees.
    Returns [(warning, bones)]."""
    parent = {b["name"]: b["parent"] for b in ir["bones"]}

    def ancestors(name):
        out, p = set(), parent.get(name)
        while p is not None:
            out.add(p)
            p = parent.get(p)
        return out

    order = irmod.constraints(ir)
    found = []
    for i, (kind, c) in enumerate(order):
        world = kind in ("path", "physics") or (kind == "transform" and not c.get("local") and c.get("kind") != "half_angle")
        if not world:
            continue
        for b in _moved(kind, c):
            up = ancestors(b)
            later = [f"{k2} {c2['name']}" for k2, c2 in order[i + 1:] if up & set(_moved(k2, c2))]
            if later:
                found.append((f"{kind} {c['name']} writes {b}'s world transform before {', '.join(later)} moves an "
                              f"ancestor of it: 4.2 and 4.3 re-pose {b} differently unless its chain inherits normally "
                              "with uniform, unsheared scale (spinegen.equiv43 measures it)", [b]))
    return found


def _downstream(ir, bones):
    """`bones`, every bone below them, and every bone a constraint drives from
    one of them (its target or source), to a fixpoint: what can play
    differently once `bones` do."""
    children = {}
    for b in ir["bones"]:
        children.setdefault(b["parent"], []).append(b["name"])
    slot_bone = {s["name"]: s["bone"] for s in ir["slots"]}
    out, todo = set(), list(bones)
    while todo:
        b = todo.pop()
        if b in out:
            continue
        out.add(b)
        todo += children.get(b, [])
        for kind, c in irmod.constraints(ir):
            src = c.get("target")
            if kind == "path":
                src = slot_bone.get(src)
            if src == b:
                todo += _moved(kind, c)
    return out


def _quirks(ir, c, keyed):
    """The 4.2.120 transform behaviours 4.3 does not reproduce, for one
    constraint: [(warning, bones it writes)]."""
    out, m, o = [], c["mix"], c["offset"]
    bones = {b["name"]: b for b in ir["bones"]}
    name = f"transform {c['name']}"
    if c["local"] and not c["relative"] and (m["scale_x"] or m["scale_y"]
                                                or ("transform", c["name"], "transform") in keyed):
        scaled = [b for b in c["bones"] if bones[b]["scale_x"] != 1 or bones[b]["scale_y"] != 1
                  or any(("bone", b, p) in keyed for p in ("scale", "scalex", "scaley"))]
        if scaled:
            out.append((f"{name}: absolute local scale on {', '.join(scaled)} (scale != 1): 4.2.120 divides the "
                        "blend by the bone's own scale, 4.3 does not; the bone scales differently", scaled))
    if not c["local"] and m["rotate"] and not -180 < o["rotation"] <= 180:
        out.append((f"{name}: world rotate offset {o['rotation']} outside (-180, 180]: 4.2 and 4.3 wrap the angle "
                    "differently and can end a turn apart (times the mix)", list(c["bones"])))
    if not c["local"] and not c["relative"] and m["shear_y"] and o["shear_y"]:
        out.append((f"{name}: world shearY offset {o['shear_y']}: 4.2 adds it after wrapping and mirrors it with "
                    "a mirrored target, 4.3 before and unmirrored", list(c["bones"])))
    if not c["local"] and c["relative"] and (m["shear_y"] or ("transform", c["name"], "transform") in keyed):
        out.append((f"{name}: world relative shearY: 4.2 wraps the target's shear angle before blending, 4.3 does "
                    "not, so while the target's x axis points into (90, 180] deg the bone shears mix x 360 deg "
                    "differently", list(c["bones"])))
    return out


def write(ir, path, version=DEFAULT_VERSION, report=None):
    skel = emit(ir, version, report)
    with open(path, "w") as f:
        f.write(spine42.dumps(skel))
    return skel


class _Emitter43(spine42._Emitter):
    """spine42's serialisation with 4.3's constraint list and spellings."""

    def run(self):
        ir = self.ir
        out = {"skeleton": self.skeleton()}
        out["bones"] = [self.bone(b) for b in ir["bones"]]
        out["slots"] = [self.slot(s) for s in ir["slots"]]
        fns = {"ik": self.ik, "transform": self.transform, "path": self.path, "physics": self.physics}
        cons = sorted(((c["order"], kind, c) for kind in fns for c in ir[kind]), key=lambda e: e[0])
        if cons:
            out["constraints"] = [fns[kind](c, None) for _, kind, c in cons]
        out["skins"] = [self.skin(s) for s in ir["skins"]]
        if ir["events"]:
            out["events"] = {e["name"]: self.event(e) for e in ir["events"]}
        if ir.get("animations"):
            out["animations"] = {a["name"]: self.animation(a) for a in ir["animations"]}
        return out

    def _head(self, c, kind):
        out = {"name": c["name"], "type": kind}
        if c.get("skin_required"):
            out["skin"] = True
        return out

    def ik(self, c, order):
        out = self._head(c, "ik")
        out.update(bones=list(c["bones"]), target=c["target"], mix=_n(c["mix"]), softness=_n(c["softness"]),
                   bendPositive=bool(c["bend_positive"]), compress=bool(c["compress"]),
                   stretch=bool(c["stretch"]), scaleY="uniform" if c["uniform"] else "none")
        return out

    def transform(self, c, order):
        out = self._head(c, "transform")
        o, m = c["offset"], c["mix"]
        out.update(bones=list(c["bones"]), source=c["target"], localSource=bool(c["local"]),
                   localTarget=bool(c["local"]), additive=bool(c["relative"]), clamp=False)
        half = c.get("_half")  # "direct" falls through: it is the generic form below
        if half and half["form"] == "scale":
            # mixRotate 1 and the blend in the property; an inert one (mix 0) stays off
            mix = m["rotate"]
            out.update(rotation=0, x=0, y=0, scaleX=0, scaleY=0, shearY=0,
                       properties={"rotate": {"offset": 0, "to": {"rotate": {
                           "offset": _n((1 - mix) * half["setup_rotation"]), "scale": _n(mix)}}}},
                       mixRotate=1 if mix else 0, mixX=0, mixY=0, mixScaleX=0, mixScaleY=0, mixShearY=0)
            return out
        out.update(rotation=_n(o["rotation"]), x=_n(o["x"]), y=_n(o["y"]), scaleX=_n(o["scale_x"]),
                   scaleY=_n(o["scale_y"]), shearY=_n(o["shear_y"]))
        out["properties"] = {p: {"offset": 0, "to": {p: {"offset": 0, "scale": 1}}} for _, p in TC_PROPS}
        out.update(mixRotate=_n(m["rotate"]), mixX=_n(m["x"]), mixY=_n(m["y"]), mixScaleX=_n(m["scale_x"]),
                   mixScaleY=_n(m["scale_y"]), mixShearY=_n(m["shear_y"]))
        return out

    def path(self, c, order):
        m = c["mix"]
        out = self._head(c, "path")
        out.update(bones=list(c["bones"]), slot=c["target"], positionMode=c["position_mode"],
                   spacingMode=c["spacing_mode"], rotateMode=c["rotate_mode"], rotation=_n(c["offset_rotation"]),
                   position=_n(c["position"]), spacing=_n(c["spacing"]), mixRotate=_n(m["rotate"]),
                   mixX=_n(m["x"]), mixY=_n(m["y"]))
        return out

    def physics(self, c, order):
        out = self._head(c, "physics")
        out.update(bone=c["bone"], x=_n(c["x"]), y=_n(c["y"]), rotate=_n(c["rotate"]), scaleX=_n(c["scale_x"]),
                   scaleY="none", shearX=_n(c["shear_x"]), limit=_n(c["limit"]), fps=_n(c["fps"]),
                   inertia=_n(c["inertia"]), strength=_n(c["strength"]), damping=_n(c["damping"]),
                   mass=_n(c["mass"]), wind=_n(c["wind"]), gravity=_n(c["gravity"]), mix=_n(c["mix"]))
        for k, v in c["global"].items():
            if v:
                out[k + "Global"] = True
        return out

    def attachment(self, slot, key, a):
        out = super().attachment(slot, key, a)
        if a["type"] == "linkedmesh":
            out["source"] = out.pop("parent")
        return out


def half_angle_check(skel):
    """[(constraint, scale, offset)] of half-angle-form transforms in a 4.3 dict (for tests and reports)."""
    out = []
    for c in skel.get("constraints", []):
        if c.get("type") != "transform":
            continue
        props = c.get("properties", {})
        if list(props) == ["rotate"] and list(props["rotate"]["to"]) == ["rotate"]:
            t = props["rotate"]["to"]["rotate"]
            out.append((c["name"], t.get("scale", 1), t.get("offset", 0)))
    return out


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
    for d in report["defaults"]:
        print(f"physics {d['physics']}: 4.3 defaults for {', '.join(d['filled'])}")
    print(f"wrote {args.out}: {len(report['hull'])} meshes re-hulled, {len(report['half_angle'])} half-angle helpers")


if __name__ == "__main__":
    main()
