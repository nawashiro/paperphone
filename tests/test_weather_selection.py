"""完全一致・90分境界・30/45分時差・欠損・短期限定を検証。"""

from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from paperphone.weather import Forecast, parse_forecasts
from paperphone.weather_selection import remaining_targets, select_today_forecasts
from tests.test_weather import body, item

UTC = timezone.utc


def forecast(hour, minute=0, *, day=2, zone=UTC, duration=1):
    start = datetime(2026, 1, day, hour, minute, tzinfo=zone)
    return Forecast(start, 5, "cloudy", start + timedelta(hours=duration))


class WeatherSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 2, 6, tzinfo=UTC)

    def chosen(self, values, now=None):
        return [slot.forecast for slot in select_today_forecasts(values, now or self.now)]

    def test_exact_matches_keep_original_forecasts(self):
        values = [forecast(18), forecast(9), forecast(12)]
        slots = select_today_forecasts(values, self.now)
        self.assertEqual([s.target_hour for s in slots], [9, 12, 18])
        self.assertEqual([s.forecast for s in slots], [values[1], values[2], values[0]])
        self.assertIs(slots[0].forecast, values[1])

    def test_ties_prefer_later_for_thirty_and_forty_five_minute_zones(self):
        for name in ("Asia/Kolkata", "Asia/Kathmandu"):
            zone = ZoneInfo(name)
            now = self.now.astimezone(zone)
            for minute in (15, 30, 45):
                target = datetime(2026, 1, 2, 9, tzinfo=zone)
                starts = [target - timedelta(minutes=minute), target + timedelta(minutes=minute)]
                values = [Forecast(s.astimezone(UTC), 5, "cloudy", (s + timedelta(hours=1)).astimezone(UTC)) for s in starts]
                with self.subTest(zone=name, minute=minute):
                    self.assertIs(self.chosen(values, now)[0], values[1])

    def test_ninety_minute_boundary_inclusive_and_no_double_use(self):
        value = forecast(10, 30)
        self.assertEqual(self.chosen([value, value]), [value, None, None])
        self.assertEqual(self.chosen([forecast(10, 31)]), [None, forecast(10, 31), None])

    def test_missing_slots_are_not_filled_from_other_slots_or_days(self):
        self.assertEqual(self.chosen([forecast(12), forecast(18), forecast(9, day=1), forecast(9, day=3)]),
                         [None, forecast(12), forecast(18)])
        self.assertEqual(self.chosen([]), [None, None, None])

    def test_short_term_period_only(self):
        self.assertEqual(self.chosen([forecast(9, duration=6)]), [None, None, None])
        medium = item("2026-01-02T09:00:00Z")
        medium["data"]["next_6_hours"] = medium["data"].pop("next_1_hours")
        values = parse_forecasts(body(medium, item("2026-01-02T12:00:00Z")))
        self.assertEqual(self.chosen(values)[0], None)
        self.assertEqual(self.chosen(values)[1].time.hour, 12)

    def test_utc_day_is_not_the_local_day(self):
        zone = ZoneInfo("Asia/Tokyo")
        now = datetime(2026, 1, 3, 0, 30, tzinfo=zone)
        self.assertEqual(self.chosen([forecast(9)], now), [None, None, None])

    def test_timezone_aware_now_is_required(self):
        with self.assertRaises(ValueError):
            select_today_forecasts([], datetime(2026, 1, 2))

    def test_past_slot_is_not_invented_when_service_omits_it(self):
        now = self.now.replace(hour=16)
        self.assertEqual(self.chosen([forecast(18)], now), [None, None, forecast(18)])

    def test_targets_remain_until_last_window_closes(self):
        zone = ZoneInfo("Asia/Tokyo")
        # 18時枠の選択窓（前後90分）が閉じるまで取得は必要。
        self.assertTrue(remaining_targets(datetime(2026, 1, 2, 19, 29, tzinfo=zone)))
        self.assertFalse(remaining_targets(datetime(2026, 1, 2, 19, 31, tzinfo=zone)))
        self.assertFalse(remaining_targets(datetime(2026, 1, 2, 21, 53, tzinfo=zone)))

    def test_remaining_targets_requires_aware_now(self):
        with self.assertRaises(ValueError):
            remaining_targets(datetime(2026, 1, 2))


if __name__ == "__main__":
    unittest.main()
