"""未完了TODOの分類と期限切れ判定。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from enum import Enum
from typing import Sequence

from .temporal import DateOnly, FloatingDateTime, TemporalValue, ZonedDateTime, system_timezone


@dataclass(frozen=True, slots=True)
class TodoInput:
    start: TemporalValue | None = None
    due: TemporalValue | None = None
    completed: bool = False
    completed_at: TemporalValue | None = None


class TodoBucket(Enum):
    TODAY = "today"
    UPCOMING = "upcoming"
    UNDATED = "undated"


@dataclass(frozen=True, slots=True)
class TodoAssessment:
    """印刷対象分類。bucketがNoneなら完了済みで候補外。"""

    bucket: TodoBucket | None
    overdue: bool
    due_before_start: bool


def _now_in_zone(now: datetime | None, zone: tzinfo) -> datetime:
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("nowにはタイムゾーン付きdatetimeが必要です")
    return now.astimezone(zone)


def _epoch(value: TemporalValue, zone: tzinfo, *, date_due_boundary: bool = False) -> float:
    if isinstance(value, DateOnly):
        boundary = value.value
        if date_due_boundary:
            try:
                boundary += timedelta(days=1)
            except OverflowError:
                return datetime.combine(boundary, time.max, zone).timestamp()
        return datetime.combine(boundary, time.min, zone).timestamp()
    if isinstance(value, ZonedDateTime):
        return value.value.timestamp()
    if isinstance(value, FloatingDateTime):
        return value.value.replace(tzinfo=zone).timestamp()
    raise TypeError(f"未対応の日時型です: {type(value).__name__}")


def _is_future_start(start: TemporalValue, now: datetime, zone: tzinfo) -> bool:
    if isinstance(start, DateOnly):
        return start.value > now.date()
    return _epoch(start, zone) > now.timestamp()


def _is_overdue(due: TemporalValue, now: datetime, zone: tzinfo) -> bool:
    if isinstance(due, DateOnly):
        return now.date() > due.value
    return now.timestamp() > _epoch(due, zone)


def _due_before_start(start: TemporalValue | None, due: TemporalValue | None, zone: tzinfo) -> bool:
    if start is None or due is None:
        return False
    start_epoch = _epoch(start, zone)
    if isinstance(due, DateOnly):
        # 日付だけの期限は、その翌日の開始まで有効。
        return _epoch(due, zone, date_due_boundary=True) <= start_epoch
    return _epoch(due, zone) < start_epoch


def classify_todos(
    items: Sequence[TodoInput],
    *,
    now: datetime | None = None,
    system_tz: tzinfo | None = None,
) -> tuple[TodoAssessment, ...]:
    """新しい時計・タイムゾーンで全TODOを再判定する。結果をキャッシュしない。"""
    zone = system_timezone() if system_tz is None else system_tz
    current = _now_in_zone(now, zone)
    return tuple(
        classify_todo(
            start=item.start,
            due=item.due,
            completed=item.completed,
            completed_at=item.completed_at,
            now=current,
            system_tz=zone,
        )
        for item in items
    )


def next_todo_reassessment(
    items: Sequence[TodoInput],
    *,
    now: datetime,
    system_tz: tzinfo | None = None,
) -> datetime:
    """次のローカル日付境界、着手または期限境界を返す。

    システムタイムゾーン変更時は呼出元がこの関数を再度呼び、最新のzoneを渡す。
    """
    zone = system_timezone() if system_tz is None else system_tz
    current = _now_in_zone(now, zone)
    now_epoch = current.timestamp()
    next_day = current.date() + timedelta(days=1)
    candidates = [datetime.combine(next_day, time.min, zone).timestamp()]

    for item in items:
        if item.completed or item.completed_at is not None:
            continue
        if item.start is not None:
            start_epoch = _epoch(item.start, zone)
            if start_epoch > now_epoch:
                candidates.append(start_epoch)
        if item.due is not None:
            due_epoch = _epoch(item.due, zone, date_due_boundary=isinstance(item.due, DateOnly))
            if due_epoch > now_epoch:
                candidates.append(due_epoch)

    return datetime.fromtimestamp(min(candidates), zone)


def classify_todo(
    *,
    start: TemporalValue | None,
    due: TemporalValue | None,
    completed: bool = False,
    completed_at: TemporalValue | None = None,
    now: datetime | None = None,
    system_tz: tzinfo | None = None,
) -> TodoAssessment:
    """TODOを「今日」「今後」「期限なし」に一意に分類する。

    完了状態または完了日時があれば候補外とする。着手日時を期限より優先し、
    期限切れでもカテゴリから除外しない。日付だけの期限はその日の終了まで有効。
    """

    zone = system_timezone() if system_tz is None else system_tz
    current = _now_in_zone(now, zone)

    if completed or completed_at is not None:
        return TodoAssessment(bucket=None, overdue=False, due_before_start=False)

    overdue = due is not None and _is_overdue(due, current, zone)
    due_before_start = _due_before_start(start, due, zone)

    if start is not None:
        bucket = TodoBucket.UPCOMING if _is_future_start(start, current, zone) else TodoBucket.TODAY
    elif due is not None:
        bucket = TodoBucket.TODAY
    else:
        bucket = TodoBucket.UNDATED

    return TodoAssessment(
        bucket=bucket,
        overdue=overdue,
        due_before_start=due_before_start,
    )
