"""EDSタスクリストからVTODOを読み取り専用で取得する。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo

import gi
from icalendar import Calendar

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from .eds import EDSClientHandle, EDSRegistryAdapter, EDSResult, EDSState, SourceKind
from .selection import ItemKey, SelectableItem
from .temporal import DateOnly, FloatingDateTime, TemporalValue, ZonedDateTime, to_system_local
from .todo import TodoBucket, TodoInput


# A true predicate leaves due-less VTODOs in the result; the parser keeps VTODOs only.
ALL_TODOS_QUERY = "#t"


class TodoDataError(ValueError):
    """不正なVTODO fixtureまたは取得内容。本文を例外へ含めない。"""


@dataclass(frozen=True, slots=True, repr=False)
class TodoRecord:
    """TODOの表示に必要な値だけを保持する。reprでは内容を伏せる。"""

    source_uid: str = field(repr=False)
    todo_uid: str = field(repr=False)
    summary: str = field(repr=False)
    start: TemporalValue | None = field(repr=False)
    due: TemporalValue | None = field(repr=False)
    completed: bool = False
    completed_at: TemporalValue | None = field(repr=False, default=None)

    @property
    def key(self) -> ItemKey:
        return ItemKey(self.source_uid, self.todo_uid)

    def as_todo_input(self) -> TodoInput:
        return TodoInput(
            start=self.start,
            due=self.due,
            completed=self.completed,
            completed_at=self.completed_at,
        )

    def as_selectable_item(
        self,
        bucket: TodoBucket,
        *,
        system_tz: tzinfo,
    ) -> SelectableItem:
        return SelectableItem(
            self.key,
            bucket.value,
            {
                "title": self.summary,
                "start": format_temporal(self.start, system_tz),
                "due": format_temporal(self.due, system_tz),
            },
        )

    def __repr__(self) -> str:
        return "TodoRecord(<personal data redacted>)"


def _component_value(component: object, name: str) -> object | None:
    try:
        return component.decoded(name)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _temporal(component: object, name: str) -> TemporalValue | None:
    property_value = getattr(component, "get", lambda _name: None)(name)
    if property_value is None:
        return None
    try:
        value = property_value.dt
    except AttributeError:
        value = property_value
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return ZonedDateTime(value)
        return FloatingDateTime(value)
    if isinstance(value, date):
        return DateOnly(value)
    return None


def _is_completed(component: object, completed_at: TemporalValue | None) -> bool:
    status = str(_component_value(component, "STATUS") or "").upper()
    if status in {"COMPLETED", "CANCELLED"} or completed_at is not None:
        return True
    percent = _component_value(component, "PERCENT-COMPLETE")
    try:
        return int(percent or 0) >= 100
    except (TypeError, ValueError):
        return False


def _parse_vtodo(component: object, source_uid: str) -> TodoRecord | None:
    if getattr(component, "name", None) != "VTODO":
        return None
    uid = str(_component_value(component, "UID") or "").strip()
    if not uid:
        return None
    summary_value = _component_value(component, "SUMMARY")
    summary = " ".join(str(summary_value or "").replace("\n", " ").split())
    start = _temporal(component, "DTSTART")
    due = _temporal(component, "DUE")
    completed_at = _temporal(component, "COMPLETED")
    return TodoRecord(
        source_uid,
        uid,
        summary,
        start,
        due,
        _is_completed(component, completed_at),
        completed_at,
    )


def parse_todos(payloads: Iterable[str | bytes], *, source_uid: str) -> tuple[TodoRecord, ...]:
    """iCalendar payloadsからUID・件名・日時・完了情報だけを投影する。"""
    if not source_uid:
        raise ValueError("source_uid must not be empty")
    todos: dict[tuple[str, str], TodoRecord] = {}
    for payload in payloads:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise TodoDataError from None
        if not isinstance(payload, str) or not payload.strip():
            continue
        try:
            root = Calendar.from_ical(payload)
        except Exception:
            raise TodoDataError from None
        components = (
            root.walk("VTODO")
            if getattr(root, "name", None) == "VCALENDAR"
            else [root]
        )
        for component in components:
            todo = _parse_vtodo(component, source_uid)
            if todo is not None:
                todos[(todo.source_uid, todo.todo_uid)] = todo
    return tuple(
        sorted(
            todos.values(),
            key=lambda todo: (todo.summary.casefold(), todo.todo_uid),
        )
    )


def format_temporal(value: TemporalValue | None, system_tz: tzinfo) -> str:
    """日付だけは維持し、時刻付き値はシステム時刻へ変換して表示する。"""
    if value is None:
        return ""
    local = to_system_local(value, system_tz=system_tz)
    if isinstance(local, datetime):
        return local.strftime("%Y-%m-%d %H:%M")
    return local.isoformat()


CALDAV_REFRESH_CHANGE_TIMEOUT_MS = 10_000


class _CalDAVRefreshRead:
    """Wait for EDS VTODO change notifications before returning refreshed data."""

    def __init__(
        self,
        eds: EDSRegistryAdapter,
        handle: EDSClientHandle,
        source_uid: str,
        callback: Callable[[EDSResult[TodoRecord], object | None], None],
        cancellable: Gio.Cancellable | None,
        user_data: object | None,
    ) -> None:
        self._eds = eds
        self._handle = handle
        self._source_uid = source_uid
        self._callback = callback
        self._cancellable = cancellable
        self._user_data = user_data
        self._view_path: str | None = None
        self._subscription_id = 0
        self._timeout_id = 0
        self._cancel_handler_id = 0
        self._initial_complete = False
        self._refresh_started = False
        self._refresh_reply_received = False
        self._change_seen = False
        self._reading = False
        self._read_dirty = False
        self._done = False
        self._refresh_state: EDSState | None = None

    def start(self) -> None:
        if self._cancellable is not None:
            if self._cancellable.is_cancelled():
                self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
                return
            self._cancel_handler_id = self._cancellable.connect(self._on_cancel)
            if self._done:
                return
        self._timeout_id = GLib.timeout_add(
            30_000, self._on_setup_timeout,
        )
        self._eds.call_read_async(
            self._handle,
            "GetView",
            GLib.Variant("(s)", (ALL_TODOS_QUERY,)),
            GLib.VariantType.new("(o)"),
            self._on_view_created,
            cancellable=self._cancellable,
        )

    def _on_view_created(self, result: EDSResult[GLib.Variant], _data: object | None) -> None:
        if result.state is not EDSState.READY or not result.items:
            self._finish(EDSResult(result.state))
            return
        try:
            self._view_path = result.items[0].unpack()[0]
            if self._done:
                self._cleanup()
                return
            self._subscription_id = self._eds.subscribe_calendar_view(
                self._handle,
                self._view_path,
                self._on_view_signal,
            )
        except Exception:
            self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
            return
        self._eds.call_calendar_view_async(
            self._handle,
            self._view_path,
            "Start",
            self._on_view_started,
            cancellable=self._cancellable,
        )

    def _on_view_started(self, result: EDSResult[GLib.Variant], _data: object | None) -> None:
        if result.state is not EDSState.READY:
            self._finish(EDSResult(result.state))

    def _on_view_signal(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface: str,
        signal_name: str,
        parameters: GLib.Variant,
        _user_data: object | None,
    ) -> None:
        if self._done:
            return
        if signal_name == "Complete" and not self._initial_complete:
            try:
                error_name, _error_message = parameters.unpack()
            except Exception:
                self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
                return
            if error_name:
                self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
                return
            self._initial_complete = True
            self._refresh_started = True
            self._eds.refresh_calendar_async(
                self._handle,
                self._on_refresh_requested,
                cancellable=self._cancellable,
            )
        elif signal_name in {"ObjectsAdded", "ObjectsModified", "ObjectsRemoved"}:
            if self._refresh_started:
                self._change_seen = True
                if self._reading:
                    self._read_dirty = True
                if self._refresh_reply_received:
                    self._read_latest()

    def _on_refresh_requested(self, result: EDSResult[GLib.Variant], _data: object | None) -> None:
        if self._done:
            return
        self._refresh_reply_received = True
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
        if result.state is not EDSState.READY:
            # Keep the EDS cache usable, but expose that freshness was not confirmed.
            self._refresh_state = result.state
            self._read_latest()
        elif self._change_seen:
            self._read_latest()
        else:
            self._timeout_id = GLib.timeout_add(
                CALDAV_REFRESH_CHANGE_TIMEOUT_MS,
                self._on_refresh_timeout,
            )

    def _on_setup_timeout(self) -> bool:
        self._timeout_id = 0
        self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
        return GLib.SOURCE_REMOVE

    def _on_refresh_timeout(self) -> bool:
        self._timeout_id = 0
        self._refresh_state = EDSState.REFRESH_UNCONFIRMED
        self._read_latest()
        return GLib.SOURCE_REMOVE

    def _read_latest(self) -> None:
        if self._done or self._reading:
            return
        self._reading = True
        self._read_dirty = False
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
        self._eds.call_read_async(
            self._handle,
            "GetObjectList",
            GLib.Variant("(s)", (ALL_TODOS_QUERY,)),
            GLib.VariantType.new("(as)"),
            self._on_latest_read,
            cancellable=self._cancellable,
        )

    def _on_latest_read(self, result: EDSResult[GLib.Variant], _data: object | None) -> None:
        if self._done:
            return
        if self._read_dirty:
            self._reading = False
            self._read_latest()
            return
        if result.state is not EDSState.READY:
            self._finish(EDSResult(result.state))
            return
        try:
            unpacked = result.items[0].unpack() if result.items else ((),)
            if (
                len(unpacked) != 1
                or not isinstance(unpacked[0], (tuple, list))
                or not all(isinstance(item, str) for item in unpacked[0])
            ):
                raise TodoDataError
            todos = parse_todos(unpacked[0], source_uid=self._source_uid)
        except Exception:
            self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))
            return
        active = tuple(todo for todo in todos if not todo.completed)
        if self._refresh_state is not None:
            state = self._refresh_state
        else:
            state = EDSState.READY if active else EDSState.EMPTY
        self._finish(EDSResult(state, active))

    def _on_cancel(self, *_args: object) -> None:
        # Gio.Cancellable.disconnect must not run inside its cancellation callback.
        handler_id = self._cancel_handler_id
        self._cancel_handler_id = 0
        if handler_id:
            GLib.idle_add(self._disconnect_cancel, handler_id)
        self._finish(EDSResult(EDSState.COMMUNICATION_ERROR))

    def _disconnect_cancel(self, handler_id: int) -> bool:
        self._cancellable.disconnect(handler_id)
        return GLib.SOURCE_REMOVE

    def _finish(self, result: EDSResult[TodoRecord]) -> None:
        if self._done:
            return
        self._done = True
        self._cleanup()
        self._callback(result, self._user_data)

    def _cleanup(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
        if self._subscription_id:
            self._eds.unsubscribe_calendar_view(self._subscription_id)
            self._subscription_id = 0
        if self._view_path is not None:
            self._eds.call_calendar_view_async(
                self._handle,
                self._view_path,
                "Dispose",
                lambda _result, _data: None,
            )
            self._view_path = None
        if self._cancellable is not None and self._cancel_handler_id:
            try:
                self._cancellable.disconnect(self._cancel_handler_id)
            except Exception:
                pass
            self._cancel_handler_id = 0


class TodoListReadAdapter:
    """利用者が選択したEDSタスクリストを非同期・読取専用で取得する。"""

    def __init__(self, eds: EDSRegistryAdapter) -> None:
        self._eds = eds

    def read_todos_async(
        self,
        handle: EDSClientHandle,
        source_uid: str,
        callback: Callable[[EDSResult[TodoRecord], object | None], None],
        *,
        backend_name: str = "",
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        if handle.kind is not SourceKind.TASK_LIST:
            raise ValueError("A task-list client handle is required")
        if not source_uid:
            raise ValueError("A task-list UID is required")

        def on_list(result: EDSResult[GLib.Variant], _ignored: object | None) -> None:
            if result.state is not EDSState.READY:
                callback(EDSResult(result.state), user_data)
                return
            try:
                unpacked = result.items[0].unpack() if result.items else ((),)
                if (
                    len(unpacked) != 1
                    or not isinstance(unpacked[0], (tuple, list))
                    or not all(isinstance(item, str) for item in unpacked[0])
                ):
                    raise TodoDataError
                todos = parse_todos(unpacked[0], source_uid=source_uid)
            except Exception:
                callback(EDSResult(EDSState.COMMUNICATION_ERROR), user_data)
                return
            active = tuple(todo for todo in todos if not todo.completed)
            callback(
                EDSResult(EDSState.READY, active) if active else EDSResult(EDSState.EMPTY),
                user_data,
            )

        def on_opened(result: EDSResult[GLib.Variant], _ignored: object | None) -> None:
            if result.state is not EDSState.READY:
                callback(EDSResult(result.state), user_data)
                return
            if backend_name.casefold() == "caldav":
                _CalDAVRefreshRead(
                    self._eds,
                    handle,
                    source_uid,
                    callback,
                    cancellable,
                    user_data,
                ).start()
                return
            self._eds.call_read_async(
                handle,
                "GetObjectList",
                GLib.Variant("(s)", (ALL_TODOS_QUERY,)),
                GLib.VariantType.new("(as)"),
                on_list,
                cancellable=cancellable,
            )

        self._eds.call_read_async(
            handle,
            "Open",
            None,
            GLib.VariantType.new("(as)"),
            on_opened,
            cancellable=cancellable,
        )
