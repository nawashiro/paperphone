"""版付きの徒歩グラフ保存・読込・スライスを検証。"""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

import numpy

from paperphone.walking import WalkingRouter, WalkingError
from paperphone.walking_store import (build_full_graph, graph_path, load_graph,
                                      save_graph, _router_from_arrays)

NODES = {1: (35.0, 139.0), 2: (35.0, 139.001), 3: (35.001, 139.001)}
EDGES = [(1, 2), (2, 3), (3, 2)]
SHA = "c" * 64


class WalkingStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_roundtrip_preserves_graph_arrays(self):
        node_ids = numpy.asarray(sorted(NODES), dtype=numpy.int64)
        coordinates = numpy.asarray([NODES[value] for value in sorted(NODES)], dtype=numpy.float64)
        path = graph_path(13, SHA, directory=self.root)
        self.assertTrue(save_graph(path, SHA, 13, node_ids, coordinates, numpy.asarray(EDGES)))
        stored = load_graph(path, SHA, 13)
        self.assertIsNotNone(stored)
        ids, coords, edges = stored
        self.assertEqual(ids.tolist(), node_ids.tolist())
        self.assertEqual(edges.tolist(), numpy.asarray(EDGES).tolist())
        # 範囲は(west, south, east, north)の順。
        router = _router_from_arrays(ids, coords, edges, (139.0, 35.0, 139.02, 35.02))
        route = router.route(35.0, 139.0, 35.001, 139.001)
        self.assertEqual(route.points, (NODES[1], NODES[2], NODES[3]))

    def test_outside_bounds_rejects_route_from_slice(self):
        node_ids = numpy.asarray(sorted(NODES), dtype=numpy.int64)
        coordinates = numpy.asarray([NODES[value] for value in sorted(NODES)], dtype=numpy.float64)
        with self.assertRaises(WalkingError) as caught:
            _router_from_arrays(node_ids, coordinates, numpy.asarray(EDGES), (140.0, 36.0, 140.1, 36.1))
        self.assertEqual(caught.exception.reason.replace("no-graph", "no-graph"), caught.exception.reason)

    def test_rejects_mismatched_prefecture(self):
        node_ids = numpy.asarray(sorted(NODES), dtype=numpy.int64)
        coordinates = numpy.asarray([NODES[value] for value in sorted(NODES)], dtype=numpy.float64)
        path = graph_path(13, SHA, directory=self.root)
        save_graph(path, SHA, 13, node_ids, coordinates, numpy.asarray(EDGES))
        self.assertIsNone(load_graph(path, SHA, 27))

    def test_rejects_corrupted_file(self):
        path = graph_path(13, SHA, directory=self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"broken")
        self.assertIsNone(load_graph(path, SHA, 13))

    def test_full_graph_build_and_save_roundtrip_on_synthetic_xml(self):
        fixture = Path(__file__).parent / "fixtures" / "osm-places.osm"
        from unittest.mock import patch
        from paperphone import walking_store
        with patch.object(walking_store, "_scan_pedestrian", wraps=walking_store._scan_pedestrian):
            # 索引の合成データはタグの名前がない。代替として小さなXMLをその場で作る。
            import xml.etree.ElementTree as ET
            root = ET.Element("osm", version="0.6")
            for node_id, (lat, lon) in NODES.items():
                node = ET.SubElement(root, "node", id=str(node_id), lat=str(lat), lon=str(lon), version="1")
                ET.SubElement(node, "tag", k="highway", v="footway")
            way = ET.SubElement(root, "way", id="1", version="1")
            for ref in (1, 2, 3):
                ET.SubElement(way, "nd", ref=str(ref))
            ET.SubElement(way, "tag", k="highway", v="footway")
            ET.ElementTree(root).write(self.root / "synthetic.osm", encoding="utf-8")
            nodes, edges = build_full_graph(self.root / "synthetic.osm")
            self.assertEqual(set(nodes), set(NODES))
            # footwayは両方向に通れる。
            path = graph_path(13, SHA, directory=self.root)
            self.assertTrue(save_graph(path, SHA, 13,
                                       numpy.asarray(sorted(NODES), dtype=numpy.int64),
                                       numpy.asarray([NODES[i] for i in sorted(NODES)], dtype=numpy.float64),
                                       numpy.asarray(sorted(set(edges)), dtype=numpy.int64)))
            self.assertIsNotNone(load_graph(path, SHA, 13))


    def test_saved_graph_slice_avoids_pbf_scan(self):
        """保存済みグラフを使う経路はWalkingRouter.from_pbfを呼ばない。"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from paperphone import walking_sheet, walking
        node_ids = numpy.asarray(sorted(NODES), dtype=numpy.int64)
        coords = numpy.asarray([NODES[v] for v in sorted(NODES)], dtype=numpy.float64)
        save_graph(graph_path(13, SHA, directory=self.root), SHA, 13,
                   node_ids, coords,
                   numpy.asarray([(1, 2), (2, 1), (2, 3), (3, 2)], dtype=numpy.int64))
        extract = SimpleNamespace(path=self.root / "x.osm.pbf", prefecture=13, sha256=SHA)
        original = walking.WalkingRouter.from_pbf
        with patch.object(walking.WalkingRouter, "from_pbf",
                          side_effect=AssertionError("PBF走査を実行しなかった")):
            router = walking_sheet._router_from_graph_cache(extract, (139.0, 35.0, 139.02, 35.02))
        self.assertIsNotNone(router)


    def test_full_graph_helper_in_walking_sheet_saves_and_serves(self):
        """起動時の実手順でも旧走査なしで経路が作れることを pin する。"""
        import xml.etree.ElementTree as ET
        from unittest.mock import patch
        from paperphone import walking_sheet
        root_xml = ET.Element("osm", version="0.6")
        for node_id, (lat, lon) in NODES.items():
            node = ET.SubElement(root_xml, "node", id=str(node_id), lat=str(lat), lon=str(lon), version="1")
            ET.SubElement(node, "tag", k="highway", v="footway")
        way = ET.SubElement(root_xml, "way", id="1", version="1")
        for ref in (1, 2, 3):
            ET.SubElement(way, "nd", ref=str(ref))
        ET.SubElement(way, "tag", k="highway", v="footway")
        path = self.root / "synthetic.osm"
        ET.ElementTree(root_xml).write(path, encoding="utf-8", xml_declaration=True)
        from types import SimpleNamespace
        from datetime import datetime, timezone
        extract = SimpleNamespace(prefecture=13, sha256=SHA, path=path,
                                  basis=datetime.now(timezone.utc))
        first = walking_sheet._router_from_full_graph(extract, (139.0, 35.0, 139.02, 35.02))
        self.assertIsNotNone(first)
        self.assertTrue(walking_sheet.graph_path(13, SHA, directory=self.root).is_file())
        path.unlink()   # PBFが無くても、2度目はキャッシュだけで経路を作る。
        second = walking_sheet._router_from_graph_cache(extract, (139.0, 35.0, 139.02, 35.02))
        self.assertIsNotNone(second)
        self.assertEqual(second.route(35.0, 139.0, 35.001, 139.001).distance_m,
                         first.route(35.0, 139.0, 35.001, 139.001).distance_m)

if __name__ == "__main__":
    unittest.main()
