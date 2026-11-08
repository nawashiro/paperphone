"""写真・メモ・既存紙アプリとカードガイドのUI非依存描画。

Android PhotoModule/NotesModules/PaperAppModule/PlaceHolderModule/ContactlessModule
およびHomeActivity.pdfActionの区画順を移植する。ファイル保存・探索・印刷はしない。
写真は明示選択したファイルのバイト列だけを生成時に捕捉し、描画中に再読込しない。
"""

from __future__ import annotations

import io
import math
from pathlib import Path
import struct

import cairo

from .booklet_layout import Painter, create_layout
from .i18n import gettext_for
from .printing_fonts import VARELA, register_printing_fonts
from .resources import PAPER_APP_ASSETS, read_paper_app

PHOTO_SHORT_EDGE = 117.5
CONTACTLESS_SEGMENTS = (
    (650.0, 358.0, 688.0, 320.0),
    (785.0, 320.0, 823.0, 358.0),
    (650.0, 541.0, 688.0, 580.0),
    (785.0, 580.0, 823.0, 541.0),
)


class PhotoError(ValueError):
    """選択写真の読取／復号失敗。メッセージに個人パスや画像内容を含めない。"""


class AssetError(ValueError):
    """選択した同梱素材が利用できない。無言で空欄に置き換えない。"""


def _photo_surface(data: bytes) -> cairo.ImageSurface:
    if not isinstance(data, bytes):
        raise PhotoError("写真は選択済みの画像バイト列で指定してください")
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader.new()
        loader.write(data)
        loader.close()
        image = loader.get_pixbuf()
        if image is None or image.get_width() <= 0 or image.get_height() <= 0:
            raise ValueError("empty image")
        width, height = image.get_width(), image.get_height()
        pixels = image.get_pixels()
        channels, stride = image.get_n_channels(), image.get_rowstride()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        target = surface.get_data()
        for y in range(height):
            for x in range(width):
                offset = y * stride + x * channels
                r, g, b = pixels[offset:offset + 3]
                # Android ColorMatrix.setSaturation(0)の係数。アルファは維持する。
                gray = min(255, round(.213 * r + .715 * g + .072 * b))
                alpha = pixels[offset + 3] if channels == 4 else 255
                gray = (gray * alpha + 127) // 255
                struct.pack_into("=I", target, y * surface.get_stride() + x * 4,
                                 (alpha << 24) | (gray << 16) | (gray << 8) | gray)
        surface.mark_dirty()
        return surface
    except Exception:
        raise PhotoError("選択した写真を読み込めません") from None


def capture_photo(path: str | Path) -> bytes:
    """利用者が明示選択した1ファイルのみを読み、検証済み不変バイト列を返す。

    呼出側がファイル選択の承認を担当する。失敗はパスを含まないPhotoError。
    元ファイル・既存PDF・一時ディレクトリへの書込みは一切行わない。
    """
    try:
        data = Path(path).read_bytes()
    except (OSError, ValueError, TypeError):
        raise PhotoError("選択した写真を読み込めません") from None
    _photo_surface(data)
    return data


def _clip(context: cairo.Context, width: float, height: float) -> None:
    context.new_path()
    context.rectangle(0, 0, width, height)
    context.clip()


def _dots(context: cairo.Context, *, rows: int, top: float = 0) -> None:
    context.set_source_rgb(0, 0, 0)
    for i in range(rows):
        for j in range(10):
            context.new_sub_path()
            context.arc(j * 17, top + i * 17, .3, 0, 2 * math.pi)
    context.fill()


def draw_placeholder(context: cairo.Context, width: float, height: float) -> None:
    """未選択区画の10列×15行。原点上のドットは区画境界でクリップする。"""
    context.save()
    try:
        _clip(context, width, height)
        _dots(context, rows=15)
    finally:
        context.restore()
        context.new_path()


def _notes_painter(title: str) -> Painter:
    def paint(context, width, height):
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo

        context.save()
        try:
            _clip(context, width, height)
            if not register_printing_fonts():
                raise AssetError("Bundled printing fonts are unavailable.")
            layout = create_layout(context, width=width)
            font = Pango.FontDescription()
            font.set_family(VARELA)
            font.set_size(round(11.33 * Pango.SCALE))
            layout.set_font_description(font)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(round(1.133 * Pango.SCALE)))
            layout.set_attributes(attrs)
            layout.set_text(title.upper(), -1)
            # Android baseline=descent-ascent。Pangoの論理行の下端を同じ基準にする。
            metrics = layout.get_context().get_metrics(font, None)
            baseline = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
            context.move_to(0, baseline - layout.get_baseline() / Pango.SCALE)
            context.set_source_rgb(0, 0, 0)
            PangoCairo.show_layout(context, layout)
            _dots(context, rows=13, top=38)
        finally:
            context.restore()
            context.new_path()
    return paint


def _photo_painter(data: bytes) -> Painter:
    surface = _photo_surface(data)
    source_width, source_height = surface.get_width(), surface.get_height()
    landscape = source_width > source_height
    short, long = sorted((source_width, source_height))

    def paint(context, width, height):
        # 旧短辺117.5ptを維持。極端な縦横比だけは切り捨てず区画へ縮小する。
        scale = min(PHOTO_SHORT_EDGE / short, width / short, height / long)
        context.save()
        try:
            _clip(context, width, height)
            if landscape:
                context.translate(0, source_width * scale)
                context.rotate(-math.pi / 2)
            context.scale(scale, scale)
            context.set_source_surface(surface, 0, 0)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.rectangle(0, 0, source_width, source_height)
            context.fill()
        finally:
            context.restore()
            context.new_path()
    return paint


def _paper_app_painter(key: str) -> Painter:
    if key not in PAPER_APP_ASSETS:
        raise AssetError("未知の紙アプリ素材です")
    try:
        surface = cairo.ImageSurface.create_from_png(io.BytesIO(read_paper_app(key)))
    except Exception:
        raise AssetError("選択した紙アプリ素材を読み込めません") from None

    def paint(context, width, height):
        context.save()
        try:
            _clip(context, width, height)
            # PaperAppModuleは縦横比補正せず154×242へ伸縮する。
            context.scale(width / surface.get_width(), height / surface.get_height())
            context.set_source_surface(surface, 0, 0)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint()
        finally:
            context.restore()
            context.new_path()
    return paint


def draw_contactless_guides(context: cairo.Context, page_width: float, page_height: float) -> None:
    """ページ原点で呼ぶ主ページ専用overlay。区画7の外側にもある旧4線を描く。"""
    context.save()
    try:
        _clip(context, page_width, page_height)
        context.set_source_rgb(0, 0, 0)
        context.set_line_width(.5)
        context.set_line_cap(cairo.LINE_CAP_BUTT)
        context.set_dash((3, 2.6), 0)
        for x1, y1, x2, y2 in CONTACTLESS_SEGMENTS:
            context.move_to(x1, y1)
            context.line_to(x2, y2)
            context.stroke()  # 各線でdash位相0を再開する。
    finally:
        context.restore()
        context.new_path()


def asset_painters(*, photo: bytes | None = None, paper_app: str | None = None,
                   notes_enabled: bool = False, occupied_indices: set[int] = frozenset(),
                   contactless: bool = False) -> dict[int, Painter]:
    """生成時に素材・翻訳を捕捉し、未選択区画を含む固定indexのpainterを返す。

    写真=3、メモ=4、紙アプリ=7。他の内容（常設表紙5を含む）のindexを
    occupied_indicesへ渡すと、その区画は返さない。選択素材との重複はエラー。
    contactlessは紙アプリ未選択時だけ7のドットを抑制し、選択素材は消さない。
    ガイド本体はrender_booklet(main_overlay=draw_contactless_guides)で別途合成する。
    画像読取失敗は生成前に明示し、空欄やドットへ黙って戻さない。
    """
    occupied = frozenset(occupied_indices)
    if any(type(i) is not int or not 0 <= i < 8 for i in occupied):
        raise ValueError("panel indices must be integers from 0 to 7")
    selected = ({3} if photo is not None else set()) | ({4} if notes_enabled else set()) | ({7} if paper_app is not None else set())
    if selected & occupied:
        raise ValueError("asset panel is already occupied")
    result = {i: draw_placeholder for i in range(8) if i not in occupied}
    if contactless and paper_app is None:
        result.pop(7, None)
    if photo is not None:
        result[3] = _photo_painter(photo)
    if notes_enabled:
        result[4] = _notes_painter(gettext_for()("Notes"))
    if paper_app is not None:
        result[7] = _paper_app_painter(paper_app)
    return result
