"""EDSソースの非同期・読み取り専用列挙。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib


SOURCE_BUS_NAME = "org.gnome.evolution.dataserver.Sources5"
SOURCE_MANAGER_PATH = "/org/gnome/evolution/dataserver/SourceManager"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
SOURCE_INTERFACE = "org.gnome.evolution.dataserver.Source"
CALENDAR_FACTORY = (
    "org.gnome.evolution.dataserver.Calendar8",
    "/org/gnome/evolution/dataserver/CalendarFactory",
    "org.gnome.evolution.dataserver.CalendarFactory",
)
ADDRESS_BOOK_FACTORY = (
    "org.gnome.evolution.dataserver.AddressBook10",
    "/org/gnome/evolution/dataserver/AddressBookFactory",
    "org.gnome.evolution.dataserver.AddressBookFactory",
)
CALENDAR_CLIENT_INTERFACE = "org.gnome.evolution.dataserver.Calendar"
ADDRESS_BOOK_CLIENT_INTERFACE = "org.gnome.evolution.dataserver.AddressBook"
CALENDAR_VIEW_INTERFACE = "org.gnome.evolution.dataserver.CalendarView"


class SourceKind(Enum):
    CALENDAR = "Calendar"
    TASK_LIST = "Task List"
    ADDRESS_BOOK = "Address Book"


READ_METHODS = {
    SourceKind.CALENDAR: frozenset({"Open", "GetObject", "GetObjectList", "GetObjectListAsComps"}),
    SourceKind.TASK_LIST: frozenset({"Open", "GetObjectList", "GetObjectListAsComps", "GetView"}),
    SourceKind.ADDRESS_BOOK: frozenset({"Open", "GetContact", "GetContactList", "GetContactListUids"}),
}

_REFRESHABLE_KINDS = frozenset({SourceKind.CALENDAR, SourceKind.TASK_LIST})
_CALENDAR_VIEW_METHODS = frozenset({"Start", "Dispose"})


class EDSState(Enum):
    READY = "ready"
    UNREGISTERED = "unregistered"
    EMPTY = "empty"
    AUTHENTICATION_REQUIRED = "authentication-required"
    COMMUNICATION_ERROR = "communication-error"
    REFRESH_UNCONFIRMED = "refresh-unconfirmed"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """一覧表示とクライアント接続に必要な最小メタデータ。"""

    uid: str = field(repr=False)
    display_name: str = field(repr=False)
    kind: SourceKind
    enabled: bool
    backend_name: str = field(default="", repr=False)


T = TypeVar("T")


@dataclass(frozen=True, slots=True, repr=False)
class EDSResult(Generic[T]):
    state: EDSState
    items: tuple[T, ...] = ()

    def __repr__(self) -> str:
        return f"EDSResult(state={self.state.value!r}, item_count={len(self.items)})"


@dataclass(frozen=True, slots=True)
class EDSClientHandle:
    kind: SourceKind
    bus_name: str = field(repr=False)
    object_path: str = field(repr=False)


def _unpack(value: object) -> object:
    while isinstance(value, GLib.Variant):
        value = value.unpack()
    return value


def _property(properties: Mapping[str, object], name: str) -> object | None:
    if name not in properties:
        return None
    return _unpack(properties[name])


def parse_managed_sources(
    objects: Mapping[str, object],
    kind: SourceKind,
) -> tuple[SourceDescriptor, ...]:
    """ObjectManager応答から対象種別だけを抽出する。

    ESourceの`Data`には認証・接続設定も含まれ得る。KeyFileはメモリ内で
    必要なキーだけを読み、元文字列や他の設定値を返却・記録しない。
    """
    sources: list[SourceDescriptor] = []
    for interfaces_value in objects.values():
        interfaces = _unpack(interfaces_value)
        if not isinstance(interfaces, Mapping):
            continue
        properties_value = interfaces.get(SOURCE_INTERFACE)
        if properties_value is None:
            continue
        properties = _unpack(properties_value)
        if not isinstance(properties, Mapping):
            continue

        uid = _property(properties, "UID")
        data = _property(properties, "Data")
        if not isinstance(uid, str) or not uid or not isinstance(data, str):
            continue

        key_file = GLib.KeyFile()
        try:
            key_file.load_from_data(
                data,
                len(data.encode("utf-8")),
                GLib.KeyFileFlags.NONE,
            )
            if not key_file.has_group("Data Source") or not key_file.has_group(kind.value):
                continue
            display_name = key_file.get_string("Data Source", "DisplayName")
            keys, _key_count = key_file.get_keys("Data Source")
            if "Enabled" in keys:
                enabled = key_file.get_boolean("Data Source", "Enabled")
            else:
                enabled = True
            extension_keys, _extension_key_count = key_file.get_keys(kind.value)
            backend_name = (
                key_file.get_string(kind.value, "BackendName")
                if "BackendName" in extension_keys
                else ""
            )
        except GLib.Error:
            # 壊れたソース設定の値をログへ出さず、そのソースだけを除外する。
            continue

        sources.append(
            SourceDescriptor(
                uid=uid,
                display_name=display_name or "Unnamed",
                kind=kind,
                enabled=enabled,
                backend_name=backend_name,
            )
        )

    return tuple(sorted(sources, key=lambda source: (source.display_name.casefold(), source.uid)))


def result_for_read(items: Sequence[T], *, source_registered: bool) -> EDSResult[T]:
    """未登録・登録済み空・データありを区別する共通結果。"""
    if not source_registered:
        return EDSResult(EDSState.UNREGISTERED)
    frozen_items = tuple(items)
    if not frozen_items:
        return EDSResult(EDSState.EMPTY)
    return EDSResult(EDSState.READY, frozen_items)


def classify_eds_error(error: BaseException) -> EDSState:
    """エラー本文を参照・保存せず、D-Busエラー名だけで大分類する。"""
    remote_name = getattr(error, "remote_error_name", None)
    if isinstance(error, GLib.Error):
        try:
            remote_name = Gio.DBusError.get_remote_error(error) or remote_name
        except (TypeError, AttributeError):
            pass
    safe_name = remote_name.casefold() if isinstance(remote_name, str) else ""
    auth_markers = (
        "authentication",
        "credential",
        "unauthorized",
        "notauthorized",
        "accessdenied",
        "permissiondenied",
    )
    if any(marker in safe_name for marker in auth_markers):
        return EDSState.AUTHENTICATION_REQUIRED
    return EDSState.COMMUNICATION_ERROR


class EDSRegistryAdapter:
    """GioのD-Bus ObjectManagerでEDSソースを非同期・読取専用に列挙する。"""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection

    def open_source_async(
        self,
        source: SourceDescriptor,
        callback: Callable[[EDSResult[EDSClientHandle], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        """Calendar/TaskList/AddressBook factoryからクライアントを開く。"""
        if source.kind is SourceKind.ADDRESS_BOOK:
            bus_name, object_path, interface = ADDRESS_BOOK_FACTORY
            method = "OpenAddressBook"
        else:
            bus_name, object_path, interface = CALENDAR_FACTORY
            method = "OpenTaskList" if source.kind is SourceKind.TASK_LIST else "OpenCalendar"

        self._connection.call(
            bus_name,
            object_path,
            interface,
            method,
            GLib.Variant("(s)", (source.uid,)),
            GLib.VariantType.new("(ss)"),
            Gio.DBusCallFlags.NONE,
            30_000,
            cancellable,
            self._on_client_opened,
            (source.kind, callback, user_data),
        )

    @staticmethod
    def _on_client_opened(
        connection: Gio.DBusConnection,
        async_result: Gio.AsyncResult,
        callback_data: tuple[
            SourceKind,
            Callable[[EDSResult[EDSClientHandle], object | None], None],
            object | None,
        ],
    ) -> None:
        kind, callback, user_data = callback_data
        try:
            object_path, bus_name = connection.call_finish(async_result).unpack()
            handle = EDSClientHandle(kind, bus_name, object_path)
            result = EDSResult(EDSState.READY, (handle,))
        except Exception as error:
            result = EDSResult(classify_eds_error(error))
        callback(result, user_data)

    def call_read_async(
        self,
        handle: EDSClientHandle,
        method: str,
        parameters: GLib.Variant | None,
        reply_type: GLib.VariantType | None,
        callback: Callable[[EDSResult[GLib.Variant], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        """明示的な読み取り許可リスト内のEDSクライアント操作だけを呼ぶ。"""
        if method not in READ_METHODS[handle.kind]:
            raise ValueError("Not a permitted EDS read operation")
        interface = (
            ADDRESS_BOOK_CLIENT_INTERFACE
            if handle.kind is SourceKind.ADDRESS_BOOK
            else CALENDAR_CLIENT_INTERFACE
        )
        self._connection.call(
            handle.bus_name,
            handle.object_path,
            interface,
            method,
            parameters,
            reply_type,
            Gio.DBusCallFlags.NONE,
            30_000,
            cancellable,
            self._on_read_ready,
            (callback, user_data),
        )

    def refresh_calendar_async(
        self,
        handle: EDSClientHandle,
        callback: Callable[[EDSResult[GLib.Variant], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        """EDS側のリモート同期を要求する。オブジェクトの書き戻しは行わない。"""
        if handle.kind not in _REFRESHABLE_KINDS:
            raise ValueError("This EDS client cannot refresh calendars")
        self._connection.call(
            handle.bus_name,
            handle.object_path,
            CALENDAR_CLIENT_INTERFACE,
            "Refresh",
            None,
            GLib.VariantType.new("()"),
            Gio.DBusCallFlags.NONE,
            30_000,
            cancellable,
            self._on_read_ready,
            (callback, user_data),
        )

    def call_calendar_view_async(
        self,
        handle: EDSClientHandle,
        view_path: str,
        method: str,
        callback: Callable[[EDSResult[GLib.Variant], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        if handle.kind not in _REFRESHABLE_KINDS or method not in _CALENDAR_VIEW_METHODS:
            raise ValueError("Not a permitted CalendarView operation")
        self._connection.call(
            handle.bus_name,
            view_path,
            CALENDAR_VIEW_INTERFACE,
            method,
            None,
            GLib.VariantType.new("()"),
            Gio.DBusCallFlags.NONE,
            30_000,
            cancellable,
            self._on_read_ready,
            (callback, user_data),
        )

    def subscribe_calendar_view(
        self,
        handle: EDSClientHandle,
        view_path: str,
        callback: Callable[..., None],
        *,
        user_data: object | None = None,
    ) -> int:
        if handle.kind not in _REFRESHABLE_KINDS:
            raise ValueError("A calendar client handle is required")
        return self._connection.signal_subscribe(
            handle.bus_name,
            CALENDAR_VIEW_INTERFACE,
            None,
            view_path,
            None,
            Gio.DBusSignalFlags.NONE,
            callback,
            user_data,
        )

    def unsubscribe_calendar_view(self, subscription_id: int) -> None:
        self._connection.signal_unsubscribe(subscription_id)

    @staticmethod
    def _on_read_ready(
        connection: Gio.DBusConnection,
        async_result: Gio.AsyncResult,
        callback_data: tuple[
            Callable[[EDSResult[GLib.Variant], object | None], None],
            object | None,
        ],
    ) -> None:
        callback, user_data = callback_data
        try:
            reply = connection.call_finish(async_result)
            result = EDSResult(EDSState.READY, (reply,))
        except Exception as error:
            result = EDSResult(classify_eds_error(error))
        callback(result, user_data)

    def list_sources_async(
        self,
        kind: SourceKind,
        callback: Callable[[EDSResult[SourceDescriptor], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        self._connection.call(
            SOURCE_BUS_NAME,
            SOURCE_MANAGER_PATH,
            OBJECT_MANAGER_INTERFACE,
            "GetManagedObjects",
            None,
            GLib.VariantType.new("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE,
            30_000,
            cancellable,
            self._on_sources_ready,
            (kind, callback, user_data),
        )

    @staticmethod
    def _on_sources_ready(
        connection: Gio.DBusConnection,
        async_result: Gio.AsyncResult,
        callback_data: tuple[
            SourceKind,
            Callable[[EDSResult[SourceDescriptor], object | None], None],
            object | None,
        ],
    ) -> None:
        kind, callback, user_data = callback_data
        try:
            reply = connection.call_finish(async_result)
            objects = reply.unpack()[0]
            sources = parse_managed_sources(objects, kind)
            result = EDSResult(EDSState.READY, sources) if sources else EDSResult(EDSState.UNREGISTERED)
        except Exception as error:
            result = EDSResult(classify_eds_error(error))
        callback(result, user_data)
