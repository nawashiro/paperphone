"""写真選択は偽FileDialog、ローカル画像は人工PNGのみを用いるGTK試験。"""

from concurrent.futures import Future
from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from paperphone.booklet_asset_view import AssetChoices, AssetSelectionView, _
from paperphone.resources import PAPER_APP_ASSETS


def synthetic_png():
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 10, 20)
    context = cairo.Context(surface)
    context.set_source_rgb(1, 0, 0)
    context.paint()
    output = io.BytesIO()
    surface.write_to_png(output)
    return output.getvalue()


class Picker:
    def __init__(self):
        self.calls = []
        self.file = None
        self.error = None

    def open(self, parent, cancellable, callback):
        self.calls.append((parent, cancellable, callback))

    def open_finish(self, result):
        if self.error:
            raise self.error
        return self.file

    def respond(self, file=None, error=None, call=-1):
        self.file, self.error = file, error
        self.calls[call][2](self, object())


class ManualWorker:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.calls.append((future, function, args))
        return future

    def finish(self, index=-1):
        future, function, args = self.calls[index]
        try:
            future.set_result(function(*args))
        except Exception as error:
            future.set_exception(error)


class AssetSelectionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        self.picker = Picker()
        self.worker = ManualWorker()
        self.dispatched = []
        self.view = AssetSelectionView(chooser_factory=lambda: self.picker,
                                       submit=self.worker.submit,
                                       dispatch=self.dispatched.append)
        self.addCleanup(self.view.close)

    def drain(self):
        while self.dispatched:
            self.dispatched.pop(0)()

    def choose(self, file):
        self.view.choose_photo_button.emit("clicked")
        self.picker.respond(file)

    def test_defaults_and_all_13_paper_app_choices_and_no_initial_photo_read(self):
        self.assertEqual(self.view.capture_choices(), AssetChoices())
        self.assertEqual(self.picker.calls, [])
        self.assertEqual(self.worker.calls, [])
        self.assertFalse(self.view.clear_photo_button.get_sensitive())
        self.assertEqual(self.view.paper_app_dropdown.get_model().get_n_items(), 14)
        for index, key in enumerate(PAPER_APP_ASSETS, 1):
            self.view.paper_app_dropdown.set_selected(index)
            self.assertEqual(self.view.capture_choices().paper_app, key)
        self.view.paper_app_dropdown.set_selected(0)
        self.assertIsNone(self.view.capture_choices().paper_app)
        self.view.notes_checkbox.set_active(True)
        self.view.contactless_checkbox.set_active(True)
        self.view.paper_app_dropdown.set_selected(8)
        captured = self.view.capture_choices()
        self.assertTrue(captured.notes_enabled)
        self.assertTrue(captured.contactless)
        # ガイドを選んでも選択済み紙アプリは保持する（旧競合動作）。
        self.assertEqual(captured.paper_app, PAPER_APP_ASSETS[7])
        self.view.notes_checkbox.set_active(False)
        self.assertTrue(captured.notes_enabled)
        with self.assertRaises(FrozenInstanceError):
            captured.contactless = False

    def test_explicit_local_choice_captures_synthetic_file_only_and_never_displays_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-user-photo.png"
            photo = synthetic_png()
            path.write_bytes(photo)
            before = path.stat().st_mtime_ns
            self.choose(Gio.File.new_for_path(str(path)))
            self.assertEqual(len(self.worker.calls), 1)
            self.assertIsNone(self.view.capture_choices().photo)
            self.assertEqual(self.view.status.get_text(), _("Reading selected photo…"))
            self.worker.finish()
            # UIの変更はdispatch先で行い、ワーカー完了時点では反映しない。
            self.assertIsNone(self.view.capture_choices().photo)
            self.drain()
            captured = self.view.capture_choices()
            self.assertEqual(captured.photo, photo)
            self.assertNotIn("photo=", repr(captured))
            self.assertEqual(self.view.status.get_text(), "")
            self.assertNotIn(directory, self.view.status.get_text())
            self.assertEqual(path.read_bytes(), photo)
            self.assertEqual(path.stat().st_mtime_ns, before)
            path.write_bytes(b"changed after choice")
            self.assertEqual(self.view.capture_choices().photo, photo)
            self.view.clear_photo()
            self.assertIsNone(self.view.capture_choices().photo)
            self.assertEqual(captured.photo, photo)

    def test_cancel_and_dismiss_never_read_a_photo(self):
        for code in (Gtk.DialogError.CANCELLED, Gtk.DialogError.DISMISSED):
            with self.subTest(code=code):
                self.view.choose_photo_button.emit("clicked")
                error = GLib.Error.new_literal(Gtk.dialog_error_quark(), "private path", code)
                self.picker.respond(error=error)
                self.assertEqual(self.worker.calls, [])
                self.assertIsNone(self.view.capture_choices().photo)
                self.assertEqual(self.view.status.get_text(), _("Photo selection was cancelled."))
        self.view.choose_photo_button.emit("clicked")
        self.picker.respond(None)
        self.assertEqual(self.worker.calls, [])

    def test_unreadable_or_invalid_local_photo_reports_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, content in (("missing.png", None), ("broken.png", b"invalid png")):
                path = Path(directory) / name
                if content is not None:
                    path.write_bytes(content)
                self.choose(Gio.File.new_for_path(str(path)))
                self.worker.finish()
                self.drain()
                self.assertIsNone(self.view.capture_choices().photo)
                self.assertEqual(self.view.status.get_text(), _(
                    "The selected photo could not be read. Choose a readable local image."))
                self.assertNotIn(directory, self.view.status.get_text())
                self.assertNotIn(name, self.view.status.get_text())

    def test_nonlocal_uris_rejected_without_worker_or_network(self):
        for uri in ("https://invalid.example/private.png", "sftp://invalid.example/private.png"):
            self.choose(Gio.File.new_for_uri(uri))
            self.assertEqual(self.worker.calls, [])
            self.assertIsNone(self.view.capture_choices().photo)
            self.assertNotIn(uri, self.view.status.get_text())

    def test_clear_cancels_picker_and_suppresses_late_picker_callback(self):
        self.view.choose_photo_button.emit("clicked")
        cancellable = self.picker.calls[-1][1]
        self.view.clear_photo_button.emit("clicked")
        self.assertTrue(cancellable.is_cancelled())
        self.picker.respond(Gio.File.new_for_path("/synthetic/unused.png"))
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.view.status.get_text(), _("No photo selected."))

    def test_clear_or_new_choice_or_close_suppresses_late_worker_results(self):
        for operation in ("clear", "new", "close"):
            with self.subTest(operation=operation):
                picker, worker, dispatched = Picker(), ManualWorker(), []
                view = AssetSelectionView(chooser_factory=lambda: picker, submit=worker.submit,
                                          dispatch=dispatched.append, photo_loader=lambda path: b"private bytes")
                try:
                    view.choose_photo_button.emit("clicked")
                    picker.respond(Gio.File.new_for_path("/synthetic/unused.png"))
                    if operation == "clear":
                        view.clear_photo()
                    elif operation == "new":
                        view.choose_photo_button.emit("clicked")
                    else:
                        view.close()
                    status = view.status.get_text()
                    worker.finish()
                    for callback in dispatched:
                        callback()
                    self.assertIsNone(view.capture_choices().photo)
                    self.assertEqual(view.status.get_text(), status)
                finally:
                    view.close()

    def test_late_old_selection_cannot_replace_newer_photo(self):
        self.view._photo_loader = lambda path: path.encode()
        self.choose(Gio.File.new_for_path("/synthetic/first.png"))
        self.choose(Gio.File.new_for_path("/synthetic/second.png"))
        self.worker.finish(1)
        self.drain()
        expected = self.view.capture_choices().photo
        self.worker.finish(0)
        self.drain()
        self.assertEqual(self.view.capture_choices().photo, expected)
        self.assertEqual(expected, b"/synthetic/second.png")

    def test_default_worker_does_not_block_gtk_and_dispatches_completion(self):
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        called_threads, callbacks = [], []
        dispatched = threading.Event()
        picker = Picker()

        def dispatch(callback):
            callbacks.append(callback)
            dispatched.set()

        def read(_path):
            called_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test worker timed out")
            return synthetic_png()

        view = AssetSelectionView(chooser_factory=lambda: picker, photo_loader=read,
                                  dispatch=dispatch)
        try:
            view.choose_photo_button.emit("clicked")
            picker.respond(Gio.File.new_for_path("/synthetic/not-read.png"))
            self.assertTrue(entered.wait(3))
            # 読取が待機中でもUIスレッドへ制御が戻り、別の選択操作を行える。
            view.notes_checkbox.set_active(True)
            self.assertTrue(view.capture_choices().notes_enabled)
            self.assertNotEqual(called_threads, [main_thread])
            self.assertIsNone(view.capture_choices().photo)
            future = view._future
            release.set()
            future.result(timeout=3)
            # Futureの完了通知は別スレッドなのでイベントで配送も同期する。
            self.assertTrue(dispatched.wait(3))
            self.assertIsNone(view.capture_choices().photo)
            callbacks.pop(0)()
            self.assertIsNotNone(view.capture_choices().photo)
        finally:
            release.set()
            view.close()

    def test_close_cancels_picker_is_idempotent_and_no_future_dialog_opens(self):
        self.view.choose_photo_button.emit("clicked")
        cancellable = self.picker.calls[-1][1]
        self.view.close()
        self.view.close()
        self.assertTrue(cancellable.is_cancelled())
        before = self.view.status.get_text()
        self.picker.respond(Gio.File.new_for_path("/synthetic/not-read.png"))
        self.view.choose_photo_button.emit("clicked")
        self.assertEqual(len(self.picker.calls), 1)
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.view.status.get_text(), before)

    def test_default_chooser_has_local_image_filters_without_opening_dialog(self):
        chooser = AssetSelectionView._new_chooser()
        self.assertIsInstance(chooser, Gtk.FileDialog)
        self.assertEqual(chooser.get_title(), _("Choose a local photo"))
        self.assertEqual(chooser.get_filters().get_n_items(), 1)
        self.assertIsNotNone(chooser.get_default_filter())

    def test_cancelling_another_selection_preserves_prior_immutable_choice(self):
        self.view._photo_loader = lambda path: synthetic_png()
        self.choose(Gio.File.new_for_path("/synthetic/not-read.png"))
        self.worker.finish()
        self.drain()
        previous = self.view.capture_choices()
        self.view.choose_photo_button.emit("clicked")
        self.picker.respond(None)
        self.assertEqual(self.view.capture_choices(), previous)
        self.assertTrue(self.view.clear_photo_button.get_sensitive())

    def test_chooser_creation_failure_is_safe_and_close_stays_available(self):
        self.view._chooser_factory = Mock(side_effect=RuntimeError("/private/chooser"))
        self.view.choose_photo_button.emit("clicked")
        self.assertNotIn("private", self.view.status.get_text())
        self.assertEqual(self.worker.calls, [])
        self.view.close()

    def test_unexpected_picker_or_loader_failures_never_expose_paths(self):
        self.view.choose_photo_button.emit("clicked")
        self.picker.respond(error=RuntimeError("/private/path details"))
        self.assertNotIn("private", self.view.status.get_text())
        self.view._photo_loader = Mock(side_effect=PermissionError("/secret/chosen.png"))
        self.choose(Gio.File.new_for_path("/synthetic/not-read.png"))
        self.worker.finish()
        self.drain()
        self.assertNotIn("secret", self.view.status.get_text())
        self.assertIsNone(self.view.capture_choices().photo)

    def test_preview_reserves_visible_height(self):
        _width, height = self.view.preview.get_size_request()
        self.assertGreaterEqual(height, 240)


if __name__ == "__main__":
    unittest.main()
