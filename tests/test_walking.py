"""版付き派生データ上の通行権と実ノード接続を、合成OSMだけで検証する。"""

from dataclasses import FrozenInstanceError
from pathlib import Path
import math
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from paperphone.osm_derived import build_derived, derived_path, DerivedError, DerivedStore
from paperphone.walking import WalkingError, WalkingRoute, WalkingRouter

SHA = "c" * 64


class WalkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "walking.osm"

    def osm(self, nodes, ways, *, node_tags=None, relations=(), **kwargs):
        root = ET.Element("osm", version="0.6")
        for node_id, (lat, lon) in nodes.items():
            node = ET.SubElement(root, "node", id=str(node_id), lat=str(lat), lon=str(lon), version="1")
            for key, value in (node_tags or {}).get(node_id, {}).items():
                ET.SubElement(node, "tag", k=key, v=value)
        for way_id, (refs, tags) in enumerate(ways, 1):
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
        ET.ElementTree(root).write(self.path, encoding="utf-8", xml_declaration=True)
        bounds = kwargs.pop("bounds", None)
        max_snap_m = kwargs.pop("max_snap_m", 100.0)
        return self.rediscover(bounds, max_snap_m)

    def rediscover(self, bounds=None, max_snap_m=100.0):
        """書き出した版を派生データ経由で経路可能にする。"""
        build_derived(self.path, 13, SHA, directory=self.root)
        store = DerivedStore.open(derived_path(13, SHA, directory=self.root), SHA, 13)
        self.addCleanup(store.close)
        return store.router(bounds, max_snap_m=max_snap_m)

    def assert_reason(self, reason, callback, *args, **kwargs):
        with self.assertRaises(Exception) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception

    def test_shortest_path_preserves_intermediate_nodes_and_lengths(self):
        nodes = {1: (35, 139), 2: (35, 139.001), 3: (35, 139.002), 4: (35.002, 139.001)}
        router = self.osm(nodes, [([1, 2, 3], {"highway": "footway"}),
                                  ([1, 4, 3], {"highway": "residential"})])
        route = router.route(*nodes[1], *nodes[3])
        self.assertEqual(route.points, (nodes[1], nodes[2], nodes[3]))
        self.assertAlmostEqual(route.distance_m, 182.171, delta=0.1)
        self.assertAlmostEqual(route.duration_seconds, route.distance_m / 1.4)
        self.assertEqual((route.start_snap_m, route.end_snap_m), (0, 0))
        reverse = router.route(*nodes[3], *nodes[1])
        self.assertEqual(reverse.points, tuple(reversed(route.points)))

    def test_distance_is_polyline_not_endpoint_chord(self):
        nodes = {1: (0, 0), 2: (0.001, 0), 3: (0.001, 0.001)}
        route = self.osm(nodes, [([1, 2, 3], {"highway": "steps"})]).route(*nodes[1], *nodes[3])
        self.assertAlmostEqual(route.distance_m, 222.39, delta=0.1)
        self.assertEqual(len(route.points), 3)

    def test_constructor_directed_shortest_path_and_defensive_copy(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (.001, 0)}
        router = WalkingRouter(nodes, [(1, 2), (1, 3), (3, 2)])
        nodes[2] = (10, 10)
        self.assertEqual(router.route(0, 0, 0, .001).points, ((0, 0), (0, .001)))
        with self.assertRaises(WalkingError) as caught:
            router.route(0, .001, 0, 0)
        self.assertEqual(caught.exception.reason, "disconnected")

    def test_equal_endpoints_require_graph_and_snap(self):
        router = WalkingRouter({1: (0, 0), 2: (0, .001)}, [(1, 2)])
        route = router.route(0, .00001, 0, .00001)
        self.assertEqual(route.points, ((0, 0),))
        self.assertEqual(route.distance_m, 0)
        self.assertEqual(route.duration_seconds, 0)
        self.assertGreater(route.start_snap_m, 1)
        self.assertEqual(route.start_snap_m, route.end_snap_m)
        self.assert_reason("no-nearby-road", router.route, 1, 1, 1, 1)
        self.assert_reason("no-graph", WalkingRouter({}, []).route, 0, 0, 0, 0)

    def test_immutable_result_does_not_hold_mutable_points(self):
        points = [[0, 0]]
        route = WalkingRoute(points, 0, 0, 0, 0)
        points[0][0] = 1
        self.assertEqual(route.points, ((0, 0),))
        with self.assertRaises(FrozenInstanceError):
            route.distance_m = 4

    def test_disconnected_is_distinct_from_absent_graph_or_nearby_node(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (0, .01), 4: (0, .011)}
        router = WalkingRouter(nodes, [(1, 2), (3, 4)])
        self.assert_reason("disconnected", router.route, *nodes[1], *nodes[4])
        self.assert_reason("no-nearby-road", router.route, 10, 10, *nodes[4])

    def test_max_snap_is_bounded_and_connectors_are_not_output(self):
        router = WalkingRouter({1: (0, 0), 2: (0, .01)}, [(1, 2)], max_snap_m=20)
        route = router.route(.0001, 0, .0001, .01)
        self.assertEqual(route.points, ((0, 0), (0, .01)))
        self.assertAlmostEqual(route.start_snap_m, 11.1195, places=3)
        self.assertAlmostEqual(route.end_snap_m, 11.1195, places=3)
        self.assertAlmostEqual(route.distance_m, 1111.9508, places=3)
        # ノードへのスナップであり、長区間の中間へ無制限に吸着しない。
        self.assert_reason("no-nearby-road", router.route, 0, .005, 0, .01)

    def test_default_snap_radius_is_explicit_100_metres(self):
        router = WalkingRouter({1: (0, 0), 2: (0, .01)}, [(1, 2)])
        self.assertEqual(router.max_snap_m, 100)
        self.assert_reason("no-nearby-road", router.route, .001, 0, 0, .01)

    def test_partial_bounds_do_not_join_across_outside_node(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (.01, .002),
                 4: (0, .003), 5: (0, .004)}
        router = self.osm(nodes, [([1, 2, 3, 4, 5], {"highway": "footway"})],
                          bounds=(-.001, -.001, .005, .001))
        self.assert_reason("disconnected", router.route, *nodes[1], *nodes[5])
        self.assert_reason("outside-bounds", router.route, *nodes[1], *nodes[3])
        self.assertEqual(router.route(*nodes[1], *nodes[2]).points, (nodes[1], nodes[2]))

    def test_missing_way_node_does_not_create_a_shortcut(self):
        nodes = {1: (0, 0), 2: (0, .001), 4: (0, .003), 5: (0, .004)}
        router = self.osm(nodes, [([1, 2, 999, 4, 5], {"highway": "path"})])
        self.assert_reason("disconnected", router.route, *nodes[1], *nodes[5])

    def test_bridge_tunnel_geometric_crossing_is_not_a_junction(self):
        nodes = {1: (0, -.001), 2: (0, 0), 3: (0, .001),
                 4: (-.001, 0), 5: (0, 0), 6: (.001, 0)}
        router = self.osm(nodes, [([1, 2, 3], {"highway": "footway", "bridge": "yes"}),
                                  ([4, 5, 6], {"highway": "footway", "tunnel": "yes"})])
        self.assert_reason("disconnected", router.route, *nodes[1], *nodes[6])
        router = self.osm(nodes, [([1, 2, 3], {"highway": "footway"}),
                                  ([4, 2, 6], {"highway": "footway"})])
        self.assertEqual(router.route(*nodes[1], *nodes[6]).points,
                         (nodes[1], nodes[2], nodes[6]))

    def test_pedestrian_access_table(self):
        allowed = [{"highway": highway} for highway in
                   ("footway", "path", "steps", "pedestrian", "residential", "service", "track")]
        allowed += [{"highway": "footway", "access": "private", "foot": "yes"},
                    {"highway": "cycleway", "foot": "designated"},
                    {"highway": "motorway", "foot": "yes"},
                    {"highway": "residential", "access": "permissive"}]
        excluded = [{"highway": highway} for highway in
                    ("motorway", "motorway_link", "trunk", "construction", "proposed", "cycleway", "bridleway", "road")]
        excluded += [{"highway": "footway", key: value} for key, value in
                     (("foot", "no"), ("foot", "private"), ("foot", "use_sidepath"),
                      ("access", "private"), ("access", "customers"), ("access", "destination"),
                      ("access", "unknown"), ("smoothness", "impassable"), ("impassable", "yes"),
                      ("area", "yes"), ("foot:conditional", "yes @ (Mo-Fr)"),
                      ("access:conditional", "no @ (sunset-sunrise)"), ("motorroad", "yes"))]
        excluded += [{"highway": "motorway", "foot": "no"},
                     {"highway": "footway", "foot": "no", "access": "yes"}]
        for tags in allowed + excluded:
            with self.subTest(tags=tags):
                router = self.osm({1: (0, 0), 2: (0, .001)}, [([1, 2], tags)])
                if tags in allowed:
                    self.assertGreater(router.route(0, 0, 0, .001).distance_m, 0)
                else:
                    self.assert_reason("no-graph", router.route, 0, 0, 0, .001)

    def test_car_oneway_and_roundabout_do_not_restrict_walking(self):
        for tags in ({"oneway": "yes"}, {"oneway": "-1"}, {"junction": "roundabout"}):
            router = self.osm({1: (0, 0), 2: (0, .001)},
                              [([1, 2], {"highway": "residential", **tags})])
            self.assertGreater(router.route(0, .001, 0, 0).distance_m, 0)

    def test_foot_direction_aliases_and_forward_backward_access(self):
        for key in ("oneway:foot", "foot:oneway"):
            for value in ("yes", "-1", "no"):
                with self.subTest(key=key, value=value):
                    router = self.osm({1: (0, 0), 2: (0, .001)},
                                      [([1, 2], {"highway": "steps", key: value})])
                    if value == "-1":
                        self.assert_reason("disconnected", router.route, 0, 0, 0, .001)
                    else:
                        self.assertGreater(router.route(0, 0, 0, .001).distance_m, 0)
                    if value == "yes":
                        self.assert_reason("disconnected", router.route, 0, .001, 0, 0)
                    else:
                        self.assertGreater(router.route(0, .001, 0, 0).distance_m, 0)
        router = self.osm({1: (0, 0), 2: (0, .001)},
                          [([1, 2], {"highway": "footway", "foot:forward": "private"})])
        self.assert_reason("disconnected", router.route, 0, 0, 0, .001)
        self.assertGreater(router.route(0, .001, 0, 0).distance_m, 0)

    def test_unresolved_or_conflicting_foot_direction_is_not_unrestricted(self):
        for tags in ({"oneway:foot": "reversible"},
                     {"oneway:foot": "yes", "foot:oneway": "-1"},
                     {"oneway:foot:conditional": "yes @ (Mo-Fr)"},
                     {"foot:backward:conditional": "no @ (Mo-Fr)"}):
            router = self.osm({1: (0, 0), 2: (0, .001)},
                              [([1, 2], {"highway": "footway", **tags})])
            self.assert_reason("no-graph", router.route, 0, 0, 0, .001)

    def test_barrier_and_crossing_node_table(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (0, .002), 4: (0, .003), 5: (0, .004)}
        allowed = [{"barrier": "bollard"}, {"barrier": "cycle_barrier"},
                   {"barrier": "gate", "foot": "yes", "access": "private"},
                   {"highway": "crossing", "crossing": "uncontrolled"}]
        blocked = [{"barrier": "wall"}, {"barrier": "fence", "foot": "yes"},
                   {"barrier": "gate"}, {"barrier": "unknown"},
                   {"barrier": "gate", "foot": "yes", "locked": "yes"},
                   {"barrier": "bollard", "foot": "private"},
                   {"highway": "crossing", "crossing": "no"},
                   {"foot:conditional": "no @ (night)"}]
        for tags in allowed + blocked:
            with self.subTest(tags=tags):
                router = self.osm(nodes, [([1, 2, 3, 4, 5], {"highway": "footway"})],
                                  node_tags={3: tags})
                if tags in allowed:
                    self.assertEqual(len(router.route(*nodes[1], *nodes[5]).points), 5)
                else:
                    self.assert_reason("disconnected", router.route, *nodes[1], *nodes[5])

    def test_generic_car_turn_restrictions_do_not_disable_foot(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (.001, .001)}
        ways = [([1, 2], {"highway": "footway"}), ([2, 3], {"highway": "footway"})]
        members = [("way", 1, "from"), ("node", 2, "via"), ("way", 2, "to")]
        for tags in ({"type": "restriction", "restriction": "no_left_turn"},
                     {"type": "restriction", "restriction:conditional": "no_left_turn @ (Mo-Fr)"},
                     {"type": "restriction", "restriction": "no_left_turn", "except": "foot"}):
            with self.subTest(tags=tags):
                router = self.osm(nodes, ways, relations=[(members, tags)])
                self.assertEqual(router.route(*nodes[1], *nodes[3]).points,
                                 (nodes[1], nodes[2], nodes[3]))

    def test_pedestrian_turn_restrictions_remove_whole_member_ways(self):
        # 先端の短い非制限wayを残し、スナップ距離で制限wayを飛び越えられないようにする。
        nodes = {1: (0, 0), 2: (0, .001), 3: (0, .004), 4: (.003, .004), 5: (.004, .004)}
        ways = [([1, 2], {"highway": "footway"}), ([2, 3], {"highway": "footway"}),
                ([3, 4], {"highway": "footway"}), ([4, 5], {"highway": "footway"})]
        members = [("way", 2, "from"), ("node", 3, "via"), ("way", 3, "to")]
        tags_list = [{"type": "restriction", "restriction:foot": "no_left_turn"},
                     {"type": "restriction", "restriction:foot": "only_straight_on"},
                     {"type": "restriction", "restriction:foot:conditional": "no_left_turn @ (Mo-Fr)"},
                     {"type": "restriction:foot", "restriction": "no_left_turn"},
                     {"type": "restriction:foot", "restriction:conditional": "no_left_turn @ (Mo-Fr)"}]
        for tags in tags_list:
            with self.subTest(tags=tags):
                router = self.osm(nodes, ways, relations=[(members, tags)])
                self.assert_reason("disconnected", router.route, *nodes[1], *nodes[5])
                self.assert_reason("disconnected", router.route, *nodes[5], *nodes[1])
                self.assertEqual(router.route(*nodes[1], *nodes[2]).points, (nodes[1], nodes[2]))
                self.assert_reason("no-nearby-road", router.route, *nodes[3], *nodes[5])

    def test_via_way_pedestrian_restriction_is_also_removed(self):
        nodes = {i: (0, i * .002) for i in range(1, 7)}
        ways = [([i, i + 1], {"highway": "footway"}) for i in range(1, 6)]
        members = [("way", 2, "from"), ("way", 3, "via"), ("way", 4, "to")]
        router = self.osm(nodes, ways, relations=[(members, {
            "type": "restriction", "restriction:foot": "no_straight_on"})])
        self.assert_reason("disconnected", router.route, *nodes[1], *nodes[6])
        self.assert_reason("no-nearby-road", router.route, *nodes[3], *nodes[6])
        self.assert_reason("no-nearby-road", router.route, *nodes[4], *nodes[6])

    def test_malformed_or_missing_pedestrian_relation_members_fail_closed(self):
        nodes = {1: (0, 0), 2: (0, .001), 3: (0, .002)}
        ways = [([1, 2], {"highway": "footway"}), ([2, 3], {"highway": "footway"})]
        valid = [("way", 1, "from"), ("node", 2, "via"), ("way", 2, "to")]
        malformed = [[], valid[:-1], valid[1:],
                     [("way", 1, "from"), ("way", 2, "to")],
                     [("node", 1, "from"), ("node", 2, "via"), ("way", 2, "to")],
                     [("way", 999, "from"), ("node", 2, "via"), ("way", 2, "to")],
                     [("way", 1, "from"), ("node", 999, "via"), ("way", 2, "to")],
                     [("way", 1, "from"), ("way", 999, "via"), ("way", 2, "to")],
                     valid + [("relation", 999, "via")],
                     valid + [("node", 3, "via")],
                     valid + [("way", 2, "unknown")]]
        for members in malformed:
            with self.subTest(members=members):
                self.assert_reason("invalid-data", self.osm, nodes, ways, relations=[(members, {
                    "type": "restriction", "restriction:foot": "no_left_turn"})])
        for tags in ({"type": "restriction", "restriction:foot": ""},
                     {"type": "route", "restriction:foot": "no_left_turn"}):
            self.assert_reason("invalid-data", self.osm, nodes, ways, relations=[(valid, tags)])

    def test_pedestrian_relation_restriction_survives_pbf_roundtrip(self):
        import osmium
        nodes = {1: (0, 0), 2: (0, .001), 3: (0, .002)}
        ways = [([1, 2], {"highway": "footway"}), ([2, 3], {"highway": "footway"})]
        self.osm(nodes, ways, relations=[([
            ("way", 1, "from"), ("node", 2, "via"), ("way", 2, "to")], {
                "type": "restriction", "restriction:foot": "no_straight_on"})])
        pbf = self.root / "restricted.osm.pbf"
        with osmium.SimpleWriter(str(pbf)) as writer:
            class Copy(osmium.SimpleHandler):
                def node(self, node):
                    writer.add_node(node)

                def way(self, way):
                    writer.add_way(way)

                def relation(self, relation):
                    writer.add_relation(relation)
            Copy().apply_file(str(self.path))
        self.path = pbf
        router = self.rediscover()
        self.assert_reason("no-graph", router.route, *nodes[1], *nodes[3])

    def test_actual_pbf_parsing_matches_xml(self):
        import osmium
        nodes = {1: (35, 139), 2: (35, 139.001), 3: (35.001, 139.001)}
        xml_router = self.osm(nodes, [([1, 2, 3], {"highway": "steps"})])
        xml_route = xml_router.route(*nodes[1], *nodes[3])
        pbf = self.root / "walking.osm.pbf"
        with osmium.SimpleWriter(str(pbf)) as writer:
            class Copy(osmium.SimpleHandler):
                def node(self, node):
                    writer.add_node(node)

                def way(self, way):
                    writer.add_way(way)
            Copy().apply_file(str(self.path))
        self.path = pbf
        router = self.rediscover(bounds=(138, 34, 140, 36))
        self.assertEqual(router.route(*nodes[1], *nodes[3]), xml_route)

    def test_generation_and_routing_stay_offline_without_location_index(self):
        self.osm({1: (0, 0), 2: (0, .001)}, [([1, 2], {"highway": "footway"})])
        import osmium
        with patch("socket.socket", side_effect=AssertionError("network")), \
                patch("logging.Logger._log", side_effect=AssertionError("logging")), \
                patch.object(osmium.index, "create_map",
                             side_effect=AssertionError("location index")):
            router = self.rediscover()
            self.assertGreater(router.route(0, 0, 0, .001).distance_m, 0)

    def test_malformed_input_errors_are_sanitized_at_generation(self):
        self.path.write_text("<osm>secret-private-coordinate-not-xml", encoding="utf-8")
        error = self.assert_reason("invalid-data", self.rediscover)
        self.assertNotIn(str(self.path), str(error))
        self.assertNotIn("secret", repr(error))
        self.assertEqual(str(error), "invalid-data")
        self.path.parent.joinpath("empty.osm").write_bytes(b"")
        missing = self.root / "missing.osm"
        with self.assertRaises(DerivedError) as caught:
            build_derived(missing, 13, SHA, directory=self.root)
        self.assertEqual(caught.exception.reason, "invalid-data")

    def test_invalid_input_bounds_graph_and_snap_parameters(self):
        router = WalkingRouter({1: (0, 0), 2: (0, .001)}, [(1, 2)])
        for value in (math.nan, math.inf, -math.inf, "secret", None, True, 10 ** 1000):
            self.assert_reason("invalid-input", router.route, value, 0, 0, .001)
        for point in ((91, 0), (-91, 0), (0, 181), (0, -181)):
            self.assert_reason("invalid-input", router.route, *point, 0, .001)
        self.path.write_text('<osm version="0.6"><node id="1" lat="0" lon="0" version="1"/></osm>',
                             encoding="utf-8")
        for bounds in ((0, 0, 0, 1), (2, 0, 1, 1), (0, 0, 1), (0, 0, 1, math.nan),
                       (0, 0, 181, 1), "secret"):
            self.assert_reason("invalid-bounds", self.rediscover, bounds=bounds)
        for radius in (0, -1, math.inf, math.nan, "secret", True, 10 ** 1000):
            self.assert_reason("invalid-input", WalkingRouter, {}, [], max_snap_m=radius)
        self.assert_reason("invalid-data", WalkingRouter, {1: (0, 0)}, [(1, 2)])
        self.assert_reason("invalid-data", WalkingRouter, {1: (math.nan, 0)}, [])


if __name__ == "__main__":
    unittest.main()
