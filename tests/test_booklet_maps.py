"""既存配置を保つ地図追加ページ。合成PBF相当のOSMだけを使用する。"""

from dataclasses import replace
from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cairo
import gi
gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Poppler

from paperphone.booklet_layout import render_booklet
from paperphone.booklet_maps import MAP_RECT, MapPageError, map_page_painter, render_with_map
from paperphone.map_rendering import MapRenderResult
from paperphone.places import Place
from paperphone.walking import WalkingRoute
from paperphone.walking_sheet import WalkingSheet
from tests.test_map_rendering import fixture_xml


def document(pdf):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(pdf))
    return Poppler.Document.new_from_stream(stream, len(pdf), None, None)


def raster(page):
    image = cairo.ImageSurface(cairo.FORMAT_ARGB32, 842, 595)
    ctx = cairo.Context(image)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    page.render(ctx)
    image.flush()
    return bytes(image.get_data())


class BookletMapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "synthetic.osm"
        self.source.write_text(fixture_xml(), encoding="utf-8")
        self.sheet = WalkingSheet(13, datetime(2026, 11, 7, tzinfo=timezone.utc),
            "a" * 64, self.source,
            Place("a", "出発地点", "place", 35, 139),
            Place("b", "目的地点", "place", 35.002, 139.003),
            WalkingRoute(((35, 139), (35.001, 139.002), (35.002, 139.003)), 340, 243, 0, 0),
            (138.9, 34.9, 139.1, 35.1))

    def test_page_count_attribution_and_main_page_raster_are_preserved(self):
        def marker(ctx, width, height):
            ctx.set_source_rgb(.2, .3, .4)
            ctx.rectangle(3, 7, 30, 40)
            ctx.fill()
        painters = {0: marker, 3: marker, 5: marker}
        plain = document(render_with_map(painters))
        mapped = document(render_with_map(painters, self.sheet))
        self.assertEqual(plain.get_n_pages(), 1)
        self.assertEqual(mapped.get_n_pages(), 2)
        self.assertEqual(raster(plain.get_page(0)), raster(mapped.get_page(0)))
        text = mapped.get_page(1).get_text()
        for value in ("出発地点", "目的地点", "OpenStreetMap", "ODbL 1.0", "OSM Liberty"):
            self.assertIn(value, text)
        # 紙面にURLをべた書きしない（2026-11-08の利用者判断）。
        self.assertNotIn("creativecommons.org", text)

    def test_all_core_and_asset_panels_remain_identical_when_map_is_added(self):
        from paperphone.booklet_assets import asset_painters, draw_contactless_guides
        from paperphone.booklet_content import core_painters
        from paperphone.selection import GenerationSnapshot, ItemKey, SelectableItem
        from tests.test_booklet_content import weather_sheet
        items = [SelectableItem(ItemKey("fixture", "contact"), "contacts", {"name": "見本", "phone": "000-0000"}),
                 SelectableItem(ItemKey("fixture", "event"), "calendar", {"title": "架空予定", "start": "2026-01-02", "end": "2026-01-03", "all_day": "true"}),
                 SelectableItem(ItemKey("fixture", "todo"), "undated", {"title": "架空TODO", "due": ""})]
        snap = GenerationSnapshot.capture(items, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), timezone_id="UTC")
        warnings = []
        core = core_painters(snap, name="見本", weather=weather_sheet(), warnings=warnings)
        photo_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 80, 120)
        photo_context = cairo.Context(photo_surface)
        photo_context.set_source_rgb(.2, .5, .8)
        photo_context.paint()
        photo_bytes = io.BytesIO()
        photo_surface.write_to_png(photo_bytes)
        assets = asset_painters(photo=photo_bytes.getvalue(), paper_app="sudoku", notes_enabled=True,
                                contactless=True, occupied_indices=set(core))
        painters = {**core, **assets}
        plain = document(render_with_map(painters, main_overlay=draw_contactless_guides))
        mapped = document(render_with_map(painters, self.sheet, main_overlay=draw_contactless_guides))
        self.assertEqual(mapped.get_n_pages(), 2)
        self.assertEqual(raster(plain.get_page(0)), raster(mapped.get_page(0)))
        self.assertEqual(warnings, [])

    def test_legacy_map_anchor_and_rotation_are_exact(self):
        captures = []
        def draw(ctx, sheet, *, x, y, width, height):
            captures.append((ctx.user_to_device(0, 0), ctx.user_to_device(1, 1), (x, y, width, height)))
            return MapRenderResult()
        with patch("paperphone.booklet_maps.draw_walking_map", side_effect=draw):
            render_with_map({}, self.sheet)
        origin, diagonal, rect = captures[0]
        self.assertAlmostEqual(origin[0], 815)
        self.assertAlmostEqual(origin[1], 569)
        self.assertAlmostEqual(diagonal[0], 814)
        self.assertAlmostEqual(diagonal[1], 568)
        self.assertEqual(rect, MAP_RECT)

    def test_actual_map_fold_centers_and_memo_region_survive(self):
        with patch("paperphone.booklet_maps.draw_walking_map", return_value=MapRenderResult()):
            page = document(render_with_map({}, self.sheet)).get_page(1)
        image = raster(page)
        def black(x, y):
            return min(image[(y * 842 + x) * 4:(y * 842 + x) * 4 + 3]) < 60
        self.assertTrue(black(812, 31))
        self.assertTrue(black(33, 34))
        # ドット(17,147)を余白・pivot・回転した位置。小数半径なので周囲で確認。
        pixels = [image[(yy * 842 + xx) * 4:(yy * 842 + xx) * 4 + 3]
                  for yy in range(421, 424) for xx in range(797, 800)]
        self.assertTrue(any(min(pixel) < 250 for pixel in pixels))

    def test_missing_source_and_overflow_are_not_blank_successful_pages(self):
        with self.assertRaises(MapPageError) as caught:
            render_with_map({}, replace(self.sheet, source_path=self.source.parent / "private-missing.pbf"))
        self.assertEqual(caught.exception.reason, "source-unavailable")
        self.assertNotIn("private", str(caught.exception))
        with patch("paperphone.booklet_maps.draw_walking_map", return_value=MapRenderResult(content_overflow=True)):
            with self.assertRaises(MapPageError) as caught:
                render_with_map({}, self.sheet)
        self.assertEqual(caught.exception.reason, "overflow")

    def test_long_left_labels_report_warning_even_if_footer_can_fit(self):
        warnings = []
        sheet = replace(self.sheet, departure=replace(self.sheet.departure, name="長い地名" * 100))
        with patch("paperphone.booklet_maps.draw_walking_map", return_value=MapRenderResult()):
            render_with_map({}, sheet, warnings=warnings)
        self.assertTrue(any(w.code == "location_name_ellipsized" for w in warnings))


if __name__ == "__main__":
    unittest.main()
