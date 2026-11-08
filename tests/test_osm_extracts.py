"""movisda抽出の条件付き取得判定と実測メタデータfixtureのテスト。"""

import json
import unittest
from pathlib import Path

from paperphone.osm_extracts import (
    CONDITIONAL,
    FAILED,
    INITIAL,
    MOVISDA_BASE,
    RELOADED,
    UNCHANGED,
    ExtractChecker,
    ExtractState,
    ExtractsError,
    prefecture_url,
    state_from_meta,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movisda_tokyo_headers.json"


def real_tokyo_state() -> ExtractState:
    with FIXTURE.open(encoding="utf-8") as stream:
        document = json.load(stream)
    return state_from_meta(document["headers"])


class PrefectureUrlTests(unittest.TestCase):
    def test_tokyo_resolves_to_jp_13(self):
        self.assertEqual(f"{MOVISDA_BASE}/admin/JP-13-latest.osm.pbf", prefecture_url(13))

    def test_hokkaido_is_first(self):
        self.assertEqual(f"{MOVISDA_BASE}/admin/JP-01-latest.osm.pbf", prefecture_url(1))

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            prefecture_url(0)
        with self.assertRaises(ValueError):
            prefecture_url(48)


class PlanTests(unittest.TestCase):
    def test_empty_state_starts_initial_without_validation_headers(self):
        plan = ExtractChecker(ExtractState()).plan()
        self.assertEqual(INITIAL, plan.action)
        self.assertFalse(plan.conditional)
        self.assertNotIn("If-None-Match", plan.headers)
        self.assertNotIn("If-Modified-Since", plan.headers)

    def test_saved_etag_becomes_if_none_match(self):
        plan = ExtractChecker(real_tokyo_state()).plan()
        self.assertEqual(CONDITIONAL, plan.action)
        self.assertEqual('"258937993"', plan.headers["If-None-Match"])

    def test_last_modified_without_etag_is_used(self):
        checker = ExtractChecker(ExtractState(last_modified="Sat, 07 Nov 2026 01:00:00 GMT"))
        plan = checker.plan({"User-Agent": "paperphone-probe/0.1"})
        self.assertEqual(CONDITIONAL, plan.action)
        self.assertEqual("Sat, 07 Nov 2026 01:00:00 GMT", plan.headers["If-Modified-Since"])
        self.assertIn("User-Agent", plan.headers)


class ResponseTests(unittest.TestCase):
    def test_real_meta_fixture_is_complete(self):
        state = real_tokyo_state()
        self.assertEqual('"258937993"', state.etag)
        self.assertEqual("Sat, 07 Nov 2026 01:00:00 GMT", state.last_modified)
        self.assertEqual(132_516_215, state.bytes_count)
        self.assertEqual("JP-13-202611070100.osm.pbf", state.filename)

    def test_not_modified_keeps_previous_state(self):
        checker = ExtractChecker(real_tokyo_state())
        outcome, after = checker.observe(304, {})
        self.assertEqual(UNCHANGED, outcome)
        self.assertEqual('"258937993"', after.state.etag)

    def test_reload_records_new_headers(self):
        checker = ExtractChecker(real_tokyo_state())
        headers = {
            "Content-Disposition": 'attachment; filename="JP-13-202611080100.osm.pbf"',
            "Content-Type": "application/x-pbf",
            "ETag": '"900000123"',
            "Last-Modified": "Sun, 08 Nov 2026 01:00:00 GMT",
            "Content-Length": "133000000",
        }
        outcome, after = checker.observe(200, headers)
        self.assertEqual(RELOADED, outcome)
        self.assertEqual('"900000123"', after.state.etag)
        self.assertEqual("JP-13-202611080100.osm.pbf", after.state.filename)
        self.assertEqual(133_000_000, after.state.bytes_count)

    def test_failure_keeps_previous_state(self):
        checker = ExtractChecker(real_tokyo_state())
        outcome, after = checker.observe(503, {})
        self.assertEqual(FAILED, outcome)
        self.assertEqual('"258937993"', after.state.etag)

    def test_non_pbf_content_type_is_rejected(self):
        checker = ExtractChecker(ExtractState())
        with self.assertRaises(ExtractsError):
            checker.observe(200, {"Content-Type": "text/html", "ETag": '"x"'})

    def test_missing_validation_headers_are_rejected(self):
        checker = ExtractChecker(ExtractState())
        with self.assertRaises(ExtractsError):
            checker.observe(200, {"Content-Type": "application/x-pbf"})

    def test_abnormal_content_length_is_rejected(self):
        checker = ExtractChecker(ExtractState())
        with self.assertRaises(ExtractsError):
            checker.observe(200, {
                "Content-Type": "application/x-pbf",
                "ETag": '"x"',
                "Content-Length": str(10_000_000_000),
            })


if __name__ == "__main__":
    unittest.main()
