"""版付き派生データ上の検索・幾何・エラー秘匿を検証する。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import LineString, Point, Polygon

from paperphone.osm_derived import build_derived, derived_path
from paperphone.places import Place, PlaceIndex, PlaceIndexError

FIXTURE = Path(__file__).parent / "fixtures" / "osm-places.osm"
SHA = "f" * 64


def _pbf_copy(target):
    import osmium
    with osmium.SimpleWriter(str(target)) as writer:
        class Copy(osmium.SimpleHandler):
            def node(self, obj):
                writer.add_node(obj)

            def way(self, obj):
                writer.add_way(obj)

            def relation(self, obj):
                writer.add_relation(obj)

        Copy().apply_file(str(FIXTURE))


class PlaceIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.temp = Path(cls.directory.name)
        build_derived(FIXTURE, 13, SHA, directory=cls.temp)
        cls.index = PlaceIndex.from_derived(derived_path(13, SHA, directory=cls.temp), SHA, 13)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_names_and_kana_variants(self):
        for query in ("さくら駅", "ＳＡＫＵＲＡ ＳＴＡＴＩＯＮ", "sakura international",
                      "桜停車場", "cherry stop", "サクラエキ", "さくらえき", "ｻｸﾗｴｷ"):
            with self.subTest(query=query):
                self.assertEqual(self.index.search(query)[0].id, "node/1")
        self.assertEqual(self.index.search("English Cafe")[0].name, "English Cafe")

    def test_partial_fuzzy_no_results_and_limits(self):
        self.assertEqual(self.index.search("Courtyard")[0].id, "relation/30")
        self.assertEqual(self.index.search("Sakra Station")[0].id, "node/1")
        self.assertEqual(self.index.search("絶対に存在しない地名"), ())
        self.assertEqual(self.index.search(""), ())
        self.assertEqual(self.index.search("　 \n"), ())
        self.assertEqual(self.index.search("桜駅", limit=0), ())
        self.assertEqual(len(self.index.search("桜駅", limit=1)), 1)
        self.assertIsInstance(self.index.search("桜駅"), tuple)

    def test_same_names_remain_distinct(self):
        places = self.index.search("桜駅")
        self.assertEqual({place.id for place in places}, {"node/1", "node/2"})
        self.assertNotEqual(places[0].latitude, places[1].latitude)
        self.assertEqual(places[0].kind, "station")
        self.assertEqual(self.index.prefecture, 13)

    def test_way_midpoint_lies_on_line_not_bbox_centre(self):
        place, = self.index.search("曲がり道")
        self.assertEqual(place.id, "way/10")
        self.assertEqual(place.kind, "footway")
        line = LineString([(139, 35), (139.04, 35), (139.04, 35.04)])
        self.assertLess(line.distance(Point(place.longitude, place.latitude)), 1e-10)
        self.assertNotEqual((place.longitude, place.latitude), (139.02, 35.02))

    def test_closed_way_area_and_relation_hole(self):
        place, = self.index.search("四角公園")
        self.assertEqual(place.id, "way/20")
        self.assertEqual(place.kind, "park")
        self.assertTrue(Polygon([(139, 36), (139.04, 36), (139.04, 36.04), (139, 36.04)])
                        .contains(Point(place.longitude, place.latitude)))
        place, = self.index.search("穴のある公園")
        self.assertEqual(place.id, "relation/30")
        area = Polygon([(139, 37), (139.06, 37), (139.06, 37.06), (139, 37.06)],
                       [[(139.01, 37.01), (139.05, 37.01), (139.05, 37.05), (139.01, 37.05)]])
        self.assertTrue(area.contains(Point(place.longitude, place.latitude)))

    def test_closed_lines_are_not_replaced_with_polygon_interiors(self):
        ring = LineString([(139, 36), (139.04, 36), (139.04, 36.04), (139, 36.04), (139, 36)])
        for query in ("環状歩道", "面ではない公園"):
            place = self.index.search(query)[0]
            self.assertLess(ring.distance(Point(place.longitude, place.latitude)), 1e-10)

    def test_get_verifies_index_membership(self):
        place = self.index.search("桜駅")[0]
        self.assertIs(self.index.get(place.id), place)
        self.assertIsNone(self.index.get("node/999999"))

    def test_invalid_geometries_are_not_fabricated(self):
        self.assertEqual(self.index.search("Invalid bowtie"), ())
        self.assertEqual(self.index.search("Missing geometry"), ())

    def test_immutable_place_and_iterable_constructor(self):
        aliases = ["alias"]
        place = Place("node/7", "Example", "cafe", 35, 139, aliases)
        aliases.append("changed")
        self.assertEqual(place.aliases, ("alias",))
        with self.assertRaises(Exception):
            place.name = "changed"
        index = PlaceIndex(iter([place]))
        self.assertEqual(index.search("alias"), (place,))
        self.assertIsNone(index.prefecture)

    def test_invalid_coordinates_and_empty_values_rejected(self):
        invalid = [Place("node/1", "Bad", "cafe", float("nan"), 139),
                   Place("node/2", "Bad", "cafe", 91, 139),
                   Place("node/3", "Bad", "cafe", 35, float("inf")),
                   Place("node/4", "Bad", "cafe", 35, 181),
                   Place("node/5", "", "cafe", 35, 139),
                   Place("node/6", "Bad", "", 35, 139)]
        self.assertEqual(PlaceIndex(invalid).search("Bad"), ())

    def test_pbf_extract_yields_same_places_as_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "synthetic.osm.pbf"
            _pbf_copy(target)
            build_derived(target, 13, SHA, directory=root)
            pbf_index = PlaceIndex.from_derived(derived_path(13, SHA, directory=root), SHA, 13)
            for query in ("桜駅", "曲がり道", "四角公園", "穴のある公園"):
                self.assertEqual(pbf_index.search(query), self.index.search(query))

    def test_cancel_and_missing_derived_data_never_return_an_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = derived_path(13, SHA, directory=root)
            with self.assertRaises(PlaceIndexError) as caught:
                PlaceIndex.from_derived(missing, SHA, 13)
            self.assertEqual(str(caught.exception), "Local place index is unavailable.")
            self.assertIsNone(caught.exception.__cause__)
            with self.assertRaises(PlaceIndexError):
                PlaceIndex.from_derived(missing, SHA, 13, cancelled=lambda: True)
            broken = root / "broken.sqlite"
            broken.write_bytes(b"broken")
            with self.assertRaises(PlaceIndexError):
                PlaceIndex.from_derived(broken, SHA, 13)

    def test_no_network_or_logging_during_runtime_search(self):
        with patch("socket.socket", side_effect=AssertionError("network")), \
                patch("logging.Logger._log", side_effect=AssertionError("logging")):
            self.assertTrue(self.index.search("桜駅"))

    def test_invalid_arguments_are_sanitized(self):
        for prefecture in (0, 48, True, "13"):
            with self.subTest(prefecture=prefecture), self.assertRaises(PlaceIndexError):
                PlaceIndex([], prefecture=prefecture)
        with self.assertRaises(PlaceIndexError) as caught:
            self.index.search("private search", limit="private")
        self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
