"""spinegen.raster: the pixel grid, the rasteriser, texturing and compositing.

The art gates trust two things about a rendered frame: it sits where
spinegen.lbs (and so spine-core, via C1) puts the geometry, and its texture
is mapped the right way round (image v runs down, world y up). Both are
checked here against lbs directly, on synthetic rigs only.
"""
import math

import numpy as np
import pytest

from spinegen import ir as irmod
from spinegen import lbs, raster, synth_art


# ---------------------------------------------------------------- helpers

def _one_region_rig(w=40, h=24, rot=0.0, att_rot=0.0, x=30.0, y=10.0):
    """root -> arm bone at (x, y) rotated `rot`, one region slot on it."""
    ir = irmod.new_rig("one_region")
    ir["bones"] = [irmod.new_bone("root"), irmod.new_bone("arm", "root", x, y, rot, 50.0)]
    att = {"type": "region", "name": "img", "path": "img", "x": 12.0, "y": 3.0, "rotation": att_rot,
           "scale_x": 1.0, "scale_y": 1.0, "width": float(w), "height": float(h), "color": "ffffffff",
           "sequence": None}
    ir["slots"] = [irmod.new_slot("s", "arm", "img")]
    ir["skins"] = [{"name": "default", "bones": [], "ik": [], "transform": [], "path": [], "physics": [],
                    "color": None, "attachments": {"s": {"img": att}}}]
    assert not irmod.validate(ir)
    return ir, att


def _marked_image(w, h, mark=(0, 0, 6, 5)):
    """Opaque grey image with a red block at rows/cols [r0:r1, c0:c1] of
    the IMAGE (row 0 = top)."""
    img = np.zeros((h, w, 4), np.uint8)
    img[...] = (128, 128, 128, 255)
    c0, r0, c1, r1 = mark
    img[r0:r1, c0:c1] = (255, 0, 0, 255)
    return img


def _red_centroid(rgba):
    m = (rgba[..., 0] > 200) & (rgba[..., 1] < 60) & (rgba[..., 3] > 200)
    ii, jj = np.nonzero(m)
    assert len(ii), "marker not drawn"
    return np.array([jj.mean() + 0.5, ii.mean() + 0.5])


# ---------------------------------------------------------------- canvas

def test_canvas_round_trip_and_pixel_centres():
    c = raster.Canvas((-10.0, 20.0), (40.0, 30.0), scale=2.0)
    assert c.shape == (60, 80)
    # pixel (0, 0) covers world x in [-10, -9.5], y in [19.5, 20]
    assert np.allclose(c.to_world([[0.5, 0.5]]), [[-9.75, 19.75]])
    pts = np.random.default_rng(0).uniform(-10, 20, (50, 2))
    assert np.allclose(c.to_world(c.to_px(pts)), pts)
    sub, (i0, j0) = c.window((0.0, 10.0), 5.0)
    assert sub.scale == c.scale and (i0, j0) == (10, 10)
    assert np.allclose(sub.to_px([[0.0, 10.0]]) + [j0, i0], c.to_px([[0.0, 10.0]]))


# ---------------------------------------------------------------- rasteriser

def test_square_covers_each_pixel_once():
    """Two triangles sharing a diagonal cover a 10 x 10 square exactly: no
    pixel lost on the shared edge, none drawn twice."""
    pts = np.array([[2, 2], [12, 2], [12, 12], [2, 12]], float)
    pix, _, tri = raster.raster_triangles(pts, [[0, 1, 2], [0, 2, 3]], (16, 16))
    assert len(pix) == 100 and len(np.unique(pix)) == 100
    rows, cols = np.divmod(pix, 16)
    assert rows.min() == 2 and rows.max() == 11 and cols.min() == 2 and cols.max() == 11
    assert set(tri.tolist()) == {0, 1}


def test_later_triangle_wins_and_degenerate_covers_nothing():
    pts = np.array([[0, 0], [10, 0], [0, 10], [10, 10], [5, 5]], float)
    pix, _, tri = raster.raster_triangles(pts, [[0, 1, 2], [1, 3, 2]], (12, 12))
    k = pix == 2 * 12 + 2          # well inside triangle 0 only
    assert tri[k][0] == 0
    pix2, _, tri2 = raster.raster_triangles(pts, [[0, 1, 2], [0, 1, 2]], (12, 12))
    assert len(pix2) == len(np.unique(pix2)) and (tri2 == 1).all()
    pix3, _, _ = raster.raster_triangles(pts, [[0, 4, 3]], (12, 12))  # collinear
    assert len(pix3) == 0


def test_attributes_interpolate_affinely():
    pts = np.array([[0, 0], [20, 0], [0, 20]], float)
    pix, vals, _ = raster.raster_triangles(pts, [[0, 1, 2]], (20, 20), attrs=pts)
    rows, cols = np.divmod(pix, 20)
    assert np.allclose(vals, np.stack([cols + 0.5, rows + 0.5], 1))


def test_texture_sample_hits_texels_and_premultiplies():
    img = np.zeros((2, 2, 4), np.uint8)
    img[0, 0] = (255, 0, 0, 255)
    img[1, 1] = (0, 0, 255, 128)
    t = raster.Texture(img)
    s = t.sample(np.array([0.25, 0.75]), np.array([0.25, 0.75]))
    assert np.allclose(s[0], [1, 0, 0, 1])
    assert np.allclose(s[1], [0, 0, 128 / 255, 128 / 255], atol=1e-6)  # blue x alpha
    assert t.texel(np.array([0.75]), np.array([0.25]))[0] == 1  # row 0, col 1


# ---------------------------------------------------------------- geometry = lbs

@pytest.mark.parametrize("rot,att_rot", [(0.0, 0.0), (37.0, 0.0), (-120.0, 25.0)])
def test_region_lands_where_lbs_puts_it_right_way_up(rot, att_rot):
    """A region's coverage is lbs' world quad, and the image's top-left
    texel lands on lbs' top-left corner (region_offsets order: BL, TL, TR,
    BR): v runs down the image while world y runs up."""
    w, h = 40, 24
    ir, att = _one_region_rig(w, h, att_rot=att_rot)
    images = {"img": _marked_image(w, h, mark=(0, 0, 6, 5))}
    canvas = raster.Canvas((-40.0, 80.0), (140.0, 140.0), 1.0)
    r = raster.Renderer(ir, images, canvas=canvas)
    pose = {"arm": {"rotation": rot}}
    fr = r.frame(pose)
    ev = lbs.Evaluator(ir)
    world = ev.world(pose)
    m = world[ev.index["arm"]]
    corners = np.array([lbs.to_world(m, x, y) for x, y in lbs.region_offsets(att)])
    assert np.allclose(fr.geoms["s"].xy, corners)
    # coverage: area and centroid of the drawn pixels match the quad
    a = fr.alpha >= 0.5
    ii, jj = np.nonzero(a)
    assert abs(a.sum() - w * h) <= 2 * (w + h)
    cpx = canvas.to_px(corners).mean(0)
    assert np.allclose([jj.mean() + 0.5, ii.mean() + 0.5], cpx, atol=0.6)
    # orientation: the marker (image top-left) sits at the TL corner, inset
    # by its own half size along the quad's edges
    bl, tl, tr, _ = canvas.to_px(corners)
    ex, ey = (tr - tl) / w, (bl - tl) / h          # px per texel along u, v
    want = tl + ex * 3 + ey * 2.5
    assert np.allclose(_red_centroid(fr.rgba()), want, atol=1.0)


def test_skinned_mesh_texel_lands_on_its_lbs_position():
    """A weighted mesh (synthetic joint-mode arm) with the elbow bent: a
    marker painted at one texel is drawn at the world point lbs gives that
    UV (barycentric within the UV triangle that holds it)."""
    ir, images, joints = synth_art.build(mode="joint", scale=0.5)
    img = images["forearm_l"].copy()
    h, w = img.shape[:2]
    u, v = 0.5, 0.3
    r0, c0 = int(v * h), int(u * w)
    img[r0 - 2:r0 + 2, c0 - 2:c0 + 2] = (255, 0, 0, 255)
    images = dict(images, forearm_l=img)
    pose = {"forearm_l": {"rotation": ir["bones"][[b["name"] for b in ir["bones"]].index("forearm_l")]["rotation"]
                          + 50.0}}
    r = raster.Renderer(ir, images, margin=0.3)
    fr = r.frame(pose)
    g = fr.geoms["forearm_l"]
    ev = lbs.Evaluator(ir)
    assert np.allclose(g.xy, ev.attachment_vertices(ev.world(pose), "forearm_l", g.att))
    # world point of (u, v) through the mesh
    uv = g.uv
    target = np.array([(c0 + 0.0) / w, (r0 + 0.0) / h])
    for t in g.tris:
        A, B, C = uv[t]
        M = np.array([B - A, C - A]).T
        lam = np.linalg.solve(M, target - A)
        if lam.min() >= -1e-9 and lam.sum() <= 1 + 1e-9:
            P = g.xy[t[0]] + lam[0] * (g.xy[t[1]] - g.xy[t[0]]) + lam[1] * (g.xy[t[2]] - g.xy[t[0]])
            break
    else:
        pytest.fail("uv not inside the mesh")
    got = _red_centroid(fr.rgba())
    assert np.allclose(got, r.canvas.to_px([P])[0], atol=1.5)


# ---------------------------------------------------------------- compositing

def test_premultiplied_over_and_topmost_slot():
    ir, _ = _one_region_rig(20, 20)
    att2 = dict(ir["skins"][0]["attachments"]["s"]["img"], name="img2", path="img2", x=20.0)
    ir["slots"].append(irmod.new_slot("s2", "arm", "img2"))
    ir["skins"][0]["attachments"]["s2"] = {"img2": att2}
    half = np.zeros((20, 20, 4), np.uint8)
    half[...] = (255, 255, 255, 128)
    images = {"img": half, "img2": _marked_image(20, 20, mark=(0, 0, 0, 0))}
    images["img2"][..., 3] = 128
    fr = raster.Renderer(ir, images, margin=0.2).frame()
    a = fr.alpha
    a1, a2 = fr.slot_alpha("s"), fr.slot_alpha("s2")
    both = (a1 > 0) & (a2 > 0)
    assert both.any()
    expect = 1 - (1 - 128 / 255) ** 2
    assert np.allclose(a[both], expect, atol=1e-5)
    assert np.allclose(fr.union_alpha({"s", "s2"})[both], expect, atol=1e-5)
    # a single 50% layer is "drawn" (>= 0.5) and owns the pixel; overlap -> the later slot
    assert (fr.top[both] == 1).all()
    only1 = (a1 > 0) & (a2 == 0)
    assert (fr.top[only1] == 0).all()


def test_supersample_keeps_the_canvas():
    ir, images, _ = synth_art.build(mode="rigid", scale=0.3)
    plain = raster.render(ir, images)
    aa = raster.render(ir, images, supersample=2)
    assert plain.shape == aa.shape
    # anti-aliasing only moves edge pixels
    diff = np.abs(plain[..., 3].astype(int) - aa[..., 3].astype(int))
    assert (diff > 128).mean() < 0.02


def test_slot_masks_are_per_slot_coverage():
    ir, images, _ = synth_art.build(mode="rigid", scale=0.3)
    masks = raster.slot_masks(ir, images)
    assert set(masks) == {s["name"] for s in ir["slots"]}
    full = raster.render(ir, images)[..., 3] / 255.0
    union = np.zeros_like(full)
    for m in masks.values():
        union = union + m * (1 - union)
    assert np.abs(union - full).max() < 2 / 255


# ---------------------------------------------------------------- images

def test_atlas_region_is_padded_back_to_its_original_size(tmp_path):
    """A stripped region (packed 4 x 3 of an original 10 x 8, offset 3, 1
    from the original's BOTTOM-left) comes back as the 10 x 8 image with
    the packed pixels at rows 4..6 (from the top), cols 3..6."""
    from PIL import Image
    page = np.zeros((16, 16, 4), np.uint8)
    page[5:8, 2:6] = (10, 200, 30, 255)
    Image.fromarray(page, "RGBA").save(tmp_path / "p.png")
    (tmp_path / "a.atlas").write_text(
        "p.png\nsize:16,16\nfilter:Linear,Linear\n"
        "full\nbounds:2,5,4,3\n"
        "stripped\nbounds:2,5,4,3\noffsets:3,1,10,8\n")
    imgs = raster.load_atlas_images(str(tmp_path / "a.atlas"))
    assert imgs["full"].shape == (3, 4, 4) and (imgs["full"][..., 1] == 200).all()
    s = imgs["stripped"]
    assert s.shape == (8, 10, 4)
    ii, jj = np.nonzero(s[..., 3])
    assert (ii.min(), ii.max(), jj.min(), jj.max()) == (4, 6, 3, 6)


def test_render_cli_writes_png(tmp_path):
    rig, imgdir, _ = synth_art.write(str(tmp_path / "c"), mode="rigid", scale=0.3)
    out = tmp_path / "o.png"
    raster.main([rig, imgdir, "-o", str(out), "--pose", '{"forearm_l": {"rotation": -50}}'])
    from PIL import Image
    im = np.asarray(Image.open(out))
    assert im.shape[2] == 4 and im[..., 3].max() == 255
    assert math.isfinite(float(im.mean()))
