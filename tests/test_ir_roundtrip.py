"""IR <-> Spine 4.2 JSON: lossless roundtrip and the emitter's rules (SOTA §3.1)."""
import copy
import math
import random

from conftest import strip_source

from spinegen import ir as irmod
from spinegen import lbs, synth
from spinegen.emit import spine42
from spinegen.meshtopo import boundary_loops, hull_is_first
from spinegen.spine42_reader import behaviour_changes, read


def features_ir():
    skel, _ = synth.features()
    return read(skel, "features")


def test_features_roundtrip_is_identical():
    a = features_ir()
    assert irmod.validate(a) == []
    b = read(spine42.emit(a), "features")
    assert irmod.diff(a, b, tol=1e-6) == []


def test_emit_is_a_fixpoint():
    first = spine42.emit(features_ir())
    again = spine42.emit(read(first, "features"))
    assert spine42.dumps(first) == spine42.dumps(again)


def test_every_timeline_type_survives():
    a = features_ir()
    kinds = {(t["type"], t["property"]) for an in a["animations"] for t in an["timelines"]}
    for k in [("bone", "rotate"), ("bone", "translate"), ("bone", "scale"), ("bone", "shear"),
              ("bone", "translatex"), ("bone", "scaley"), ("bone", "shearx"), ("bone", "inherit"),
              ("slot", "attachment"), ("slot", "rgba"), ("slot", "rgb"), ("slot", "alpha"), ("slot", "rgba2"),
              ("slot", "rgb2"), ("ik", "ik"), ("transform", "transform"), ("path", "position"),
              ("path", "spacing"), ("path", "mix"), ("physics", "inertia"), ("physics", "wind"),
              ("physics", "reset"), ("deform", "deform"), ("draw_order", "draw_order"), ("event", "event")]:
        assert k in kinds, k
    types = {att["type"] for *_, att in irmod.iter_attachments(a)}
    assert types == {"region", "mesh", "linkedmesh", "point", "boundingbox", "clipping", "path"}


def test_save_load(tmp_path):
    a = features_ir()
    rig, motion = irmod.save(a, str(tmp_path / "build" / "rig.json"))
    loaded = irmod.load(rig)
    assert irmod.diff(a, loaded) == []
    import json
    on_disk = json.load(open(rig))
    assert on_disk["schema"] == "spinegen-rig/1" and "animations" not in on_disk
    assert on_disk["counts"]["bones"] == len(a["bones"]) and on_disk["counts"]["animations"] == 4
    assert json.load(open(motion))["schema"] == "spinegen-motion-ir/1"


def test_weights_are_setup_world():
    # both arm bones lie on the x axis at setup, so world positions are the grid itself
    a = read(synth.arm("ramp")[0], "arm")
    mesh = a["skins"][0]["attachments"]["arm"]["arm"]
    # (not exact: like the runtime, cos(90 deg) uses PI = 3.1415927 and is -2.3e-8, not 0)
    xs = sorted({round(x, 4) for x, _ in mesh["vertices"]})
    assert xs == [float(x) for x in synth.ARM_X]
    for (x, y), row in zip(mesh["vertices"], mesh["weights"]):
        w_fore = dict((b, w) for b, w in row).get("fore", 0.0)
        assert abs(w_fore - min(1, max(0, (x - 60) / 80))) < 1e-6
        assert all(len(inf) == 2 for inf in row)  # consistent influences carry no own position


def test_explicit_fields_and_version():
    out = spine42.emit(features_ir())
    assert out["skeleton"]["spine"] == "4.2.43" and out["skeleton"]["referenceScale"] == 100
    for b in out["bones"]:
        assert "inherit" in b and "transform" not in b
    for p in out["physics"]:
        for k in ("inertia", "strength", "damping", "mass", "limit", "fps"):
            assert k in p, k
    for t in out["transform"]:
        for k in ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"):
            assert k in t, k
    assert spine42.emit(features_ir(), version="4.2.120")["skeleton"]["spine"] == "4.2.120"


def test_orders_renumbered_contiguously():
    a = features_ir()
    # gaps, a duplicate and a big number: 4.2 would skip some of these silently
    for c, o in zip(irmod.constraints(a), [0, 3, 3, 7, 9, 20, 21, 40, 41, 50, 90]):
        c[1]["order"] = o
    before = [(k, c["name"]) for k, c in irmod.constraints(a)]
    rep = {}
    out = spine42.emit(a, report=rep)
    orders = sorted(c["order"] for kind in ("ik", "transform", "path", "physics") for c in out.get(kind, []))
    assert orders == list(range(len(before)))
    b = read(out, "x")
    assert [(k, c["name"]) for k, c in irmod.constraints(b)] == before  # evaluation order kept
    assert rep["orders"]


def test_reader_marks_what_42_never_runs():
    # N = 4: orders 0..3 run, one each, first match in ik, transform, path, physics order
    a = features_ir()
    for kind in ("transform", "path", "physics"):
        a[kind] = []
    a["ik"] = a["ik"][:4]
    skel = spine42.emit(a)
    orders = {"ik_aim": 0, "ik_leg": 0, "ik_leg2": 5, "ik_aim_onlyT": 1}  # repeat, past the end, two that run
    for c in skel["ik"]:
        c["order"] = orders[c["name"]]
    b = read(skel, "x")
    assert {c["name"]: c["runs"] for c in b["ik"]} == {"ik_aim": True, "ik_leg": False, "ik_leg2": False,
                                                       "ik_aim_onlyT": True}
    assert [c["name"] for _, c in irmod.running(b)] == ["ik_aim", "ik_aim_onlyT"]
    assert sum("never runs in 4.2" in w for w in b["source"]["warnings"]) == 2


def test_dead_constraint_is_emitted_inert():
    skel, _ = synth.order_rig()
    a = read(skel, "gap")
    assert [c["runs"] for c in a["transform"]] == [False]
    rep = {}
    out = spine42.emit(a, report=rep)
    tc = out["transform"][0]
    assert tc["order"] == 1 and all(tc[k] == 0 for k in ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY",
                                                          "mixShearY"))
    assert "transform" not in out["animations"]["move"]  # its mix keys would have switched it on
    assert rep["inert"] == [{"kind": "transform", "name": "tc", "order": 2, "timelines_dropped": 1}]
    b = read(out, "gap")
    assert all(c["runs"] for _, c in irmod.constraints(b))
    assert irmod.diff(strip_source(spine42.prepare(a)), strip_source(b), tol=1e-6) == []  # warnings differ


def test_hull_fixed_and_vertices_follow():
    a = read(synth.arm("ramp")[0], "arm")
    mesh = a["skins"][0]["attachments"]["arm"]["arm"]
    n = len(mesh["vertices"])
    perm = list(range(n))
    random.Random(7).shuffle(perm)
    from spinegen.meshtopo import reorder_mesh
    reorder_mesh(mesh, perm, 0)  # scramble and lose the hull, like Hard Time's meshes
    a["animations"] = [{"name": "d", "timelines": [{"type": "deform", "property": "deform",
                                                    "target": {"skin": "default", "slot": "arm", "attachment": "arm"},
                                                    "keys": [{"time": 0.0, "offsets": None, "curve": None},
                                                             {"time": 1.0, "offsets": _deform_for(mesh),
                                                              "curve": None}]}]}]
    assert not hull_is_first(mesh["triangles"], mesh["hull"])
    rep = {}
    out = spine42.emit(a, report=rep)
    m2 = out["skins"][0]["attachments"]["arm"]["arm"]
    tris = [m2["triangles"][i:i + 3] for i in range(0, len(m2["triangles"]), 3)]
    assert m2["hull"] == 2 * len(synth.ARM_X) + 2 * (len(synth.ARM_Y) - 2)
    assert hull_is_first(tris, m2["hull"], boundary_loops(tris))
    b = read(out, "arm")
    # same world vertices, setup and deformed, once the new order is undone
    ev_a, ev_b = lbs.Evaluator(a), lbs.Evaluator(b)
    p_new = rep["hull"][0]["perm"]
    from spinegen import playback
    for pose in ({}, {"fore": {"rotation": 50}}):
        for t in (0.0, 1.0):
            sa, sb = playback.sample(a, "d", t), playback.sample(b, "d", t)
            va = ev_a.frame(pose, deform=sa["deform"])["slots"]["arm"]["vertices"]
            vb = ev_b.frame(pose, deform=sb["deform"])["slots"]["arm"]["vertices"]
            vb_old = [None] * len(va)
            for new, old in enumerate(p_new):
                vb_old[2 * old:2 * old + 2] = vb[2 * new:2 * new + 2]
            assert max(abs(x - y) for x, y in zip(va, vb_old)) < 1e-5
    # and a second emit leaves it alone
    rep2 = {}
    spine42.emit(b, report=rep2)
    assert rep2["hull"] == []


def _deform_for(mesh):
    out = []
    for i, row in enumerate(mesh["weights"]):
        for _ in row:
            out += [0.1 * i, -0.05 * i]
    return out


def test_semantic_constraints_expand():
    a = read(synth.arm()[0], "arm")
    a["bones"].append(irmod.new_bone("elbow_mid", "upper", x=100))
    a["transform"].append({"name": "half", "order": 0, "kind": "half_angle", "bone": "elbow_mid",
                           "target": "fore", "mix": 0.5})
    strength, damping = irmod.spring_to_spine(2.0, 0.4, mass=1.5)
    a["physics"].append(dict(features_ir()["physics"][0], name="spring", order=1, bone="hand",
                             spring={"f": 2.0, "zeta": 0.4}, mass=1.5))
    out = spine42.emit(a)
    t = out["transform"][0]
    assert t["local"] is True and t["relative"] is False and t["mixRotate"] == 0.5
    assert all(t[k] == 0 for k in ("mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"))
    p = out["physics"][0]
    assert math.isclose(p["strength"], strength, rel_tol=1e-6) and math.isclose(p["damping"], damping, rel_tol=1e-5)
    f, z = irmod.spine_to_spring(p["strength"], p["damping"], p["mass"])
    assert math.isclose(f, 2.0, rel_tol=1e-5) and math.isclose(z, 0.4, rel_tol=1e-4)
    # the expanded form is what reads back
    assert irmod.diff(spine42.prepare(a), read(out, "arm")) == []
    # and the half angle does half: helper turns 30 when the forearm turns 60
    ev = lbs.Evaluator(read(out, "arm"))
    w = ev.world_dict(ev.world({"fore": {"rotation": 60}}))
    assert abs(math.degrees(math.atan2(w["elbow_mid"][2], w["elbow_mid"][0])) - 30) < 1e-4


def test_draw_order_keys_roundtrip():
    a = features_ir()
    tl = next(t for an in a["animations"] for t in an["timelines"] if t["type"] == "draw_order")
    names = [s["name"] for s in a["slots"]]
    assert sorted(tl["keys"][0]["order"]) == sorted(names) and tl["keys"][0]["order"] != names
    assert tl["keys"][1]["order"] is None
    rnd = random.Random(3)
    for _ in range(20):
        order = names[:]
        rnd.shuffle(order)
        tl["keys"][0]["order"] = order
        b = read(spine42.emit(a), "x")
        tl_b = next(t for an in b["animations"] for t in an["timelines"] if t["type"] == "draw_order")
        assert tl_b["keys"][0]["order"] == order


def _old_spellings():
    """The arm with every pre-4.2 key 4.2.120's SkeletonJson ignores."""
    skel = copy.deepcopy(synth.arm()[0])
    skel["skeleton"]["spine"] = "4.1.20"
    skel["bones"][3]["transform"] = "noScale"  # 4.1 name for inherit
    mesh_vals = skel["skins"][0]["attachments"]["arm"]["arm"]
    n = sum(mesh_vals["vertices"][i] for i in _vertex_starts(mesh_vals["vertices"]))
    skel["skins"][0]["attachments"]["arm"]["arm2"] = {"type": "linkedmesh", "parent": "arm", "deform": False}
    skel["animations"]["bend"]["deform"] = {"default": {"arm": {"arm": [{"time": 0}, {"time": 1, "offset": 0,
                                                                                     "vertices": [1.0] * (2 * n)}]}}}
    skel["animations"]["bend"]["draworder"] = [{"time": 0.5, "offsets": [{"slot": "arm", "offset": 1}]}]
    skel["physics"] = [{"name": "p", "bone": "hand", "rotate": 1}]
    return skel, n


def test_old_spellings_are_ignored_as_42_ignores_them():
    """spine-core 4.2.120 reads only inherit, timelines, attachments and
    drawOrder; the IR must pose the file as that runtime does."""
    skel, _ = _old_spellings()
    a = read(skel, "old")
    assert a["bones"][3]["inherit"] == "normal"
    assert a["skins"][0]["attachments"]["arm"]["arm2"]["timelines"] is True
    assert not [t for t in a["animations"][0]["timelines"] if t["type"] in ("deform", "draw_order")]
    warnings = a["source"]["warnings"]
    for key in ("'transform' ignored", "'deform' ignored", "'draworder' ignored"):
        assert any(key in w for w in warnings), key
    assert sum("ignored" in w for w in warnings) == 4  # bone transform, linked deform, anim deform, draworder
    assert any("4.1.20" in w and "physics" in w for w in warnings)
    assert behaviour_changes(a) == []
    phys = a["physics"][0]  # 4.2 defaults resolved explicitly
    assert (phys["inertia"], phys["strength"], phys["damping"], phys["mass"], phys["limit"], phys["fps"]) == \
        (1.0, 100.0, 1.0, 1.0, 5000.0, 60.0)
    out = spine42.emit(a)  # what played (normal) is what is written: no live inherit from an ignored key
    assert out["bones"][3]["inherit"] == "normal" and "transform" not in out["bones"][3]
    assert "attachments" not in out["animations"]["bend"] and "drawOrder" not in out["animations"]["bend"]


def test_old_spellings_upgrade_is_an_explicit_behaviour_change():
    skel, n = _old_spellings()
    a = read(skel, "old", upgrade_legacy=True)
    assert a["bones"][3]["inherit"] == "noScale"
    assert a["skins"][0]["attachments"]["arm"]["arm2"]["timelines"] is False
    deform = [t for t in a["animations"][0]["timelines"] if t["type"] == "deform"]
    assert len(deform) == 1 and deform[0]["keys"][1]["offsets"] == [1.0] * (2 * n)
    assert len(behaviour_changes(a)) == 4
    out = spine42.emit(a)
    assert out["bones"][3]["inherit"] == "noScale" and "transform" not in out["bones"][3]
    assert out["animations"]["bend"]["attachments"]["default"]["arm"]["arm"]["deform"][1]["vertices"] == [1] * (2 * n)


def test_dict_skins_are_no_skins_in_42():
    skel = copy.deepcopy(synth.arm()[0])
    skel["skins"] = {"default": skel["skins"][0]["attachments"]}
    assert read(skel, "old")["skins"] == []
    assert len(read(skel, "old", upgrade_legacy=True)["skins"]) == 1


def _vertex_starts(values):
    i, out = 0, []
    while i < len(values):
        out.append(i)
        i += 1 + 4 * values[i]
    return out


def test_wrong_hull_count_alone_is_fixed_without_reordering():
    a = read(synth.arm("ramp")[0], "arm")
    mesh = a["skins"][0]["attachments"]["arm"]["arm"]
    good = mesh["hull"]
    mesh["hull"] = 0  # outline already first, count missing (Hard Time writes 0)
    rep = {}
    out = spine42.emit(a, report=rep)
    assert out["skins"][0]["attachments"]["arm"]["arm"]["hull"] == good
    assert rep["hull"][0]["reordered"] is False


def _grid_mesh(hole=False, island=False):
    """A 4x4-cell square (UV 0..1) as point list + triangles; `hole` drops the
    centre-left cell (enclosed by the outline), `island` adds a detached
    triangle beyond it at u > 1."""
    pts = [(i / 4, j / 4) for j in range(5) for i in range(5)]
    tris = []
    for j in range(4):
        for i in range(4):
            if hole and (i, j) == (1, 1):
                continue
            a, b, c, d = j * 5 + i, j * 5 + i + 1, (j + 1) * 5 + i + 1, (j + 1) * 5 + i
            tris += [[a, b, c], [a, c, d]]
    if island:
        n = len(pts)
        pts += [(1.2, 0.0), (1.4, 0.0), (1.4, 0.2)]
        tris.append([n, n + 1, n + 2])
    return pts, tris


def test_hole_rim_is_inside_the_outline_an_island_is_not():
    from spinegen.meshtopo import hull_permutation, outside_outline
    for hole, island, stray in ((True, False, 0), (False, True, 3), (True, True, 3)):
        pts, tris = _grid_mesh(hole, island)
        loops = boundary_loops(tris)
        perm, hull = hull_permutation(tris, len(pts), pts, 0, loops)
        assert hull == 16  # the square's outline, first
        outer = next(lp for lp in loops if set(lp) == {perm[i] for i in range(hull)})
        assert len(outside_outline(pts, loops, outer)) == stray, (hole, island)
