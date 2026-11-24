"""Read per-line luminance and compare a manually entered timing hypothesis."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, choice, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张照片超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(opened, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(opened.size) < 32 or max(opened.size) > 2600 or math.prod(opened.size) > 4_000_000:
                raise Invalid("照片边长须在32–2600 px内，总像素不超过400万")
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            return normalized.size, png(normalized)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("照片无法解码") from error


def png(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def settings(value, size):
    width, height = size
    x = integer(value.get("x"), "起点 X", 0, width - 16)
    y = integer(value.get("y"), "起点 Y", 0, height - 16)
    return {
        "x": x, "y": y,
        "w": integer(value.get("w"), "选区宽度", 16, width - x),
        "h": integer(value.get("h"), "选区高度", 16, height - y),
        "axis": choice(value.get("axis"), "读出方向", {"rows", "columns"}),
        "line_us": number(value.get("line_us"), "每线读出时间", 1, 10_000),
        "exposure_us": number(value.get("exposure_us"), "曝光时间", 1, 1_000_000),
        "summary": choice(value.get("summary"), "线统计", {"mean", "median"}),
        "trial_hz": number(value.get("trial_hz"), "试放频率", 0.1, 100_000),
        "correction_cap": correction_cap(value.get("correction_cap", 8)),
    }


def defaults(size):
    width, height = size
    return settings({
        "x": width // 8, "y": 0, "w": width * 3 // 4, "h": height, "axis": "rows",
        "line_us": 1000, "exposure_us": 500, "summary": "mean", "trial_hz": 760,
        "correction_cap": 8,
    }, size)


def capture_profile(image, configuration):
    x, y, w, h = (configuration[k] for k in ("x", "y", "w", "h"))
    region = image[y:y+h, x:x+w].astype(np.float64) @ LUMA
    row_readout = configuration["axis"] == "rows"
    axis = 1 if row_readout else 0
    summarize = np.mean if configuration["summary"] == "mean" else np.median
    levels = summarize(region, axis=axis)
    positions = np.arange(len(levels)) + (y if row_readout else x)
    extent = (w if row_readout else h)
    dark = np.count_nonzero(region < 2, axis=axis) / extent
    white = np.count_nonzero(region > 253, axis=axis) / extent
    return positions, levels, dark, white


def spectral_line(values, line_us):
    values = np.asarray(values, dtype=np.float64)
    length = len(values)
    points = 2 ** math.ceil(math.log2(length * 4))
    window = np.hanning(length)
    detrended = (values - values.mean()) * window
    response = 2 * np.abs(np.fft.rfft(detrended, n=points)) / window.sum()
    response[0] = 0
    response[-1] *= 0.5
    spacing = line_us / 1_000_000
    hz = np.fft.rfftfreq(points, d=spacing)
    interior = np.flatnonzero((response[1:-1] > response[:-2]) & (response[1:-1] >= response[2:])) + 1
    if response[-1] > response[-2]:
        interior = np.append(interior, len(response) - 1)
    floor = max(0.25, 2.5 * float(np.median(response[1:])))
    candidates = sorted((k for k in interior if response[k] > floor), key=lambda k: (-response[k], k))
    peaks = []
    separation = 1 / (length * spacing)
    for index in candidates:
        if any(abs(hz[index] - peak["hz"]) < separation for peak in peaks):
            continue
        peaks.append({"hz": float(hz[index]), "amplitude": float(response[index]), "index": int(index)})
        if len(peaks) == 12:
            break
    return {"spacing_hz": float(hz[1]), "resolution_hz": separation, "nyquist_hz": float(hz[-1]),
            "floor": floor, "peaks": peaks, "amplitudes": response.tolist()}


def manual_fit(positions, levels, line_us, frequency):
    times = positions * line_us / 1_000_000
    argument = (times * frequency % 1.0) * (2 * np.pi)
    basis = np.column_stack((np.ones(len(times)), np.cos(argument), np.sin(argument)))
    coefficients, _, rank, _ = np.linalg.lstsq(basis, levels, rcond=1e-9)
    prediction = basis @ coefficients
    radians_per_sample = frequency * line_us / 1_000_000
    cycles = abs((radians_per_sample + 0.5) % 1.0 - 0.5)
    return {
        "trial_hz": frequency, "apparent_hz": cycles * 1_000_000 / line_us,
        "mean": float(coefficients[0]),
        "amplitude": float(np.hypot(coefficients[1], coefficients[2])) if rank == 3 else None,
        "phase_deg": float(np.degrees(np.arctan2(-coefficients[2], coefficients[1])) % 360) if rank == 3 else None,
        "error": float(np.sqrt(np.mean((levels - prediction) ** 2))),
    }, prediction


def correction_cap(value):
    return integer(value, "单像素最大校正灰阶", 1, 64)


def line_correction(config, fit, predicted):
    """Signed per-readout-line offsets that flatten the adopted trial, never overshooting it.

    A line above the trial's flat level is pushed down and a line below it up; the
    magnitude stays within the trial's own deviation so local brightness differences
    (bright lettering, dark borders) keep their original ordering.
    """
    row_readout = config["axis"] == "rows"
    length = config["h"] if row_readout else config["w"]
    start = config["y"] if row_readout else config["x"]
    if len(predicted) != length:
        raise Invalid("试放与选区读出线数量不一致")
    cap = correction_cap(config["correction_cap"])
    offsets = np.clip(fit["mean"] - predicted, -cap, cap)
    return start, row_readout, np.asarray(offsets, dtype=np.float64)


def corrected_image(image, config, fit, predicted):
    """Apply one shared offset per readout line inside the frame; leave the rest as is.

    RGB channels receive the same additive amount. Per pixel the offset is trimmed to
    the headroom so channels not already at 0 or 255 can never land on a saturated
    value, which means no pixel is clipped and frame-exterior pixels stay untouched.
    """
    _, row_readout, wanted = line_correction(config, fit, predicted)
    x, y, w, h = config["x"], config["y"], config["w"], config["h"]
    region = image[y:y+h, x:x+w].astype(np.float64)
    offset = wanted.reshape((h, 1, 1) if row_readout else (1, w, 1))
    headroom = np.where(offset < 0, region.min(axis=2, keepdims=True) - 1.0,
                        254.0 - region.max(axis=2, keepdims=True))
    allowed = np.clip(offset, -np.maximum(headroom, 0.0), np.maximum(headroom, 0.0))
    # Floor for upward and ceil for downward moves keep rounding inside every bound.
    rounded = np.where(allowed >= 0, np.floor(region + allowed), np.ceil(region + allowed))
    adjusted = rounded.astype(np.uint8)
    result = image.copy()
    result[y:y+h, x:x+w] = adjusted
    actual = adjusted.astype(np.int16) - region.astype(np.int16)
    stats = {
        "cap": int(config["correction_cap"]),
        "max_applied": float(np.abs(actual).max()),
        "mean_abs": float(np.abs(allowed).mean()),
        "pixels_changed": int(np.count_nonzero(actual.any(axis=2))),
        "frame_pixels": int(w * h),
    }
    return result, stats


def line_level(image, config, line_index):
    """Luma summary of one readout line inside the current frame."""
    x, y, w, h = config["x"], config["y"], config["w"], config["h"]
    if config["axis"] == "rows":
        if not y <= line_index < y + h:
            return None
        strip = image[line_index, x:x+w]
    else:
        if not x <= line_index < x + w:
            return None
        strip = image[y:y+h, line_index]
    values = strip.astype(np.float64) @ LUMA
    if config["summary"] == "mean":
        return float(values.mean())
    return float(np.median(values))


def report(image, config):
    positions, measured, dark, white = capture_profile(image, config)
    fit, predicted = manual_fit(positions, measured, config["line_us"], config["trial_hz"])
    return {
        "summary": {
            "count": len(positions), "mean": float(measured.mean()), "min": float(measured.min()),
            "max": float(measured.max()), "rms": float(np.std(measured)),
            "dark_fraction": float(dark.mean()), "white_fraction": float(white.mean()),
        },
        "spectrum": spectral_line(measured, config["line_us"]), "fit": fit,
        "lines": [
            {"index": int(index), "time_us": float(index * config["line_us"]), "measured": float(level),
             "fit": float(fitted), "dark_fraction": float(low), "white_fraction": float(high)}
            for index, level, fitted, low, high in zip(positions, measured, predicted, dark, white)
        ],
    }, predicted


def practice():
    """Two synthetic captures of one light, with distinct readout timing and phase."""
    width, height = 408, 288
    y, x = np.indices((height, width), dtype=np.float64)
    outputs = {}
    for slot, rate, exposures, amplitudes, phases in (
        ("a", 1000, 415, (53, 31), (0.4, 1.4)),
        ("b", 1250, 620, (16, 31), (1.2, -0.5)),
    ):
        time = y / rate
        signal = sum(amplitude * np.cos(2 * np.pi * frequency * time + phase)
                     for amplitude, frequency, phase in zip(amplitudes, (760, 380), phases))
        texture = 4 * np.sin(x / 9 + y / 62) + 7 * np.cos(x / 72)
        gradient = (x - width / 2) * 0.025
        levels = 124 + signal + texture + gradient
        image = np.stack((levels + 3, levels, levels - 5), axis=-1)
        data = png(Image.fromarray(np.rint(np.clip(image, 0, 255)).astype(np.uint8), mode="RGB"))
        conf = {**defaults((width, height)), "line_us": 1_000_000 / rate, "exposure_us": exposures}
        outputs[slot] = ((width, height), data, conf)
    return outputs
