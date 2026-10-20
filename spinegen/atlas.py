"""Read Spine texture atlases (4.x `bounds:`/`offsets:` and the legacy
`xy:`/`size:`/`orig:`/`offset:` layout).

Only geometry is needed on the Python side: a region attachment's world quad
depends on the region's packed size, original size and whitespace offsets
(lbs.region_offsets), and the L7 ATLAS gate compares a page's declared
`size:` with the PNG. UVs come from the declared size, never from the PNG
(SOTA §2), so nothing here opens an image.
"""


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
