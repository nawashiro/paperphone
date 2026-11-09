"""天気ブロックのCairo/Pango描画。出典を本文の切詰めに巻き込まない。"""

from dataclasses import dataclass
import math

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .weather_sheet import (WeatherSheet, weather_lines, known_symbol,
                            symbol_description, time_of_day)
from .i18n import gettext_for
from .printing_fonts import WORK_SANS, register_printing_fonts

_ = gettext_for()


def compact_weather_lines(sheet: WeatherSheet) -> tuple[str, ...]:
    """紙面用の短い行。時間帯見出し・罫線・アイコンは組版が担当する。"""
    lines = []
    for row in sheet.rows:
        if not row.available:
            # 過ぎた時刻や欠落した枠は印字しない。
            continue
        description = symbol_description(row.symbol)
        start = row.actual_at.strftime("%H:%M")
        lines.append(_("{t} °C ({start}–{end})").format(
            t=f"{row.temperature_c:g}", start=start, end=row.period_end.strftime("%H:%M")))
        lines.append(_(description) if description else _("Unknown weather condition"))
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


def _fits(layout, available):
    natural = layout.get_size()[1] / Pango.SCALE
    if available < 2:
        return None
    if natural > available:
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_height(round(available * Pango.SCALE))
    return min(natural, available)


def _draw_compact_body(context, sheet, *, x, y, width, height):
    lines = compact_weather_lines(sheet)
    cursor = 0
    yy = y
    overflow = False
    for row in sheet.rows:
        if not row.available:
            continue
        heading = time_of_day(row.actual_at)
        heading_layout = _layout(context, _(heading) if heading else "", width, 7.31)
        used = _fits(heading_layout, y + height - yy)
        if used is None:
            overflow = True
            break
        if heading:
            context.move_to(x, yy)
            PangoCairo.show_layout(context, heading_layout)
            yy += used + 2
            context.set_line_width(.5)
            context.move_to(x, yy)
            context.line_to(x + width, yy)
            context.stroke()
            yy += 3
        block_height = min(42, y + height - yy)
        left = 26 if row.symbol_known else 0
        if left:
            draw_weather_glyph(context, row.symbol, x=x, y=yy + 2)
        for line, size in ((lines[cursor], 8.5), (lines[cursor + 1], 6)):
            layout = _layout(context, line, width - left, size)
            used = _fits(layout, block_height)
            if used is None:
                overflow = True
                break
            context.move_to(x + left, yy)
            PangoCairo.show_layout(context, layout)
            yy += used + 1
            block_height -= used + 1
        if overflow:
            break
        cursor += 2
        yy += 4
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
