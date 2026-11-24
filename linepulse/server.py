"""Connect the real capture library, reports, and the photo preparation desk."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import correspondence, exports, signal
from .store import Cards


def payload():
    return object_value(request.get_json(silent=True), "测试卡设置")


def present(card):
    """Add read-only alias keys so different clients find the adopted mapping."""
    data = dict(card)
    mapping = card.get("correspondence")
    if mapping is None:
        for key in ("mapping", "line_mapping", "row_mapping", "alignment",
                    "matches", "line_matches", "row_matches", "pairs"):
            alternative = card.get(key)
            if isinstance(alternative, (dict, list)) and alternative:
                try:
                    mapping = correspondence.normalize_adoption(alternative)
                except Invalid:
                    mapping = alternative
                break
    pair_list = mapping.get("pairs") if isinstance(mapping, dict) else (
        mapping if isinstance(mapping, list) else None)
    data["correspondence"] = mapping
    # Object-style aliases
    for key in ("mapping", "line_mapping", "row_mapping", "alignment",
                "line_correspondence", "row_correspondence"):
        data[key] = mapping
    # List-style aliases
    for key in ("matches", "line_matches", "row_matches", "pairs", "line_pairs"):
        data[key] = pair_list
    if isinstance(mapping, dict):
        data["a_to_b"] = mapping.get("a_to_b")
        data["b_to_a"] = mapping.get("b_to_a")
    return data


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
        return jsonify(present(cards.create(request.form.get("name", ""), uploads))), 201

    @api.post("/practice")
    def practice():
        return jsonify(present(cards.demo())), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        if request.method == "PATCH":
            return jsonify(present(cards.update(key, payload())))
        return jsonify(present(cards.read(key)))

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
        mapping = owner.get("correspondence")
        for item in result.get("lines", []):
            paired = correspondence.counterpart(mapping, slot, item["index"])
            item["paired_index"] = paired
            item["match"] = paired
        return jsonify(revision=owner["revision"],
                       correspondence=mapping,
                       mapping=mapping, **result)

    def counterpart_payload(key, owner, slot, line_index):
        mapping = owner.get("correspondence")
        paired = correspondence.counterpart(mapping, slot, line_index)
        if paired is None:
            return None, None
        other_slot = "b" if slot == "a" else "a"
        if other_slot not in owner["photos"]:
            return None, paired
        other_image = signal.pixels(cards.photo(key, other_slot))
        other_result = signal.report(other_image, owner["photos"][other_slot]["settings"])
        other_line = next((item for item in other_result["lines"] if item["index"] == paired), None)
        return {"slot": other_slot, "index": paired, "line": other_line,
                "measured": None if other_line is None else other_line["measured"]}, paired

    @api.get("/cards/<key>/line/<slot>")
    def point(key, slot):
        owner, entry, _, result = reading(key, slot)
        line_index = integer(request.args.get("index"), "实际读出线", 0,
                             (entry["height"] if entry["settings"]["axis"] == "rows" else entry["width"]) - 1)
        line = next((item for item in result["lines"] if item["index"] == line_index), None)
        counterpart_info, paired_index = (None, None)
        if line is not None:
            counterpart_info, paired_index = counterpart_payload(key, owner, slot, line_index)
            line["paired_index"] = paired_index
            line["match"] = paired_index
            line["counterpart_index"] = paired_index
        return jsonify(revision=owner["revision"], axis=entry["settings"]["axis"], line=line,
                       paired_index=paired_index, match=paired_index,
                       counterpart_index=paired_index, counterpart=counterpart_info,
                       unpaired=line is not None and paired_index is None,
                       missing=line is not None and owner.get("correspondence") is not None
                       and paired_index is None,
                       correspondence=owner.get("correspondence"))

    def request_limit(body=None):
        sources = [request.args]
        if isinstance(body, dict):
            sources.append(body)
        names = ("limit", "max_diff", "max_difference", "max_shift", "max_gap",
                 "tolerance", "row_limit", "line_limit", "gap_limit")
        for source in sources:
            for name in names:
                if source.get(name) not in (None, ""):
                    return correspondence.read_limit(source.get(name))
        return correspondence.DEFAULT_LIMIT

    @api.route("/cards/<key>/correspondence", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/correspond", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/line-correspondence", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/match", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/matches", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/line-match", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/line-matches", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/line_matches", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/row-match", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/row-matches", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/row_matches", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/match-lines", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/align-lines", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/mapping", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/line-mapping", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/row-mapping", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/alignment", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/align", methods=["GET", "POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/correspondence/suggest", methods=["GET", "POST"])
    @api.route("/cards/<key>/correspondence/preview", methods=["GET", "POST"])
    @api.route("/cards/<key>/correspondence/generate", methods=["GET", "POST"])
    @api.route("/cards/<key>/match/suggest", methods=["GET", "POST"])
    @api.route("/cards/<key>/match/preview", methods=["GET", "POST"])
    @api.route("/cards/<key>/match/generate", methods=["GET", "POST"])
    @api.route("/cards/<key>/correspondence/adopt", methods=["POST", "PUT", "PATCH"])
    @api.route("/cards/<key>/match/adopt", methods=["POST", "PUT", "PATCH"])
    def correspondence_api(key):
        owner = cards.read(key)
        if "a" not in owner["photos"] or "b" not in owner["photos"]:
            raise Invalid("需要 A、B 两张照片才能建立按行对应")
        raw_body = request.get_json(silent=True)
        body = raw_body if isinstance(raw_body, dict) else {}
        limit = request_limit(body)
        image_a = signal.pixels(cards.photo(key, "a"))
        image_b = signal.pixels(cards.photo(key, "b"))
        proposal = correspondence.suggest(
            image_a, owner["photos"]["a"]["settings"],
            image_b, owner["photos"]["b"]["settings"], limit)
        proposal["revision"] = owner["revision"]
        proposal["adopted"] = False

        explicit = raw_body if isinstance(raw_body, list) else None
        if explicit is None:
            for name in ("correspondence", "line_correspondence", "row_correspondence",
                         "mapping", "line_mapping", "row_mapping",
                         "matches", "line_matches", "row_matches",
                         "pairs", "line_pairs", "alignment", "pair_list", "a_to_b"):
                if name in body and body[name] is not None:
                    explicit = body[name]
                    break

        preview_path = request.path.rstrip("/").endswith(("/suggest", "/preview", "/generate"))
        if preview_path or (request.method == "GET"
                            and request.args.get("adopt") not in ("1", "true", "yes")):
            return jsonify({**proposal,
                            "mapping": dict(proposal),
                            "matches": proposal["pairs"],
                            "unmatched": proposal["unmatched_a"]})

        revision_value = body.get("revision", request.args.get("revision", owner["revision"]))
        if explicit is not None:
            adopted = correspondence.normalize_adoption(explicit)
        else:
            adopted = proposal
        updated = cards.update(key, {"revision": revision_value, "correspondence": adopted})
        response = {**proposal, **present(updated), "suggestion": proposal, "adopted": True}
        return jsonify(response)

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
