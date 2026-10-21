"""spinegen.atlas.pack: the page our rigs ship with.

What the rest of the pipeline relies on: the page's declared size is its
real size (L7), every region reads back pixel for pixel through
raster.load_atlas_images (whitespace offsets included), regions never
overlap and keep their gutter, and the text parses as a 4.x atlas.
"""
import numpy as np
import pytest

from spinegen import atlas, raster


def _blob(h, w, box, rgb, seed):
    """An (h, w) RGBA image, transparent except a noisy block at box =
    (x0, y0, x1, y1) with an anti-aliased (alpha 128) rim."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 4), np.uint8)
    x0, y0, x1, y1 = box
    img[y0:y1, x0:x1, :3] = np.clip(np.array(rgb) + rng.integers(-30, 30, (y1 - y0, x1 - x0, 3)), 0, 255)
    img[y0:y1, x0:x1, 3] = 255
    img[y0, x0:x1, 3] = 128
    img[y1 - 1, x0:x1, 3] = 128
    return img


@pytest.fixture
def images():
    return {
        "torso": _blob(120, 80, (10, 4, 70, 118), (200, 60, 40), 1),       # transparent border
        "arm_l": _blob(90, 30, (0, 0, 30, 90), (40, 90, 200), 2),          # no border at all
        "arm_r": _blob(90, 34, (3, 0, 34, 85), (40, 90, 200), 3),          # border on two sides
        "head": _blob(64, 64, (8, 8, 56, 56), (120, 80, 50), 4),
        "empty": np.zeros((10, 12, 4), np.uint8),                          # nothing drawn
    }


def _write(tmp_path, images, **kw):
    path, png = atlas.write(str(tmp_path / "rig.atlas"), images, **kw)
    return path, png


def test_round_trip_pixel_for_pixel(tmp_path, images):
    path, _ = _write(tmp_path, images)
    back = raster.load_atlas_images(path)
    assert set(back) == set(images)
    for name, img in images.items():
        assert back[name].shape == img.shape, name
        assert np.array_equal(back[name], img), name


def test_page_size_is_real_size_and_text_is_4x(tmp_path, images):
    from PIL import Image
    path, png = _write(tmp_path, images)
    text = open(path).read()
    at = atlas.parse(text)
    (page,) = at["pages"]
    w, h = Image.open(png).size
    assert (page["width"], page["height"]) == (w, h)
    assert page["pma"] is False
    assert "bounds:" in text and "xy:" not in text and "orig:" not in text
    # stripped regions carry offsets, unstripped ones do not need them
    assert at["regions"]["torso"]["width"] == 60 and at["regions"]["torso"]["original_width"] == 80
    assert at["regions"]["torso"]["offset_x"] == 10 and at["regions"]["torso"]["offset_y"] == 2
    assert at["regions"]["arm_l"]["width"] == 30 and at["regions"]["arm_l"]["original_height"] == 90


def test_regions_disjoint_with_gutter(images):
    pad = 3
    page, text = atlas.pack(images, page_width=128, pad=pad)
    regs = atlas.parse(text)["regions"]
    boxes = [(r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"]) for r in regs.values()]
    for i, a in enumerate(boxes):
        assert a[0] >= pad and a[1] >= pad
        assert a[2] <= page.shape[1] - pad and a[3] <= page.shape[0] - pad
        for b in boxes[i + 1:]:
            gap_x = max(b[0] - a[2], a[0] - b[2])
            gap_y = max(b[1] - a[3], a[1] - b[3])
            assert max(gap_x, gap_y) >= 2 * pad, (a, b)
    assert page.shape[1] <= 128


def test_wide_image_sets_page_width():
    wide = {"wide": np.full((4, 300, 4), 255, np.uint8), "small": np.full((5, 5, 4), 255, np.uint8)}
    page, text = atlas.pack(wide, page_width=64, pad=2)
    assert page.shape[1] == 304
    r = atlas.parse(text)["regions"]["wide"]
    assert (r["x"], r["width"]) == (2, 300)


def test_deterministic_and_rejects_bad_input(images):
    a = atlas.pack(images)
    b = atlas.pack(dict(reversed(list(images.items()))))
    assert np.array_equal(a[0], b[0])
    with pytest.raises(ValueError):
        atlas.pack({"bad:name": np.zeros((2, 2, 4), np.uint8)})
    with pytest.raises(ValueError):
        atlas.pack({"rgb": np.zeros((2, 2, 3), np.uint8)})
