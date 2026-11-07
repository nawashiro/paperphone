"""EDSソース列挙・状態区別のfixtureテスト。"""

import unittest

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from paperphone.eds import (
    EDSClientHandle,
    EDSRegistryAdapter,
    EDSResult,
    EDSState,
    SOURCE_BUS_NAME,
    SOURCE_INTERFACE,
    SOURCE_MANAGER_PATH,
    SourceDescriptor,
    SourceKind,
    classify_eds_error,
    parse_managed_sources,
    result_for_read,
)


class FakeReply:
    def __init__(self, objects):
        self.objects = objects

    def unpack(self):
        return self.objects if isinstance(self.objects, tuple) else (self.objects,)


class FakeAsyncResult:
    def __init__(self, *, reply=None, error=None):
        self.reply = reply
        self.error = error


class FakeConnection:
    def call(self, *args):
        self.args = args
        self.callback = args[-2]
        self.user_data = args[-1]

    def call_finish(self, result):
        if result.error is not None:
            raise result.error
        return result.reply

    def complete(self, result):
        self.callback(self, result, self.user_data)


def source_object(uid, data):
    return {
        SOURCE_INTERFACE: {
            "UID": GLib.Variant("s", uid),
            "Data": GLib.Variant("s", data),
        }
    }


class EDSRegistryAdapterTest(unittest.TestCase):
    def test_list_sources_is_async_read_only_object_manager_call(self):
        connection = FakeConnection()
        adapter = EDSRegistryAdapter(connection)
        results = []
        adapter.list_sources_async(SourceKind.CALENDAR, lambda result, _data: results.append(result))

        self.assertEqual(connection.args[0], SOURCE_BUS_NAME)
        self.assertEqual(connection.args[1], SOURCE_MANAGER_PATH)
        self.assertEqual(connection.args[2], "org.freedesktop.DBus.ObjectManager")
        self.assertEqual(connection.args[3], "GetManagedObjects")

        connection.complete(FakeAsyncResult(reply=FakeReply({})))
        self.assertEqual(results, [EDSResult(EDSState.UNREGISTERED)])

    def test_async_enumeration_returns_fixture_sources_without_raw_data(self):
        connection = FakeConnection()
        results = []
        fixture_data = (
            "[Data Source]\nDisplayName=Fixture Calendar\nEnabled=true\n"
            "[Calendar]\nBackendName=local\n[Authentication]\nUser=fixture-only\n"
        )
        objects = {"/source/fixture": source_object("uid-fixture", fixture_data)}
        EDSRegistryAdapter(connection).list_sources_async(
            SourceKind.CALENDAR,
            lambda result, _data: results.append(result),
        )

        connection.complete(FakeAsyncResult(reply=FakeReply(objects)))
        self.assertEqual(results[0].state, EDSState.READY)
        self.assertEqual(results[0].items[0].display_name, "Fixture Calendar")
        self.assertNotIn("fixture-only", repr(results[0]))
        self.assertNotIn("uid-fixture", repr(results[0]))
        self.assertNotIn("Fixture Calendar", repr(results[0]))

    def test_open_source_uses_matching_factory_and_returns_ephemeral_handle(self):
        cases = (
            (SourceKind.CALENDAR, "org.gnome.evolution.dataserver.Calendar8", "OpenCalendar"),
            (SourceKind.TASK_LIST, "org.gnome.evolution.dataserver.Calendar8", "OpenTaskList"),
            (SourceKind.ADDRESS_BOOK, "org.gnome.evolution.dataserver.AddressBook10", "OpenAddressBook"),
        )
        for kind, expected_service, expected_method in cases:
            with self.subTest(kind=kind):
                connection = FakeConnection()
                adapter = EDSRegistryAdapter(connection)
                results = []
                source = SourceDescriptor("uid-fixture", "Fixture", kind, True)

                adapter.open_source_async(source, lambda result, _data: results.append(result))

                self.assertEqual(connection.args[0], expected_service)
                self.assertEqual(connection.args[3], expected_method)
                self.assertEqual(connection.args[4].unpack(), ("uid-fixture",))
                connection.complete(FakeAsyncResult(reply=FakeReply(("/client/fixture", ":1.fixture"))))
                self.assertEqual(results[0].state, EDSState.READY)
                self.assertEqual(results[0].items[0].kind, kind)
                self.assertEqual(results[0].items[0].object_path, "/client/fixture")

    def test_read_adapter_allows_reads_and_rejects_write_methods(self):
        connection = FakeConnection()
        adapter = EDSRegistryAdapter(connection)
        handle = EDSClientHandle(SourceKind.CALENDAR, ":1.fixture", "/client/fixture")
        results = []

        adapter.call_read_async(
            handle,
            "GetObjectList",
            GLib.Variant("(s)", ('(contains? "summary" "fixture")',)),
            GLib.VariantType.new("(as)"),
            lambda result, _data: results.append(result),
        )
        self.assertEqual(connection.args[2], "org.gnome.evolution.dataserver.Calendar")
        self.assertEqual(connection.args[3], "GetObjectList")
        connection.complete(FakeAsyncResult(reply=FakeReply(([],))))
        self.assertEqual(results[0].state, EDSState.READY)

        adapter.call_read_async(
            handle,
            "GetObject",
            GLib.Variant("(ss)", ("uid-fixture", "")),
            GLib.VariantType.new("(s)"),
            lambda result, _data: results.append(result),
        )
        self.assertEqual(connection.args[3], "GetObject")
        connection.complete(FakeAsyncResult(reply=FakeReply(("fixture payload",))))
        self.assertEqual(results[1].state, EDSState.READY)
        self.assertNotIn("fixture payload", repr(results[1]))

        calls_before = connection.args
        with self.assertRaises(ValueError):
            adapter.call_read_async(
                handle,
                "ModifyObjects",
                None,
                None,
                lambda _result, _data: None,
            )
        self.assertIs(connection.args, calls_before)

    def test_refresh_and_view_lifecycle_are_separate_from_object_writes(self):
        connection = FakeConnection()
        adapter = EDSRegistryAdapter(connection)
        handle = EDSClientHandle(SourceKind.TASK_LIST, ":1.fixture", "/client")
        results = []
        adapter.refresh_calendar_async(handle, lambda result, _: results.append(result))
        self.assertEqual(connection.args[2:4], ("org.gnome.evolution.dataserver.Calendar", "Refresh"))
        self.assertEqual(connection.args[5].dup_string(), "()")
        connection.complete(FakeAsyncResult(reply=FakeReply(())))
        self.assertEqual(results[0].state, EDSState.READY)
        for method in ("Start", "Dispose"):
            adapter.call_calendar_view_async(handle, "/view", method, lambda *_: None)
            self.assertEqual(connection.args[1:4], ("/view", "org.gnome.evolution.dataserver.CalendarView", method))
        for method in ("CreateObjects", "ModifyObjects", "RemoveObjects", "Refresh"):
            with self.assertRaises(ValueError):
                adapter.call_read_async(handle, method, None, None, lambda *_: None)
            with self.assertRaises(ValueError):
                adapter.call_calendar_view_async(handle, "/view", method, lambda *_: None)

    def test_view_subscription_is_scoped_to_the_client_and_object(self):
        class SignalConnection(FakeConnection):
            def signal_subscribe(self, *args):
                self.signal_args = args
                return 42

            def signal_unsubscribe(self, subscription_id):
                self.unsubscribed = subscription_id

        connection = SignalConnection()
        adapter = EDSRegistryAdapter(connection)
        handle = EDSClientHandle(SourceKind.TASK_LIST, ":1.fixture", "/client")
        subscription = adapter.subscribe_calendar_view(handle, "/view", lambda *_: None)
        self.assertEqual(connection.signal_args[:4], (
            ":1.fixture", "org.gnome.evolution.dataserver.CalendarView", None, "/view",
        ))
        adapter.unsubscribe_calendar_view(subscription)
        self.assertEqual(connection.unsubscribed, 42)

    def test_parser_reads_only_requested_extension_and_minimal_metadata(self):
        calendar_data = (
            "[Data Source]\nDisplayName=Fixture Calendar\nEnabled=true\n"
            "[Calendar]\nBackendName=local\n[Authentication]\nUser=fixture-only\n"
        )
        book_data = (
            "[Data Source]\nDisplayName=Fixture Book\nEnabled=false\n"
            "[Address Book]\nBackendName=local\n"
        )
        objects = {
            "/source/calendar": source_object("uid-calendar-fixture", calendar_data),
            "/source/book": source_object("uid-book-fixture", book_data),
        }

        calendars = parse_managed_sources(objects, SourceKind.CALENDAR)
        books = parse_managed_sources(objects, SourceKind.ADDRESS_BOOK)

        self.assertEqual(len(calendars), 1)
        self.assertEqual(calendars[0].display_name, "Fixture Calendar")
        self.assertTrue(calendars[0].enabled)
        self.assertEqual(calendars[0].kind, SourceKind.CALENDAR)
        self.assertEqual(len(books), 1)
        self.assertFalse(books[0].enabled)
        self.assertNotIn("Authentication", repr(calendars[0]))
        self.assertNotIn("fixture-only", repr(calendars[0]))
        self.assertNotIn("uid-calendar-fixture", repr(calendars[0]))
        self.assertNotIn("Fixture Calendar", repr(calendars[0]))

    def test_task_list_extension_is_distinct_from_calendar_extension(self):
        task_data = "[Data Source]\nDisplayName=Fixture Tasks\n[Task List]\nBackendName=caldav\n"
        objects = {"/source/tasks": source_object("uid-task-fixture", task_data)}

        tasks = parse_managed_sources(objects, SourceKind.TASK_LIST)
        calendars = parse_managed_sources(objects, SourceKind.CALENDAR)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].display_name, "Fixture Tasks")
        self.assertEqual(tasks[0].backend_name, "caldav")
        self.assertNotIn("caldav", repr(tasks[0]))
        self.assertEqual(calendars, ())

    def test_empty_result_differs_from_unregistered_source(self):
        self.assertEqual(result_for_read((), source_registered=True).state, EDSState.EMPTY)
        self.assertEqual(result_for_read((), source_registered=False).state, EDSState.UNREGISTERED)
        self.assertEqual(result_for_read(("fixture item",), source_registered=True).state, EDSState.READY)

    def test_authentication_error_differs_from_communication_error(self):
        class RemoteError(Exception):
            remote_error_name = "org.gnome.evolution.dataserver.AuthenticationFailed"

        self.assertEqual(classify_eds_error(RemoteError()), EDSState.AUTHENTICATION_REQUIRED)
        self.assertEqual(classify_eds_error(TimeoutError()), EDSState.COMMUNICATION_ERROR)

    def test_async_remote_failures_return_status_without_error_text(self):
        class RemoteError(Exception):
            remote_error_name = "org.gnome.evolution.dataserver.AuthenticationFailed"

        for error, expected in (
            (RemoteError("fixture authentication text"), EDSState.AUTHENTICATION_REQUIRED),
            (TimeoutError("fixture transport text"), EDSState.COMMUNICATION_ERROR),
        ):
            with self.subTest(expected=expected):
                connection = FakeConnection()
                results = []
                EDSRegistryAdapter(connection).list_sources_async(
                    SourceKind.TASK_LIST,
                    lambda result, _data: results.append(result),
                )
                connection.complete(FakeAsyncResult(error=error))
                self.assertEqual(results[0].state, expected)
                self.assertEqual(results[0].items, ())
                self.assertNotIn("fixture", repr(results[0]))


if __name__ == "__main__":
    unittest.main()
