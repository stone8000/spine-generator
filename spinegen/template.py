"""Rig templates (`spinegen-template/1`): which bones a kind of character
has, where they go, how each joint bends, and how to recognise them in a
source skeleton.

    load(name_or_path) -> expanded template
    match_bones(source bones, template) -> ({template bone: source bone}, notes)
    tokens(name) -> ["arm", "l", "fore"]

WHY A TEMPLATE
  The rig builder must not know a character. Everything that is a decision
  about anatomy lives in templates/<name>.json: the bone tree, which side
  of a joint bends (a sleeve bends at the shoulder, the torso does not),
  where half-angle helpers and leg IK go, and the sign convention per side.
  The ingest step uses the same file to map a source skeleton's bones onto
  template roles, so that "armL", "upperarm_l" and "left_upper_arm" all
  become upperarm_l without a per-character table.

EXPANSION
  "{s}" in any name expands once per entry of "sides" (l, r). The result
  is flat: bones in template order (parents first), joints by name,
  helpers and ik per side, each entry carrying its "side" (None when
  unsided). Values given per side ({"l": .., "r": ..}) resolve to the
  side's value.
"""
import json
import os
import re

SCHEMA = "spinegen-template/1"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def load(name_or_path):
    """Expanded template from a name in templates/ or a JSON path."""
    path = name_or_path
    if not os.path.exists(str(path)):
        path = os.path.join(TEMPLATE_DIR, f"{name_or_path}.json")
    with open(path) as f:
        raw = json.load(f)
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} file (schema {raw.get('schema')!r})")
    return expand(raw)


def expand(raw):
    sides = raw.get("sides", {})
    out = {"schema": SCHEMA, "name": raw["name"], "sides": sides, "bones": [], "joints": {}, "helpers": [],
           "ik": [], "match_order": [], "foot_contact": raw.get("foot_contact"),
           "leg_style": raw.get("leg_style")}
    for b in raw["bones"]:
        for side in _sides_of(b["name"], sides):
            e = _sub(b, side)
            out["bones"].append({"name": e["name"], "side": side, "parents": _as_list(e.get("parent")),
                                 "joint": e.get("joint"), "tails": _as_list(e.get("tail")),
                                 "optional": bool(e.get("optional", False)), "kind": e.get("kind", "bone"),
                                 "match": [[t.lower() for t in m] for m in e.get("match", [])]})
    for name, j in raw.get("joints", {}).items():
        for side in _sides_of(name, sides):
            e = _sub(dict(j, name=name), side)
            out["joints"][e["name"]] = {"band": e.get("band", "none"), "helper": e.get("helper"),
                                        "budget": e.get("budget"), "side": side}
            for opt in ("reach", "rows", "row_points", "overlap", "compose", "power"):  # optional band shape (spinegen.rigbuild.joint_bands)
                if e.get(opt) is not None:
                    out["joints"][e["name"]][opt] = e[opt]
    for key in ("helpers", "ik"):
        for h in raw.get(key, []):
            for side in _sides_of(h["name"], sides):
                out[key].append(dict(_sub(h, side), side=side))
    for name in raw.get("match_order", [b["name"] for b in raw["bones"]]):
        out["match_order"] += [_fill(name, s) for s in _sides_of(name, sides)]
    names = [b["name"] for b in out["bones"]]
    if len(set(names)) != len(names):
        raise ValueError(f"template {raw['name']}: duplicate bone names")
    for b in out["bones"]:
        for p in b["parents"]:
            if p not in names or names.index(p) >= names.index(b["name"]):
                raise ValueError(f"template {raw['name']}: bone {b['name']} parent {p} must come before it")
    return out


def bone(t, name):
    for b in t["bones"]:
        if b["name"] == name:
            return b
    raise KeyError(name)


# ---------------------------------------------------------------- matching source bones

_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def tokens(name):
    """Lower-case name tokens: split at non-alphanumerics, digits and
    camelCase ("armL_fore" -> arm, l, fore; "upperArmLeft" -> upper, arm,
    left; "left_arm_0_upper_arm" -> left, arm, 0, upper, arm)."""
    out = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        out += [t.lower() for t in _CAMEL.findall(part)]
    return out


def side_of(name, t):
    """The template side whose tokens appear in `name` (None if none, or if
    tokens of two sides do)."""
    toks = set(tokens(name))
    hits = [s for s, spec in t["sides"].items() if toks & set(spec.get("tokens", []))]
    return hits[0] if len(hits) == 1 else None


def match_bones(source, t, exclude=()):
    """Map source bones onto template bones.

    source   [{"name", "parent"}] parents first (the IR bone list works)
    exclude  source bone names that may not take a role (e.g. the source's
             own half-angle helpers: the builder makes its own)
    Returns ({template bone: source bone}, notes). A source bone takes the
    first role in match_order that has a token set contained in its name
    tokens and the same side. When several source bones want one role, the
    one under the source bone its template parent took wins, then the one
    matching an earlier token set, then the one nearest the root (then the
    one with more descendants); the others stay unmatched (accessories) and
    are named in `notes`.
    """
    depth, kids = {}, {}
    for b in source:
        depth[b["name"]] = 0 if b.get("parent") is None else depth.get(b["parent"], 0) + 1
    for b in reversed(source):
        kids[b["name"]] = kids.get(b["name"], 0)
        if b.get("parent") is not None:
            kids[b["parent"]] = kids.get(b["parent"], 0) + 1 + kids[b["name"]]
    by_role = {}
    tb = {b["name"]: b for b in t["bones"]}
    for b in source:
        if b["name"] in exclude:
            continue
        toks, side = set(tokens(b["name"])), side_of(b["name"], t)
        for role in t["match_order"]:
            spec = tb[role]
            if spec["side"] != side or spec["kind"] != "bone":
                continue
            if any(set(m) <= toks for m in spec["match"]):
                by_role.setdefault(role, []).append(b["name"])
                break
    parent_of = {b["name"]: b.get("parent") for b in source}

    def ancestors(n):
        out = set()
        p = parent_of.get(n)
        while p is not None:
            out.add(p)
            p = parent_of.get(p)
        return out

    def match_index(role, n):
        toks = set(tokens(n))
        return next((k for k, m in enumerate(tb[role]["match"]) if set(m) <= toks), len(tb[role]["match"]))

    out, notes = {}, []
    # roles in template order (parents first), so a role can ask whether a
    # candidate hangs below the source bone its template parent took: the
    # Hard Time warden has a root-parented IK target "knee_l" next to the
    # real shin "shin_l" (under the thigh), and "waist" above "chest" (the
    # bone its arms hang from); nearest-the-root alone took the target and
    # the waist (P5)
    for role in [b["name"] for b in t["bones"] if b["name"] in by_role]:
        cands = by_role[role]
        parents = [out[p] for p in tb[role]["parents"] if p in out]

        def key(n):
            under = not parents or any(p in ancestors(n) for p in parents)
            return (not under, match_index(role, n), depth[n], -kids[n], n)
        cands.sort(key=key)
        out[role] = cands[0]
        if len(cands) > 1:
            notes.append(f"{role}: took {cands[0]!r}; {cands[1:]} also matched and stay accessories")
    return out, notes


# ---------------------------------------------------------------- helpers

def _sides_of(name, sides):
    return list(sides) if "{s}" in name else [None]


def _fill(s, side):
    return s.replace("{s}", side) if side is not None else s


def _sub(obj, side):
    """Expand {s} in every string and pick per-side values ({"l": .., "r": ..})."""
    if isinstance(obj, str):
        return _fill(obj, side)
    if isinstance(obj, list):
        return [_sub(v, side) for v in obj]
    if isinstance(obj, dict):
        if side is not None and obj and set(obj) <= {"l", "r"} and side in obj:
            return _sub(obj[side], side)
        return {k: _sub(v, side) for k, v in obj.items()}
    return obj


def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]
