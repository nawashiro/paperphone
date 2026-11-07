"""日時型とシステムタイムゾーン変換の境界テスト。"""

import os
from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from paperphone.temporal import (
    DateOnly,
    FloatingDateTime,
    ZonedDateTime,
    system_timezone,
    to_system_local,
)


class TemporalModelTest(unittest.TestCase):
    def test_date_only_is_never_shifted_by_timezone_conversion(self):
        value = DateOnly(date(2024, 1, 1))

        self.assertEqual(
            to_system_local(value, system_tz=ZoneInfo("Pacific/Honolulu")),
            date(2024, 1, 1),
        )

    def test_zoned_datetime_converts_same_instant_to_japan(self):
        value = ZonedDateTime(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))

        result = to_system_local(value, system_tz=ZoneInfo("Asia/Tokyo"))

        self.assertEqual(result, datetime(2024, 1, 1, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo")))

    def test_utc_conversion_preserves_utc_wall_time(self):
        value = ZonedDateTime(datetime(2024, 6, 15, 12, 30, tzinfo=timezone.utc))

        self.assertEqual(to_system_local(value, system_tz=ZoneInfo("UTC")), value.value)

    def test_daylight_saving_uses_offset_for_target_datetime(self):
        zone = ZoneInfo("Europe/Berlin")
        winter = ZonedDateTime(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc))
        summer = ZonedDateTime(datetime(2024, 7, 15, 12, 0, tzinfo=timezone.utc))

        winter_local = to_system_local(winter, system_tz=zone)
        summer_local = to_system_local(summer, system_tz=zone)

        self.assertEqual(winter_local.hour, 13)
        self.assertEqual(summer_local.hour, 14)
        self.assertEqual(winter_local.utcoffset().total_seconds(), 3600)
        self.assertEqual(summer_local.utcoffset().total_seconds(), 7200)

    def test_non_hour_offsets_of_thirty_and_forty_five_minutes(self):
        instant = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        india = to_system_local(ZonedDateTime(instant), system_tz=ZoneInfo("Asia/Kolkata"))
        nepal = to_system_local(ZonedDateTime(instant), system_tz=ZoneInfo("Asia/Kathmandu"))

        self.assertEqual(india.utcoffset().total_seconds(), 5 * 3600 + 30 * 60)
        self.assertEqual(nepal.utcoffset().total_seconds(), 5 * 3600 + 45 * 60)

    def test_floating_datetime_keeps_wall_clock_fields(self):
        value = FloatingDateTime(datetime(2024, 7, 1, 9, 45))

        result = to_system_local(value, system_tz=ZoneInfo("Asia/Tokyo"))

        self.assertEqual((result.year, result.month, result.day, result.hour, result.minute), (2024, 7, 1, 9, 45))
        self.assertEqual(result.utcoffset().total_seconds(), 9 * 3600)

    def test_system_timezone_uses_tz_environment_override(self):
        with patch.dict(os.environ, {"TZ": "Asia/Kathmandu"}):
            zone = system_timezone()

        self.assertEqual(datetime(2024, 1, 1, tzinfo=zone).utcoffset().total_seconds(), 5 * 3600 + 45 * 60)

    def test_models_reject_values_with_the_wrong_temporal_kind(self):
        with self.assertRaises(TypeError):
            DateOnly(datetime(2024, 1, 1))
        with self.assertRaises(ValueError):
            ZonedDateTime(datetime(2024, 1, 1))
        with self.assertRaises(ValueError):
            FloatingDateTime(datetime(2024, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
