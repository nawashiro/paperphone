"""版付き派生データの生成・版照合・範囲照会・取消・失敗分類を検証する。"""

from pathlib import Path
import io
import sqlite3
import tempfile
import unittest
import unittest.mock as mock
import xml.etree.ElementTree as ET

from paperphone.osm_derived import (_pack_paths, _unpack_paths, build_derived,
                                    derived_path, DerivedError, DerivedStore)
from paperphone.walking import WalkingError

SHA = "c" * 64


def walking_xml(nodes, ways, *, node_tags=None, relations=(), area_ways=()):
    root = ET.Element("osm", version="0.6")
    for node_id, (lat, lon) in nodes.items():
        node = ET.SubElement(root, "node", id=str(node_id), lat=str(lat), lon=str(lon), version="1")
        for key, value in (node_tags or {}).get(node_id, {}).items():
            ET.SubElement(node, "tag", k=key, v=value)
    for way_id, refs, tags in ways:
        way = ET.SubElement(root, "way", id=str(way_id), version="1")
        for ref in refs:
            ET.SubElement(way, "nd", ref=str(ref))
        for key, value in tags.items():
            ET.SubElement(way, "tag", k=key, v=value)
    for relation_id, (members, tags) in enumerate(relations, 1):
        relation = ET.SubElement(root, "relation", id=str(relation_id), version="1")
        for kind, ref, role in members:
            ET.SubElement(relation, "member", type=kind, ref=str(ref), role=role)
        for key, value in tags.items():
            ET.SubElement(relation, "tag", k=key, v=value)
    stream = io.StringIO()
    ET.ElementTree(root).write(stream, encoding="unicode", xml_declaration=True)
    return stream.getvalue()


class DerivedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, text, prefecture=13, sha256=SHA, cancelled=None, progress=None):
        source = self.root / f"fixture-{sha256[:4]}.osm"
        source.write_text(text, encoding="utf-8")
        return build_derived(source, prefecture, sha256, directory=self.root,
                             cancelled=cancelled, progress=progress)

    def open(self, prefecture=13, sha256=SHA):
        return DerivedStore.open(derived_path(prefecture, sha256, directory=self.root),
                                 sha256, prefecture)

    def test_pack_and_unpack_roundtrip(self):
        rings = (((0.0, 1.0), (1.0, 1.0), (1.5, 3.25)), ((0.1, 0.2),))
        self.assertEqual(_unpack_paths(_pack_paths(rings)), rings)

    def test_build_writes_versioned_derived_database(self):
        text = walking_xml({17: (35, 139), 19: (35, 139.001)},
                           [(1, [17, 19], {"highway": "footway"})])
        path = self.build(text)
        self.assertEqual(path, derived_path(13, SHA, directory=self.root))
        self.assertTrue(path.is_file())
        with sqlite3.connect(str(path)) as db:
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        self.assertEqual(meta["version"], "2")
        self.assertEqual(meta["prefecture"], "13")
        self.assertEqual(meta["sha256"], SHA)
        with self.open() as store:
            router = store.router((138, 34, 140, 36))
        self.assertEqual(sorted(router.route(35, 139, 35, 139.001).points),
                         [(35, 139), (35, 139.001)])

    def test_open_verifies_prefecture_and_version(self):
        text = walking_xml({17: (35, 139), 19: (35, 139.001)},
                           [(1, [17, 19], {"highway": "footway"})])
        self.build(text, sha256=SHA)
        self.assertIsNotNone(self.open(13, SHA))
        self.assertIsNone(self.open(27, SHA))
        self.assertIsNone(self.open(13, "d" * 64))
        self.assertIsNone(DerivedStore.open(self.root / "missing.sqlite", SHA, 13))
        broken = self.root / "broken.sqlite"
        broken.write_bytes(b"broken")
        self.assertIsNone(DerivedStore.open(broken, SHA, 13))

    def test_version_mismatch_opens_as_absent(self):
        text = walking_xml({17: (35, 139), 19: (35, 139.001)},
                           [(1, [17, 19], {"highway": "footway"})])
        path = self.build(text)
        with sqlite3.connect(str(path)) as db:
            db.execute("UPDATE meta SET value = '99' WHERE key = 'version'")
        self.assertIsNone(self.open())

    def map_features(self, store):
        from paperphone.map_rendering import project
        px0, py0 = project(34.999, 138.999)
        px1, py1 = project(35.001, 139.006)
        return store.map_features((px0, py0, px1, py1))

    def test_chains_same_class_ways_and_keeps_junctions_apart(self):
        from paperphone.map_rendering import project
        nodes = {1: (35, 139), 2: (35.0001, 139), 3: (35.0001, 139.0001), 4: (35, 139.0001),
                 5: (35.0002, 139.0001), 6: (35.0002, 139.0002)}
        ways = [(1, [1, 2], {"highway": "residential"}),
                (2, [2, 3], {"highway": "residential"}),
                (3, [3, 4], {"highway": "residential"}),
                (4, [5, 6], {"highway": "primary"}),
                (5, [6, 6, 5], {"highway": "service"})]
        self.build(walking_xml(nodes, ways))
        with self.open() as store:
            _, roads, _ = self.map_features(store)
        self.assertEqual(sorted(road_class for road_class, _ in roads),
                         ["primary", "residential", "service"])
        residential = next(points for kind, [points] in roads if kind == "residential")
        self.assertEqual(len(residential), 4)
        self.assertIn(project(35.0001, 139), residential)

    def test_chain_extends_through_shared_endpoint(self):
        from paperphone.map_rendering import project
        nodes = {1: (35, 139), 2: (35.0001, 139), 3: (35.0001, 139.0001)}
        ways = [(1, [2, 1], {"highway": "residential"}),
                (2, [2, 3], {"highway": "residential"})]
        self.build(walking_xml(nodes, ways))
        with self.open() as store:
            _, roads, _ = self.map_features(store)
        self.assertEqual(len(roads), 1)
        points = roads[0][1][0]
        self.assertEqual(points[0], project(35.0001, 139.0001))

    def test_chains_do_not_merge_at_branching_points(self):
        nodes = {1: (35, 139), 2: (35.0001, 139),
                 3: (35.0001, 139.0001), 4: (35, 139.0001)}
        ways = [(1, [1, 2], {"highway": "residential"}),
                (2, [2, 3], {"highway": "residential"}),
                (3, [2, 4], {"highway": "residential"})]
        self.build(walking_xml(nodes, ways))
        with self.open() as store:
            _, roads, _ = self.map_features(store)
        self.assertEqual(len(roads), 3)

    def test_closed_chains_store_one_record(self):
        nodes = {1: (35, 139), 2: (35.0001, 139), 3: (35.00005, 139.0001)}
        ways = [(1, [1, 2], {"highway": "residential"}),
                (2, [2, 3, 1], {"highway": "residential"})]
        self.build(walking_xml(nodes, ways))
        with self.open() as store:
            _, roads, _ = self.map_features(store)
        self.assertEqual(len(roads), 1)
        points = roads[0][1][0]
        self.assertEqual(points[0], points[-1])

    def test_simplify_discards_print_invisible_points(self):
        from paperphone.osm_derived import SIMPLIFY_TOLERANCE_M, _simplify_path
        from shapely.geometry import LineString, Point
        line = [(0.0, 0.0), (0.01, 0.0000001), (0.02, 0.0), (0.03, 0.0000002), (0.04, 0.0)]
        simplified = _simplify_path(line)
        self.assertLess(len(simplified), len(line))
        self.assertEqual(simplified[0], line[0])
        self.assertEqual(simplified[-1], line[-1])
        distance = max(Point(*point).distance(LineString(simplified)) for point in line)
        self.assertLessEqual(distance, SIMPLIFY_TOLERANCE_M)

    def test_invalid_simplification_keeps_original_geometry(self):
        from unittest.mock import patch
        from paperphone.osm_derived import _simplify_path
        source = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]
        broken = type("Broken", (), {"is_empty": False, "is_valid": False,
                                     "coords": ()})()
        with patch("shapely.geometry.LineString.simplify", return_value=broken):
            self.assertEqual(_simplify_path(source), source)

    def test_scan_pass_count_does_not_grow(self):
        import osmium as osmium_module
        original = osmium_module.SimpleHandler.apply_file
        calls = []

        def counting(self, *args, **kwargs):
            calls.append(args)
            return original(self, *args, **kwargs)

        text = walking_xml({1: (35, 139), 2: (35.0001, 139)},
                           [(1, [1, 2], {"highway": "footway"})])
        source = self.root / "fixture-count.osm"
        source.write_text(text, encoding="utf-8")
        with mock.patch.object(osmium_module.SimpleHandler, "apply_file", new=counting):
            build_derived(source, 13, "e" * 64, directory=self.root)
        self.assertEqual(len(calls), 3)

    def test_label_importance_follows_source_tags(self):
        values = ((1, {"place": "city", "name": "City"}),
                  (2, {"place": "hamlet", "name": "Hamlet"}),
                  (3, {"railway": "station", "name": "Station"}),
                  (4, {"amenity": "library", "name": "Library"}),
                  (5, {"tourism": "hotel", "name": "Hotel"}))
        nodes = {node_id: (35, 139 + node_id * 0.001) for node_id, _ in values}
        self.build(walking_xml(nodes, [], node_tags=dict(values)))
        with self.open() as store:
            _, _, labels = self.map_features(store)
        order = {name: index for index, (name, _, _) in enumerate(labels)}
        self.assertLess(order["City"], order["Station"])
        self.assertLess(order["Station"], order["Hamlet"])
        self.assertLess(order["Hamlet"], order["Library"])
        self.assertLess(order["Library"], order["Hotel"])

    def test_places_catalog_query_matches_saved_rows(self):
        text = walking_xml(
            {1: (35, 139)},
            [],
            node_tags={1: {"name": "桜駅", "railway": "station", "alt_name": "Sakura Station"}})
        self.build(text)
        with self.open() as store:
            index = store.place_index()
        self.assertEqual(index.prefecture, 13)
        self.assertIsNotNone(index.source_path)
        found, = index.search(" Sakura　Station ")
        self.assertEqual(found.id, "node/1")
        self.assertEqual(found.kind, "station")
        self.assertEqual(found.aliases, ("Sakura Station",))
        self.assertEqual(index.get("node/1"), found)

    def test_place_aliases_and_names_survive_storage(self):
        text = walking_xml(
            {5: (35.3, 139.3)},
            [],
            node_tags={5: {"name": "茅場", "name:en": "Cattail Café", "amenity": "cafe"}})
        self.build(text)
        with self.open() as store:
            index = store.place_index()
        cafe, = index.search("cattail")
        self.assertEqual(cafe.name, "茅場")

    def test_router_slice_filters_ways_outside_bounds(self):
        text = walking_xml(
            {1: (35, 139), 2: (35, 139.001), 3: (35.01, 139.002)},
            [(1, [1, 2, 3], {"highway": "footway"})])
        self.build(text)
        with self.open() as store:
            router = store.router((138.999, 34.999, 139.002, 35.002))
            self.assertEqual(router.route(*[35, 139], *[35, 139.001]).points,
                             ((35, 139), (35, 139.001)))
            with self.assertRaises(WalkingError) as caught:
                router.route(35, 139, 35.01, 139.002)
            self.assertEqual(caught.exception.reason, "outside-bounds")
            with self.assertRaises(WalkingError) as caught:
                store.router((140, 36, 140.1, 36.1)).route(36.05, 140.05, 36.05, 140.06)
            self.assertEqual(caught.exception.reason, "no-graph")

    def test_router_invalid_bounds_are_explicit(self):
        text = walking_xml({1: (35, 139)}, [(1, [1, 2], {"highway": "footway"})])
        self.build(text)
        store = self.open()
        with self.assertRaises(WalkingError) as caught:
            store.router("secret")
        self.assertEqual(caught.exception.reason, "invalid-bounds")
        store.close()

    def test_walk_graph_preserves_turn_restriction_exclusions(self):
        # 歩行者ターン制限のwayは派生データに現れない。無関係な区間は残る。
        members = (("way", 2, "from"), ("node", 3, "via"), ("way", 3, "to"))
        text = walking_xml(
            {1: (35, 139), 2: (35, 139.001), 3: (35, 139.002), 4: (35, 139.003),
             5: (35, 139.004)},
            [(1, [1, 2], {"highway": "footway"}), (2, [2, 3], {"highway": "footway"}),
             (3, [3, 4], {"highway": "footway"}), (4, [4, 5], {"highway": "footway"})],
            relations=[(members, {"type": "restriction", "restriction:foot": "no_left_turn"})])
        self.build(text)
        with self.open() as store:
            router = store.router((138, 34, 140, 36))
        with self.assertRaises(WalkingError) as caught:
            router.route(35, 139, 35, 139.004)
        self.assertEqual(caught.exception.reason, "disconnected")
        self.assertEqual(router.route(35, 139, 35, 139.001).points,
                         ((35, 139), (35, 139.001)))

    def test_cancelled_build_leaves_no_partial_state(self):
        text = walking_xml({1: (35, 139), 2: (35, 139.001)},
                           [(1, [1, 2], {"highway": "footway"})])
        with self.assertRaises(DerivedError) as caught:
            self.build(text, cancelled=lambda: True)
        self.assertEqual(caught.exception.reason, "cancelled")
        self.assertEqual(caught.exception.__cause__, None)
        self.assertFalse(list(self.root.glob("*.sqlite")))
        self.assertFalse(list(self.root.glob(".derived-*")))

    def test_cancelled_mid_scan_leaves_no_partial_state(self):
        text = walking_xml({1: (35, 139), 2: (35, 139.001)},
                           [(1, [1, 2], {"highway": "footway"})])
        source = self.root / "mid.osm"
        source.write_text(text, encoding="utf-8")
        counter = [0]

        def cancel_early():
            counter[0] += 1
            return counter[0] >= 2

        with self.assertRaises(DerivedError) as caught:
            build_derived(source, 13, SHA, directory=self.root, cancelled=cancel_early)
        self.assertEqual(caught.exception.reason, "cancelled")
        self.assertFalse(list(self.root.glob("*.sqlite")))
        self.assertFalse(list(self.root.glob(".derived-*")))

    def test_progress_reports_once_per_phase(self):
        text = walking_xml({1: (35, 139), 2: (35, 139.001)},
                           [(1, [1, 2], {"highway": "footway"})])
        seen = []
        path_ = self.build(text, progress=seen.append)
        self.assertLessEqual(seen[0], 0.33)
        self.assertLess(seen[0], seen[-1])
        self.assertEqual(seen[-1], 1.0)
        self.assertTrue(path_.is_file())

    def test_scan_failures_are_sanitized(self):
        text = b"not an osm file"
        source = self.root / "broken.osm"
        source.write_bytes(text)
        with self.assertRaises(DerivedError) as caught:
            build_derived(source, 13, SHA, directory=self.root)
        self.assertEqual(caught.exception.reason, "invalid-data")
        self.assertNotIn("broken", str(caught.exception))
        self.assertFalse(list(self.root.glob("*.sqlite")))
        self.assertFalse(list(self.root.glob(".derived-*")))

    def test_missing_extract_dependency_is_unavailable(self):
        text = walking_xml({1: (35, 139)}, [(1, [1, 2], {"highway": "footway"})])
        source = self.root / "dep.osm"
        source.write_text(text, encoding="utf-8")
        import paperphone.osm_derived as derived_module
        with mock.patch.object(derived_module, "_scan_pedestrian",
                               side_effect=ImportError("osmium")):
            with self.assertRaises(DerivedError) as caught:
                build_derived(source, 13, SHA, directory=self.root)
        self.assertEqual(caught.exception.reason, "unavailable")

    def test_invalid_arguments_never_touch_the_filesystem(self):
        for prefecture, sha256 in ((0, SHA), (48, SHA), ("13", SHA), (13, "")):
            with self.subTest(prefecture=prefecture, sha256=sha256), \
                    self.assertRaises(DerivedError):
                derived_path(prefecture, sha256, directory=self.root)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIsNone(DerivedStore.open(self.root / "x.sqlite", SHA, True))
        self.assertIsNone(DerivedStore.open(self.root / "x.sqlite", SHA, 0))

    def test_rebuild_replaces_stale_versions_atomically(self):
        text = walking_xml({1: (35, 139), 2: (35, 139.001)},
                           [(1, [1, 2], {"highway": "footway"})])
        self.build(text, sha256=SHA)
        old_file = derived_path(13, SHA, directory=self.root)
        self.build(text, sha256="d" * 64)
        self.assertFalse(old_file.is_file())
        self.assertTrue(derived_path(13, "d" * 64, directory=self.root).is_file())


if __name__ == "__main__":
    unittest.main()
