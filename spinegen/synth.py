"""Synthetic rigs generated in code: test fixtures that are ours to publish.

    python -m spinegen.synth OUTDIR [--rig arm|features|order_gap|order_dup] [--weights rigid|ramp]

WHY
  The repo is public and third-party skeletons must stay out of it (SOTA §6),
  yet every converter and gate needs a skeleton to chew on. These are built
  from a few numbers, so tests regenerate them instead of committing files,
  and qa/ can be pointed at the same output.

  arm       a two-bone arm (upper, fore, hand) with one weighted strip mesh
            spanning the elbow: `rigid` weights bind each vertex 100% to the
            nearer bone (folds when the elbow closes), `ramp` blends them
            across the joint.
  features  the arm plus one of everything the IR and the 4.2 emitter carry:
            every inherit mode under a sheared, non-uniformly scaled and a
            reflected parent; 1- and 2-bone IK (softness, stretch, child
            offset); the four transform-constraint variants; path and
            physics constraints; region (whitespace-stripped), unweighted
            mesh, linked mesh, point, bounding box, clipping and path
            attachments; a second skin; events; and animations using every
            4.2 timeline type, plus single-key "pose_*" animations that put
            the constraints to work for the oracle.
            It also carries the places where the runtime's arithmetic is
            not the clean one (lbs "FAITHFUL, NOT EXACT"): a local absolute
            transform constraint with scaled bones, every mix non-zero and
            the target more than 180 deg away; a world transform constraint
            on children of every inherit mode under a non-uniformly scaled
            (and a mirrored) parent, read by a later local constraint and
            re-updated by a later IK on the parent; a physics bone (noScale)
            re-updated by a later IK; a two-bone IK whose parent is scaled
            non-uniformly (in reach and, in the poses, out of it); and a
            mesh drawn mirrored at setup (G1 takes that as its unfolded
            state).
  order_gap / order_dup
            constraints 4.2 never runs: an IK at order 0 and a transform
            constraint at order 2 (a gap) or also 0 (a repeat), with an
            animation keying the dead one's mixes.
"""
import argparse
import math
import os
import struct
import zlib

ARM_X = [-10 + 20 * i for i in range(12)]  # strip columns; the elbow sits between 90 and 110
ARM_Y = [-15.0, 0.0, 15.0]
ELBOW = 100.0


def strip_mesh(xs, ys, weight_fn, bone_names):
    """A grid strip (outline first, interior after) as a Spine 4.2 mesh."""
    nx, ny = len(xs), len(ys)
    grid = {}
    order = []
    # outline: bottom row left->right, right column up, top row right->left, left column down
    loop = [(i, 0) for i in range(nx)] + [(nx - 1, j) for j in range(1, ny)] + \
           [(i, ny - 1) for i in range(nx - 2, -1, -1)] + [(0, j) for j in range(ny - 2, 0, -1)]
    for ij in loop:
        grid[ij] = len(order)
        order.append(ij)
    for j in range(1, ny - 1):
        for i in range(1, nx - 1):
            grid[(i, j)] = len(order)
            order.append((i, j))
    x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
    uvs, vertices = [], []
    for i, j in order:
        x, y = xs[i], ys[j]
        uvs += [(x - x0) / (x1 - x0), (y1 - y) / (y1 - y0)]
        infl = [(b, w) for b, w in weight_fn(x, y) if w > 0]
        vertices.append(len(infl))
        for b, w in infl:
            bx = 0.0 if b == bone_names[0] else ELBOW  # both arm bones lie on the x axis at setup
            vertices += [bone_names.index(b), x - bx, y, w]
    tris = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, d = grid[(i, j)], grid[(i + 1, j)], grid[(i + 1, j + 1)], grid[(i, j + 1)]
            tris += [a, b, c, a, c, d]
    return {"type": "mesh", "uvs": [round(u, 6) for u in uvs], "triangles": tris, "vertices": vertices,
            "hull": len(loop), "width": x1 - x0, "height": y1 - y0}


def _weights(kind):
    def rigid(x, y):
        return [("upper", 1.0)] if x < ELBOW else [("fore", 1.0)]

    def ramp(x, y):
        w = min(1.0, max(0.0, (x - 60.0) / 80.0))
        return [("upper", round(1 - w, 6)), ("fore", round(w, 6))]
    return {"rigid": rigid, "ramp": ramp}[kind]


def arm(weights="rigid"):
    """(skeleton JSON dict, atlas text) of the plain two-bone arm."""
    bones = [{"name": "root"},
             {"name": "upper", "parent": "root", "length": ELBOW},
             {"name": "fore", "parent": "upper", "x": ELBOW, "length": 100},
             {"name": "hand", "parent": "fore", "x": 100, "length": 20}]
    # weighted vertices are bound by bone index: root 0, upper 1, fore 2
    mesh = strip_mesh(ARM_X, ARM_Y, _weights(weights), ["upper", "fore"])
    mesh["vertices"] = _shift_bone_index(mesh["vertices"], 1)
    skel = {"skeleton": {"hash": "synthetic-arm", "spine": "4.2.43", "x": -10, "y": -15, "width": 240,
                         "height": 30, "referenceScale": 100, "images": "./"},
            "bones": bones,
            "slots": [{"name": "arm", "bone": "upper", "attachment": "arm"},
                      {"name": "hand", "bone": "hand", "attachment": "hand"}],
            "skins": [{"name": "default", "attachments": {
                "arm": {"arm": mesh},
                "hand": {"hand": {"x": 12, "y": 0, "rotation": 10, "width": 30, "height": 20}}}}],
            "animations": {"bend": {"bones": {"fore": {"rotate": [
                {"time": 0, "value": 0, "curve": [0.1667, 0, 0.3333, 60]},
                {"time": 0.5, "value": 90}, {"time": 1, "value": 0}]}}}}}
    return skel, ATLAS_ARM


def _shift_bone_index(values, base):
    out, i = [], 0
    while i < len(values):
        n = values[i]
        out.append(n)
        i += 1
        for _ in range(n):
            out += [values[i] + base, values[i + 1], values[i + 2], values[i + 3]]
            i += 4
    return out


ATLAS_ARM = """synth.png
size:512,256
filter:Linear,Linear
arm
bounds:0,0,220,30
hand
bounds:230,0,24,16
offsets:3,2,30,20
"""

ATLAS_FEATURES = ATLAS_ARM + """arm_alt
bounds:0,40,220,30
patch
bounds:260,0,40,40
rotate:90
"""

INHERIT = ["normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection"]


def features(weights="ramp"):
    """(skeleton JSON dict, atlas text) exercising every supported feature."""
    skel, _ = arm(weights)
    bones = skel["bones"]
    bones += [
        {"name": "tip", "parent": "fore", "x": 60, "y": 10, "rotation": 30, "length": 40},
        {"name": "elbow_mid", "parent": "upper", "x": ELBOW, "length": 10},
        {"name": "follow_w", "parent": "root", "x": 50, "y": -80, "rotation": -20, "length": 30},
        {"name": "follow_wr", "parent": "root", "x": -50, "y": -80, "rotation": 10, "length": 30},
        {"name": "follow_lr", "parent": "upper", "x": 30, "y": 20, "rotation": 5, "length": 20},
        # parents that make inherit modes differ: sheared + non-uniform scale, and a reflection
        {"name": "p", "parent": "root", "x": -200, "y": 100, "rotation": 30, "scaleX": 1.5, "scaleY": 0.8,
         "shearX": 5, "shearY": -8, "length": 50},
        {"name": "pr", "parent": "root", "x": 200, "y": 100, "rotation": -20, "scaleX": -1.25, "scaleY": 0.9,
         "length": 50},
    ]
    for parent in ("p", "pr"):
        for mode in INHERIT:
            bones.append({"name": f"{parent}_{mode}", "parent": parent, "x": 20, "y": 5, "rotation": 15,
                          "scaleX": 1.2, "scaleY": 0.9, "shearX": 3, "shearY": 4, "length": 25, "inherit": mode})
    bones += [
        # 1-bone IK with stretch/compress
        {"name": "aim", "parent": "root", "x": 0, "y": -200, "rotation": 45, "length": 80},
        {"name": "aim_target", "parent": "root", "x": 120, "y": -150},
        # 2-bone IK legs: soft, and stretchy with a child offset off the parent's axis
        {"name": "hip", "parent": "root", "x": -300, "y": -100},
        {"name": "thigh", "parent": "hip", "rotation": -80, "length": 90},
        {"name": "shin", "parent": "thigh", "x": 90, "y": 4, "rotation": 20, "length": 85},
        {"name": "foot_target", "parent": "root", "x": -270, "y": -260},
        {"name": "hip2", "parent": "root", "x": 300, "y": -100, "scaleX": 1.1, "scaleY": 1.1},
        {"name": "thigh2", "parent": "hip2", "rotation": -100, "length": 90},
        {"name": "shin2", "parent": "thigh2", "x": 90, "y": 6, "rotation": -25, "length": 85},
        {"name": "foot2_target", "parent": "root", "x": 330, "y": -330},
        {"name": "path_follow", "parent": "root", "x": 0, "y": 300, "length": 10},
        {"name": "aim_onlyT", "parent": "p", "x": 40, "y": 0, "length": 30, "inherit": "onlyTranslation"},
        {"name": "aim_noScale", "parent": "p", "x": 40, "y": 10, "length": 30, "inherit": "noScale"},
        # local absolute transform constraint: scaled bone, target 340 deg away
        {"name": "tla_t", "parent": "root", "x": 0, "y": -400, "rotation": 170, "scaleX": 1.2, "scaleY": 0.9,
         "shearY": 6, "length": 30},
        {"name": "tla_b", "parent": "root", "x": 60, "y": -400, "rotation": -170, "scaleX": 2, "scaleY": 1.5,
         "shearY": 5, "length": 30},
        # world constraint whose angle (340 deg + offset 250) needs more than one turn of wrapping
        {"name": "tla_w", "parent": "root", "x": 120, "y": -400, "rotation": -170, "length": 30},
        # applied-transform chains: world TC on children, then IK on their parents
        {"name": "ch_t", "parent": "root", "x": 100, "y": -320, "rotation": 120, "length": 30},
        {"name": "ch_aim", "parent": "root", "x": -260, "y": -300},
        {"name": "ch_reader", "parent": "root", "x": -80, "y": -460, "length": 40},
    ]
    for parent, sx in (("chp", 2.0), ("chq", -2.0)):
        bones.append({"name": parent, "parent": "root", "x": -150, "y": -400 if sx > 0 else -520, "rotation": 20,
                      "scaleX": sx, "scaleY": 0.7, "length": 50})
        for mode in INHERIT:
            bones.append({"name": f"{parent}_{mode}", "parent": parent, "x": 30, "y": 4, "rotation": 50,
                          "shearY": 8, "length": 40, "inherit": mode})
    bones += [
        # physics bone re-updated by a later IK on its parent
        {"name": "phy_p", "parent": "root", "x": 250, "y": -400, "rotation": 20, "scaleX": 2, "scaleY": 0.7,
         "length": 50},
        {"name": "phy_b", "parent": "phy_p", "x": 30, "rotation": 50, "length": 40, "inherit": "noScale"},
        # two-bone IK under a non-uniformly scaled parent
        {"name": "hip3", "parent": "root", "x": -500, "y": 200},
        {"name": "thigh3", "parent": "hip3", "rotation": -80, "scaleX": 1.5, "scaleY": 1.0, "length": 90},
        {"name": "shin3", "parent": "thigh3", "x": 90, "rotation": 20, "length": 85},
        {"name": "foot3_target", "parent": "root", "x": -440, "y": 50},
    ]
    chain_children = [f"{p}_{m}" for p in ("chp", "chq") for m in INHERIT]
    skel["ik"] = [
        {"name": "ik_aim", "order": 0, "bones": ["aim"], "target": "aim_target", "compress": True, "stretch": True,
         "uniform": True},
        {"name": "ik_leg", "order": 1, "bones": ["thigh", "shin"], "target": "foot_target", "bendPositive": False,
         "softness": 12},
        {"name": "ik_leg2", "order": 2, "bones": ["thigh2", "shin2"], "target": "foot2_target", "stretch": True,
         "mix": 0.8},
        {"name": "ik_aim_onlyT", "order": 3, "bones": ["aim_onlyT"], "target": "aim_target", "stretch": True},
        {"name": "ik_aim_noScale", "order": 4, "bones": ["aim_noScale"], "target": "aim_target", "stretch": True},
    ]
    skel["transform"] = [
        {"name": "tc_half", "order": 5, "bones": ["elbow_mid"], "target": "fore", "local": True,
         "mixRotate": 0.5, "mixX": 0, "mixScaleX": 0, "mixShearY": 0},
        {"name": "tc_world", "order": 6, "bones": ["follow_w"], "target": "fore", "rotation": 15, "x": 5, "y": -3,
         "scaleX": 0.1, "scaleY": -0.1, "shearY": 4, "mixRotate": 0.5, "mixX": 0.7, "mixY": 0.3,
         "mixScaleX": 0.6, "mixScaleY": 0.4, "mixShearY": 0.5},
        {"name": "tc_world_rel", "order": 7, "bones": ["follow_wr"], "target": "tip", "relative": True,
         "rotation": 10, "x": 2, "mixRotate": 0.4, "mixX": 0.2, "mixY": 0.1, "mixScaleX": 0.3, "mixScaleY": 0.2,
         "mixShearY": 0.3},
        {"name": "tc_local_rel", "order": 8, "bones": ["follow_lr"], "target": "tip", "local": True,
         "relative": True, "rotation": 5, "mixRotate": 0.5, "mixX": 0.5, "mixY": 0.5, "mixScaleX": 0.5,
         "mixScaleY": 0.5, "mixShearY": 0.5},
    ]
    skel["transform"] += [
        {"name": "tc_local_abs", "order": 11, "bones": ["tla_b"], "target": "tla_t", "local": True,
         "rotation": 10, "x": 2, "y": -3, "scaleX": 0.1, "scaleY": -0.1, "shearY": 3, "mixRotate": 0.5,
         "mixX": 0.3, "mixY": 0.4, "mixScaleX": 0.5, "mixScaleY": 0.6, "mixShearY": 0.7},
        {"name": "tc_chain", "order": 12, "bones": chain_children, "target": "ch_t", "rotation": 15, "x": 4,
         "mixRotate": 0.5, "mixX": 0.3, "mixY": 0.2, "mixScaleX": 0.4, "mixScaleY": 0.3, "mixShearY": 0.4},
        {"name": "tc_read", "order": 13, "bones": ["ch_reader"], "target": "chp_normal", "local": True,
         "relative": True, "mixRotate": 0.3, "mixX": 0, "mixY": 0, "mixScaleX": 0.5, "mixScaleY": 1,
         "mixShearY": 1},
        {"name": "tc_world_wrap", "order": 19, "bones": ["tla_w"], "target": "tla_t", "rotation": 250,
         "mixRotate": 0.5, "mixX": 0, "mixY": 0, "mixScaleX": 0, "mixScaleY": 0, "mixShearY": 0},
    ]
    skel["ik"] += [
        {"name": "ik_chp", "order": 14, "bones": ["chp"], "target": "ch_aim"},
        {"name": "ik_chq", "order": 15, "bones": ["chq"], "target": "ch_aim"},
        {"name": "ik_phy", "order": 17, "bones": ["phy_p"], "target": "ch_aim"},
        {"name": "ik_leg3", "order": 18, "bones": ["thigh3", "shin3"], "target": "foot3_target", "softness": 4},
    ]
    skel["path"] = [{"name": "pc", "order": 9, "bones": ["path_follow"], "target": "path", "mixRotate": 0,
                     "mixX": 0, "mixY": 0, "position": 0.25, "spacingMode": "fixed", "rotateMode": "chain"}]
    skel["physics"] = [{"name": "phys_tip", "order": 10, "bone": "tip", "rotate": 1, "inertia": 0.5,
                        "strength": 120, "damping": 0.85, "mass": 1.2, "limit": 2000, "fps": 60, "wind": 1,
                        "gravity": -2, "mix": 0.9, "windGlobal": True},
                       {"name": "phys_ns", "order": 16, "bone": "phy_b", "rotate": 1, "inertia": 1, "strength": 100,
                        "damping": 1, "mass": 1, "limit": 5000, "fps": 60}]
    skel["slots"] += [
        {"name": "patch", "bone": "fore", "attachment": "patch", "color": "ffffffee", "blend": "additive"},
        {"name": "muzzle", "bone": "tip", "attachment": "muzzle", "dark": "000000"},
        {"name": "hitbox", "bone": "upper", "attachment": "hitbox"},
        {"name": "clip", "bone": "root", "attachment": "clip"},
        {"name": "path", "bone": "root", "attachment": "path"},
        {"name": "mirror", "bone": "pr", "attachment": "patch_m"},
    ]
    for name in ("tla_b", "tla_w", "ch_reader", "thigh3", "shin3", "phy_b", *chain_children):
        skel["slots"].append({"name": f"box_{name}", "bone": name, "attachment": "box"})
    skel["slots"][1]["dark"] = "102030"  # hand: two-colour tint, for rgba2/rgb2 timelines
    atts = skel["skins"][0]["attachments"]
    atts["patch"] = {"patch": {"type": "mesh", "uvs": [0, 1, 1, 1, 1, 0, 0, 0, 0.5, 0.5],
                               "triangles": [0, 1, 4, 1, 2, 4, 2, 3, 4, 3, 0, 4],
                               "vertices": [0, -20, 40, -20, 40, 20, 0, 20, 20, 0], "hull": 4,
                               "edges": [0, 2, 2, 4, 4, 6, 6, 0], "width": 40, "height": 40}}
    atts["muzzle"] = {"muzzle": {"type": "point", "x": 40, "y": 0, "rotation": 5, "color": "ff0000ff"}}
    atts["hitbox"] = {"hitbox": {"type": "boundingbox", "vertexCount": 4,
                                 "vertices": [0, -20, 100, -20, 100, 20, 0, 20]}}
    atts["clip"] = {"clip": {"type": "clipping", "end": "path", "vertexCount": 3,
                             "vertices": [-400, -400, 400, -400, 0, 400]}}
    atts["path"] = {"path": {"type": "path", "closed": False, "constantSpeed": True, "lengths": [150],
                             "vertexCount": 6, "vertices": [-100, 300, -50, 330, 0, 330, 50, 330, 100, 300, 120, 290]}}
    # the same patch on the mirrored parent pr (scaleX -1.25): mirrored at setup
    atts["mirror"] = {"patch_m": dict(atts["patch"]["patch"], path="patch")}
    for slot in skel["slots"]:
        if slot["name"].startswith("box_"):
            atts[slot["name"]] = {"box": {"path": "hand", "x": 15, "y": 0, "width": 30, "height": 20}}
    atts["arm"]["arm_alt"] = {"type": "linkedmesh", "parent": "arm", "timelines": True, "width": 220, "height": 30}
    skel["skins"].append({"name": "alt", "attachments": {"hand": {"hand": {
        "name": "hand_alt", "path": "hand", "x": 10, "y": 2, "rotation": -5, "scaleX": 1.1, "width": 30,
        "height": 20}}}})
    skel["events"] = {"hit": {"int": 1, "float": 0.5, "string": "x"},
                      "step": {"audio": "step.ogg", "volume": 0.8, "balance": -0.2}}
    skel["animations"] = _feature_animations(skel)
    return skel, ATLAS_FEATURES


def _feature_animations(skel):
    n_infl = 0
    v = skel["skins"][0]["attachments"]["arm"]["arm"]["vertices"]
    i = 0
    while i < len(v):
        n_infl += v[i]
        i += 1 + 4 * v[i]
    arm_deform = [round(0.5 * math.sin(k), 4) for k in range(2 * n_infl)]
    anims = {
        "swing": {
            "bones": {
                "upper": {"rotate": [{"time": 0, "value": 0, "curve": [0.1667, 0, 0.3333, 80]},
                                     {"time": 0.5, "value": 80}, {"time": 1, "value": 0}],
                          "translate": [{"time": 0, "x": 0, "y": 0, "curve": [0.3, 0, 0.6, 10, 0.3, 0, 0.6, 5]},
                                        {"time": 1, "x": 10, "y": 5}]},
                "fore": {"rotate": [{"time": 0}, {"time": 0.5, "value": 120, "curve": "stepped"},
                                    {"time": 1, "value": 10}]},
                "hand": {"scale": [{"time": 0}, {"time": 0.5, "x": 1.2, "y": 0.8, "curve": "stepped"},
                                   {"time": 1}],
                         "shear": [{"time": 0}, {"time": 1, "x": 10, "y": -5}]},
                "tip": {"translatex": [{"time": 0}, {"time": 1, "value": 15}],
                        "scaley": [{"time": 0.2, "value": 1.1}], "shearx": [{"time": 0.3, "value": 4}]},
                "p_normal": {"inherit": [{"time": 0.5, "inherit": "noScale"}]},
            },
            "slots": {
                "hand": {"attachment": [{"time": 0.25, "name": None}, {"time": 0.5, "name": "hand"}],
                         "rgba2": [{"time": 0, "light": "ffffffff", "dark": "000000",
                                    "curve": [0.3, 1, 0.6, 0.5, 0.3, 1, 0.6, 0.5, 0.3, 1, 0.6, 0.5,
                                              0.3, 1, 0.6, 0.5, 0.3, 0, 0.6, 0.2, 0.3, 0, 0.6, 0.2,
                                              0.3, 0, 0.6, 0.2]},
                                   {"time": 1, "light": "80808080", "dark": "336699"}]},
                "arm": {"rgba": [{"time": 0, "color": "ffffffff"}, {"time": 1, "color": "ff0000cc"}],
                        "attachment": [{"time": 0.7, "name": "arm_alt"}]},
                "patch": {"alpha": [{"time": 0, "value": 1}, {"time": 1, "value": 0.25}],
                          "rgb": [{"time": 0, "color": "ffffff"}, {"time": 1, "color": "00ff00"}]},
                "muzzle": {"rgb2": [{"time": 0, "light": "ffffff", "dark": "000000"},
                                    {"time": 1, "light": "ff8800", "dark": "112233"}]},
            },
            "ik": {"ik_leg": [{"time": 0, "mix": 1, "softness": 12, "bendPositive": False},
                              {"time": 0.5, "mix": 0.5, "softness": 0, "curve": [0.6, 0.5, 0.8, 0.9, 0.6, 0, 0.8, 0]},
                              {"time": 1, "mix": 1, "bendPositive": True, "compress": True, "stretch": True}]},
            "transform": {"tc_world": [{"time": 0, "mixRotate": 0.5, "mixX": 0.7, "mixY": 0.3, "mixScaleX": 0.6,
                                        "mixScaleY": 0.4, "mixShearY": 0.5},
                                       {"time": 1, "mixRotate": 1, "mixX": 1, "mixScaleX": 0, "mixShearY": 0}]},
            "path": {"pc": {"position": [{"time": 0, "value": 0.25}, {"time": 1, "value": 0.75}],
                            "spacing": [{"time": 0.5, "value": 3}],
                            "mix": [{"time": 0, "mixRotate": 0, "mixX": 0}, {"time": 1, "mixRotate": 0, "mixX": 0}]}},
            "physics": {"phys_tip": {"inertia": [{"time": 0, "value": 0.5}, {"time": 1, "value": 0.8}],
                                     "reset": [{"time": 0.5}]},
                        "": {"wind": [{"time": 0, "value": 0}, {"time": 1, "value": 2}]}},
            "attachments": {"default": {
                "arm": {"arm": {"deform": [{"time": 0}, {"time": 0.5, "offset": 2, "vertices": arm_deform[2:-4],
                                                         "curve": [0.6, 0, 0.8, 1]},
                                           {"time": 1}]}},
                "patch": {"patch": {"deform": [{"time": 0}, {"time": 1, "vertices": [0, 0, 5, -5, 5, 5, 0, 0, 2, 0]}]}}}},
            "drawOrder": [{"time": 0.5, "offsets": [{"slot": "arm", "offset": 2}, {"slot": "path", "offset": -3}]},
                          {"time": 0.9}],
            "events": [{"time": 0.1, "name": "hit"}, {"time": 0.6, "name": "step", "int": 3, "volume": 0.5}],
        },
    }
    # single-key poses: what oracle_check compares with physics off
    anims["pose_reach"] = {"bones": {
        "upper": {"rotate": [{"value": 35}]}, "fore": {"rotate": [{"value": 70}], "scale": [{"x": 1.1, "y": 0.95}]},
        "aim_target": {"translate": [{"x": 260, "y": 40}]}, "foot_target": {"translate": [{"x": -20, "y": -25}]},
        "foot2_target": {"translate": [{"x": 60, "y": -60}]}, "p": {"rotate": [{"value": 40}]},
        "pr": {"scale": [{"x": 0.7, "y": 1.3}]}, "tip": {"rotate": [{"value": -25}]}}}
    anims["pose_reach"]["bones"]["foot3_target"] = {"translate": [{"x": 260, "y": -260}]}  # out of reach
    anims["pose_fold"] = {"bones": {"fore": {"rotate": [{"value": 170}]}, "upper": {"shear": [{"x": 5, "y": 10}]},
                                    "foot_target": {"translate": [{"x": 0, "y": 80}]},
                                    "foot3_target": {"translate": [{"x": -55, "y": 140}]}}}  # closer than it folds
    # past 180 deg: the half-angle helper and the local absolute constraint do not wrap
    anims["pose_wrap"] = {"bones": {"fore": {"rotate": [{"value": 200}]}, "tla_t": {"rotate": [{"value": 45}]},
                                    "chp": {"rotate": [{"value": -35}], "scale": [{"x": 0.8, "y": 1.3}]},
                                    "ch_t": {"rotate": [{"value": 150}]},
                                    "foot3_target": {"translate": [{"x": 20, "y": -40}]}}}
    return anims


# ---------------------------------------------------------------- files

def blank_png(width, height):
    """A fully transparent RGBA PNG (the atlas page size is what L7 checks)."""
    row = b"\x00" + b"\x00" * (4 * width)
    raw = zlib.compress(row * height, 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def order_rig(repeat=False):
    """(skeleton JSON dict, atlas text): an IK at order 0 and a transform
    constraint 4.2 never runs, at order 2 (a gap: N = 2, so only 0 and 1
    run) or, with `repeat`, also at order 0 (only the first match runs).
    An animation keys the dead constraint's mixes; it still does nothing."""
    bones = [{"name": "root"}, {"name": "t", "parent": "root", "x": 80, "y": 60, "length": 40},
             {"name": "b", "parent": "root", "x": -50, "length": 40},
             {"name": "c", "parent": "root", "x": 0, "y": -50, "length": 40}]
    box = {"path": "hand", "x": 15, "y": 0, "width": 30, "height": 20}
    skel = {"skeleton": {"hash": "synthetic-order", "spine": "4.2.43", "width": 300, "height": 300,
                         "referenceScale": 100, "images": "./"},
            "bones": bones,
            "slots": [{"name": "sb", "bone": "b", "attachment": "box"}, {"name": "sc", "bone": "c", "attachment": "box"}],
            "ik": [{"name": "ik", "order": 0, "bones": ["c"], "target": "t"}],
            "transform": [{"name": "tc", "order": 0 if repeat else 2, "bones": ["b"], "target": "t",
                           "mixRotate": 1, "mixX": 1, "mixY": 1, "mixScaleX": 0, "mixScaleY": 0, "mixShearY": 0}],
            "skins": [{"name": "default", "attachments": {"sb": {"box": dict(box)}, "sc": {"box": dict(box)}}}],
            "animations": {"move": {"bones": {"t": {"translate": [{"time": 0}, {"time": 1, "x": -40, "y": 30}]}},
                                    "transform": {"tc": [{"time": 0, "mixRotate": 1, "mixX": 1, "mixY": 1,
                                                          "mixScaleX": 0, "mixScaleY": 0, "mixShearY": 0},
                                                         {"time": 1, "mixRotate": 0.5, "mixX": 0.5, "mixY": 0.5,
                                                          "mixScaleX": 0, "mixScaleY": 0, "mixShearY": 0}]}}}}
    return skel, ATLAS_ARM


def build(rig="arm", weights=None):
    """(skeleton JSON dict, atlas text) of a named synthetic rig."""
    if rig == "arm":
        return arm(weights or "rigid")
    if rig == "features":
        return features(weights or "ramp")
    if rig in ("order_gap", "order_dup"):
        return order_rig(repeat=rig == "order_dup")
    raise ValueError(f"unknown synthetic rig {rig!r}")


def write(outdir, rig="arm", weights=None, name=None):
    """Write <name>.json, <name>.atlas and the blank page PNG; returns the paths."""
    import json
    skel, atlas = build(rig, weights)
    name = name or f"synth_{rig}"
    os.makedirs(outdir, exist_ok=True)
    jp, ap = os.path.join(outdir, name + ".json"), os.path.join(outdir, name + ".atlas")
    with open(jp, "w") as f:
        json.dump(skel, f, indent=1)
    with open(ap, "w") as f:
        f.write(atlas)
    with open(os.path.join(outdir, "synth.png"), "wb") as f:
        f.write(blank_png(512, 256))
    return jp, ap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir")
    ap.add_argument("--rig", choices=["arm", "features", "order_gap", "order_dup"], default="arm")
    ap.add_argument("--weights", choices=["rigid", "ramp"])
    args = ap.parse_args(argv)
    for p in write(args.outdir, args.rig, args.weights):
        print(p)


if __name__ == "__main__":
    main()
