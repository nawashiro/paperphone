"""VEVENTの読取専用パースとシステム日の繰り返し展開。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import TypeAlias

from dateutil.rrule import rruleset, rrulestr
from icalendar import Calendar

from paperphone.temporal import DateOnly, FloatingDateTime, TemporalValue, ZonedDateTime


CalendarDateTime: TypeAlias = date | datetime


class CalendarDataError(ValueError):
    """不正なiCalendar fixtureまたは取得内容。本文を例外へ含めない。"""


@dataclass(frozen=True, slots=True, repr=False)
class CalendarEvent:
    uid: str = field(repr=False)
    instance_id: str = field(repr=False)
    summary: str = field(repr=False)
    start: TemporalValue = field(repr=False)
    end: TemporalValue = field(repr=False)

    def __repr__(self) -> str:
        return "CalendarEvent(<personal data redacted>)"

    @property
    def all_day(self) -> bool:
        return isinstance(self.start, DateOnly)


@dataclass(frozen=True, slots=True, repr=False)
class _Entry:
    component: object
    uid: str
    start: CalendarDateTime
    end: CalendarDateTime
    recurrence_id: CalendarDateTime | None
    range_future: bool
    sequence: int


def _properties(component: object, name: str) -> list[object]:
    return [value for key, value in component.property_items() if key.upper() == name.upper()]


def _decoded(component: object, name: str) -> object | None:
    values = _properties(component, name)
    if not values:
        return None
    try:
        return values[0].dt if name.upper() in ("DTSTART", "DTEND", "RECURRENCE-ID") else component.decoded(name)
    except (KeyError, ValueError, TypeError):
        return None


def _add_duration(value: CalendarDateTime, duration: timedelta) -> CalendarDateTime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return (value.astimezone(timezone.utc) + duration).astimezone(value.tzinfo)
    return value + duration


def _difference(start: CalendarDateTime, end: CalendarDateTime) -> timedelta:
    if isinstance(start, datetime) and isinstance(end, datetime) and start.tzinfo and end.tzinfo:
        return end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    return end - start


def _shift_wall(value: CalendarDateTime, difference: timedelta) -> CalendarDateTime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return (value.replace(tzinfo=None) + difference).replace(tzinfo=value.tzinfo)
    return value + difference


def _duration(component: object, start: CalendarDateTime, end: CalendarDateTime | None) -> timedelta:
    if end is not None:
        try:
            return _difference(start, end)
        except TypeError:
            raise CalendarDataError from None
    duration = _decoded(component, "DURATION")
    if isinstance(duration, timedelta):
        return duration
    return timedelta(days=1) if isinstance(start, date) and not isinstance(start, datetime) else timedelta(0)


def _parse_component(component: object) -> _Entry | None:
    if getattr(component, "name", None) != "VEVENT":
        return None
    uid_value = _decoded(component, "UID")
    uid = str(uid_value) if uid_value is not None else ""
    start = _decoded(component, "DTSTART")
    rid = _decoded(component, "RECURRENCE-ID")
    if (
        not isinstance(start, (date, datetime))
        and isinstance(rid, (date, datetime))
        and _is_cancelled(component)
    ):
        start = rid
    if not uid or not isinstance(start, (date, datetime)):
        return None
    end = _decoded(component, "DTEND")
    if not isinstance(end, (date, datetime)):
        end = None
    try:
        duration = _duration(component, start, end)
        resolved_end = end if end is not None else _add_duration(start, duration)
    except (TypeError, OverflowError):
        raise CalendarDataError from None

    if not isinstance(rid, (date, datetime)):
        rid = None
    recurrence_property = _properties(component, "RECURRENCE-ID")
    range_future = bool(
        recurrence_property
        and str(recurrence_property[0].params.get("RANGE", "")).upper() == "THISANDFUTURE"
    )
    sequence_value = _decoded(component, "SEQUENCE")
    try:
        sequence = int(sequence_value or 0)
    except (TypeError, ValueError):
        sequence = 0
    return _Entry(component, uid, start, resolved_end, rid, range_future, sequence)


def _parse_payloads(payloads: Iterable[str | bytes]) -> tuple[_Entry, ...]:
    entries: list[_Entry] = []
    for payload in payloads:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise CalendarDataError from None
        if not isinstance(payload, str) or not payload.strip():
            continue
        try:
            root = Calendar.from_ical(payload)
        except Exception:
            raise CalendarDataError from None
        components = root.walk("VEVENT") if getattr(root, "name", None) == "VCALENDAR" else [root]
        for component in components:
            entry = _parse_component(component)
            if entry is not None:
                entries.append(entry)
    return tuple(entries)


def recurring_uids(payloads: Iterable[str | bytes]) -> tuple[str, ...]:
    """候補VEVENTからUIDを抽出し、例外を含むシリーズ再取得対象を返す。"""
    uids: set[str] = set()
    for entry in _parse_payloads(payloads):
        if (
            _properties(entry.component, "RRULE")
            or _properties(entry.component, "RDATE")
            or _properties(entry.component, "EXRULE")
            or entry.recurrence_id is not None
        ):
            uids.add(entry.uid)
    return tuple(sorted(uids))


def _as_rule_datetime(value: CalendarDateTime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def _as_component_value(value: datetime, prototype: CalendarDateTime) -> CalendarDateTime:
    return value.date() if isinstance(prototype, date) and not isinstance(prototype, datetime) else value


def _duration_for_rdates(component: object) -> dict[tuple[str, str], timedelta]:
    durations: dict[tuple[str, str], timedelta] = {}
    for prop in _properties(component, "RDATE"):
        for value in getattr(prop, "dts", ()):
            item = value.dt
            if isinstance(item, tuple) and len(item) == 2:
                start, end_or_duration = item
                if isinstance(end_or_duration, timedelta):
                    period = end_or_duration
                else:
                    try:
                        period = _difference(start, end_or_duration)
                    except TypeError:
                        raise CalendarDataError from None
                if isinstance(start, (date, datetime)):
                    durations[_value_key(start)] = period
    return durations


def _build_recurrence_set(entry: _Entry) -> tuple[rruleset, dict[tuple[str, str], timedelta]]:
    component = entry.component
    dtstart = _as_rule_datetime(entry.start)
    values = rruleset()
    values.rdate(dtstart)

    try:
        for rule in _properties(component, "RRULE"):
            parsed_rule = rrulestr(rule.to_ical().decode("ascii"), dtstart=dtstart)
            if isinstance(parsed_rule, rruleset):
                for subrule in getattr(parsed_rule, "_rrule", ()):
                    values.rrule(subrule)
                for rdate in getattr(parsed_rule, "_rdate", ()):
                    values.rdate(rdate)
            else:
                values.rrule(parsed_rule)
        for rule in _properties(component, "EXRULE"):
            parsed_rule = rrulestr(rule.to_ical().decode("ascii"), dtstart=dtstart)
            if isinstance(parsed_rule, rruleset):
                for subrule in getattr(parsed_rule, "_rrule", ()):
                    values.exrule(subrule)
            else:
                values.exrule(parsed_rule)
    except Exception:
        raise CalendarDataError from None

    for prop_name, action in (("RDATE", values.rdate), ("EXDATE", values.exdate)):
        for prop in _properties(component, prop_name):
            for value in getattr(prop, "dts", ()):
                item = value.dt
                if isinstance(item, tuple):
                    item = item[0]
                if isinstance(item, (date, datetime)):
                    action(_as_rule_datetime(item))

    return values, _duration_for_rdates(component)


def _value_key(value: CalendarDateTime) -> tuple[str, str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return ("floating", value.isoformat(timespec="microseconds"))
        return ("instant", value.astimezone(timezone.utc).isoformat(timespec="microseconds"))
    return ("date", value.isoformat())


def _sort_value(value: CalendarDateTime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return datetime.combine(value, time.min)


def _is_cancelled(component: object) -> bool:
    status = _decoded(component, "STATUS")
    return str(status or "").upper() == "CANCELLED"


def _summary(component: object, fallback: str = "") -> str:
    value = _decoded(component, "SUMMARY")
    return str(value) if value is not None else fallback


def _local_bounds(day: date, zone: tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).replace(tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=zone)
    return start, end


def _overlaps_day(
    start: CalendarDateTime,
    end: CalendarDateTime,
    day: date,
    zone: tzinfo,
) -> bool:
    if isinstance(start, date) and not isinstance(start, datetime):
        if not isinstance(end, date) or isinstance(end, datetime):
            return False
        return start < day + timedelta(days=1) and end > day
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False

    day_start, day_end = _local_bounds(day, zone)
    if start.tzinfo is None:
        start = start.replace(tzinfo=zone)
        end = end.replace(tzinfo=zone)
    start = start.astimezone(zone)
    end = end.astimezone(zone)
    return start < day_end and end > day_start


def _rule_bounds(
    day: date,
    zone: tzinfo,
    prototype: CalendarDateTime,
    duration: timedelta,
) -> tuple[datetime, datetime]:
    if isinstance(prototype, date) and not isinstance(prototype, datetime):
        extra_days = max(0, (duration.days + (1 if duration.seconds or duration.microseconds else 0)))
        lower_day = day - timedelta(days=extra_days)
        upper_day = day + timedelta(days=1)
        return datetime.combine(lower_day, time.min), datetime.combine(upper_day, time.min)

    day_start, day_end = _local_bounds(day, zone)
    if isinstance(prototype, datetime) and prototype.tzinfo is not None:
        event_zone = prototype.tzinfo
        return day_start.astimezone(event_zone) - duration, day_end.astimezone(event_zone)
    return day_start.replace(tzinfo=None) - duration, day_end.replace(tzinfo=None)


def _display_order(value: TemporalValue, zone: tzinfo) -> datetime:
    if isinstance(value, DateOnly):
        local = datetime.combine(value.value, time.min).replace(tzinfo=zone)
    elif isinstance(value, ZonedDateTime):
        local = value.value.astimezone(zone)
    else:
        local = value.value.replace(tzinfo=zone)
    return local.replace(tzinfo=None)


def _to_temporal(value: CalendarDateTime) -> TemporalValue:
    if isinstance(value, datetime):
        return FloatingDateTime(value) if value.tzinfo is None else ZonedDateTime(value)
    return DateOnly(value)


def _event_from_values(
    uid: str,
    recurrence_id: CalendarDateTime,
    summary: str,
    start: CalendarDateTime,
    end: CalendarDateTime,
) -> CalendarEvent:
    return CalendarEvent(uid, "/".join(_value_key(recurrence_id)), summary, _to_temporal(start), _to_temporal(end))


def _apply_override(
    base_start: CalendarDateTime,
    base_duration: timedelta,
    base_summary: str,
    override: _Entry,
) -> tuple[CalendarDateTime, CalendarDateTime, str]:
    override_start = override.start
    override_duration = _duration(override.component, override.start, _decoded(override.component, "DTEND"))
    override_end_present = bool(_properties(override.component, "DTEND") or _properties(override.component, "DURATION"))
    if not override_end_present:
        override_duration = base_duration
    return override_start, _add_duration(override_start, override_duration), _summary(override.component, base_summary)


def _expand_series(
    master: _Entry,
    exceptions: tuple[_Entry, ...],
    day: date,
    zone: tzinfo,
) -> list[CalendarEvent]:
    if _is_cancelled(master.component):
        return []
    recurrence_set, rdate_durations = _build_recurrence_set(master)
    base_duration = _duration(master.component, master.start, _decoded(master.component, "DTEND"))
    range_overrides = sorted(
        (entry for entry in exceptions if entry.range_future),
        key=lambda entry: _sort_value(entry.recurrence_id or entry.start),
    )
    exact_overrides = {
        _value_key(entry.recurrence_id): entry
        for entry in exceptions
        if entry.recurrence_id is not None and not entry.range_future
    }

    max_duration = max([base_duration, *rdate_durations.values()], default=base_duration)
    lower, upper = _rule_bounds(day, zone, master.start, max_duration)
    try:
        candidate_datetimes = recurrence_set.between(lower, upper, inc=True)
    except Exception:
        raise CalendarDataError from None

    events: list[CalendarEvent] = []
    emitted: set[tuple[str, str]] = set()
    candidates = [_as_component_value(value, master.start) for value in candidate_datetimes]

    for recurrence_id in candidates:
        key = _value_key(recurrence_id)
        start = recurrence_id
        duration = rdate_durations.get(key, base_duration)
        summary = _summary(master.component)
        future_override: _Entry | None = None
        for override in range_overrides:
            assert override.recurrence_id is not None
            if _sort_value(override.recurrence_id) <= _sort_value(recurrence_id):
                future_override = override
            else:
                break
        if future_override is not None:
            if _is_cancelled(future_override.component):
                continue
            assert future_override.recurrence_id is not None
            offset = future_override.start - future_override.recurrence_id
            start = _shift_wall(recurrence_id, offset)
            future_duration = _duration(
                future_override.component,
                future_override.start,
                _decoded(future_override.component, "DTEND"),
            )
            has_future_duration = bool(
                _properties(future_override.component, "DTEND")
                or _properties(future_override.component, "DURATION")
            )
            if has_future_duration:
                duration = future_duration
            summary = _summary(future_override.component, summary)

        exact_override = exact_overrides.get(key)
        if exact_override is not None:
            if _is_cancelled(exact_override.component):
                continue
            start, end, summary = _apply_override(start, duration, summary, exact_override)
        else:
            end = _add_duration(start, duration)

        if _overlaps_day(start, end, day, zone):
            events.append(_event_from_values(master.uid, recurrence_id, summary, start, end))
            emitted.add(key)

    # Detached instances can be moved into today even when their original recurrence ID is outside it.
    for override in exceptions:
        if override.recurrence_id is None or _is_cancelled(override.component):
            continue
        key = _value_key(override.recurrence_id)
        if key in emitted:
            continue
        has_override_duration = bool(
            _properties(override.component, "DTEND") or _properties(override.component, "DURATION")
        )
        override_duration = (
            _duration(override.component, override.start, _decoded(override.component, "DTEND"))
            if has_override_duration
            else base_duration
        )
        end = _add_duration(override.start, override_duration)
        if _overlaps_day(override.start, end, day, zone):
            events.append(
                _event_from_values(
                    master.uid,
                    override.recurrence_id,
                    _summary(override.component, _summary(master.component)),
                    override.start,
                    end,
                )
            )
            emitted.add(key)
    return events


def events_for_local_day(
    payloads: Iterable[str | bytes],
    day: date,
    *,
    system_tz: tzinfo,
) -> tuple[CalendarEvent, ...]:
    """VEVENT群から指定したシステム日の重複しない予定を生成する。"""
    entries = _parse_payloads(payloads)
    by_uid: dict[str, list[_Entry]] = {}
    for entry in entries:
        by_uid.setdefault(entry.uid, []).append(entry)

    events: list[CalendarEvent] = []
    for uid, group in by_uid.items():
        masters = [entry for entry in group if entry.recurrence_id is None]
        exceptions = tuple(entry for entry in group if entry.recurrence_id is not None)
        if masters:
            master = max(masters, key=lambda entry: entry.sequence)
            if (
                _properties(master.component, "RRULE")
                or _properties(master.component, "RDATE")
                or _properties(master.component, "EXRULE")
            ):
                events.extend(_expand_series(master, exceptions, day, system_tz))
            elif not _is_cancelled(master.component) and _overlaps_day(master.start, master.end, day, system_tz):
                events.append(
                    _event_from_values(uid, master.start, _summary(master.component), master.start, master.end)
                )
        else:
            # EDS may return a detached component even if its master was removed.
            for exception in exceptions:
                if not _is_cancelled(exception.component) and _overlaps_day(
                    exception.start, exception.end, day, system_tz
                ):
                    events.append(
                        _event_from_values(
                            uid,
                            exception.recurrence_id or exception.start,
                            _summary(exception.component),
                            exception.start,
                            exception.end,
                        )
                    )

    unique: dict[tuple[str, str], CalendarEvent] = {}
    for event in events:
        key = (event.uid, event.instance_id)
        unique[key] = event
    return tuple(
        sorted(
            unique.values(),
            key=lambda event: (
                _display_order(event.start, system_tz),
                event.uid,
                event.instance_id,
            ),
        )
    )
