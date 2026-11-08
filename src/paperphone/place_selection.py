"""ローカル地名インデックスで出発地・目的地を選ぶGTKビュー。"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from threading import Event

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .i18n import gettext_for
from .places import Place, PlaceIndex
from .prefectures import prefecture_at

_ = gettext_for()


_INDEX_CACHE_LIMIT = 8
_INDEX_CACHE: OrderedDict = OrderedDict()
_INDEX_CACHE_LOCK = Lock()


class PlaceSelectionView(Gtk.Box):
    def __init__(self, index=None, *, fix=None, submit=None, dispatch=None,
                 index_factory=PlaceIndex.from_pbf):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.index = None
        self._fix = fix
        self._index_factory = index_factory
        self._token = 0
        self._closed = False
        self._build_cancel = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="place-search")
        self._submit = submit or self._worker.submit
        self._dispatch = dispatch or self._idle
        self.selected = {"start": None, "destination": None}
        self.entries, self.results, self.chosen_labels = {}, {}, {}
        self.current_button = Gtk.Button(label=_("Use current location as departure"))
        self.current_button.connect("clicked", lambda _b: self.use_current_location())
        self.progress = Gtk.ProgressBar()
        self.progress.set_visible(False)
        self.status = Gtk.Label(label="")
        self.status.set_wrap(True)
        self._timers = {}
        self._busy = False
        self.append(self.current_button)
        for endpoint, title in (("start", _("Departure")), ("destination", _("Destination"))):
            entry = Gtk.SearchEntry()
            entry.update_property([Gtk.AccessibleProperty.LABEL], [title])
            # 入力と同時に候補を出すため、明示した検索・取消ボタンは置かない。
            entry.connect("changed", lambda entry, key=endpoint: self._on_search_text(key, entry.get_text()))
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.entries[endpoint] = entry
            rows = Gtk.ListBox()
            rows.set_selection_mode(Gtk.SelectionMode.NONE)
            rows.update_property([Gtk.AccessibleProperty.LABEL], [title])
            chosen = Gtk.Label(label=_("No place selected."), xalign=0)
            chosen.set_wrap(True)
            self.results[endpoint], self.chosen_labels[endpoint] = rows, chosen
            # 候負を読み切るまでスクロールなしに表示する。選択が済むと候負は消える。
            for widget in (entry, chosen, rows):
                self.append(widget)
        self.append(self.status)
        if index is not None:
            self.set_index(index)


    def _say(self, text):
        """結果と失敗を視覚と読み上げの両方に送る。"""
        self.status.set_text(text)
        if Gtk.check_version(4, 14, 0) is None:
            self.status.announce(text, Gtk.AccessibleAnnouncementPriority.MEDIUM)

    @staticmethod
    def _idle(callback):
        def run():
            callback()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    @property
    def selected_start(self):
        return self.selected["start"]

    @property
    def selected_destination(self):
        return self.selected["destination"]

    def _clear_rows(self, endpoint):
        box = self.results[endpoint]
        while child := box.get_first_child():
            box.remove(child)

    def _set_busy(self, busy):
        for entry in self.entries.values():
            entry.set_sensitive(not busy and self.index is not None)
        self._busy = busy
        self.current_button.set_sensitive(not busy and self.index is not None and self._fix is not None)
        # 索引の作成中は進行を随時束ねる。版のPBF走査の進捗は取得元手順に
        # 細かく依存するため、便宜的に常時くるませる。
        self.progress.set_visible(busy)
        if busy:
            self.progress.pulse()

    def _in_scope(self, place):
        if self.index is None:
            return False
        if place.id != "current-location":
            # 実際に選択県のPBFから読めた候補を一般化境界で誤って除外しない。
            return self.index.get(place.id) == place
        try:
            return prefecture_at(place.latitude, place.longitude) == self.index.prefecture
        except (TypeError, ValueError):
            return False

    def set_index(self, index):
        if self._build_cancel is not None:
            self._build_cancel.set()
        self._token += 1
        self.index = index
        for key in self.selected:
            self.selected[key] = None
            self.chosen_labels[key].set_text(_("No place selected."))
            self._clear_rows(key)
        self._set_busy(False)
        self._say(_("Search stays on this device.") if index else "")
        if index is not None and self._fix is not None:
            self.use_current_location()

    def load_extract(self, extract):
        """取得済みの版別PBFだけをワーカーで読む。取得・外部検索はしない。

        同じ抽出版（県・SHA-256・パス）の索引は作り直さない：
        - メモリ内（この起動の中）
        - キャッシュディスク（起動のたびに0.1秒程度のJSON読取で済む）
        の順に探し、なければPBF走査後に両方へ保存する。
        """
        cached = self._cached_index(extract)
        if cached is not None:
            self._index_ready(self._token, cached)
            return
        disk = self._disk_index(extract)
        if disk is not None:
            self._store_index(extract, disk)
            self._index_ready(self._token, disk)
            return
        self.set_index(None)
        token = self._token
        cancelled = Event()
        self._build_cancel = cancelled
        self._set_busy(True)
        self._say(_("Building the local place index…"))

        def work():
            try:
                index = self._cached_index(extract)
                if index is None:
                    index = self._index_factory(
                        extract.path, prefecture=extract.prefecture, cancelled=cancelled.is_set)
                    self._store_index(extract, index)
                    self._save_disk_index(extract, index)
            except Exception:
                self._dispatch(lambda: self._index_ready(token, None))
            else:
                self._dispatch(lambda: self._index_ready(token, index))
        try:
            self._submit(work)
        except Exception:
            self._index_ready(token, None)

    @staticmethod
    def _index_key(extract):
        sha = getattr(extract, "sha256", "")
        prefecture = getattr(extract, "prefecture", None)
        return (str(extract.path), prefecture, sha)

    def _cached_index(self, extract):
        key = self._index_key(extract)
        with _INDEX_CACHE_LOCK:
            if key in _INDEX_CACHE:
                _INDEX_CACHE.move_to_end(key)
                return _INDEX_CACHE[key]
        return None

    def _disk_index(self, extract):
        sha = getattr(extract, "sha256", "")
        if not sha:
            return None
        return PlaceIndex.load_cache(
            PlaceIndex.cache_path(extract.prefecture, sha), sha)

    def _save_disk_index(self, extract, index):
        sha = getattr(extract, "sha256", "")
        if not sha or getattr(index, "prefecture", None) != extract.prefecture:
            return
        try:
            index.save_cache(PlaceIndex.cache_path(extract.prefecture, sha), sha)
        except Exception:
            # 検出できない書出し失敗は、次の起動でPBF走査に戻るだけ。
            pass

    def _store_index(self, extract, index):
        key = self._index_key(extract)
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE[key] = index
            while len(_INDEX_CACHE) > _INDEX_CACHE_LIMIT:
                _INDEX_CACHE.popitem(last=False)

    def _index_ready(self, token, index):
        if self._closed or token != self._token:
            return
        self.set_index(index)
        if index is None:
            self._say(_("Place index unavailable."))

    def set_current_location(self, fix):
        """許可済み位置を再利用する。利用者が明示した別の出発地は変更しない。"""
        self._fix = fix
        previous = self.selected_start
        may_default = previous is None or previous.id == "current-location"
        if previous is not None and previous.id == "current-location":
            self.selected["start"] = None
            self.chosen_labels["start"].set_text(_("No place selected."))
        if may_default and self.index is not None and fix is not None:
            self.use_current_location()

    def use_current_location(self):
        if self._fix is None or self.index is None:
            return
        point = Place("current-location", _("Current location"), "current-location",
                      self._fix.latitude, self._fix.longitude)
        if self._in_scope(point):
            self.choose("start", point)
        else:
            self._say(_("Current location is outside the selected prefecture. Search for a departure."))

    def _on_search_text(self, endpoint, text):
        """入力の変化で検索を起こす。ボタンや取消は置かない。短い入力は待つ。"""
        if self.index is None:
            return
        old = self._timers.get(endpoint, 0)
        if old:
            GLib.source_remove(old)
        self._update_token = None
        self._clear_rows(endpoint)
        self._set_busy(False)
        self._say(_("Search stays on this device."))
        if len(text.strip()) < 2:
            return
        self._timers[endpoint] = GLib.timeout_add(300, lambda: self._search_from_debounce(endpoint, text))

    def _search_from_debounce(self, endpoint, text):
        self._timers.pop(endpoint, None)
        current = self.entries[endpoint].get_text().strip()
        if current == text:
            self.search(endpoint)
        return GLib.SOURCE_REMOVE

    def search(self, endpoint):
        if self._closed or self.index is None:
            return
        query = self.entries[endpoint].get_text().strip()
        self._token += 1
        token, index = self._token, self.index
        self._clear_rows(endpoint)
        if not query:
            self._say(_("Enter a place name."))
            return
        self._set_busy(True)
        self._say(_("Searching local places…"))

        def work():
            try:
                matches = index.search(query)
            except Exception:
                self._dispatch(lambda: self._search_ready(endpoint, token, (), True))
            else:
                self._dispatch(lambda: self._search_ready(endpoint, token, matches, False))
        try:
            self._submit(work)
        except Exception:
            self._search_ready(endpoint, token, (), True)

    def _search_ready(self, endpoint, token, matches, failed):
        if self._closed or token != self._token:
            return
        self._set_busy(False)
        for place in matches:
            button = Gtk.Button(label=place.name)
            button.connect("clicked", lambda _b, candidate=place: self.choose(endpoint, candidate))
            self.results[endpoint].append(button)
        self._say(_("Search failed.") if failed else
                  (_("No local places found.") if not matches else ""))

    def choose(self, endpoint, place):
        if not self._in_scope(place):
            self._say(_("This place is outside the selected prefecture."))
            return False
        self.selected[endpoint] = place
        self.chosen_labels[endpoint].set_text(place.name)
        # 選択したら候補は消す。普通のウィンドウからスクロールを強いられない。
        self._clear_rows(endpoint)
        self.status.set_text("")
        return True

    def cancel(self):
        """読込中の索引作成と検索を打ち切る。選択はそのまま残る。"""
        if self._build_cancel is not None:
            self._build_cancel.set()
        self._token += 1
        self._set_busy(False)

    def close(self):
        if self._build_cancel is not None:
            self._build_cancel.set()
        self._closed = True
        self._token += 1
        self._worker.shutdown(wait=False, cancel_futures=True)
