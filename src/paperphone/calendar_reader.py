"""EDSカレンダーから今日に重なる予定を読み取る。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone, tzinfo

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from paperphone.eds import EDSClientHandle, EDSRegistryAdapter, EDSResult, EDSState, SourceKind
from paperphone.events import CalendarEvent, events_for_local_day, recurring_uids


_READ_CONCURRENCY = 8


def local_day_query(day: date, system_tz: tzinfo) -> str:
    """EDSのoccur-in-time-range?向けに、ローカル日の半開区間をUTC秒へ変換する。"""
    start = datetime.combine(day, time.min).replace(tzinfo=system_tz)
    end = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=system_tz)
    start_utc = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_utc = end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f'(occur-in-time-range? (make-time "{start_utc}") (make-time "{end_utc}"))'


def _object_list(reply: GLib.Variant) -> tuple[str, ...]:
    unpacked = reply.unpack()
    if len(unpacked) != 1 or not isinstance(unpacked[0], (tuple, list)):
        raise ValueError("Unexpected EDS object-list response")
    values = unpacked[0]
    if not all(isinstance(value, str) for value in values):
        raise ValueError("Unexpected EDS object-list item")
    return tuple(values)


def _single_object(reply: GLib.Variant) -> str:
    unpacked = reply.unpack()
    if len(unpacked) != 1 or not isinstance(unpacked[0], str) or not unpacked[0].strip():
        raise ValueError("Unexpected EDS object response")
    return unpacked[0]


class CalendarReadAdapter:
    """カレンダーの読取操作だけで、今日の予定とシリーズ例外を取得する。"""

    def __init__(self, eds: EDSRegistryAdapter) -> None:
        self._eds = eds

    def read_today_async(
        self,
        handle: EDSClientHandle,
        day: date,
        system_tz: tzinfo,
        callback: Callable[[EDSResult[CalendarEvent], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        if handle.kind is not SourceKind.CALENDAR:
            raise ValueError("A calendar client handle is required")
        self._eds.call_read_async(
            handle,
            "Open",
            None,
            GLib.VariantType.new("(as)"),
            lambda result, _unused: self._on_opened(
                result, handle, day, system_tz, callback, cancellable, user_data
            ),
            cancellable=cancellable,
        )

    def _on_opened(
        self,
        result: EDSResult[GLib.Variant],
        handle: EDSClientHandle,
        day: date,
        system_tz: tzinfo,
        callback: Callable[[EDSResult[CalendarEvent], object | None], None],
        cancellable: Gio.Cancellable | None,
        user_data: object | None,
    ) -> None:
        if result.state is not EDSState.READY:
            callback(EDSResult(result.state), user_data)
            return
        query = local_day_query(day, system_tz)
        self._eds.call_read_async(
            handle,
            "GetObjectList",
            GLib.Variant("(s)", (query,)),
            GLib.VariantType.new("(as)"),
            lambda list_result, _unused: self._on_range_list(
                list_result, handle, day, system_tz, callback, cancellable, user_data
            ),
            cancellable=cancellable,
        )

    def _on_range_list(
        self,
        result: EDSResult[GLib.Variant],
        handle: EDSClientHandle,
        day: date,
        system_tz: tzinfo,
        callback: Callable[[EDSResult[CalendarEvent], object | None], None],
        cancellable: Gio.Cancellable | None,
        user_data: object | None,
    ) -> None:
        if result.state is not EDSState.READY:
            callback(EDSResult(result.state), user_data)
            return
        try:
            payloads = list(_object_list(result.items[0]))
            uids = recurring_uids(payloads)
        except Exception:
            callback(EDSResult(EDSState.COMMUNICATION_ERROR), user_data)
            return
        if not uids:
            self._finish(payloads, day, system_tz, callback, user_data)
            return

        pending = list(uids)
        active = 0
        failure: EDSState | None = None
        completed = False

        def launch_next() -> None:
            nonlocal active, completed
            while pending and active < _READ_CONCURRENCY and failure is None and not completed:
                uid = pending.pop(0)
                active += 1
                self._eds.call_read_async(
                    handle,
                    "GetObject",
                    GLib.Variant("(ss)", (uid, "")),
                    GLib.VariantType.new("(s)"),
                    lambda uid_result, _unused, _uid=uid: on_uid_list(uid_result, _uid),
                    cancellable=cancellable,
                )
            if active == 0 and not pending and not completed:
                completed = True
                if failure is not None:
                    callback(EDSResult(failure), user_data)
                else:
                    self._finish(payloads, day, system_tz, callback, user_data)

        def on_uid_list(uid_result: EDSResult[GLib.Variant], _uid: str) -> None:
            nonlocal active, failure
            active -= 1
            if uid_result.state is not EDSState.READY:
                failure = uid_result.state
                pending.clear()
            else:
                try:
                    payloads.append(_single_object(uid_result.items[0]))
                except Exception:
                    failure = EDSState.COMMUNICATION_ERROR
                    pending.clear()
            launch_next()

        launch_next()

    @staticmethod
    def _finish(
        payloads: list[str],
        day: date,
        system_tz: tzinfo,
        callback: Callable[[EDSResult[CalendarEvent], object | None], None],
        user_data: object | None,
    ) -> None:
        try:
            events = events_for_local_day(payloads, day, system_tz=system_tz)
        except Exception:
            callback(EDSResult(EDSState.COMMUNICATION_ERROR), user_data)
            return
        state = EDSState.READY if events else EDSState.EMPTY
        callback(EDSResult(state, events), user_data)
