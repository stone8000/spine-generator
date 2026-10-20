"""G1 FOLDS: triangles whose world winding agrees with their (y-down) UV winding."""
import numpy as np

from spinegen import lbs, synth
from spinegen.spine42_reader import read


def arm(weights):
    return lbs.Evaluator(read(synth.arm(weights)[0], "arm"))


def test_fold_mask_on_one_triangle():
    uv = [[0, 0], [1, 0], [0, 1]]           # clockwise on screen (v points down)
    up = [[0, 0], [1, 0], [0, -1]]          # the same triangle drawn y-up: not folded
    assert not lbs.fold_mask(up, uv, [[0, 1, 2]]).any()
    mirrored = [[0, 0], [-1, 0], [0, -1]]
    assert lbs.fold_mask(mirrored, uv, [[0, 1, 2]]).all()
    flat = [[0, 0], [1, 0], [2, 0]]         # degenerate: not counted
    assert not lbs.fold_mask(flat, uv, [[0, 1, 2]]).any()


def test_rest_pose_has_no_folds():
    for w in ("rigid", "ramp"):
        f = arm(w).folds()
        assert f["folds"] == 0 and f["triangles"] == 2 * 11 * 2


def test_rigid_mesh_folds_when_forearm_folds_back():
    # every vertex 100% on one bone: the strip cell spanning the elbow turns inside out
    f = arm("rigid").folds({"fore": {"rotation": 170}})
    assert f["folds"] > 0
    assert f["slots"] == {"arm": f["folds"]}


def test_blended_mesh_bends_without_folding_at_moderate_angles():
    ev = arm("ramp")
    assert all(ev.folds({"fore": {"rotation": a}})["folds"] == 0 for a in (30, 60, -60))


def test_fold_count_matches_world_vertices():
    ev = arm("rigid")
    pose = {"fore": {"rotation": 170}}
    v = np.array(ev.frame(pose)["slots"]["arm"]["vertices"]).reshape(-1, 2)
    mesh = ev.ir["skins"][0]["attachments"]["arm"]["arm"]
    assert lbs.fold_mask(v, mesh["uvs"], mesh["triangles"]).sum() == ev.folds(pose)["folds"]


def test_mesh_mirrored_at_setup_is_not_folded():
    """A limb reused mirrored (bone scaleX -1) has every triangle's winding
    equal to its UV winding at setup: that is its unfolded state."""
    skel, _ = synth.arm("rigid")
    skel["bones"][1]["scaleX"] = -1
    ev = lbs.Evaluator(read(skel, "mirrored"))
    mesh = ev.ir["skins"][0]["attachments"]["arm"]["arm"]
    v = np.array(ev.frame()["slots"]["arm"]["vertices"]).reshape(-1, 2)
    assert lbs.fold_reference(v, mesh["uvs"], mesh["triangles"]) == 1
    assert lbs.fold_mask(v, mesh["uvs"], mesh["triangles"]).all()  # the unmirrored rule would call all 44 folded
    assert ev.folds()["folds"] == 0
    assert ev.folds({"fore": {"rotation": 170}})["folds"] > 0  # folding the mirrored arm still counts


def test_fold_reference_majority_and_tie():
    uv = [[0, 0], [1, 0], [0, 1]]
    up, mirrored = [[0, 0], [1, 0], [0, -1]], [[0, 0], [-1, 0], [0, -1]]
    assert lbs.fold_reference(up, uv, [[0, 1, 2]]) == -1
    assert lbs.fold_reference(mirrored, uv, [[0, 1, 2]]) == 1
    both = mirrored + [[p[0] + 5, p[1]] for p in up]
    assert lbs.fold_reference(both, uv + uv, [[0, 1, 2], [3, 4, 5]]) == -1  # a tie is the unmirrored default
