"""追加地図ページ。MapsModuleの原点・回転・地図/メモ領域を維持する。"""

from dataclasses import dataclass
import math

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .booklet_layout import create_layout, render_booklet
from .i18n import gettext_for, language_code, using_language
from .map_rendering import draw_walking_map
from .printing_fonts import VARELA, WORK_SANS, register_printing_fonts

MAP_RECT = (294.0, 28.0, 493.0, 493.0)
MAP_FOLD_CENTERS = ((3.0, 538.0), (782.0, 535.0))


@dataclass(frozen=True)
class MapPageWarning:
    code: str
    count: int = 1


class MapPageError(Exception):
    def __init__(self, reason):
        self.reason = reason if reason in ("overflow", "source-unavailable", "invalid-route") else "source-unavailable"
        super().__init__(self.reason)


def _text(ctx, value, x, y, width, size, *, lines=1, family=VARELA):
    layout = create_layout(ctx, width=width)
    font = Pango.FontDescription(family)
    font.set_size(round(size * Pango.SCALE))
    layout.set_font_description(font)
    layout.set_text(value, -1)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_single_paragraph_mode(True)
    layout.set_height(-lines)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)
    return layout.is_ellipsized() or "\n" in value or "\r" in value


def map_page_painter(sheet, *, warnings=None):
    """重い地図読込を含むため生成ワーカーで実行するcallbackを返す。"""
    notices = warnings if warnings is not None else []
    captured_language = language_code()
    _ = gettext_for(captured_language)
    if not register_printing_fonts():
        raise RuntimeError("Bundled printing fonts are unavailable.")
    departure_name, destination_name = sheet.departure.name, sheet.destination.name

    def paint(ctx, width, height):
        ctx.save()
        try:
            # MapsModule.draw: 余白の後、局所pivot(787,541)で−180度。
            ctx.translate(787, 541)
            ctx.rotate(-math.pi)
            result = draw_walking_map(ctx, sheet, x=MAP_RECT[0], y=MAP_RECT[1],
                                      width=MAP_RECT[2], height=MAP_RECT[3])
            if result.error:
                raise MapPageError(result.error)
            if result.content_overflow:
                raise MapPageError("overflow")
            ctx.set_source_rgb(0, 0, 0)
            _text(ctx, _("MAPS"), 0, 0, 256, 11.33)
            for name, label, top in ((departure_name, _("LEAVING FROM"), 28),
                                      (destination_name, _("GOING TO"), 73)):
                _text(ctx, label, 24, top, 232, 6)
                if _text(ctx, name, 24, top + 8, 232, 9, lines=2, family=WORK_SANS):
                    notices.append(MapPageWarning("location_name_ellipsized"))
            # 左側の既存A/B丸と5点の接続目印。
            for letter, cy in (("A", 38), ("B", 83)):
                ctx.set_source_rgb(0, 0, 0)
                ctx.arc(7, cy, 7, 0, 2 * math.pi)
                ctx.fill()
                ctx.set_source_rgb(1, 1, 1)
                _text(ctx, letter, 4, cy - 5, 12, 8.36)
            ctx.set_source_rgb(0, 0, 0)
            for i in range(5):
                ctx.arc(7, 51 + 5 * i, .3, 0, 2 * math.pi)
                ctx.fill()
            for row in range(24):
                for column in range(17):
                    ctx.arc(column * 17, 130 + row * 17, .3, 0, 2 * math.pi)
                    ctx.fill()
            for cx, cy in MAP_FOLD_CENTERS:
                ctx.arc(cx, cy, 3, 0, 2 * math.pi)
                ctx.fill()
        finally:
            ctx.restore()
    def fixed_language_paint(ctx, width, height):
        with using_language(captured_language):
            paint(ctx, width, height)
    return fixed_language_paint


def render_with_map(painters, sheet=None, *, main_overlay=None, warnings=None):
    """地図を指定しない場合は主ページだけ。失敗を空白の成功ページにしない。"""
    extras = () if sheet is None else (map_page_painter(sheet, warnings=warnings),)
    return render_booklet(painters, extra_pages=extras, main_overlay=main_overlay)
