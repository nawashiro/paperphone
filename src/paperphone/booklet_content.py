"""既存Front/Contacts/Calendar/Tasks/WeatherModuleの154×242pt紙面。

選択済みGenerationSnapshotだけを読む。ソース読取、時計、保存、ログは使わない。
長文と超過件数は個人情報を持たないContentWarningでプレビュー側へ返す。
写真・メモ等は別の合成層が未指定の区画へ追加する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import math

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .booklet_layout import Painter, create_layout, render_booklet
from .events import CalendarEvent
from .i18n import gettext_for, language_code, using_language
from .printing_fonts import VARELA, WORK_SANS, register_printing_fonts
from .selection import GenerationSnapshot, ItemKey, SelectableItem
from .temporal import to_system_local
from .weather_sheet import WeatherSheet, WeatherRow
from .weather_rendering import draw_weather_sheet


@dataclass(frozen=True, slots=True)
class ContentWarning:
    """安全な構造化警告。キー・件名・電話・本文等を一切含まない。

    panelは固定index、codeはtext_ellipsized/items_omitted/invalid_date等の固定値。
    countは省略件数（長文なら省略フィールド数）。
    """

    panel: int
    code: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class CoreBookletResult:
    pdf: bytes = field(repr=False)
    warnings: tuple[ContentWarning, ...]


def calendar_item(event: CalendarEvent, source_uid: str, system_tz: tzinfo) -> SelectableItem:
    """予定を不変生成用に投影する。終日終了日はiCalendarの排他的境界のまま。

    時刻付きは対象日時に有効なオフセットで変換する。現在の固定時差は使わない。
    """
    start = to_system_local(event.start, system_tz=system_tz)
    end = to_system_local(event.end, system_tz=system_tz)
    return SelectableItem(ItemKey(source_uid, event.uid, event.instance_id), "calendar", {
        "title": event.summary,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": str(event.all_day).lower(),
    })


def _cover_date(value, pattern):
    # GTKはLC_TIMEも初期化する。ワーカーからプロセス全体のlocaleを変更せず、
    # 英語の%Bだけを明示してgettextの英日設定と一致させる。
    months = ("January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")
    return value.strftime(pattern.replace("%B", months[value.month - 1]))


def _warn(warnings, panel, code, count=1):
    for index, warning in enumerate(warnings):
        if warning.panel == panel and warning.code == code:
            warnings[index] = ContentWarning(panel, code, warning.count + count)
            return
    warnings.append(ContentWarning(panel, code, count))


def _text(ctx, value, x, y, width, size, *, panel, warnings, family=VARELA):
    """一行を明示的に省略し、クリップに頼らず検知する。"""
    layout = create_layout(ctx, width=width)
    font = Pango.FontDescription(family)
    font.set_size(round(size * Pango.SCALE))
    layout.set_font_description(font)
    layout.set_single_paragraph_mode(True)
    layout.set_text(value, -1)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_height(-1)
    if layout.is_ellipsized() or "\n" in value or "\r" in value:
        _warn(warnings, panel, "text_ellipsized")
    ctx.set_source_rgb(0, 0, 0)
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)


def _calendar_period(fields, today, _, warnings):
    try:
        if fields.get("all_day") == "true":
            first = date.fromisoformat(fields["start"])
            last = date.fromisoformat(fields["end"]) - timedelta(days=1)
            dates = first.isoformat() if last <= first else f"{first.isoformat()}–{last.isoformat()}"
            return f"{_('All day')} · {dates}"
        start = datetime.fromisoformat(fields["start"])
        end = datetime.fromisoformat(fields["end"])
        def fmt(value):
            return value.strftime("%H:%M" if value.date() == today else "%m-%d %H:%M")
        return f"{fmt(start)}–{fmt(end)}"
    except (KeyError, ValueError, TypeError):
        _warn(warnings, 1, "invalid_date")
        return ""


def _due_label(value, today, _, warnings):
    if not value:
        return ""
    try:
        # TodoRecord already converted this value in the snapshot's system zone.
        parsed = datetime.fromisoformat(value) if len(value) > 10 else date.fromisoformat(value)
        day = parsed.date() if isinstance(parsed, datetime) else parsed
        distance = (day - today).days
        if distance == 0:
            relative = _("Today")
        elif distance == 1:
            relative = _("Tomorrow")
        elif distance > 1:
            relative = _("In {days} days").format(days=distance)
        else:
            relative = _("{days} days ago").format(days=-distance)
        if isinstance(parsed, datetime):
            relative += parsed.strftime(" %H:%M")
        return _("Due: {when}").format(when=relative)
    except (TypeError, ValueError):
        _warn(warnings, 2, "invalid_date")
        return ""


def _snapshot_weather(snapshot):
    for item in snapshot.items:
        if item.category != "weather":
            continue
        fields = dict(item.fields)
        def dt(key):
            return datetime.fromisoformat(fields[key]) if fields.get(key) else None
        rows = []
        for hour in (9, 12, 18):
            prefix = f"forecast_{hour}_"
            actual = dt(prefix + "actual_at")
            rows.append(WeatherRow(hour, actual, dt(prefix + "period_end"),
                float(fields[prefix + "temperature_c"]) if actual else None,
                fields.get(prefix + "symbol") or None,
                fields.get(prefix + "symbol_known") == "true"))
        return WeatherSheet(tuple(rows), dt("fetched_at"), dt("checked_at"), dt("expires_at"),
                            fields.get("deprecated") == "true", fields.get("expired") == "true",
                            fields["attribution"])
    return None


def core_painters(snapshot: GenerationSnapshot, *, name: str,
                  weather: WeatherSheet | None = None,
                  warnings: list[ContentWarning]) -> dict[int, Painter]:
    """合成用callbackを返す。警告はrender_bookletでcallback実行後に参照する。

    選択されたカテゴリだけを固定位置へ置く（表紙は常に5）。weatherは不変モデルを
    渡せるが、snapshotにweatherがある場合はその凍結内容を優先する。
    翻訳と日付も生成時点で捕捉し、callback内で現在の設定・ソースを再読込しない。
    同梱フォントを登録できない場合は個人値を含まないRuntimeErrorで中断する。
    """
    if not register_printing_fonts():
        raise RuntimeError("Bundled printing fonts are unavailable.")
    captured_language = language_code()
    _ = gettext_for(captured_language)
    try:
        captured_zone = ZoneInfo(snapshot.timezone_id)
    except (ZoneInfoNotFoundError, ValueError):
        # /etc/localtime由来の名前や固定時差はIANAキーでないことがある。
        # その場合は生成時刻自身が保持する捕捉済みtzinfoを使う。
        captured_zone = snapshot.created_at.tzinfo
    today = snapshot.created_at.astimezone(captured_zone).date()
    groups = {
        0: tuple(dict(i.fields) for i in snapshot.items if i.category == "contacts"),
        1: tuple(dict(i.fields) for i in snapshot.items if i.category == "calendar"),
        2: tuple(dict(i.fields) for i in snapshot.items if i.category in ("today", "upcoming", "undated")),
    }
    def cover(ctx, width, height):
        # 見出しは翻訳カタログの適用外。氏名以外の文言はどの言語でも同じにする。
        _text(ctx, "{name}'S".format(name=name.upper()), 0, 0, width, 17.78,
              panel=5, warnings=warnings)
        _text(ctx, "PHONE", 0, 24, width, 17.78, panel=5, warnings=warnings)
        # FrontModule.toExtent: dd MMMM yyyy。日本語カタログは年月日の数値形式へ。
        _text(ctx, _cover_date(today, _("%d %B %Y")), 0, height - 12, width, 9,
              panel=5, warnings=warnings)
    painters = {5: cover}
    for panel, rows in groups.items():
        if not rows:
            continue
        def paint(ctx, width, height, panel=panel, rows=rows):
            title = {0: _("CONTACTS"), 1: _("CALENDAR"), 2: _("TASKS")}[panel]
            _text(ctx, title, 0, 0, width, 11.33, panel=panel, warnings=warnings)
            capacity = {0: 7, 1: 6, 2: 9}[panel]
            if len(rows) > capacity:
                _warn(warnings, panel, "items_omitted", len(rows) - capacity)
            if panel == 1:
                ctx.set_line_width(.5)
                ctx.move_to(0, 34)
                ctx.line_to(width, 34)
                ctx.stroke()
            for index, fields in enumerate(rows[:capacity]):
                if panel == 0:
                    y = 24 + index * 32
                    _text(ctx, fields.get("name", "").upper(), 0, y, width, 7.31,
                          panel=panel, warnings=warnings, family=WORK_SANS)
                    _text(ctx, fields.get("phone", ""), 0, y + 9, width, 7.31,
                          panel=panel, warnings=warnings, family=WORK_SANS)
                elif panel == 1:
                    y = 54 + index * 32
                    _text(ctx, fields.get("title", ""), 0, y, width, 9,
                          panel=panel, warnings=warnings)
                    _text(ctx, _calendar_period(fields, today, _, warnings), 0, y + 11,
                          width, 6.47, panel=panel, warnings=warnings, family=WORK_SANS)
                else:
                    y = 28 + index * 22
                    ctx.set_line_width(.5)
                    ctx.arc(2.9, y + 8.5, 2.9, 0, 2 * math.pi)
                    ctx.stroke()
                    _text(ctx, fields.get("title", ""), 16, y, width - 16, 9,
                          panel=panel, warnings=warnings)
                    _text(ctx, _due_label(fields.get("due", ""), today, _, warnings),
                          16, y + 12, width - 16, 6.47, panel=panel,
                          warnings=warnings, family=WORK_SANS)
            if panel == 2:
                count = min(3 if len(rows) < 7 else max(0, 9 - len(rows)),
                            max(0, int((height - 28 - min(len(rows), capacity) * 22) // 20)))
                for index in range(count):
                    ctx.arc(2.9, 36.5 + min(len(rows), capacity) * 22 + index * 20,
                            2.9, 0, 2 * math.pi)
                    ctx.stroke()
        painters[panel] = paint
    try:
        sheet = _snapshot_weather(snapshot) or weather
    except (KeyError, TypeError, ValueError, OverflowError):
        # 不正スナップショットの個人値を例外本文へ伝播しない。
        _warn(warnings, 6, "invalid_weather")
        sheet = None
    if sheet is not None:
        def paint_weather(ctx, width, height):
            _text(ctx, _("WEATHER"), 0, 0, width, 11.33, panel=6, warnings=warnings)
            with using_language(captured_language):
                result = draw_weather_sheet(ctx, sheet, x=0, y=24, width=width,
                                            height=height - 24, compact=True)
            if result.content_overflow:
                _warn(warnings, 6, "text_ellipsized")
        painters[6] = paint_weather
    return painters


def render_core_booklet(snapshot: GenerationSnapshot, *, name: str,
                        weather: WeatherSheet | None = None) -> CoreBookletResult:
    """単体テストや最小プレビュー用。写真等との合成にはcore_paintersを使う。"""
    warnings: list[ContentWarning] = []
    pdf = render_booklet(core_painters(snapshot, name=name, weather=weather, warnings=warnings))
    return CoreBookletResult(pdf, tuple(warnings))
