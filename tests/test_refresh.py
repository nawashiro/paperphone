"""複数EDSソース更新・取消・読取専用経路のfixtureテスト。"""

from datetime import date, timezone
import unittest

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from paperphone.eds import EDSClientHandle, EDSResult, EDSState, SourceDescriptor, SourceKind
from paperphone.refresh import EDSRefreshController, RefreshState


TODO_FIXTURE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:fixture-todo
SUMMARY:Fixture TODO
END:VTODO
END:VCALENDAR
"""
CONTACT_FIXTURE = """BEGIN:VCARD
VERSION:3.0
UID:fixture-contact
FN:Fixture Contact
TEL:+1-555-0199
END:VCARD
"""


class FakeRegistry:
    def __init__(self, *, fail_open=(), defer_open=False):
        self.fail_open = set(fail_open)
        self.defer_open = defer_open
        self.open_calls = []
        self.read_calls = []
        self.pending_open = []
        self.todo_payload = TODO_FIXTURE

    def open_source_async(self, source, callback, *, cancellable=None, user_data=None):
        self.open_calls.append(source.kind)
        if self.defer_open:
            self.pending_open.append((source, callback, user_data))
            return
        self.complete_open(source, callback, user_data)

    def complete_open(self, source, callback, user_data=None):
        if source.uid in self.fail_open:
            result = EDSResult(EDSState.AUTHENTICATION_REQUIRED)
        else:
            handle = EDSClientHandle(source.kind, ":1.fixture", f"/client/{source.kind.value}")
            result = EDSResult(EDSState.READY, (handle,))
        callback(result, user_data)

    def call_read_async(self, handle, method, parameters, reply_type, callback, *, cancellable=None, user_data=None):
        self.read_calls.append((handle.kind, method, parameters))
        if method == "Open":
            response = GLib.Variant("(as)", ([],))
        elif method == "GetObjectList":
            if handle.kind is SourceKind.TASK_LIST:
                values = [self.todo_payload]
            else:
                values = []
            response = GLib.Variant("(as)", (values,))
        elif method == "GetContactList":
            response = GLib.Variant("(as)", ([CONTACT_FIXTURE],))
        else:
            raise AssertionError("Unexpected EDS read method")
        callback(EDSResult(EDSState.READY, (response,)), user_data)


def source(kind, uid):
    return SourceDescriptor(uid, f"Fixture {kind.value}", kind, True)


class RefreshControllerTest(unittest.TestCase):
    def test_reads_selected_source_kinds_and_returns_source_specific_results(self):
        registry = FakeRegistry()
        selected = (
            source(SourceKind.ADDRESS_BOOK, "book-source"),
            source(SourceKind.TASK_LIST, "task-source"),
            source(SourceKind.CALENDAR, "calendar-source"),
        )
        reports = []

        job = EDSRefreshController(registry).refresh_async(
            selected,
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )

        self.assertEqual(job.state, RefreshState.READY)
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.state, RefreshState.READY)
        self.assertEqual(len(report.sources), 3)
        self.assertEqual(
            {result.source.kind: result.state for result in report.sources},
            {
                SourceKind.ADDRESS_BOOK: EDSState.READY,
                SourceKind.TASK_LIST: EDSState.READY,
                SourceKind.CALENDAR: EDSState.EMPTY,
            },
        )
        self.assertEqual(
            {kind for kind, _method, _params in registry.read_calls},
            {SourceKind.ADDRESS_BOOK, SourceKind.TASK_LIST, SourceKind.CALENDAR},
        )
        self.assertTrue(
            all(method in {"Open", "GetObjectList", "GetContactList"} for _kind, method, _params in registry.read_calls)
        )
        self.assertNotIn("Fixture Contact", repr(report))
        self.assertNotIn("task-source", repr(report))

    def test_one_source_failure_does_not_stop_other_read_only_sources(self):
        registry = FakeRegistry(fail_open=("unavailable-book",))
        selected = (
            source(SourceKind.ADDRESS_BOOK, "unavailable-book"),
            source(SourceKind.TASK_LIST, "available-task-list"),
        )
        reports = []

        EDSRefreshController(registry).refresh_async(
            selected,
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )

        self.assertEqual(reports[0].state, RefreshState.PARTIAL)
        states = {item.source.uid: item.state for item in reports[0].sources}
        self.assertEqual(states["unavailable-book"], EDSState.AUTHENTICATION_REQUIRED)
        self.assertEqual(states["available-task-list"], EDSState.READY)

    def test_cancel_finishes_once_and_late_dbus_reply_is_ignored(self):
        registry = FakeRegistry(defer_open=True)
        reports = []
        selected_source = source(SourceKind.TASK_LIST, "fixture-task-list")
        job = EDSRefreshController(registry).refresh_async(
            (selected_source,),
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )

        self.assertEqual(job.state, RefreshState.LOADING)
        self.assertFalse(job.cancellable.is_cancelled())
        job.cancel()
        self.assertEqual(job.state, RefreshState.CANCELLED)
        self.assertTrue(job.cancellable.is_cancelled())
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].state, RefreshState.CANCELLED)

        source_value, callback, user_data = registry.pending_open[0]
        registry.complete_open(source_value, callback, user_data)
        self.assertEqual(len(reports), 1)
        self.assertEqual(registry.read_calls, [])

    def test_each_refresh_re_reads_external_todo_changes_without_caching(self):
        registry = FakeRegistry()
        controller = EDSRefreshController(registry)
        selected = (source(SourceKind.TASK_LIST, "task-source"),)
        reports = []

        controller.refresh_async(
            selected,
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )
        registry.todo_payload = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:fixture-todo
SUMMARY:Fixture TODO changed externally
END:VTODO
END:VCALENDAR
"""
        controller.refresh_async(
            selected,
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].sources[0].items[0].summary, "Fixture TODO")
        self.assertEqual(
            reports[1].sources[0].items[0].summary,
            "Fixture TODO changed externally",
        )
        self.assertEqual(registry.open_calls, [SourceKind.TASK_LIST, SourceKind.TASK_LIST])
        self.assertTrue(all(method in {"Open", "GetObjectList"} for _kind, method, _p in registry.read_calls))

    def test_empty_selection_finishes_without_opening_any_source(self):
        registry = FakeRegistry()
        reports = []

        EDSRefreshController(registry).refresh_async(
            (),
            date(2025, 3, 10),
            timezone.utc,
            lambda report, _data: reports.append(report),
        )

        self.assertEqual(reports[0].state, RefreshState.READY)
        self.assertEqual(reports[0].sources, ())
        self.assertEqual(registry.open_calls, [])

    def test_disabled_sources_are_not_read(self):
        disabled = SourceDescriptor("disabled-source", "Fixture Disabled", SourceKind.TASK_LIST, False)
        registry = FakeRegistry()

        with self.assertRaises(ValueError):
            EDSRefreshController(registry).refresh_async(
                (disabled,),
                date(2025, 3, 10),
                timezone.utc,
                lambda _report, _data: None,
            )
        self.assertEqual(registry.open_calls, [])


if __name__ == "__main__":
    unittest.main()
