"""A4横冊子の座標基盤。内容・プレースホルダー・カードガイドは描かない。

根拠: Android ui/pdf/PdfGenerator.kt の定数、moveToNext、drawFoldingHints、
HomeActivity.pdfAction の順序、および移行資料 reference/render_coordinate_reference.py。
ページは保存済み座標参照PDFと同じ842×595ptを使う。ISO A4の厳密な
841.89×595.28ptへ置換すると基準と異なるため、ここでは丸め直さない。
参照資料はAndroid実出力ではなく模式図であり、全面の画素一致は保証しない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import io
import math

import cairo

PAGE_WIDTH = 842.0
PAGE_HEIGHT = 595.0
PAGE_MARGINS = 28.0
PADDING_1 = 58.0
PADDING_2 = 54.0
MODULE_WIDTH = 154.0
MODULE_HEIGHT = 242.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * PAGE_MARGINS
CONTENT_HEIGHT = PAGE_HEIGHT - 2 * PAGE_MARGINS
# MapsModule.CANVAS_PIVOT_X/Y。対称余白の内寸とは一致しない。
EXTRA_WIDTH = 787.0
EXTRA_HEIGHT = 541.0

Painter = Callable[[cairo.Context, float, float], None]


@dataclass(frozen=True)
class PanelTransform:
    """ページ上の区画左上と局所座標の回転。単位はpt・度。"""

    index: int
    x: float
    y: float
    rotation_degrees: int = 0
    width: float = MODULE_WIDTH
    height: float = MODULE_HEIGHT

    def apply(self, context: cairo.Context) -> None:
        """ページ原点のcontextを区画の描画原点へ移す。"""
        context.translate(self.x, self.y)
        if self.rotation_degrees:
            context.translate(self.width, self.height)
            context.rotate(math.radians(self.rotation_degrees))


# 均等な4分割ではない。列の間隔は58、54、58pt、行間は58pt。
_COLUMN_OFFSETS = (
    0.0,
    MODULE_WIDTH + PADDING_1,
    2 * MODULE_WIDTH + PADDING_1 + PADDING_2,
    3 * MODULE_WIDTH + 2 * PADDING_1 + PADDING_2,
)
PANEL_TRANSFORMS = tuple(
    PanelTransform(
        index,
        PAGE_MARGINS + _COLUMN_OFFSETS[index % 4],
        PAGE_MARGINS + (index // 4) * (MODULE_HEIGHT + PADDING_1),
        -180 if index in (0, 1) else 0,
    )
    for index in range(8)
)

# PdfGenerator.drawFoldingHints: 余白移動後(601,0)、(601,537)、各5pt角。
FOLD_MARKS = (
    (PAGE_MARGINS + 601.0, PAGE_MARGINS, 5.0, 5.0),
    (PAGE_MARGINS + 601.0, PAGE_MARGINS + 537.0, 5.0, 5.0),
)


def create_layout(context: cairo.Context, *, width: float = MODULE_WIDTH):
    """区画で使える72dpiのPangoレイアウトを作る（幅はpt）。

    内容・フォント・高さ・省略方針は呼出側が決める。Pangoの通常のpt指定を
    PDFの座標単位と一致させ、画面のDPIを印刷へ持ち込まない。
    """
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    if not math.isfinite(width) or width <= 0:
        raise ValueError("layout width must be finite and positive")
    layout = PangoCairo.create_layout(context)
    PangoCairo.context_set_resolution(layout.get_context(), 72.0)
    layout.set_width(round(width * Pango.SCALE))
    return layout


def _paint(context: cairo.Context, painter: Painter, width: float, height: float) -> None:
    # save/restoreはパスを保存しないため、パスも区画ごとに明示的に分離する。
    context.new_path()
    context.rectangle(0, 0, width, height)
    context.clip()
    painter(context, width, height)
    context.new_path()


def render_booklet(
    painters: Mapping[int, Painter], *, extra_pages: Sequence[Painter] = (),
    main_overlay: Painter | None = None,
) -> bytes:
    """固定8区画と任意の追加ページをメモリ上のPDFへ描く。

    paintersのキーはHomeActivityと同じ0..7。未指定区画は空欄で、詰め直さない。
    各callback(context, width, height)には変換・クリップ済みの154×242ptを渡す。
    Pangoはcreate_layoutで利用可能。通常の区画境界線は描かない。
    main_overlayは区画と折り目の後、主ページ原点(0,0)で842×595ptを渡す。
    用紙端だけでクリップし、カードガイドなど区画を越す描画に使う。
    未指定時の出力と既存callbackの座標は変えず、追加ページには適用しない。

    extra_pagesは同じA4横で、原点(28,28)、MapsModuleの論理寸法787×541ptを渡す。
    元のPdfGeneratorと同じく追加ページは用紙端だけでクリップする。
    対称余白の内寸786×539ptでクリップすると既存の端のドットを欠くためである。
    地図固有の回転・折り目・帰属は描画側の責務であり、ここでは主ページの目印を
    複製しない。なお模式参照の地図2番目の折り目中心(785,538)は実ソースの
    (782,535)と異なるため、7.4ではMapsModule.drawFoldHintsを根拠にする。
    callbackはcontextのsave/restoreを釣り合わせ、
    reset_clip/show_pageやsurface操作をしないこと。例外は呼出側へ伝える。
    ファイル保存や既存PDFの削除は行わない。
    """
    painters = dict(painters)
    extra_pages = tuple(extra_pages)
    if any(type(index) is not int or not 0 <= index < 8 for index in painters):
        raise ValueError("panel indices must be integers from 0 to 7")
    if main_overlay is not None and not callable(main_overlay):
        raise TypeError("main_overlay must be callable")
    if any(not callable(painter) for painter in (*painters.values(), *extra_pages)):
        raise TypeError("painters must be callable")

    output = io.BytesIO()
    surface = cairo.PDFSurface(output, PAGE_WIDTH, PAGE_HEIGHT)
    try:
        context = cairo.Context(surface)
        for panel in PANEL_TRANSFORMS:
            if panel.index not in painters:
                continue
            context.save()
            try:
                panel.apply(context)
                _paint(context, painters[panel.index], panel.width, panel.height)
            finally:
                context.restore()
                context.new_path()

        context.set_source_rgb(0, 0, 0)
        for rectangle in FOLD_MARKS:
            context.rectangle(*rectangle)
        context.fill()
        if main_overlay is not None:
            context.save()
            try:
                _paint(context, main_overlay, PAGE_WIDTH, PAGE_HEIGHT)
            finally:
                context.restore()
                context.new_path()
        context.show_page()

        for painter in extra_pages:
            context.save()
            try:
                context.translate(PAGE_MARGINS, PAGE_MARGINS)
                context.new_path()
                context.rectangle(-PAGE_MARGINS, -PAGE_MARGINS, PAGE_WIDTH, PAGE_HEIGHT)
                context.clip()
                painter(context, EXTRA_WIDTH, EXTRA_HEIGHT)
            finally:
                context.restore()
                context.new_path()
            context.show_page()
    finally:
        surface.finish()
    return output.getvalue()
