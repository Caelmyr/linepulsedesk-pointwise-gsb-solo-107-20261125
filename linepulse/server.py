"""Connect the real capture library, reports, and the photo preparation desk."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for
from PIL import Image

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import exports, signal
from .store import Cards


def payload():
    return object_value(request.get_json(silent=True), "测试卡设置")


def download(data, mime, filename=None):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename,
                     as_attachment=filename is not None)


def create_app(directory=None):
    root = Path(directory or os.environ.get("LINEPULSE_DATA", ".linepulse-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("linepulse", __name__, url_prefix="/linepulse")

    @desk.get("/")
    def home():
        return render_template("linepulse.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="linepulse")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 38 * 1024 * 1024
        uploads = {}
        for slot in ("a", "b"):
            item = request.files.get(slot)
            if not item or not item.filename:
                continue
            size, image = signal.decode(item.stream.read(signal.MAX_IMAGE_BYTES + 1))
            uploads[slot] = (Path(item.filename).name[:140], size, image, signal.defaults(size))
        return jsonify(cards.create(request.form.get("name", ""), uploads)), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, payload()) if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/source/<slot>.png")
    def source(key, slot):
        return send_file(cards.photo(key, slot), mimetype="image/png", conditional=True)

    def reading(key, slot):
        card = cards.read(key, request.args.get("revision"))
        if slot not in card["photos"]:
            raise Invalid("该测试卡没有选择的照片")
        entry = card["photos"][slot]
        image = signal.pixels(cards.photo(key, slot))
        result, predicted = signal.report(image, entry["settings"])
        return card, entry, image, result, predicted

    @api.get("/cards/<key>/trace/<slot>")
    def trace(key, slot):
        owner, _, _, result, _ = reading(key, slot)
        return jsonify(revision=owner["revision"], **result)

    @api.get("/cards/<key>/line/<slot>")
    def point(key, slot):
        owner, entry, _, result, _ = reading(key, slot)
        line_index = integer(request.args.get("index"), "实际读出线", 0,
                             (entry["height"] if entry["settings"]["axis"] == "rows" else entry["width"]) - 1)
        line = next((item for item in result["lines"] if item["index"] == line_index), None)
        payload_out = {"revision": owner["revision"], "axis": entry["settings"]["axis"], "line": line}
        if line is not None and "correction" in entry:
            corrected = signal.pixels(cards.corrected_photo(key, slot))
            payload_out["corrected"] = signal.line_level(corrected, entry["settings"], line_index)
        return jsonify(payload_out)

    @api.get("/cards/<key>/export/<slot>/trace.csv")
    def trace_csv(key, slot):
        _, _, _, result, _ = reading(key, slot)
        return download(exports.trace_table(result), "text/csv", f"lines-{slot}.csv")

    @api.get("/cards/<key>/export/<slot>/proof.png")
    def plate(key, slot):
        _, entry, image, result, _ = reading(key, slot)
        view = request.args.get("view", "original")
        if view == "corrected":
            shown = signal.pixels(cards.corrected_photo(key, slot))
        elif view != "original":
            raise Invalid("校样视图必须是原图或校正图")
        else:
            shown = image
        return download(exports.proof(shown, entry, result, view), "image/png", f"readout-{slot}-{view}.png")

    @api.get("/cards/<key>/corrected/<slot>.png")
    def corrected_source(key, slot):
        return send_file(cards.corrected_photo(key, slot), mimetype="image/png", conditional=True)

    @api.post("/cards/<key>/correction/<slot>")
    def adopt_correction(key, slot):
        body = payload()
        cap = signal.correction_cap(body.get("correction_cap", 8))
        card = cards.read(key, integer(body.get("revision"), "工作版本", 1))
        if slot not in card["photos"]:
            raise Invalid("该测试卡没有选择的照片")
        entry = card["photos"][slot]
        config = signal.settings({**entry["settings"], "correction_cap": cap},
                                 (entry["width"], entry["height"]))
        image = signal.pixels(cards.photo(key, slot))
        positions, measured, _, _ = signal.capture_profile(image, config)
        fit, predicted = signal.manual_fit(positions, measured, config["line_us"], config["trial_hz"])
        corrected, stats = signal.corrected_image(image, config, fit, predicted)
        updated = cards.adopt_correction(
            key, body["revision"], slot, {"correction_cap": cap},
            signal.png(Image.fromarray(corrected, mode="RGB")), stats)
        return jsonify(updated), 201

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card = cards.read(key, request.args.get("revision"))
        prepared = {}
        for slot, entry in card["photos"].items():
            path = cards.photo(key, slot)
            prepared[slot] = (path.read_bytes(),
                              signal.report(signal.pixels(path), entry["settings"])[0])
        return download(exports.package(card, prepared), "application/zip", f"linepulse-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare/<slot>")
    def prepare(key, slot):
        card = cards.read(key, request.args.get("revision"))
        if slot not in card["photos"]:
            raise Invalid("该测试卡没有选择的照片")
        image = signal.pixels(cards.photo(key, slot)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["photos"][slot]["name"])
        project = services["store"].create(f"{card['name']} · 原片 {slot.upper()}")
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk
