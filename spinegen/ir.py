"""Version-neutral rig and motion IR (`spinegen-rig/1`, `spinegen-motion-ir/1`).

WHY AN IR
  Spine 4.2 and 4.3 disagree on where constraints live, on physics defaults
  and on how constraint order is stored (SOTA §2, §3.1). Everything upstream
  of the emitters (rig builder, budget, motion synthesis) works on this IR
  and never sees a version's quirks; emit/spine42.py (and later spine43.py)
  is the only place a version's file format exists.

RULES (SOTA §3.1)
  * Every value is explicit. The reader resolves the source version's
    defaults, so nothing here depends on "what the runtime assumes when a
    field is missing" — that is exactly what differs between versions.
  * Vertex attachments (mesh, bounding box, clipping, path) store vertices
    as SETUP-POSE WORLD coordinates, with weights [[bone, w], ...] per
    vertex (None = rigidly bound to the slot's bone). The emitter converts
    to each bone's setup-local space. "Setup pose" means bone setup values
    WITHOUT constraints (the bind pose); see lbs.bind_pose.
    When a vertex's influences disagree about where it is (a file written
    with rounded local coordinates disagrees by ~0.01 px), every influence
    of that vertex carries its own world position, [bone, w, [x, y]], and
    the vertex position is their weighted blend. Keeping them makes
    read -> emit lossless; a generated rig never needs them.
  * Constraints are stored semantically: ik (bend_positive instead of a
    sign), transform (or the `half_angle` helper shorthand), path, physics
    (raw Spine parameters, or a `spring` {f, zeta} the emitter expands).
    `order` is the evaluation order. `runs` (default True) is False for a
    constraint the source file's runtime never runs: 4.2 runs one constraint
    per order value 0..N-1, so one in a gap, past N-1 or sharing an order
    with an earlier one is dead (the reader decides, see running()).
  * Animations are timelines with absolute bezier control points, the 4.x
    key layout, with every field explicit. Linear keys have curve None.

IN MEMORY one dict holds rig and animations; save() splits it into rig.json
and motion.json, load() merges them back. save() also writes `counts`
(what L2 COUNTS compares; `qa/run.mjs check --ir` prefers it). Optional
keys qa reads and nothing here requires: bones[].flip (G3 exception) and a
top-level foot_plant list (M1).

  rig = {
    "schema": "spinegen-rig/1", "name": str,
    "source": {"format", "spine", "warnings": [...]} | None,
    "skeleton": {"hash", "x", "y", "width", "height", "reference_scale",
                 "fps", "images", "audio"},
    "bones":  [{"name", "parent", "x", "y", "rotation", "scale_x", "scale_y",
                "shear_x", "shear_y", "inherit", "length", "skin_required",
                "role", "color", "icon", "visible"}],          # parents first
    "slots":  [{"name", "bone", "attachment", "color", "dark", "blend",
                "visible"}],                                 # = setup draw order
    "ik":        [{"name", "order", "runs", "skin_required", "bones", "target", "mix",
                   "softness", "bend_positive", "compress", "stretch", "uniform"}],
    "transform": [{"name", "order", "runs", "skin_required", "kind": "transform",
                   "bones", "target", "local", "relative",
                   "offset": {"rotation", "x", "y", "scale_x", "scale_y", "shear_y"},
                   "mix": {"rotate", "x", "y", "scale_x", "scale_y", "shear_y"}}
                  | {"name", "order", "kind": "half_angle", "bone", "target", "mix"}],
    "path":      [{"name", "order", "runs", "skin_required", "bones", "target",
                   "position_mode", "spacing_mode", "rotate_mode",
                   "offset_rotation", "position", "spacing",
                   "mix": {"rotate", "x", "y"}}],
    "physics":   [{"name", "order", "runs", "skin_required", "bone", "x", "y", "rotate",
                   "scale_x", "shear_x", "limit", "fps", "inertia", "strength",
                   "damping", "mass", "wind", "gravity", "mix",
                   "global": {"inertia", "strength", "damping", "mass", "wind",
                              "gravity", "mix"},
                   "spring": None | {"f", "zeta"}}],
    "skins": [{"name", "bones", "ik", "transform", "path", "physics", "color",
               "attachments": {slot: {key: attachment}}}],
    "events": [{"name", "int", "float", "string", "audio", "volume", "balance"}],
    "animations": [{"name", "timelines": [timeline]}],
  }

  attachment (always has "type", "name"):
    region      {"path", "x", "y", "rotation", "scale_x", "scale_y", "width",
                 "height", "color", "sequence"}
    mesh        {"path", "color", "width", "height", "uvs": [[u, v]],
                 "triangles": [[i, j, k]], "hull", "edges": [[i, j]] | None,
                 "vertices": [[x, y]], "weights", "sequence"}
    linkedmesh  {"path", "color", "width", "height", "parent", "skin",
                 "timelines", "sequence"}
    boundingbox {"color", "vertices", "weights"}
    clipping    {"color", "end", "vertices", "weights"}
    path        {"color", "closed", "constant_speed", "lengths", "vertices",
                 "weights"}
    point       {"x", "y", "rotation", "color"}

  timeline = {"type", "target", "property", "keys": [key]}
    type bone      property rotate|translate|translatex|translatey|scale|...|inherit
    type slot      property attachment|rgba|rgb|alpha|rgba2|rgb2
    type ik|transform        property = type
    type path      property position|spacing|mix
    type physics   property inertia|strength|damping|mass|wind|gravity|mix|reset
                   (target "" = every physics constraint)
    type deform|sequence     target {"skin", "slot", "attachment"}
    type draw_order|event    target None
  key = {"time", <fields of CHANNELS[(type, property)]>, <step fields>,
         "curve": None | "stepped" | [cx1, cy1, cx2, cy2] * channels}
    deform keys: {"time", "offsets": [..] | None, "curve"} — offsets are dense
      in the runtime's layout (per vertex, per influence, in that influence's
      bone-local axes; unweighted: the slot bone's axes); the curve is in
      percent space (0..1).
    draw_order keys: {"time", "order": [slot names] | None (= setup order)}.
"""
import copy
import json
import math
import os

SCHEMA_RIG = "spinegen-rig/1"
SCHEMA_MOTION = "spinegen-motion-ir/1"

INHERIT_MODES = ("normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection")
VERTEX_TYPES = ("mesh", "boundingbox", "clipping", "path")
CONSTRAINT_KINDS = ("ik", "transform", "path", "physics")  # the 4.2 runtime's tie-break order

# Curve channels per timeline, in curve order (4 control values per channel).
CHANNELS = {
    ("bone", "rotate"): ["value"],
    ("bone", "translate"): ["x", "y"], ("bone", "translatex"): ["value"], ("bone", "translatey"): ["value"],
    ("bone", "scale"): ["x", "y"], ("bone", "scalex"): ["value"], ("bone", "scaley"): ["value"],
    ("bone", "shear"): ["x", "y"], ("bone", "shearx"): ["value"], ("bone", "sheary"): ["value"],
    ("slot", "rgba"): ["r", "g", "b", "a"], ("slot", "rgb"): ["r", "g", "b"], ("slot", "alpha"): ["value"],
    ("slot", "rgba2"): ["r", "g", "b", "a", "r2", "g2", "b2"], ("slot", "rgb2"): ["r", "g", "b", "r2", "g2", "b2"],
    ("ik", "ik"): ["mix", "softness"],
    ("transform", "transform"): ["mix_rotate", "mix_x", "mix_y", "mix_scale_x", "mix_scale_y", "mix_shear_y"],
    ("path", "position"): ["value"], ("path", "spacing"): ["value"], ("path", "mix"): ["mix_rotate", "mix_x", "mix_y"],
    ("physics", "inertia"): ["value"], ("physics", "strength"): ["value"], ("physics", "damping"): ["value"],
    ("physics", "mass"): ["value"], ("physics", "wind"): ["value"], ("physics", "gravity"): ["value"],
    ("physics", "mix"): ["value"],
    ("deform", "deform"): ["percent"],
}
# Fields that hold without interpolation (the runtime steps them).
STEP_FIELDS = {
    ("bone", "inherit"): ["inherit"],
    ("slot", "attachment"): ["name"],
    ("ik", "ik"): ["bend_positive", "compress", "stretch"],
    ("physics", "reset"): [],
    ("deform", "deform"): ["offsets"],
    ("sequence", "sequence"): ["mode", "index", "delay"],
    ("draw_order", "draw_order"): ["order"],
    ("event", "event"): ["name", "int", "float", "string", "volume", "balance"],
}
SECTION_ORDER = ("bone", "slot", "ik", "transform", "path", "physics", "deform", "sequence", "draw_order", "event")


# ---------------------------------------------------------------- constructors

def new_rig(name="rig"):
    return {
        "schema": SCHEMA_RIG, "name": name, "source": None,
        "skeleton": {"hash": None, "x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0,
                     "reference_scale": 100.0, "fps": None, "images": None, "audio": None},
        "bones": [], "slots": [], "ik": [], "transform": [], "path": [], "physics": [],
        "skins": [], "events": [], "animations": [],
    }


def new_bone(name, parent=None, x=0.0, y=0.0, rotation=0.0, length=0.0, **kw):
    b = {"name": name, "parent": parent, "x": float(x), "y": float(y), "rotation": float(rotation),
         "scale_x": 1.0, "scale_y": 1.0, "shear_x": 0.0, "shear_y": 0.0, "inherit": "normal",
         "length": float(length), "skin_required": False, "role": None, "color": None, "icon": None,
         "visible": None}
    b.update(kw)
    return b


def new_slot(name, bone, attachment=None, **kw):
    s = {"name": name, "bone": bone, "attachment": attachment, "color": "ffffffff", "dark": None,
         "blend": "normal", "visible": None}
    s.update(kw)
    return s


def spring_to_spine(f, zeta, mass=1.0):
    """(f Hz, zeta) of a damped spring -> Spine physics (strength, damping).

    The 4.2 solver integrates v += -x*strength/mass*dt, then v *= damping^(60 dt),
    so omega = sqrt(strength/mass) and the decay rate is -60 ln(damping) per
    second, i.e. 2*zeta*omega (research/experiments/py/exp5.py, [E10]).
    """
    w = 2 * math.pi * f
    return mass * w * w, math.exp(-2 * zeta * w / 60.0)


def spine_to_spring(strength, damping, mass=1.0):
    w = math.sqrt(max(strength, 0.0) / mass)
    c = -60.0 * math.log(damping) if damping > 0 else math.inf
    return w / (2 * math.pi), (c / (2 * w) if w > 0 else math.inf)


# ---------------------------------------------------------------- lookups

def by_name(items):
    return {it["name"]: it for it in items}


def bone_index(ir):
    return {b["name"]: i for i, b in enumerate(ir["bones"])}


def find_skin(ir, name):
    for s in ir["skins"]:
        if s["name"] == name:
            return s
    return None


def default_skin(ir):
    return find_skin(ir, "default")


def iter_attachments(ir):
    """Yield (skin_name, slot_name, key, attachment) over every skin."""
    for skin in ir["skins"]:
        for slot, atts in skin["attachments"].items():
            for key, att in atts.items():
                yield skin["name"], slot, key, att


def resolve_attachment(ir, skin_name, slot, key):
    """The attachment a slot shows for `key`: the named skin, then default."""
    for name in (skin_name, "default"):
        skin = find_skin(ir, name) if name else None
        if skin and key in skin["attachments"].get(slot, {}):
            return skin["attachments"][slot][key]
    return None


def geometry_of(ir, slot, att):
    """The attachment that owns a linked mesh's vertices (itself otherwise)."""
    seen = 0
    while att is not None and att["type"] == "linkedmesh":
        parent = resolve_attachment(ir, att["skin"] or "default", slot, att["parent"])
        if parent is None:
            raise ValueError(f"linked mesh {att['name']!r} on {slot!r}: parent {att['parent']!r} not found")
        att = parent
        seen += 1
        if seen > 32:
            raise ValueError("linked mesh cycle")
    return att


def constraints(ir):
    """Every constraint as (kind, constraint), sorted by (order, kind, index).

    This includes constraints that never run (`runs` False); running() is
    the ones that do. The sort is the 4.2 tie-break (ik, transform, path,
    physics, then list position), so among the constraints that run it is
    their evaluation order, and the emitter's renumbering keeps it.
    """
    out = []
    for rank, kind in enumerate(CONSTRAINT_KINDS):
        for i, c in enumerate(ir[kind]):
            out.append(((c.get("order", 0), rank, i), kind, c))
    out.sort(key=lambda e: e[0])
    return [(kind, c) for _, kind, c in out]


def running(ir):
    """The constraints that run, in evaluation order (constraints() minus
    the ones marked `runs: False`)."""
    return [(kind, c) for kind, c in constraints(ir) if c.get("runs", True)]


def dead_by_order_42(ir):
    """Constraints the 4.2 runtime never runs because of their `order`.

    Skeleton.updateCache walks the order values 0..N-1 (N = all
    constraints) and takes, for each, the first constraint with that value
    in ik, transform, path, physics list order. A constraint whose order is
    past N-1 (a gap below it pushes it there) or repeats an order already
    taken is never sorted into the update cache: it does nothing and
    nothing reports it (E4). Returns [(kind, constraint)].
    """
    taken = set()
    n = sum(len(ir[k]) for k in CONSTRAINT_KINDS)
    for i in range(n):
        for kind in CONSTRAINT_KINDS:
            hit = next((c for c in ir[kind] if c.get("order", 0) == i), None)
            if hit is not None:
                taken.add(id(hit))
                break
    return [(kind, c) for kind, c in constraints(ir) if id(c) not in taken]


def duration(anim):
    t = 0.0
    for tl in anim["timelines"]:
        for k in tl["keys"]:
            t = max(t, k["time"])
    return t


def counts(ir):
    """What L2 COUNTS compares against the runtime."""
    return {
        "bones": len(ir["bones"]), "slots": len(ir["slots"]), "skins": len(ir["skins"]),
        "animations": len(ir.get("animations", [])), "events": len(ir["events"]),
        "ik": len(ir["ik"]), "transform": len(ir["transform"]), "path": len(ir["path"]),
        "physics": len(ir["physics"]),
        "meshes": sum(1 for *_, a in iter_attachments(ir) if a["type"] in ("mesh", "linkedmesh")),
    }


# ---------------------------------------------------------------- validation

def validate(ir):
    """Structural problems as strings (empty = fine). Cheap; run it on load."""
    errs = []
    names = set()
    for b in ir["bones"]:
        if b["parent"] is not None and b["parent"] not in names:
            errs.append(f"bone {b['name']}: parent {b['parent']!r} missing or after it")
        if b["inherit"] not in INHERIT_MODES:
            errs.append(f"bone {b['name']}: inherit {b['inherit']!r}")
        names.add(b["name"])
    slots = {s["name"] for s in ir["slots"]}
    for s in ir["slots"]:
        if s["bone"] not in names:
            errs.append(f"slot {s['name']}: bone {s['bone']!r} missing")
    for kind, c in constraints(ir):
        bones = c.get("bones") or [c.get("bone")]
        for b in bones:
            if b not in names:
                errs.append(f"{kind} {c['name']}: bone {b!r} missing")
        tgt = c.get("target")
        if kind == "path":
            if tgt not in slots:
                errs.append(f"path {c['name']}: slot {tgt!r} missing")
        elif kind != "physics" and tgt not in names:
            errs.append(f"{kind} {c['name']}: target {tgt!r} missing")
    for skin, slot, key, a in iter_attachments(ir):
        where = f"{skin}/{slot}/{key}"
        if slot not in slots:
            errs.append(f"{where}: slot missing")
        if a["type"] in VERTEX_TYPES:
            n = len(a["vertices"])
            w = a.get("weights")
            if w is not None:
                if len(w) != n:
                    errs.append(f"{where}: {len(w)} weight rows for {n} vertices")
                for i, row in enumerate(w):
                    if not row:
                        errs.append(f"{where}: vertex {i} has no influence")
                        continue
                    if abs(sum(inf[1] for inf in row) - 1.0) > 1e-4:
                        errs.append(f"{where}: vertex {i} weights sum {sum(inf[1] for inf in row):.6f}")
                    for inf in row:
                        if inf[0] not in names:
                            errs.append(f"{where}: vertex {i} bone {inf[0]!r} missing")
        if a["type"] == "mesh":
            n = len(a["vertices"])
            if len(a["uvs"]) != n:
                errs.append(f"{where}: {len(a['uvs'])} uvs for {n} vertices")
            for t in a["triangles"]:
                if any(i < 0 or i >= n for i in t):
                    errs.append(f"{where}: triangle {t} out of range")
                    break
    return errs


# ---------------------------------------------------------------- comparison

def diff(a, b, tol=1e-5, path=""):
    """Paths where two IR values differ; numbers within `tol` (absolute) are equal.

    Timelines compare by (type, target, property), not list position, so an
    emitter may group them by section without making two IRs "different".
    """
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'b' if k not in a else 'a'}")
            elif k == "timelines" and isinstance(a[k], list):
                ka = {_tl_key(t): t for t in a[k]}
                kb = {_tl_key(t): t for t in b[k]}
                if len(ka) != len(a[k]) or len(kb) != len(b[k]):
                    out.append(f"{path}.timelines: duplicate timeline")
                for tk in sorted(set(ka) | set(kb), key=str):
                    if tk not in ka or tk not in kb:
                        out.append(f"{path}.timelines[{tk}]: only in {'b' if tk not in ka else 'a'}")
                    else:
                        out += diff(ka[tk], kb[tk], tol, f"{path}.timelines[{tk}]")
            else:
                out += diff(a[k], b[k], tol, f"{path}.{k}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, tol, f"{path}[{i}]")
    elif _is_num(a) and _is_num(b):
        if not abs(a - b) <= tol:
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _tl_key(tl):
    t = tl["target"]
    if isinstance(t, dict):
        t = (t["skin"], t["slot"], t["attachment"])
    return (tl["type"], t, tl["property"])


# ---------------------------------------------------------------- files

def save(ir, rig_path, motion_path=None):
    """Write rig.json (+ counts, for qa's L2) and motion.json next to it."""
    if motion_path is None:
        motion_path = os.path.join(os.path.dirname(os.path.abspath(rig_path)), "motion.json")
    rig = {k: v for k, v in ir.items() if k != "animations"}
    rig["counts"] = counts(ir)  # derived; ignored by load
    rig["motion"] = os.path.relpath(os.path.abspath(motion_path), os.path.dirname(os.path.abspath(rig_path)))
    motion = {"schema": SCHEMA_MOTION, "rig": ir["name"], "animations": ir.get("animations", [])}
    _write_json(rig_path, rig)
    _write_json(motion_path, motion)
    return rig_path, motion_path


def load(rig_path, motion_path=None):
    with open(rig_path) as f:
        rig = json.load(f)
    if rig.get("schema") != SCHEMA_RIG:
        raise ValueError(f"{rig_path}: schema {rig.get('schema')!r}, expected {SCHEMA_RIG}")
    rig.pop("counts", None)
    rel = rig.pop("motion", None)
    if motion_path is None and rel:
        motion_path = os.path.join(os.path.dirname(os.path.abspath(rig_path)), rel)
    rig["animations"] = []
    if motion_path and os.path.exists(motion_path):
        with open(motion_path) as f:
            motion = json.load(f)
        if motion.get("schema") != SCHEMA_MOTION:
            raise ValueError(f"{motion_path}: schema {motion.get('schema')!r}, expected {SCHEMA_MOTION}")
        rig["animations"] = motion["animations"]
    return rig


def clone(ir):
    return copy.deepcopy(ir)


def _write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
