"""Cross-photo alias matching for one physical flicker frequency."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from image_lab.validation import Invalid, number


CRITICAL_KEYS = ("x", "y", "w", "h", "axis", "line_us")


def sample_rate_hz(line_us: float) -> float:
    return 1_000_000.0 / float(line_us)


def alias_frequency(frequency_hz: float, line_us: float) -> float:
    """Fold a physical frequency through line sampling to its apparent frequency."""
    rate = sample_rate_hz(line_us)
    phase = (float(frequency_hz) / rate) % 1.0
    return min(phase, 1.0 - phase) * rate


def critical_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: settings[key] for key in CRITICAL_KEYS}


def fingerprint(settings: dict[str, Any]) -> str:
    raw = json.dumps(critical_settings(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_search(value: dict[str, Any]) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}

    def first(*names, default=None):
        for name in names:
            if name in source and source[name] is not None:
                return source[name]
        return default

    min_hz = number(first("min_hz", "frequency_min", "min_frequency_hz", "low_hz",
                          "start_hz", default=1.0), "真实频率下限", 0, 100_000)
    max_hz = number(first("max_hz", "frequency_max", "max_frequency_hz", "high_hz",
                          "end_hz", default=100_000.0), "真实频率上限", 0, 100_000)
    if min_hz > max_hz:
        raise Invalid("真实频率搜索范围的下限不能大于上限")
    min_amplitude = number(first("min_amplitude", "amplitude_min", "minimum_amplitude",
                                 "amplitude_threshold", "threshold", default=0.0),
                           "峰的最低亮度幅度", 0, 100_000)
    tolerance_hz = number(first("tolerance_hz", "max_difference_hz", "frequency_difference_hz",
                                "difference_hz", "allowed_difference_hz", "tolerance",
                                default=2.0), "允许的频率差", 0, 10_000)
    return {"min_hz": min_hz, "max_hz": max_hz,
            "min_amplitude": min_amplitude, "tolerance_hz": tolerance_hz}


def _peak_id(peak: dict[str, Any]) -> Any:
    return peak.get("index", round(float(peak["hz"]), 9))


def _peak_center(peak: dict[str, Any], spectrum: dict[str, Any] | None = None) -> float:
    """Refine a bin peak with parabolic interpolation when full FFT data exists."""
    center = float(peak["hz"])
    bins = spectrum.get("amplitudes") if isinstance(spectrum, dict) else None
    index = peak.get("index")
    if not isinstance(bins, list) or not isinstance(index, int) or not 0 < index < len(bins) - 1:
        return center
    left, middle, right = map(float, (bins[index - 1], bins[index], bins[index + 1]))
    denominator = left - 2.0 * middle + right
    if abs(denominator) <= 1e-12:
        return center
    offset = 0.5 * (left - right) / denominator
    spacing = float(spectrum.get("spacing_hz", 0.0))
    if spacing > 0 and math.isfinite(offset) and abs(offset) <= 0.75:
        return center + offset * spacing
    return center


def _eligible_peaks(spectrum: dict[str, Any], min_amplitude: float) -> list[dict[str, Any]]:
    # “最低幅度”是入选门槛；加一个很小的容差，避免 JSON/浮点往返把等值峰排除。
    return [peak for peak in spectrum.get("peaks", [])
            if float(peak.get("amplitude", 0.0)) + 1e-9 >= min_amplitude]


def _match_peak(frequency_hz: float, spectrum: dict[str, Any], line_us: float,
                min_amplitude: float, tolerance_hz: float,
                required_peak: dict[str, Any] | None = None) -> dict[str, Any] | None:
    apparent = alias_frequency(frequency_hz, line_us)
    peaks = _eligible_peaks(spectrum, min_amplitude)
    if required_peak is not None:
        required_id = _peak_id(required_peak)
        peaks = [peak for peak in peaks if _peak_id(peak) == required_id]
    if not peaks:
        return None
    peak = min(peaks, key=lambda item: (abs(float(item["hz"]) - apparent),
                                        -float(item.get("amplitude", 0.0))))
    difference = abs(float(peak["hz"]) - apparent)
    if difference > tolerance_hz + 1e-8:
        return None
    return {
        "peak_index": peak.get("index"),
        "peak_hz": float(peak["hz"]),
        "apparent_hz": float(apparent),
        "difference_hz": float(difference),
        "amplitude": float(peak.get("amplitude", 0.0)),
    }


def _branch_estimates(anchor_frequency: float, peak_hz: float, rate: float):
    """Nearest unfolded estimates k*rate +/- peak_hz and their branch keys."""
    estimates = []
    plus_order = int(round((anchor_frequency - peak_hz) / rate))
    for order in range(max(0, plus_order - 1), plus_order + 2):
        frequency = order * rate + peak_hz
        estimates.append((frequency, 1, order))
    minus_order = int(round((anchor_frequency + peak_hz) / rate))
    for order in range(max(1, minus_order - 1), minus_order + 2):
        frequency = order * rate - peak_hz
        estimates.append((frequency, -1, order))
    return estimates


def _candidate_score(match_a: dict[str, Any], match_b: dict[str, Any],
                     spectrum_a: dict[str, Any], spectrum_b: dict[str, Any]) -> dict[str, float]:
    amp_a = max(0.0, float(match_a["amplitude"]))
    amp_b = max(0.0, float(match_b["amplitude"]))
    strongest_a = max([float(p.get("amplitude", 0.0)) for p in spectrum_a.get("peaks", [])] + [1e-12])
    strongest_b = max([float(p.get("amplitude", 0.0)) for p in spectrum_b.get("peaks", [])] + [1e-12])
    relative_a = amp_a / strongest_a
    relative_b = amp_b / strongest_b
    floor_a = max(float(spectrum_a.get("floor", 0.25)), 1e-9)
    floor_b = max(float(spectrum_b.get("floor", 0.25)), 1e-9)
    prominence_a = amp_a / floor_a
    prominence_b = amp_b / floor_b
    minimum_amplitude = min(amp_a, amp_b)
    balance = minimum_amplitude / max(max(amp_a, amp_b), 1e-12)
    weakest_relative = min(relative_a, relative_b)
    weakest_prominence = min(prominence_a, prominence_b)
    prominence_confidence = min(1.0, weakest_prominence / 5.0)
    relative_confidence = min(1.0, weakest_relative / 0.25)
    clear_confidence = prominence_confidence * relative_confidence
    weak_peak_clear = bool(weakest_prominence >= 5.0 and weakest_relative >= 0.25)

    # Rank first by combined brightness evidence.  A weak partner is allowed to
    # back a strong peak when it is still prominent in its own spectrum; only
    # unclear/imbalanced evidence receives a modest penalty.
    combined_strength = amp_a + amp_b
    score = combined_strength * (0.75 + 0.25 * clear_confidence) * (0.90 + 0.10 * balance)
    return {
        "score": float(score),
        "combined_strength": float(combined_strength),
        "joint_strength": float(math.sqrt(amp_a * amp_b)),
        "minimum_amplitude": float(minimum_amplitude),
        "balance": float(balance),
        "weak_peak_clear": weak_peak_clear,
        "clear_confidence": float(clear_confidence),
        "weakest_relative_strength": float(weakest_relative),
        "weakest_prominence": float(weakest_prominence),
        "relative_strength_a": float(relative_a),
        "relative_strength_b": float(relative_b),
    }


def evaluate_frequency(frequency_hz: float, spectrum_a: dict[str, Any],
                       settings_a: dict[str, Any], spectrum_b: dict[str, Any],
                       settings_b: dict[str, Any], search: dict[str, Any]) -> dict[str, Any] | None:
    criteria = validate_search(search)
    frequency = float(frequency_hz)
    if not criteria["min_hz"] - 1e-8 <= frequency <= criteria["max_hz"] + 1e-8:
        return None
    match_a = _match_peak(frequency, spectrum_a, settings_a["line_us"],
                          criteria["min_amplitude"], criteria["tolerance_hz"])
    match_b = _match_peak(frequency, spectrum_b, settings_b["line_us"],
                          criteria["min_amplitude"], criteria["tolerance_hz"])
    if match_a is None or match_b is None:
        return None
    scored = _candidate_score(match_a, match_b, spectrum_a, spectrum_b)
    return {
        "frequency_hz": frequency,
        "frequency_difference_hz": abs(match_a["difference_hz"] - match_b["difference_hz"]),
        "matches": {"a": match_a, "b": match_b},
        "recommended": True,
        **scored,
    }


def find_common_frequencies(spectrum_a: dict[str, Any], settings_a: dict[str, Any],
                            spectrum_b: dict[str, Any], settings_b: dict[str, Any],
                            search: dict[str, Any]) -> dict[str, Any]:
    criteria = validate_search(search)
    fmin = criteria["min_hz"]
    fmax = criteria["max_hz"]
    threshold = criteria["min_amplitude"]
    tolerance = criteria["tolerance_hz"]
    rate_a = sample_rate_hz(settings_a["line_us"])
    rate_b = sample_rate_hz(settings_b["line_us"])
    peaks_a = _eligible_peaks(spectrum_a, threshold)
    peaks_b = _eligible_peaks(spectrum_b, threshold)
    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}

    # Every physical hypothesis has an A-side unfolding.  Pair it with the
    # nearest B-side unfoldings instead of comparing apparent peak positions.
    for peak_a in peaks_a:
        peak_a_hz = _peak_center(peak_a, spectrum_a)
        plus_order = int(math.floor((fmax - peak_a_hz) / rate_a))
        for order in range(0, plus_order + 1):
            anchor_a = order * rate_a + peak_a_hz
            if fmin <= anchor_a <= fmax:
                branches_a = [(anchor_a, 1, order)]
            else:
                branches_a = []
            minus_max_order = int(math.ceil((fmax + peak_a_hz) / rate_a))
            minus_min_order = max(1, int(math.floor((fmin + peak_a_hz) / rate_a)))
            for minus_order in range(minus_min_order, minus_max_order + 1):
                anchor_a = minus_order * rate_a - peak_a_hz
                if fmin <= anchor_a <= fmax:
                    branches_a.append((anchor_a, -1, minus_order))

            for anchor_a, sign_a, order_a in branches_a:
                bin_anchor_a = order_a * rate_a + sign_a * float(peak_a["hz"])
                for peak_b in peaks_b:
                    peak_b_hz = _peak_center(peak_b, spectrum_b)
                    for anchor_b, sign_b, order_b in _branch_estimates(
                            anchor_a, peak_b_hz, rate_b):
                        bin_anchor_b = order_b * rate_b + sign_b * float(peak_b["hz"])
                        gap = abs(bin_anchor_a - bin_anchor_b)
                        if gap > 2.0 * tolerance + 1e-8:
                            continue
                        refined_frequency = (anchor_a + anchor_b) / 2.0
                        balanced_frequency = (bin_anchor_a + bin_anchor_b) / 2.0
                        frequency = refined_frequency
                        in_range = fmin - 1e-8 <= frequency <= fmax + 1e-8
                        match_a = match_b = None
                        if in_range:
                            match_a = _match_peak(frequency, spectrum_a, settings_a["line_us"],
                                                  threshold, tolerance, peak_a)
                            match_b = _match_peak(frequency, spectrum_b, settings_b["line_us"],
                                                  threshold, tolerance, peak_b)
                        if match_a is None or match_b is None:
                            frequency = balanced_frequency
                            if fmin - 1e-8 <= frequency <= fmax + 1e-8:
                                match_a = _match_peak(frequency, spectrum_a, settings_a["line_us"],
                                                      threshold, tolerance, peak_a)
                                match_b = _match_peak(frequency, spectrum_b, settings_b["line_us"],
                                                      threshold, tolerance, peak_b)
                        if match_a is None or match_b is None:
                            continue
                        if not fmin - 1e-8 <= frequency <= fmax + 1e-8:
                            continue
                        key = (
                            _peak_id(peak_a), sign_a, order_a,
                            _peak_id(peak_b), sign_b, order_b,
                        )
                        scored = _candidate_score(match_a, match_b, spectrum_a, spectrum_b)
                        candidate = {
                            "frequency_hz": float(frequency),
                            "refined_frequency_hz": float(refined_frequency),
                            "frequency_difference_hz": float(gap),
                            "matches": {"a": match_a, "b": match_b},
                            **scored,
                        }
                        old = candidates.get(key)
                        if old is None or candidate["score"] > old["score"]:
                            candidates[key] = candidate

    ranked = sorted(candidates.values(),
                    key=lambda item: (-item["score"], item["frequency_hz"]))
    deduped = []
    for candidate in ranked:
        ids = (candidate["matches"]["a"].get("peak_index"),
               candidate["matches"]["b"].get("peak_index"))
        duplicate = False
        for previous in deduped:
            previous_ids = (previous["matches"]["a"].get("peak_index"),
                            previous["matches"]["b"].get("peak_index"))
            if ids == previous_ids and abs(candidate["frequency_hz"] - previous["frequency_hz"]) <= max(1e-6, tolerance):
                duplicate = True
                break
        if not duplicate:
            deduped.append(candidate)
    ranked = deduped
    # Alias orders can create dozens of mathematically valid repeats in a very
    # wide range.  Keep the response useful while retaining the strongest ones.
    ranked = ranked[:30]
    if ranked:
        ranked[0]["recommended"] = True
        for item in ranked[1:]:
            item["recommended"] = False
    return {"criteria": criteria, "candidates": ranked,
            "recommended": ranked[0] if ranked else None}


def adoption_state(card: dict[str, Any]) -> dict[str, Any] | None:
    trial = card.get("common_trial")
    if not isinstance(trial, dict):
        return None
    stored = trial.get("fingerprints", {})
    current = {slot: fingerprint(entry["settings"])
               for slot, entry in card.get("photos", {}).items()}
    changed = [slot for slot in ("a", "b")
               if slot in current and stored.get(slot) != current[slot]]
    result = dict(trial)
    result["current_fingerprints"] = current
    result["changed_slots"] = changed
    result["stale"] = bool(changed)
    return result


# Descriptive aliases for callers/tests that prefer singular or explicit names.
find_common_frequency = find_common_frequencies
common_frequencies = find_common_frequencies
common_frequency = find_common_frequencies
suggest_common_frequency = find_common_frequencies
