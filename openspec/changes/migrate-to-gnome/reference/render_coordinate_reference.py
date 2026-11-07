#!/usr/bin/env python3
"""Android版PDFの座標と素材から模式的な参照資料を生成する。

Androidアプリを描画したものではなく、画素精度の基準として扱わない。
架空のラベルと地図のみを使う。
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cairo

PAGE_WIDTH = 842.0
PAGE_HEIGHT = 595.0
PAGE_MARGIN = 28.0
MODULE_WIDTH = 154.0
MODULE_HEIGHT = 242.0
PADDING_1 = 58.0
PADDING_2 = 54.0

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
PAPER_APP = REPO_ROOT / "app/src/main/res/drawable-nodpi/pigeon_origami.png"


def text(cr: cairo.Context, x: float, y: float, value: str, size: float = 8.0) -> None:
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    cr.move_to(x, y)
    cr.show_text(value)


def dotted_grid(cr: cairo.Context, x: float, y: float, columns: int, rows: int, step: float = 17.0) -> None:
    cr.save()
    cr.set_source_rgb(0.32, 0.32, 0.32)
    for row in range(rows):
        for column in range(columns):
            cr.new_path()
            cr.arc(x + column * step, y + row * step, 0.35, 0, 2 * math.pi)
            cr.fill()
    cr.restore()


def slot_origin(index: int) -> tuple[float, float]:
    x_positions = (0.0, MODULE_WIDTH + PADDING_1,
                   MODULE_WIDTH * 2 + PADDING_1 + PADDING_2,
                   MODULE_WIDTH * 3 + PADDING_1 * 2 + PADDING_2)
    row, column = divmod(index, 4)
    return x_positions[column], row * (MODULE_HEIGHT + PADDING_1)


def draw_slot_outline(cr: cairo.Context, index: int, title: str) -> None:
    x, y = slot_origin(index)
    cr.save()
    cr.translate(PAGE_MARGIN + x, PAGE_MARGIN + y)
    if index in (0, 1):
        cr.translate(MODULE_WIDTH, MODULE_HEIGHT)
        cr.rotate(math.pi)
    cr.set_source_rgba(0.25, 0.25, 0.25, 0.55)
    cr.set_line_width(0.45)
    cr.set_dash([2.0, 2.0])
    cr.rectangle(0, 0, MODULE_WIDTH, MODULE_HEIGHT)
    cr.stroke()
    cr.set_dash([])
    if title:
        text(cr, 3, 10, f"SLOT {index + 1}: {title}", 6.0)
    cr.restore()


def draw_main_page(cr: cairo.Context) -> None:
    cr.set_source_rgb(0, 0, 0)

    # 位置・順序・回転はHomeActivity.pdfActionとPdfGenerator.moveToNextに従う。
    # 破線の区画線は参照用の注釈。
    labels = (
        "CONTACTS", "CALENDAR", "TASKS", "PHOTO",
        "NOTES", "COVER", "WEATHER", "",
    )
    for index, label in enumerate(labels):
        draw_slot_outline(cr, index, label)

    # 匿名の仮データ。連絡先、予定、位置情報の実データは含めない。
    x, y = slot_origin(0)
    cr.save()
    cr.translate(PAGE_MARGIN + x + MODULE_WIDTH, PAGE_MARGIN + y + MODULE_HEIGHT)
    cr.rotate(math.pi)
    text(cr, 5, 25, "CONTACTS", 8)
    text(cr, 5, 50, "SAMPLE PERSON", 6)
    text(cr, 5, 65, "000-000-0000", 6)
    cr.restore()

    x, y = slot_origin(1)
    cr.save()
    cr.translate(PAGE_MARGIN + x + MODULE_WIDTH, PAGE_MARGIN + y + MODULE_HEIGHT)
    cr.rotate(math.pi)
    text(cr, 5, 25, "CALENDAR", 8)
    text(cr, 5, 52, "SAMPLE EVENT", 7)
    text(cr, 5, 67, "09:00 - 10:00", 6)
    cr.restore()

    x, y = slot_origin(2)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 25, "TASKS", 8)
    cr.new_path()
    cr.arc(PAGE_MARGIN + x + 8, PAGE_MARGIN + y + 48, 2.5, 0, 2 * math.pi)
    cr.stroke()
    text(cr, PAGE_MARGIN + x + 16, PAGE_MARGIN + y + 50, "SAMPLE TASK", 6)
    text(cr, PAGE_MARGIN + x + 16, PAGE_MARGIN + y + 62, "TODAY", 5)
    dotted_grid(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 90, 8, 7, 17)

    x, y = slot_origin(3)
    cr.save()
    cr.set_source_rgb(0.93, 0.93, 0.93)
    cr.rectangle(PAGE_MARGIN + x + 12, PAGE_MARGIN + y + 35, 128, 165)
    cr.fill()
    cr.set_source_rgb(0.2, 0.2, 0.2)
    text(cr, PAGE_MARGIN + x + 20, PAGE_MARGIN + y + 115, "USER PHOTO OMITTED", 6)
    cr.restore()

    x, y = slot_origin(4)
    text(cr, PAGE_MARGIN + x + 4, PAGE_MARGIN + y + 20, "NOTES", 8)
    dotted_grid(cr, PAGE_MARGIN + x + 4, PAGE_MARGIN + y + 42, 9, 11)

    x, y = slot_origin(5)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 30, "SAMPLE'S", 11)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 48, "PHONE", 11)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 225, "01 JAN 2000", 6)

    x, y = slot_origin(6)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 22, "WEATHER", 8)
    text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 40, "SAMPLE PLACE", 6)
    for row, label in enumerate(("09:00  10 C  CLOUDY", "12:00  12 C  CLEAR", "18:00  11 C  RAIN")):
        y_row = PAGE_MARGIN + y + 75 + row * 42
        cr.new_path()
        cr.arc(PAGE_MARGIN + x + 13, y_row - 4, 5, 0, 2 * math.pi)
        cr.stroke()
        text(cr, PAGE_MARGIN + x + 25, y_row, label, 5.5)

    x, y = slot_origin(7)
    if PAPER_APP.is_file():
        image = cairo.ImageSurface.create_from_png(str(PAPER_APP))
        cr.save()
        cr.translate(PAGE_MARGIN + x, PAGE_MARGIN + y)
        cr.scale(MODULE_WIDTH / image.get_width(), MODULE_HEIGHT / image.get_height())
        cr.set_source_surface(image, 0, 0)
        cr.paint()
        cr.restore()
    else:
        text(cr, PAGE_MARGIN + x + 5, PAGE_MARGIN + y + 25, "PAPER APP ASSET UNAVAILABLE", 6)

    # 主ページの折り目用目印（PdfGenerator.drawFoldingHints）。
    cr.save()
    cr.set_source_rgb(0, 0, 0)
    cr.rectangle(PAGE_MARGIN + 601, PAGE_MARGIN, 5, 5)
    cr.fill()
    cr.rectangle(PAGE_MARGIN + 601, PAGE_MARGIN + 537, 5, 5)
    cr.fill()
    cr.restore()

    # カード用切り込みガイド（ContactlessModule.draw）。区画ではなく重ね描き。
    cr.save()
    cr.set_source_rgb(0.1, 0.1, 0.1)
    cr.set_line_width(0.5)
    cr.set_dash([3.0, 2.6])
    for start, end in (((650, 358), (688, 320)), ((785, 320), (823, 358)),
                       ((650, 541), (688, 580)), ((785, 580), (823, 541))):
        cr.move_to(*start)
        cr.line_to(*end)
        cr.stroke()
    cr.restore()


def draw_fake_map(cr: cairo.Context) -> None:
    """MapsModuleと同じ地図領域に架空の経路図を描く。"""
    cr.save()
    cr.translate(294, 28)
    cr.set_source_rgb(0.95, 0.95, 0.95)
    cr.rectangle(0, 0, 493, 493)
    cr.fill()
    cr.set_source_rgb(0.68, 0.68, 0.68)
    cr.set_line_width(3)
    for points in (
        ((0, 90), (110, 120), (220, 80), (350, 145), (493, 115)),
        ((20, 280), (145, 240), (270, 300), (390, 255), (493, 290)),
        ((100, 0), (130, 130), (90, 250), (145, 390), (130, 493)),
        ((350, 0), (320, 130), (370, 260), (330, 370), (355, 493)),
    ):
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.stroke()
    cr.set_source_rgb(0.15, 0.35, 0.65)
    cr.set_line_width(4)
    cr.move_to(55, 405)
    cr.curve_to(140, 330, 180, 360, 235, 260)
    cr.curve_to(285, 160, 380, 195, 430, 85)
    cr.stroke()
    cr.set_source_rgb(0.05, 0.05, 0.05)
    cr.new_path()
    cr.arc(55, 405, 7, 0, 2 * math.pi)
    cr.fill()
    cr.new_path()
    cr.arc(430, 85, 7, 0, 2 * math.pi)
    cr.fill()
    text(cr, 8, 482, "FICTIONAL MAP — NOT FOR NAVIGATION", 7)
    cr.restore()


def draw_map_page(cr: cairo.Context) -> None:
    # MapsModule.drawと同じ変換と論理配置を使う。
    cr.translate(PAGE_MARGIN, PAGE_MARGIN)
    cr.translate(787, 541)
    cr.rotate(-math.pi)
    text(cr, 0, 14, "MAPS", 11)
    draw_fake_map(cr)

    # 元モジュールと同じ領域に、簡略化した出発地・目的地の欄を描く。
    cr.set_source_rgb(0, 0, 0)
    cr.new_path()
    cr.arc(7, 38, 7, 0, 2 * math.pi)
    cr.fill()
    cr.new_path()
    cr.arc(7, 83, 7, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    text(cr, 4, 41, "A", 7)
    text(cr, 4, 86, "B", 7)
    cr.set_source_rgb(0, 0, 0)
    text(cr, 24, 34, "LEAVING FROM", 6)
    text(cr, 24, 49, "SAMPLE ORIGIN", 8)
    text(cr, 24, 71, "GOING TO", 6)
    text(cr, 24, 86, "SAMPLE DESTINATION", 8)
    dotted_grid(cr, 0, 130, 17, 24)

    # MapsModule.drawFoldHintsの地図ページ用折り目目印。
    cr.set_source_rgb(0, 0, 0)
    cr.new_path()
    cr.arc(3, 538, 3, 0, 2 * math.pi)
    cr.fill()
    cr.new_path()
    cr.arc(785, 538, 3, 0, 2 * math.pi)
    cr.fill()
    cr.show_page()


def render(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    surface = cairo.PDFSurface(str(output), PAGE_WIDTH, PAGE_HEIGHT)
    cr = cairo.Context(surface)
    draw_main_page(cr)
    cr.show_page()
    draw_map_page(cr)
    surface.finish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=SCRIPT_DIR / "coordinate-reference.pdf",
        help="出力するPDFのパス",
    )
    args = parser.parse_args()
    render(args.output.resolve())
    print(f"座標参照PDFを作成しました（Android実レンダリングではありません）: {args.output.resolve()}")


if __name__ == "__main__":
    main()
