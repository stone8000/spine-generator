"""Software renderer for IR rigs: textured meshes and region quads on a pixel
grid, with per-slot coverage kept for the art gates.

    python -m spinegen.raster <rig.json | skeleton.json> <images dir | .atlas> -o out.png
        [--pose JSON] [--scale 1] [--supersample 2]

WHY A RENDERER OF OUR OWN
  The art gates (SOTA §5 G4 seam gap, G5 holes, G6 ghost) are statements
  about pixels: background showing inside a joint, an enclosed hole, an
  overlap band or a stale copy of the arm becoming visible. The budget
  scanner (spinegen.budget) asks them about every 2 deg of every joint, a
  thousand frames per character, so they must run in-process and fast.
  Geometry is NOT re-derived here: world vertices come from
  spinegen.lbs (Evaluator.world / attachment_vertices), which C1 pins to
  spine-core 4.2.120 within 1e-4 px, so a rendered frame sits exactly where
  the official runtime puts the mesh. Only texturing and compositing are
  ours, and the gates only threshold alpha at 0.5, where an approximation
  of the GPU's filtering does not move a pixel.

HOW IT DRAWS (what a Spine renderer does, minus the GPU)
  * Slots in draw order (setup order unless `order` is given); each shows
    its attachment from the active skin (lbs resolves it).
  * A mesh draws its triangles in index order with affine texture mapping
    (no perspective, like every Spine runtime). A region is a quad of two
    triangles, [0,1,2, 2,3,0], over lbs.region_offsets' corners.
  * Triangles are rasterised all at once per slot (numpy, no per-triangle
    Python loop): candidate pixel centres from each triangle's bounding
    box, a barycentric inside test, and where triangles of one slot
    overlap (shared edges, a fold) the later triangle wins, as it would
    when drawn last. One slot therefore covers each pixel at most once and
    never double-blends along its own internal edges.
  * Texture lookup is bilinear on premultiplied RGBA, clamped to the edge;
    slot colour x attachment colour tint it. Compositing is premultiplied
    "over" (additive slots add colour and no coverage). The output is
    straight (un-premultiplied) RGBA uint8.
  * Images are whole attachment images: an atlas region stripped of
    whitespace is padded back to its original size (load_atlas_images), so
    a mesh's UVs (which span the original image) and a region's full
    width x height quad both map onto the image directly.

CANVAS
  A Canvas is a pixel grid over WORLD space (y up): `origin` is the world
  (x, y) of the top-left corner of pixel (0, 0) — the left edge and the TOP
  edge of the box — `size` its width and height in world units and `scale`
  pixels per world unit. Row index grows downward, i.e. toward smaller
  world y. This is a rendering transform only; layer/canvas space of the
  rig builder is a different thing and is converted there, not here.

WHAT A FRAME KEEPS (for the gates)
  Per slot its fragments: flat pixel index, premultiplied tinted RGBA and
  the texel it sampled; per pixel the topmost slot drawn with alpha >= 0.5
  and that slot's texel. Texel identity is what lets G6 say "this pixel now
  shows a part of the torso that the arm hid at rest".
"""
import argparse
import json
import math
import os
from collections import namedtuple

import numpy as np

from . import ir as irmod
from . import lbs

QUAD_TRIS = np.array([[0, 1, 2], [2, 3, 0]], dtype=np.int64)
# lbs.region_offsets corner order (attachment space, y up): bottom-left,
# top-left, top-right, bottom-right; the image's v runs down.
QUAD_UVS = np.array([[0.0, 1.0], [0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
MIN_ALPHA = 1.0 / 512  # fragments below this are dropped: they change no 8-bit pixel
COVER = 0.5            # "drawn" for the gates (SOTA §5: alpha < 0.5 is background)


# ---------------------------------------------------------------- canvas

class Canvas:
    """A pixel grid over world space; see the module docstring."""

    def __init__(self, origin, size, scale=1.0):
        self.x0, self.y0 = float(origin[0]), float(origin[1])
        self.w, self.h = float(size[0]), float(size[1])
        self.scale = float(scale)
        self.W = max(1, int(math.ceil(self.w * self.scale - 1e-9)))
        self.H = max(1, int(math.ceil(self.h * self.scale - 1e-9)))

    @property
    def shape(self):
        return (self.H, self.W)

    @property
    def origin(self):
        return (self.x0, self.y0)

    @property
    def size(self):
        return (self.w, self.h)

    def to_px(self, xy):
        """World (n, 2) -> continuous pixel coords (col, row); pixel (i, j)
        has its centre at (j + 0.5, i + 0.5)."""
        xy = np.asarray(xy, float).reshape(-1, 2)
        return np.stack([(xy[:, 0] - self.x0) * self.scale, (self.y0 - xy[:, 1]) * self.scale], 1)

    def to_world(self, px):
        px = np.asarray(px, float).reshape(-1, 2)
        return np.stack([self.x0 + px[:, 0] / self.scale, self.y0 - px[:, 1] / self.scale], 1)

    def pixel_affine(self, m):
        """2x3 pixel-space matrix of a world affine (a, b, c, d, x, y) acting
        on world points, for warping masks (cv2.warpAffine convention)."""
        a, b, c, d, tx, ty = m
        s, x0, y0 = self.scale, self.x0, self.y0
        # p = S(w) with S(w) = ((wx - x0) s, (y0 - wy) s); world map w' = M w + t
        # => p' = S(M S^-1(p) + t); S^-1(p) = (x0 + px/s, y0 - py/s)
        A = np.array([[a, -b], [-c, d]], float)
        wx0, wy0 = a * x0 + b * y0 + tx, c * x0 + d * y0 + ty
        off = np.array([(wx0 - x0) * s, (y0 - wy0) * s])
        return np.hstack([A, off[:, None]])

    def window(self, center, radius):
        """A sub-canvas on the same pixel grid covering a square of half-side
        `radius` (world units) around `center`, clipped to this canvas."""
        cx, cy = self.to_px([center])[0]
        r = radius * self.scale
        j0, j1 = max(0, int(math.floor(cx - r))), min(self.W, int(math.ceil(cx + r)))
        i0, i1 = max(0, int(math.floor(cy - r))), min(self.H, int(math.ceil(cy + r)))
        j1, i1 = max(j1, j0 + 1), max(i1, i0 + 1)
        return Canvas((self.x0 + j0 / self.scale, self.y0 - i0 / self.scale),
                      ((j1 - j0) / self.scale, (i1 - i0) / self.scale), self.scale), (i0, j0)

    def scaled(self, scale):
        return Canvas(self.origin, self.size, scale)

    def to_dict(self):
        return {"origin": list(self.origin), "size": list(self.size), "scale": self.scale, "shape": list(self.shape)}


def bbox_canvas(xy, margin=0.1, scale=1.0, min_size=1.0):
    """Canvas around world points with `margin` x the box size on each side."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    lo, hi = xy.min(0), xy.max(0)
    span = np.maximum(hi - lo, min_size)
    lo, hi = lo - margin * span, hi + margin * span
    return Canvas((lo[0], hi[1]), (hi[0] - lo[0], hi[1] - lo[1]), scale)


# ---------------------------------------------------------------- textures

class Texture:
    """An RGBA image as premultiplied float32, sampled by normalised (u, v)
    with v down (Spine's mesh UV convention)."""

    def __init__(self, rgba):
        a = _as_rgba(rgba).astype(np.float32) / 255.0
        a[..., :3] *= a[..., 3:4]
        self.data = a
        self.h, self.w = a.shape[:2]
        self._flat = a.reshape(-1, 4)

    def sample(self, u, v):
        """Bilinear, clamp-to-edge; returns premultiplied (n, 4)."""
        x = np.asarray(u, float) * self.w - 0.5
        y = np.asarray(v, float) * self.h - 0.5
        x0, y0 = np.floor(x), np.floor(y)
        fx, fy = (x - x0).astype(np.float32)[:, None], (y - y0).astype(np.float32)[:, None]
        x0, y0 = x0.astype(np.int64), y0.astype(np.int64)
        xa, xb = np.clip(x0, 0, self.w - 1), np.clip(x0 + 1, 0, self.w - 1)
        ya, yb = np.clip(y0, 0, self.h - 1) * self.w, np.clip(y0 + 1, 0, self.h - 1) * self.w
        f = self._flat
        top = f[ya + xa] * (1 - fx) + f[ya + xb] * fx
        bot = f[yb + xa] * (1 - fx) + f[yb + xb] * fx
        return top * (1 - fy) + bot * fy

    def texel(self, u, v):
        """Flat index of the texel under (u, v) (nearest)."""
        x = np.clip(np.floor(np.asarray(u, float) * self.w), 0, self.w - 1).astype(np.int64)
        y = np.clip(np.floor(np.asarray(v, float) * self.h), 0, self.h - 1).astype(np.int64)
        return y * self.w + x


def _as_rgba(img):
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.stack([a, a, a, np.full_like(a, 255)], -1)
    elif a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, a.dtype)], -1)
    if a.dtype != np.uint8:
        a = np.clip(np.round(a.astype(float) * (255.0 if a.max() <= 1.0 else 1.0)), 0, 255).astype(np.uint8)
    return a


def _hex_rgba(s):
    if not s:
        return np.ones(4, np.float32)
    s = s.lstrip("#")
    vals = [int(s[i:i + 2], 16) / 255.0 for i in range(0, len(s), 2)]
    if len(vals) == 3:
        vals.append(1.0)
    return np.array(vals[:4], np.float32)


# ---------------------------------------------------------------- rasteriser

def raster_triangles(pts, tris, shape, attrs=None):
    """Pixels covered by triangles, the later triangle winning each pixel.

    pts (n, 2) continuous pixel coords (col, row), tris (m, 3), shape (H, W),
    attrs (n, k) per-vertex values interpolated affinely (barycentric).
    Returns (pix, values, tri): flat pixel indices (unique), the
    interpolated attrs (F, k) (None without attrs) and the triangle index
    of each pixel. A pixel is covered when its centre is inside or on the
    triangle; degenerate triangles cover nothing.

    Scanline form, vectorised over (triangle, row) pairs: each row's span
    comes from the three edge half-planes, so only covered pixels are ever
    generated (a bounding-box test would generate twice as many).
    """
    H, W = shape
    pts = np.asarray(pts, float).reshape(-1, 2)
    tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    k_attr = 0 if attrs is None else np.asarray(attrs).reshape(len(pts), -1).shape[1]
    empty = (np.zeros(0, np.int64), None if attrs is None else np.zeros((0, k_attr)), np.zeros(0, np.int64))
    if len(tris) == 0 or len(pts) == 0:
        return empty
    P = pts[tris]
    x, y = P[..., 0], P[..., 1]
    d = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    good = np.isfinite(d) & (np.abs(d) > 1e-12)
    i0 = np.clip(np.ceil(y.min(1) - 0.5 - 1e-9), 0, H).astype(np.int64)
    i1 = np.clip(np.floor(y.max(1) - 0.5 + 1e-9), -1, H - 1).astype(np.int64)
    ny = np.where(good, np.maximum(i1 - i0 + 1, 0), 0)
    ids = np.nonzero(ny)[0]
    if len(ids) == 0:
        return empty
    # edge half-planes A x + B y + C >= 0 (inside), oriented by sign(d)
    sg = np.sign(d)[:, None]
    xn, yn = np.roll(x, -1, 1), np.roll(y, -1, 1)
    A = -sg * (yn - y)
    B = sg * (xn - x)
    C = sg * ((yn - y) * x - (xn - x) * y)
    # one entry per (triangle, row)
    t = np.repeat(ids, ny[ids])
    row = i0[t] + (np.arange(len(t)) - np.repeat(np.cumsum(ny[ids]) - ny[ids], ny[ids]))
    yc = row + 0.5
    rhs = -(B[t] * yc[:, None] + C[t])          # A x >= rhs
    At = A[t]
    with np.errstate(divide="ignore", invalid="ignore"):
        bound = rhs / At
    lo = np.where(At > 0, bound, -np.inf).max(1)
    hi = np.where(At < 0, bound, np.inf).min(1)
    flat_ok = np.where(At == 0, rhs <= 1e-9 * (np.abs(B[t]) + 1), True).all(1)
    eps = 1e-7
    j0 = np.clip(np.ceil(lo - 0.5 - eps), 0, W).astype(np.int64)
    j1 = np.clip(np.floor(hi - 0.5 + eps), -1, W - 1).astype(np.int64)
    nx = np.where(flat_ok, np.maximum(j1 - j0 + 1, 0), 0)
    if nx.sum() == 0:
        return empty
    tt = np.repeat(t, nx)
    col = np.repeat(j0, nx) + (np.arange(len(tt)) - np.repeat(np.cumsum(nx) - nx, nx))
    rr = np.repeat(row, nx)
    pix = rr * W + col
    # last triangle wins: entries are in triangle order, keep the highest
    lo_p = pix.min()
    buf = np.full(int(pix.max() - lo_p + 1), -1, np.int64)
    np.maximum.at(buf, pix - lo_p, np.arange(len(pix)))
    keep = buf[buf >= 0]
    pix, tt, col, rr = pix[keep], tt[keep], col[keep], rr[keep]
    vals = None
    if attrs is not None:
        Av = np.asarray(attrs, float).reshape(len(pts), -1)
        ex, ey = col + 0.5 - x[tt, 0], rr + 0.5 - y[tt, 0]
        dt = d[tt]
        l1 = (ex * (y[tt, 2] - y[tt, 0]) - ey * (x[tt, 2] - x[tt, 0])) / dt
        l2 = (ey * (x[tt, 1] - x[tt, 0]) - ex * (y[tt, 1] - y[tt, 0])) / dt
        a0 = Av[tris[tt, 0]]
        vals = a0 + l1[:, None] * (Av[tris[tt, 1]] - a0) + l2[:, None] * (Av[tris[tt, 2]] - a0)
    return pix, vals, tt


# ---------------------------------------------------------------- frames

SlotGeom = namedtuple("SlotGeom", "slot att xy uv tris tex_key tint blend")


class Frag:
    """One slot's contribution to a frame: flat pixel index, premultiplied
    tinted RGBA, sampled texel (flat index into the slot's texture)."""
    __slots__ = ("idx", "rgba", "texel", "tex_key")

    def __init__(self, idx, rgba, texel, tex_key):
        self.idx, self.rgba, self.texel, self.tex_key = idx, rgba, texel, tex_key

    @property
    def alpha(self):
        return self.rgba[:, 3]


class Frame:
    """A rendered frame: composite image plus what the gates need."""

    def __init__(self, canvas, world, slots, frags, image, top, top_texel, geoms):
        self.canvas = canvas
        self.world = world          # bone world matrices (lbs order)
        self.slots = slots          # drawn slot names, draw order
        self.frags = frags          # {slot: Frag}
        self.image = image          # (H, W, 4) premultiplied float32
        self.top = top              # (H, W) int16: index into slots of the topmost slot with alpha >= 0.5, -1 none
        self.top_texel = top_texel  # (H, W) int64: that slot's texel
        self.geoms = geoms          # {slot: SlotGeom}

    @property
    def shape(self):
        return self.canvas.shape

    def rgba(self):
        """Straight-alpha uint8 RGBA."""
        return premult_to_rgba(self.image)

    @property
    def alpha(self):
        return self.image[..., 3]

    def slot_alpha(self, slot):
        out = np.zeros(self.canvas.H * self.canvas.W, np.float32)
        f = self.frags.get(slot)
        if f is not None:
            out[f.idx] = f.alpha
        return out.reshape(self.canvas.shape)

    def union_alpha(self, slots):
        """Composite ("over") alpha of a subset of slots."""
        out = np.zeros(self.canvas.H * self.canvas.W, np.float32)
        for s in self.slots:
            if s in slots and s in self.frags:
                f = self.frags[s]
                out[f.idx] = out[f.idx] + f.alpha * (1 - out[f.idx])
        return out.reshape(self.canvas.shape)

    def top_slot_name(self, index):
        return self.slots[index] if index >= 0 else None


def premult_to_rgba(img):
    a = img[..., 3:4]
    rgb = np.where(a > 1e-6, img[..., :3] / np.maximum(a, 1e-6), 0.0)
    out = np.concatenate([rgb, a], -1)
    return np.clip(np.round(out * 255.0), 0, 255).astype(np.uint8)


class Renderer:
    """Renders one IR with one set of images; bindings, textures and the
    canvas are prepared once and reused across poses.

    images      {attachment path or name: (h, w, 4) uint8 RGBA} — whole
                attachment images (see load_atlas_images for atlases)
    canvas      a Canvas, or None for the setup bounding box + `margin`
    missing     "raise" (default) or "skip" for an attachment without image
    """

    def __init__(self, ir, images, skin=None, canvas=None, scale=1.0, margin=0.1, evaluator=None,
                 missing="raise"):
        self.ir = ir
        self.ev = evaluator or lbs.Evaluator(ir, None, skin)
        self.images = images
        self.missing = missing
        self._textures = {}
        self.warnings = []
        self.slot_index = {s["name"]: i for i, s in enumerate(ir["slots"])}
        self.canvas = canvas if canvas is not None else bbox_canvas(self.setup_points(), margin, scale)

    # ---- geometry

    def setup_points(self, world=None):
        world = world if world is not None else self.ev.world()
        pts = [g.xy for g in self.geometry(world).values()]
        if not pts:
            return np.zeros((1, 2))
        return np.concatenate(pts)

    def texture(self, key):
        if key not in self._textures:
            img = self.images.get(key)
            self._textures[key] = Texture(img) if img is not None else None
        return self._textures[key]

    def _tex_key(self, att):
        for k in (att.get("path"), att.get("name")):
            if k is not None and k in self.images:
                return k
        return att.get("path") or att.get("name")

    def geometry(self, world, attachments=None, deform=None, slots=None, order=None):
        """{slot: SlotGeom} for the drawable attachments shown, in draw order."""
        out = {}
        shown = dict(self.ev.shown(attachments))
        names = order if order is not None else [s["name"] for s in self.ir["slots"]]
        for name in names:
            if slots is not None and name not in slots:
                continue
            att = shown.get(name)
            if att is None or att["type"] not in ("region", "mesh", "linkedmesh"):
                if att is not None and att["type"] == "clipping":
                    self._warn(f"slot {name}: clipping is not rendered (drawn unclipped)")
                continue
            slot = self.ir["slots"][self.slot_index[name]]
            tint = _hex_rgba(slot.get("color")) * _hex_rgba(att.get("color"))
            key = self._tex_key(att)
            if att["type"] == "region":
                xy = self._region_xy(world, slot, att, key)
                if xy is None:
                    continue
                uv, tris = QUAD_UVS, QUAD_TRIS
            else:
                geo = irmod.geometry_of(self.ir, name, att)
                xy = self.ev.attachment_vertices(world, name, att, (deform or {}).get(name))
                uv = np.asarray(geo["uvs"], float).reshape(-1, 2)
                tris = np.asarray(geo["triangles"], np.int64).reshape(-1, 3)
            out[name] = SlotGeom(name, att, xy, uv, tris, key, tint, slot.get("blend") or "normal")
        return out

    def _region_xy(self, world, slot, att, key):
        a = att
        if att.get("width") is None or att.get("height") is None:
            tex = self.texture(key)
            if tex is None:
                return None
            a = dict(att, width=float(tex.w), height=float(tex.h))
        m = world[self.ev.index[slot["bone"]]]
        return np.array([lbs.to_world(m, x, y) for x, y in lbs.region_offsets(a, None)])

    def _warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ---- rasterising

    def fragments(self, g, canvas=None):
        canvas = canvas or self.canvas
        tex = self.texture(g.tex_key)
        if tex is None:
            if self.missing == "skip":
                self._warn(f"slot {g.slot}: no image for {g.tex_key!r}; not drawn")
                return Frag(np.zeros(0, np.int64), np.zeros((0, 4), np.float32), np.zeros(0, np.int64), g.tex_key)
            raise KeyError(f"slot {g.slot}: no image {g.tex_key!r} (images has {len(self.images)} keys)")
        pix, uv, _ = raster_triangles(canvas.to_px(g.xy), g.tris, canvas.shape, g.uv)
        if len(pix) == 0:
            return Frag(pix, np.zeros((0, 4), np.float32), np.zeros(0, np.int64), g.tex_key)
        rgba = tex.sample(uv[:, 0], uv[:, 1]) * g.tint
        keep = rgba[:, 3] >= MIN_ALPHA
        return Frag(pix[keep], rgba[keep].astype(np.float32), tex.texel(uv[keep, 0], uv[keep, 1]), g.tex_key)

    def frame(self, pose=None, state=None, attachments=None, deform=None, slots=None, order=None,
              world=None, canvas=None, reuse=None, constraints=True):
        """Render one pose. `reuse` {slot: Frag} skips rasterising slots the
        caller knows did not move (the budget scanner's static parts)."""
        canvas = canvas or self.canvas
        if world is None:
            world = self.ev.world(pose, state, constraints=constraints)
        geoms = self.geometry(world, attachments, deform, slots, order)
        frags = {}
        for name, g in geoms.items():
            frags[name] = reuse[name] if reuse and name in reuse else self.fragments(g, canvas)
        return self.composite(canvas, world, geoms, frags)

    def composite(self, canvas, world, geoms, frags):
        n = canvas.H * canvas.W
        img = np.zeros((n, 4), np.float32)
        top = np.full(n, -1, np.int16)
        top_tex = np.full(n, -1, np.int64)
        names = list(geoms)
        for i, name in enumerate(names):
            f = frags[name]
            if len(f.idx) == 0:
                continue
            blend = geoms[name].blend
            if blend == "additive":
                img[f.idx, :3] += f.rgba[:, :3]
                continue
            if blend not in ("normal", None):
                self._warn(f"slot {name}: blend {blend!r} drawn as normal")
            dst = img[f.idx]
            img[f.idx] = f.rgba + dst * (1 - f.rgba[:, 3:4])
            solid = f.rgba[:, 3] >= COVER
            top[f.idx[solid]] = i
            top_tex[f.idx[solid]] = f.texel[solid]
        np.clip(img, 0.0, 1.0, out=img)
        return Frame(canvas, world, names, frags, img.reshape(canvas.H, canvas.W, 4),
                     top.reshape(canvas.shape), top_tex.reshape(canvas.shape), geoms)

    # ---- texture space

    def texture_lookup(self, g, frame_map, canvas=None, fill=-1):
        """For every texel of g's texture, the value of `frame_map` (H, W)
        at the canvas pixel that texel lands on in g's pose (nearest);
        `fill` where the mesh does not cover the texel or it lands off the
        canvas. This is how a per-pixel fact about a frame (who covered it
        at rest) becomes a per-texel fact that moves with the art."""
        canvas = canvas or self.canvas
        tex = self.texture(g.tex_key)
        tw, th = tex.w, tex.h
        out = np.full(tw * th, fill, dtype=np.asarray(frame_map).dtype)
        uv_px = np.asarray(g.uv, float) * [tw, th]
        pix, pxy, _ = raster_triangles(uv_px, g.tris, (th, tw), canvas.to_px(g.xy))
        if len(pix) == 0:
            return out.reshape(th, tw)
        j = np.floor(pxy[:, 0]).astype(np.int64)
        i = np.floor(pxy[:, 1]).astype(np.int64)
        ok = (j >= 0) & (j < canvas.W) & (i >= 0) & (i < canvas.H)
        out[pix[ok]] = np.asarray(frame_map)[i[ok], j[ok]]
        return out.reshape(th, tw)


# ---------------------------------------------------------------- public API

def _canvas_from(r_or_ev_points, size, origin, scale):
    if size is not None and origin is not None:
        return Canvas(origin, size, scale)
    if size is not None or origin is not None:
        raise ValueError("give both size and origin (world units) or neither")
    return None


def render(ir, images, pose=None, size=None, origin=None, skin=None, slots=None, scale=1.0,
           supersample=1, **kw):
    """(H, W, 4) uint8 straight RGBA of `ir` in `pose` (lbs-style absolute
    local values per bone). size/origin: the output canvas in world units
    (origin = world x, y of the top-left corner); default the setup bounding
    box + 10%. slots: render only these. supersample k renders at k x scale
    and box-filters down (anti-aliased edges, for previews)."""
    r = Renderer(ir, images, skin=skin, canvas=_canvas_from(None, size, origin, scale), scale=scale)
    if supersample > 1:
        c = r.canvas
        big = Canvas(c.origin, (c.W / c.scale, c.H / c.scale), c.scale * supersample)
        img = r.frame(pose, slots=slots, canvas=big, **kw).image
        k = supersample
        img = img[:c.H * k, :c.W * k].reshape(c.H, k, c.W, k, 4).mean((1, 3))
        return premult_to_rgba(img)
    return r.frame(pose, slots=slots, **kw).rgba()


def slot_masks(ir, images, pose=None, size=None, origin=None, skin=None, slots=None, scale=1.0, **kw):
    """{slot: (H, W) float32 alpha} of each drawn slot on one canvas (each
    slot's own coverage, before occlusion), for seam/ghost tests."""
    r = Renderer(ir, images, skin=skin, canvas=_canvas_from(None, size, origin, scale), scale=scale)
    fr = r.frame(pose, slots=slots, **kw)
    return {s: fr.slot_alpha(s) for s in fr.slots}


# ---------------------------------------------------------------- images

def load_atlas_images(atlas_path):
    """{region name: (h, w, 4) uint8} from a Spine atlas and its page PNGs,
    each padded back to its original (unstripped) size. Rotated regions are
    not supported (none of our atlases rotate)."""
    from PIL import Image

    from . import atlas as atlasmod
    at = atlasmod.load(atlas_path)
    base = os.path.dirname(os.path.abspath(atlas_path))
    pages = {p["name"]: np.asarray(Image.open(os.path.join(base, p["name"])).convert("RGBA")) for p in at["pages"]}
    out = {}
    for name, r in at["regions"].items():
        if r["degrees"]:
            raise NotImplementedError(f"atlas region {name!r} is rotated {r['degrees']} deg")
        page = pages[r["page"]]
        x, y, w, h = (int(round(v)) for v in (r["x"], r["y"], r["width"], r["height"]))
        packed = page[y:y + h, x:x + w]
        ow, oh = int(round(r["original_width"])), int(round(r["original_height"]))
        if (ow, oh) == (w, h):
            out[name] = packed.copy()
            continue
        full = np.zeros((oh, ow, 4), np.uint8)
        ox = int(round(r["offset_x"]))
        oy = oh - int(round(r["offset_y"])) - h  # offsets count from the original's bottom-left
        full[oy:oy + h, ox:ox + w] = packed
        out[name] = full
    return out


def load_image_dir(path):
    """{relative path without .png: RGBA uint8} for every PNG under `path`."""
    from PIL import Image
    out = {}
    for root, _, files in os.walk(path):
        for f in sorted(files):
            if f.lower().endswith(".png"):
                full = os.path.join(root, f)
                key = os.path.relpath(full, path)[:-4].replace(os.sep, "/")
                out[key] = np.asarray(Image.open(full).convert("RGBA"))
    return out


def load_images(path):
    """Images from an .atlas file or a directory of PNGs."""
    if str(path).endswith(".atlas"):
        return load_atlas_images(path)
    return load_image_dir(path)


def load_rig(path):
    """An IR from rig.json (spinegen-rig/1) or a Spine 4.x skeleton JSON."""
    with open(path) as f:
        head = json.load(f)
    if head.get("schema") == irmod.SCHEMA_RIG:
        return irmod.load(path)
    from .spine42_reader import read
    return read(head, os.path.splitext(os.path.basename(path))[0])


def save_png(path, rgba):
    from PIL import Image
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray(np.asarray(rgba, np.uint8), "RGBA").save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig")
    ap.add_argument("images", help="directory of PNGs or a .atlas")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--pose", default=None, help='JSON {"bone": {"rotation": deg}} (absolute local) or @file')
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--supersample", type=int, default=1)
    ap.add_argument("--skin", default=None)
    a = ap.parse_args(argv)
    pose = None
    if a.pose:
        pose = json.load(open(a.pose[1:])) if a.pose.startswith("@") else json.loads(a.pose)
    img = render(load_rig(a.rig), load_images(a.images), pose=pose, skin=a.skin, scale=a.scale,
                 supersample=a.supersample)
    save_png(a.out, img)
    print(f"{a.out}: {img.shape[1]}x{img.shape[0]}")


if __name__ == "__main__":
    main()
