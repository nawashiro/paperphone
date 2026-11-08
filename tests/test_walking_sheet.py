"""選択版と徒歩経路の紙面投影。県/版違い・経路なしを成功扱いしない。"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from paperphone.osm_derived import build_derived, derived_path
from paperphone.places import Place, PlaceIndex
from paperphone.selection import GenerationSnapshot
from paperphone.walking import WalkingError, WalkingRoute
from paperphone.walking_sheet import build_walking_sheet, RoutePreparationError

SHA = "b" * 64
XML = '''<osm version="0.6">
<node id="1" lat="35.68" lon="139.76"><tag k="name" v="出発駅"/><tag k="railway" v="station"/></node>
<node id="2" lat="35.681" lon="139.76"/>
<node id="3" lat="35.682" lon="139.76"><tag k="name" v="到着公園"/><tag k="leisure" v="park"/></node>
<node id="4" lat="35.685" lon="139.76"><tag k="name" v="孤立地点"/></node>
<node id="5" lat="35.686" lon="139.76"/>
<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/></way>
<way id="20"><nd ref="4"/><nd ref="5"/><tag k="highway" v="footway"/></way>
</osm>'''
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


class WalkingSheetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "map.osm"
        self.path.write_text(XML, encoding="utf-8")
        build_derived(self.path, 13, SHA, directory=self.root)
        self.index = PlaceIndex.from_derived(derived_path(13, SHA, directory=self.root), SHA, 13)
        self.extract = SimpleNamespace(prefecture=13, path=self.path, basis=NOW, sha256=SHA)
        self.start = self.index.get("node/1")
        self.end = self.index.get("node/3")

    def test_real_graph_distance_time_and_polyline_reach_print_snapshot(self):
        sheet = build_walking_sheet(self.extract, self.index, self.start, self.end)
        self.assertEqual(len(sheet.route.points), 3)
        self.assertGreater(sheet.route.distance_m, 200)
        self.assertAlmostEqual(sheet.route.duration_seconds, sheet.route.distance_m / 1.4)
        fields = sheet.as_selectable_item().fields
        self.assertIn("35.68", fields["polyline"])
        self.assertEqual(fields["basis"], NOW.isoformat())
        self.assertEqual(fields["departure_name"], "出発駅")
        self.assertIn("ODbL", fields["attribution"])
        self.assertNotIn("source_path", fields)
        snapshot = GenerationSnapshot.capture([sheet.as_selectable_item()], created_at=NOW, timezone_id="UTC")
        self.assertEqual(snapshot.items[0].category, "walking-map")
        with self.assertRaises(FrozenInstanceError):
            sheet.prefecture = 27

    def test_missing_endpoints_different_prefecture_or_source_are_explicit_failures(self):
        with self.assertRaises(RoutePreparationError) as caught:
            build_walking_sheet(self.extract, self.index, None, self.end)
        self.assertEqual(caught.exception.reason, "missing-endpoints")
        for attr, value in (("prefecture", 27), ("source_path", str(self.root / "another.osm")),
                            ("source_path", None)):
            original = getattr(self.index, attr)
            setattr(self.index, attr, value)
            with self.assertRaises(RoutePreparationError) as caught:
                build_walking_sheet(self.extract, self.index, self.start, self.end)
            self.assertEqual(caught.exception.reason, "source-mismatch")
            setattr(self.index, attr, original)

    def test_outside_prefecture_candidate_is_not_replaced_with_straight_line(self):
        outsider = Place("foreign/1", "Other", "city", 34.69, 135.5)
        with self.assertRaises(RoutePreparationError) as caught:
            build_walking_sheet(self.extract, self.index, self.start, outsider)
        self.assertEqual(caught.exception.reason, "outside-prefecture")

    def test_disconnected_graph_has_no_successful_print_model(self):
        with self.assertRaises(WalkingError) as caught:
            build_walking_sheet(self.extract, self.index, self.start, self.index.get("node/4"))
        self.assertEqual(caught.exception.reason, "disconnected")

    def test_equal_endpoints_have_zero_distance_and_one_real_point(self):
        sheet = build_walking_sheet(self.extract, self.index, self.start, self.start)
        self.assertEqual(sheet.route.distance_m, 0)
        self.assertEqual(len(sheet.route.points), 1)

    def test_current_location_snaps_to_road_without_invented_connector(self):
        current = Place("current-location", "Current", "current-location", 35.6801, 139.7601)
        sheet = build_walking_sheet(self.extract, self.index, current, self.end)
        self.assertGreater(sheet.route.start_snap_m, 0)
        self.assertNotEqual(sheet.route.points[0], (current.latitude, current.longitude))
        self.assertEqual(sheet.route.points[0], (35.68, 139.76))

    def test_absent_derived_data_fails_without_scanning_the_extract(self):
        derived_path(13, SHA, directory=self.root).unlink()
        with self.assertRaises(WalkingError) as caught:
            build_walking_sheet(self.extract, self.index, self.start, self.end)
        self.assertEqual(caught.exception.reason, "unavailable")

    def test_router_selection_honours_an_injected_slice(self):
        seen = {}
        def factory(bounds):
            seen["bounds"] = bounds
            return InjectedRouter()
        sheet = build_walking_sheet(self.extract, self.index, self.start, self.end,
                                    router_factory=factory)
        self.assertEqual(sheet.route.points, ((35.68, 139.76), (35.681, 139.76), (35.682, 139.76)))
        self.assertIsInstance(seen["bounds"], tuple)
        self.assertEqual(len(seen["bounds"]), 4)


class InjectedRouter:
    def route(self, *coordinates):
        return WalkingRoute(((35.68, 139.76), (35.681, 139.76), (35.682, 139.76)),
                            260.0, 260.0 / 1.4, 0.0, 0.0)


if __name__ == "__main__":
    unittest.main()
