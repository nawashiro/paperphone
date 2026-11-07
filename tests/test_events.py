"""予定取得・繰り返し展開の匿名fixtureテスト。"""

import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from paperphone.events import events_for_local_day, recurring_uids
from paperphone.temporal import DateOnly, ZonedDateTime


class CalendarEventExpansionTest(unittest.TestCase):
    def test_all_day_span_remains_date_only_across_system_zones(self):
        fixture = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:fixture-all-day
DTSTART;VALUE=DATE:20250308
DTEND;VALUE=DATE:20250310
SUMMARY:Fixture all day
END:VEVENT
END:VCALENDAR
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 9),
            system_tz=ZoneInfo("America/Los_Angeles"),
        )

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0].start, DateOnly)
        self.assertEqual(events[0].start.value, date(2025, 3, 8))
        self.assertEqual(events[0].end.value, date(2025, 3, 10))

    def test_overnight_timed_event_overlaps_following_local_day(self):
        fixture = """BEGIN:VEVENT
UID:fixture-overnight
DTSTART;TZID=America/New_York:20250308T233000
DTEND;TZID=America/New_York:20250309T013000
SUMMARY:Fixture overnight
END:VEVENT
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 9),
            system_tz=timezone.utc,
        )

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0].start, ZonedDateTime)
        self.assertEqual(events[0].start.value.astimezone(timezone.utc), datetime(2025, 3, 9, 4, 30, tzinfo=timezone.utc))

    def test_recurring_overnight_instances_include_previous_days_overlap(self):
        fixture = """BEGIN:VEVENT
UID:fixture-overnight-series
DTSTART:20250308T230000Z
DTEND:20250309T010000Z
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Fixture overnight series
END:VEVENT
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 9),
            system_tz=timezone.utc,
        )
        self.assertEqual(len(events), 2)
        self.assertEqual([event.start.value.day for event in events], [8, 9])

    def test_recurring_tzid_wall_time_survives_dst_transition(self):
        fixture = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:fixture-dst-series
DTSTART;TZID=America/New_York:20250308T090000
DTEND;TZID=America/New_York:20250308T100000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Fixture morning
END:VEVENT
END:VCALENDAR
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 9),
            system_tz=ZoneInfo("America/New_York"),
        )

        self.assertEqual(len(events), 1)
        value = events[0].start.value
        self.assertEqual((value.hour, value.minute), (9, 0))
        self.assertEqual(value.astimezone(timezone.utc).hour, 13)

    def test_exdate_removes_an_rrule_occurrence(self):
        fixture = """BEGIN:VEVENT
UID:fixture-exdate
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=3
EXDATE:20250311T090000Z
SUMMARY:Fixture excluded
END:VEVENT
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 11),
            system_tz=timezone.utc,
        )
        self.assertEqual(events, ())

    def test_this_and_future_override_changes_subsequent_instances(self):
        master = """BEGIN:VEVENT
UID:fixture-future-range
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Fixture before
END:VEVENT
"""
        override = """BEGIN:VEVENT
UID:fixture-future-range
RECURRENCE-ID;RANGE=THISANDFUTURE:20250311T090000Z
DTSTART:20250311T110000Z
DTEND:20250311T120000Z
SUMMARY:Fixture after
END:VEVENT
"""
        events = events_for_local_day(
            (master, override),
            date(2025, 3, 12),
            system_tz=timezone.utc,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start.value.hour, 11)
        self.assertEqual(events[0].summary, "Fixture after")

    def test_this_and_future_wall_time_shift_survives_dst_transition(self):
        master = """BEGIN:VEVENT
UID:fixture-future-dst
DTSTART;TZID=America/New_York:20250308T013000
DTEND;TZID=America/New_York:20250308T023000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Fixture future DST
END:VEVENT
"""
        override = """BEGIN:VEVENT
UID:fixture-future-dst
RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=America/New_York:20250308T013000
DTSTART;TZID=America/New_York:20250308T033000
DTEND;TZID=America/New_York:20250308T043000
SUMMARY:Fixture shifted
END:VEVENT
"""
        events = events_for_local_day(
            (master, override),
            date(2025, 3, 9),
            system_tz=ZoneInfo("America/New_York"),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].start.value.hour, events[0].start.value.minute), (3, 30))

    def test_recurring_duration_is_elapsed_time_across_dst(self):
        fixture = """BEGIN:VEVENT
UID:fixture-dst-duration
DTSTART;TZID=America/New_York:20250309T013000
DTEND;TZID=America/New_York:20250309T033000
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Fixture DST duration
END:VEVENT
"""
        events = events_for_local_day(
            (fixture,),
            date(2025, 3, 10),
            system_tz=ZoneInfo("America/New_York"),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start.value.hour, 1)
        self.assertEqual(events[0].end.value.hour, 2)
        self.assertEqual(
            events[0].end.value.astimezone(timezone.utc) - events[0].start.value.astimezone(timezone.utc),
            timedelta(hours=1),
        )

    def test_detached_change_replaces_original_instance_without_duplicate(self):
        master = """BEGIN:VEVENT
UID:fixture-series
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Original title
END:VEVENT
"""
        override = """BEGIN:VEVENT
UID:fixture-series
RECURRENCE-ID:20250311T090000Z
DTSTART:20250311T110000Z
DTEND:20250311T120000Z
SUMMARY:Changed title
END:VEVENT
"""
        events = events_for_local_day(
            (master, override),
            date(2025, 3, 11),
            system_tz=timezone.utc,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].summary, "Changed title")
        self.assertEqual(events[0].start.value.hour, 11)
        self.assertEqual(events[0].end.value.hour, 12)

    def test_modified_exception_without_dtend_inherits_series_duration(self):
        master = """BEGIN:VEVENT
UID:fixture-inherited-duration
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Fixture original
END:VEVENT
"""
        override = """BEGIN:VEVENT
UID:fixture-inherited-duration
RECURRENCE-ID:20250311T090000Z
DTSTART:20250311T110000Z
SUMMARY:Fixture moved
END:VEVENT
"""
        events = events_for_local_day(
            (master, override),
            date(2025, 3, 11),
            system_tz=timezone.utc,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start.value.hour, 11)
        self.assertEqual(events[0].end.value.hour, 12)

    def test_moved_exception_into_today_is_included_by_recurrence_id(self):
        master = """BEGIN:VEVENT
UID:fixture-moved
DTSTART:20250309T090000Z
DTEND:20250309T100000Z
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Fixture series
END:VEVENT
"""
        moved = """BEGIN:VEVENT
UID:fixture-moved
RECURRENCE-ID:20250309T090000Z
DTSTART:20250312T140000Z
DTEND:20250312T150000Z
SUMMARY:Moved occurrence
END:VEVENT
"""
        events = events_for_local_day(
            (master, moved),
            date(2025, 3, 12),
            system_tz=timezone.utc,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].summary, "Moved occurrence")
        self.assertEqual(events[0].start.value.hour, 14)

    def test_cancelled_detached_occurrence_is_removed(self):
        master = """BEGIN:VEVENT
UID:fixture-cancelled
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Fixture series
END:VEVENT
"""
        cancelled = """BEGIN:VEVENT
UID:fixture-cancelled
RECURRENCE-ID:20250311T090000Z
STATUS:CANCELLED
SUMMARY:Fixture series
END:VEVENT
"""
        events = events_for_local_day(
            (master, cancelled),
            date(2025, 3, 11),
            system_tz=timezone.utc,
        )

        self.assertEqual(events, ())

    def test_duplicate_components_do_not_duplicate_instances(self):
        fixture = """BEGIN:VEVENT
UID:fixture-duplicate
DTSTART:20250311T090000Z
DTEND:20250311T100000Z
SUMMARY:Fixture duplicate
END:VEVENT
"""
        events = events_for_local_day(
            (fixture, fixture),
            date(2025, 3, 11),
            system_tz=timezone.utc,
        )
        self.assertEqual(len(events), 1)
        self.assertNotIn("fixture-duplicate", repr(events[0]))
        self.assertNotIn("Fixture duplicate", repr(events[0]))

    def test_recurring_source_uids_include_detached_exception_series(self):
        override = """BEGIN:VEVENT
UID:fixture-exception-series
RECURRENCE-ID:20250311T090000Z
DTSTART:20250311T100000Z
DTEND:20250311T110000Z
END:VEVENT
"""
        self.assertEqual(recurring_uids((override,)), ("fixture-exception-series",))


if __name__ == "__main__":
    unittest.main()
