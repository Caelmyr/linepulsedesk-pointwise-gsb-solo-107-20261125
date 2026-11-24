"""Connect the real capture library, reports, and the photo preparation desk."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, number, object_value

from . import common, exports, signal
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

    def present_card(owner):
        trial = common.adoption_state(owner)
        if trial is not None:
            owner["common_trial"] = trial
            owner["common"] = trial
            owner["common_hz"] = trial["frequency_hz"]
            owner["common_frequency_hz"] = trial["frequency_hz"]
        return owner

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        owner = cards.update(key, payload()) if request.method == "PATCH" else cards.read(key)
        return jsonify(present_card(owner))

    @api.route("/cards/<key>/common-frequency", methods=["GET", "POST"])
    @api.route("/cards/<key>/common-frequency/search", methods=["GET", "POST"])
    @api.route("/cards/<key>/common", methods=["GET", "POST"])
    def common_frequency_search(key):
        if request.method == "POST":
            body = request.get_json(silent=True)
            if body is None:
                body = {}
            body = object_value(body, "共同频率参数")
        else:
            body = request.args.to_dict()
        card = cards.read(key, body.get("revision"))
        _, _, _, found = common_inputs(card, body)
        return jsonify(revision=card["revision"], **found)

    @api.route("/cards/<key>/common-trial", methods=["POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/common-frequency/adopt", methods=["POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/common", methods=["PUT", "PATCH"])
    def common_trial_save(key):
        return jsonify(present_card(adopt_body(key)))

    @api.delete("/cards/<key>/common-trial")
    @api.route("/cards/<key>/common", methods=["DELETE"])
    def common_trial_clear(key):
        body = request.get_json(silent=True) or {}
        revision = integer(body.get("revision", request.args.get("revision")), "工作版本", 1)
        return jsonify(present_card(cards.clear_common_trial(key, revision)))

    @api.get("/cards/<key>/source/<slot>.png")
    def source(key, slot):
        return send_file(cards.photo(key, slot), mimetype="image/png", conditional=True)

    def reading(key, slot):
        card = cards.read(key, request.args.get("revision"))
        if slot not in card["photos"]:
            raise Invalid("该测试卡没有选择的照片")
        entry = card["photos"][slot]
        image = signal.pixels(cards.photo(key, slot))
        common_trial = common.adoption_state(card)
        common_hz = common_trial.get("frequency_hz") if common_trial else None
        result = signal.report(image, entry["settings"], common_hz)
        if common_trial is not None:
            result["common_trial"] = common_trial
        return card, entry, image, result

    def common_inputs(card, body=None):
        if set(card["photos"]) != {"a", "b"}:
            raise Invalid("共同闪烁频率需要同一测试卡中有 A、B 两张照片")
        body = body if isinstance(body, dict) else {}
        search_body = body.get("search", body.get("criteria", body.get("range", body)))
        criteria = common.validate_search(search_body)
        spectra = {}
        settings = {}
        for slot in ("a", "b"):
            entry = card["photos"][slot]
            image = signal.pixels(cards.photo(card["id"], slot))
            report = signal.report(image, entry["settings"])
            spectra[slot] = report["spectrum"]
            settings[slot] = entry["settings"]
        found = common.find_common_frequencies(
            spectra["a"], settings["a"], spectra["b"], settings["b"], criteria)
        return criteria, spectra, settings, found

    def adopt_body(key):
        body = payload()
        revision = integer(body.get("revision"), "工作版本", 1)
        card = cards.read(key, revision)
        criteria, spectra, settings, found = common_inputs(card, body)
        nested = body.get("search") if isinstance(body.get("search"), dict) else body.get("criteria", body.get("range", {}))
        if not isinstance(nested, dict):
            nested = {}
        requested = body.get("frequency_hz", body.get("common_hz", nested.get("frequency_hz", nested.get("common_hz"))))
        if requested is None and body.get("candidate_index") is not None:
            index = integer(body.get("candidate_index"), "候选序号", 0, 29)
            if index >= len(found["candidates"]):
                raise Invalid("选择的共同频率候选不存在")
            requested = found["candidates"][index]["frequency_hz"]
        if requested is None and body.get("adopt_recommended", True):
            if not found["recommended"]:
                raise Invalid("没有两张照片共同匹配的频率，不能采用")
            requested = found["recommended"]["frequency_hz"]
        frequency = number(requested, "共同试放频率", criteria["min_hz"], criteria["max_hz"])
        evaluated = common.evaluate_frequency(
            frequency, spectra["a"], settings["a"], spectra["b"], settings["b"], criteria)
        if evaluated is None:
            raise Invalid("该频率在两张照片上都没有超过门槛且对得上的峰，不能作为共同试放")
        trial = {
            "frequency_hz": frequency,
            "criteria": criteria,
            "match": evaluated,
            "recommended_hz": found["recommended"]["frequency_hz"] if found["recommended"] else None,
            "fingerprints": {slot: common.fingerprint(settings[slot]) for slot in ("a", "b")},
            "critical": {slot: common.critical_settings(settings[slot]) for slot in ("a", "b")},
            "adopted_from_revision": revision,
        }
        return cards.save_common_trial(key, revision, trial)

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
        common_trial = common.adoption_state(card)
        common_hz = common_trial.get("frequency_hz") if common_trial else None
        prepared = {}
        for slot, entry in card["photos"].items():
            path = cards.photo(key, slot)
            prepared[slot] = (path.read_bytes(),
                              signal.report(signal.pixels(path), entry["settings"], common_hz))
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
