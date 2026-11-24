"""Store independent test cards with normalized photos and explicit revisions."""

from pathlib import Path
from threading import RLock
from time import time_ns
from uuid import uuid4
import json

from image_lab.validation import Conflict, Invalid, identifier, integer, object_value, text

from . import signal


class Cards:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def folder(self, key):
        folder = self.root / identifier(key)
        if not (folder / "card.json").is_file():
            raise Invalid("没有找到这份测试卡")
        return folder

    def read(self, key, revision=None):
        with self.lock:
            card = json.loads((self.folder(key) / "card.json").read_text())
            if revision is not None and integer(revision, "工作版本", 1) != card["revision"]:
                raise Conflict("当前测试卡已有改动，请重新打开")
            for entry in card["photos"].values():
                entry["settings"].setdefault("correction_cap", 8)
            return card

    @staticmethod
    def commit(folder, record):
        temporary = folder / (uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
        temporary.replace(folder / "card.json")

    def list(self):
        with self.lock:
            entries = []
            for path in self.root.glob("*/card.json"):
                value = json.loads(path.read_text())
                entries.append({"id": value["id"], "name": value["name"], "revision": value["revision"],
                                "photo_count": len(value["photos"]), "synthetic": value["synthetic"],
                                "modified_ns": path.stat().st_mtime_ns})
            return sorted(entries, key=lambda row: (-row["modified_ns"], row["id"]))

    def create(self, name, uploads, synthetic=False):
        name = text(name, "测试卡名称", 80, False)
        if "a" not in uploads or set(uploads) - {"a", "b"}:
            raise Invalid("须先提供相片 A")
        with self.lock:
            if len(self.list()) >= 150:
                raise Invalid("测试卡库已达150份")
            key = uuid4().hex
            folder = self.root / key
            folder.mkdir()
            photos = {}
            for slot, (title, size, data, config) in uploads.items():
                (folder / f"{slot}.png").write_bytes(data)
                photos[slot] = {"name": text(title, "原片名称", 140, False),
                                "width": size[0], "height": size[1], "settings": config}
            card = {"id": key, "revision": 1, "name": name, "synthetic": synthetic,
                    "active": "a", "photos": photos}
            self.commit(folder, card)
            return card

    def demo(self):
        captures = {slot: (f"合成读出 {slot.upper()}", size, pixels, cfg)
                    for slot, (size, pixels, cfg) in signal.practice().items()}
        return self.create("台架 · 同一光源的两次扫描", captures, synthetic=True)

    def update(self, key, payload):
        with self.lock:
            card = self.read(key, integer(payload.get("revision"), "工作版本", 1))
            name = text(payload.get("name", card["name"]), "测试卡名称", 80, False)
            proposed = object_value(payload.get("settings", {}), "读出设置")
            if not set(proposed) <= set(card["photos"]):
                raise Invalid("只可编辑已有相片的读出设置")
            for slot, value in proposed.items():
                entry = card["photos"][slot]
                confidence = object_value(value, "照片设置")
                entry["settings"] = signal.settings({**entry["settings"], **confidence}, (entry["width"], entry["height"]))
                self._drop_correction(self.folder(key), slot, entry)
            card.update(name=name, revision=card["revision"] + 1,
                        active=payload.get("active", card["active"]))
            if card["active"] not in card["photos"]:
                raise Invalid("选中的相片不存在")
            self.commit(self.folder(key), card)
            return card

    def adopt_correction(self, key, revision, slot, settings, corrected_png, stats):
        """Persist one adopted light correction; the original photo stays untouched."""
        with self.lock:
            folder = self.folder(key)
            card = self.read(key, integer(revision, "工作版本", 1))
            if slot not in card["photos"]:
                raise Invalid("该测试卡没有选择的照片")
            entry = card["photos"][slot]
            entry["settings"] = signal.settings(
                {**entry["settings"], **settings}, (entry["width"], entry["height"]))
            temporary = folder / (uuid4().hex + ".png.tmp")
            temporary.write_bytes(corrected_png)
            temporary.replace(folder / f"corrected-{slot}.png")
            entry["correction"] = {
                "revision": card["revision"] + 1, "adopted_ns": time_ns(),
                "trial_hz": entry["settings"]["trial_hz"],
                "correction_cap": entry["settings"]["correction_cap"], **stats,
            }
            card["revision"] += 1
            self.commit(folder, card)
            return card

    @staticmethod
    def _drop_correction(folder, slot, entry):
        entry.pop("correction", None)
        (folder / f"corrected-{slot}.png").unlink(missing_ok=True)

    def corrected_photo(self, key, slot):
        if slot not in {"a", "b"}:
            raise Invalid("照片编号必须为 A 或 B")
        folder = self.folder(key)
        path = folder / f"corrected-{slot}.png"
        record = json.loads((folder / "card.json").read_text())
        entry = record["photos"].get(slot)
        if not entry or "correction" not in entry or not path.is_file():
            raise Invalid("这张照片还没有采用轻校正")
        return path

    def photo(self, key, slot):
        if slot not in {"a", "b"}:
            raise Invalid("照片编号必须为 A 或 B")
        path = self.folder(key) / f"{slot}.png"
        if not path.is_file():
            raise Invalid("该测试卡没有这张相片")
        return path
