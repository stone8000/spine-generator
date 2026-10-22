"""spinegen rig -> Spine 4.3 -> the motion kit's presets -> QA on spine-core 4.3.13.

    python -m spinegen.bridge_motionkit <rig.json> <atlas> -o <outdir>
        [--presets fighter-idle-bounce,punch,celebrate,hit-react] [--facing 1|-1]
        [--name NAME] [--arm-softness 0.1] [--no-qa]

WHY
  spinegen builds rigs that hold big angles (meshes across the joints,
  half-angle helpers, planted leg IK); motionkit/ makes motion that reads
  well on screen (Iron Jaw-measured presets, screen-space retargeting,
  physics bake, IK guard, smoothness check) but only for Spine 4.3. This is
  the seam between them, so a spinegen character gets the kit's motion and
  both judges see the result: the kit's smoothness check and qa's gates on
  the official 4.3.13 runtime.

STEPS (each writes into <outdir>; nothing goes next to the input)
  1. ARM IK, added at emit time and only here: the kit's punch, celebrate
     and idle move the FISTS as IK targets on screen, and a spinegen rig's
     arms are FK. Per side with a template shoulder and elbow (bone roles
     `arm_<s>.shoulder`, `arm_<s>.elbow`): a target `ik_hand_<s>` under the
     root at the forearm's tip (the wrist), a two-bone IK upper arm ->
     forearm onto it, ordered right after the rig's other IK so the
     half-angle helpers and physics read the solved arm. bend direction
     from the SETUP elbow (the sign of the forearm's local angle, which is
     what keeps the setup pose; an arm drawn straight takes the template's
     outward rule); softness = `arm_softness` x arm length, capped under the
     setup slack like the legs (rigbuild SOFT_GUARD) so the setup pose does
     not move. The rig's own animations are dropped: they were keyed FK and
     an arm IK at mix 1 would pin those arms (the kit's presets replace
     them).
  2. emit.spine43 -> <name>.rig43.json, with the atlas and its page copied
     beside it.
  3. A recipe from the TEMPLATE ROLES (not the kit's name guessing):
       hip/body <- `hips`, chest <- `torso`, head <- `head`,
       fists <- the arm IK targets (front first: further along `facing`),
       shoulders <- the shoulder bones, shoulderBack <- the one behind,
       hair <- accessory chains under the head, chain <- other accessory
       chains; an accessory the IR already drives with runtime physics is
       left to it (baking the kit's hair physics on top would move it twice).
     size is the kit's own estimate (`spine-motion init`), so preset
     distances follow the rig exactly as the kit would scale them.
  4. node motionkit/bin/spine-motion.js generate <rig43> <recipe> --check
     (npm ci in motionkit/ first when its runtime is missing), plus
     `check --json` for the per-animation scores.
  5. qa/run.mjs check --runtime 4.3 with an IR summary (counts, constraint
     order, planted feet for M1: the kit never lifts a foot target, so every
     preset animation plants both feet for its whole length).
  bridge.json in <outdir> holds every number of every step.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

from . import ir as irmod
from . import lbs
from .emit import spine43

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(REPO, "motionkit")
QA_RUN = os.path.join(REPO, "qa", "run.mjs")
DEFAULT_PRESETS = ("fighter-idle-bounce", "punch", "celebrate", "hit-react")
ANIM_NAMES = {"fighter-idle-bounce": "idle", "fighter-idle-weave": "idle_weave", "punch": "punch",
              "celebrate": "celebrate", "hit-react": "hit", "breathe": "breathe", "jump": "jump"}
SOFT_GUARD = 0.999  # as rigbuild: softness stays this fraction under the setup slack
SETUP_TOL = 0.01    # px: the arm IK may not move the setup pose more than this


# ---------------------------------------------------------------- arm IK

def _by_role(ir):
    return {b.get("role"): b["name"] for b in ir["bones"] if b.get("role")}


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def add_arm_ik(ir, softness=0.1):
    """A copy of `ir` with a two-bone IK per arm (see the module doc, step 1).

    Returns (ir, report): report["arms"][side] = {upper, fore, target, target_xy,
    bend_positive, softness, softness_wanted, slack, setup_error_px}."""
    out = irmod.clone(ir)
    roles = _by_role(out)
    root = next(b["name"] for b in out["bones"] if b["parent"] is None)
    bind = lbs.bind_pose(out)
    names = {b["name"] for b in out["bones"]}
    report = {"arms": {}, "notes": []}
    new_ik = []
    for side in ("l", "r"):
        upper, fore = roles.get(f"arm_{side}.shoulder"), roles.get(f"arm_{side}.elbow")
        if not (upper and fore):
            report["notes"].append(f"side {side}: no shoulder/elbow roles; no arm IK")
            continue
        bu = next(b for b in out["bones"] if b["name"] == upper)
        bf = next(b for b in out["bones"] if b["name"] == fore)
        if bf["parent"] != upper:
            report["notes"].append(f"side {side}: {fore} is not a child of {upper}; no arm IK")
            continue
        target = f"ik_hand_{side}"
        if target in names:
            report["notes"].append(f"side {side}: {target} already exists; no arm IK")
            continue
        tip = lbs.to_world(bind[fore], bf["length"], 0.0)
        shoulder = bind[upper][4:6]
        l1 = math.hypot(*(lbs.to_world(bind[upper], 0, 0)[k] - bind[fore][4 + k] for k in (0, 1)))
        l2 = bf["length"] * bf["scale_x"]
        slack = max(0.0, l1 + l2 - math.hypot(tip[0] - shoulder[0], tip[1] - shoulder[1]))
        want = softness * (l1 + l2)
        soft = min(want, slack * SOFT_GUARD)
        # the forearm's local angle IS the IK's a2 (child offset on the parent's x axis); a mirrored
        # parent turns it the other way
        flip = (1 if bu["scale_x"] >= 0 else -1) * (1 if bu["scale_y"] >= 0 else -1)
        a2 = _wrap(bf["rotation"]) * flip
        bend = a2 > 0 if abs(a2) > 1e-6 else True
        out["bones"].append(irmod.new_bone(target, root, *lbs.to_local(bind[root], *tip), role="ik_target"))
        names.add(target)
        new_ik.append({"name": f"arm_ik_{side}", "order": 0, "runs": True, "skin_required": False,
                       "bones": [upper, fore], "target": target, "mix": 1.0, "softness": soft,
                       "bend_positive": bool(bend), "compress": False, "stretch": False, "uniform": False})
        report["arms"][side] = {"upper": upper, "fore": fore, "target": target, "target_xy": [tip[0], tip[1]],
                                "bend_positive": bool(bend), "elbow_local_deg": round(a2, 3),
                                "softness": round(soft, 4), "softness_wanted": round(want, 3),
                                "slack": round(slack, 4), "arm_length": round(l1 + l2, 3)}
    if not new_ik:
        return out, report
    # right after the last IK that runs: the helpers and physics then read the solved arm
    order = irmod.constraints(out)
    last_ik = max((i for i, (k, c) in enumerate(order) if k == "ik"), default=-1)
    seq = [c for _, c in order[:last_ik + 1]] + new_ik + [c for _, c in order[last_ik + 1:]]
    out["ik"] += new_ik
    for i, c in enumerate(seq):
        c["order"] = i
    report["setup_error_px"] = setup_error(ir, out)
    if report["setup_error_px"] > SETUP_TOL:
        report["notes"].append(f"arm IK moves the setup pose by {report['setup_error_px']:.4f} px")
    return out, report


def setup_error(a, b):
    """Largest setup-pose world move of any bone `a` has, constraints on (lbs, 4.2 semantics;
    equiv43 shows 4.3 agrees on these rigs)."""
    wa = lbs.Evaluator(a).world()
    eb = lbs.Evaluator(b)
    wb = eb.world_dict(eb.world())
    worst = 0.0
    for i, bone in enumerate(a["bones"]):
        m, n = wa[i], wb[bone["name"]]
        worst = max(worst, math.hypot(m[4] - n[4], m[5] - n[5]),
                    100 * max(abs(m[k] - n[k]) for k in range(4)))  # matrix entries as px at 100 px
    return worst


# ---------------------------------------------------------------- roles and recipe

def _chains(ir, bones):
    """Parent -> child chains among `bones` (a set of names), roots first."""
    parent = {b["name"]: b["parent"] for b in ir["bones"]}
    kids = {}
    for b in ir["bones"]:
        if b["name"] in bones and parent[b["name"]] in bones:
            kids.setdefault(parent[b["name"]], []).append(b["name"])
    out = []
    for b in ir["bones"]:
        n = b["name"]
        if n not in bones or parent[n] in bones:
            continue
        chain = [n]
        while kids.get(chain[-1]):
            chain.append(kids[chain[-1]][0])
        out.append(chain)
    return out


def roles_from_template(ir, arm_report, facing=1):
    """The kit's role map from the IR's template roles (module doc, step 3)."""
    roles, notes = {}, []
    by = _by_role(ir)
    root = next(b["name"] for b in ir["bones"] if b["parent"] is None)
    roles["root"] = root
    for kit, tmpl in (("hip", "hips"), ("body", "hips"), ("chest", "torso"), ("head", "head")):
        if by.get(tmpl):
            roles[kit] = by[tmpl]
        elif kit != "body":
            notes.append(f"no template bone '{tmpl}' for the kit's role '{kit}'")
    bind = lbs.bind_pose(ir)
    fists = [a["target"] for a in arm_report["arms"].values()]
    if fists:
        xs = {a["target"]: a["target_xy"][0] for a in arm_report["arms"].values()}
        fists.sort(key=lambda f: -facing * xs[f])
        roles["fists"] = fists
        roles["fist"] = roles["fistFront"] = fists[0]
        if len(fists) > 1:
            roles["fistBack"] = fists[1]
    shoulders = [by[f"arm_{s}.shoulder"] for s in ("l", "r") if by.get(f"arm_{s}.shoulder")]
    if shoulders:
        roles["shoulders"] = shoulders
        roles["shoulderBack"] = min(shoulders, key=lambda s: facing * bind[s][4])
    physics = {c["bone"] for c in ir["physics"]}
    acc = {b["name"] for b in ir["bones"] if b.get("role") == "accessory"}
    left = sorted(acc & physics)
    if left:
        notes.append(f"accessories with runtime physics in the IR, left to it: {', '.join(left)}")
    head = by.get("head")
    hair, other = [], []
    for ch in _chains(ir, acc - physics):
        root_parent = next(b["parent"] for b in ir["bones"] if b["name"] == ch[0])
        (hair if root_parent == head else other).append(ch)
    if hair:
        roles["hair"] = max(hair, key=len)
    if other:
        roles["chain"] = max(other, key=len)
    return roles, notes


def make_recipe(roles, presets, size, facing=1):
    anims = []
    for p in presets:
        a = {"name": ANIM_NAMES.get(p, p.replace("-", "_")), "preset": p}
        if p.startswith("fighter-idle"):
            a["params"] = {"hair": "keys" if roles.get("hair") else "none"}
        anims.append(a)
    return {"fps": 30, "facing": facing, "size": size, "ikGuard": True, "roles": roles, "animations": anims}


# ---------------------------------------------------------------- node steps

def ensure_kit():
    """npm ci in motionkit/ when its pinned runtime is missing."""
    if not os.path.isdir(os.path.join(KIT, "node_modules", "@esotericsoftware", "spine-core")):
        subprocess.run(["npm", "ci"], cwd=KIT, check=True, capture_output=True, text=True)


def kit(*args, cwd=None):
    p = subprocess.run(["node", os.path.join(KIT, "bin", "spine-motion.js"), *args], capture_output=True,
                       text=True, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"spine-motion {args[0]} failed ({p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def kit_size(rig43):
    """The kit's own size estimate for this rig (`spine-motion init`)."""
    tmp = rig43[:-5] + ".init.recipe.json"
    kit("init", rig43, "-o", tmp, "--force")
    with open(tmp) as f:
        guess = json.load(f)
    os.remove(tmp)
    return guess["size"], guess["roles"]


def qa_ir(ir, motion_json):
    """What qa's --ir reads, for the generated file: counts (the kit's
    animations replace the rig's), constraint order, planted feet."""
    with open(motion_json) as f:
        skel = json.load(f)
    anims = skel.get("animations", {})
    counts = irmod.counts(dict(ir, animations=[]))
    counts["animations"] = len(anims)
    counts["events"] = len(skel.get("events", {}))  # the kit's swing / impact / hit
    feet = sorted({fp["bone"] for fp in ir.get("foot_plant", [])})
    plants = [{"bone": b, "anim": a} for a in anims for b in feet]
    return {"counts": counts, "foot_plant": plants,
            **{k: [{"name": c["name"], "order": c["order"]} for c in ir[k]] for k in ("ik", "transform", "path", "physics")}}


def qa_check(motion_json, atlas, ir_summary_path, out_path):
    p = subprocess.run(["node", QA_RUN, "check", "--runtime", "4.3", "--skeleton", motion_json, "--atlas", atlas,
                        "--ir", ir_summary_path, "--out", out_path], capture_output=True, text=True)
    if p.returncode not in (0, 1):
        raise RuntimeError(f"qa check failed ({p.returncode}): {p.stderr.strip()}")
    with open(out_path) as f:
        return json.load(f), p.stderr


# ---------------------------------------------------------------- the bridge

def run(rig_path, atlas, out_dir, presets=DEFAULT_PRESETS, facing=1, name=None, arm_softness=0.1, qa=True):
    ir = irmod.load(rig_path)
    name = name or ir.get("name") or "rig"
    os.makedirs(out_dir, exist_ok=True)
    rep = {"input": {"rig": os.path.abspath(rig_path), "atlas": os.path.abspath(atlas)}, "name": name,
           "presets": list(presets), "facing": facing}

    bridged, arms = add_arm_ik(ir, arm_softness)
    rep["arm_ik"] = arms
    dropped = [a["name"] for a in bridged.get("animations", [])]
    bridged["animations"] = []
    rep["dropped_animations"] = dropped
    irmod.save(bridged, os.path.join(out_dir, f"{name}.bridge_rig.json"), os.path.join(out_dir, f"{name}.bridge_motion.json"))

    rig43 = os.path.join(out_dir, f"{name}.rig43.json")
    emit_rep = {}
    spine43.write(bridged, rig43, report=emit_rep)
    rep["emit43"] = {k: emit_rep[k] for k in ("warnings", "defaults", "half_angle", "differs")}
    atlas_out = os.path.join(out_dir, f"{name}.atlas")
    shutil.copyfile(atlas, atlas_out)
    for page in _atlas_pages(atlas):
        shutil.copyfile(os.path.join(os.path.dirname(atlas), page), os.path.join(out_dir, page))

    ensure_kit()
    size, guessed = kit_size(rig43)
    roles, notes = roles_from_template(bridged, arms, facing)
    recipe = make_recipe(roles, presets, size, facing)
    recipe_path = os.path.join(out_dir, f"{name}.recipe.json")
    with open(recipe_path, "w") as f:
        json.dump(recipe, f, indent=1, ensure_ascii=False)
    rep["recipe"] = {"path": recipe_path, "size": size, "roles": roles, "notes": notes, "kit_guessed_roles": guessed}

    motion = os.path.join(out_dir, f"{name}.motion.json")
    rep["generate_stdout"] = kit("generate", rig43, recipe_path, "-o", motion, "--check")
    loops = [a["name"] for a in recipe["animations"] if a["preset"] in ("fighter-idle-bounce", "fighter-idle-weave", "celebrate", "breathe")]
    checks = json.loads(kit("check", motion, "--loops", ",".join(loops), "--json"))
    rep["smoothness"] = [{"name": r["name"], "score": r["score"], "loop": r["loop"], "keys": r["keyCount"],
                          "errors": sum(i["severity"] == "error" for i in r["issues"]),
                          "warnings": sum(i["severity"] == "warn" for i in r["issues"]),
                          "issues": r["issues"]} for r in checks]
    if qa:
        summ = os.path.join(out_dir, f"{name}.qa_ir.json")
        with open(summ, "w") as f:
            json.dump(qa_ir(bridged, motion), f, indent=1)
        report, text = qa_check(motion, atlas_out, summ, os.path.join(out_dir, f"{name}.qa43.json"))
        rep["qa"] = {"pass": report["pass"], "runtime": report["runtime"],
                     "gates": {g["id"]: {k: g.get(k) for k in ("pass", "value", "max", "hard", "skipped", "note", "worst")}
                               for g in report["gates"]}, "text": text}
    rep["files"] = {"rig43": rig43, "recipe": recipe_path, "motion": motion, "atlas": atlas_out,
                    "check_md": motion[:-5] + ".check.md"}
    with open(os.path.join(out_dir, "bridge.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    return rep


def _atlas_pages(atlas):
    """Page file names of a Spine atlas (a line ending in an image extension, before its `size:`)."""
    pages = []
    with open(atlas) as f:
        for line in f:
            s = line.strip()
            if s and ":" not in s and s.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                pages.append(s)
    return pages


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig", help="IR rig.json (motion.json beside it)")
    ap.add_argument("atlas")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--presets", default=",".join(DEFAULT_PRESETS))
    ap.add_argument("--facing", type=int, default=1, choices=(1, -1))
    ap.add_argument("--name", default=None)
    ap.add_argument("--arm-softness", type=float, default=0.1)
    ap.add_argument("--no-qa", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.rig, args.atlas, args.out, [p.strip() for p in args.presets.split(",") if p.strip()],
              args.facing, args.name, args.arm_softness, not args.no_qa)
    for side, a in rep["arm_ik"]["arms"].items():
        print(f"arm IK {side}: {a['upper']} -> {a['fore']} onto {a['target']}, bendPositive {a['bend_positive']}, "
              f"softness {a['softness']} (wanted {a['softness_wanted']}, slack {a['slack']})")
    print(f"setup moved by the arm IK: {rep['arm_ik'].get('setup_error_px', 0):.2e} px")
    for n in rep["arm_ik"]["notes"] + rep["recipe"]["notes"]:
        print("note:", n)
    print(f"recipe: size {rep['recipe']['size']}, roles {json.dumps(rep['recipe']['roles'], ensure_ascii=False)}")
    for s in rep["smoothness"]:
        print(f"  {s['name']:10s} score {s['score']:3d}  errors {s['errors']}  warnings {s['warnings']}  keys {s['keys']}")
    if "qa" in rep:
        print(rep["qa"]["text"].rstrip())
    print(f"outputs in {args.out}")
    return 0 if rep.get("qa", {}).get("pass", True) else 1


if __name__ == "__main__":
    sys.exit(main())
