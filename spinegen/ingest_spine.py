"""Unpack an existing Spine character into layers + joints, the rig
builder's input (SOTA §3.2 layers.json, joints.json).

    python -m spinegen.ingest_spine <skeleton.json | rig.json> <.atlas | images dir> -o <layers dir>
        [--template biped_mascot] [--skin name] [--margin 8]

WHY
  The rig builder (spinegen.rigbuild) starts from what an artist hands
  over: flat layer images placed on a canvas, plus where the joints are.
  A character that already exists as a Spine skeleton (the monkey, a cut-out
  made by another tool) has that information baked into bones, attachments
  and an atlas. Unpacking it into the same form as a PSD export lets one
  builder serve both, and lets a rebuilt rig be compared with the original
  pixel for pixel: the layers recomposite to exactly the original setup
  render (recomposite(); the test pins the difference to <= 1/255).

WHAT IT WRITES (layers dir)
  layers.json  spinegen-layers/1
    {"schema", "canvas": [W, H], "world": {"origin": [wx, wy], "y": "down"},
     "source": {"kind", "file", "atlas", "sha256", "rig"},
     "layers": [{"name", "file", "x", "y", "w", "h", "z", "role", "tags",
                 "slot", "attachment", "color", "blend",
                 "src": {"bone", "type", "influences", "integer_placement"}}]}
    canvas = image space: x right, y DOWN, pixel (i, j) covers [j, j+1) x
    [i, i+1). world.origin is the world (y up) position of the canvas
    corner (0, 0): canvas (cx, cy) <-> world (wx + cx, wy - cy). Only the
    rig builder converts back (rigbuild.Canvas), in one place.
    Each layer PNG is its attachment drawn ALONE at the SETUP pose on that
    canvas (spinegen.raster, the renderer the art gates trust), cropped to
    its pixels, untinted: the slot x attachment colour is kept in "color"
    so it is applied once, by the rebuilt slot. A pure integer translation
    of the source image (every monkey part) is copied texel for texel;
    anything else (rotated, scaled, a deformed mesh) is resampled once,
    and "src.integer_placement" says which.
    Attachments a slot does not show at setup (a prop that appears in one
    animation) become layers tagged "hidden", drawn where they would be at
    setup.
  joints.json  spinegen-joints/1
    {"schema", "template", "H", "joints": {name: {"xy", "conf", "src"}},
     "bones": {template bone: source bone},
     "accessories": [{"name", "parent", "xy", "dir", "len", "src"}],
     "notes": [...]}
    Joint positions are the setup origins of the source bones mapped onto
    template roles (spinegen.template.match_bones) in canvas coordinates;
    a joint with no source bone of its own sits at the tip of the bone
    that should end there, when that bone has a length. conf is 1.0: these
    are measured, not estimated. Source bones that match no role
    (cuffs, props, a banana) are accessories, kept with their own setup
    transform under their mapped parent.
  layers/<name>.png, source/rig.json + motion.json (the source as IR, for
  the builder's physics and animation carry-over).
"""
import argparse
import hashlib
import json
import math
import os

import numpy as np

from . import ir as irmod
from . import lbs, raster
from . import template as tmpl

SCHEMA_LAYERS = "spinegen-layers/1"
SCHEMA_JOINTS = "spinegen-joints/1"
MARGIN = 8  # px of empty canvas around the character


def unpack(skeleton, images, outdir=None, template="biped_mascot", skin=None, margin=MARGIN):
    """Unpack a rig (IR dict or file path) with its images (dict or .atlas /
    directory path). Returns {"layers": layers.json dict, "joints":
    joints.json dict, "images": {layer name: RGBA}, "canvas": raster.Canvas,
    "paths": {...} when `outdir` is given}."""
    src_path = skeleton if isinstance(skeleton, (str, os.PathLike)) else None
    atlas_path = images if isinstance(images, (str, os.PathLike)) else None
    ir = raster.load_rig(skeleton) if src_path else skeleton
    imgs = raster.load_images(images) if atlas_path else images
    t = tmpl.load(template) if isinstance(template, str) else template
    r = raster.Renderer(_untinted(ir), imgs, skin=skin)
    ev0 = lbs.Evaluator(ir, None, skin)  # the tinted original, for each layer's colour
    world = r.ev.world()

    plan = _layer_plan(r)
    canvas = _fit_canvas(r, world, plan, margin)
    layers, layer_imgs = [], {}
    names = _layer_names(plan)
    mapping, notes, exclude = _match(ir, t)
    rename = _renames(ir, mapping)
    for z, (slot, key, att, hidden) in enumerate(plan):
        fr = r.frame(world=world, slots=[slot], attachments={slot: key}, canvas=canvas)
        rgba = raster.premult_to_rgba(fr.image)
        box = _alpha_box(rgba[..., 3])
        if box is None:
            notes.append(f"layer {names[z]}: draws no pixels at setup; skipped")
            continue
        x0, y0, x1, y1 = box
        name = names[z]
        src_slot = ir["slots"][r.slot_index[slot]]
        layer_imgs[name] = rgba[y0:y1, x0:x1].copy()
        layers.append({
            "name": name, "file": f"layers/{name}.png", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "z": z,
            "role": rename.get(src_slot["bone"], src_slot["bone"]),
            "tags": ["mesh"] + (["hidden"] if hidden else []),
            "slot": slot, "attachment": key, "color": _tint_hex(src_slot, ev0.attachment(slot, key)), "blend": src_slot.get("blend") or "normal",
            "src": {"bone": src_slot["bone"], "type": att["type"],
                    "influences": sorted({rename.get(b, b) for b in _influences(att, src_slot)}),
                    "integer_placement": _integer_placement(r, world, slot, att)},
        })
    H = _character_height(layers)
    out_layers = {"schema": SCHEMA_LAYERS, "canvas": [canvas.W, canvas.H],
                  "world": {"origin": [float(canvas.origin[0]), float(canvas.origin[1])], "y": "down"},
                  "source": {"kind": "spine" if src_path else "ir",
                             "file": os.path.abspath(src_path) if src_path else None,
                             "atlas": os.path.abspath(atlas_path) if atlas_path else None,
                             "sha256": _sha256(src_path) if src_path else None,
                             "rig": "source/rig.json", "skin": skin},
                  "layers": layers}
    out_joints = _joints(ir, world, r.ev.index, canvas, t, mapping, rename, exclude, H)
    out_joints["notes"] = notes
    res = {"layers": out_layers, "joints": out_joints, "images": layer_imgs, "canvas": canvas, "ir": ir}
    if outdir is not None:
        res["paths"] = write(outdir, res)
    return res


def write(outdir, res):
    from PIL import Image
    os.makedirs(os.path.join(outdir, "layers"), exist_ok=True)
    for name, img in res["images"].items():
        Image.fromarray(img, "RGBA").save(os.path.join(outdir, "layers", f"{name}.png"))
    lp, jp = os.path.join(outdir, "layers.json"), os.path.join(outdir, "joints.json")
    _write_json(lp, res["layers"])
    _write_json(jp, res["joints"])
    os.makedirs(os.path.join(outdir, "source"), exist_ok=True)
    rig_p = os.path.join(outdir, "source", "rig.json")
    irmod.save(res["ir"], rig_p, os.path.join(outdir, "source", "motion.json"))
    return {"layers": lp, "joints": jp, "source": rig_p}


# ---------------------------------------------------------------- reading a layers dir back

def load_layers(layers_dir):
    """(layers.json dict, {layer name: RGBA uint8})."""
    from PIL import Image
    with open(os.path.join(layers_dir, "layers.json")) as f:
        doc = json.load(f)
    if doc.get("schema") != SCHEMA_LAYERS:
        raise ValueError(f"{layers_dir}/layers.json: not {SCHEMA_LAYERS}")
    imgs = {}
    for L in doc["layers"]:
        imgs[L["name"]] = np.asarray(Image.open(os.path.join(layers_dir, L["file"])).convert("RGBA"))
        if imgs[L["name"]].shape[:2] != (L["h"], L["w"]):
            raise ValueError(f"layer {L['name']}: PNG is {imgs[L['name']].shape[1]}x{imgs[L['name']].shape[0]}, "
                             f"layers.json says {L['w']}x{L['h']}")
    return doc, imgs


def recomposite(doc, imgs, include_hidden=False):
    """The layers drawn back onto the canvas in z order, the way
    spinegen.raster composites (premultiplied "over", additive slots add
    colour), tinted by each layer's colour. Returns (H, W, 4) uint8."""
    W, H = doc["canvas"]
    acc = np.zeros((H, W, 4), np.float32)
    for L in sorted(doc["layers"], key=lambda L: L["z"]):
        if "hidden" in L["tags"] and not include_hidden:
            continue
        a = imgs[L["name"]].astype(np.float32) / 255.0
        a[..., :3] *= a[..., 3:4]
        a *= raster._hex_rgba(L.get("color"))
        y0, x0 = L["y"], L["x"]
        dst = acc[y0:y0 + L["h"], x0:x0 + L["w"]]
        if L.get("blend") == "additive":
            dst[..., :3] += a[..., :3]
        else:
            dst[...] = a + dst * (1 - a[..., 3:4])
    np.clip(acc, 0.0, 1.0, out=acc)
    return raster.premult_to_rgba(acc)


def setup_render(ir, images, doc, skin=None):
    """raster.render of `ir`'s setup pose on the layers' canvas."""
    W, H = doc["canvas"]
    return raster.render(ir, images, size=(W, H), origin=doc["world"]["origin"], skin=skin)


# ---------------------------------------------------------------- layers

def _untinted(ir):
    """A shallow copy whose slots and attachments are white, so a layer
    image is the bare art (its tint is written to the layer instead)."""
    c = dict(ir)
    c["slots"] = [dict(s, color="ffffffff") for s in ir["slots"]]
    c["skins"] = []
    for sk in ir["skins"]:
        atts = {slot: {k: (dict(a, color="ffffffff") if "color" in a else a) for k, a in m.items()}
                for slot, m in sk["attachments"].items()}
        c["skins"].append(dict(sk, attachments=atts))
    return c


def _layer_plan(r):
    """[(slot, attachment key, attachment, hidden)] in draw order: each
    slot's setup attachment first, then the others its skin holds."""
    plan = []
    shown = dict(r.ev.shown())
    skin = r.ev._active_skin()
    for s in r.ir["slots"]:
        keys = []
        if s["attachment"] is not None and shown.get(s["name"]) is not None:
            keys.append((s["attachment"], False))
        for sk in [irmod.default_skin(r.ir)] + ([skin] if skin is not None else []):
            for key in (sk or {}).get("attachments", {}).get(s["name"], {}):
                if key not in [k for k, _ in keys]:
                    keys.append((key, True))
        for key, hidden in keys:
            att = r.ev.attachment(s["name"], key)
            if att is not None and att["type"] in ("region", "mesh", "linkedmesh"):
                plan.append((s["name"], key, att, hidden))
    return plan


def _layer_names(plan):
    """Attachment name when it is unique, else slot.attachment."""
    counts = {}
    for _, key, _, _ in plan:
        counts[key] = counts.get(key, 0) + 1
    return [key if counts[key] == 1 else f"{slot}.{key}" for slot, key, _, _ in plan]


def _fit_canvas(r, world, plan, margin):
    pts = []
    for slot, key, _, _ in plan:
        g = r.geometry(world, attachments={slot: key}, slots=[slot])
        pts += [gg.xy for gg in g.values()]
    allp = np.concatenate(pts) if pts else np.zeros((1, 2))
    lo, hi = allp.min(0), allp.max(0)
    ox, oy = math.floor(lo[0]) - margin, math.ceil(hi[1]) + margin
    W, H = math.ceil(hi[0]) + margin - ox, oy - (math.floor(lo[1]) - margin)
    return raster.Canvas((float(ox), float(oy)), (float(W), float(H)), 1.0)


def _alpha_box(alpha):
    m = alpha > 0
    if not m.any():
        return None
    rows, cols = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _tint_hex(slot, att):
    c = raster._hex_rgba(slot.get("color")) * raster._hex_rgba(att.get("color"))
    return "".join(f"{int(round(v * 255)):02x}" for v in c)


def _influences(att, slot):
    if att["type"] == "mesh" and att.get("weights"):
        return {inf[0] for row in att["weights"] for inf in row}
    return {slot["bone"]}


def _integer_placement(r, world, slot, att, tol=1e-3):
    """True when the attachment shows its image unrotated, unscaled, at an
    integer canvas offset: then the layer PNG is the source texels."""
    g = r.geometry(world, attachments={slot: att["name"]}, slots=[slot])
    if slot not in g:
        return False
    gg = g[slot]
    tex = r.texture(gg.tex_key)
    if tex is None:
        return False
    uv = np.asarray(gg.uv, float) * [tex.w, tex.h]
    xy = np.asarray(gg.xy, float)
    want = np.stack([xy[0, 0] - uv[0, 0] + uv[:, 0], xy[0, 1] + uv[0, 1] - uv[:, 1]], 1)
    corner = (xy[0, 0] - uv[0, 0], xy[0, 1] + uv[0, 1])
    return bool(np.abs(want - xy).max() < tol and all(abs(c - round(c)) < tol for c in corner))


def _character_height(layers):
    shown = [L for L in layers if "hidden" not in L["tags"]]
    if not shown:
        return 0
    return int(max(L["y"] + L["h"] for L in shown) - min(L["y"] for L in shown))


# ---------------------------------------------------------------- joints

def _match(ir, t):
    """Source bone -> template role, leaving out the source's own
    half-angle / transform helpers (the builder makes its own)."""
    exclude = set()
    for c in ir.get("transform", []):
        exclude |= set([c["bone"]] if c.get("kind") == "half_angle" else c.get("bones", []))
    mapping, notes = tmpl.match_bones(ir["bones"], t, exclude)
    if exclude:
        notes.append(f"source constraint-driven bones not mapped (the builder makes its own helpers): "
                     f"{sorted(exclude)}")
    return mapping, notes, exclude


def _renames(ir, mapping):
    """Source bone -> name in the rebuilt rig (template name or itself)."""
    inv = {src: role for role, src in mapping.items()}
    return {b["name"]: inv.get(b["name"], b["name"]) for b in ir["bones"]}


def _joints(ir, world, index, canvas, t, mapping, rename, exclude, H):
    def cxy(wx, wy):
        return [round(float(wx - canvas.origin[0]), 4), round(float(canvas.origin[1] - wy), 4)]

    bones = irmod.by_name(ir["bones"])
    joints = {}
    for spec in t["bones"]:
        src = mapping.get(spec["name"])
        if src is None or spec["joint"] is None or spec["joint"] in joints:
            continue
        m = world[index[src]]
        joints[spec["joint"]] = {"xy": cxy(m[4], m[5]), "conf": 1.0, "src": f"setup_bone:{src}"}
    # a joint without its own bone: the tip of the bone that ends there
    for spec in t["bones"]:
        src = mapping.get(spec["name"])
        if src is None or bones[src]["length"] <= 0:
            continue
        tails = [j for j in spec["tails"] if j in t["joints"]]
        if not tails or any(j in joints for j in tails):
            continue
        a, _, c, _, x, y = world[index[src]]
        L = bones[src]["length"]
        joints[tails[0]] = {"xy": cxy(x + a * L, y + c * L), "conf": 1.0, "src": f"bone_tip:{src}"}
    acc = []
    mapped = set(mapping.values())
    for b in ir["bones"]:
        if b["name"] in mapped or b["name"] in exclude:
            continue
        a, _, c, _, x, y = world[index[b["name"]]]
        n = math.hypot(a, c) or 1.0
        parent = rename.get(b["parent"]) if b["parent"] is not None else None
        acc.append({"name": b["name"], "parent": parent, "xy": cxy(x, y),
                    "dir": [round(a / n, 6), round(-c / n, 6)], "len": round(float(b["length"] * n), 4),
                    "src": f"setup_bone:{b['name']}"})
    return {"schema": SCHEMA_JOINTS, "template": t["name"], "H": H, "joints": joints,
            "bones": {role: src for role, src in mapping.items()}, "accessories": acc}


# ---------------------------------------------------------------- misc

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skeleton")
    ap.add_argument("images", help=".atlas or a directory of PNGs")
    ap.add_argument("-o", "--out", required=True, help="layers dir to write")
    ap.add_argument("--template", default="biped_mascot")
    ap.add_argument("--skin", default=None)
    ap.add_argument("--margin", type=int, default=MARGIN)
    a = ap.parse_args(argv)
    res = unpack(a.skeleton, a.images, a.out, a.template, a.skin, a.margin)
    doc, imgs = load_layers(a.out)
    src_imgs = raster.load_images(a.images)
    diff = np.abs(recomposite(doc, imgs).astype(int) - setup_render(res["ir"], src_imgs, doc, a.skin).astype(int))
    W, H = doc["canvas"]
    print(f"{a.out}: {len(doc['layers'])} layers on {W}x{H}, {len(res['joints']['joints'])} joints, "
          f"{len(res['joints']['accessories'])} accessories; recomposite vs setup render max diff {int(diff.max())}")
    for n in res["joints"]["notes"]:
        print("  note:", n)


if __name__ == "__main__":
    main()
