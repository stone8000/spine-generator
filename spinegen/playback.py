"""Sample an IR animation at time t, as spine-core plays it alone on track 0.

WHY
  oracle_check compares numpy LBS with the official runtime on animated
  poses, not just the setup pose; that needs the local pose the runtime has
  at time t. With one animation on track 0 the runtime applies every
  timeline at alpha 1 with MixBlend.first, which reduces to:

    before a timeline's first key   the setup value
    rotate / translate / shear      setup + keyed value
    scale                           setup * keyed value
    attachment, inherit, booleans   the key at or before t (stepped)
    deform                          offsets blended by the curve's percent,
                                    only while the slot shows the timeline's
                                    attachment (a linked mesh with
                                    `timelines` uses its parent's)
    ik / transform mixes            the keyed values

  Curves are evaluated by spinegen.curves (the runtime's polyline). Key
  times are rounded to float32 first, because the runtime keeps frames in a
  Float32Array: a key at 0.2 really sits at 0.20000000298, so at t = 0.2 the
  runtime is still before it. Physics is not simulated: compare against
  `dump --physics none`. Colours, draw order and events do not move
  vertices and are not sampled.
"""
import struct

from . import ir as irmod
from .curves import evaluate

_ADD = {"rotate": ("rotation",), "translate": ("x", "y"), "translatex": ("x",), "translatey": ("y",),
        "shear": ("shear_x", "shear_y"), "shearx": ("shear_x",), "sheary": ("shear_y",)}
_MUL = {"scale": ("scale_x", "scale_y"), "scalex": ("scale_x",), "scaley": ("scale_y",)}


def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def _runtime_keys(keys):
    """Keys with the times the runtime actually stores (float32)."""
    return [dict(k, time=_f32(k["time"])) for k in keys]


def _value(keys, field, t, index):
    return evaluate(keys, field, t, field_index=index)


def _at_or_before(keys, t):
    k = keys[0]
    for key in keys:
        if key["time"] <= t:
            k = key
        else:
            break
    return k


def _percent(keys, t):
    """(key index, 0..1 blend toward the next key) for a percent-curve timeline."""
    i = max(j for j, k in enumerate(keys) if k["time"] <= t)
    if i == len(keys) - 1:
        return i, 0.0
    a, b = keys[i], keys[i + 1]
    seg = [{"time": a["time"], "p": 0.0, "curve": a.get("curve")}, {"time": b["time"], "p": 1.0}]
    return i, evaluate(seg, "p", t)


def find_animation(ir, name):
    for a in ir.get("animations", []):
        if a["name"] == name:
            return a
    raise KeyError(f"no animation {name!r}")


def sample(ir, anim, t, loop=False):
    """Pose of `anim` (name or IR animation) at time t.

    Returns {"bones", "attachments", "deform", "state"} — the keyword
    arguments of lbs.Evaluator.frame (pose=bones).
    """
    if isinstance(anim, str):
        anim = find_animation(ir, anim)
    dur = irmod.duration(anim)
    t = (t % dur if dur > 0 else 0.0) if loop else min(t, dur)
    setup = irmod.by_name(ir["bones"])
    bones = {}
    attachments, deform, state = {}, {}, {}
    deform_tls = []
    for tl in anim["timelines"]:
        typ, prop, tgt = tl["type"], tl["property"], tl["target"]
        if not tl["keys"] or typ in ("physics", "draw_order", "event", "sequence"):
            continue
        keys = _runtime_keys(tl["keys"])
        if typ == "deform":
            deform_tls.append((tgt, keys))
            continue
        if t < keys[0]["time"]:
            continue  # alpha 1, MixBlend.first: the setup value
        if typ == "bone":
            pose = bones.setdefault(tgt, {})
            b = setup[tgt]
            if prop == "inherit":
                pose["inherit"] = _at_or_before(keys, t)["inherit"]
            elif prop in _ADD:
                for i, (f, ch) in enumerate(zip(_ADD[prop], irmod.CHANNELS[("bone", prop)])):
                    pose[f] = b[f] + _value(keys, ch, t, i)
            elif prop in _MUL:
                for i, (f, ch) in enumerate(zip(_MUL[prop], irmod.CHANNELS[("bone", prop)])):
                    pose[f] = b[f] * _value(keys, ch, t, i)
        elif typ == "slot" and prop == "attachment":
            attachments[tgt] = _at_or_before(keys, t)["name"]
        elif typ == "ik":
            k = _at_or_before(keys, t)
            state[tgt] = {"mix": _value(keys, "mix", t, 0), "softness": _value(keys, "softness", t, 1),
                          "bend_positive": k["bend_positive"], "compress": k["compress"], "stretch": k["stretch"]}
        elif typ == "transform":
            fields = irmod.CHANNELS[("transform", "transform")]
            vals = {f: _value(keys, f, t, i) for i, f in enumerate(fields)}
            state[tgt] = {"mix": {"rotate": vals["mix_rotate"], "x": vals["mix_x"], "y": vals["mix_y"],
                                  "scale_x": vals["mix_scale_x"], "scale_y": vals["mix_scale_y"],
                                  "shear_y": vals["mix_shear_y"]}}
        elif typ == "path":
            st = state.setdefault(tgt, {})
            if prop == "mix":
                st["mix"] = {"rotate": _value(keys, "mix_rotate", t, 0), "x": _value(keys, "mix_x", t, 1),
                             "y": _value(keys, "mix_y", t, 2)}
            else:
                st[prop] = _value(keys, "value", t, 0)
    shown = {s["name"]: attachments.get(s["name"], s["attachment"]) for s in ir["slots"]}
    for tgt, keys in deform_tls:
        if t < keys[0]["time"] or shown.get(tgt["slot"]) is None:
            continue
        mine = irmod.resolve_attachment(ir, tgt["skin"], tgt["slot"], tgt["attachment"])
        cur = irmod.resolve_attachment(ir, None, tgt["slot"], shown[tgt["slot"]])
        if cur is None or _timeline_attachment(ir, tgt["slot"], cur) is not mine:
            continue
        i, p = _percent(keys, t)
        a = keys[i]["offsets"]
        b = keys[i + 1]["offsets"] if i + 1 < len(keys) else None
        n = len(a or b or [])
        if n == 0:
            continue
        a, b = a or [0.0] * n, b or [0.0] * n
        deform[tgt["slot"]] = a if i + 1 == len(keys) else [x + (y - x) * p for x, y in zip(a, b)]
    return {"bones": bones, "attachments": attachments, "deform": deform, "state": state}


def _timeline_attachment(ir, slot, att):
    """The attachment whose deform keys drive `att` (a linked mesh with
    `timelines` borrows its parent's)."""
    if att["type"] == "linkedmesh" and att["timelines"]:
        return irmod.resolve_attachment(ir, att["skin"] or "default", slot, att["parent"])
    return att
