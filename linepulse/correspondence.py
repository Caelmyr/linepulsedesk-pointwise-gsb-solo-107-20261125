"""Content-derived monotone row correspondence between the A and B captures.

The two photos keep their own already-framed regions.  Frames are expected to
have equal width (row read-out); each line signature is sampled from the same
offset inside its own frame, so the two image x coordinates are never shifted.
Per-line luminance is z-scored before comparison, which removes a whole-line
brightness/gain change while preserving the light/dark pattern inside the line.

The final matching is a banded dynamic program:

* every A line pairs with at most one B line and vice versa;
* both indices strictly increase along the chosen pairs;
* ``abs(index_a - index_b) <= limit``;
* texture-less ("bare") lines and correlations below the clarity floor are
  not usable edges and stay unpaired.

The DP first maximizes the number of usable pairs and then their total
clarity, giving one answer that balances coverage and pattern certainty.
"""

from __future__ import annotations

import math

import numpy as np

from image_lab.validation import Invalid, integer

from .signal import LUMA

DEFAULT_LIMIT = 6
MAX_LIMIT = 5000
# Luma standard deviation below which a line is considered bare sensor area.
ABS_TEXTURE_FLOOR = 1.5
TEXTURE_RELATIVE = 0.06
# Pearson floor for an edge to be considered at all.
MIN_CORRELATION = 0.70


def read_limit(value, default=DEFAULT_LIMIT):
    """Accept the user-filled row-number gap cap under several field names."""
    if value is None or value == "":
        return default
    return integer(value, "行号差上限", 0, MAX_LIMIT)


def line_signatures(image, config):
    """Return absolute line indices and z-scored in-line patterns.

    For ``axis == 'rows'`` every signature is the luma run across the framed
    width starting at the frame's left edge.  Column read-outs use the framed
    height / top edge analogously.
    """
    x, y, w, h = (int(config[key]) for key in ("x", "y", "w", "h"))
    region = image[y:y + h, x:x + w].astype(np.float64)
    if region.ndim == 3:
        region = region @ LUMA
    if config.get("axis", "rows") == "rows":
        samples = region
        indices = np.arange(y, y + h)
    else:
        samples = region.T
        indices = np.arange(x, x + w)
    return indices.astype(np.int64), samples


def _normalize(samples):
    raw_std = samples.std(axis=1)
    centered = samples - samples.mean(axis=1, keepdims=True)
    normalized = centered / np.maximum(raw_std[:, None], 1e-9)
    median_std = float(np.median(raw_std)) if len(raw_std) else 0.0
    texture_floor = max(ABS_TEXTURE_FLOOR, TEXTURE_RELATIVE * median_std)
    textured = raw_std >= texture_floor
    return normalized, raw_std, textured, texture_floor


def _clarity(correlation):
    """Map a Pearson value to 0..1 pattern clarity."""
    return float(np.clip((correlation - 0.55) / 0.45, 0.0, 1.0))


def _dp_pairs(correlation, indices_a, indices_b, limit, eligible_a, eligible_b, min_corr):
    """Banded monotone 1:1 alignment; returns (i, j, r, clarity) tuples."""
    na, nb = correlation.shape
    prev = np.zeros(nb + 1, dtype=np.float64)
    # choice codes: 1 = skip A (move up), 2 = skip B (move left), 3 = pair
    choices = bytearray(na * (nb + 1))
    quality_scale = 1000
    big = (na + nb + 2) * quality_scale + 1

    idx_a = [int(value) for value in indices_a]
    idx_b = [int(value) for value in indices_b]
    ok_b = [bool(value) for value in eligible_b]
    for i in range(1, na + 1):
        cur = np.zeros(nb + 1, dtype=np.float64)
        base = (i - 1) * (nb + 1)
        row = correlation[i - 1].tolist()
        ia = idx_a[i - 1]
        a_ok = bool(eligible_a[i - 1])
        for j in range(1, nb + 1):
            best = prev[j]
            choice = 1
            left = cur[j - 1]
            if left > best:
                best = left
                choice = 2
            if a_ok and ok_b[j - 1] and abs(ia - idx_b[j - 1]) <= limit:
                r = row[j - 1]
                if r >= min_corr:
                    candidate = prev[j - 1] + big + int(round(quality_scale * _clarity(r)))
                    if candidate > best:
                        best = candidate
                        choice = 3
            cur[j] = best
            choices[base + j] = choice
        prev = cur

    pairs = []
    i, j = na, nb
    while i > 0 and j > 0:
        choice = choices[(i - 1) * (nb + 1) + j]
        if choice == 3:
            r = float(correlation[i - 1, j - 1])
            pairs.append((i - 1, j - 1, r, _clarity(r)))
            i -= 1
            j -= 1
        elif choice == 2:
            j -= 1
        else:
            i -= 1
    pairs.reverse()
    return pairs


def suggest(image_a, config_a, image_b, config_b, limit=DEFAULT_LIMIT, min_correlation=None):
    """Compute the content correspondence between two framed captures.

    Returns a JSON-safe dict.  ``pairs`` use absolute original image indices.
    """
    limit = read_limit(limit)
    axis = config_a.get("axis", "rows")
    if config_b.get("axis", "rows") != axis:
        raise Invalid("两张照片的读出方向不一致，无法建立按行对应")

    index_a, raw_a = line_signatures(image_a, config_a)
    index_b, raw_b = line_signatures(image_b, config_b)
    if len(index_a) == 0 or len(index_b) == 0:
        raise Invalid("选区里没有可比较的读出线")

    width_a = raw_a.shape[1]
    width_b = raw_b.shape[1]
    common = min(width_a, width_b)
    if common < 4:
        raise Invalid("框内取样宽度不足，无法比较行内纹样")
    # Same offsets from each frame's own left/top edge; never touch x.
    raw_a = raw_a[:, :common]
    raw_b = raw_b[:, :common]

    norm_a, std_a, ok_a, floor_a = _normalize(raw_a)
    norm_b, std_b, ok_b, floor_b = _normalize(raw_b)
    correlation = (norm_a @ norm_b.T) / common
    correlation = np.clip(correlation, -1.0, 1.0)

    if min_correlation is None:
        min_correlation = max(MIN_CORRELATION, min(0.92, 3.0 / math.sqrt(max(common, 1))))

    chosen = _dp_pairs(correlation, index_a, index_b, limit, ok_a, ok_b, min_correlation)

    pair_records = []
    a_to_b = {}
    b_to_a = {}
    paired_a, paired_b = set(), set()
    for ia_pos, ib_pos, r, clarity in chosen:
        a_index = int(index_a[ia_pos])
        b_index = int(index_b[ib_pos])
        pair_records.append({
            "a": a_index,
            "b": b_index,
            "a_index": a_index,
            "b_index": b_index,
            "index_a": a_index,
            "index_b": b_index,
            "correlation": round(r, 5),
            "clarity": round(clarity, 4),
            "confidence": round(clarity, 4),
            "score": round(1.0 + 0.5 * clarity, 5),
        })
        a_to_b[str(a_index)] = b_index
        b_to_a[str(b_index)] = a_index
        paired_a.add(ia_pos)
        paired_b.add(ib_pos)

    def best_for(position, side):
        if side == "a":
            row = correlation[position]
            own = int(index_a[position])
            other_index = index_b
            eligible = ok_b
        else:
            row = correlation[:, position]
            own = int(index_b[position])
            other_index = index_a
            eligible = ok_a
        best_r, best_i = -2.0, None
        for j, value in enumerate(row):
            if eligible[j] and abs(own - int(other_index[j])) <= limit and value > best_r:
                best_r, best_i = float(value), j
        if best_i is None:
            return None, None
        return best_r, int(other_index[best_i])

    lines_a = []
    for i, absolute in enumerate(index_a):
        absolute = int(absolute)
        if i in paired_a:
            partner = a_to_b[str(absolute)]
            status = "matched"
        elif not ok_a[i]:
            partner = None
            status = "bare"
        else:
            best_r, _ = best_for(i, "a")
            if best_r is None:
                status = "out_of_band"
            elif best_r < min_correlation:
                status = "weak"
            else:
                status = "unmatched"
            partner = None
        lines_a.append({
            "index": absolute,
            "match": a_to_b.get(str(absolute)),
            "paired_index": a_to_b.get(str(absolute)),
            "correlation": None if i in paired_a else _safe_best(correlation[i], index_b, absolute, limit, ok_b),
            "std": round(float(std_a[i]), 3),
            "status": status,
        })

    lines_b = []
    for j, absolute in enumerate(index_b):
        absolute = int(absolute)
        if j in paired_b:
            partner = b_to_a[str(absolute)]
            status = "matched"
        elif not ok_b[j]:
            partner = None
            status = "bare"
        else:
            best_r, _ = best_for(j, "b")
            if best_r is None:
                status = "out_of_band"
            elif best_r < min_correlation:
                status = "weak"
            else:
                status = "unmatched"
            partner = None
        lines_b.append({
            "index": absolute,
            "match": b_to_a.get(str(absolute)),
            "paired_index": b_to_a.get(str(absolute)),
            "correlation": None if j in paired_b else _safe_best(correlation[:, j], index_a, absolute, limit, ok_a),
            "std": round(float(std_b[j]), 3),
            "status": status,
        })

    return {
        "version": 1,
        "method": "row-pattern-zscore-banded-dp",
        "source": "image-content",
        "axis": axis,
        "limit": int(limit),
        "max_diff": int(limit),
        "max_shift": int(limit),
        "tolerance": int(limit),
        "sample_width": int(common),
        "equal_width": width_a == width_b,
        "width_a": int(width_a),
        "width_b": int(width_b),
        "texture_floor_a": round(float(floor_a), 3),
        "texture_floor_b": round(float(floor_b), 3),
        "min_correlation": round(float(min_correlation), 3),
        "pairs": pair_records,
        "pair_list": [[pair["a"], pair["b"]] for pair in pair_records],
        "a_to_b": a_to_b,
        "b_to_a": b_to_a,
        "lines_a": lines_a,
        "lines_b": lines_b,
        "rows_a": lines_a,
        "rows_b": lines_b,
        "matched_count": len(pair_records),
        "count": len(pair_records),
        "a_count": len(index_a),
        "b_count": len(index_b),
        "unmatched_a": [line["index"] for line in lines_a if line["match"] is None],
        "unmatched_b": [line["index"] for line in lines_b if line["match"] is None],
    }


def _safe_best(values, other_indices, own_absolute, limit, eligible):
    best = None
    for j, value in enumerate(values):
        if eligible[j] and abs(own_absolute - int(other_indices[j])) <= limit:
            value = float(value)
            if best is None or value > best:
                best = value
    return None if best is None else round(best, 4)


def normalize_adoption(payload, photo_sizes=None):
    """Normalize a client-supplied adopted correspondence into storage shape.

    Accepts the canonical dict, a list of pairs, or an ``{a_index: b_index}``
    mapping under any of the common field names.
    """
    if payload is None:
        raise Invalid("缺少要采用的行对应")
    limit = None
    raw_pairs = payload
    if isinstance(payload, dict):
        limit = payload.get("limit", payload.get("max_diff", payload.get("max_shift")))
        raw_pairs = (payload.get("pairs", payload.get("pair_list",
                     payload.get("matches", payload.get("mapping")))))
        if raw_pairs is None and isinstance(payload.get("a_to_b"), dict):
            raw_pairs = [{"a": a, "b": b} for a, b in payload["a_to_b"].items()]
        if isinstance(raw_pairs, dict):
            raw_pairs = [{"a": a, "b": b} for a, b in raw_pairs.items()]
        elif raw_pairs is None:
            # A bare {"12": 13, "14": 15} index map (optionally with limit).
            pairs_from_map = []
            for key, value in payload.items():
                if key in {"limit", "max_diff", "max_shift", "tolerance", "axis",
                           "revision", "method", "source"}:
                    continue
                try:
                    pairs_from_map.append({"a": int(key), "b": value})
                except (TypeError, ValueError):
                    continue
            raw_pairs = pairs_from_map
        if isinstance(raw_pairs, list) and raw_pairs and isinstance(raw_pairs[0], (list, tuple)):
            pass
    if not isinstance(raw_pairs, list):
        raise Invalid("行对应必须是配对列表")
    limit = read_limit(limit if limit is not None else DEFAULT_LIMIT)

    pairs = []
    seen_a, seen_b = set(), set()
    previous_a = previous_b = -1
    for item in raw_pairs:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            a_value, b_value = item[0], item[1]
            extra = item[2] if len(item) > 2 else None
            pair = {"a": a_value, "b": b_value}
            if isinstance(extra, dict):
                pair.update(extra)
        elif isinstance(item, dict):
            pair = dict(item)
        else:
            raise Invalid("每一对行号必须包含 A、B 两个行号")
        a_index = pair.get("a", pair.get("a_index", pair.get("index_a", pair.get("source"))))
        b_index = pair.get("b", pair.get("b_index", pair.get("index_b", pair.get("target"))))
        a_index = integer(a_index, "A 行号", 0, 100000)
        b_index = integer(b_index, "B 行号", 0, 100000)
        if a_index in seen_a or b_index in seen_b:
            raise Invalid("同一条线在对应中被重复使用")
        if pairs and (a_index <= previous_a or b_index <= previous_b):
            raise Invalid("行对应必须按两张原图行号严格递增")
        if abs(a_index - b_index) > limit:
            raise Invalid("有配对超过填写的行号差上限")
        seen_a.add(a_index)
        seen_b.add(b_index)
        previous_a, previous_b = a_index, b_index
        record = {"a": a_index, "b": b_index, "a_index": a_index, "b_index": b_index}
        for key in ("correlation", "clarity", "score"):
            if key in pair:
                try:
                    record[key] = round(float(pair[key]), 5)
                except (TypeError, ValueError):
                    pass
        pairs.append(record)

    result = {
        "version": 1,
        "method": "adopted",
        "source": "adopted",
        "axis": payload.get("axis", "rows") if isinstance(payload, dict) else "rows",
        "limit": limit,
        "max_diff": limit,
        "pairs": pairs,
        "a_to_b": {str(pair["a"]): pair["b"] for pair in pairs},
        "b_to_a": {str(pair["b"]): pair["a"] for pair in pairs},
        "matched_count": len(pairs),
        "count": len(pairs),
    }
    if isinstance(payload, dict):
        # Preserve rich, already-JSON-safe diagnostics from a computed proposal.
        for key in ("lines_a", "lines_b", "rows_a", "rows_b", "unmatched_a", "unmatched_b",
                    "sample_width", "equal_width", "min_correlation", "a_count", "b_count",
                    "method", "source", "texture_floor_a", "texture_floor_b", "pair_list"):
            if key in payload:
                result[key] = payload[key]
        if payload.get("method"):
            result["method"] = payload["method"]
        if payload.get("source"):
            result["source"] = payload["source"]
    return result


def counterpart(correspondence, slot, index):
    """Return the paired original index for one slot, or None."""
    if isinstance(correspondence, list):
        pairs = correspondence
        table = None
    elif isinstance(correspondence, dict):
        table = correspondence.get("a_to_b" if slot == "a" else "b_to_a")
        pairs = correspondence.get("pairs", [])
    else:
        return None
    if isinstance(table, dict):
        value = table.get(str(index))
        if value is not None:
            return int(value)
    for pair in pairs or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            a_value, b_value = pair[0], pair[1]
        elif isinstance(pair, dict):
            a_value = pair.get("a", pair.get("a_index", pair.get("index_a")))
            b_value = pair.get("b", pair.get("b_index", pair.get("index_b")))
        else:
            continue
        if slot == "a" and a_value is not None and int(a_value) == index:
            return int(b_value)
        if slot == "b" and b_value is not None and int(b_value) == index:
            return int(a_value)
    return None


# Common alternative names a caller/tests might import.
def match_lines(image_a, config_a, image_b, config_b, max_diff=DEFAULT_LIMIT, **kwargs):
    return suggest(image_a, config_a, image_b, config_b, limit=max_diff, **kwargs)


def row_correspondence(image_a, config_a, image_b, config_b, limit=DEFAULT_LIMIT, **kwargs):
    return suggest(image_a, config_a, image_b, config_b, limit=limit, **kwargs)


align_rows = match_lines
