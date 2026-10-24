"""A folder of full-canvas layer PNGs + joints (the motion kit's image
template) -> layers.json + joints.json, the rig builder's input.

    python -m spinegen.ingest_layers <layer dir> -o <layers dir>
        [--joints kit_joints.json | --guess | --defaults] [--facing 1|-1] [--name boxer]

WHY
  The motion kit's studio rigs a character from AI-generated layers: one
  canvas, fixed file names (motionkit/src/rigTemplate.ts PARTS), 19 joints
  the user drags onto the art (JOINTS), facing right, a boxing guard in
  profile. Its autorig makes every layer a rigid full-canvas region on a
  bone. spinegen.rigbuild needs the same information as a layers dir
  (spinegen.ingest_spine's layers.json / joints.json) and then builds
  meshes, joint bands, helpers and planted leg IK from it, with no Spine
  source at all. This module is that bridge for the kit's layer folders.

JOINTS (one of)
  --joints FILE  the kit's Joints object, {JointId: {"x", "y"}} in canvas
                 px (y down), as the studio holds it after the user dragged
                 the red dots; {"joints": {...}} is accepted too.
  --guess        the kit's own guess (motionkit/src/autorig.ts guessJoints,
                 run by node on the decoded alpha: motionkit/scripts/
                 guess-joints.ts), what the studio shows before any drag.
  --defaults     the template's default joints scaled to the canvas
                 (rigTemplate.ts defaultJoints; KIT_JOINTS mirrors its table,
                 tests/test_sideview.py checks the mirror against the .ts).
  default: --joints <layer dir>/joints.json when present, else --defaults.

WHAT IT WRITES (spinegen.ingest_spine's formats)
  layers.json  each PART present becomes a layer cropped to its pixels,
               z = the template's draw order (PARTS: first = furthest
               back), role = the template bone it follows:
                 torso -> torso, pelvis -> hips, neck -> neck,
                 head / hair_back -> head, hair_front* -> accessory hairF,
                 arm_<k>_upper / _lower, fist_<k> -> upperarm / forearm /
                 hand of side S(k), leg_<k>_upper / _lower -> thigh / shin
                 of S(k) (the kit's shin layer carries the foot: the
                 builder's foot bone takes it below the ankle).
               S(front) is the NEAR side, the one turned to the viewer:
               a character facing right (+x, facing 1) shows its right
               side, so front = "r", back = "l"; facing -1 swaps them.
               world.origin puts the kit's ground point (ROOT_PX scaled to
               the canvas) at world (0, 0): world = the kit's Spine units.
  joints.json  template joints (spinegen-joints/1): root at the ground
               point, pelvis = hip, waist, neck = neckBase, head =
               headBase, shoulder/elbow/wrist/hip/knee/ankle per side from
               F/B; the hair accessory from hairRoot -> hairTip. Plus
               "view": "side", "facing", "near" / "far" (the sides): the
               rig builder reads them (rigbuild LEG STYLES "side").
  No source rig: no physics, no animations (the kit's presets make them,
  spinegen.bridge_motionkit).
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np

from . import ingest_spine

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(REPO, "motionkit")
SCHEMA_LAYERS = ingest_spine.SCHEMA_LAYERS
SCHEMA_JOINTS = ingest_spine.SCHEMA_JOINTS
ALPHA_MIN = 8  # an alpha above this is art (crop box, height)

# motionkit/src/rigTemplate.ts: CANVAS, ROOT_PX and JOINTS in Spine units
# (x right, y up from the ground point) before P()'s rounding
KIT_CANVAS = (1024, 1536)
KIT_ROOT = (512, 1456)
KIT_JOINTS = {
    "hip": (0, 660), "waist": (0, 792), "neckBase": (0, 1056), "headBase": (0, 1122), "headTop": (34, 1317),
    "shoulderF": (55, 1034), "elbowF": (132, 901), "wristF": (242, 993),
    "shoulderB": (-44, 1023), "elbowB": (33, 890), "wristB": (125, 999),
    "hipF": (33, 660), "kneeF": (79, 333), "ankleF": (56, 40),
    "hipB": (-33, 660), "kneeB": (-79, 333), "ankleB": (-79, 40),
    "hairRoot": (-10, 1291), "hairTip": (-135, 1143),
}
# draw order (first = furthest back) and the kit bone of each part (rigTemplate.ts PARTS)
KIT_PARTS = [("hair_back", "head"), ("arm_back_upper", "upperArmB"), ("arm_back_lower", "foreArmB"),
             ("fist_back", "handB"), ("leg_back_upper", "thighB"), ("leg_back_lower", "shinB"), ("pelvis", "hip"),
             ("leg_front_upper", "thighF"), ("leg_front_lower", "shinF"), ("torso", "chest"), ("neck", "neck"),
             ("head", "head"), ("hair_front", "hairF"), ("arm_front_upper", "upperArmF"),
             ("arm_front_lower", "foreArmF"), ("fist_front", "handF")]
# kit joint -> template joint ({s}: the side, F = near, B = far)
JOINT_MAP = {"hip": "pelvis", "waist": "waist", "neckBase": "neck", "headBase": "head",
             "shoulder{K}": "shoulder_{s}", "elbow{K}": "elbow_{s}", "wrist{K}": "wrist_{s}",
             "hip{K}": "hip_{s}", "knee{K}": "knee_{s}", "ankle{K}": "ankle_{s}"}
# kit bone -> template bone
BONE_MAP = {"hip": "hips", "chest": "torso", "neck": "neck", "head": "head",
            "upperArm{K}": "upperarm_{s}", "foreArm{K}": "forearm_{s}", "hand{K}": "hand_{s}",
            "thigh{K}": "thigh_{s}", "shin{K}": "shin_{s}"}
HAIR = "hairF"


def sides(facing):
    """{"F": near side, "B": far side}: facing right shows the right side."""
    if facing not in (1, -1):
        raise ValueError(f"facing must be 1 (right) or -1 (left), not {facing!r}")
    return {"F": "r", "B": "l"} if facing == 1 else {"F": "l", "B": "r"}


def kit_root(canvas):
    W, H = canvas
    return KIT_ROOT[0] / KIT_CANVAS[0] * W, KIT_ROOT[1] / KIT_CANVAS[1] * H


def default_joints(canvas=KIT_CANVAS):
    """rigTemplate.ts defaultJoints: the template joints scaled to `canvas`
    (canvas px, y down; P() rounds on the 1024 x 1536 canvas first)."""
    sx, sy = canvas[0] / KIT_CANVAS[0], canvas[1] / KIT_CANVAS[1]
    out = {}
    for k, (x, y) in KIT_JOINTS.items():
        # Math.round(ROOT + x), Math.round(ROOT - y): JS rounds .5 up
        px, py = np.floor(KIT_ROOT[0] + x + 0.5), np.floor(KIT_ROOT[1] - y + 0.5)
        out[k] = {"x": float(px * sx), "y": float(py * sy)}
    return out


def load_pngs(layer_dir):
    """{part name: RGBA uint8}: every PNG in the folder (one canvas size)."""
    from PIL import Image
    imgs = {}
    for f in sorted(os.listdir(layer_dir)):
        if f.lower().endswith(".png"):
            imgs[f[:-4]] = np.asarray(Image.open(os.path.join(layer_dir, f)).convert("RGBA"))
    if not imgs:
        raise ValueError(f"{layer_dir}: no PNG layers")
    shapes = {im.shape[:2] for im in imgs.values()}
    if len(shapes) != 1:
        raise ValueError(f"{layer_dir}: layers differ in size {sorted(shapes)} (the kit draws every layer on one canvas)")
    return imgs


def guess_joints(imgs):
    """The kit's autorig guess (guessJoints) for these layers, via node."""
    h, w = next(iter(imgs.values())).shape[:2]
    with tempfile.TemporaryDirectory() as d:
        man = {"width": w, "height": h, "layers": {}}
        for n, im in imgs.items():
            np.ascontiguousarray(im[..., 3]).tofile(os.path.join(d, f"{n}.alpha"))
            man["layers"][n] = f"{n}.alpha"
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(man, f)
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("node is not installed: --guess runs the kit's guessJoints")
        out = subprocess.run([node, os.path.join(KIT, "scripts", "guess-joints.ts"), os.path.join(d, "manifest.json")],
                             cwd=KIT, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["joints"]


def _part_of(name):
    """(kit part, kit bone) of a layer file name, or None."""
    if name.startswith("hair_front"):
        return "hair_front", HAIR
    return next(((p, b) for p, b in KIT_PARTS if p == name), None)


def _fill(pattern, side_key, side):
    return pattern.replace("{K}", side_key).replace("{s}", side)


def _template_bone(kit_bone, sd):
    for k, v in BONE_MAP.items():
        for K, s in (sd.items() if "{K}" in k else [("", "")]):
            if _fill(k, K, s) == kit_bone:
                return _fill(v, K, s)
    return None


def unpack(layer_dir, kit_joints=None, facing=1, name=None, imgs=None):
    """Returns {"layers": layers.json dict, "joints": joints.json dict,
    "images": {layer name: RGBA cropped}}. `kit_joints` {JointId: {x, y}}
    (canvas px, y down); None = the template defaults."""
    imgs = imgs if imgs is not None else load_pngs(layer_dir)
    H_img, W_img = next(iter(imgs.values())).shape[:2]
    canvas = (W_img, H_img)
    sd = sides(facing)
    kj = dict(default_joints(canvas))
    given = set()
    if kit_joints:
        kj.update({k: {"x": float(v["x"]), "y": float(v["y"])} for k, v in kit_joints.items()})
        given = set(kit_joints)
    rx, ry = kit_root(canvas)
    layers, images, notes = [], {}, []
    order = [p for p, _ in KIT_PARTS]
    top = bottom = None
    for n in sorted(imgs, key=lambda n: (order.index(_part_of(n)[0]) if _part_of(n) else len(order), n)):
        part = _part_of(n)
        if part is None:
            notes.append(f"layer {n}: not a part of the kit's template; left out")
            continue
        img = imgs[n]
        a = img[..., 3] > ALPHA_MIN
        if not a.any():
            notes.append(f"layer {n}: empty; left out")
            continue
        ii, jj = np.nonzero(a)
        y0, y1, x0, x1 = int(ii.min()), int(ii.max()) + 1, int(jj.min()), int(jj.max()) + 1
        top = y0 if top is None else min(top, y0)
        bottom = y1 if bottom is None else max(bottom, y1)
        role = HAIR if part[1] == HAIR else _template_bone(part[1], sd)
        images[n] = np.ascontiguousarray(img[y0:y1, x0:x1])
        layers.append({"name": n, "file": f"layers/{n}.png", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                       "z": len(layers), "role": role, "tags": [], "slot": n, "attachment": n, "color": None,
                       "blend": "normal",
                       "src": {"bone": part[1], "type": "region", "influences": [], "integer_placement": True}})
    if not layers:
        raise ValueError(f"{layer_dir}: none of the kit's layers")
    doc = {"schema": SCHEMA_LAYERS, "canvas": [W_img, H_img], "world": {"origin": [-rx, ry], "y": "down"},
           "name": name or os.path.basename(os.path.normpath(layer_dir)),
           "source": {"kind": "motionkit-layers", "dir": os.path.abspath(layer_dir), "rig": None,
                      "joints": "given" if given else "defaults"},
           "layers": layers}
    joints = {"root": {"xy": [rx, ry], "conf": 1.0, "src": "motionkit:ROOT_PX"}}
    for k, v in JOINT_MAP.items():
        for K, s in (sd.items() if "{K}" in k else [("", "")]):
            kid = _fill(k, K, s)
            if kid in kj:
                joints[_fill(v, K, s)] = {"xy": [kj[kid]["x"], kj[kid]["y"]], "conf": 1.0 if kid in given else 0.5,
                                          "src": f"motionkit:{kid}"}
    bones = {}
    for k, v in BONE_MAP.items():
        for K, s in (sd.items() if "{K}" in k else [("", "")]):
            bones[_fill(v, K, s)] = _fill(k, K, s)
    accessories = []
    if any(L["role"] == HAIR for L in layers):
        r, t = (np.array([kj[k]["x"], kj[k]["y"]]) for k in ("hairRoot", "hairTip"))
        d = t - r
        ln = float(np.linalg.norm(d)) or 1.0
        accessories.append({"name": HAIR, "parent": "head", "xy": r.tolist(), "dir": (d / ln).tolist(), "len": ln,
                            "src": "motionkit:hairRoot->hairTip"})
    jdoc = {"schema": SCHEMA_JOINTS, "template": "biped_mascot", "H": float(bottom - top), "joints": joints,
            "bones": bones, "accessories": accessories, "view": "side", "facing": int(facing),
            "near": sd["F"], "far": sd["B"], "notes": notes}
    return {"layers": doc, "joints": jdoc, "images": images}


def write(outdir, res):
    from PIL import Image
    os.makedirs(os.path.join(outdir, "layers"), exist_ok=True)
    for n, img in res["images"].items():
        Image.fromarray(img, "RGBA").save(os.path.join(outdir, "layers", f"{n}.png"))
    for key in ("layers", "joints"):
        with open(os.path.join(outdir, f"{key}.json"), "w") as f:
            json.dump(res[key], f, indent=1)
            f.write("\n")
    return {"layers": os.path.join(outdir, "layers.json"), "joints": os.path.join(outdir, "joints.json")}


def mirror(imgs, kit_joints=None):
    """The same character facing the other way: every layer flipped
    left-right and the joints with it (canvas px). For tests and for art
    drawn facing left."""
    W = next(iter(imgs.values())).shape[1]
    kj = kit_joints or default_joints((W, next(iter(imgs.values())).shape[0]))
    return ({n: np.ascontiguousarray(im[:, ::-1]) for n, im in imgs.items()},
            {k: {"x": W - v["x"], "y": v["y"]} for k, v in kj.items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("layer_dir")
    ap.add_argument("-o", "--out", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--joints", default=None, help="the kit's Joints JSON {JointId: {x, y}} (canvas px)")
    g.add_argument("--guess", action="store_true", help="the kit's autorig guess (node)")
    g.add_argument("--defaults", action="store_true", help="the template's default joints")
    ap.add_argument("--facing", type=int, default=1, choices=(1, -1))
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    imgs = load_pngs(a.layer_dir)
    kj = None
    if a.guess:
        kj = guess_joints(imgs)
    elif not a.defaults:
        p = a.joints or os.path.join(a.layer_dir, "joints.json")
        if a.joints or os.path.exists(p):
            with open(p) as f:
                kj = json.load(f)
            kj = kj.get("joints", kj)
    res = unpack(a.layer_dir, kj, a.facing, a.name, imgs)
    paths = write(a.out, res)
    j = res["joints"]
    print(f"{paths['layers']}: {len(res['layers']['layers'])} layers, H {j['H']:g} px, facing {j['facing']} "
          f"(near {j['near']}, far {j['far']}), joints {'given' if kj else 'defaults'}")
    for n in j["notes"]:
        print("  note:", n)


if __name__ == "__main__":
    main()
