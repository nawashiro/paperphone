"""天気ブロックのCairo/Pango描画。出典を本文の切詰めに巻き込まない。"""

from dataclasses import dataclass
import math

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .weather_sheet import WeatherSheet, weather_lines, known_symbol
from .i18n import gettext_for
from .printing_fonts import WORK_SANS, register_printing_fonts


def compact_weather_lines(sheet: WeatherSheet) -> tuple[str, ...]:
    """紙面用の短い表示。時刻だけを扱い、日付や機械向け列を載せない。"""
    _ = gettext_for()
    lines = []
    for row in sheet.rows:
        if not row.available:
            # 過ぎた時刻や欠落した枠は印字しない。
            continue
        start = row.actual_at
        lines.append(_("At {when}: {temperature} °C").format(
            when=start.strftime("%H:%M"), temperature=f"{row.temperature_c:g}"))
        lines.append(_("Weather period: {start}–{end}").format(
            start=start.strftime("%H:%M"), end=row.period_end.strftime("%H:%M")))
        lines.append(row.symbol if row.symbol_known else _("Unknown weather condition"))
    return tuple(lines)


@dataclass(frozen=True)
class WeatherRenderResult:
    content_overflow: bool


def _layout(context, text, width, size):
    layout = PangoCairo.create_layout(context)
    PangoCairo.context_set_resolution(layout.get_context(), 72)
    if not register_printing_fonts():
        raise RuntimeError("Bundled printing fonts are unavailable.")
    font = Pango.FontDescription(WORK_SANS)
    font.set_size(round(size * Pango.SCALE))
    layout.set_font_description(font)
    layout.set_width(round(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_text(text, -1)
    return layout


def draw_weather_glyph(context, code: str | None, *, x: float, y: float) -> bool:
    """19ptの自作線画。未知コードには既知の記号を絶対に代入しない。"""
    if not code or not known_symbol(code):
        return False
    context.save()
    try:
        context.translate(x, y)
        context.set_line_width(.7)
        if code.startswith(("clearsky", "fair")):
            context.arc(9.5, 9.5, 4, 0, 2 * math.pi)
            context.stroke()
            for angle in range(0, 360, 45):
                a = math.radians(angle)
                context.move_to(9.5 + 6 * math.cos(a), 9.5 + 6 * math.sin(a))
                context.line_to(9.5 + 9 * math.cos(a), 9.5 + 9 * math.sin(a))
            context.stroke()
        elif code == "fog":
            for yy in (5, 9, 13):
                context.move_to(1, yy)
                context.line_to(18, yy)
            context.stroke()
        else:
            # 雲・降水の簡略化した輪郭。
            context.move_to(3, 12)
            context.curve_to(-1, 12, -1, 6, 4, 6)
            context.curve_to(4, 0, 13, 0, 14, 6)
            context.curve_to(20, 4, 22, 12, 16, 12)
            context.close_path()
            context.stroke()
            if "thunder" in code:
                context.move_to(11, 12)
                context.line_to(7, 16)
                context.line_to(11, 16)
                context.line_to(8, 20)
            elif "snow" in code or "sleet" in code:
                for xx in (4, 10, 16):
                    context.move_to(xx - 1, 15)
                    context.line_to(xx + 1, 17)
                    context.move_to(xx + 1, 15)
                    context.line_to(xx - 1, 17)
            elif "rain" in code:
                for xx in (4, 10, 16):
                    context.move_to(xx, 14)
                    context.line_to(xx - 2, 18)
            context.stroke()
    finally:
        context.restore()
    return True


def _draw_compact_body(context, sheet, *, x, y, width, height):
    lines = compact_weather_lines(sheet)
    cursor = 1
    blocks = [(lines[0], 7.31, None, 12)]
    for row in sheet.rows:
        count = 3 if row.available else 1
        blocks.append(("\n".join(lines[cursor:cursor + count]), 7.31,
                       row.symbol if row.available and row.symbol_known else None, 42))
        cursor += count
    blocks.append(("\n".join(lines[cursor:]), 6.47, None, height))
    yy = y
    overflow = False
    for text, size, code, limit in blocks:
        left = 26 if code and known_symbol(code) else 0
        layout = _layout(context, text, width - left, size)
        natural = layout.get_size()[1] / Pango.SCALE
        available = min(limit, y + height - yy)
        if available < size:
            overflow = True
            break
        if natural > available:
            overflow = True
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_height(round(available * Pango.SCALE))
        if left:
            draw_weather_glyph(context, code, x=x, y=yy + 2)
        context.move_to(x + left, yy)
        PangoCairo.show_layout(context, layout)
        yy += min(natural, available) + 3
    return overflow


def draw_weather_sheet(context, sheet: WeatherSheet, *, x, y, width, height,
                       compact: bool = False) -> WeatherRenderResult:
    """ポイント単位の矩形へ本文と帰属を描く。超過は必ず呼出し元に返す。

    折り畳み紙面への配置は冊子生成側が担当する。帰属を収められない矩形は
    出力せず例外とし、出典を切り落として成功とみなさない。
    """
    if not all(math.isfinite(v) for v in (x, y, width, height)) or min(width, height) <= 0:
        raise ValueError("天気描画領域が不正です。")
    attribution = _layout(context, sheet.attribution, width, 6.5)
    _, footer_height = attribution.get_size()
    footer_height /= Pango.SCALE
    body_height = height - footer_height - 8
    if body_height < 12:
        raise ValueError("天気の帰属表示を収める領域がありません。")
    overflow = False
    if not compact:
        body = _layout(context, "\n".join(weather_lines(sheet)[:-1]), width, 8)
        _, text_height = body.get_size()
        overflow = text_height / Pango.SCALE > body_height
        if overflow:
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_height(round(body_height * Pango.SCALE))
    context.save()
    try:
        context.rectangle(x, y, width, height)
        context.clip()
        context.set_source_rgb(0, 0, 0)
        if compact:
            overflow = _draw_compact_body(context, sheet, x=x, y=y, width=width, height=body_height)
        else:
            context.move_to(x, y)
            PangoCairo.show_layout(context, body)
        context.move_to(x, y + height - footer_height)
        PangoCairo.show_layout(context, attribution)
    finally:
        context.restore()
    return WeatherRenderResult(content_overflow=overflow)
