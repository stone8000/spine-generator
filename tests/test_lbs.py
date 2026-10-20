"""numpy LBS: hand-computed transforms for every inherit mode, skinning, IK.

The oracle comparison (gate C1) is in test_oracle.py; these cases are worked
out on paper so they hold without Node.
"""
import math

import numpy as np
import pytest

from spinegen import ir as irmod
from spinegen import lbs, synth
from spinegen.spine42_reader import read

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def rot(deg, s=1.0):
    r = math.radians(deg)
    return np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]]) * s


def mat(w):
    return np.array([[w[0], w[1]], [w[2], w[3]]])


def rig_with_children(parent_kw):
    """root -> p (parent_kw) -> one child per inherit mode at (10, 0), rotation 30."""
    a = irmod.new_rig("modes")
    a["bones"] = [irmod.new_bone("root"), irmod.new_bone("p", "root", **parent_kw)]
    for mode in irmod.INHERIT_MODES:
        a["bones"].append(irmod.new_bone(mode, "p", x=10, rotation=30, inherit=mode))
    ev = lbs.Evaluator(a)
    return ev.world_dict(ev.world())


@pytest.mark.parametrize("mode,expect", [
    ("normal", rot(120, 2)),                 # parent's rotation and scale
    ("onlyTranslation", rot(30)),            # neither
    ("noRotationOrReflection", rot(30, 2)),  # scale only
    ("noScale", rot(120)),                   # rotation only
    ("noScaleOrReflection", rot(120)),
])
def test_inherit_under_rotated_scaled_parent(mode, expect):
    w = rig_with_children({"rotation": 90, "scale_x": 2, "scale_y": 2})
    assert np.allclose(mat(w[mode]), expect, atol=1e-6)
    assert np.allclose(w[mode][4:], [0, 20], atol=1e-5)  # translation always inherits: R(90) * 2 * (10, 0)


@pytest.mark.parametrize("mode,expect", [
    ("normal", [[-C30, S30], [S30, C30]]),               # mirrored
    ("noScale", [[-C30, S30], [S30, C30]]),              # keeps the reflection
    ("noScaleOrReflection", rot(150)),                   # drops it: x axis mirrored, frame proper
    ("noRotationOrReflection", rot(30)),
    ("onlyTranslation", rot(30)),
])
def test_inherit_under_reflected_parent(mode, expect):
    w = rig_with_children({"scale_x": -1})
    assert np.allclose(mat(w[mode]), expect, atol=1e-6)
    assert np.allclose(w[mode][4:], [-10, 0], atol=1e-6)


def test_no_rotation_keeps_non_uniform_parent_scale():
    # parent stretched 2x along its x axis, which points up: the child sees
    # world-y stretched by 2 but none of the parent's 90 deg turn
    w = rig_with_children({"rotation": 90, "scale_x": 2, "scale_y": 1})
    assert np.allclose(mat(w["noRotationOrReflection"]), [[C30, -S30], [2 * S30, 2 * C30]], atol=1e-6)


def test_local_shear_and_scale():
    a = irmod.new_rig()
    a["bones"] = [irmod.new_bone("root", rotation=20, scale_x=2, scale_y=0.5, shear_x=10, shear_y=-15)]
    ev = lbs.Evaluator(a)
    w = ev.world()[0]
    x_axis = 2 * np.array([math.cos(math.radians(30)), math.sin(math.radians(30))])
    y_axis = 0.5 * np.array([math.cos(math.radians(95)), math.sin(math.radians(95))])
    assert np.allclose([w[0], w[2]], x_axis, atol=1e-6) and np.allclose([w[1], w[3]], y_axis, atol=1e-6)


@pytest.mark.parametrize("mode", ["normal", "onlyTranslation"])
@pytest.mark.parametrize("parent", [(35, 1.7, 0.6, 12), (-120, -1.3, 0.8, -7), (200, 0.9, -1.1, 3)])
def test_applied_from_world_inverts_a_shear_free_local(mode, parent):
    p = lbs.compose(None, 3, -4, parent[0], parent[1], parent[2], parent[3], -5)
    w = lbs.compose(p, 7, 2, 40, 1.3, 0.7, 0, 0, mode)
    # shear_y comes back as ~1.3e-6 deg: compose builds the y axis 90 deg on with the
    # runtime's PI (3.1415927), the decomposition measures it with the true one
    assert np.allclose(lbs.applied_from_world(p, w, mode), (7, 2, 40, 1.3, 0.7, 0, 0), atol=2e-6)


# The runtime's Bone.updateAppliedTransform is not an exact inverse; these
# are its documented results, worked out by hand (lbs must match them).

def test_applied_from_world_sheared_local_loses_cos_shear_of_y_scale():
    p = lbs.compose(None, 0, 0, 35, 1.7, 1.7, 0, 0)
    w = lbs.compose(p, 7, 2, 40, 1.3, 0.7, 0, 30)
    x, y, r, sx, sy, shx, shy = lbs.applied_from_world(p, w)
    assert np.allclose((r, sx, sy, shy), (40, 1.3, 0.7 * math.cos(math.radians(30)), 30), atol=1e-6)


def test_applied_from_world_no_rotation_forgets_the_parent_rotation():
    # uniform parent k R(35): the frame is k R(35) too, the local is L(40 - 35),
    # and the runtime reports that 5 deg, so re-composing turns the bone by -35 more
    p = lbs.compose(None, 0, 0, 35, 2, 2, 0, 0)
    w = lbs.compose(p, 7, 2, 40, 1.3, 0.7, 0, 0, "noRotationOrReflection")
    back = lbs.applied_from_world(p, w, "noRotationOrReflection")
    assert abs(back[2] - 5) < 1e-5
    again = lbs.compose(p, *back, "noRotationOrReflection")  # world angle 35 + (5 - 35), not 40
    assert abs(math.degrees(math.atan2(again[2], again[0])) - 5) < 1e-4


@pytest.mark.parametrize("mode", ["noScale", "noScaleOrReflection"])
def test_applied_from_world_no_scale_measures_from_the_pose_rotation_frame(mode):
    # the frame is built from the pose rotation (40) and already points the
    # bone's x axis, so the angle left over is 0
    p = lbs.compose(None, 0, 0, 35, 1.7, 0.6, 0, 0)
    w = lbs.compose(p, 7, 2, 40, 1.3, 0.7, 0, 0, mode)
    back = lbs.applied_from_world(p, w, mode, rotation=40)
    assert np.allclose(back[2:5], (0, 1.3, 0.7), atol=1e-6)


def test_applied_from_world_root_reports_shear_with_the_opposite_sign():
    w = lbs.compose(None, 1, 2, 30, 1.5, 1.2, 0, 20)
    assert np.allclose(lbs.applied_from_world(None, w)[2:], (30, 1.5, 1.2, 0, -20), atol=1e-6)


def arm(weights="ramp"):
    return read(synth.arm(weights)[0], "arm")


def test_weighted_vertex_blends_its_bones():
    a = arm("ramp")
    ev = lbs.Evaluator(a)
    mesh = a["skins"][0]["attachments"]["arm"]["arm"]
    i = next(k for k, (x, y) in enumerate(mesh["vertices"]) if abs(x - 90) < 1e-4 and abs(y - 15) < 1e-4)
    w_fore = dict((b, w) for b, w in mesh["weights"][i])["fore"]
    v = np.array(ev.frame({"fore": {"rotation": 90}})["slots"]["arm"]["vertices"]).reshape(-1, 2)[i]
    rigid_upper = np.array([90, 15])
    rigid_fore = np.array([100, 0]) + rot(90) @ np.array([-10, 15])
    assert np.allclose(v, (1 - w_fore) * rigid_upper + w_fore * rigid_fore, atol=1e-5)


def test_rigid_mesh_follows_slot_bone():
    a = read(synth.features()[0], "f")
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world({"fore": {"rotation": 30}}))
    v = np.array(ev.frame({"fore": {"rotation": 30}})["slots"]["patch"]["vertices"]).reshape(-1, 2)
    m = w["fore"]
    expect = [(m[0] * x + m[1] * y + m[4], m[2] * x + m[3] * y + m[5])
              for x, y in [(0, -20), (40, -20), (40, 20), (0, 20), (20, 0)]]
    assert np.allclose(v, expect, atol=1e-4)


def test_region_quad_with_stripped_whitespace():
    # 30x20 image packed as 24x16 at offset (3, 2): the quad covers only the packed part
    att = {"x": 12, "y": 0, "rotation": 0, "scale_x": 1, "scale_y": 1, "width": 30, "height": 20}
    region = {"width": 24, "height": 16, "offset_x": 3, "offset_y": 2, "original_width": 30, "original_height": 20}
    q = lbs.region_offsets(att, region)
    assert np.allclose(q, [[0, -8], [0, 8], [24, 8], [24, -8]])  # BL, TL, TR, BR as the runtime orders them
    assert np.allclose(lbs.region_offsets(att), [[-3, -10], [-3, 10], [27, 10], [27, -10]])
    att["rotation"] = 90  # corner (-15, -10) turns to (10, -15), then moves by (12, 0)
    assert np.allclose(lbs.region_offsets(att)[0], [22, -15], atol=1e-5)


def ik_rig(two_bone, **ik):
    a = irmod.new_rig()
    a["bones"] = [irmod.new_bone("root"), irmod.new_bone("hip", "root", x=0, y=0),
                  irmod.new_bone("thigh", "hip", rotation=-90, length=100),
                  irmod.new_bone("shin", "thigh", x=100, length=100), irmod.new_bone("target", "root", x=60, y=-120)]
    a["ik"] = [dict({"name": "ik", "order": 0, "skin_required": False,
                     "bones": ["thigh", "shin"] if two_bone else ["thigh"], "target": "target", "mix": 1.0,
                     "softness": 0.0, "bend_positive": True, "compress": False, "stretch": False, "uniform": False},
                    **ik)]
    return a


def tip(w, name, length=100):
    return np.array([w[name][4] + w[name][0] * length, w[name][5] + w[name][2] * length])


def test_one_bone_ik_aims_and_stretches():
    a = ik_rig(False, stretch=True)
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert abs(math.degrees(math.atan2(w["thigh"][2], w["thigh"][0])) - math.degrees(math.atan2(-120, 60))) < 1e-4
    assert np.allclose(tip(w, "thigh"), [60, -120], atol=1e-4)  # stretched to reach


@pytest.mark.parametrize("bend", [True, False])
def test_two_bone_ik_reaches_with_bend(bend):
    a = ik_rig(True, bend_positive=bend)
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert np.allclose(tip(w, "shin"), [60, -120], atol=1e-4)
    knee = np.array(w["shin"][4:])
    # positive bend turns the child counter-clockwise, so the knee lands
    # clockwise of the hip->target line
    side = 60 * knee[1] - (-120) * knee[0]
    assert (side < 0) == bend


def test_two_bone_ik_out_of_reach_straightens_or_stretches():
    a = ik_rig(True)
    a["bones"][-1].update(x=0, y=-300)
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert np.allclose(tip(w, "shin"), [0, -200], atol=1e-4)  # straight, as far as it goes
    a["ik"][0]["stretch"] = True
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert np.allclose(tip(w, "shin"), [0, -300], atol=1e-3)


def test_ik_mix_zero_and_state_override():
    a = ik_rig(True, mix=0.0)
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert np.allclose(tip(w, "shin"), [0, -200], atol=1e-4)
    w = ev.world_dict(ev.world(state={"ik": {"mix": 1.0}}))
    assert np.allclose(tip(w, "shin"), [60, -120], atol=1e-4)


def test_pose_accepts_spine_and_ir_field_names():
    ev = lbs.Evaluator(arm())
    a = ev.world({"fore": {"scaleX": 2, "shearY": 10}})
    b = ev.world({"fore": {"scale_x": 2, "shear_y": 10}})
    assert np.allclose(a, b)


def test_frame_has_dump_shape():
    f = lbs.setup_frame(arm())
    assert set(f) == {"bones", "slots"}
    assert set(f["bones"]["fore"]) == set("abcdxy")
    assert f["slots"]["arm"]["attachment"] == "arm" and len(f["slots"]["arm"]["vertices"]) == 2 * 36
    assert len(f["slots"]["hand"]["vertices"]) == 8


def test_two_bone_ik_with_non_uniform_parent_scale_reaches():
    # the child's end moves on an ellipse (psx * l2, psy * l2); in reach it lands on the target
    a = ik_rig(True)
    a["bones"][2].update(scale_x=1.5, scale_y=1.0)
    a["bones"][-1].update(x=70, y=-160)
    ev = lbs.Evaluator(a)
    w = ev.world_dict(ev.world())
    assert np.allclose(tip(w, "shin"), [70, -160], atol=1e-4)
    assert ev.warnings == []
