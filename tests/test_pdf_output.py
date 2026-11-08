"""人工PDF、偽ダイアログ・印刷先と一時ディレクトリだけを使う出力試験。"""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from paperphone import pdf_output
from paperphone.pdf_output import PdfOutputView, _atomic_save, _document, _


def synthetic_pdf(label="Snapshot", pages=2):
    output = io.BytesIO()
    surface = cairo.PDFSurface(output, 842, 595)
    context = cairo.Context(surface)
    for number in range(pages):
        context.set_source_rgb(0.1 * number, 0.2, 0.5)
        context.rectangle(24, 28, 120 + number * 30, 60)
        context.fill()
        context.move_to(35, 140)
        context.set_font_size(24)
        context.show_text(f"{label} page {number + 1}")
        context.show_page()
    surface.finish()
    return output.getvalue()


class Chooser:
    def set_title(self, title):
        self.title = title

    def set_initial_name(self, name):
        self.name = name

    def save(self, parent, cancellable, callback):
        self.cancellable, self.callback = cancellable, callback

    def save_finish(self, result):
        if self.error:
            raise self.error
        return self.file

    def respond(self, file=None, error=None):
        self.file, self.error = file, error
        self.callback(self, object())


class PrintContext:
    def __init__(self):
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 842, 595)
        self.cairo = cairo.Context(self.surface)
        self.cairo.set_source_rgb(1, 1, 1)
        self.cairo.paint()

    def get_cairo_context(self):
        return self.cairo

    def pixels(self):
        self.surface.flush()
        return bytes(self.surface.get_data())


class Printer:
    def __init__(self, result=Gtk.PrintOperationResult.APPLY):
        self.result, self.callbacks, self.contexts = result, {}, []
        self.cancelled = False
        self.before_draw = None

    def set_default_page_setup(self, setup):
        self.setup = setup

    def set_print_settings(self, settings):
        self.settings = settings

    def set_unit(self, unit):
        self.unit = unit

    def set_use_full_page(self, value):
        self.full_page = value

    def set_allow_async(self, value):
        self.allow_async = value

    def set_n_pages(self, value):
        self.n_pages = value

    def connect(self, name, callback):
        self.callbacks[name] = callback

    def cancel(self):
        self.cancelled = True

    def run(self, action, parent):
        self.action = action
        self.callbacks["begin-print"](self, None)
        if self.before_draw:
            self.before_draw()
        if self.result == Gtk.PrintOperationResult.APPLY:
            for page in range(self.n_pages):
                context = PrintContext()
                self.callbacks["draw-page"](self, context, page)
                self.contexts.append(context)
        self.callbacks["done"](self, self.result)
        return self.result


class PdfOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        language = patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "en"})
        language.start()
        self.addCleanup(language.stop)
        self.chooser = Chooser()
        self.printer = Printer()
        self.view = PdfOutputView(chooser_factory=lambda: self.chooser,
                                  print_factory=lambda: self.printer,
                                  dispatch=lambda callback: callback())
        self.addCleanup(self.view.close)
        self.blob = synthetic_pdf()

    def ready(self):
        self.view.set_pdf(self.blob, ("Synthetic omission warning",))

    def save(self, path):
        self.view.save_button.emit("clicked")
        self.chooser.respond(Gio.File.new_for_path(str(path)))

    def test_contract_defaults_readonly_snapshot_and_invalid_pdf_preserves_old(self):
        default = PdfOutputView()
        self.addCleanup(default.close)
        self.assertIsInstance(default, Gtk.Box)
        self.assertIsNone(default.pdf_bytes)
        self.assertFalse(default.save_button.get_sensitive())
        self.assertFalse(default.print_button.get_sensitive())
        self.ready()
        self.assertIs(self.view.pdf_bytes, self.blob)
        self.assertEqual(self.view._document.get_n_pages(), 2)
        self.assertIn("Snapshot page 1", self.view._document.get_page(0).get_text())
        self.assertEqual(self.view.warnings.get_text(), "Synthetic omission warning")
        with self.assertRaises(AttributeError):
            self.view.pdf_bytes = b"other"
        with self.assertRaises(TypeError):
            self.view.set_pdf(bytearray(self.blob))
        with self.assertRaises(GLib.Error):
            self.view.set_pdf(b"not a PDF")
        self.assertIs(self.view.pdf_bytes, self.blob)

    def test_exact_saved_bytes_and_poppler_page_content(self):
        self.ready()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "chosen.pdf"
            target.write_bytes(b"old destination")
            self.view.save_button.emit("clicked")
            # Opening a chooser is not permission to overwrite anything yet.
            self.assertEqual(target.read_bytes(), b"old destination")
            self.chooser.respond(Gio.File.new_for_path(str(target)))
            self.assertEqual(target.read_bytes(), self.blob)
            saved = _document(target.read_bytes())
            self.assertEqual(saved.get_n_pages(), self.view._document.get_n_pages())
            for page in range(saved.get_n_pages()):
                self.assertEqual(saved.get_page(page).get_text(),
                                 self.view._document.get_page(page).get_text())
            self.assertEqual(list(Path(directory).iterdir()), [target])
        self.assertEqual(self.view.status.get_text(), _("PDF saved."))
        self.assertIs(self.view.pdf_bytes, self.blob)

    def test_cancel_denial_remote_and_write_failure_preserve_destination(self):
        self.ready()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "protected.pdf"
            target.write_bytes(b"keep me")
            self.view.save_button.emit("clicked")
            self.chooser.respond(error=GLib.Error.new_literal(
                Gtk.dialog_error_quark(), "synthetic cancel", Gtk.DialogError.DISMISSED))
            self.assertEqual(self.view.status.get_text(), _("Save cancelled. PDF unchanged."))
            self.view.save_button.emit("clicked")
            self.chooser.respond(error=GLib.Error.new_literal(
                Gio.io_error_quark(), "synthetic denial", Gio.IOErrorEnum.PERMISSION_DENIED))
            self.assertIn("Could not save", self.view.status.get_text())
            self.view.save_button.emit("clicked")
            self.chooser.respond(Gio.File.new_for_uri("sftp://example.invalid/not-written.pdf"))
            self.assertIn("local file", self.view.status.get_text())
            for failing_call in ("mkstemp", "fsync", "replace"):
                module = pdf_output.tempfile if failing_call == "mkstemp" else pdf_output.os
                with self.subTest(failing_call=failing_call), patch.object(
                        module, failing_call, side_effect=PermissionError("synthetic")):
                    self.save(target)
                    self.assertIn("Could not save", self.view.status.get_text())
                self.assertEqual(target.read_bytes(), b"keep me")
                self.assertEqual(list(Path(directory).iterdir()), [target])
        self.assertIs(self.view.pdf_bytes, self.blob)
        self.assertEqual(self.view._document.get_n_pages(), 2)

    def test_partial_temporary_write_never_replaces_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.pdf"
            target.write_bytes(b"old")
            real_fdopen = pdf_output.os.fdopen

            class BrokenWriter:
                def __init__(self, fd, mode):
                    self.file = real_fdopen(fd, mode)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.file.close()

                def write(self, blob):
                    self.file.write(blob[:23])
                    raise OSError("synthetic full disk")

            with patch.object(pdf_output.os, "fdopen", BrokenWriter):
                with self.assertRaises(OSError):
                    _atomic_save(str(target), self.blob)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_preview_navigation_and_print_render_all_same_poppler_pages(self):
        self.ready()
        document = self.view._document
        calls = []

        class RecordedDocument:
            def get_n_pages(self):
                return document.get_n_pages()

            def get_page(self, number):
                source = document.get_page(number)

                class Page:
                    def get_size(self):
                        return source.get_size()

                    def render(self, context):
                        calls.append(number)
                        source.render(context)
                return Page()

        self.view._document = RecordedDocument()
        preview_pixels = []
        for page in range(2):
            context = PrintContext()
            self.view._draw_preview(self.view.preview, context.cairo, 842, 595)
            preview_pixels.append(context.pixels())
            if page == 0:
                self.view.next_button.emit("clicked")
        self.assertEqual(calls, [0, 1])
        self.assertFalse(self.view.next_button.get_sensitive())
        self.assertTrue(self.view.previous_button.get_sensitive())
        self.view.print_button.emit("clicked")
        self.assertEqual(calls, [0, 1, 0, 1])
        self.assertEqual(self.printer.n_pages, 2)
        self.assertEqual([c.pixels() for c in self.printer.contexts], preview_pixels)
        self.assertTrue(any(byte != 255 for byte in preview_pixels[0]))
        self.assertNotEqual(preview_pixels[0], preview_pixels[1])
        self.assertEqual(self.printer.unit, Gtk.Unit.POINTS)
        self.assertTrue(self.printer.full_page)
        self.assertFalse(self.printer.allow_async)
        self.assertEqual(self.printer.setup.get_orientation(), Gtk.PageOrientation.LANDSCAPE)
        self.assertEqual(self.printer.setup.get_paper_size().get_name(), Gtk.PAPER_NAME_A4)
        self.assertEqual(self.printer.settings.get_scale(), 100)
        self.assertEqual(self.printer.settings.get_number_up(), 1)
        self.assertEqual(self.printer.action, Gtk.PrintOperationAction.PRINT_DIALOG)
        self.assertEqual(self.view.status.get_text(), _(
            "Print operation accepted. Physical printing is not confirmed."))
        self.assertIs(self.view.pdf_bytes, self.blob)

    def test_real_gtk_draw_callbacks_export_content_without_dialog_or_printer(self):
        self.ready()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "synthetic-print-export.pdf"
            actual = Gtk.PrintOperation.new()
            actual.set_export_filename(str(target))

            class ExportOnly:
                def __getattr__(self, name):
                    return getattr(actual, name)

                def run(self, action, parent):
                    self_action = Gtk.PrintOperationAction.EXPORT
                    return actual.run(self_action, None)

            self.view._print_factory = ExportOnly
            self.view.print_button.emit("clicked")
            self.assertTrue(target.exists(), self.view.status.get_text())
            printed = _document(target.read_bytes())
            self.assertEqual(printed.get_n_pages(), 2)
            for page in range(2):
                self.assertEqual(printed.get_page(page).get_text(),
                                 self.view._document.get_page(page).get_text())
            self.assertEqual(self.view.status.get_text(), _(
                "Print operation accepted. Physical printing is not confirmed."))
        self.assertIs(self.view.pdf_bytes, self.blob)

    def test_print_cancel_and_error_do_not_claim_success(self):
        self.ready()
        for result, expected in ((Gtk.PrintOperationResult.CANCEL, "Print cancelled"),
                                 (Gtk.PrintOperationResult.ERROR, "Could not print")):
            self.printer.result = result
            self.view.print_button.emit("clicked")
            self.assertIn(expected, self.view.status.get_text())
            self.assertNotIn("accepted", self.view.status.get_text())
            self.assertIs(self.view.pdf_bytes, self.blob)
        self.assertEqual(self.printer.contexts, [])

    def test_print_callback_failure_cancels_and_does_not_claim_acceptance(self):
        self.ready()
        with patch.object(PrintContext, "get_cairo_context", side_effect=RuntimeError("synthetic")):
            self.view.print_button.emit("clicked")
        self.assertTrue(self.printer.cancelled)
        self.assertIn("Could not print", self.view.status.get_text())

    def test_clear_close_and_regeneration_invalidate_pending_save(self):
        self.ready()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "untouched.pdf"
            target.write_bytes(b"keep")
            for invalidate in (self.view.clear_pdf,
                               lambda: self.view.set_pdf(synthetic_pdf("Replacement", 1)),
                               self.view.close):
                self.view.set_pdf(self.blob)
                self.view.save_button.emit("clicked")
                invalidate()
                self.assertTrue(self.chooser.cancellable.is_cancelled())
                self.chooser.respond(Gio.File.new_for_path(str(target)))
                self.assertEqual(target.read_bytes(), b"keep")
        self.assertIsNone(self.view.pdf_bytes)
        self.assertFalse(self.view.print_button.get_sensitive())
        with self.assertRaises(RuntimeError):
            self.view.set_pdf(self.blob)

    def test_print_captures_document_before_reentrant_snapshot_change(self):
        self.ready()
        self.printer.before_draw = lambda: self.view.set_pdf(synthetic_pdf("New", 1))
        self.view.print_button.emit("clicked")
        self.assertTrue(self.printer.cancelled)
        self.assertEqual(len(self.printer.contexts), 2)
        self.assertEqual(self.view._document.get_n_pages(), 1)
        self.assertIn("PDF ready", self.view.status.get_text())


if __name__ == "__main__":
    unittest.main()
