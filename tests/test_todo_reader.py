"""VTODOのEDS読取・日時パースfixtureテスト。"""

from datetime import date, datetime, timezone
import unittest
from zoneinfo import ZoneInfo

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from paperphone.eds import EDSClientHandle, EDSResult, EDSState, SourceKind
from paperphone.temporal import DateOnly, FloatingDateTime, ZonedDateTime
from paperphone.todo import TodoBucket
from paperphone.todo_reader import (
    ALL_TODOS_QUERY,
    TodoListReadAdapter,
    parse_todos,
)


def vtodo(uid, *, summary="Fixture task", fields=()):
    content = ["BEGIN:VTODO", "UID:" + uid]
    if summary is not None:
        content.append("SUMMARY:" + summary)
    content.extend(fields)
    content.append("END:VTODO")
    return "\r\n".join(content)


def calendar(*components):
    return "\r\n".join(("BEGIN:VCALENDAR", "VERSION:2.0", *components, "END:VCALENDAR"))


class FakeTaskListRegistry:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call_read_async(self, handle, method, parameters, reply_type, callback, *, cancellable=None, user_data=None):
        self.calls.append((handle, method, parameters, reply_type))
        if method == "Open":
            reply = GLib.Variant("(as)", ([],))
            result = EDSResult(EDSState.READY, (reply,))
        elif method == "GetObjectList":
            result = self.response
        else:
            raise AssertionError("Unexpected EDS method")
        callback(result, user_data)


class TodoVCardParserTest(unittest.TestCase):
    def test_parses_date_only_zoned_and_floating_values(self):
        date_only = vtodo(
            "todo-date-only",
            summary="日本語のタスク",
            fields=("DTSTART;VALUE=DATE:20250310", "DUE;VALUE=DATE:20250311"),
        )
        zoned = vtodo(
            "todo-zoned",
            fields=(
                "DTSTART;TZID=America/New_York:20250310T090000",
                "DUE;TZID=America/New_York:20250310T100000",
            ),
        )
        floating = vtodo("todo-floating", fields=("DTSTART:20250310T090000",))

        todos = {
            todo.todo_uid: todo
            for todo in parse_todos(
                (calendar(date_only, zoned, floating),),
                source_uid="task-list-fixture",
            )
        }

        self.assertEqual(todos["todo-date-only"].summary, "日本語のタスク")
        self.assertEqual(todos["todo-date-only"].start, DateOnly(date(2025, 3, 10)))
        self.assertEqual(todos["todo-date-only"].due, DateOnly(date(2025, 3, 11)))
        self.assertEqual(
            todos["todo-zoned"].start,
            ZonedDateTime(datetime(2025, 3, 10, 9, tzinfo=ZoneInfo("America/New_York"))),
        )
        self.assertEqual(
            todos["todo-floating"].start,
            FloatingDateTime(datetime(2025, 3, 10, 9)),
        )

    def test_completion_status_timestamp_percent_and_cancelled_items_are_marked(self):
        completed_status = vtodo("completed-status", fields=("STATUS:COMPLETED",))
        completed_at = vtodo("completed-at", fields=("COMPLETED:20250310T100000Z",))
        percent = vtodo("completed-percent", fields=("PERCENT-COMPLETE:100",))
        cancelled = vtodo("cancelled", fields=("STATUS:CANCELLED",))
        active = vtodo("active", fields=("PERCENT-COMPLETE:50",))

        todos = {
            todo.todo_uid: todo
            for todo in parse_todos(
                (calendar(completed_status, completed_at, percent, cancelled, active),),
                source_uid="task-list-fixture",
            )
        }

        self.assertTrue(todos["completed-status"].completed)
        self.assertTrue(todos["completed-at"].completed)
        self.assertIsInstance(todos["completed-at"].completed_at, ZonedDateTime)
        self.assertTrue(todos["completed-percent"].completed)
        self.assertTrue(todos["cancelled"].completed)
        self.assertFalse(todos["active"].completed)

    def test_tasks_without_start_or_due_are_preserved_and_repr_is_redacted(self):
        todo = parse_todos(
            (vtodo("private-todo-uid", summary="Private Fixture Task"),),
            source_uid="private-task-source",
        )[0]

        self.assertIsNone(todo.start)
        self.assertIsNone(todo.due)
        for private_value in ("private-todo-uid", "private-task-source", "Private Fixture Task"):
            self.assertNotIn(private_value, repr(todo))
            self.assertNotIn(
                private_value,
                repr(todo.as_selectable_item(TodoBucket.UNDATED, system_tz=timezone.utc)),
            )

    def test_malformed_payload_does_not_escape_contents(self):
        with self.assertRaises(ValueError) as raised:
            parse_todos(("fixture secret that is not an icalendar component",), source_uid="source")
        self.assertNotIn("fixture secret", str(raised.exception))


class TodoListReadAdapterTest(unittest.TestCase):
    @staticmethod
    def handle():
        return EDSClientHandle(SourceKind.TASK_LIST, ":1.fixture", "/task-list/fixture")

    def test_reader_includes_all_unfinished_tasks_and_never_calls_writes(self):
        active = vtodo("active-todo", summary="Fixture active", fields=("STATUS:NEEDS-ACTION",))
        completed = vtodo("completed-todo", summary="Fixture done", fields=("STATUS:COMPLETED",))
        reply = GLib.Variant("(as)", ([calendar(active, completed)],))
        registry = FakeTaskListRegistry(EDSResult(EDSState.READY, (reply,)))
        results = []

        TodoListReadAdapter(registry).read_todos_async(
            self.handle(),
            "task-source-fixture",
            lambda result, _data: results.append(result),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, EDSState.READY)
        self.assertEqual([todo.todo_uid for todo in results[0].items], ["active-todo"])
        self.assertEqual([call[1] for call in registry.calls], ["Open", "GetObjectList"])
        self.assertEqual(registry.calls[1][2].unpack(), (ALL_TODOS_QUERY,))
        self.assertTrue(all(method in {"Open", "GetObjectList"} for _h, method, _p, _r in registry.calls))
        self.assertNotIn("Fixture active", repr(results[0]))
        self.assertNotIn("task-source-fixture", repr(results[0]))

    def test_empty_or_only_completed_list_is_empty(self):
        for payloads in ((), (calendar(vtodo("done", fields=("STATUS:COMPLETED",))),)):
            with self.subTest(payload_count=len(payloads)):
                reply = GLib.Variant("(as)", (list(payloads),))
                results = []
                TodoListReadAdapter(FakeTaskListRegistry(EDSResult(EDSState.READY, (reply,)))).read_todos_async(
                    self.handle(),
                    "task-list-fixture",
                    lambda result, _data: results.append(result),
                )
                self.assertEqual(results[0].state, EDSState.EMPTY)

    def test_failed_open_is_reported_without_payload_or_second_call(self):
        class FailedRegistry:
            def __init__(self):
                self.calls = []

            def call_read_async(self, handle, method, parameters, reply_type, callback, *, cancellable=None, user_data=None):
                self.calls.append(method)
                callback(EDSResult(EDSState.AUTHENTICATION_REQUIRED), user_data)

        registry = FailedRegistry()
        results = []
        TodoListReadAdapter(registry).read_todos_async(
            self.handle(),
            "task-list-fixture",
            lambda result, _data: results.append(result),
        )
        self.assertEqual(registry.calls, ["Open"])
        self.assertEqual(results, [EDSResult(EDSState.AUTHENTICATION_REQUIRED)])

    def test_calendar_handle_is_rejected_before_eds_calls(self):
        registry = FakeTaskListRegistry(EDSResult(EDSState.EMPTY))
        with self.assertRaises(ValueError):
            TodoListReadAdapter(registry).read_todos_async(
                EDSClientHandle(SourceKind.CALENDAR, ":1.fixture", "/calendar"),
                "calendar-source",
                lambda _result, _data: None,
            )
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
