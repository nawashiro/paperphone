"""合成データのみで既存サイズの英日紙面をCairo/Pango/Poppler検証。"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import gi
gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Poppler

from paperphone.booklet_content import calendar_item, core_painters, render_core_booklet
from paperphone.events import CalendarEvent
from paperphone.printing_fonts import register_printing_fonts
from paperphone.selection import GenerationSnapshot, ItemKey, SelectableItem
from paperphone.temporal import DateOnly, ZonedDateTime
from paperphone.weather import Forecast, ForecastResult
from paperphone.weather_sheet import build_weather_sheet

UTC = timezone.utc
NOW = datetime(2026, 1, 2, 23, 30, tzinfo=UTC)


def snapshot(items=(), zone="UTC", now=NOW):
    return GenerationSnapshot.capture(items, created_at=now, timezone_id=zone)


def text_of(pdf):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(pdf))
    doc = Poppler.Document.new_from_stream(stream, len(pdf), None, None)
    return doc.get_page(0).get_text()


def item(category, fields, uid="fixture"):
    return SelectableItem(ItemKey("source", uid), category, fields)


def weather_sheet(symbol="cloudy"):
    now = NOW.replace(hour=8, minute=0)
    forecasts = tuple(Forecast(now.replace(hour=hour, minute=30), 5, symbol,
                     now.replace(hour=hour, minute=30) + timedelta(hours=1)) for hour in (9, 12, 18))
    return build_weather_sheet(ForecastResult(forecasts, now - timedelta(minutes=5), now,
                               now + timedelta(hours=1)), now=now)


class BookletContentTests(unittest.TestCase):
    def setUp(self):
        language = patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "en"})
        language.start()
        self.addCleanup(language.stop)

    def test_non_iana_system_zone_keeps_captured_local_date(self):
        from paperphone.selection import GenerationSnapshot
        fixed = timezone(timedelta(hours=5, minutes=30))
        source = GenerationSnapshot.capture([], created_at=datetime(2026, 1, 2, 23, 30, tzinfo=fixed),
                                            timezone_id="system-localtime")
        result = render_core_booklet(source, name="Fixture")
        self.assertIn("02 January 2026", text_of(result.pdf))

    def test_english_cover_month_is_not_changed_by_process_locale(self):
        from paperphone.booklet_content import _cover_date
        class JapaneseMonthDate(date):
            def strftime(self, pattern):
                # libcのロケール依存部分だけを模擬。文字列リテラルは変換しない。
                if "%B" in pattern:
                    pattern = pattern.replace("%B", "1月")
                return super().strftime(pattern)
        value = JapaneseMonthDate(2026, 1, 3)
        self.assertEqual(_cover_date(value, "%d %B %Y"), "03 January 2026")
        self.assertEqual(_cover_date(value, "%Y年%m月%d日"), "2026年01月03日")

    def test_fixed_panels_and_cover_snapshot_local_date(self):
        source = snapshot([item("contacts", {"name": "Example", "phone": "000-0000"}),
                           item("today", {"title": "Task", "due": ""})], "Asia/Tokyo")
        warnings = []
        self.assertEqual(set(core_painters(source, name="Sample", weather=weather_sheet(),
                                         warnings=warnings)), {0, 2, 5, 6})
        result = render_core_booklet(source, name="Sample")
        text = text_of(result.pdf)
        self.assertIn("SAMPLE'S", text)
        self.assertIn("PHONE", text)
        self.assertIn("03 January 2026", text)
        self.assertEqual(result.warnings, ())
        self.assertNotIn("SAMPLE", repr(result))
        with self.assertRaises(FrozenInstanceError):
            result.warnings = ()

    def test_japanese_and_english_pdf_no_missing_glyphs(self):
        for title in ("Example appointment", "架空の予定 山田花子"):
            event = CalendarEvent("fixture", "instance", title,
                DateOnly(date(2026, 1, 2)), DateOnly(date(2026, 1, 3)))
            source = snapshot([calendar_item(event, "source", ZoneInfo("Pacific/Honolulu")),
                               item("contacts", {"name": "架空連絡先", "phone": "000-0000"}),
                               item("undated", {"title": "架空の買い物", "due": ""})])
            result = render_core_booklet(source, name="見本")
            text = text_of(result.pdf)
            self.assertIn(title, text)
            self.assertIn("架空連絡先", text)
            self.assertIn("架空の買い物", text)
            self.assertIn("All day", text)
            self.assertIn("2026-01-02", text)
            self.assertNotIn("00:00", text)
            self.assertNotIn("\ufffd", text)
            self.assertEqual(result.warnings, ())

    def test_japanese_cover_date_translation_and_calendar_label(self):
        translations = {"%d %B %Y": "%Y年%m月%d日", "All day": "終日", "CALENDAR": "予定",
                        "{name}'S": "{name}の", "PHONE": "ペーパーフォン"}
        event = CalendarEvent("fixture", "instance", "架空の予定", DateOnly(date(2026, 1, 2)),
                              DateOnly(date(2026, 1, 3)))
        with patch("paperphone.booklet_content.gettext_for",
                   return_value=lambda message: translations.get(message, message)):
            result = render_core_booklet(snapshot([calendar_item(event, "source", UTC)]), name="見本")
        text = text_of(result.pdf)
        self.assertIn("2026年01月02日", "".join(text.split()))
        self.assertIn("終日", text)
        self.assertIn("見本の", text)
        self.assertEqual(result.warnings, ())

    def test_source_zone_conversion_uses_target_date_dst_and_real_minutes(self):
        # Tokyo 18:30 is New York 04:30 in January, not the current summer offset.
        start = datetime(2026, 1, 2, 18, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        event = CalendarEvent("fixture", "instance", "Fixture", ZonedDateTime(start),
                              ZonedDateTime(start + timedelta(hours=1)))
        projected = calendar_item(event, "source", ZoneInfo("America/New_York"))
        self.assertEqual(projected.fields["start"], "2026-01-02T04:30:00-05:00")
        result = render_core_booklet(snapshot([projected]), name="Sample")
        text = text_of(result.pdf)
        self.assertIn("04:30", text)
        self.assertIn("05:30", text)

    def test_true_relative_due_not_fabricated_tomorrow_and_no_due_no_label(self):
        items = [item("today", {"title": "Later", "due": "2026-01-09"}, "later"),
                 item("upcoming", {"title": "Earlier", "due": "2025-12-30 09:30"}, "earlier"),
                 item("undated", {"title": "No due", "due": ""}, "none")]
        text = text_of(render_core_booklet(snapshot(items), name="Sample").pdf)
        self.assertIn("In 7 days", text)
        self.assertIn("3 days ago 09:30", text)
        self.assertEqual(text.count("Due:"), 2)
        self.assertNotIn("Tomorrow", text)

    def test_long_text_and_count_omissions_are_safe_structured_warnings(self):
        items = [item("calendar", {"title": "秘密の架空長文" * 90,
                 "start": "2026-01-02T09:30:00+00:00", "end": "2026-01-02T10:30:00+00:00",
                 "all_day": "false"}, str(index)) for index in range(8)]
        items += [item("contacts", {"name": "Example", "phone": "000"}, str(i)) for i in range(9)]
        items += [item("today", {"title": "Fixture", "due": ""}, str(i)) for i in range(11)]
        result = render_core_booklet(snapshot(items), name="名前" * 100)
        self.assertEqual({(w.panel, w.count) for w in result.warnings if w.code == "items_omitted"},
                         {(0, 2), (1, 2), (2, 2)})
        self.assertIn((1, "text_ellipsized"), {(w.panel, w.code) for w in result.warnings})
        self.assertIn((5, "text_ellipsized"), {(w.panel, w.code) for w in result.warnings})
        self.assertNotIn("秘密", repr(result.warnings))
        self.assertNotIn("source", repr(result.warnings))
        self.assertEqual(text_of(result.pdf).count("09:30"), 6)

    def test_separate_ellipsized_fields_aggregate_exact_count(self):
        source = snapshot([item("contacts", {"name": "架空の長い氏名" * 80,
                                             "phone": "000-0000-0000 " * 80})])
        result = render_core_booklet(source, name="Sample")
        fields = [warning for warning in result.warnings
                  if warning.panel == 0 and warning.code == "text_ellipsized"]
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].count, 2)
        self.assertNotIn("架空", repr(result.warnings))

    def test_snapshot_is_frozen_against_updated_source_and_weather(self):
        fields = {"title": "Frozen fixture", "due": "2026-01-02"}
        source = snapshot([item("today", fields), weather_sheet().as_selectable_item()])
        fields["title"] = "Changed fixture"
        # Even an explicit newly fetched model cannot replace weather captured in snapshot.
        result = render_core_booklet(source, name="Sample", weather=weather_sheet("future_unknown"))
        text = text_of(result.pdf)
        self.assertIn("Frozen fixture", text)
        self.assertNotIn("Changed fixture", text)
        self.assertNotIn("future_unknown", text)
        self.assertEqual(result.warnings, ())
        updated = render_core_booklet(snapshot([item("today", fields)]), name="Sample")
        self.assertIn("Changed fixture", text_of(updated.pdf))
        self.assertIn("Frozen fixture", text_of(result.pdf))

    def test_three_forecasts_fit_actual_times_periods_and_attribution(self):
        for symbol in ("cloudy", "heavyrainshowersandthunder_day", "future_unknown"):
            result = render_core_booklet(snapshot(), name="Sample", weather=weather_sheet(symbol))
            text = text_of(result.pdf)
            self.assertEqual(result.warnings, (), symbol)
            for real in ("09:30", "10:30", "12:30", "13:30", "18:30", "19:30"):
                self.assertIn(real, text)
            self.assertNotIn("09:00", text)
            # 紙面には取得日・機械向けコード列を置かない（2026-11-08の判断）。
            self.assertNotIn("Fetched", text)
            self.assertNotIn("+0000", text)
            self.assertIn("MET Norway", text)
            self.assertIn("CC-BY 4.0", text)
    
    def test_long_weather_code_does_not_print_machine_code(self):
        result = render_core_booklet(snapshot(), name="Sample", weather=weather_sheet("unknown" * 100))
        text = text_of(result.pdf)
        self.assertNotIn("unknown", text)
        self.assertIn("MET Norway", text)

    def test_real_japanese_catalog_cover_calendar_and_weather(self):
        from paperphone.i18n import gettext_for
        with patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "ja"}):
            translate = gettext_for("ja")
            self.assertEqual(translate("%d %B %Y"), "%Y年%m月%d日")
            event = CalendarEvent("fixture", "instance", "架空の予定", DateOnly(date(2026, 1, 2)),
                                  DateOnly(date(2026, 1, 3)))
            result = render_core_booklet(snapshot([calendar_item(event, "source", UTC)]),
                                         name="見本", weather=weather_sheet())
        text = "".join(text_of(result.pdf).split())
        self.assertIn("2026年01月02日", text)
        self.assertIn(translate("All day"), text)
        self.assertIn(translate("WEATHER"), text)
        self.assertIn("09:30", text)
        self.assertEqual(result.warnings, ())

    def test_missing_bundled_fonts_raise_safe_error(self):
        with patch("paperphone.booklet_content.register_printing_fonts", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "^Bundled printing fonts are unavailable\\.$"):
                render_core_booklet(snapshot(), name="秘密の架空名")

    def test_bundled_fonts_registered_and_used(self):
        self.assertTrue(register_printing_fonts())
        result = render_core_booklet(snapshot([item("contacts", {"name": "Example", "phone": "000"})]),
                                     name="Sample")
        self.assertIn(b"Varela", result.pdf)
        self.assertIn(b"WorkSans", result.pdf)


if __name__ == "__main__":
    unittest.main()
