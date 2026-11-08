"""配布候補環境でPDF保存と印刷を実証する。個人データは出力しない。

python3 tools/probe_save_print.py
ウィンドウが手前に出たら、その順で操作する:
  1. 権限不足の保存(自動・ダイアログなし・書込み禁止ディレクトリへのEXPORT)
  2. 保存ダイアログ(FileDialog) — 取消しても先へ進む
  3. 印刷ダイアログ(PrintOperation) — 取消でも先へ進む
出力は result=<分類> の集合値と成功条件のみ。エラーは型・ドメイン名までで、
メッセージ・パス・プリンタ名は出力しない。自動再試行はしない。
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, GLib, Gtk, PangoCairo


PROBE_TEXT = "Paper Phone — 実証ページ\nPDF保存と印刷の疎通確認"


def draw_page(operation, context, page_nr):
    layout = context.create_pango_layout()
    layout.set_markup(PROBE_TEXT, -1)
    width, height = layout.get_pixel_size()
    cairo = context.get_cairo_context()
    cairo.set_source_rgb(0, 0, 0)
    cairo.move_to((context.get_width() - width) / 2, (context.get_height() - height) / 2)
    PangoCairo.show_layout(cairo, layout)


class Printer:
    def __init__(self):
        self.results: dict[str, object] = {}

    def export_to(self, path: str) -> tuple[bool, str]:
        operation = Gtk.PrintOperation.new()
        operation.set_n_pages(1)
        operation.connect("draw-page", draw_page)
        operation.set_export_filename(path)
        try:
            result = operation.run(Gtk.PrintOperationAction.EXPORT, None)
        except GLib.Error as error:
            # パスや本文を含まないドメイン名だけを返す。
            return False, f"gerror:{getattr(error, 'domain', 'unknown')}"
        ok = result == Gtk.PrintOperationResult.APPLY
        return ok, str(result)


class Probe(Gtk.Application):
    def __init__(self, args):
        super().__init__(application_id="dev.nawashiro.PaperPhone.SavePrintProbe",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.args = args
        self.window = None
        self.results: dict[str, object] = {}

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title="実証: PDF保存と印刷")
        window.set_default_size(420, 220)
        window.set_child(Gtk.Label(label=(
            "この順に操作を促します:\n"
            " 1. 権限不足の保存(自動)\n"
            " 2. PDF保存ダイアログ(取り消しても構いません)\n"
            " 3. 印刷ダイアログ(取り消しても構いません)\n"
            "パス・プリンタ名・エラー詳細は記録しません。")))
        window.present()
        self.window = window
        GLib.idle_add(self.stage_denied_save)

    def stage_denied_save(self):
        if self.args.skip_denied:
            return self.stage_save_dialog()
        base = Path(tempfile.mkdtemp(prefix="paperphone-probe-"))
        base.chmod(stat.S_IRUSR | stat.S_IXUSR)  # 0o500: 所有者も書込み不能
        try:
            ok, result = Printer().export_to(str(base / "probe-denied.pdf"))
            self.results["denied_save_blocked"] = not ok
            self.results["denied_result"] = result
        finally:
            base.chmod(0o700)
            shutil.rmtree(base, ignore_errors=True)
        return self.stage_save_dialog()

    def stage_save_dialog(self, *_):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("実証: PDFを保存する場所を選ぶ")
        if self.args.save_dir is not None:
            folder = self.args.save_dir
            folder.mkdir(parents=True, exist_ok=True)
            dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
        dialog.save(self.window, None, self.on_save_response)
        return False

    def on_save_response(self, dialog, response):
        try:
            file = dialog.save_finish(response)
        except Exception:
            file = None
        if file is None:
            self.results["save_cancelled"] = True
            return self.stage_print()
        save_path = Path(file.get_path())
        # パスそのものは出力せず、書込み可能性だけを集計する。
        self.results["save_target_writable_by_probe"] = os.access(save_path.parent, os.R_OK | os.W_OK | os.X_OK)
        self.results["save_target_exists_before"] = save_path.exists()
        operation = Gtk.PrintOperation.new()
        operation.set_n_pages(1)
        operation.connect("draw-page", draw_page)
        operation.set_export_filename(save_path.as_posix())
        try:
            result = operation.run(Gtk.PrintOperationAction.EXPORT, self.window)
        except GLib.Error as error:
            self.results["save_ok"] = False
            self.results["save_result"] = f"gerror:{getattr(error, 'domain', 'unknown')}"
            return self.stage_print()
        ok = result == Gtk.PrintOperationResult.APPLY
        self.results["save_result"] = str(result)
        self.results["save_ok"] = ok
        if ok:
            self.verify_pdf(Path(file.get_path()))
        return self.stage_print()

    def verify_pdf(self, path: Path):
        blob = path.read_bytes()
        self.results["saved_pdf_magic_ok"] = blob[:5] == b"%PDF-"
        self.results["saved_pdf_size_over_1kb"] = len(blob) > 1024
        try:
            gi.require_version("Poppler", "0.18")
            from gi.repository import Gio, Poppler
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(blob))
            document = Poppler.Document.new_from_stream(stream, len(blob), None, None)
            self.results["saved_pdf_pages"] = document.get_n_pages()
            text = document.get_page(0).get_text() if document.get_n_pages() else ""
            self.results["saved_pdf_contains_probe_text"] = "Paper Phone" in text
        except Exception:
            self.results["saved_pdf_pages"] = None

    def stage_print(self, *_):
        if self.args.skip_print:
            return self.finish()
        operation = Gtk.PrintOperation.new()
        operation.set_n_pages(1)
        operation.connect("draw-page", draw_page)
        result = operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, self.window)
        self.results["print_result"] = str(result)
        self.results["print_cancelled"] = result == Gtk.PrintOperationResult.CANCEL
        return self.finish()

    def finish(self, *_):
        print(f"target_wayland={os.environ.get('WAYLAND_DISPLAY') is not None}")
        print(f"gtk_version={Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            print(f"adw_version={Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}")
        except Exception:
            self.results["adw_version"] = "unavailable"
        try:
            gi.require_version("Poppler", "0.18")
            from gi.repository import Poppler
            self.results["poppler_version"] = Poppler.get_version()
        except Exception:
            self.results["poppler_version"] = "unavailable"
        for name, value in sorted(self.results.items()):
            print(f"{name}={value}")
        self.quit()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-denied", action="store_true")
    parser.add_argument("--skip-print", action="store_true")
    parser.add_argument("--save-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "build" / "cache",
                        help="FileDialogの既定フォルダ(書込み可能な場所)")
    return Probe(parser.parse_args()).run(None)


if __name__ == "__main__":
    raise SystemExit(main())
