"""Export actual image coordinates, measured levels, and the active trial."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw, ImageFont

from .signal import png


def trace_table(result):
    buffer = StringIO(newline="")
    fields = ("index", "time_us", "measured", "fit", "dark_fraction", "white_fraction")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(result["lines"])
    return buffer.getvalue().encode("utf-8")


def proof(image, entry, result, view="original"):
    sheet = Image.new("RGB", (1120, 670), "#0c1521")
    canvas = ImageDraw.Draw(sheet)
    regular = ImageFont.load_default(size=16)
    headline = ImageFont.load_default(size=26)
    canvas.text((32, 32), "LINEPULSE / SENSOR READOUT", font=headline, fill="#edf3f8")
    view_label = "Corrected (in-frame light correction)" if view == "corrected" else "Original"
    canvas.text((32, 72), f"{entry['width']} x {entry['height']} px  |  {entry['settings']['line_us']:.2f} us per line  |  {view_label}",
                font=regular, fill="#adc0cf")
    correction = entry.get("correction")
    if view == "corrected" and correction:
        canvas.text((32, 92),
                    f"Trial {correction['trial_hz']:.2f} Hz  |  per-pixel cap {correction['correction_cap']}  |  applied max {correction['max_applied']:.1f}  |  pixels {correction['pixels_changed']}/{correction['frame_pixels']}",
                    font=regular, fill="#8cdaca")
    thumbnail = Image.fromarray(image)
    thumbnail.thumbnail((522, 490))
    canvas.rounded_rectangle((26, 116, 556, 618), radius=12, fill="#112031")
    px, py = 30 + (522 - thumbnail.width) // 2, 122 + (484 - thumbnail.height) // 2
    sheet.paste(thumbnail, (px, py))
    x, y, w, h = [entry["settings"][name] for name in ("x", "y", "w", "h")]
    scale_x = thumbnail.width / entry["width"]
    scale_y = thumbnail.height / entry["height"]
    canvas.rectangle((px + x * scale_x, py + y * scale_y, px + (x + w) * scale_x - 1,
                      py + (y + h) * scale_y - 1), outline="#6bdfca", width=3)
    canvas.rounded_rectangle((582, 116, 1094, 618), radius=12, fill="#112031")
    canvas.text((600, 135), "Measured profile / saved trial", font=regular, fill="#edf3f8")
    graph = (606, 185, 1070, 546)
    first, top, last, bottom = graph
    for index in range(6):
        line_y = top + index * (bottom - top) / 5
        canvas.line((first, line_y, last, line_y), fill="#30465a")
    rows = result["lines"]
    sample_values = [row[key] for row in rows for key in ("measured", "fit")]
    lo, hi = min(sample_values) - 1.0, max(sample_values) + 1.0
    for key, color in (("measured", "#8cdaca"), ("fit", "#f3bc79")):
        points = [(first + i * (last - first) / (len(rows) - 1),
                   bottom - (row[key] - lo) * (bottom - top) / (hi - lo))
                  for i, row in enumerate(rows)]
        canvas.line(points, fill=color, width=2)
    fit = result["fit"]
    canvas.text((606, 571), f"Trial {fit['trial_hz']:.2f} Hz  |  Residual {fit['error']:.2f}",
                font=regular, fill="#adc0cf")
    return png(sheet)


def package(card, data):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=4) as archive:
        archive.writestr("card.json", json.dumps(card, ensure_ascii=False, indent=2))
        for slot, (original, result) in data.items():
            archive.writestr(f"sources/{slot}.png", original)
            archive.writestr(f"profiles/{slot}.csv", trace_table(result))
    return buffer.getvalue()
