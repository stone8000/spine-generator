"""spinegen.artreq: the art request pack (SOTA §7 P3), on synthetic
characters whose problems are painted in by construction: a smeared
underlay, a cut-out shoulder that tears, an elbow bent past its band, a
shard cut into a forearm. Third-party art is never used here.
"""
import json
import os

import numpy as np
from PIL import Image

from spinegen import art_gates as ag
from spinegen import artreq, budget, synth_art

SCALE = 0.5
SHOULDER = {"shoulder": {"out": 60.0, "in": 0.0}}


def _hidden_torso_texels(ir, images, joints):
    sc = artreq.Scene(ir, images, joints)
    vis = sc.visible_texels("torso")
    alpha = images["torso"][..., 3] >= 128
    return sc, vis, alpha & ~vis


def _smear(img, vis):
    """Every hidden texel takes the colour of the nearest visible texel in
    its row: the edge-colour smear the monkey's layers carry under each
    occluder."""
    out = img.copy()
    for i in range(img.shape[0]):
        cols = np.nonzero(vis[i])[0]
        if not len(cols):
            continue
        hid = np.nonzero(~vis[i] & (img[i, :, 3] >= 128))[0]
        near = cols[np.abs(hid[:, None] - cols[None, :]).argmin(1)]
        out[i, hid, :3] = img[i, near, :3]
    return out


def _item(doc, prefix):
    found = [it for it in doc["items"] if it["id"].startswith(prefix)]
    assert found, [it["id"] for it in doc["items"]]
    return found[0]


def _mask(out, it):
    return np.asarray(Image.open(os.path.join(out, it["mask"]["file"]))) > 0


def test_paint_mask_is_the_smeared_underlay_the_raised_arm_reveals(tmp_path):
    """The torso under the upper arm is a smear: the shoulder sweep reveals
    it, the item is a request (unfinished) and its mask covers what the arm
    hid and nothing the rest pose shows. The same torso painted through
    (stripes continue under the arm) is listed only as a check."""
    ir, images, joints = synth_art.build(mode="joint", arms=1, scale=SCALE)
    sc, vis, hidden = _hidden_torso_texels(ir, images, joints)
    smeared = dict(images, torso=_smear(images["torso"], vis))
    out = str(tmp_path / "smear")
    doc = artreq.pack(ir, smeared, joints, out, targets=SHOULDER, step=10, ik_poses=None)
    it = _item(doc, "paint_torso__under_upper_arm_l")
    assert it["unfinished"] and it["paint"]["copied_frac"] >= 0.9
    m = _mask(out, it)
    assert it["mask"]["pad"] == [0, 0, 0, 0] and m.shape == images["torso"].shape[:2]
    assert not (m & vis).any(), "the mask never touches paint the rest pose shows"
    # it lies on the arm's side of the torso (side l: image columns < centre), and covers
    # most of what the arm hid there
    assert np.nonzero(m)[1].mean() < m.shape[1] / 2
    assert (m & hidden).sum() >= 0.5 * m.sum()
    # the poses that use it are the shoulder sweep's outward direction
    assert any(u["group"] == "shoulder_l -" for u in it["uses"])

    out2 = str(tmp_path / "painted")
    doc2 = artreq.pack(ir, images, joints, out2, targets=SHOULDER, step=10, ik_poses=None)
    it2 = _item(doc2, "paint_torso__under_upper_arm_l")
    assert not it2["unfinished"] and it2["paint"]["copied_frac"] < artreq.SMEAR_FRAC
    readme = open(os.path.join(out2, "README.md")).read()
    assert "請確認" in readme and "確認：" in readme


def test_a_seam_that_opens_is_painted_on_the_lower_layer(tmp_path):
    """A square-cut elbow tears at once (G4). The gap is carried back into
    the forearm (drawn below the sleeve), by the forearm's own mesh: the
    item names the joint, is a request whatever the paint looks like, and
    its mask lies past the forearm image's top edge (pad), where the piece
    must continue under the sleeve."""
    ir, images, joints = synth_art.build(mode="rigid", arms=1, scale=SCALE, overlap=0, round_ends=False)
    out = str(tmp_path / "seam")
    doc = artreq.pack(ir, images, joints, out, targets={"elbow": {"out": 40.0, "in": 40.0}}, step=10, ik_poses=None)
    it = _item(doc, "paint_forearm_l__under_upper_arm_l")
    assert it["joints"] == ["elbow_l"] and it["gap_px"] > 0 and it["unfinished"]
    m = _mask(out, it)
    pad = it["mask"]["pad"]
    th, tw = images["forearm_l"].shape[:2]
    assert m.shape == (th + pad[1] + pad[3], tw + pad[0] + pad[2])
    assert pad[1] > 0 and it["outside_px"] > 0, "the forearm grows upward, under the sleeve"
    rows, cols = np.nonzero(m)
    assert rows.mean() < pad[1] + 0.25 * th
    # the original image sits at (pad left, pad top) and none of its rest-visible paint is masked
    vis = artreq.Scene(ir, images, joints).visible_texels("forearm_l")
    assert not (m[pad[1]:pad[1] + th, pad[0]:pad[0] + tw] & vis).any()
    crop = np.asarray(Image.open(os.path.join(out, it["crop"])))
    assert crop.shape[1] > 2 * crop.shape[0]   # rest | pose | highlighted


def test_squash_mask_sits_in_the_crook_the_elbow_bends_into(tmp_path):
    """Bent past its band, the elbow crushes the sleeve's triangles on the
    side it bends toward: elbow + (counter-clockwise) swings the hanging
    forearm toward +x, so the crook, and the mask, are on the sleeve's
    right half, at its elbow end."""
    ir, images, joints = synth_art.build(mode="joint", arms=1, scale=SCALE)
    out = str(tmp_path / "crook")
    doc = artreq.pack(ir, images, joints, out, targets={"elbow": {"out": 120.0, "in": 0.0}}, step=10,
                      ik_poses=None)
    sgn = artreq.outward_sign(ir, next(j for j in budget.load_joints(joints) if j["name"] == "elbow_l"))
    it = _item(doc, f"narrow_upper_arm_l__elbow_l{'p' if sgn > 0 else 'm'}")
    m = _mask(out, it)
    rows, cols = np.nonzero(m)
    h, w = m.shape
    assert (cols.mean() > w / 2) == (sgn > 0), "the crook side"
    assert rows.mean() > 0.6 * h, "at the elbow end of the sleeve"
    assert it["uses"][0]["worst"]["joints"]["elbow_l"] * sgn > 90


def test_clean_mask_is_the_shard_the_forearm_carries(tmp_path):
    """A forearm image cut with the torso behind it carries that torso
    paint along (budget artreq "clean"); the item's mask is exactly the
    carried texels, inside the forearm image."""
    ir, images, joints = synth_art.build(mode="joint", arms=1, scale=SCALE, shard=True)
    bj = budget.scan(ir, images, joints, step=4.0, max_angle=24.0, only={"elbow_l"})
    assert any(a["kind"] == "clean" and a["slot"] == "forearm_l" for a in bj["artreq"])
    out = str(tmp_path / "shard")
    doc = artreq.pack(ir, images, joints, out, budget=bj, targets={}, ik_poses=None)
    it = _item(doc, "clean_forearm_l__torso")
    m = _mask(out, it)
    sc = artreq.Scene(ir, images, joints)
    dup = ag.duplicates(sc.rest, "torso", "forearm_l")
    tex = np.unique(sc.rest.top_texel.reshape(-1)[np.nonzero(dup["carried"])[0]])
    th, tw = images["forearm_l"].shape[:2]
    want = np.zeros(th * tw, bool)
    want[tex[tex >= 0]] = True
    assert m.shape == (th, tw) and (m.reshape(-1) == want).all() and want.sum() > 50
    prompt = open(os.path.join(out, it["prompt"])).read()
    assert "# English" in prompt and "# 中文" in prompt and "mask" in prompt


def test_pack_folder_with_a_bridge_attachment(tmp_path):
    """The bridge's pose items become attachment requests with a guide mask
    on the character canvas at the requested pose (the joint ranges from
    bridge.json); every item folder has its mask, overlay or crop, and a
    two-language prompt; artreq.json keeps a sign-off slot."""
    ir, images, joints = synth_art.build(mode="joint", arms=1, scale=SCALE)
    bridge_doc = {"budget": {"groups": {"arm_l": {"joints": ["shoulder_l", "elbow_l"]}},
                             "fit": {"cheer": {"before": {"joints": {
                                 "shoulder_l": {"max_plus": 0.0, "max_minus": -20.0},
                                 "elbow_l": {"max_plus": 150.0, "max_minus": 0.0}}}}}}}
    bridge_items = [{"id": "pose_cheer_arm_l", "kind": "attachment", "anim": "cheer", "factor": 0.5,
                     "what": "a second l arm", "why": "the preset asks elbow_l 100..150 deg against soft -90..90, hard "
                                                     "-100..100; ...", "status": "open"}]
    out = str(tmp_path / "pack")
    doc = artreq.pack(ir, images, joints, out, bridge_doc=bridge_doc, bridge_items=bridge_items,
                      targets={"shoulder": {"out": 40.0, "raise": 165.0}, "elbow": {"out": 120.0}},
                      step=20, ik_poses=None, name="synth")
    on_disk = json.load(open(os.path.join(out, "artreq.json")))
    assert on_disk["schema"] == "spinegen-artreq/1" and on_disk["name"] == "synth"
    att = _item(doc, "pose_cheer_arm_l")
    assert att["kind"] == "attachment" and att["pose"] == {"shoulder_l": -20.0, "elbow_l": 150.0}
    assert att["mask"]["space"] == "canvas" and _mask(out, att).sum() > 1000
    up = _item(doc, "arm_up_l")
    assert abs(up["pose"]["shoulder_l"]) == 165.0
    # the bridge's requested range is planned too (clamped to the elbow target)
    assert any(g.startswith("cheer") for g in on_disk["poses"]["groups"])
    for it in doc["items"]:
        folder = os.path.join(out, it["folder"])
        assert os.path.exists(os.path.join(folder, "mask.png")) and os.path.exists(os.path.join(folder, "prompt.txt"))
        assert it["signoff"] == {"by": None, "at": None} and it["status"] == "open"
        if it["mask"]["space"] == "layer":
            m = _mask(out, it)
            th, tw = images[it["layer"]].shape[:2]
            p = it["mask"]["pad"]
            assert m.shape == (th + p[1] + p[3], tw + p[0] + p[2])
            assert os.path.exists(os.path.join(out, it["layer_overlay"]))
    readme = open(os.path.join(out, "README.md")).read()
    assert readme.startswith("# 補畫單：synth") and "提示詞（English）" in readme and "提示詞（中文）" in readme
    assert readme.count("<a id=") == len(doc["items"])
