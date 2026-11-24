"""Unit and end-to-end checks for the LinePulse short proof.

Run with: python3 -m unittest discover -s tests -v
"""

import io
import os
import site
import sys
import tempfile
import unittest

# When launched with "python3 -I", third-party packages must be added explicitly.
_deps = os.environ.get("LINEPULSE_DEPS")
if _deps:
    site.addsitedir(_deps)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from image_lab.validation import Invalid
from linepulse import shortproof, signal
from linepulse.server import create_app


def make_lines(count, start=0):
    rng = np.random.default_rng(7)
    drift = 40 * np.sin(np.linspace(0, 3.2, count))
    measured = 128 + drift + rng.normal(0, 1.4, count)
    # A short, bright spike that generic thinning must not erase.
    measured[count // 2] = 235
    fit = measured - rng.normal(0, 0.8, count)
    return [
        {"index": start + i, "time_us": float((start + i) * 1000),
         "measured": float(measured[i]), "fit": float(fit[i]),
         "dark_fraction": 0.0, "white_fraction": 0.0}
        for i in range(count)
    ]


class ShortProofAlgorithmTests(unittest.TestCase):
    def test_default_budget_and_pin_set(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, None, None)
        self.assertEqual(proof["budget"], shortproof.DEFAULT_BUDGET)
        self.assertEqual(len(proof["retained"]), shortproof.DEFAULT_BUDGET)
        self.assertEqual(proof["pinned"], [0, 2399])
        self.assertIn(0, proof["retained"])
        self.assertIn(2399, proof["retained"])

    def test_picked_line_is_pinned_once(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 120, 137)
        self.assertEqual(proof["anchor"], 137)
        self.assertIn(137, proof["retained"])
        self.assertEqual(proof["pinned"], [0, 137, 2399])
        # Same real line only counts once.
        self.assertEqual(len(proof["retained"]), len(set(proof["retained"])))
        self.assertEqual(len(proof["points"]), 120)

    def test_anchor_equalling_end_does_not_double_count(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 100, 2399)
        self.assertEqual(proof["pinned"], [0, 2399])
        self.assertEqual(len(proof["points"]), 100)

    def test_budget_too_small_for_pins_explains_why(self):
        lines = make_lines(2400)
        with self.assertRaises(Invalid) as context:
            shortproof.build_short_proof(lines, 2, 137)
        message = str(context.exception)
        self.assertIn("2", message)
        self.assertIn("3", message)
        self.assertIn("首尾", message)

    def test_budget_two_without_interior_anchor_is_allowed(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 2, None)
        self.assertEqual(proof["retained"], [0, 2399])

    def test_budget_bounds(self):
        lines = make_lines(100)
        with self.assertRaises(Invalid):
            shortproof.build_short_proof(lines, 1, None)
        with self.assertRaises(Invalid):
            shortproof.build_short_proof(lines, 5001, None)
        with self.assertRaises(Invalid):
            shortproof.build_short_proof(lines, "abc", None)

    def test_out_of_selection_anchor_is_ignored_not_clipped(self):
        lines = make_lines(200, start=10)
        proof = shortproof.build_short_proof(lines, 50, 999)
        self.assertIsNone(proof["anchor"])
        self.assertEqual(proof["pinned"], [10, 209])
        self.assertTrue(all(10 <= index <= 209 for index in proof["retained"]))

    def test_budget_above_count_keeps_every_real_line(self):
        lines = make_lines(60)
        proof = shortproof.build_short_proof(lines, 300, None)
        self.assertTrue(proof["complete"])
        self.assertEqual([row["index"] for row in proof["points"]], [row["index"] for row in lines])

    def test_retained_points_are_real_lines_in_original_order(self):
        lines = make_lines(2400, start=30)
        proof = shortproof.build_short_proof(lines, 180, 500)
        real_indices = [row["index"] for row in lines]
        self.assertEqual(proof["retained"], sorted(proof["retained"]))
        self.assertTrue(all(index in set(real_indices) for index in proof["retained"]))
        self.assertEqual([row["index"] for row in proof["points"]], proof["retained"])
        # Point rows are the original measured/fit dictionaries, not resampled values.
        source = {row["index"]: row for row in lines}
        for row in proof["points"]:
            self.assertIs(row, source[row["index"]])

    def test_bright_spike_survives_compromise_selection(self):
        lines = make_lines(2400)
        spike_index = lines[2400 // 2]["index"]
        proof = shortproof.build_short_proof(lines, 240, None)
        self.assertIn(spike_index, proof["retained"])

    def test_slow_transitions_are_covered_across_the_whole_range(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 240, None)
        retained = np.asarray(proof["retained"])
        gaps = np.diff(retained)
        # No large uncovered hole: the gap-filling guard spans every region.
        self.assertLessEqual(gaps.max(), 2 * np.ceil(2399 / 239) + 2)

    def test_measured_and_fit_share_one_index_set(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 240, 777)
        self.assertEqual(len(proof["points"]), 240)
        for row in proof["points"]:
            self.assertIn("measured", row)
            self.assertIn("fit", row)

    def test_budget_exactly_equals_pinned_count_succeeds(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 3, 137)
        self.assertEqual(proof["retained"], [0, 137, 2399])
        self.assertEqual(proof["pinned"], [0, 137, 2399])

    def test_tiny_selection_with_anchor_covers_every_line(self):
        lines = make_lines(3, start=5)
        proof = shortproof.build_short_proof(lines, 3, 6)
        self.assertTrue(proof["complete"])
        self.assertEqual(proof["retained"], [5, 6, 7])

    def test_selection_is_deterministic(self):
        lines = make_lines(2400)
        first = shortproof.build_short_proof(lines, 150, 333)["retained"]
        second = shortproof.build_short_proof(lines, 150, 333)["retained"]
        self.assertEqual(first, second)

    def test_extra_points_never_collapse_into_one_region(self):
        lines = make_lines(2400)
        proof = shortproof.build_short_proof(lines, 60, 1200)
        gaps = np.diff(np.asarray(proof["retained"]))
        # With 60 points over 2400 lines the average gap is ~40; a single
        # uncovered hole larger than twice that would mean a region is missing.
        self.assertLessEqual(int(gaps.max()), 82)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="linepulse-test-")
        cls.app = create_app(cls.temp_dir.name)
        cls.client = cls.app.test_client()
        image = Image.fromarray(np.full((64, 96, 3), 120, dtype=np.uint8), mode="RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        response = cls.client.post("/linepulse/cards", data={
            "name": "短校样测试卡", "a": (io.BytesIO(buffer.getvalue()), "a.png"),
        })
        assert response.status_code == 201, response.data
        cls.card = response.get_json()
        cls.key = cls.card["id"]

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_trace_endpoint_returns_full_lines(self):
        response = self.client.get(f"/linepulse/cards/{self.key}/trace/a")
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertGreater(len(result["lines"]), 16)

    def test_shortproof_endpoint_pins_and_dedupes(self):
        response = self.client.get(f"/linepulse/cards/{self.key}/shortproof/a?budget=20&anchor=10")
        self.assertEqual(response.status_code, 200)
        proof = response.get_json()
        self.assertEqual(len(proof["retained"]), 20)
        first = proof["retained"][0]
        last = proof["retained"][-1]
        self.assertIn(first, proof["pinned"])
        self.assertIn(last, proof["pinned"])
        self.assertIn(10, proof["retained"])
        self.assertEqual(proof["anchor"], 10)
        self.assertEqual(proof["retained"], sorted(proof["retained"]))

    def test_shortproof_small_budget_error_is_400_with_reason(self):
        response = self.client.get(f"/linepulse/cards/{self.key}/shortproof/a?budget=2&anchor=10")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_proof_png_uses_same_points_as_json(self):
        query = "?budget=25&anchor=12"
        json_response = self.client.get(f"/linepulse/cards/{self.key}/shortproof/a{query}")
        png_response = self.client.get(f"/linepulse/cards/{self.key}/export/a/proof.png{query}")
        self.assertEqual(png_response.status_code, 200)
        self.assertEqual(png_response.mimetype, "image/png")
        proof = json_response.get_json()
        rendered = Image.open(io.BytesIO(png_response.data))
        self.assertEqual(rendered.size, (1120, 670))
        self.assertEqual(len(proof["points"]), 25)
        # Default-parameter PNG also renders and applies the default budget.
        default_png = self.client.get(f"/linepulse/cards/{self.key}/export/a/proof.png")
        self.assertEqual(default_png.status_code, 200)
        default_json = self.client.get(f"/linepulse/cards/{self.key}/shortproof/a").get_json()
        self.assertEqual(default_json["budget"], shortproof.DEFAULT_BUDGET)

    def test_csv_still_carries_every_unsampled_reading(self):
        response = self.client.get(f"/linepulse/cards/{self.key}/export/a/trace.csv")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8").strip().splitlines()
        full = self.client.get(f"/linepulse/cards/{self.key}/trace/a").get_json()
        self.assertEqual(len(body) - 1, len(full["lines"]))
        self.assertEqual(body[0].split(",")[:3], ["index", "time_us", "measured"])

    def test_line_lookup_uses_real_original_index(self):
        proof = self.client.get(f"/linepulse/cards/{self.key}/shortproof/a?budget=15").get_json()
        target = proof["retained"][7]
        response = self.client.get(f"/linepulse/cards/{self.key}/line/a?index={target}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["line"]["index"], target)

    def test_columns_axis_shortproof(self):
        # Use a dedicated card: switching axis changes the selection range and
        # would otherwise leak into the shared-card assertions above.
        image = Image.fromarray(np.full((64, 96, 3), 120, dtype=np.uint8), mode="RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        created = self.client.post("/linepulse/cards", data={
            "name": "列轴短校样卡", "a": (io.BytesIO(buffer.getvalue()), "columns.png"),
        })
        self.assertEqual(created.status_code, 201)
        key = created.get_json()["id"]
        card = self.client.get(f"/linepulse/cards/{key}").get_json()
        settings = card["photos"]["a"]["settings"]
        updated = self.client.patch(f"/linepulse/cards/{key}", json={
            "revision": card["revision"], "active": "a",
            "settings": {"a": {**settings, "axis": "columns"}},
        })
        self.assertEqual(updated.status_code, 200)
        trace = self.client.get(f"/linepulse/cards/{key}/trace/a").get_json()
        column_anchor = trace["lines"][5]["index"]
        proof = self.client.get(
            f"/linepulse/cards/{key}/shortproof/a?budget=18&anchor={column_anchor}").get_json()
        self.assertEqual(len(proof["retained"]), 18)
        self.assertIn(column_anchor, proof["retained"])
        self.assertEqual(proof["pinned"][0], proof["retained"][0])


if __name__ == "__main__":
    unittest.main()
