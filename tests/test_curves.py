import math

from spinegen.curves import evaluate, fit, max_error


def samples(fn, dur, rate=240):
    n = int(dur * rate) + 1
    t = [i / rate for i in range(n)]
    return t, [fn(x) for x in t]


def test_linear_and_stepped_evaluation():
    keys = [{"time": 0, "value": 0}, {"time": 1, "value": 10, "curve": "stepped"}, {"time": 2, "value": 20}]
    assert evaluate(keys, "value", 0.5) == 5
    assert evaluate(keys, "value", 1.5) == 10
    assert evaluate(keys, "value", 3) == 20


def test_thirds_bezier_is_runtime_polyline():
    # handles at thirds with the chord's slope reproduce a straight line exactly
    keys = [{"time": 0, "value": 0, "curve": [1 / 3, 10 / 3, 2 / 3, 20 / 3]}, {"time": 1, "value": 10}]
    for x in (0.05, 0.37, 0.91):
        assert abs(evaluate(keys, "value", x) - 10 * x) < 1e-9


def test_loop_sine_few_keys_within_tolerance():
    t, v = samples(lambda x: 5 * math.sin(2 * math.pi * x / 2.0), 2.0)
    keys, _ = fit(t, {"value": v}, 0.1, loop=True)
    assert max_error(keys, t, {"value": v}, ["value"])["value"] <= 0.1 + 1e-9
    assert len(keys) <= 9
    # closed loop: same slope leaving the start as arriving at the end
    c0, cl = keys[0]["curve"], keys[-2]["curve"]
    assert abs((c0[1] - keys[0]["value"]) - (keys[-1]["value"] - cl[3])) < 1e-3


def test_strike_with_overshoot_is_kept():
    # 0.333 s windup to -20, 0.1 s strike to +60 with a 6 deg overshoot, hold, recover
    def f(x):
        if x < 0.333:
            return -20 * math.sin(math.pi / 2 * x / 0.333) ** 2
        if x < 0.433:
            u = (x - 0.333) / 0.1
            return -20 + 80 * (1 - (1 - u) ** 3)
        if x < 0.667:
            u = (x - 0.433) / 0.234
            return 60 + 6 * math.exp(-6 * u) * math.sin(math.pi * u)
        u = (x - 0.667) / 0.333
        return 60 * (1 - u * u * (3 - 2 * u))
    t, v = samples(f, 1.0)
    keys, _ = fit(t, {"value": v}, 0.2)
    err = max_error(keys, t, {"value": v}, ["value"])["value"]
    assert err <= 0.2 + 1e-9
    assert len(keys) < 30  # vs 241 samples
    # the overshoot past the 60 deg hold pose survives, within tolerance of its true peak
    assert abs(max(evaluate(keys, "value", x) for x in t) - max(v)) <= 0.2
    assert max(v) > 61


def test_two_fields_share_key_times():
    t, x = samples(lambda s: 30 * math.sin(3 * s), 1.5)
    _, y = samples(lambda s: -12 * s * s, 1.5)
    keys, _ = fit(t, {"x": x, "y": y}, 0.25)
    err = max_error(keys, t, {"x": x, "y": y}, ["x", "y"])
    assert err["x"] <= 0.25 + 1e-9 and err["y"] <= 0.25 + 1e-9
    assert all(len(k.get("curve", [0] * 8)) == 8 for k in keys)
