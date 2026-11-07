"""EDSカレンダー非同期読取のfixtureテスト。"""

import unittest
from datetime import date, timezone
from zoneinfo import ZoneInfo

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from paperphone.calendar_reader import CalendarReadAdapter, local_day_query
from paperphone.eds import EDSClientHandle, EDSResult, EDSState, SourceKind


class FakeEDSAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call_read_async(
        self,
        handle,
        method,
        parameters,
        reply_type,
        callback,
        *,
        cancellable=None,
    ):
        self.calls.append((method, parameters, reply_type))
        response = self.responses.pop(0)
        callback(response, None)


def object_list(*payloads):
    return EDSResult(EDSState.READY, (GLib.Variant("(as)", (list(payloads),)),))


def single_object(payload):
    return EDSResult(EDSState.READY, (GLib.Variant("(s)", (payload,)),))


class CalendarReadAdapterTest(unittest.TestCase):
    def test_local_day_query_uses_actual_23_hour_dst_day(self):
        query = local_day_query(date(2025, 3, 9), ZoneInfo("America/Los_Angeles"))
        self.assertEqual(
            query,
            '(occur-in-time-range? (make-time "20250309T080000Z") '
            '(make-time "20250310T070000Z"))',
        )

    def test_reader_opens_then_queries_range_and_fetches_series_exceptions(self):
        master = """BEGIN:VEVENT
UID:fixture-reader-series
DTSTART:20250310T090000Z
DTEND:20250310T100000Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Fixture original
END:VEVENT
"""
        override = """BEGIN:VEVENT
UID:fixture-reader-series
RECURRENCE-ID:20250311T090000Z
DTSTART:20250311T110000Z
DTEND:20250311T120000Z
SUMMARY:Fixture changed
END:VEVENT
"""
        opened = EDSResult(EDSState.READY, (GLib.Variant("(as)", ([],)),))
        series = f"BEGIN:VCALENDAR\nVERSION:2.0\n{master}{override}END:VCALENDAR\n"
        fake = FakeEDSAdapter((opened, object_list(master), single_object(series)))
        handle = EDSClientHandle(SourceKind.CALENDAR, ":1.fixture", "/client/fixture")
        results = []

        CalendarReadAdapter(fake).read_today_async(
            handle,
            date(2025, 3, 11),
            timezone.utc,
            lambda result, _data: results.append(result),
        )

        self.assertEqual([call[0] for call in fake.calls], ["Open", "GetObjectList", "GetObject"])
        self.assertIn("occur-in-time-range?", fake.calls[1][1].unpack()[0])
        self.assertEqual(fake.calls[2][1].unpack(), ("fixture-reader-series", ""))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, EDSState.READY)
        self.assertEqual(len(results[0].items), 1)
        self.assertEqual(results[0].items[0].summary, "Fixture changed")
        self.assertEqual(results[0].items[0].start.value.hour, 11)

    def test_empty_calendar_is_distinct_from_unregistered_source(self):
        opened = EDSResult(EDSState.READY, (GLib.Variant("(as)", ([],)),))
        fake = FakeEDSAdapter((opened, object_list()))
        results = []
        CalendarReadAdapter(fake).read_today_async(
            EDSClientHandle(SourceKind.CALENDAR, ":1.fixture", "/client/fixture"),
            date(2025, 3, 11),
            timezone.utc,
            lambda result, _data: results.append(result),
        )
        self.assertEqual(results[0].state, EDSState.EMPTY)
        self.assertEqual(len(fake.calls), 2)

    def test_non_calendar_handle_is_rejected_before_any_call(self):
        fake = FakeEDSAdapter(())
        with self.assertRaises(ValueError):
            CalendarReadAdapter(fake).read_today_async(
                EDSClientHandle(SourceKind.TASK_LIST, ":1.fixture", "/client/fixture"),
                date(2025, 3, 11),
                timezone.utc,
                lambda _result, _data: None,
            )
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
