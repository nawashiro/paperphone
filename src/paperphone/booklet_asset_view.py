"""明示選択したローカル写真と既存紙素材を選ぶ独立GTKビュー。

ホスト画面への組込みは別タスク。写真の読取・復号だけをワーカーへ送り、
バイト列はメモリ内に限定する。ファイルパス・URI・画像内容を表示／記録しない。
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from .booklet_assets import capture_photo
from .i18n import gettext_for
from .resources import PAPER_APP_ASSETS, paper_app_names

_ = gettext_for()


@dataclass(frozen=True)
class AssetChoices:
    """生成時の不変選択。写真の内容はreprにも含めない。"""

    photo: bytes | None = field(default=None, repr=False)
    paper_app: str | None = None
    notes_enabled: bool = False
    contactless: bool = False


def _dispatch(callback):
    def run():
        callback()
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


def _cancelled(error):
    return isinstance(error, GLib.Error) and (
        error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
        or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.CANCELLED)
        or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
    )


class AssetSelectionView(Gtk.Box):
    """全13紙アプリ・メモ・ガイドと、利用者操作時だけ開く写真選択。

    chooser_factoryはGtk.FileDialog互換(open/open_finish)のテスト用差替え口。
    submitはFutureを返す関数、dispatchはGTKスレッドへの配送関数。
    ホスト破棄時はclose()を呼ぶ。取消／clear／close後の遅い完了は無視する。
    """

    def __init__(self, *, chooser_factory=None, submit=None, dispatch=None,
                 photo_loader=capture_photo):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._chooser_factory = chooser_factory or self._new_chooser
        self._photo_loader = photo_loader
        self._dispatch = dispatch or _dispatch
        self._worker = None if submit is not None else ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="selected-photo")
        self._submit = submit if submit is not None else self._worker.submit
        self._closed = False
        self._generation = 0
        self._cancellable = None
        self._future = None
        self._photo = None
        self.photo_state = "none"
        # 写真ページと紙アプリページで別の親に分けるため、それぞれをまとめる。
        self.photo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.paper_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.choose_photo_button = Gtk.Button(label=_("Choose a local photo"))
        self.clear_photo_button = Gtk.Button(label=_("Clear photo"))
        self.clear_photo_button.set_sensitive(False)
        self.choose_photo_button.connect("clicked", self._choose_photo)
        self.clear_photo_button.connect("clicked", lambda _button: self.clear_photo())
        self.preview = Gtk.Image()
        self.preview.set_size_request(-1, 240)
        self.preview.set_hexpand(True)
        self.preview.set_vexpand(True)
        self.preview.set_visible(False)
        self.status = Gtk.Label(label=_("No photo selected."), xalign=0)
        self.status.set_wrap(True)
        for widget in (self.choose_photo_button, self.clear_photo_button,
                       self.preview, self.status):
            self.photo_box.append(widget)
        label = Gtk.Label(label=_("Paper app"), xalign=0)
        self.paper_app_dropdown = Gtk.DropDown.new_from_strings(
            [_("None"), *paper_app_names()])
        label.set_mnemonic_widget(self.paper_app_dropdown)
        self.paper_app_dropdown.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Paper app")])
        self.notes_checkbox = Gtk.CheckButton(label=_("Notes"))
        self.contactless_checkbox = Gtk.CheckButton(label=_("Card cutting guides"))
        for widget in (label, self.paper_app_dropdown, self.notes_checkbox,
                       self.contactless_checkbox):
            self.paper_box.append(widget)
        self.append(self.photo_box)

    @staticmethod
    def _new_chooser():
        dialog = Gtk.FileDialog(title=_("Choose a local photo"))
        images = Gtk.FileFilter()
        images.set_name(_("Choose a local photo"))
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)
        return dialog

    def _invalidate(self):
        self._generation += 1
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        if self._future is not None:
            self._future.cancel()
            self._future = None
        return self._generation

    def _choose_photo(self, _button):
        if self._closed:
            return
        token = self._invalidate()
        cancellable = Gio.Cancellable()
        self._cancellable = cancellable
        self.photo_state = "choosing"
        self.status.set_text(_("Choosing a photo…"))
        self.clear_photo_button.set_sensitive(True)
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        try:
            chooser = self._chooser_factory()
            chooser.open(parent, cancellable,
                         lambda dialog, result: self._chosen(dialog, result, token))
        except Exception:
            if not self._closed and token == self._generation:
                self._failed()

    def _chosen(self, chooser, result, token):
        try:
            selected = chooser.open_finish(result)
        except Exception as error:
            if not self._closed and token == self._generation:
                self._cancellable = None
                if _cancelled(error):
                    self.photo_state = "cancelled"
                    self.status.set_text(_("Photo selection was cancelled."))
                    self.clear_photo_button.set_sensitive(self._photo is not None)
                else:
                    self._failed()
            return
        if self._closed or token != self._generation:
            return
        self._cancellable = None
        if selected is None:
            self.photo_state = "cancelled"
            self.status.set_text(_("Photo selection was cancelled."))
            self.clear_photo_button.set_sensitive(self._photo is not None)
            return
        # GioのURI読取APIは使わない。ローカル実体へのパスだけを許可する。
        if not selected.is_native() or selected.get_path() is None:
            self._photo = None
            self._failed()
            return
        path = selected.get_path()
        self._photo = None
        self.photo_state = "reading"
        self.status.set_text(_("Reading selected photo…"))
        try:
            future = self._submit(self._photo_loader, path)
            self._future = future
            future.add_done_callback(
                lambda completed: self._dispatch(lambda: self._loaded(completed, token)))
        except Exception:
            self._failed()

    def _failed(self):
        self.photo_state = "failed"
        self.status.set_text(_(
            "The selected photo could not be read. Choose a readable local image."))
        self.clear_photo_button.set_sensitive(self._photo is not None)
        self._clear_preview()

    def _loaded(self, future, token):
        if self._closed or token != self._generation:
            return
        self._future = None
        try:
            data = future.result()
            if not isinstance(data, bytes) or not data:
                raise ValueError("invalid captured photo")
        except Exception:
            self._failed()
            return
        self._photo = data
        self.photo_state = "ready"
        # 読取成功は画面に出さない。結果が片付けられて見えたら十分。
        self.status.set_text("")
        self.clear_photo_button.set_sensitive(True)
        self._update_preview(data)

    def _update_preview(self, data):
        """選択した写真をメモリ内でのみプレビュー表示する。"""
        self.preview.set_visible(True)
        try:
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
            # ワーカー読取と同様に非同期の縮小プレビューを作る。
            # 完了コールバックの引数並びは描画環境で異なるため、任意個数で受ける。
            GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(
                stream, 480, 320, True, None,
                lambda *args: self._preview_loaded(args[1] if len(args) >= 2 else args[-1]))
        except Exception:
            self.preview.set_visible(False)

    def _preview_loaded(self, result):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_finish(result)
            if pixbuf is not None:
                self.preview.set_from_pixbuf(pixbuf)
        except Exception:
            self.preview.set_visible(False)

    def _clear_preview(self):
        self.preview.set_from_pixbuf(None)
        self.preview.set_visible(False)
        try:
            self.preview.clear()
        except Exception:
            pass

    def clear_photo(self):
        """実行中の選択／読取も取消し、以後の古い完了を無効化する。"""
        if self._closed:
            return
        self._invalidate()
        self._photo = None
        self.photo_state = "none"
        self.status.set_text(_("No photo selected."))
        self.clear_photo_button.set_sensitive(False)

    def retranslate(self):
        """写真バイト列と素材IDを変えず、リストの表示名だけを更新する。"""
        selected = self.paper_app_dropdown.get_selected()
        self.paper_app_dropdown.set_model(Gtk.StringList.new([_("None"), *paper_app_names()]))
        self.paper_app_dropdown.set_selected(selected)
        self.paper_app_dropdown.update_property([Gtk.AccessibleProperty.LABEL], [_("Paper app")])

    def capture_choices(self) -> AssetChoices:
        """現在の選択を捕捉する。画像ファイルの再読込は行わない。"""
        selected = self.paper_app_dropdown.get_selected()
        app = PAPER_APP_ASSETS[selected - 1] if 1 <= selected <= len(PAPER_APP_ASSETS) else None
        return AssetChoices(self._photo, app, self.notes_checkbox.get_active(),
                            self.contactless_checkbox.get_active())

    def close(self):
        """ホスト破棄時の明示的な終了。実行中読取は完了後に破棄する。"""
        if self._closed:
            return
        self._closed = True
        self.photo_state = "closed"
        self._invalidate()
        self._photo = None
        self.choose_photo_button.set_sensitive(False)
        self.clear_photo_button.set_sensitive(False)
        if self._worker is not None:
            self._worker.shutdown(wait=False, cancel_futures=True)
