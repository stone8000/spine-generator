"""Trial: the same layers, rig and motions, skinned two ways — the motion kit's rigid regions vs spinegen meshes.

    node motionkit/scripts/trial/rig.ts <work>            # rig.json + frames.json (official 4.3 runtime)
    python research/trials/skin_trial.py <work> <layers dir>

The bones of every frame come from the official runtime (frames.json), so the two renders differ in nothing but
the attachments:

  rigid    the autorig's full-canvas region per layer, one bone each (what the kit ships today)
  skinned  spinegen.mesh.build + spinegen.weights.compute per layer: joint bands, ramps that hand each layer's
           ends over to the neighbouring bone, and half-angle helpers at elbows and knees

Skinning is Spine's own formula (a weighted vertex is the weight-blended sum of its bone-local positions through
each bone's world matrix), evaluated on the runtime's matrices. Writes contact sheets, a metrics table and
skinned.json (the Spine 4.3 skeleton with the meshes, importable into the editor) to <work>.

Joint gap: background pixels inside a disk of JOINT_DISK x the limb half-width around each joint, minus the same
count at rest — the pixels where the limb has come apart. A trial proxy for SOTA gate G4, not the gate itself.
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from spinegen import mesh as meshmod  # noqa: E402
from spinegen import weights as wmod  # noqa: E402
from spinegen.raster import raster_triangles  # noqa: E402

ROOT = (512.0, 1456.0)      # motion kit template: canvas px of the Spine origin (rigTemplate.ROOT_PX)
SCALE = 0.5                 # render scale: metrics and sheets at half resolution (full res is ~4x slower)
OUT_W, OUT_H = int(1200 * SCALE), int(2000 * SCALE)   # render canvas, room for jumps and raised arms
OX, OY = 600.0 * SCALE, 1800.0 * SCALE                # render px of the Spine origin
JOINT_DISK = 0.7            # joint-gap disk radius, in limb half-widths

# joint -> (upper bone, helper or None, lower bone); the joint sits at the lower bone's origin
JOINTS = {
    "shoulderF": ("chest", None, "upperArmF"), "elbowF": ("upperArmF", "elbowF_mid", "foreArmF"),
    "wristF": ("foreArmF", None, "handF"),
    "shoulderB": ("chest", None, "upperArmB"), "elbowB": ("upperArmB", "elbowB_mid", "foreArmB"),
    "wristB": ("foreArmB", None, "handB"),
    "hipF": ("hip", None, "thighF"), "kneeF": ("thighF", "kneeF_mid", "shinF"),
    "hipB": ("hip", None, "thighB"), "kneeB": ("thighB", "kneeB_mid", "shinB"),
    "waist": ("hip", None, "chest"), "neckBase": ("chest", None, "neck"), "headBase": ("neck", None, "head"),
    "hairRoot": ("head", None, "hairF"),
}
# which joints each layer spans. The torso and pelvis do not follow the limbs.
#
# Shoulders and hips stay RIGID (the limb's root end is 100% on the limb bone), measured on this trial: ramping the
# upper arm's root into the chest without a helper put vertices between two bones up to ~110 degrees apart in
# `celebrate`, and linear blend skinning collapsed the whole upper arm into a swollen blob (4 folded triangles,
# research/trials/README.md). The template already paints the limb roots into the torso/pelvis with round ends,
# which is what a rigid root needs. Elbows and knees bend with half-angle helpers.
LAYER_JOINTS = {
    "arm_front_upper": ["elbowF"], "arm_front_lower": ["elbowF", "wristF"], "fist_front": ["wristF"],
    "arm_back_upper": ["elbowB"], "arm_back_lower": ["elbowB", "wristB"], "fist_back": ["wristB"],
    "leg_front_upper": ["kneeF"], "leg_front_lower": ["kneeF"],
    "leg_back_upper": ["kneeB"], "leg_back_lower": ["kneeB"],
    "pelvis": ["waist"], "torso": ["waist", "neckBase"], "neck": ["neckBase", "headBase"], "head": ["headBase"],
    "hair_front": ["hairRoot"], "hair_back": [],
}


def to_canvas(p):
    return np.stack([np.asarray(p)[..., 0] + ROOT[0], ROOT[1] - np.asarray(p)[..., 1]], -1)


def to_spine(c):
    return np.stack([np.asarray(c)[..., 0] - ROOT[0], ROOT[1] - np.asarray(c)[..., 1]], -1)


def apply(m, local):
    a, b, c, d, x, y = m
    return np.stack([a * local[:, 0] + b * local[:, 1] + x, c * local[:, 0] + d * local[:, 1] + y], 1)


def inverse(m, world):
    a, b, c, d, x, y = m
    det = a * d - b * c
    dx, dy = world[:, 0] - x, world[:, 1] - y
    return np.stack([(d * dx - b * dy) / det, (a * dy - c * dx) / det], 1)


def bone_line(setup, rig_bones, name):
    """Head and tail of a bone in canvas px, from the runtime's setup matrices."""
    a, b, c, d, x, y = setup[name]
    length = rig_bones[name].get("length", 0.0) or 0.0
    head = np.array([x, y])
    tail = head + length * np.array([a, c]) / max(1e-9, np.hypot(a, c))
    return to_canvas(head), to_canvas(tail)


def halfwidth(alpha_union, at):
    dist = cv2.distanceTransform((alpha_union > 8).astype(np.uint8), cv2.DIST_L2, 5)
    x, y = int(round(at[0])), int(round(at[1]))
    r = 6
    win = dist[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    return float(win.max()) if win.size else 0.0


def build_skinned(rig, setup, images):
    bones = {b["name"]: b for b in rig["bones"]}
    slots = rig["slots"]
    joint_xy = {j: bone_line(setup, bones, low)[0] for j, (_, _, low) in JOINTS.items() if low in setup}
    owner = {s["name"]: s["bone"] for s in slots}
    out = {}
    for s in slots:
        name = s["name"]
        alpha = images[name][..., 3]
        joints, bone_names = [], [owner[name]]
        bands = []
        for j in LAYER_JOINTS.get(name, []):
            up, helper, low = JOINTS[j]
            if low not in setup or up not in setup:
                continue
            neighbour = [n for n, js in LAYER_JOINTS.items() if j in js and n != name and n in images]
            union = alpha.copy()
            for n in neighbour:
                union = np.maximum(union, images[n][..., 3])
            hw = halfwidth(union, joint_xy[j])
            if hw < 3:
                continue
            u0, u1 = bone_line(setup, bones, up)
            l0, l1 = bone_line(setup, bones, low)
            du = (u1 - u0) / max(1e-9, np.linalg.norm(u1 - u0))
            dl = (l1 - l0) / max(1e-9, np.linalg.norm(l1 - l0))
            axis = du + dl if np.linalg.norm(du + dl) > 1e-6 else dl
            axis = axis / np.linalg.norm(axis)
            joints.append({"center": tuple(joint_xy[j]), "upper": up, "helper": helper, "lower": low,
                           "halfwidth": hw, "axis": tuple(axis)})
            bands.append({"center": tuple(joint_xy[j]), "axis": tuple(axis), "halfwidth": hw})
            for nb in (up, helper, low):
                if nb and nb not in bone_names:
                    bone_names.append(nb)
        m = meshmod.build(alpha, (0, 0), bands=bands)
        verts, tris, hull = m["verts"], m["tris"], m["hull"]
        bl = []
        for bn in bone_names:
            h, t = bone_line(setup, bones, bn)
            bl.append({"name": bn, "head": tuple(h), "tail": tuple(t)})
        W = wmod.compute(verts, tris, verts[:hull], bl, joints=joints)
        world = to_spine(verts)
        influences = []
        for i, infl in enumerate(W):
            influences.append([(bn, w, inverse(setup[bn], world[i:i + 1])[0]) for bn, w in infl])
        out[name] = {"verts": verts, "uvs": m["uvs"], "tris": tris, "hull": hull, "influences": influences,
                     "stats": m.get("stats", {}), "joints": [j for j in LAYER_JOINTS.get(name, [])]}
    return out


def pack_influences(sk):
    """(N, K) bone names, weights and (N, K, 2) bone-local positions, zero-padded, for vectorised skinning."""
    K = max(len(i) for i in sk["influences"])
    n = len(sk["influences"])
    names = [[None] * K for _ in range(n)]
    W = np.zeros((n, K))
    L = np.zeros((n, K, 2))
    for i, infl in enumerate(sk["influences"]):
        for k, (bn, w, lp) in enumerate(infl):
            names[i][k], W[i, k], L[i, k] = bn, w, lp
    used = sorted({bn for row in names for bn in row if bn})
    idx = np.array([[used.index(bn) if bn else 0 for bn in row] for row in names])
    sk["_packed"] = (used, idx, W, L)


def skin_world(sk, frame_bones):
    if "_packed" not in sk:
        pack_influences(sk)
    used, idx, W, L = sk["_packed"]
    M = np.array([frame_bones[b] for b in used])[idx]          # (N, K, 6)
    x = M[..., 0] * L[..., 0] + M[..., 1] * L[..., 1] + M[..., 4]
    y = M[..., 2] * L[..., 0] + M[..., 3] * L[..., 1] + M[..., 5]
    return np.stack([(W * x).sum(1), (W * y).sum(1)], 1)


class Texture:
    """A layer image cropped to its ink (as Spine's packer strips whitespace), as float, with its canvas offset."""

    def __init__(self, rgba):
        ys, xs = np.nonzero(rgba[..., 3] > 0)
        self.x0, self.y0 = max(0, xs.min() - 2), max(0, ys.min() - 2)
        self.x1, self.y1 = min(rgba.shape[1], xs.max() + 3), min(rgba.shape[0], ys.max() + 3)
        self.full = (rgba.shape[1], rgba.shape[0])
        self.f = rgba[self.y0:self.y1, self.x0:self.x1].astype(np.float32) / 255.0


def region_quad(att, m, tex):
    """World corners of a region attachment, trimmed to the layer's ink, with texel coords into the trimmed image."""
    w, h = att["width"], att["height"]
    r = np.radians(att.get("rotation", 0.0))
    cr, sr = np.cos(r), np.sin(r)
    # image px (x right, y down) of the trimmed rectangle -> region-local (centre origin, y up)
    corners = np.array([[tex.x0, tex.y1], [tex.x0, tex.y0], [tex.x1, tex.y0], [tex.x1, tex.y1]], float)
    loc = np.stack([corners[:, 0] - w / 2, h / 2 - corners[:, 1]], 1)
    loc = np.stack([loc[:, 0] * cr - loc[:, 1] * sr + att.get("x", 0.0), loc[:, 0] * sr + loc[:, 1] * cr + att.get("y", 0.0)], 1)
    texel = corners - np.array([tex.x0, tex.y0])
    return apply(m, loc), texel, np.array([[0, 1, 2], [0, 2, 3]])


def draw(canvas, tex, world, texel, tris):
    H, W = canvas.shape[:2]
    px = np.stack([world[:, 0] * SCALE + OX, OY - world[:, 1] * SCALE], 1)
    th, tw = tex.f.shape[:2]
    pix, val, _ = raster_triangles(px, tris, (H, W), attrs=texel)
    if len(pix) == 0:
        return
    u = np.clip(val[:, 0] - 0.5, 0, tw - 1.001)
    v = np.clip(val[:, 1] - 0.5, 0, th - 1.001)
    x0, y0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    f = tex.f
    col = (f[y0, x0] * (1 - fx) * (1 - fy) + f[y0, x0 + 1] * fx * (1 - fy)
           + f[y0 + 1, x0] * (1 - fx) * fy + f[y0 + 1, x0 + 1] * fx * fy)
    a = col[:, 3:4]
    flat = canvas.reshape(-1, 4)
    dst = flat[pix]
    out_a = a + dst[:, 3:4] * (1 - a)
    out_c = (col[:, :3] * a + dst[:, :3] * dst[:, 3:4] * (1 - a)) / np.maximum(out_a, 1e-6)
    flat[pix] = np.concatenate([out_c, out_a], 1)


TEXTURES = {}


def render(rig, images, skinned, frame_bones, mode):
    canvas = np.zeros((OUT_H, OUT_W, 4), np.float32)
    atts = rig["skins"][0]["attachments"]
    for s in rig["slots"]:
        name = s["name"]
        tex = TEXTURES.get(name) or TEXTURES.setdefault(name, Texture(images[name]))
        if mode == "rigid":
            world, texel, tris = region_quad(atts[name][name], frame_bones[s["bone"]], tex)
        else:
            sk = skinned[name]
            texel = sk["uvs"] * np.array(tex.full) - np.array([tex.x0, tex.y0])
            world, tris = skin_world(sk, frame_bones), sk["tris"]
        draw(canvas, tex, world, texel, tris)
    return canvas


def folds(skinned, frame_bones, setup_bones):
    n = 0
    for sk in skinned.values():
        w0 = skin_world(sk, setup_bones)
        w1 = skin_world(sk, frame_bones)
        T = sk["tris"]
        def area(p):
            a, b, c = p[T[:, 0]], p[T[:, 1]], p[T[:, 2]]
            return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        a0, a1 = area(w0), area(w1)
        n += int(np.sum((np.sign(a0) != np.sign(a1)) & (np.abs(a1) > 1e-6)))
    return n


def joint_gaps(canvas, frame_bones, rig, radii):
    bones = {b["name"]: b for b in rig["bones"]}
    gaps = {}
    for j, (_, _, low) in JOINTS.items():
        if low not in frame_bones or j not in radii:
            continue
        x, y = frame_bones[low][4] * SCALE + OX, OY - frame_bones[low][5] * SCALE
        r = radii[j] * JOINT_DISK * SCALE
        y0, y1 = max(0, int(y - r - 1)), min(OUT_H, int(y + r + 2))
        x0, x1 = max(0, int(x - r - 1)), min(OUT_W, int(x + r + 2))
        yy, xx = np.mgrid[y0:y1, x0:x1]
        m = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
        gaps[j] = int(np.sum((canvas[y0:y1, x0:x1, 3] < 0.5) & m))
    return gaps


def to_image(canvas, bg=(0.93, 0.94, 0.96)):
    a = canvas[..., 3:4]
    rgb = canvas[..., :3] * a + np.array(bg) * (1 - a)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def write_skinned_json(rig, skinned, path):
    out = json.loads(json.dumps(rig))
    atts = out["skins"][0]["attachments"]
    names = [b["name"] for b in out["bones"]]
    for s in out["slots"]:
        sk = skinned[s["name"]]
        verts = []
        for infl in sk["influences"]:
            verts.append(len(infl))
            for bn, w, lp in infl:
                verts += [names.index(bn), round(float(lp[0]), 3), round(float(lp[1]), 3), round(float(w), 5)]
        atts[s["name"]][s["name"]] = {
            "type": "mesh", "uvs": [round(float(v), 6) for v in sk["uvs"].reshape(-1)],
            "triangles": [int(i) for i in sk["tris"].reshape(-1)], "vertices": verts, "hull": int(sk["hull"]),
            "width": 1024, "height": 1536,
        }
    with open(path, "w") as f:
        json.dump(out, f)


def main():
    work, layers_dir = sys.argv[1], sys.argv[2]
    rig = json.load(open(os.path.join(work, "rig.json")))
    frames = json.load(open(os.path.join(work, "frames.json")))
    setup = frames["setup"]
    images = {s["name"]: np.array(Image.open(os.path.join(layers_dir, s["name"] + ".png")).convert("RGBA"))
              for s in rig["slots"]}
    skinned = build_skinned(rig, setup, images)
    nv = sum(len(s["verts"]) for s in skinned.values())
    grid = sum(meshmod.grid_equivalent_vertex_count(images[n][..., 3]) for n in skinned)
    print(f"skinned: {len(skinned)} meshes, {nv} vertices (22 px grid equivalent {grid})")
    write_skinned_json(rig, skinned, os.path.join(work, "skinned.json"))

    rest_r = render(rig, images, skinned, setup, "rigid")
    rest_s = render(rig, images, skinned, setup, "skinned")
    print(f"rest pose, rigid vs skinned: max colour diff {np.abs(to_image(rest_r).astype(int) - to_image(rest_s)).max()}, "
          f"alpha px differing {(np.abs(rest_r[..., 3] - rest_s[..., 3]) > 0.5).sum()}")
    bones = {b["name"]: b for b in rig["bones"]}
    radii = {}
    for j, (up, _, low) in JOINTS.items():
        if low in setup:
            names = [n for n, js in LAYER_JOINTS.items() if j in js and n in images]
            if names:
                union = np.max(np.stack([images[n][..., 3] for n in names]), 0)
                radii[j] = halfwidth(union, bone_line(setup, bones, low)[0])
    base = {m: joint_gaps(c, setup, rig, radii) for m, c in (("rigid", rest_r), ("skinned", rest_s))}

    rows, table = [], []
    for anim, flist in frames["animations"].items():
        worst = {"rigid": {}, "skinned": {}}
        fold_max = 0
        picks = sorted(set(np.linspace(0, len(flist) - 1, 7).round().astype(int)))
        sheet = {"rigid": [], "skinned": []}
        gap_t = {"rigid": [], "skinned": []}
        for k, fr in enumerate(flist):
            for mode in ("rigid", "skinned"):
                c = render(rig, images, skinned, fr["bones"], mode)
                g = joint_gaps(c, fr["bones"], rig, radii)
                tot = 0
                for j, v in g.items():
                    d = max(0, v - base[mode].get(j, 0))
                    worst[mode][j] = max(worst[mode].get(j, 0), d)
                    tot += d
                gap_t[mode].append(tot)
                if k in picks:
                    sheet[mode].append((fr["t"], c))
            fold_max = max(fold_max, folds(skinned, fr["bones"], setup))
        # worst frame of the rigid version always goes on the sheet
        kw = int(np.argmax(gap_t["rigid"]))
        if kw not in picks:
            for mode in ("rigid", "skinned"):
                c = render(rig, images, skinned, flist[kw]["bones"], mode)
                sheet[mode].append((flist[kw]["t"], c))
        for mode in ("rigid", "skinned"):
            sheet[mode].sort(key=lambda x: x[0])
        rows.append((anim, sheet))
        tr = sum(worst["rigid"].values())
        ts = sum(worst["skinned"].values())
        bad_r = {j: v for j, v in worst["rigid"].items() if v > 20}
        bad_s = {j: v for j, v in worst["skinned"].items() if v > 20}
        table.append((anim, tr, ts, fold_max, bad_r, bad_s))
        print(f"{anim:10s} joint-gap px (sum of per-joint worst): rigid {tr:6d}  skinned {ts:6d}   skinned folds max {fold_max}")
        print(f"           rigid  joints > 20 px: {bad_r}")
        print(f"           skinned joints > 20 px: {bad_s}")

    # contact sheet: per animation two rows (rigid, skinned), frames cropped to the union of ink
    tiles = []
    for anim, sheet in rows:
        alls = [c for m in sheet for _, c in sheet[m]]
        ink = np.max(np.stack([c[..., 3] for c in alls]), 0) > 0.05
        ys, xs = np.nonzero(ink)
        y0, y1, x0, x1 = max(0, ys.min() - 20), ys.max() + 20, max(0, xs.min() - 20), xs.max() + 20
        scale = 300.0 / (y1 - y0)
        for mode in ("rigid", "skinned"):
            row = []
            for t, c in sheet[mode]:
                im = cv2.resize(to_image(c[y0:y1, x0:x1]), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                im = cv2.copyMakeBorder(im, 28, 4, 4, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255))
                cv2.putText(im, f"{anim} {mode} t={t:.2f}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (180, 30, 30) if mode == "rigid" else (30, 120, 30), 1, cv2.LINE_AA)
                row.append(im)
            tiles.append(np.concatenate(row, 1))
    width = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, 0, 0, width - t.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
    for i in range(0, len(tiles), 2):
        anim = rows[i // 2][0]
        Image.fromarray(np.concatenate(tiles[i:i + 2], 0)).save(os.path.join(work, f"sheet_{anim}.png"))
    with open(os.path.join(work, "metrics.json"), "w") as f:
        json.dump([{"anim": a, "rigid_gap": r, "skinned_gap": s, "skinned_folds": fo, "rigid_bad": br, "skinned_bad": bs}
                   for a, r, s, fo, br, bs in table], f, indent=1)


if __name__ == "__main__":
    main()
