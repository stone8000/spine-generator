"""SOTA P1 (6): a rig read into the IR and re-emitted draws the same in spine-core.

    python -m spinegen.reemit_check x.json x.atlas [--out DIR] [--fps 30]
        [--physics none|update] [--tol 0.01] [--gates] [--upgrade-legacy] [--json]

WHY NOT "SAME BYTES"
  The emitter changes a file on purpose: it moves each mesh's outline to the
  front (hull first), renumbers constraint `order` (a constraint that never
  ran is written inert, so it still does nothing), and writes out every
  default. So "the same rig" means what a player shows: spine-core draws the
  same triangles, with the same UVs, at the same world positions, in every
  sampled frame. This tool checks exactly that, in three parts:

  topology   from the two FILES (not the IR, so the check cannot pass by
             comparing the IR with itself): for every mesh, emitted vertex i
             is original vertex perm[i] (perm from the emitter's report), its
             UV is the same, and the triangle list maps back to the original
             list index for index, in the same order and winding. Same
             triangles + same UVs + same world positions = same pixels.
  world      `qa/run.mjs dump` of both files, setup pose and every animation
             on the --fps grid: bone matrices compared directly, slot world
             vertices compared per vertex through perm.
  gates      (--gates) `qa/run.mjs check` on both files, the emitted one with
             --ir (the IR's counts), side by side; check measures each
             file's C1 itself.

The reader's warnings are printed: keys spine-core 4.2 ignores (and so
does the reader) and constraints that never ran. --upgrade-legacy reads
those old keys as their 4.2 meaning instead; that is a deliberate behaviour
change, so each one is listed and a world difference is expected (the
verdict says so; the exit code still reports it).

Exit 0 when the topology corresponds and the largest world difference is
within --tol (0.01 px), 1 otherwise, 2 when the oracle is not installed.
"""
import argparse
import json
import os
import sys
import tempfile

import numpy as np

from . import ir as irmod
from . import oracle
from .emit import spine42
from .spine42_reader import behaviour_changes, read


def _skin_maps(raw):
    """{skin: {slot: {key: attachment map}}} for list (4.x) and dict (pre-4.0) skins."""
    skins = raw.get("skins") or []
    if isinstance(skins, dict):
        return {name: atts or {} for name, atts in skins.items()}
    return {s.get("name", "default"): s.get("attachments") or {} for s in skins}


def emit_copy(skeleton, out_dir, upgrade_legacy=False):
    """Read `skeleton`, emit it into out_dir; returns (json path, rig.json path, emitter report).

    The report also carries the reader's view: "reader_warnings" (everything
    it noted) and "behaviour_changes" (only with upgrade_legacy)."""
    a = read(skeleton, upgrade_legacy=upgrade_legacy)
    report = {"reader_warnings": list(a["source"]["warnings"]), "behaviour_changes": behaviour_changes(a)}
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.basename(skeleton))
    spine42.write(a, out, report=report)
    rig, _ = irmod.save(a, os.path.join(out_dir, "rig.json"))
    return out, rig, report


def perm_table(report):
    """{(skin, slot, key): perm} of the meshes the emitter re-ordered (perm[new] = old)."""
    return {(h["skin"], h["slot"], h["attachment"]): h["perm"] for h in report["hull"] if h["reordered"]}


def topology(original, emitted, perms):
    """Mesh correspondence between the two files through perm.

    Returns {"meshes", "reordered", "uv_max", "problems": [...]}: uv_max is
    the largest UV difference of corresponding vertices (the emitter writes 6
    decimals, so <= 5e-7); a problem is any mesh whose vertex count,
    triangle list or hull does not correspond.
    """
    with open(original) as f:
        a = _skin_maps(json.load(f))
    with open(emitted) as f:
        b = _skin_maps(json.load(f))
    out = {"meshes": 0, "reordered": 0, "uv_max": 0.0, "problems": []}
    for skin, slots in a.items():
        for slot, atts in slots.items():
            for key, ma in atts.items():
                if ma.get("type") != "mesh":
                    continue  # linked meshes share their parent's geometry
                where = f"{skin}/{slot}/{key}"
                mb = b.get(skin, {}).get(slot, {}).get(key)
                if mb is None or mb.get("type") != "mesh":
                    out["problems"].append(f"{where}: missing from the emitted file")
                    continue
                out["meshes"] += 1
                uva = np.asarray(ma["uvs"], float).reshape(-1, 2)
                uvb = np.asarray(mb["uvs"], float).reshape(-1, 2)
                perm = perms.get((skin, slot, key))
                if perm is None:
                    perm = list(range(len(uvb)))
                else:
                    out["reordered"] += 1
                if len(uva) != len(uvb) or sorted(perm) != list(range(len(uva))):
                    out["problems"].append(f"{where}: {len(uva)} vs {len(uvb)} vertices, or perm is not a permutation")
                    continue
                out["uv_max"] = max(out["uv_max"], float(np.abs(uvb - uva[perm]).max()))
                tb = [perm[v] for v in mb["triangles"]]
                if tb != list(ma["triangles"]):
                    diff = sum(x != y for x, y in zip(tb, ma["triangles"])) + abs(len(tb) - len(ma["triangles"]))
                    out["problems"].append(f"{where}: {diff} triangle indices do not map back to the original")
                if not mb.get("hull", 0) > 0:
                    out["problems"].append(f"{where}: emitted hull {mb.get('hull')}")
    return out


def _slot_perms(emitted, perms, skin):
    """{(slot, attachment name): perm} as dump reports slots: a linked mesh
    shows its parent's vertices, so it takes the parent's perm."""
    with open(emitted) as f:
        maps = _skin_maps(json.load(f))
    out = {}
    for sk, slots in maps.items():
        if sk != (skin or "default"):
            continue
        for slot, atts in slots.items():
            for key, m in atts.items():
                src = (sk, slot, key)
                if m.get("type") == "linkedmesh":
                    src = (m.get("skin") or "default", slot, m["parent"])
                if src in perms:
                    out[(slot, m.get("name", key))] = perms[src]
    return out


def compare_dumps(ref, test, slot_perms):
    """Largest bone and vertex difference between two dumps of the same playback.

    Returns {"frames", "max_px", "bone_px", "bone_m", "worst", "mismatch"}.
    """
    out = {"frames": 0, "max_px": 0.0, "bone_px": 0.0, "bone_m": 0.0, "worst": None, "mismatch": []}
    if len(ref["frames"]) != len(test["frames"]):
        out["mismatch"].append(f"{len(ref['frames'])} vs {len(test['frames'])} frames")
        return out
    for fa, fb in zip(ref["frames"], test["frames"]):
        out["frames"] += 1
        for name, ba in fa["bones"].items():
            bb = fb["bones"][name]
            out["bone_px"] = max(out["bone_px"], float(np.hypot(ba["x"] - bb["x"], ba["y"] - bb["y"])))
            out["bone_m"] = max(out["bone_m"], max(abs(ba[k] - bb[k]) for k in "abcd"))
        for slot, sa in fa["slots"].items():
            sb = fb["slots"][slot]
            if sa["attachment"] != sb["attachment"] or len(sa["vertices"]) != len(sb["vertices"]):
                out["mismatch"].append(f"{slot} at t={fa['t']}: {sa['attachment']!r} vs {sb['attachment']!r}")
                continue
            if not sa["vertices"]:
                continue
            va = np.asarray(sa["vertices"], float).reshape(-1, 2)
            vb = np.asarray(sb["vertices"], float).reshape(-1, 2)
            perm = slot_perms.get((slot, sa["attachment"]))
            if perm is not None:
                back = np.empty_like(vb)
                back[perm] = vb  # emitted vertex i is original vertex perm[i]
                vb = back
            d = np.hypot(*(va - vb).T)
            i = int(d.argmax())
            if d[i] > out["max_px"]:
                out["max_px"] = float(d[i])
                out["worst"] = {"anim": fa["anim"], "t": fa["t"], "slot": slot, "vertex": i}
    return out


def world(original, emitted, atlas, slot_perms, fps=30, physics="none", anims=None):
    """Per-animation world comparison ({name or "setup": compare_dumps(...)})."""
    if anims is None:
        anims = [a["name"] for a in read(original)["animations"]]
    out = {"setup": compare_dumps(oracle.dump(original, atlas), oracle.dump(emitted, atlas), slot_perms)}
    for anim in anims:
        ra = oracle.dump(original, atlas, anim=anim, fps=fps, physics=physics)
        rb = oracle.dump(emitted, atlas, anim=anim, fps=fps, physics=physics)
        out[anim] = compare_dumps(ra, rb, slot_perms)
    return out


def gates(original, emitted, atlas, rig):
    """qa check on both files: ({gate id: gate} original, same for emitted).

    The emitted file gets --ir (its counts). check measures C1 on each file
    itself (setup and every animation)."""
    _, ra = oracle.check(original, atlas)
    _, rb = oracle.check(emitted, atlas, ir=rig)
    return ({g["id"]: g for g in ra["gates"]}, {g["id"]: g for g in rb["gates"]})


def summary(world_rep):
    keys = ("max_px", "bone_px", "bone_m")
    out = {k: max(r[k] for r in world_rep.values()) for k in keys}
    out["frames"] = sum(r["frames"] for r in world_rep.values())
    out["mismatch"] = [m for r in world_rep.values() for m in r["mismatch"]]
    worst = max(world_rep.values(), key=lambda r: r["max_px"])
    out["worst"] = worst["worst"]
    return out


def _gate_cell(g):
    if g is None:
        return "-"
    if g.get("skipped"):
        return "skip"
    tag = "pass" if g["pass"] else ("FAIL" if g.get("hard", True) else "warn")
    v = g["value"]
    return f"{tag} {v:.4g}" if isinstance(v, float) else f"{tag} {v}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="P1 (6): IR round trip through emit/spine42, judged by spine-core")
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("--out", help="directory for the emitted file and rig.json (default: a temporary one)")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--physics", choices=["none", "update", "reset"], default="none")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--gates", action="store_true", help="also run qa check on both files")
    ap.add_argument("--upgrade-legacy", action="store_true",
                    help="read pre-4.2 keys that 4.2 ignores as their 4.2 meaning (a deliberate behaviour change)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ok, why = oracle.available()
    if not ok:
        print(f"oracle unavailable: {why}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="spinegen-reemit-") as tmp:
        work = args.out or tmp
        os.makedirs(work, exist_ok=True)
        emitted, rig, report = emit_copy(args.skeleton, work, args.upgrade_legacy)
        perms = perm_table(report)
        topo = topology(args.skeleton, emitted, perms)
        wrep = world(args.skeleton, emitted, args.atlas, _slot_perms(emitted, perms, None), args.fps, args.physics)
        tot = summary(wrep)
        ga = gb = None
        if args.gates:
            ga, gb = gates(args.skeleton, emitted, args.atlas, rig)
    ok = not topo["problems"] and not tot["mismatch"] and tot["max_px"] <= args.tol
    if args.json:
        print(json.dumps({"pass": ok, "topology": topo, "world": wrep, "total": tot, "emitter": {
            "hull": [{k: h[k] for k in ("skin", "slot", "attachment", "hull", "reordered")} for h in report["hull"]],
            "orders": report["orders"], "inert": report["inert"], "warnings": report["warnings"]},
            "reader": {"warnings": report["reader_warnings"], "behaviour_changes": report["behaviour_changes"]},
            "gates": None if ga is None else {"original": ga, "emitted": gb}}, indent=1))
        return 0 if ok else 1
    print(f"emitted {emitted if args.out else '(temporary)'}: {len(report['hull'])} meshes re-hulled "
          f"({topo['reordered']} re-ordered), {len(report['orders'])} constraint orders renumbered")
    for w in report["reader_warnings"]:
        print("  reader:", w)
    for w in report["warnings"]:
        print("  emitter:", w)
    print(f"topology: {topo['meshes']} meshes, triangles map back index for index: "
          f"{'yes' if not topo['problems'] else 'NO'}; max UV diff {topo['uv_max']:.1e}")
    for p in topo["problems"][:20]:
        print("  problem:", p)
    for name, r in wrep.items():
        print(f"  {name:<12} {r['frames']:>4} frames  max vertex {r['max_px']:.2e} px  "
              f"bone origin {r['bone_px']:.2e} px  matrix {r['bone_m']:.2e}")
    w = tot["worst"] or {}
    print(f"P1(6) {'PASS' if ok else 'FAIL'}: max world vertex diff {tot['max_px']:.2e} px over {tot['frames']} frames "
          f"(tol {args.tol}, {args.fps:g} fps, physics {args.physics}); worst {w.get('anim')} t={w.get('t')} "
          f"{w.get('slot')}#{w.get('vertex')}")
    for m in tot["mismatch"][:20]:
        print("  mismatch:", m)
    if report["behaviour_changes"] and not ok:
        print(f"  expected: --upgrade-legacy made {len(report['behaviour_changes'])} deliberate behaviour change(s) "
              "(listed above as 'reader: behaviour change'), so the emitted file is not meant to play the same")
    if ga is not None:
        print(f"{'gate':<20} {'original':<10} emitted")
        for gid in gb:
            print(f"{gid:<20} {_gate_cell(ga.get(gid)):<10} {_gate_cell(gb[gid])}")
            if not gb[gid]["pass"] and gb[gid].get("hard", True):
                for item in gb[gid].get("items", [])[:8]:
                    print(f"{'':<31}- {item}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
