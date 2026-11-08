"""都道府県の選択と、明示操作のみのOSM抽出更新。

選択した県の保存済み抽出があれば自動で読み込み、ダウンロードは
「更新を確認」ボタンを押した時だけ行う。 利用者が押す操作だけ
ネットワーク又はディスク削除を起こす。説明文や免責の文言はUIに出さない。
"""

from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .i18n import N_, gettext_for, language_code
from .osm_derived import DerivedError, ensure_derived
from .osm_store import ExtractFailure, ExtractUpdate
from .prefectures import PREFECTURES, prefecture_at

_ = gettext_for()

ERRORS = {
    "network": N_("Download failed."),
    "http": N_("Download failed."),
    "missing": N_("Extract unavailable."),
    "denied": N_("Access denied."),
    "limited": N_("Rate limited."),
    "wait": N_("Wait before retrying."),
    "invalid": N_("Downloaded data is invalid."),
    "cache": N_("Saved data is damaged."),
    "storage": N_("Save failed."),
    "redirect": N_("Unexpected redirect."),
    "confirmation": N_("Not confirmed."),
    "derived": N_("Map data could not be prepared."),
}


class OsmRegionView(Gtk.Box):
    def __init__(self, store, *, fix=None, submit=None, dispatch=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.store = store
        self.selected_extract = None
        self.extract_listener = None
        self._closed = False
        self._generation = 0
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="osm-extract")
        self._submit = submit or self._worker.submit
        self._dispatch = dispatch or self._idle
        self.attribution = Gtk.Label(label="© OpenStreetMap contributors · ODbL 1.0")
        self.attribution.set_wrap(True)
        labels = [_("Select a prefecture")] + [
            p.name_ja if language_code() == "ja" else p.name_en for p in PREFECTURES]
        self.prefecture = Gtk.DropDown.new_from_strings(labels)
        self.prefecture.set_selected(0)
        self.prefecture.update_property([Gtk.AccessibleProperty.LABEL], [_("Prefecture")])
        self.update_button = Gtk.Button(label=_("Check for updates"))
        self.delete_button = Gtk.Button(label=_("Delete stored map data"))
        self.status = Gtk.Label(label="")
        self.status.set_wrap(True)
        self.progress = Gtk.ProgressBar()
        self.progress.set_visible(False)
        self._selection_handler = self.prefecture.connect("notify::selected", self._selection_changed)
        self.update_button.connect("clicked", lambda _b: self._run(True))
        self.delete_button.connect("clicked", lambda _b: self._run_delete())
        for widget in (self.prefecture, self.update_button, self.delete_button,
                       self.progress, self.status, self.attribution):
            self.append(widget)
        self._set_busy(False)
        self.status.set_text("")
        if fix is not None:
            self.set_current_location(fix)

    @staticmethod
    def _idle(callback):
        def run():
            callback()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    @property
    def prefecture_code(self):
        index = self.prefecture.get_selected()
        return PREFECTURES[index - 1].code if 1 <= index <= len(PREFECTURES) else None

    def retranslate(self):
        """県ID・抽出版・実行中ジョブを維持してリストの表示名だけを切り替える。"""
        selected = self.prefecture.get_selected()
        labels = [_("Select a prefecture")] + [
            p.name_ja if language_code() == "ja" else p.name_en for p in PREFECTURES]
        self.prefecture.handler_block(self._selection_handler)
        try:
            self.prefecture.set_model(Gtk.StringList.new(labels))
            self.prefecture.set_selected(selected)
        finally:
            self.prefecture.handler_unblock(self._selection_handler)
        self.prefecture.update_property([Gtk.AccessibleProperty.LABEL], [_("Prefecture")])

    def set_current_location(self, fix):
        try:
            code = prefecture_at(fix.latitude, fix.longitude)
        except (ValueError, TypeError):
            code = None
        if code is not None:
            index = next(i for i, p in enumerate(PREFECTURES) if p.code == code)
            self.prefecture.set_selected(index + 1)
        else:
            self.location_unavailable()

    def location_unavailable(self):
        self.status.set_text(_("Location unavailable."))

    def _selection_changed(self, *_args):
        self._generation += 1
        self.selected_extract = None
        self._set_busy(False)
        if self.extract_listener is not None:
            self.extract_listener(None)
        if self.prefecture_code is not None:
            # 保存済みの抽出があれば自動で読み込む。通信はしない。
            self._run(False)

    def _set_busy(self, busy):
        self.prefecture.set_sensitive(not busy)
        enabled = not busy and self.prefecture_code is not None
        self.update_button.set_sensitive(enabled)
        self.delete_button.set_sensitive(enabled)
        self.progress.set_visible(busy)
        if busy:
            # 個々の読取進捗度は取得元手順に大きく依存するので、便宜的に常時くるませる。
            self.progress.pulse()

    def _run(self, update):
        code = self.prefecture_code
        if self._closed or code is None or not self.prefecture.get_sensitive():
            return
        generation = self._generation
        self.selected_extract = None
        self._set_busy(True)

        def work():
            try:
                result = (self.store.update(code, confirmed=True) if update
                          else ExtractUpdate(self.store.load(code), False))
                if result.extract is None:
                    raise ExtractFailure("missing")
                # 保存済み抽出版に対する派生データをここで一度だけ作る。
                ensure_derived(result.extract,
                               cancelled=lambda: self._closed or generation != self._generation)
            except DerivedError as error:
                if error.reason != "cancelled":
                    self._dispatch(lambda: self._finish(None, "derived", generation))
            except ExtractFailure as error:
                reason = error.reason
                self._dispatch(lambda: self._finish(None, reason, generation))
            except Exception:
                self._dispatch(lambda: self._finish(None, "storage", generation))
            else:
                self._dispatch(lambda: self._finish(result, None, generation))
        try:
            self._submit(work)
        except Exception:
            self._finish(None, "storage", generation)

    def _run_delete(self):
        code = self.prefecture_code
        if self._closed or code is None or not self.delete_button.get_sensitive():
            return
        generation = self._generation
        self.selected_extract = None
        self._set_busy(True)

        def work():
            try:
                self.store.delete(code)
            except ExtractFailure as error:
                reason = error.reason
                self._dispatch(lambda: self._finish(None, reason, generation))
            except Exception:
                self._dispatch(lambda: self._finish(None, "storage", generation))
            else:
                self._dispatch(lambda: self._finish(None, "deleted", generation))
        try:
            self._submit(work)
        except Exception:
            self._finish(None, "storage", generation)

    def _finish(self, result, reason, generation):
        if self._closed or generation != self._generation:
            return
        self._set_busy(False)
        if reason == "deleted":
            self.status.set_text(_("Deleted."))
            return
        if reason:
            self.status.set_text(_(ERRORS.get(reason, ERRORS["network"])))
            return
        if result is None:
            self.status.set_text(_("Deleted."))
            return
        self.selected_extract = result.extract
        # 成功時は静かに済ませ、失敗の理由以外の説明を出さない。
        self.status.set_text("")
        if self.extract_listener is not None:
            self.extract_listener(self.selected_extract)

    def close(self):
        self._closed = True
        self._worker.shutdown(wait=False, cancel_futures=True)
