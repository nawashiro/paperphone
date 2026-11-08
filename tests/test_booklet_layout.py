"""7.1の座標・回転・クリップと模式参照の限定的なラスター比較。

保存済みPDFはAndroid実出力ではない。全面画素一致は受入基準にしない。
折り目のみ同一図形として比較し、他の内容に差があることを明示的に確認する。
PopplerとCairoのメモリ画素を使用し、画像処理ライブラリや素材変更は不要。
"""

import importlib.util
from pathlib import Path
import unittest

import cairo
import gi

gi.require_version("Poppler", "0.18")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, GLib, Pango, PangoCairo, Poppler

from paperphone.booklet_layout import (
    CONTENT_HEIGHT, CONTENT_WIDTH, EXTRA_HEIGHT, EXTRA_WIDTH, FOLD_MARKS, MODULE_HEIGHT, MODULE_WIDTH,
    PAGE_HEIGHT, PAGE_MARGINS, PAGE_WIDTH, PANEL_TRANSFORMS,
    create_layout, render_booklet,
)

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "openspec/changes/migrate-to-gnome/reference"
# PdfGenerator.moveToNext + HomeActivity.pdfAction、ページ余白を含む絶対座標。
EXPECTED = (
    (28, 28, -180), (240, 28, -180), (448, 28, 0), (660, 28, 0),
    (28, 328, 0), (240, 328, 0), (448, 328, 0), (660, 328, 0),
)


def document(data):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    return Poppler.Document.new_from_stream(stream, len(data), None, None)


def raster(doc, page=0, scale=2):
    page = doc.get_page(page)
    width, height = page.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, round(width * scale), round(height * scale))
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    context.scale(scale, scale)
    page.render(context)
    surface.flush()
    return surface


def region(surface, x, y, width, height, scale=2):
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    return b"".join(
        data[row * stride + x * scale * 4:row * stride + (x + width) * scale * 4]
        for row in range(y * scale, (y + height) * scale)
    )


def pixel(surface, x, y):
    # RGB24の未使用バイトの並びに依存せず、黒白は先頭3成分で比較できる。
    offset = round(y * 2) * surface.get_stride() + round(x * 2) * 4
    return bytes(surface.get_data()[offset:offset + 3])


class BookletLayoutTests(unittest.TestCase):
    def test_page_dimensions_match_saved_coordinate_reference(self):
        doc = document(render_booklet({}))
        self.assertEqual(doc.get_n_pages(), 1)
        self.assertEqual(doc.get_page(0).get_size(), (842.0, 595.0))
        reference = document((REFERENCE / "coordinate-reference.pdf").read_bytes())
        self.assertEqual(reference.get_page(0).get_size(), (PAGE_WIDTH, PAGE_HEIGHT))
        self.assertEqual((MODULE_WIDTH, MODULE_HEIGHT, PAGE_MARGINS), (154, 242, 28))
        self.assertEqual(FOLD_MARKS, ((629, 28, 5, 5), (629, 565, 5, 5)))

    def test_recording_painters_observe_exact_origins_corners_and_rotations(self):
        calls = []

        def painter(index):
            def record(context, width, height):
                calls.append(index)
                self.assertEqual((width, height), (154, 242))
                for actual, wanted in zip(context.clip_extents(), (0, 0, 154, 242)):
                    self.assertAlmostEqual(actual, wanted)
                self.assertEqual(context.get_source().get_rgba(), (0, 0, 0, 1))
                self.assertEqual(list(context.copy_path()), [])
                x, y, rotation = EXPECTED[index]
                if rotation:
                    expected_corners = ((x + 154, y + 242), (x, y + 242), (x, y), (x + 154, y))
                    direction = (-1, -1)
                else:
                    expected_corners = ((x, y), (x + 154, y), (x + 154, y + 242), (x, y + 242))
                    direction = (1, 1)
                for point, expected in zip(((0, 0), (154, 0), (154, 242), (0, 242)), expected_corners):
                    for actual, wanted in zip(context.user_to_device(*point), expected):
                        self.assertAlmostEqual(actual, wanted)
                self.assertAlmostEqual(context.user_to_device_distance(1, 0)[0], direction[0])
                self.assertAlmostEqual(context.user_to_device_distance(0, 1)[1], direction[1])
                # 次のcallbackへ描画状態と未完のパスを漏らさない。
                context.translate(100, 200)
                context.set_source_rgb(1, 0, 0)
                context.rectangle(-1000, -1000, 2000, 2000)
            return record

        render_booklet({index: painter(index) for index in reversed(range(8))})
        self.assertEqual(calls, list(range(8)))
        self.assertEqual(tuple((p.x, p.y, p.rotation_degrees) for p in PANEL_TRANSFORMS), EXPECTED)

    def test_geometry_agrees_with_original_reference_script(self):
        spec = importlib.util.spec_from_file_location("booklet_coordinate_reference", REFERENCE / "render_coordinate_reference.py")
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        self.assertEqual((PAGE_WIDTH, PAGE_HEIGHT), (reference.PAGE_WIDTH, reference.PAGE_HEIGHT))
        for index, panel in enumerate(PANEL_TRANSFORMS):
            x, y = reference.slot_origin(index)
            self.assertEqual((panel.x, panel.y), (x + reference.PAGE_MARGIN, y + reference.PAGE_MARGIN))

    def test_sparse_indices_do_not_shift_and_extra_pages_start_after_main(self):
        calls = []

        def main(context, width, height):
            calls.append((context.user_to_device(0, 0), width, height))
            context.rectangle(0, 0, 10, 20)
            context.fill()

        def extra(context, width, height):
            calls.append((context.user_to_device(0, 0), width, height))
            self.assertEqual(context.clip_extents(), (-28, -28, 814, 567))
            context.set_source_rgb(0, 0, 0)
            context.rectangle(0, 0, 10, 20)
            context.fill()

        alone = document(render_booklet({5: main}))
        calls.clear()
        with_extra = document(render_booklet({5: main}, extra_pages=(extra, extra)))
        self.assertEqual(calls, [((240, 328), 154, 242), ((28, 28), 787, 541), ((28, 28), 787, 541)])
        self.assertEqual((CONTENT_WIDTH, CONTENT_HEIGHT), (786, 539))
        self.assertEqual(with_extra.get_n_pages(), 3)
        for index in range(3):
            self.assertEqual(with_extra.get_page(index).get_size(), (842, 595))
        self.assertEqual(bytes(raster(alone).get_data()), bytes(raster(with_extra).get_data()))
        for index in (1, 2):
            image = raster(with_extra, index)
            self.assertEqual(pixel(image, 30, 30), b"\0\0\0")
            self.assertEqual(pixel(image, 631, 30), b"\xff\xff\xff")

    def test_extra_legacy_edges_survive_without_symmetric_margin_clipping(self):
        def legacy_edge(context, width, height):
            self.assertEqual((width, height), (EXTRA_WIDTH, EXTRA_HEIGHT))
            self.assertEqual((width, height), (787, 541))
            context.rectangle(786, 540, 3, 3)
            context.fill()

        image = raster(document(render_booklet({}, extra_pages=(legacy_edge,))), page=1)
        # MapsModuleのx787/y541は用紙内。対称余白の786×539では消えてしまう。
        self.assertEqual(pixel(image, 815, 569), b"\0\0\0")
        self.assertEqual(pixel(image, 814.5, 568.5), b"\0\0\0")
        self.assertEqual(pixel(image, 818, 572), b"\xff\xff\xff")

    def test_clipping_leaves_inherited_margins_gaps_and_unselected_slots_blank(self):
        def overflow(context, width, height):
            context.set_source_rgb(0, 0, 0)
            context.paint()  # クリップなしでは全面を塗り潰す。

        image = raster(document(render_booklet({i: overflow for i in range(8)})))
        for x, y, rotation in EXPECTED:
            self.assertEqual(pixel(image, x + 1, y + 1), b"\0\0\0")
            self.assertEqual(pixel(image, x + 153, y + 241), b"\0\0\0")
            for point in ((x - 1, y + 100), (x + 155, y + 100), (x + 70, y - 1), (x + 70, y + 243)):
                self.assertEqual(pixel(image, *point), b"\xff\xff\xff")
        empty = raster(document(render_booklet({})))
        for x, y, rotation in EXPECTED:
            self.assertEqual(pixel(empty, x + 20, y + 20), b"\xff\xff\xff")

    def test_asymmetric_marker_raster_has_inherited_orientation(self):
        def marker(context, width, height):
            context.rectangle(2, 3, 7, 11)
            context.fill()

        image = raster(document(render_booklet({i: marker for i in range(8)})))
        for x, y, rotation in EXPECTED:
            top = pixel(image, x + 5, y + 7)
            bottom = pixel(image, x + 149, y + 235)
            self.assertEqual(top, b"\xff\xff\xff" if rotation else b"\0\0\0")
            self.assertEqual(bottom, b"\0\0\0" if rotation else b"\xff\xff\xff")

    def test_only_fold_regions_match_coordinate_baseline_not_full_artwork(self):
        reference = raster(document((REFERENCE / "coordinate-reference.pdf").read_bytes()))
        candidate = raster(document(render_booklet({})))
        for x, y in ((629, 28), (629, 565)):
            # 周囲の白地も含め、位置・寸法・色の差分を検出する。
            self.assertEqual(region(candidate, x - 2, y - 2, 9, 9),
                             region(reference, x - 2, y - 2, 9, 9))
            self.assertEqual(pixel(candidate, x + 2, y + 2), b"\0\0\0")
        self.assertNotEqual(bytes(candidate.get_data()), bytes(reference.get_data()),
                            "模式資料の文字・写真枠・素材は7.1で移植していない")

    def test_pango_layout_uses_print_points_inside_rotated_panel(self):
        def painter(context, width, height):
            layout = create_layout(context, width=width)
            self.assertEqual(PangoCairo.context_get_resolution(layout.get_context()), 72)
            self.assertEqual(layout.get_width(), 154 * Pango.SCALE)
            layout.set_font_description(Pango.FontDescription.from_string("Sans 9"))
            layout.set_text("Geometry / 座標", -1)
            PangoCairo.show_layout(context, layout)

        doc = document(render_booklet({0: painter, 5: painter}))
        self.assertIn("Geometry", doc.get_page(0).get_text())

    def test_reject_invalid_indices_and_noncallable_painters_before_rendering(self):
        for key in (-1, 8, True, 0.0, "0"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                render_booklet({key: lambda *args: None})
        with self.assertRaises(TypeError):
            render_booklet({0: None})
        with self.assertRaises(TypeError):
            render_booklet({}, extra_pages=(None,))

    def test_main_overlay_is_page_origin_fullpage_after_panels_main_only(self):
        calls = []

        def panel(context, width, height):
            calls.append("panel")
            context.paint()

        def overlay(context, width, height):
            calls.append("overlay")
            self.assertEqual((width, height), (842, 595))
            self.assertEqual(context.user_to_device(0, 0), (0, 0))
            self.assertEqual(context.clip_extents(), (0, 0, 842, 595))
            self.assertEqual(list(context.copy_path()), [])
            # 区画左端660より外側も描ける。折り目の後に呼ばれる。
            context.rectangle(650, 320, 10, 10)
            context.fill()
            context.set_source_rgb(1, 1, 1)
            context.rectangle(629, 28, 5, 5)
            context.fill()

        def extra(context, width, height):
            calls.append("extra")

        doc = document(render_booklet({7: panel}, main_overlay=overlay, extra_pages=(extra,)))
        self.assertEqual(calls, ["panel", "overlay", "extra"])
        self.assertEqual(doc.get_n_pages(), 2)
        self.assertEqual(pixel(raster(doc), 652, 322), b"\0\0\0")
        self.assertEqual(pixel(raster(doc), 631, 30), b"\xff\xff\xff")
        self.assertEqual(pixel(raster(doc, 1), 652, 322), b"\xff\xff\xff")
        without = raster(document(render_booklet({})))
        explicit_none = raster(document(render_booklet({}, main_overlay=None)))
        self.assertEqual(bytes(without.get_data()), bytes(explicit_none.get_data()))
        with self.assertRaises(TypeError):
            render_booklet({}, main_overlay=42)
        with self.assertRaisesRegex(RuntimeError, "overlay failed"):
            render_booklet({}, main_overlay=lambda *args: (_ for _ in ()).throw(RuntimeError("overlay failed")))

    def test_callback_errors_propagate(self):
        def fail(*args):
            raise RuntimeError("painter failed")

        with self.assertRaisesRegex(RuntimeError, "painter failed"):
            render_booklet({0: fail})
        with self.assertRaisesRegex(RuntimeError, "painter failed"):
            render_booklet({}, extra_pages=(fail,))


if __name__ == "__main__":
    unittest.main()
