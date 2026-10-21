"""Read and write Spine texture atlases (4.x `bounds:`/`offsets:`; the
legacy `xy:`/`size:`/`orig:`/`offset:` layout is read too).

READING
  Only geometry is needed on the Python side: a region attachment's world
  quad depends on the region's packed size, original size and whitespace
  offsets (lbs.region_offsets), and the L7 ATLAS gate compares a page's
  declared `size:` with the PNG. UVs come from the declared size, never from
  the PNG (SOTA §2), so parse() never opens an image.

WRITING (pack)
  SOTA §2 picks our own packer over libGDX TexturePacker: the rig builder
  has a dozen to a few dozen layer images, one page, and needs an exact
  contract more than tight packing:
    * page `size:` is the PNG's real size (L7; the runtime computes UVs from
      the declared size, so a mismatch silently shifts every UV);
    * transparent borders are stripped and recorded as `offsets:`, so
      raster.load_atlas_images (and every Spine runtime) pads the region
      back to the original image, pixel for pixel;
    * nothing is rotated or scaled, and `pma: false` (straight alpha), the
      emitted files' convention;
    * regions keep `pad` px of transparent gutter on every side, so linear
      filtering never bleeds a neighbour into an edge texel.
  The layout is a shelf packer (tallest first, rows left to right). It is
  deterministic for a given input, which keeps rebuilt atlases diffable.
"""
import numpy as np


def parse(text):
    """Atlas text -> {"pages": [...], "regions": {name: region}}.

    A region is {"page", "x", "y", "width", "height", "offset_x", "offset_y",
    "original_width", "original_height", "degrees", "index"}; width/height
    are the unrotated packed size, as the runtime keeps them. When two
    regions share a name (sequence frames), the first wins, as in
    TextureAtlas.findRegion.
    """
    pages, regions = [], {}
    page = region = None
    expect_page = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            expect_page = True
            region = None
            continue
        if ":" not in line:
            if expect_page or page is None:
                page = {"name": line, "width": 0, "height": 0, "format": None, "filter": None,
                        "repeat": None, "pma": False}
                pages.append(page)
                expect_page = False
                region = None
            else:
                region = {"page": page["name"], "name": line, "x": 0, "y": 0, "width": 0, "height": 0,
                          "offset_x": 0.0, "offset_y": 0.0, "original_width": None, "original_height": None,
                          "degrees": 0, "index": -1}
                if line not in regions:
                    regions[line] = region
            continue
        expect_page = False
        key, _, value = line.partition(":")
        key, vals = key.strip(), [v.strip() for v in value.split(",")]
        if region is None:
            _page_entry(page, key, vals)
        else:
            _region_entry(region, key, vals)
    for r in regions.values():
        if r["original_width"] is None:
            r["original_width"], r["original_height"] = r["width"], r["height"]
    return {"pages": pages, "regions": regions}


def load(path):
    with open(path) as f:
        return parse(f.read())


def _page_entry(page, key, vals):
    if key == "size":
        page["width"], page["height"] = int(vals[0]), int(vals[1])
    elif key == "format":
        page["format"] = vals[0]
    elif key == "filter":
        page["filter"] = vals
    elif key == "repeat":
        page["repeat"] = vals[0]
    elif key == "pma":
        page["pma"] = vals[0] == "true"


def _region_entry(r, key, vals):
    num = [float(v) for v in vals] if key in ("bounds", "xy", "size", "offsets", "offset", "orig") else None
    if key == "bounds":
        r["x"], r["y"], r["width"], r["height"] = num
    elif key == "xy":
        r["x"], r["y"] = num
    elif key == "size":
        r["width"], r["height"] = num
    elif key == "offsets":
        r["offset_x"], r["offset_y"], r["original_width"], r["original_height"] = num
    elif key == "offset":
        r["offset_x"], r["offset_y"] = num
    elif key == "orig":
        r["original_width"], r["original_height"] = num
    elif key == "rotate":
        v = vals[0]
        r["degrees"] = 90 if v == "true" else 0 if v == "false" else int(float(v))
    elif key == "index":
        r["index"] = int(vals[0])
    else:
        r.setdefault("extra", {})[key] = vals


# ---------------------------------------------------------------- writing

def pack(images, page_width=1024, pad=2, page_name="page.png", strip=True):
    """Pack {region name: (h, w, 4) uint8 RGBA} into one page.

    Returns (page (H, W, 4) uint8, atlas text). The page is as wide as its
    widest row and as tall as its rows (never wider than `page_width`
    unless one image is: then that image sets the width). With `strip`,
    each image's fully transparent border is cut off and written as
    `offsets:`; a fully transparent image keeps a 1 x 1 texel so its
    region still exists (L7: every attachment has a region).
    """
    if pad < 0:
        raise ValueError("pad must be >= 0")
    items = []
    for name, img in images.items():
        img = np.asarray(img)
        if img.ndim != 3 or img.shape[2] != 4 or img.dtype != np.uint8:
            raise ValueError(f"image {name!r}: need (h, w, 4) uint8 RGBA, got {img.shape} {img.dtype}")
        if "\n" in name or ":" in name or name != name.strip() or not name:
            raise ValueError(f"region name {name!r} cannot be written to an atlas")
        oh, ow = img.shape[:2]
        x0, y0, x1, y1 = _content_box(img) if strip else (0, 0, ow, oh)
        items.append({"name": name, "img": img[y0:y1, x0:x1], "ow": ow, "oh": oh,
                      "ox": x0, "oy": oh - y1})  # offsets count from the original's bottom-left
    order = sorted(range(len(items)), key=lambda i: (-items[i]["img"].shape[0], -items[i]["img"].shape[1],
                                                     items[i]["name"]))
    limit = max([page_width] + [it["img"].shape[1] + 2 * pad for it in items])
    x = y = row_h = width = 0
    for i in order:
        it = items[i]
        h, w = it["img"].shape[:2]
        if x > 0 and x + w + 2 * pad > limit:
            y += row_h
            x = row_h = 0
        it["x"], it["y"] = x + pad, y + pad
        x += w + 2 * pad
        row_h = max(row_h, h + 2 * pad)
        width = max(width, x)
    height = y + row_h
    page = np.zeros((max(height, 1), max(width, 1), 4), np.uint8)
    for it in items:
        h, w = it["img"].shape[:2]
        page[it["y"]:it["y"] + h, it["x"]:it["x"] + w] = it["img"]
    lines = [page_name, f"size:{page.shape[1]},{page.shape[0]}", "format:RGBA8888", "filter:Linear,Linear",
             "repeat:none", "pma:false"]
    for it in items:  # input order: the reader does not care, a diff does
        h, w = it["img"].shape[:2]
        lines.append(it["name"])
        lines.append(f"bounds:{it['x']},{it['y']},{w},{h}")
        if (w, h) != (it["ow"], it["oh"]):
            lines.append(f"offsets:{it['ox']},{it['oy']},{it['ow']},{it['oh']}")
    return page, "\n".join(lines) + "\n"


def write(path, images, page_width=1024, pad=2, strip=True):
    """pack() and write `<path>` (.atlas) plus its page PNG next to it
    (same basename); returns (atlas path, png path)."""
    import os

    from PIL import Image
    base = os.path.splitext(os.path.basename(path))[0]
    page, text = pack(images, page_width, pad, page_name=base + ".png", strip=strip)
    png = os.path.join(os.path.dirname(os.path.abspath(path)), base + ".png")
    Image.fromarray(page, "RGBA").save(png)
    with open(path, "w") as f:
        f.write(text)
    return path, png


def _content_box(img):
    """(x0, y0, x1, y1) of the pixels with alpha > 0; a 1 x 1 box for an
    empty image (a region needs at least one texel)."""
    a = img[..., 3] > 0
    if not a.any():
        return 0, 0, 1, 1
    rows, cols = np.flatnonzero(a.any(1)), np.flatnonzero(a.any(0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1
