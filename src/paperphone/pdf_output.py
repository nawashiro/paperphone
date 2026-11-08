"""生成済みPDFだけを扱う、メモリ限定のプレビュー・保存・印刷ビュー。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Gtk, Poppler

from .i18n import gettext_for

_ = gettext_for()


def _document(blob: bytes):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(blob))
    return Poppler.Document.new_from_stream(stream, len(blob), None, None)


def _atomic_save(path: str, blob: bytes) -> None:
    """明示的に選択されたローカル保存先だけを、完成後に置換する。

    一時ファイルは選択先と同じディレクトリに0600で作り、例外時も削除する。
    元ファイルを開く・切り詰める操作や、私的なPDFキャッシュは作らない。
    """
    target = Path(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".paperphone-", suffix=".tmp",
                                         dir=target.parent)
        with os.fdopen(fd, "wb") as output:
            output.write(blob)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _cancelled(error):
    return isinstance(error, GLib.Error) and (
        error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
        or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
        or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.CANCELLED))


class PdfOutputView(Gtk.Box):
    """ホストはset_pdfで生成結果を渡し、破棄時にcloseを呼ぶ。

    chooser_factory/print_factoryはGtk.FileDialog/Gtk.PrintOperation互換の
    無引数factory。dispatchはGTKメインコンテキストにcallbackを配送する。
    保存操作中の再生成・clear・closeは未確定の選択を無効にする。
    """

    def __init__(self, *, chooser_factory=Gtk.FileDialog.new,
                 print_factory=Gtk.PrintOperation.new, dispatch=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._chooser_factory = chooser_factory
        self._print_factory = print_factory
        self._dispatch = dispatch or self._idle
        self._pdf_bytes = None
        self._document = None
        self._page = 0
        self._revision = 0
        self._closed = False
        self._chooser_cancel = None
        self._operation = None

        self.status = Gtk.Label(label=_("Generate a booklet to preview, save or print."))
        self.status.set_wrap(True)
        self.warnings = Gtk.Label(xalign=0)
        self.warnings.set_wrap(True)
        self.warnings.set_selectable(True)
        self.preview = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.preview.set_content_width(600)
        self.preview.set_content_height(425)
        self.preview.set_draw_func(self._draw_preview)
        self.preview.update_property([Gtk.AccessibleProperty.LABEL], [_("PDF preview")])
        self.previous_button = Gtk.Button(label=_("Previous page"))
        self.next_button = Gtk.Button(label=_("Next page"))
        self.page_label = Gtk.Label()
        self.previous_button.connect("clicked", lambda *_: self._navigate(-1))
        self.next_button.connect("clicked", lambda *_: self._navigate(1))
        navigation = Gtk.Box(spacing=12)
        for widget in (self.previous_button, self.page_label, self.next_button):
            navigation.append(widget)
        self.save_button = Gtk.Button(label=_("Save PDF…"))
        self.print_button = Gtk.Button(label=_("Print…"))
        self.save_button.connect("clicked", self._save)
        self.print_button.connect("clicked", self._print)
        actions = Gtk.Box(spacing=12)
        actions.append(self.save_button)
        actions.append(self.print_button)
        self.output_hint = Gtk.Label(label=_(
            "Print on A4 paper in landscape at 100% (Actual Size), one PDF page per sheet. "
            "Disable Fit to Page."))
        self.output_hint.set_wrap(True)
        folding = Gtk.Expander(label=_("Folding instructions"))
        self.fold_hint = Gtk.Label(label=_(
            "If the optional map page is printed on the reverse, lay it map-side up and fold in half so the two circles touch. "
            "Fold in half again so the squares touch, then fold in half one last time. "
            "Without a map page, use the main-page fold marks and check the cover orientation. "
            "If using the card guides, carefully cut along the dotted diagonal lines to hold a contactless card."))
        self.fold_hint.set_wrap(True)
        folding.set_child(self.fold_hint)
        for widget in (self.status, self.warnings, self.preview, navigation, actions,
                       self.output_hint, folding):
            self.append(widget)
        self._update_controls()

    @staticmethod
    def _idle(callback):
        def run():
            callback()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    def retranslate(self):
        """PDF・警告・ダイアログを保持し、固定状態文とページ表示だけを更新する。"""
        messages = (
            "Generate a booklet to preview, save or print.",
            "PDF ready. Preview, save and print use this same snapshot.",
            "PDF saved.", "Save cancelled. PDF unchanged.",
            "Print cancelled. PDF unchanged.",
            "Print operation accepted. Physical printing is not confirmed.",
            "Could not print PDF. The PDF snapshot is unchanged.",
            "Could not save PDF. Existing files and the PDF snapshot are unchanged.",
            "Choose a local file to save the PDF. The PDF snapshot is unchanged.",
        )
        current = self.status.get_text()
        for message in messages:
            if any(current == gettext_for(language)(message) for language in ("en", "ja")):
                self.status.set_text(_(message))
                break
        self.preview.update_property([Gtk.AccessibleProperty.LABEL], [_("PDF preview")])
        self._update_controls()

    @property
    def pdf_bytes(self) -> bytes | None:
        return self._pdf_bytes

    def set_pdf(self, blob: bytes, warnings=()) -> None:
        if self._closed:
            raise RuntimeError("PDF output view is closed")
        if not isinstance(blob, bytes):
            raise TypeError("PDF snapshot must be bytes")
        # 検証に失敗しても現在のプレビューを失わない。
        document = _document(blob)
        if document.get_n_pages() < 1:
            raise ValueError("PDF snapshot has no pages")
        warning_text = "\n".join(str(warning) for warning in warnings)
        self._invalidate()
        self._pdf_bytes, self._document, self._page = blob, document, 0
        self.warnings.set_text(warning_text)
        self.status.set_text(_("PDF ready. Preview, save and print use this same snapshot."))
        self._update_controls()

    def _invalidate(self):
        self._revision += 1
        if self._chooser_cancel is not None:
            self._chooser_cancel.cancel()
            self._chooser_cancel = None
        if self._operation is not None:
            self._operation.cancel()
            self._operation = None

    def clear_pdf(self) -> None:
        self._invalidate()
        self._pdf_bytes = self._document = None
        self._page = 0
        self.warnings.set_text("")
        self.status.set_text(_("Generate a booklet to preview, save or print."))
        self._update_controls()

    def close(self) -> None:
        self.clear_pdf()
        self._closed = True

    def _update_controls(self):
        count = self._document.get_n_pages() if self._document else 0
        self.page_label.set_text(_("Page {page} of {pages}").format(
            page=self._page + 1, pages=count) if count else _("No PDF"))
        self.previous_button.set_sensitive(self._page > 0)
        self.next_button.set_sensitive(self._page + 1 < count)
        self.save_button.set_sensitive(bool(count) and self._chooser_cancel is None)
        self.print_button.set_sensitive(bool(count) and self._operation is None)
        self.preview.queue_draw()

    def _navigate(self, delta):
        if self._document:
            self._page = max(0, min(self._page + delta, self._document.get_n_pages() - 1))
            self._update_controls()

    def _draw_preview(self, _area, context, width, height):
        if self._document is None or width <= 0 or height <= 0:
            return
        page = self._document.get_page(self._page)
        pw, ph = page.get_size()
        scale = min(width / pw, height / ph)
        context.save()
        try:
            context.translate((width - pw * scale) / 2, (height - ph * scale) / 2)
            context.scale(scale, scale)
            context.set_source_rgb(1, 1, 1)
            context.rectangle(0, 0, pw, ph)
            context.fill()
            page.render(context)
        finally:
            context.restore()

    def _parent(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _save(self, *_args):
        if self._pdf_bytes is None or self._closed or self._chooser_cancel is not None:
            return
        blob, revision = self._pdf_bytes, self._revision
        cancel = self._chooser_cancel = Gio.Cancellable.new()
        self._update_controls()

        def response(dialog, result):
            def finish():
                try:
                    selected = dialog.save_finish(result)
                except Exception as error:
                    if revision == self._revision and not self._closed:
                        self._chooser_cancel = None
                        self.status.set_text(_("Save cancelled. PDF unchanged.") if _cancelled(error)
                                             else _("Could not save PDF. Existing files and the PDF snapshot are unchanged."))
                        self._update_controls()
                    return
                if revision != self._revision or self._closed or cancel.is_cancelled():
                    return
                self._chooser_cancel = None
                if selected is None:
                    self.status.set_text(_("Save cancelled. PDF unchanged."))
                elif selected.get_path() is None:
                    self.status.set_text(_("Choose a local file to save the PDF. The PDF snapshot is unchanged."))
                else:
                    try:
                        _atomic_save(selected.get_path(), blob)
                    except Exception:
                        self.status.set_text(_("Could not save PDF. Existing files and the PDF snapshot are unchanged."))
                    else:
                        self.status.set_text(_("PDF saved."))
                self._update_controls()
            self._dispatch(finish)

        try:
            dialog = self._chooser_factory()
            dialog.set_title(_("Save PDF"))
            dialog.set_initial_name("paperphone.pdf")
            # FileDialog.saveの正常応答だけが、既存保存先の置換の承認となる。
            dialog.save(self._parent(), cancel, response)
        except Exception:
            self._chooser_cancel = None
            self.status.set_text(_("Could not save PDF. Existing files and the PDF snapshot are unchanged."))
            self._update_controls()

    def _print(self, *_args):
        if self._document is None or self._closed or self._operation is not None:
            return
        document, revision = self._document, self._revision
        failed = False
        finished = False

        def begin(operation, _context):
            operation.set_n_pages(document.get_n_pages())

        def draw(operation, context, page_number):
            nonlocal failed
            try:
                cairo = context.get_cairo_context()
                cairo.save()
                try:
                    # POINTS + full_page: PDFの座標を倍率1のまま出力する。
                    document.get_page(page_number).render(cairo)
                finally:
                    cairo.restore()
            except Exception:
                failed = True
                operation.cancel()

        def done(_operation, result):
            nonlocal finished
            if finished or result == Gtk.PrintOperationResult.IN_PROGRESS:
                return
            finished = True
            if revision != self._revision or self._closed:
                return
            self._operation = None
            if failed or result == Gtk.PrintOperationResult.ERROR:
                self.status.set_text(_("Could not print PDF. The PDF snapshot is unchanged."))
            elif result == Gtk.PrintOperationResult.CANCEL:
                self.status.set_text(_("Print cancelled. PDF unchanged."))
            elif result == Gtk.PrintOperationResult.APPLY:
                self.status.set_text(_("Print operation accepted. Physical printing is not confirmed."))
            self._update_controls()

        try:
            operation = self._operation = self._print_factory()
            setup = Gtk.PageSetup.new()
            setup.set_paper_size(Gtk.PaperSize.new(Gtk.PAPER_NAME_A4))
            setup.set_orientation(Gtk.PageOrientation.LANDSCAPE)
            for edge in ("top", "bottom", "left", "right"):
                getattr(setup, f"set_{edge}_margin")(0, Gtk.Unit.POINTS)
            settings = Gtk.PrintSettings.new()
            settings.set_scale(100)
            settings.set_number_up(1)
            operation.set_default_page_setup(setup)
            operation.set_print_settings(settings)
            operation.set_unit(Gtk.Unit.POINTS)
            operation.set_use_full_page(True)
            operation.set_allow_async(False)
            operation.set_n_pages(document.get_n_pages())
            operation.connect("begin-print", begin)
            operation.connect("draw-page", draw)
            operation.connect("done", done)
            self._update_controls()
            done(operation, operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, self._parent()))
        except Exception:
            failed = True
            done(self._operation, Gtk.PrintOperationResult.ERROR)
