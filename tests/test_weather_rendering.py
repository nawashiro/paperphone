"""合成予報からPDFを生成し、出典・ライセンス・実時刻を抽出して確認。"""

import io
import os
from dataclasses import replace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import cairo
import gi
gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Poppler

from paperphone.weather_rendering import draw_weather_sheet, draw_weather_glyph
from paperphone.weather_sheet import build_weather_sheet
from tests.test_weather_sheet import NOW, result_at


class WeatherRenderingTests(unittest.TestCase):
    def setUp(self):
        from paperphone.i18n import gettext_for
        self.gettext = gettext_for
        language = patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "en"})
        language.start()
        self.addCleanup(language.stop)
        # weather_sheet captures its translator at import, before this fixture starts.
        translator = patch("paperphone.weather_sheet._", gettext_for("en"))
        translator.start()
        self.addCleanup(translator.stop)

    def render(self, width=450, height=650):
        sheet = build_weather_sheet(result_at(symbol="future_unknown"), now=NOW)
        buffer = io.BytesIO()
        surface = cairo.PDFSurface(buffer, 595, 842)
        try:
            context = cairo.Context(surface)
            result = draw_weather_sheet(context, sheet, x=20, y=20, width=width, height=height)
            context.show_page()
        finally:
            surface.finish()
        data = buffer.getvalue()
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        doc = Poppler.Document.new_from_stream(stream, len(data), None, None)
        return result, doc.get_page(0).get_text()

    def test_pdf_contains_actual_time_period_and_full_attribution(self):
        result, text = self.render()
        self.assertFalse(result.content_overflow)
        self.assertIn("09:30", text)
        self.assertIn("10:30", text)
        self.assertIn("Unknown weather condition", text)
        self.assertIn("MET Norway", text)
        self.assertIn("CC-BY 4.0", text)

    def test_small_block_reports_overflow_but_keeps_attribution(self):
        # 一行が入り切らない狭い高さで overflow を返し、帰属は必ず載せる。
        result, pdf = self.render(width=154, height=28)
        self.assertTrue(result.content_overflow)
        self.assertIn("MET Norway", pdf)
        self.assertIn("CC-BY 4.0", pdf)

    def test_unknown_code_never_draws_a_known_glyph(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 30, 30)
        context = cairo.Context(surface)
        before = bytes(surface.get_data())
        self.assertFalse(draw_weather_glyph(context, "future_unknown", x=0, y=0))
        self.assertEqual(bytes(surface.get_data()), before)
        self.assertTrue(draw_weather_glyph(context, "clearsky_day", x=0, y=0))
        self.assertNotEqual(bytes(surface.get_data()), before)

    def test_compact_footer_never_uses_less_than_6_5_points(self):
        from unittest.mock import patch
        from paperphone import weather_rendering
        sheet = build_weather_sheet(result_at(), now=NOW)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 154, 242)
        original = weather_rendering._layout
        with patch.object(weather_rendering, "_layout", wraps=original) as layout:
            result = draw_weather_sheet(cairo.Context(surface), sheet,
                x=0, y=0, width=154, height=218, compact=True)
        self.assertFalse(result.content_overflow)
        footer = [call for call in layout.call_args_list if call.args[1] == sheet.attribution]
        self.assertEqual(len(footer), 1)
        self.assertEqual(footer[0].args[3], 6.5)

    def test_compact_mixed_forecast_fetch_check_zones_remain_explicit(self):
        from paperphone.booklet_content import render_core_booklet
        from paperphone.selection import GenerationSnapshot
        sheet = build_weather_sheet(result_at(), now=NOW)
        tokyo = ZoneInfo("Asia/Tokyo")
        rows = tuple(replace(row, actual_at=row.actual_at.astimezone(tokyo),
                             period_end=row.period_end.astimezone(tokyo))
                     if row.available else row for row in sheet.rows)
        sheet = replace(sheet, rows=rows,
                        checked_at=sheet.checked_at.astimezone(ZoneInfo("Asia/Kathmandu")))
        snapshot = GenerationSnapshot.capture([], created_at=NOW, timezone_id="UTC")
        result = render_core_booklet(snapshot, name="Sample", weather=sheet)
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(result.pdf))
        doc = Poppler.Document.new_from_stream(stream, len(result.pdf), None, None)
        text = doc.get_page(0).get_text()
        # 機械向けの日付・オフセット列は載せず、時刻だけ明示する（2026-11-08の判断）。
        self.assertIn("18:30", text)
        self.assertIn("19:30", "".join(text.split()))
        self.assertNotIn("Fetched:", text)
        self.assertNotIn("Checked:", text)
        self.assertEqual(result.warnings, ())

    def test_impossibly_small_block_fails_instead_of_hiding_source(self):
        sheet = build_weather_sheet(result_at(), now=NOW)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20, 20)
        with self.assertRaises(ValueError):
            draw_weather_sheet(cairo.Context(surface), sheet, x=0, y=0, width=10, height=10)


if __name__ == "__main__":
    unittest.main()
