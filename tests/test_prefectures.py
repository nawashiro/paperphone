"""内蔵47都道府県と純Python幾何判定（公開都市座標・合成fixture）。"""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from paperphone.prefectures import (
    PREFECTURES, PrefectureBoundaries, prefecture_at, prefecture_by_code, _builtin,
)

RESOURCE = Path(__file__).resolve().parents[1] / "src/paperphone/resources/maps/japan-prefectures.geojson"
# 公開の県庁所在都市の中心付近。利用者の位置情報ではない。
CAPITALS = (
    (43.062, 141.354), (40.824, 140.740), (39.702, 141.154),
    (38.268, 140.869), (39.720, 140.103), (38.240, 140.363),
    (37.760, 140.474), (36.365, 140.471), (36.555, 139.882),
    (36.389, 139.060), (35.861, 139.646), (35.607, 140.106),
    (35.682, 139.759), (35.444, 139.638), (37.916, 139.036),
    (36.695, 137.214), (36.561, 136.656), (36.065, 136.222),
    (35.664, 138.568), (36.648, 138.194), (35.423, 136.760),
    (34.975, 138.383), (35.181, 136.906), (34.719, 136.506),
    (35.018, 135.855), (35.012, 135.768), (34.694, 135.502),
    (34.690, 135.196), (34.685, 135.805), (34.230, 135.171),
    (35.501, 134.235), (35.468, 133.050), (34.655, 133.919),
    (34.385, 132.455), (34.178, 131.474), (34.070, 134.555),
    (34.343, 134.047), (33.839, 132.765), (33.559, 133.531),
    (33.590, 130.402), (33.264, 130.300), (32.750, 129.878),
    (32.803, 130.708), (33.239, 131.609), (31.907, 131.421),
    (31.596, 130.558), (26.212, 127.681),
)


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def feature(code, rings, kind="Polygon"):
    return {"type": "Feature", "properties": {"code": code},
            "geometry": {"type": kind, "coordinates": rings}}


def square(x, y, size=10):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


class BuiltinTests(unittest.TestCase):
    def test_all_47_codes_and_bilingual_names(self):
        self.assertEqual(list(range(1, 48)), [p.code for p in PREFECTURES])
        self.assertEqual(47, len({p.name_ja for p in PREFECTURES}))
        self.assertEqual(47, len({p.name_en for p in PREFECTURES}))
        for prefecture in PREFECTURES:
            self.assertTrue(prefecture.name_en.isascii())
            self.assertFalse(prefecture.name_ja.isascii())
            self.assertIs(prefecture, prefecture_by_code(prefecture.code))
        self.assertEqual("東京都", prefecture_by_code(13).name_ja)
        self.assertEqual("Tokyo", prefecture_by_code(13).name_en)

    def test_unknown_codes_do_not_coerce(self):
        for code in (0, 48, -1, None, "13", 13.0, True):
            self.assertIsNone(prefecture_by_code(code))

    def test_all_47_real_capital_coordinates(self):
        for code, (latitude, longitude) in enumerate(CAPITALS, 1):
            with self.subTest(code=code):
                self.assertEqual(code, prefecture_at(latitude, longitude))

    def test_islands_and_non_capital_cities(self):
        for code, lat, lon in ((13, 27.094, 142.191), (47, 24.340, 124.157),
                               (1, 41.768, 140.729), (27, 34.573, 135.483)):
            with self.subTest(code=code, lat=lat):
                self.assertEqual(code, prefecture_at(lat, lon))

    def test_outside_and_valid_global_extremes(self):
        for point in ((0, 0), (51.507, -0.128), (37.566, 126.978),
                      (30, 145), (90, 180), (-90, -180)):
            self.assertIsNone(prefecture_at(*point))

    def test_invalid_coordinate_types_ranges_and_nonfinite(self):
        for value in (None, "35", True, [], complex(1, 2), float("nan"),
                      float("inf"), -float("inf"), 10 ** 1000):
            for point in ((value, 139), (35, value)):
                with self.subTest(value=repr(value), latitude_first=point[0] is value):
                    with self.assertRaises(ValueError):
                        prefecture_at(*point)
        for point in ((90.001, 139), (-90.001, 139), (35, 180.001), (35, -180.001)):
            with self.assertRaises(ValueError):
                prefecture_at(*point)

    def test_cold_load_and_lookup_have_no_network(self):
        _builtin.cache_clear()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(13, prefecture_at(35.682, 139.759))

    def test_bundled_real_geometry_and_provenance(self):
        document = json.loads(RESOURCE.read_text(encoding="utf-8"))
        self.assertEqual(list(range(1, 48)), [f["properties"]["code"] for f in document["features"]])
        self.assertEqual("https://osm.download.movisda.io/index.geojson", document["provenance"]["source_url"])
        self.assertEqual(64, len(document["provenance"]["source_sha256"]))
        for f in document["features"]:
            self.assertTrue(f["properties"]["name_ja"])
            self.assertTrue(f["properties"]["name_en"])
            self.assertEqual(12, len(f["properties"]["source_timestamp"]))
            self.assertIn(f["geometry"]["type"], ("Polygon", "MultiPolygon"))
            self.assertGreater(len(json.dumps(f["geometry"])), 300)  # bboxではない


class GeometryTests(unittest.TestCase):
    def test_polygon_hole_outer_boundary_and_vertices(self):
        boundaries = PrefectureBoundaries.from_geojson(collection(feature(13, [square(0, 0), square(3, 3, 4)])))
        for lat, lon in ((1, 1), (0, 0), (5, 0), (10, 10)):
            self.assertEqual(13, boundaries.lookup(lat, lon))
        for lat, lon in ((5, 5), (3, 3), (5, 3), (-1, 1), (11, 5)):
            self.assertIsNone(boundaries.lookup(lat, lon))

    def test_multipolygon_disconnected_islands_and_hole(self):
        boundaries = PrefectureBoundaries.from_geojson(collection(
            feature(47, [[square(0, 0), square(3, 3, 4)], [square(20, 20)]], "MultiPolygon")))
        self.assertEqual(47, boundaries.lookup(1, 1))
        self.assertEqual(47, boundaries.lookup(21, 21))
        self.assertIsNone(boundaries.lookup(5, 5))
        self.assertIsNone(boundaries.lookup(15, 15))

    def test_not_just_bounding_boxes_and_lat_lon_order(self):
        triangle = [[130, 30], [140, 30], [130, 40], [130, 30]]
        boundaries = PrefectureBoundaries.from_geojson(collection(feature(1, [triangle])))
        self.assertEqual(1, boundaries.lookup(31, 131))
        self.assertIsNone(boundaries.lookup(39, 139))

    def test_winding_does_not_change_result_and_input_is_copied(self):
        document = collection(feature(13, [square(0, 0)[::-1]]))
        boundaries = PrefectureBoundaries.from_geojson(document)
        document["features"][0]["geometry"]["coordinates"][0][1][0] = 90
        self.assertEqual(13, boundaries.lookup(5, 5))

    def test_shared_boundary_and_overlapping_regions_are_unknown(self):
        boundaries = PrefectureBoundaries.from_geojson(collection(
            feature(1, [square(0, 0)]), feature(2, [square(10, 0)])))
        self.assertIsNone(boundaries.lookup(5, 10))
        self.assertEqual(1, boundaries.lookup(5, 9))
        overlap = PrefectureBoundaries.from_geojson(collection(
            feature(1, [square(0, 0)]), feature(2, [square(5, 0)])))
        self.assertIsNone(overlap.lookup(5, 7))

    def test_empty_collection_is_unknown(self):
        self.assertIsNone(PrefectureBoundaries.from_geojson(collection()).lookup(35, 139))

    def test_invalid_geometry_is_rejected(self):
        good = collection(feature(1, [square(0, 0)]))
        variants = []
        for kind in ("Point", "LineString", "GeometryCollection"):
            bad = copy.deepcopy(good)
            bad["features"][0]["geometry"]["type"] = kind
            variants.append(bad)
        variants += [collection(feature(0, [square(0, 0)])),
                     collection(feature(1, [])),
                     collection(feature(1, [[[0, 0], [1, 0], [1, 1], [0, 1]]])),
                     collection(feature(1, [square(180, 0)])),
                     collection(good["features"][0], good["features"][0])]
        for bad in variants:
            with self.assertRaises(ValueError):
                PrefectureBoundaries.from_geojson(bad)


if __name__ == "__main__":
    unittest.main()
