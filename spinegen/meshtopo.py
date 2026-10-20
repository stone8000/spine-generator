"""Mesh topology: boundary loops and the hull-first vertex order Spine expects.

WHY
  Spine's editor treats the first `hull` vertices of a mesh as its outline,
  in order; the runtime never reads `hull`, so a wrong value loads and draws
  fine and only breaks when an artist opens the file (L8 MESH). Hard Time's
  meshes all say hull 0 with the outline scattered through the list. The
  emitter therefore derives the outline from the triangles and moves it to
  the front.
"""
from collections import Counter


def boundary_loops(triangles):
    """Closed outlines of a triangle mesh, each a simple list of vertex indices.

    A boundary edge belongs to exactly one triangle; following those edges in
    their triangle's winding gives consistently oriented loops. Where two
    outlines touch at one vertex (Hard Time's prisoner towel), the walk is cut
    there into simple cycles; the cycles are the same whichever way the pinch
    is walked, so the result does not depend on vertex numbering. Returns
    None when the boundary does not close (a non-manifold mesh), which
    callers treat as "leave the mesh alone".
    """
    count = Counter()
    for t in triangles:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            count[(min(a, b), max(a, b))] += 1
    out, indeg = {}, Counter()
    for t in triangles:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            if count[(min(a, b), max(a, b))] == 1:
                out.setdefault(a, []).append(b)
                indeg[b] += 1
    if any(len(out.get(v, [])) != indeg[v] for v in set(out) | set(indeg)):
        return None
    for v in out:
        out[v].sort(reverse=True)  # pop() takes the smallest: deterministic
    loops = []
    for start in sorted(out):
        while out[start]:
            path, pos, v = [start], {start: 0}, start
            while True:
                if not out.get(v):
                    return None
                w = out[v].pop()
                if w not in pos:
                    pos[w] = len(path)
                    path.append(w)
                    v = w
                    continue
                i = pos[w]  # the walk came back to w: cut the cycle off
                loops.append(path[i:])
                for x in path[i + 1:]:
                    del pos[x]
                del path[i + 1:]
                v = w
                if len(path) == 1:
                    break
    return loops


def polygon_area(points, loop):
    s = 0.0
    for i, a in enumerate(loop):
        b = loop[(i + 1) % len(loop)]
        s += points[a][0] * points[b][1] - points[b][0] * points[a][1]
    return 0.5 * s


def inside_polygon(points, loop, xy):
    """Whether xy lies strictly inside the polygon through points[loop] (even-odd)."""
    x, y = xy
    inside = False
    for i, a in enumerate(loop):
        (xi, yi), (xj, yj) = points[a], points[loop[i - 1]]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            inside = not inside
    return inside


def outside_outline(uvs, loops, outer):
    """Boundary vertices of the other loops that lie outside the `outer` outline.

    Spine's hull is one polygon and every other vertex is interior to it, so
    the rim of a hole inside the outline is fine (the editor shows it as
    interior vertices); a separate island outside the outline has no place
    in any hull, and L8 MESH flags exactly these vertices.
    """
    on_outline = set(outer)
    rest = {v for lp in loops if lp is not outer for v in lp if v not in on_outline}
    return sorted(v for v in rest if not inside_polygon(uvs, outer, uvs[v]))


def hull_is_first(triangles, hull, loops=None):
    """True when vertices 0..hull-1, in order, walk one boundary loop."""
    loops = boundary_loops(triangles) if loops is None else loops
    if not loops or hull < 3:
        return False
    for loop in loops:
        if len(loop) != hull or set(loop) != set(range(hull)):
            continue
        pos = {v: i for i, v in enumerate(loop)}
        step = (pos[1] - pos[0]) % hull
        if step not in (1, hull - 1):
            return False
        return all((pos[(i + 1) % hull] - pos[i]) % hull == step for i in range(hull))
    return False


def hull_permutation(triangles, n, uvs, hull, loops=None):
    """(perm, hull) with the outer outline first; perm[new] = old.

    The outer outline is the loop with the largest UV area. A mesh whose
    outline already comes first keeps its order (perm is the identity), so
    emitting twice is stable. Returns (None, hull) when the boundary cannot
    be read as simple loops. A hole or a separate island has no place in
    Spine's one-polygon hull; its vertices simply follow the outline (a hole's
    rim is then interior to the hull, an island's is not: outside_outline).
    """
    loops = boundary_loops(triangles) if loops is None else loops
    if not loops:
        return None, hull
    if hull_is_first(triangles, hull, loops):
        return list(range(n)), hull
    outer = max(loops, key=lambda lp: abs(polygon_area(uvs, lp)))
    if hull_is_first(triangles, len(outer), loops):  # only the count was wrong
        return list(range(n)), len(outer)
    first = min(range(len(outer)), key=lambda i: outer[i])
    outer = outer[first:] + outer[:first]
    on_outline = set(outer)
    rest = [i for i in range(n) if i not in on_outline]
    return outer + rest, len(outer)


def reorder_mesh(att, perm, hull):
    """Apply perm[new] = old to an IR mesh attachment, in place."""
    inv = {old: new for new, old in enumerate(perm)}
    att["vertices"] = [att["vertices"][i] for i in perm]
    att["uvs"] = [att["uvs"][i] for i in perm]
    if att.get("weights") is not None:
        att["weights"] = [att["weights"][i] for i in perm]
    att["triangles"] = [[inv[v] for v in t] for t in att["triangles"]]
    if att.get("edges") is not None:
        att["edges"] = [[inv[a], inv[b]] for a, b in att["edges"]]
    att["hull"] = hull
    return att


def permute_offsets(offsets, perm, sizes):
    """Reorder a dense deform array whose vertex i owns `sizes[i]` values."""
    starts = [0]
    for s in sizes:
        starts.append(starts[-1] + s)
    return [v for i in perm for v in offsets[starts[i]:starts[i + 1]]]
