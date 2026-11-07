"""CalDAV同期開始と変更通知の順序・取消・未確認状態のfixture。"""

from datetime import date, timezone
from unittest.mock import patch
import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from paperphone.eds import EDSClientHandle, EDSResult, EDSState, SourceDescriptor, SourceKind
from paperphone.refresh import EDSRefreshController, RefreshState
from paperphone.todo_reader import TodoListReadAdapter


class Registry:
    def __init__(self):
        self.calls = []
        self.signal_callback = None
        self.refresh_callback = None
        self.payload = "BEGIN:VTODO\r\nUID:fixture\r\nSUMMARY:Before\r\nEND:VTODO"
        self.defer_view = False
        self.defer_read = False

    def open_source_async(self, source, callback, **kwargs):
        callback(EDSResult(EDSState.READY, (self.handle,)), None)

    @property
    def handle(self):
        return EDSClientHandle(SourceKind.TASK_LIST, ":1.fixture", "/client")

    def call_read_async(self, handle, method, parameters, reply_type, callback, **kwargs):
        self.calls.append(method)
        if method == "Open":
            response = GLib.Variant("(as)", ([],))
        elif method == "GetView":
            response = GLib.Variant("(o)", ("/view",))
            if self.defer_view:
                self.view_callback = callback
                return
        elif method == "GetObjectList":
            response = GLib.Variant("(as)", ([self.payload],))
            if self.defer_read:
                self.read_callback = callback
                self.read_response = response
                return
        else:
            raise AssertionError(method)
        callback(EDSResult(EDSState.READY, (response,)), None)

    def subscribe_calendar_view(self, handle, path, callback):
        self.calls.append("Subscribe")
        self.signal_callback = callback
        return 1

    def unsubscribe_calendar_view(self, subscription_id):
        self.calls.append("Unsubscribe")

    def call_calendar_view_async(self, handle, path, method, callback, **kwargs):
        self.calls.append(method)
        callback(EDSResult(EDSState.READY), None)

    def refresh_calendar_async(self, handle, callback, **kwargs):
        self.calls.append("Refresh")
        self.refresh_callback = callback

    def emit(self, name, params=None):
        self.signal_callback(None, ":1.fixture", "/view", "fixture", name,
                             params or GLib.Variant("(as)", ([self.payload],)), None)

    def complete_initial(self):
        self.emit("Complete", GLib.Variant("(ss)", ("", "")))

    def accept_refresh(self, state=EDSState.READY):
        self.refresh_callback(EDSResult(state), None)


class CalDAVRefreshTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.results = []
        self.cancel = Gio.Cancellable.new()
        TodoListReadAdapter(self.registry).read_todos_async(
            self.registry.handle, "fixture-source",
            lambda result, _data: self.results.append(result),
            backend_name="caldav", cancellable=self.cancel,
        )

    def tearDown(self):
        self.cancel.cancel()
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_refresh_reply_and_initial_objects_are_not_sync_completion(self):
        self.registry.emit("ObjectsAdded")
        self.assertNotIn("Refresh", self.registry.calls)
        self.registry.complete_initial()
        self.registry.accept_refresh()
        self.assertEqual(self.results, [])
        self.assertNotIn("GetObjectList", self.registry.calls)
        self.registry.payload = self.registry.payload.replace("Before", "After")
        self.registry.emit("ObjectsModified")
        self.assertEqual(self.results[0].items[0].summary, "After")
        self.assertEqual(self.registry.calls[-3:], ["GetObjectList", "Unsubscribe", "Dispose"])

    def test_change_before_refresh_reply_is_not_lost(self):
        self.registry.complete_initial()
        self.registry.emit("ObjectsModified")
        self.assertEqual(self.results, [])
        self.registry.accept_refresh()
        self.assertEqual(self.results[0].state, EDSState.READY)
        self.registry.emit("ObjectsModified")
        self.assertEqual(len(self.results), 1)

    def test_notification_during_read_retries_instead_of_returning_stale_data(self):
        self.registry.complete_initial()
        self.registry.accept_refresh()
        self.registry.defer_read = True
        self.registry.emit("ObjectsModified")
        self.registry.payload = self.registry.payload.replace("Before", "After")
        self.registry.emit("ObjectsModified")
        self.registry.defer_read = False
        self.registry.read_callback(EDSResult(EDSState.READY, (self.registry.read_response,)), None)
        self.assertEqual(self.results[0].items[0].summary, "After")
        self.assertEqual(self.registry.calls.count("GetObjectList"), 2)

    def test_completed_and_deleted_notifications_re_read_candidates(self):
        self.registry.complete_initial()
        self.registry.accept_refresh()
        self.registry.payload = self.registry.payload.replace("END:VTODO", "STATUS:COMPLETED\r\nEND:VTODO")
        self.registry.emit("ObjectsRemoved")
        self.assertEqual(self.results[0].state, EDSState.EMPTY)
        self.assertEqual(self.results[0].items, ())

    def test_no_change_timeout_keeps_cache_and_marks_freshness_unconfirmed(self):
        self.registry.complete_initial()
        with patch("paperphone.todo_reader.GLib.timeout_add") as schedule:
            self.registry.accept_refresh()
            timeout_callback = schedule.call_args.args[1]
        timeout_callback()
        self.assertEqual(self.results[0].state, EDSState.REFRESH_UNCONFIRMED)
        self.assertEqual(self.results[0].items[0].summary, "Before")

    def test_refresh_auth_failure_retains_cache_without_claiming_freshness(self):
        self.registry.complete_initial()
        self.registry.accept_refresh(EDSState.AUTHENTICATION_REQUIRED)
        self.assertEqual(self.results[0].state, EDSState.AUTHENTICATION_REQUIRED)
        self.assertEqual(len(self.results[0].items), 1)

    def test_cancel_disposes_view_and_ignores_late_signals(self):
        self.registry.complete_initial()
        self.cancel.cancel()
        self.assertEqual(self.registry.calls[-2:], ["Unsubscribe", "Dispose"])
        self.registry.accept_refresh()
        self.registry.emit("ObjectsModified")
        self.assertNotIn("GetObjectList", self.registry.calls)
        self.assertEqual(len(self.results), 1)

    def test_setup_timeout_is_bounded(self):
        self.cancel.cancel()
        registry = Registry()
        results = []
        with patch("paperphone.todo_reader.GLib.timeout_add") as schedule:
            TodoListReadAdapter(registry).read_todos_async(
                registry.handle, "source", lambda result, _: results.append(result),
                backend_name="caldav",
            )
            schedule.call_args.args[1]()
        self.assertEqual(results[0].state, EDSState.COMMUNICATION_ERROR)
        self.assertEqual(registry.calls[-2:], ["Unsubscribe", "Dispose"])

    def test_cancellation_before_get_view_reply_disposes_late_view(self):
        self.cancel.cancel()
        registry = Registry()
        registry.defer_view = True
        cancel = Gio.Cancellable.new()
        results = []
        TodoListReadAdapter(registry).read_todos_async(
            registry.handle, "source", lambda result, _: results.append(result),
            backend_name="caldav", cancellable=cancel,
        )
        cancel.cancel()
        registry.view_callback(EDSResult(EDSState.READY, (GLib.Variant("(o)", ("/view",)),)), None)
        self.assertEqual(registry.calls[-1], "Dispose")
        self.assertNotIn("Subscribe", registry.calls)
        self.assertEqual(len(results), 1)

    def test_controller_routes_caldav_and_preserves_cancelled_state(self):
        self.cancel.cancel()
        registry = Registry()
        reports = []
        job = EDSRefreshController(registry).refresh_async(
            (SourceDescriptor("source", "Fixture", SourceKind.TASK_LIST, True, "caldav"),),
            date(2025, 3, 10), timezone.utc,
            lambda report, _: reports.append(report),
        )
        registry.complete_initial()
        job.cancel()
        self.assertTrue(job.cancellable.is_cancelled())
        self.assertEqual(reports[0].state, RefreshState.CANCELLED)
        self.assertEqual(len(reports), 1)
        self.assertEqual(registry.calls[-2:], ["Unsubscribe", "Dispose"])


if __name__ == "__main__":
    unittest.main()
