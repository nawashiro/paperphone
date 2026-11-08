"""7.3: 人工写真・既存13素材・旧Canvas座標によるCairo/Poppler比較。

Android実出力との画素一致ではなく、旧ソースを根拠に独立合成したPDFとの比較。
写真／出力はメモリと一時試験ディレクトリのみ。画像処理追加パッケージは不要。
"""

import hashlib
import io
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import cairo
import gi

gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Poppler

from paperphone.booklet_assets import (
    AssetError, CONTACTLESS_SEGMENTS, PhotoError, asset_painters, capture_photo,
    draw_contactless_guides, draw_placeholder,
)
from paperphone.booklet_layout import render_booklet
from paperphone.resources import PAPER_APP_ASSETS, read_paper_app

ROOT = Path(__file__).resolve().parents[1]


def document(data):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    return Poppler.Document.new_from_stream(stream, len(data), None, None)


def raster(data):
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 1684, 1190)
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    context.scale(2, 2)
    document(data).get_page(0).render(context)
    surface.flush()
    return surface


def pixel(surface, x, y):
    offset = round(y * 2) * surface.get_stride() + round(x * 2) * 4
    return bytes(surface.get_data()[offset:offset + 3])


def synthetic_png(width=40, height=60, color=(1, 0, 0)):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    context.set_source_rgb(*color)
    context.paint()
    # 非対称の白い左上マーカーで回転方向も検証できる。
    context.set_source_rgb(1, 1, 1)
    context.rectangle(0, 0, width / 4, height / 4)
    context.fill()
    output = io.BytesIO()
    surface.write_to_png(output)
    return output.getvalue()


def legacy_dots(context, width, height):
    context.set_source_rgb(0, 0, 0)
    for row in range(15):
        for col in range(10):
            context.new_sub_path()
            context.arc(col * 17, row * 17, .3, 0, math.tau)
    context.fill()


def legacy_guides(context, width, height):
    context.set_source_rgb(0, 0, 0)
    context.set_line_width(.5)
    context.set_dash([3, 2.6], 0)
    for points in ((650, 358, 688, 320), (785, 320, 823, 358),
                   (650, 541, 688, 580), (785, 580, 823, 541)):
        context.move_to(*points[:2])
        context.line_to(*points[2:])
        context.stroke()


class BookletAssetTests(unittest.TestCase):
    def test_default_fixed_slots_and_caller_occupation_are_not_mutated(self):
        occupied = {0, 1, 2, 5, 6}
        before = occupied.copy()
        painters = asset_painters(occupied_indices=occupied)
        self.assertEqual(set(painters), {3, 4, 7})
        self.assertEqual(occupied, before)
        self.assertEqual(set(asset_painters()), set(range(8)))
        image = raster(render_booklet(painters))
        reference = raster(render_booklet({i: legacy_dots for i in (3, 4, 7)}))
        self.assertEqual(bytes(image.get_data()), bytes(reference.get_data()))

    def test_exact_dots_and_clip_in_standalone_painter(self):
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 400, 600)
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(2, 2)
        context.translate(10, 10)
        context.set_source_rgb(1, 0, 0)
        original_matrix = context.get_matrix()
        draw_placeholder(context, 154, 242)
        self.assertEqual(context.get_matrix(), original_matrix)
        self.assertEqual(context.get_source().get_rgba(), (1, 0, 0, 1))
        surface.flush()
        for col in range(1, 10):
            for row in range(1, 15):
                self.assertNotEqual(pixel(surface, 10 + col * 17, 10 + row * 17), b"\xff" * 3)
        for point in ((9.5, 27), (164, 27), (27, 9.5), (27, 252), (18, 18)):
            self.assertEqual(pixel(surface, *point), b"\xff" * 3)

    def test_notes_grid_has_13_rows_offset38_and_captured_translated_title(self):
        with patch("paperphone.booklet_assets.gettext_for", return_value=lambda text: "メモ"):
            painters = asset_painters(notes_enabled=True, occupied_indices={0, 1, 2, 3, 5, 6, 7})
        pdf = render_booklet(painters)
        self.assertIn("メモ", document(pdf).get_page(0).get_text())
        image = raster(pdf)
        for col in range(1, 10):
            for row in range(13):
                # 最終行の中心はy242（区画下端）。残る上半円だけを検査する。
                y = 328 + 38 + row * 17 - (.5 if row == 12 else 0)
                self.assertNotEqual(pixel(image, 28 + col * 17, y), b"\xff" * 3)
        for y in (345, 351, 357):
            self.assertEqual(pixel(image, 45, y), b"\xff" * 3)

    def test_photo_dimensions_monochrome_and_legacy_rotation(self):
        for width, height in ((40, 60), (60, 40), (40, 40), (10, 100), (100, 10)):
            with self.subTest(size=(width, height)):
                pdf = render_booklet(asset_painters(photo=synthetic_png(width, height),
                                                  occupied_indices={0, 1, 2, 4, 5, 6, 7}))
                image = raster(pdf)
                short, long = sorted((width, height))
                scale = min(117.5 / short, 242 / long)
                dw, dh = short * scale, long * scale
                # 赤をAndroid係数で灰色化: 255*.213=54。RGB全成分が一致する。
                center = pixel(image, 660 + dw * .7, 28 + dh * .7)
                self.assertEqual(center, bytes((54, 54, 54)))
                self.assertEqual(pixel(image, 660 + dw + 1, 28 + dh / 2), b"\xff" * 3)
                self.assertEqual(pixel(image, 660 + dw / 2, 28 + dh + 1), b"\xff" * 3)
                marker_y = dh * (.875 if width > height else .125)
                self.assertEqual(pixel(image, 660 + dw * .125, 28 + marker_y), b"\xff" * 3)
                opposite_y = dh * (.125 if width > height else .875)
                self.assertEqual(pixel(image, 660 + dw * .125, 28 + opposite_y), bytes((54, 54, 54)))

    def test_photo_alpha_is_kept_and_composited_on_white(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20, 40)
        context = cairo.Context(surface)
        context.set_source_rgba(0, 0, 0, .5)
        context.paint()
        out = io.BytesIO()
        surface.write_to_png(out)
        image = raster(render_booklet(asset_painters(photo=out.getvalue(), occupied_indices={0, 1, 2, 4, 5, 6, 7})))
        self.assertIn(pixel(image, 700, 100), (bytes((127, 127, 127)), bytes((128, 128, 128))))

    def test_photo_capture_only_reads_chosen_file_and_existing_files_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            source, existing = Path(directory) / "selected.png", Path(directory) / "existing.pdf"
            source.write_bytes(synthetic_png())
            existing.write_bytes(b"existing user PDF")
            original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (source, existing)}
            captured = capture_photo(source)
            painters = asset_painters(photo=captured)
            first = bytes(raster(render_booklet(painters)).get_data())
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original}, original)
            source.write_bytes(synthetic_png(color=(0, 1, 0)))
            self.assertEqual(bytes(raster(render_booklet(painters)).get_data()), first)
            self.assertEqual(captured, original[source][0])
            self.assertEqual(existing.read_bytes(), b"existing user PDF")

    def test_photo_errors_are_explicit_and_path_free(self):
        for data in (b"", b"not an image", "/private/photo.png", bytearray(synthetic_png())):
            with self.subTest(kind=type(data)), self.assertRaises(PhotoError) as error:
                asset_painters(photo=data)
            self.assertNotIn("private", str(error.exception))
        with self.assertRaises(PhotoError) as error:
            capture_photo("/private/nonexistent/chosen-image.png")
        self.assertNotIn("private", str(error.exception))
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("/secret/path")):
            with self.assertRaises(PhotoError) as error:
                capture_photo("chosen.png")
            self.assertNotIn("secret", str(error.exception))

    def test_all_13_assets_dimensions_and_pdf_pixels_match_legacy_stretch(self):
        self.assertEqual(len(PAPER_APP_ASSETS), 13)
        for key in PAPER_APP_ASSETS:
            with self.subTest(key=key):
                data = read_paper_app(key)
                source = ROOT / "app/src/main/res/drawable-nodpi" / (key + ".png")
                digest = hashlib.sha256(source.read_bytes()).digest()
                self.assertEqual(hashlib.sha256(data).digest(), digest)
                width, height = struct.unpack(">II", data[16:24])
                self.assertGreater(width, 154)
                self.assertGreater(height, 242)
                self.assertAlmostEqual(width / height, 154 / 242, delta=.015)
                captured = cairo.ImageSurface.create_from_png(io.BytesIO(data))

                def expected(context, w, h):
                    context.scale(154 / width, 242 / height)
                    context.set_source_surface(captured, 0, 0)
                    context.get_source().set_filter(cairo.FILTER_BILINEAR)
                    context.paint()

                painters = asset_painters(paper_app=key, occupied_indices=set(range(7)))
                actual = raster(render_booklet(painters))
                reference = raster(render_booklet({7: expected}))
                self.assertEqual(bytes(actual.get_data()), bytes(reference.get_data()))
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)

    def test_app_resource_failures_are_explicit_and_captured_before_render(self):
        for key in ("../private", "unknown"):
            with self.assertRaises(AssetError):
                asset_painters(paper_app=key)
        for error in (FileNotFoundError("private path"), OSError("broken")):
            with patch("paperphone.booklet_assets.read_paper_app", side_effect=error):
                with self.assertRaises(AssetError) as caught:
                    asset_painters(paper_app="maze")
                self.assertNotIn("private", str(caught.exception))
        with patch("paperphone.booklet_assets.read_paper_app", return_value=b"bad png"):
            with self.assertRaises(AssetError):
                asset_painters(paper_app="maze")
        painters = asset_painters(paper_app="maze")
        with patch("paperphone.booklet_assets.read_paper_app", side_effect=AssertionError("must not reread")):
            self.assertTrue(render_booklet(painters).startswith(b"%PDF"))

    def test_contactless_exact_four_global_segments_dash_and_conflict7(self):
        self.assertEqual(CONTACTLESS_SEGMENTS, ((650, 358, 688, 320), (785, 320, 823, 358),
                                              (650, 541, 688, 580), (785, 580, 823, 541)))
        self.assertNotIn(7, asset_painters(contactless=True))
        self.assertIn(7, asset_painters(contactless=True, paper_app="maze"))
        for app in (None, "maze"):
            with self.subTest(app=app):
                painters = asset_painters(contactless=True, paper_app=app)
                actual = raster(render_booklet(painters, main_overlay=draw_contactless_guides))
                expected = raster(render_booklet(painters, main_overlay=legacy_guides))
                self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))
                # 旧区画7の左端660より外側、および下端570より下にも描かれる。
                self.assertNotEqual(pixel(actual, 652, 356), b"\xff" * 3)
                self.assertNotEqual(pixel(actual, 686, 578), b"\xff" * 3)
        blank = raster(render_booklet(asset_painters(contactless=True)))
        dots = raster(render_booklet(asset_painters()))
        self.assertEqual(pixel(blank, 677, 345), b"\xff" * 3)
        self.assertNotEqual(pixel(dots, 677, 345), b"\xff" * 3)

    def test_composed_photo_dots_app_and_global_guides_match_independent_fixture(self):
        # 旧PhotoModuleの117.5pt短辺・-90度回転を別実装で合成する。
        photo = synthetic_png(60, 40)
        neutral = cairo.ImageSurface.create_from_png(io.BytesIO(synthetic_png(60, 40, (54 / 255,) * 3)))
        app = cairo.ImageSurface.create_from_png(io.BytesIO(read_paper_app("pigeon_origami")))

        def expected_photo(context, width, height):
            context.translate(0, 60 * 117.5 / 40)
            context.rotate(-math.pi / 2)
            context.scale(117.5 / 40, 117.5 / 40)
            context.set_source_surface(neutral)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.rectangle(0, 0, 60, 40)
            context.fill()

        def expected_app(context, width, height):
            context.scale(154 / app.get_width(), 242 / app.get_height())
            context.set_source_surface(app)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint()

        actual = raster(render_booklet(asset_painters(photo=photo, paper_app="pigeon_origami",
                                                      occupied_indices={5}, contactless=True),
                                       main_overlay=draw_contactless_guides))
        reference = {i: legacy_dots for i in (0, 1, 2, 4, 6)}
        reference.update({3: expected_photo, 7: expected_app})
        expected = raster(render_booklet(reference, main_overlay=legacy_guides))
        self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))

    def test_invalid_occupied_indices_and_asset_collisions(self):
        for key in (-1, 8, True, 1.5, "1"):
            with self.assertRaises(ValueError):
                asset_painters(occupied_indices={key})
        for kwargs, index in (({"photo": synthetic_png()}, 3), ({"notes_enabled": True}, 4),
                              ({"paper_app": "maze"}, 7)):
            with self.assertRaises(ValueError):
                asset_painters(occupied_indices={index}, **kwargs)


if __name__ == "__main__":
    unittest.main()
