"""版付き派生データだけを使うベクタ地図試験。実地点・外部通信を使用しない。"""

from dataclasses import replace
from datetime import datetime, timezone
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import cairo
import gi
gi.require_version('Poppler', '0.18')
from gi.repository import Poppler

from paperphone.map_rendering import (
    AREA_COLORS, ROAD_STYLES, _read_features, draw_walking_map, project,
)
from paperphone.osm_derived import build_derived, derived_path
from paperphone.places import Place
from paperphone.walking import WalkingRoute
from paperphone.walking_sheet import WalkingSheet

SHA = "a" * 64


def fixture_xml():
    # 単純wayと穴を持つrelationの両方。原POIは道路の端点と異なる。
    nodes = {
        1: (35.0, 139.0), 2: (35.001, 139.002), 3: (35.002, 139.003),
        10: (35.0002, 139.0003), 11: (35.0002, 139.0013),
        12: (35.0012, 139.0013), 13: (35.0012, 139.0003),
        20: (35.0005, 139.0006), 21: (35.0005, 139.0010),
        22: (35.0009, 139.0010), 23: (35.0009, 139.0006),
        30: (35.0015, 139.0003), 31: (35.0015, 139.0007),
        32: (35.0019, 139.0007), 33: (35.0019, 139.0003),
        40: (35.0013, 139.0016), 41: (35.0013, 139.002),
        42: (35.0017, 139.002), 43: (35.0017, 139.0016),
        50: (35.002, 139.0004), 51: (35.002, 139.0008),
        52: (35.0024, 139.0008), 53: (35.0024, 139.0004),
    }
    text = ['<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">']
    for identifier, (lat, lon) in nodes.items():
        text.append(f'<node id="{identifier}" lat="{lat}" lon="{lon}" version="1"/>')

    def way(identifier, refs, tags):
        text.append(f'<way id="{identifier}" version="1">')
        text.extend(f'<nd ref="{ref}"/>' for ref in refs)
        text.extend(f'<tag k="{key}" v="{value}"/>' for key, value in tags.items())
        text.append('</way>')

    way(1, [1, 2, 3], {"highway": "primary", "name": "Main Street"})
    way(2, [1, 3], {"highway": "footway", "name": "散歩道"})
    way(10, [10, 11, 12, 13, 10], {})
    way(20, [20, 21, 22, 23, 20], {})
    way(30, [30, 31, 32, 33, 30], {"building": "yes", "name": "図書館"})
    way(40, [40, 41, 42, 43, 40], {"leisure": "park", "name": "公園"})
    way(50, [50, 51, 52, 53, 50], {"landuse": "forest"})
    text.append('<relation id="100" version="1"><member type="way" ref="10" role="outer"/>'
                '<member type="way" ref="20" role="inner"/><tag k="type" v="multipolygon"/>'
                '<tag k="natural" v="water"/><tag k="name" v="Lake"/></relation></osm>')
    return ''.join(text)


def _pbf_copy(source, target):
    import osmium
    with osmium.SimpleWriter(str(target)) as writer:
        class Copy(osmium.SimpleHandler):
            def node(self, value): writer.add_node(value)
            def way(self, value): writer.add_way(value)
            def relation(self, value): writer.add_relation(value)
        Copy().apply_file(str(source))


class MapRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.xml = self.directory / 'synthetic.osm'
        self.xml.write_text(fixture_xml(), encoding='utf-8')
        build_derived(self.xml, 13, SHA, directory=self.directory)
        self.sheet = WalkingSheet(
            13, datetime(2026, 11, 7, tzinfo=timezone.utc), SHA,
            derived_path(13, SHA, directory=self.directory),
            Place('a', 'Departure 出発', 'place', 34.9999, 138.9999),
            Place('b', 'Destination 目的地', 'place', 35.0022, 139.0032),
            WalkingRoute(((35, 139), (35.001, 139.002), (35.002, 139.003)), 340, 243, 14, 28),
            (138.9, 34.9, 139.1, 35.1),
        )

    def pdf(self, sheet=None, width=550, height=450):
        output = io.BytesIO()
        surface = cairo.PDFSurface(output, width + 40, height + 40)
        context = cairo.Context(surface)
        context.translate(3, 4)
        context.set_line_width(13)
        matrix = context.get_matrix()
        result = draw_walking_map(context, sheet or self.sheet, x=10, y=10, width=width, height=height)
        self.assertEqual(context.get_matrix(), matrix)
        self.assertEqual(context.get_line_width(), 13)
        surface.finish()
        p = output.getvalue()
        return result, p

    def bounds(self):
        sw = project(34.999, 138.999)
        ne = project(35.004, 139.005)
        return (*sw, *ne)

    def test_raw_osm_classes_areas_and_holes(self):
        areas, roads, labels = _read_features(self.sheet, self.bounds())
        self.assertEqual({kind for kind, _ in areas}, {'water', 'building', 'park', 'forest'})
        self.assertEqual({kind for kind, _ in roads}, {'primary', 'footway'})
        water = next(rings for kind, rings in areas if kind == 'water')
        # 外周と内周のリングで穴を保持する。
        self.assertEqual(len(water), 2)
        from shapely.geometry import Point, Polygon
        hole = Point(*project(35.0007, 139.0008))
        self.assertTrue(Polygon(water[0]).contains(hole))
        self.assertTrue(Polygon(water[1]).contains(hole))
        self.assertIn('散歩道', [name for name, _, _ in labels])
        self.assertGreater(ROAD_STYLES['primary'][0], ROAD_STYLES['footway'][0])
        self.assertNotEqual(ROAD_STYLES['primary'][1], ROAD_STYLES['footway'][1])

    def test_route_overlay_uses_only_graph_points_without_poi_connectors(self):
        from unittest.mock import Mock, call
        from paperphone.map_rendering import _route_path
        context = Mock()
        coordinates = tuple(project(lat, lon) for lat, lon in self.sheet.route.points)
        _route_path(context, coordinates, lambda px, py: (px, py))
        self.assertEqual(context.method_calls, [call.new_path(), call.move_to(*coordinates[0]),
                                                call.line_to(*coordinates[1]), call.line_to(*coordinates[2])])
        context.reset_mock()
        _route_path(context, coordinates[:1], lambda px, py: (px, py))
        context.line_to.assert_not_called()

    def test_public_style_reference_values_are_adapted_for_print(self):
        # OSM Liberty park=.7*#d8e8c8+.3*#f5f3ee、water=.5*#9ebdff+.5*背景。
        self.assertEqual(AREA_COLORS['park'], '#e1ebd3')
        self.assertEqual(AREA_COLORS['water'], '#cad8f7')
        self.assertEqual(AREA_COLORS['building'], '#dcd9d6')
        self.assertEqual(ROAD_STYLES['residential'][0], 1.9)

    def test_actual_pbf_is_read_locally(self):
        pbf = self.directory / 'synthetic.osm.pbf'
        _pbf_copy(self.xml, pbf)
        build_derived(pbf, 13, SHA, directory=self.directory)
        xml_sheet = self.sheet
        pbf_sheet = replace(self.sheet,
                            source_path=derived_path(13, SHA, directory=self.directory))
        a = _read_features(pbf_sheet, self.bounds())
        b = _read_features(xml_sheet, self.bounds())
        self.assertEqual([(kind, tuple(tuple(point) for point in rings)) for kind, rings in a[0]],
                         [(kind, tuple(tuple(point) for point in rings)) for kind, rings in b[0]])
        result, _ = self.pdf(pbf_sheet)
        self.assertIsNone(result.error)

    def test_projected_bounds_contain_all_original_and_route_points(self):
        # 迂回路が元のPOIの矩形からはみ出す場合も含める。
        route = replace(self.sheet.route, points=((35, 139), (35.01, 139.02), (35.002, 139.003)))
        result, _ = self.pdf(replace(self.sheet, route=route))
        self.assertIsNone(result.error)
        self.assertFalse(result.content_overflow)
        west, south, east, north = result.projected_bounds
        for lat, lon in (*route.points, (self.sheet.departure.latitude, self.sheet.departure.longitude),
                         (self.sheet.destination.latitude, self.sheet.destination.longitude)):
            px, py = project(lat, lon)
            self.assertTrue(west < px < east)
            self.assertTrue(south < py < north)
        # 緯度35度では東西と南北を同じ角度の縮尺にはしない。
        east_delta = project(35, 139.001)[0] - project(35, 139)[0]
        north_delta = project(35.001, 139)[1] - project(35, 139)[1]
        self.assertGreater(north_delta, east_delta * 1.2)

    def test_vector_pdf_extractable_bilingual_footer_without_network(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
            result, pdf = self.pdf()
        self.assertIsNone(result.error)
        self.assertFalse(result.content_overflow)
        self.assertNotIn(b'/Subtype /Image', pdf)
        target = self.directory / 'map.pdf'
        target.write_bytes(pdf)
        text = Poppler.Document.new_from_file(target.as_uri(), None).get_page(0).get_text()
        for expected in ('A', 'B', 'OpenStreetMap contributors',
                         'ODbL 1.0',
                         'OSM Liberty', 'OpenMapTiles',
                         'Mapbox', 'CC BY 4.0', 'CC BY 3.0'):
            self.assertIn(expected, text)
        # 紙面にURLをべた書きしない（2026-11-08の利用者判断）。
        for removed in ('https://', 'https://openmaptiles.org/'):
            self.assertNotIn(removed, text)
        # 紙面には地名と基準日の注記を置かない（2026-11-08の判断）。
        for removed in ('出発', '目的地', '徒歩距離', '道路までのずれ', 'データ基準日'):
            self.assertNotIn(removed, text)
        self.assertNotIn('2026-11-07', text)

    def test_tiny_rectangle_explicitly_reports_overflow_before_source_read(self):
        with patch('paperphone.map_rendering._read_features', side_effect=AssertionError('not reached')):
            for width, height in ((8, 8), (550, 35), (0, 50), (float('nan'), 200)):
                surface = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)
                result = draw_walking_map(cairo.Context(surface), self.sheet,
                                          x=0, y=0, width=width, height=height)
                self.assertTrue(result.content_overflow)
                self.assertEqual(surface.ink_extents(), (0, 0, 0, 0))

    def test_single_point_zero_distance_preserves_both_endpoints(self):
        point = replace(self.sheet.departure, name='Same 同一点')
        sheet = replace(self.sheet, departure=point, destination=point,
                        route=WalkingRoute(((point.latitude, point.longitude),), 0, 0, 0, 0))
        result, pdf = self.pdf(sheet)
        self.assertIsNone(result.error)
        self.assertFalse(result.content_overflow)
        self.assertTrue(all(map(lambda n: abs(n) < 1e10, result.projected_bounds)))
        target = self.directory / 'same.pdf'
        target.write_bytes(pdf)
        text = Poppler.Document.new_from_file(target.as_uri(), None).get_page(0).get_text()
        self.assertIn('A', text)
        self.assertIn('B', text)
        self.assertNotIn('徒歩距離', text)

    def test_invalid_source_error_is_safe_and_context_restored(self):
        source = self.directory / 'PRIVATE-PATH.sqlite'
        source.write_bytes(b'not a derived store')
        result, _ = self.pdf(replace(self.sheet, source_path=source))
        self.assertEqual(result.error, 'source-unavailable')
        self.assertNotIn('PRIVATE', repr(result))
        result, _ = self.pdf(replace(self.sheet,
                                     source_path=self.directory / 'missing.sqlite'))
        self.assertEqual(result.error, 'source-unavailable')

    def test_hole_is_not_painted_as_water(self):
        # パレットの画素確認は人工地図だけ。PDF自体はベクタのまま。
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 500)
        context = cairo.Context(surface)
        result = draw_walking_map(context, self.sheet, x=0, y=0, width=600, height=500)
        self.assertIsNone(result.error)
        # 実際の穴の描画を単独の投影パスでも確認し、ラベル等の重なりを避ける。
        from paperphone.map_rendering import _path, _color
        areas, _, _ = _read_features(self.sheet, self.bounds())
        water = next(rings for kind, rings in areas if kind == 'water')
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ox, oy = project(35, 139)
        screen = lambda px, py: ((px - ox) * 2, 400 - (py - oy) * 2)
        context.new_path()
        _path(context, water, screen)
        _color(context, AREA_COLORS['water'])
        context.fill()
        surface.flush()
        px, py = map(int, screen(*project(35.0007, 139.0008)))
        data = bytes(surface.get_data())
        self.assertEqual(data[py * surface.get_stride() + px * 4:py * surface.get_stride() + px * 4 + 4], b'\xff\xff\xff\xff')


if __name__ == '__main__':
    unittest.main()
