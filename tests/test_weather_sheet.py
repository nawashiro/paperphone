"""画面と紙面が同じ実時刻・対象期間を使い、未知状態や欠損を偽装しない。"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from paperphone.selection import GenerationSnapshot
from paperphone.weather import Forecast, ForecastResult
from paperphone.weather_location import WeatherLocationResult, WeatherLocationState
from paperphone.i18n import gettext_for
from paperphone.weather_sheet import build_weather_sheet, known_symbol, weather_lines

UTC = timezone.utc
_ = gettext_for()

NOW = datetime(2026, 1, 2, 8, tzinfo=UTC)


def result_at(hour=9, minute=30, *, symbol="cloudy", zone=UTC):
    start = datetime(2026, 1, 2, hour, minute, tzinfo=zone)
    forecast = Forecast(start, 5, symbol, start + timedelta(hours=1))
    return ForecastResult((forecast,), NOW - timedelta(minutes=5), NOW, NOW + timedelta(hours=1))


class WeatherSheetTests(unittest.TestCase):
    def test_actual_time_not_replaced_by_target(self):
        result = result_at()
        sheet = build_weather_sheet(result, now=NOW)
        row = sheet.rows[0]
        self.assertEqual(row.target_hour, 9)
        self.assertEqual(row.actual_at.minute, 30)
        self.assertEqual(row.period_end.hour, 10)
        fields = sheet.as_selectable_item().fields
        self.assertIn("09:30", fields["forecast_9_actual_at"])
        self.assertIn("10:30", fields["forecast_9_period_end"])
        self.assertIn("09:30", fields["text"])
        self.assertIn("07:55", fields["fetched_at"])
        self.assertNotIn("latitude", fields)
        self.assertNotIn("longitude", fields)

    def test_no_replacement_for_omitted_past_slot(self):
        result = result_at(18, 0)
        sheet = build_weather_sheet(result, now=NOW.replace(hour=16))
        self.assertFalse(sheet.rows[0].available)
        self.assertFalse(sheet.rows[1].available)
        self.assertTrue(sheet.rows[2].available)
        fields = sheet.as_selectable_item().fields
        self.assertEqual(fields["forecast_9_actual_at"], "")
        self.assertEqual(fields["forecast_9_temperature_c"], "")

    def test_unknown_code_is_retained_without_known_icon_or_state(self):
        sheet = build_weather_sheet(result_at(symbol="future_unknown"), now=NOW)
        self.assertFalse(sheet.rows[0].symbol_known)
        self.assertEqual(sheet.rows[0].symbol, "future_unknown")
        fields = sheet.as_selectable_item().fields
        self.assertEqual(fields["forecast_9_symbol_known"], "false")
        self.assertIn(_("Unknown weather condition"), fields["text"])
        self.assertNotIn("cloudy", fields["text"])
        self.assertTrue(known_symbol("clearsky_day"))
        self.assertTrue(known_symbol("snowshowers_polartwilight"))
        self.assertFalse(known_symbol("rain_unknownvariant"))

    def test_fractional_offsets_preserve_local_period_and_source_time(self):
        for key in ("Asia/Kolkata", "Asia/Kathmandu"):
            zone = ZoneInfo(key)
            now = NOW.astimezone(zone)
            sheet = build_weather_sheet(result_at(zone=zone), now=now)
            self.assertEqual(sheet.rows[0].actual_at.hour, 9)
            self.assertEqual(sheet.rows[0].actual_at.minute, 30)
            self.assertEqual(sheet.rows[0].period_end.hour, 10)
            self.assertEqual(sheet.fetched_at, NOW - timedelta(minutes=5))
            self.assertEqual(sheet.fetched_at.utcoffset(), now.utcoffset())

    def test_timezone_change_reprojects_without_mutating_snapshot(self):
        result = result_at()
        sheet = build_weather_sheet(result, now=NOW)
        snapshot = GenerationSnapshot.capture([sheet.as_selectable_item()],
            created_at=NOW, timezone_id="UTC")
        fields_before = snapshot.items[0].fields
        tokyo = build_weather_sheet(result, now=NOW.astimezone(ZoneInfo("Asia/Tokyo")))
        self.assertEqual(tokyo.rows[2].actual_at.hour, 18)
        self.assertEqual(snapshot.items[0].fields, fields_before)
        with self.assertRaises(FrozenInstanceError):
            sheet.rows = ()

    def test_revalidation_does_not_overwrite_fetch_time(self):
        result = result_at()
        checked = replace(result, checked_at=NOW + timedelta(hours=1))
        sheet = build_weather_sheet(checked, now=NOW)
        self.assertEqual(sheet.fetched_at, result.fetched_at)
        self.assertEqual(sheet.checked_at, checked.checked_at)
        self.assertIn("MET Norway", weather_lines(sheet)[-1])


class WeatherDetailsViewTests(unittest.TestCase):
    def test_gtk_and_print_model_use_same_projection(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from paperphone.weather_location_view import WeatherLocationView
        Gtk.init()
        view = WeatherLocationView(Mock(), clock=lambda: NOW)
        try:
            view._changed(WeatherLocationResult(WeatherLocationState.READY, 500,
                                               result_at(symbol="future_unknown")))
            self.assertEqual(view.forecast_details.get_text(), view.printable_item.fields["text"])
            self.assertIn("09:30", view.forecast_details.get_text())
            self.assertIn(_("Unknown weather condition"), view.forecast_details.get_text())
            view._changed(WeatherLocationResult(WeatherLocationState.FAILED))
            self.assertIsNone(view.printable_item)
            self.assertEqual(view.forecast_details.get_text(), "")
        finally:
            view.close()


if __name__ == "__main__":
    unittest.main()
