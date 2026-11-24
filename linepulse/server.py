"""Connect the real capture library, reports, and the photo preparation desk."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

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
        return card, entry, image, signal.report(image, entry["settings"])

    @api.get("/cards/<key>/trace/<slot>")
    def trace(key, slot):
        owner, _, _, result = reading(key, slot)
        return jsonify(revision=owner["revision"], **result)

    @api.get("/cards/<key>/line/<slot>")
    def point(key, slot):
        owner, entry, _, result = reading(key, slot)
        line_index = integer(request.args.get("index"), "实际读出线", 0,
                             (entry["height"] if entry["settings"]["axis"] == "rows" else entry["width"]) - 1)
        line = next((item for item in result["lines"] if item["index"] == line_index), None)
        return jsonify(revision=owner["revision"], axis=entry["settings"]["axis"], line=line)

    @api.get("/cards/<key>/export/<slot>/trace.csv")
    def trace_csv(key, slot):
        _, _, _, result = reading(key, slot)
        return download(exports.trace_table(result), "text/csv", f"lines-{slot}.csv")

    @api.get("/cards/<key>/export/<slot>/proof.png")
    def plate(key, slot):
        _, entry, image, result = reading(key, slot)
        return download(exports.proof(image, entry, result), "image/png", f"readout-{slot}.png")

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card = cards.read(key, request.args.get("revision"))
        prepared = {}
        for slot, entry in card["photos"].items():
            path = cards.photo(key, slot)
            prepared[slot] = (path.read_bytes(), signal.report(signal.pixels(path), entry["settings"]))
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
