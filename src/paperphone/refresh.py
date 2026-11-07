"""EDS読取の更新・キャンセルとソース単位の結果集約。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, tzinfo
from enum import Enum

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from .calendar_reader import CalendarReadAdapter
from .contacts import AddressBookReadAdapter, ContactRecord
from .eds import EDSClientHandle, EDSRegistryAdapter, EDSResult, EDSState, SourceDescriptor, SourceKind
from .events import CalendarEvent
from .todo_reader import TodoListReadAdapter, TodoRecord


_MAX_PARALLEL_SOURCES = 4


class RefreshState(Enum):
    LOADING = "loading"
    READY = "ready"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, repr=False)
class SourceRefreshResult:
    source: SourceDescriptor = field(repr=False)
    state: EDSState
    items: tuple[CalendarEvent | ContactRecord | TodoRecord, ...] = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"SourceRefreshResult(kind={self.source.kind.value!r}, "
            f"state={self.state.value!r}, item_count={len(self.items)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RefreshReport:
    state: RefreshState
    sources: tuple[SourceRefreshResult, ...] = field(repr=False)

    def __repr__(self) -> str:
        return f"RefreshReport(state={self.state.value!r}, source_count={len(self.sources)})"


class RefreshJob:
    """複数EDSソースの読取をまとめ、完了前にキャンセルできる。"""

    def __init__(
        self,
        registry: EDSRegistryAdapter,
        sources: Sequence[SourceDescriptor],
        day: date,
        system_tz: tzinfo,
        callback: Callable[[RefreshReport, object | None], None],
        user_data: object | None,
    ) -> None:
        self._registry = registry
        self._pending = deque(sources)
        self._day = day
        self._system_tz = system_tz
        self._callback = callback
        self._user_data = user_data
        self._cancellable = Gio.Cancellable.new()
        self._results: list[SourceRefreshResult] = []
        self._active = 0
        self._completed = False
        self.state = RefreshState.LOADING
        self._launch_next()

    @property
    def cancellable(self) -> Gio.Cancellable:
        return self._cancellable

    def cancel(self) -> None:
        if self._completed:
            return
        self._pending.clear()
        self._complete(RefreshState.CANCELLED)
        self._cancellable.cancel()

    def _launch_next(self) -> None:
        while (
            self._pending
            and self._active < _MAX_PARALLEL_SOURCES
            and not self._completed
        ):
            source = self._pending.popleft()
            self._active += 1
            self._registry.open_source_async(
                source,
                lambda result, _data, _source=source: self._on_opened(_source, result),
                cancellable=self._cancellable,
            )
        if self._active == 0 and not self._pending and not self._completed:
            has_failures = any(
                item.state not in (EDSState.READY, EDSState.EMPTY)
                for item in self._results
            )
            self._complete(RefreshState.PARTIAL if has_failures else RefreshState.READY)

    def _on_opened(
        self,
        source: SourceDescriptor,
        result: EDSResult[EDSClientHandle],
    ) -> None:
        if self._completed:
            return
        if result.state is not EDSState.READY or not result.items:
            self._results.append(SourceRefreshResult(source, result.state, ()))
            self._active -= 1
            self._launch_next()
            return

        handle = result.items[0]
        read_callback = lambda read_result, _data: self._on_read(source, read_result)
        if source.kind is SourceKind.CALENDAR:
            CalendarReadAdapter(self._registry).read_today_async(
                handle,
                self._day,
                self._system_tz,
                read_callback,
                cancellable=self._cancellable,
            )
        elif source.kind is SourceKind.ADDRESS_BOOK:
            AddressBookReadAdapter(self._registry).read_contacts_async(
                source,
                handle,
                read_callback,
                cancellable=self._cancellable,
            )
        elif source.kind is SourceKind.TASK_LIST:
            TodoListReadAdapter(self._registry).read_todos_async(
                handle,
                source.uid,
                read_callback,
                backend_name=source.backend_name,
                cancellable=self._cancellable,
            )
        else:
            self._on_read(source, EDSResult(EDSState.COMMUNICATION_ERROR))

    def _on_read(
        self,
        source: SourceDescriptor,
        result: EDSResult[object],
    ) -> None:
        if self._completed:
            return
        self._results.append(SourceRefreshResult(source, result.state, result.items))
        self._active -= 1
        self._launch_next()

    def _complete(self, state: RefreshState) -> None:
        if self._completed:
            return
        self._completed = True
        self.state = state
        report = RefreshReport(state, tuple(self._results))
        self._callback(report, self._user_data)


class EDSRefreshController:
    """選択されたソースだけを種類別読取アダプターへ接続する。"""

    def __init__(self, registry: EDSRegistryAdapter) -> None:
        self._registry = registry

    def refresh_async(
        self,
        sources: Sequence[SourceDescriptor],
        day: date,
        system_tz: tzinfo,
        callback: Callable[[RefreshReport, object | None], None],
        *,
        user_data: object | None = None,
    ) -> RefreshJob:
        if any(not source.enabled for source in sources):
            raise ValueError("Disabled EDS sources cannot be refreshed")
        return RefreshJob(
            self._registry,
            sources,
            day,
            system_tz,
            callback,
            user_data,
        )
