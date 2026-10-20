"""Spine 4.x curve evaluation (runtime-faithful) and least-squares key fitting.

EVALUATION
  The official runtimes do not evaluate a bezier segment exactly. CurveTimeline
  .setBezier forward-differences the curve at s = 0.1 .. 0.9 and the runtime
  interpolates LINEARLY between those nine points and the two keys. Everything
  here that checks an error does it against that polyline, so a tolerance means
  "what the player actually shows", not "what the maths says".

FITTING
  Dense samples (a synthesised motion at 120-240 Hz, or a 30 fps capture) are
  turned into the fewest keys that stay within a tolerance:

  * handles sit at one third of the segment in TIME, so x(s) is linear in s and
    each segment is a cubic Hermite in time; the value handles are then linear
    in the key slopes;
  * every key gets ONE slope shared by both sides (C1, no corners), and all
    slopes are solved together by linear least squares over every sample;
  * keys start at the ends and the local extrema (peaks stay exact — that is
    where the eye looks), then the worst sample of every segment that is still
    out of tolerance becomes a key, until none is.

  Overshoot in the samples is kept: nothing here clamps a tangent.
"""
import math

import numpy as np

BEZIER_STEPS = 10  # runtime: 9 inner points at s = 0.1 .. 0.9


def _bezier_points(t0, v0, cx1, cy1, cx2, cy2, t1, v1):
    pts = [(t0, v0)]
    for k in range(1, BEZIER_STEPS):
        s = k / BEZIER_STEPS
        a, b, c, d = (1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s * s, s ** 3
        pts.append((a * t0 + b * cx1 + c * cx2 + d * t1, a * v0 + b * cy1 + c * cy2 + d * v1))
    pts.append((t1, v1))
    return pts


def evaluate(keys, field, t, default=0.0, field_index=0):
    """Value of a Spine 4.x timeline `keys` at time t, as the runtime computes it."""
    if not keys:
        return default
    if t <= keys[0].get("time", 0.0):
        return keys[0].get(field, default)
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        t0, t1 = a.get("time", 0.0), b.get("time", 0.0)
        if t < t1:
            v0, v1 = a.get(field, default), b.get(field, default)
            c = a.get("curve")
            if c == "stepped":
                return v0
            if c is None or t1 - t0 <= 0:
                return v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1e-12)
            cx1, cy1, cx2, cy2 = c[field_index * 4: field_index * 4 + 4]
            pts = _bezier_points(t0, v0, cx1, cy1, cx2, cy2, t1, v1)
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                if t <= xb:
                    return ya + (yb - ya) * (t - xa) / max(xb - xa, 1e-12)
            return v1
    return keys[-1].get(field, default)


def _basis(s):
    return (1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s * s, s ** 3


def _runtime_basis(s):
    """Weights of (v0, h1, h2, v1) in the runtime polyline at parameter s.

    With handles at thirds in time, the polyline vertices sit at uniform s, so
    the runtime value at s is a linear blend of the two neighbouring vertices,
    and each vertex is a fixed blend of the four control values.
    """
    k = min(BEZIER_STEPS - 1, int(s * BEZIER_STEPS))
    sa, sb = k / BEZIER_STEPS, (k + 1) / BEZIER_STEPS
    u = (s - sa) / (sb - sa)
    ba, bb = np.array(_basis(sa)), np.array(_basis(sb))
    return (1 - u) * ba + u * bb


SMOOTH = 0.1  # weight pulling a key's in- and out-slope together


def _solve_slopes(t, v, idx, loop):
    """Least-squares in/out slopes per key for fixed key indices (handles at thirds).

    Unknowns are laid out [out_0, in_1, out_1, in_2, ..., in_last]. Each key's
    two slopes are tied by a soft penalty, so a smooth passage stays C1 and a
    real corner in the data (the instant a strike starts) gets a broken
    tangent instead of a pile of keys around it. Spine stores the two sides of
    a key independently (cy2 of one segment, cy1 of the next), so this costs
    nothing in the file.
    """
    n_keys = len(idx)
    n_var = 2 * (n_keys - 1)
    out_ = lambda k: 2 * k  # noqa: E731  slope leaving key k
    in_ = lambda k: 2 * k - 1  # noqa: E731  slope arriving at key k
    rows, rhs = [], []
    for k in range(n_keys - 1):
        ia, ib = idx[k], idx[k + 1]
        h = t[ib] - t[ia]
        for i in range(ia, ib + 1):
            s = (t[i] - t[ia]) / h
            b0, b1, b2, b3 = _runtime_basis(s)
            row = np.zeros(n_var)
            row[out_(k)] += b1 * h / 3
            row[in_(k + 1)] -= b2 * h / 3
            rows.append(row)
            rhs.append(v[i] - (b0 + b1) * v[ia] - (b2 + b3) * v[ib])
    pairs = [(in_(k), out_(k), k) for k in range(1, n_keys - 1)]
    if loop and n_keys > 2:  # the end of the loop arrives where the start leaves
        pairs.append((in_(n_keys - 1), out_(0), 0))
    for a, b, k in pairs:
        ka, kb = max(0, k - 1), min(n_keys - 1, k + 1)
        hbar = (t[idx[kb]] - t[idx[ka]]) / 2
        row = np.zeros(n_var)
        row[a], row[b] = SMOOTH * hbar / 3, -SMOOTH * hbar / 3
        rows.append(row)
        rhs.append(0.0)
    A, y = np.array(rows), np.array(rhs)
    # a whisper of ridge keeps flat stretches from inventing slopes
    lam = 1e-9 * max(1.0, float(np.abs(y).max(initial=0.0)))
    m = np.linalg.solve(A.T @ A + lam * np.eye(n_var), A.T @ y)
    m_out = [m[out_(k)] for k in range(n_keys - 1)] + [0.0]
    m_in = [0.0] + [m[in_(k)] for k in range(1, n_keys)]
    return m_in, m_out


def _extrema(v, tol):
    out = []
    for i in range(1, len(v) - 1):
        if (v[i] - v[i - 1]) * (v[i + 1] - v[i]) < 0:
            out.append(i)
    # drop wiggles smaller than the tolerance
    keep = []
    for i in out:
        if not keep or abs(v[i] - v[keep[-1]]) > tol:
            keep.append(i)
    return keep


def fit(times, channels, tol, loop=False, max_iter=200):
    """Fit one timeline with one or more fields sharing key times.

    times     increasing sample times (s)
    channels  {field: samples}; all the same length as times
    tol       {field: max abs error} or one number for every field
    loop      the last sample is the loop point and equals the first

    Returns (keys, report): Spine 4.x key dicts and the worst runtime error per field.
    """
    t = np.asarray(times, float)
    fields = list(channels)
    V = {f: np.asarray(channels[f], float) for f in fields}
    # aim 2% inside the tolerance: values are written rounded to 4 decimals
    tolf = {f: 0.98 * (tol[f] if isinstance(tol, dict) else tol) for f in fields}
    idx = {0, len(t) - 1}
    for f in fields:
        idx.update(_extrema(V[f], tolf[f]))
    idx = sorted(idx)
    for _ in range(max_iter):
        slopes = {f: _solve_slopes(t, V[f], idx, loop) for f in fields}
        worst, add = {f: 0.0 for f in fields}, set()
        for k in range(len(idx) - 1):
            ia, ib = idx[k], idx[k + 1]
            h = t[ib] - t[ia]
            bad_i, bad_e = None, 0.0
            for f in fields:
                v, (m_in, m_out) = V[f], slopes[f]
                for i in range(ia + 1, ib):
                    s = (t[i] - t[ia]) / h
                    b0, b1, b2, b3 = _runtime_basis(s)
                    y = (b0 * v[ia] + b1 * (v[ia] + m_out[k] * h / 3)
                         + b2 * (v[ib] - m_in[k + 1] * h / 3) + b3 * v[ib])
                    e = abs(y - v[i])
                    worst[f] = max(worst[f], e)
                    if e > tolf[f] and e / tolf[f] > bad_e:
                        bad_i, bad_e = i, e / tolf[f]
            if bad_i is not None:
                add.add(bad_i)
        if not add:
            break
        idx = sorted(set(idx) | add)
    keys = []
    for k, i in enumerate(idx):
        key = {"time": round(float(t[i]), 6)}
        for f in fields:
            key[f] = round(float(V[f][i]), 4)
        if k < len(idx) - 1:
            j = idx[k + 1]
            h = t[j] - t[i]
            curve = []
            for f in fields:
                m_in, m_out = slopes[f]
                curve += [round(float(t[i] + h / 3), 6), round(float(V[f][i] + m_out[k] * h / 3), 4),
                          round(float(t[j] - h / 3), 6), round(float(V[f][j] - m_in[k + 1] * h / 3), 4)]
            key["curve"] = curve
        keys.append(key)
    # default values are omitted by Spine; keep the file small the same way
    report = {f: worst[f] for f in fields}
    return keys, report


def max_error(keys, times, channels, field_order):
    """Worst runtime error of `keys` against the samples, per field."""
    out = {}
    for fi, f in enumerate(field_order):
        out[f] = max(abs(evaluate(keys, f, float(tt), field_index=fi) - float(vv))
                     for tt, vv in zip(times, channels[f]))
    return out


def sample_timeline(keys, field_order, duration, rate=240.0, defaults=None):
    """Dense runtime-faithful samples of an existing timeline."""
    n = max(2, int(math.ceil(duration * rate)) + 1)
    times = [min(duration, i / rate) for i in range(n)]
    defaults = defaults or {}
    return times, {f: [evaluate(keys, f, tt, defaults.get(f, 0.0), fi) for tt in times]
                   for fi, f in enumerate(field_order)}
