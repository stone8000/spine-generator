"""P5 (second character): what the Hard Time warden needed from the
generator, kept general and tested on synthetic inputs only: bone matching
by structure, a layer's bone from its weights, coinciding band rows, a
limb folded at setup, a planted boot's toe, a throttled physics limit.
"""
import json
import os

import numpy as np

from spinegen import ingest_spine, mesh, meshtopo, rigbuild
from spinegen import template as tmpl

from test_rigbuild import layers_dir  # noqa: F401  (module fixture: synthetic layers dir)


def test_match_prefers_the_bone_under_its_template_parent():
    """A root-parented IK target named like a shin ("knee_l") loses to the
    shin under the thigh; "chest" (the earlier match set) beats "waist"
    though it is deeper. Nearest-the-root alone took the target and the
    waist."""
    src = [{"name": "root", "parent": None}, {"name": "hips", "parent": "root"},
           {"name": "waist", "parent": "hips"}, {"name": "chest", "parent": "waist"},
           {"name": "arm_l", "parent": "chest"}, {"name": "leg_l", "parent": "hips"},
           {"name": "shin_l", "parent": "leg_l"}, {"name": "knee_l", "parent": "root"},
           {"name": "foot_l", "parent": "root"}]
    out, notes = tmpl.match_bones(src, tmpl.load("biped_mascot"))
    assert out["shin_l"] == "shin_l" and out["torso"] == "chest" and out["upperarm_l"] == "arm_l"
    assert any("knee_l" in n for n in notes)


def test_layer_role_comes_from_its_weights_when_the_slot_bone_does_not_move_it():
    rename = {"leg_l": "thigh_l", "root": "root"}
    slot = {"name": "thigh", "bone": "root"}
    att = {"type": "mesh", "weights": [[["leg_l", 0.9], ["hips", 0.1]], [["leg_l", 1.0]]]}
    assert ingest_spine._role(att, slot, rename) == "thigh_l"
    # a slot bone that is among the influences keeps the layer (the monkey's thighs)
    att2 = {"type": "mesh", "weights": [[["root", 0.2], ["leg_l", 0.8]]]}
    assert ingest_spine._role(att2, slot, rename) == "root"


def test_two_bands_meeting_on_one_row_line_do_not_split_the_mesh():
    """Two bands that each run exactly half the gap put a row on the same
    line, one as a straight edge, one through row points: the mesh must
    stay one piece with its whole outline as the hull (qa L8)."""
    import cv2
    ax, nrm = np.array([-0.6, 0.8]), np.array([0.8, 0.6])   # a diagonal limb (exactly vertical ones hid it)
    c0 = np.array([250.0, 40.0])
    c1 = c0 + ax * 330
    alpha = np.zeros((400, 320), np.uint8)
    cv2.fillPoly(alpha, [np.array([c0 + nrm * 45, c1 + nrm * 40, c1 - nrm * 40, c0 - nrm * 45]).astype(np.int32)], 255)
    A, B = c0 + ax * 60, c0 + ax * 250   # each band runs half the 190 px gap: 4.75 halfwidths
    a = {"center": A.tolist(), "axis": ax.tolist(), "halfwidth": 20.0, "reach": [2.0, 4.75], "rows": 3}
    b = {"center": B.tolist(), "axis": ax.tolist(), "halfwidth": 20.0, "reach": [4.75, 2.0], "rows": [3, 4],
         "row_points": 3}
    m = mesh.build(alpha, (0, 0), bands=[a, b], **rigbuild.MESH_PROFILES["lean"])
    loops = meshtopo.boundary_loops(np.asarray(m["tris"]).tolist())
    assert m["holes"] == 0 and len(loops) == 1 and len(loops[0]) == m["hull"]


def test_a_limb_folded_at_setup_keeps_its_joint_rigid(layers_dir):  # noqa: F811
    """The forearm aimed back up along the upper arm (the warden's baton arm
    is folded 163 deg): no one axis orders the two pieces, so the elbow is
    rigid and the forearm stays 100% on its bone."""
    with open(os.path.join(layers_dir, "joints.json")) as f:
        joints = json.load(f)
    el, sh = joints["joints"]["elbow_l"]["xy"], joints["joints"]["shoulder_l"]["xy"]
    joints["joints"]["wrist_l"]["xy"] = [el[0] + 0.3 * (sh[0] - el[0]) + 3, el[1] + 0.8 * (sh[1] - el[1])]
    res = rigbuild.build(layers_dir, joints=joints, mode="joint")
    assert res["report"]["joint_modes"]["elbow_l"] == "rigid" and res["report"]["joint_modes"]["elbow_r"] == "helper"
    assert any("elbow_l: folded" in n for n in res["report"]["notes"])
    from spinegen import ir as irmod
    att = next(a for _, s, _, a in irmod.iter_attachments(res["ir"]) if s == "forearm_l")
    assert all(len(row) == 1 for row in att["weights"])


def test_planted_boot_is_all_foot_below_the_ankle():
    class B:
        bones = {"foot_l": {"kind": "bone", "parent": "ik_foot_l", "head": (100.0, 200.0)},
                 "ik_foot_l": {"kind": "ik_target", "parent": "root"},
                 "thigh_l": {"kind": "bone", "parent": "hips", "head": (0.0, 0.0)}, "hips": {"kind": "bone"}}
    m = {"verts": np.array([[100.0, 190.0], [160.0, 205.0], [40.0, 230.0]])}
    w = [[("foot_l", 0.7), ("shin_l", 0.3)], [("foot_l", 0.96), ("shin_l", 0.04)], [("foot_l", 1.0)]]
    out = rigbuild._Builder._planted_sole(B(), {"role": "foot_l"}, m, w)
    assert out == [w[0], [("foot_l", 1.0)], [("foot_l", 1.0)]]
    assert rigbuild._Builder._planted_sole(B(), {"role": "thigh_l"}, m, w) == w


def test_a_throttling_physics_limit_is_raised_and_matched_unthrottled(layers_dir, tmp_path):  # noqa: F811
    src = json.load(open(os.path.join(layers_dir, "source", "rig.json")))
    src["physics"] = [{"name": "phys_arm", "order": 0, "bone": "upperarm_l", "x": 0.0, "y": 0.0, "rotate": 1.0,
                       "scale_x": 0.0, "shear_x": 0.0, "limit": 60.0, "fps": 60.0, "inertia": 0.5, "strength": 100.0,
                       "damping": 0.85, "mass": 1.0, "wind": 0.0, "gravity": 0.0, "mix": 1.0, "inertia_global": False,
                       "strength_global": False, "damping_global": False, "mass_global": False, "wind_global": False,
                       "gravity_global": False, "mix_global": False, "skin_required": False}]
    res = rigbuild.build(layers_dir, mode="joint", source=src)
    (c,) = res["ir"]["physics"]
    assert c["limit"] == rigbuild.PHYS_LIMIT_FIX
    assert any("limit 60 < 1000 raised" in n for n in res["report"]["notes"])
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"physics": [{"name": "a", "limit": 80}, {"name": "b", "limit": 5000}]}))
    fixed = rigbuild._limit_fixed(str(p), str(tmp_path))
    assert fixed != str(p) and [c["limit"] for c in json.load(open(fixed))["physics"]] == [5000.0, 5000]
    q = tmp_path / "ok.json"
    q.write_text(json.dumps({"physics": [{"name": "b", "limit": 5000}]}))
    assert rigbuild._limit_fixed(str(q), str(tmp_path)) == str(q)
