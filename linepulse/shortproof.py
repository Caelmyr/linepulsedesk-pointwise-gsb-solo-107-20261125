"""Short proof: pick a bounded set of real readout lines.

The full per-line profile may hold more than two thousand readings, which makes
the measured and trial curves overlap on the desk and on print.  A short proof
keeps a fixed point budget while guaranteeing that the first readout line of
the selection, the last one, and the line picked on the source photo stay in
the set (each real line only counts once).  Remaining points are a compromise
between short bright spikes and longer brightness transitions.

Only real readout lines from the current selection are ever returned, in their
original order, so the same retained indices drive the desk chart, the proof
PNG, and any manual trial comparison.  The per-line CSV keeps every reading.
"""

import numpy as np

from image_lab.validation import Invalid, integer


MIN_BUDGET = 2
MAX_BUDGET = 5000
DEFAULT_BUDGET = 240


def budget_value(raw):
    return integer(raw if raw is not None else DEFAULT_BUDGET, "短校样保留点数", MIN_BUDGET, MAX_BUDGET)


def anchor_value(raw):
    """The picked line is optional; out-of-range values are ignored, never clipped."""
    if raw is None or str(raw).strip() == "":
        return None
    return integer(raw, "点选的读出线号", 0, 10_000_000)


def _moving_average(values, window):
    """Symmetric moving average with trimmed windows at both ends."""
    radius = window // 2
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    count = len(values)
    left = np.maximum(0, np.arange(count) - radius)
    right = np.minimum(count, np.arange(count) + radius + 1)
    return (cumulative[right] - cumulative[left]) / (right - left)


def _importance(measured):
    """Compromise score: short bright spikes together with long brightness drifts.

    The spike term is a positive residual above a short local baseline, so dark
    dips do not outrank the bright tips the operator is looking for.  The trend
    term is the slope magnitude of a long moving average, which rewards the
    slower light-to-dark transitions.  Each term is scaled to a comparable
    0-to-1 range before the two are added with equal weight.
    """
    values = np.asarray(measured, dtype=np.float64)
    count = len(values)
    short_window = max(3, count // 150 | 1)
    long_window = max(5, count // 24 | 1)
    baseline = _moving_average(values, short_window)
    envelope = _moving_average(values, long_window)
    bright = np.maximum(0.0, values - baseline)
    spike_scale = bright.max() or 1.0
    spike = bright / spike_scale
    slope = np.abs(np.diff(envelope, prepend=envelope[0]))
    trend_scale = slope.max() or 1.0
    trend = slope / trend_scale
    return spike + trend


def build_short_proof(lines, budget, anchor=None):
    """Return retained readout rows and the indices that explain the choice.

    ``lines`` are the real per-line rows from ``signal.report`` (their ``index``
    is the original image line number).  The returned ``points`` are the same
    row dictionaries, in original line order.  ``retained`` holds the matching
    original line indices, and ``pinned`` the mandatory lines actually applied.
    """
    budget = budget_value(budget)
    count = len(lines)
    if count < MIN_BUDGET:
        raise Invalid("当前选区至少要有2条读出线")
    indices = np.asarray([row["index"] for row in lines], dtype=np.int64)
    by_index = {int(index): row for index, row in zip(indices, lines)}
    first_index, last_index = int(indices[0]), int(indices[-1])

    applied_anchor = None
    if anchor is not None:
        anchor = anchor_value(anchor)
        if anchor is not None and anchor in by_index:
            applied_anchor = anchor
    pinned = sorted({first_index, last_index, applied_anchor} - {None})
    if budget < len(pinned):
        names = "、".join(f"第 {line} 条" for line in pinned)
        raise Invalid(
            f"短校样预算 {budget} 点放不下必须保留的 {len(pinned)} 条线（{names}）："
            f"选区首尾和在原图点选的线都必须保留，同一条只算一次，请把保留点数提高到 {len(pinned)} 或以上"
        )

    if budget >= count:
        retained = [int(index) for index in indices]
        points = [by_index[index] for index in retained]
        return _result(budget, anchor, applied_anchor, pinned, retained, points, count)

    scores = _importance(np.asarray([row["measured"] for row in lines]))
    ranking = sorted(range(count), key=lambda position: (-scores[position], position))
    chosen = set(pinned)  # pinned only contains real indices present in by_index
    # Minimum gap between extra points: even importance cannot collapse the
    # whole proof onto one neighbourhood, and the mandatory lines win gaps.
    min_gap = max(1, (count - 1) // max(budget - 1, 1))

    def blocked(candidate):
        return any(abs(candidate - kept) < min_gap for kept in chosen)

    for position in ranking:
        if len(chosen) >= budget:
            break
        candidate = int(indices[position])
        if candidate in chosen or blocked(candidate):
            continue
        chosen.add(candidate)

    # Fill any budget left over (spacing guard, tiny selections) by repeatedly
    # adding the largest uncovered gap, so the proof still spans every region.
    ordered = sorted(chosen)
    while len(chosen) < budget:
        gaps = [(right - left, left, right) for left, right in zip(ordered, ordered[1:])]
        gap, left, right = max(gaps)
        if gap <= 1:
            break
        candidate = left + gap // 2
        chosen.add(candidate)
        ordered = sorted(chosen)

    retained = sorted(chosen)
    points = [by_index[index] for index in retained]
    return _result(budget, anchor, applied_anchor, pinned, retained, points, count)


def _result(budget, requested_anchor, applied_anchor, pinned, retained, points, count):
    return {
        "budget": int(budget),
        "line_count": int(count),
        "anchor": applied_anchor,
        "anchor_requested": requested_anchor,
        "pinned": [int(index) for index in pinned],
        "retained": [int(index) for index in retained],
        "points": points,
        "complete": len(retained) == count,
    }
