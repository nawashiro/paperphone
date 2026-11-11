"""GNOMEメイン画面。明示的な取得、ソース単位の除外確認、不変PDFを合成する。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from .application_services import GenerationFailure, GenerationRequest, ROUTE_REASONS
from .booklet_asset_view import AssetSelectionView
from .booklet_content import calendar_item
from .contact_selection import ContactSelectionView
from .eds import EDSResult, EDSState, SourceKind
from .i18n import N_, gettext_for, language_code, set_language
from .osm_region_view import OsmRegionView
from .place_selection import PlaceSelectionView
from .refresh import RefreshState
from .selection import GenerationSnapshot, SelectionModel
from .todo import next_todo_reassessment
from .todo_selection import TodoSelectionView
from .weather import ATTRIBUTION_SHORT
from .weather_location import WeatherLocationState
from .weather_location_view import WeatherLocationView
from .weather_sheet import build_weather_sheet, weather_lines


# 翻訳カタログへの入力。ビューは指定言語を明示して取得する。
MESSAGES = (
    N_("Settings"), N_("Language"), N_("Name on cover"), N_("MET Norway client contact"),
    N_("Location and client contact are sent to MET Norway."),
    N_("Save settings"), N_("Invalid contact."),
    N_("Sources"), N_("Refresh selected sources"), N_("Cancel"),
    N_("Sources from GNOME accounts."), N_("Not read yet."),
    N_("OK"), N_("Failed."), N_("Reading selected sources…"),
    N_("Personal sources are unavailable. Set up GNOME Online Accounts first."),
    N_("CalDAV changes were not confirmed; cached items are excluded until acknowledged."),
    N_("I acknowledge exclusion of this source"), N_("Selection changed. Generate again to update the PDF."),
    N_("Removed unavailable selections."), N_("Contacts"), N_("Calendar"), N_("Tasks"), N_("Weather"),
    N_("Walking map"), N_("Photos"), N_("Paper apps"), N_("PDF"),
    N_("Generate booklet"), N_("Confirm unavailable source exclusions before generating."),
    N_("Generating booklet…"), N_("Booklet ready. Later edits do not change this PDF."),
    N_("Generation failed. Your previous PDF has been kept."),
    N_("Walking map failed. The map is not included."),  # 回路失敗でも他の内容は印刷する。
    N_("Source selection changed during refresh; old results were ignored."),
    N_("Refresh cancelled."), N_("No events for today."),
    N_("Photo selection is still in progress."),
    N_("Delete stored map data"), N_("Map data deleted."), N_("Map data could not be deleted."),
    N_("Settings could not be saved."),
    N_("Close"), N_("Danger zone"),
    N_("photo: excluded"), N_("weather: excluded"), N_("map: excluded"), N_("{source}: excluded"),
    N_("Map failure: {reason}"), N_("Task List"), N_("Address Book"),
)


STATE_MESSAGES = {
    EDSState.READY: N_("OK."), EDSState.EMPTY: N_("OK."),
    EDSState.UNREGISTERED: N_("Not registered."),
    EDSState.AUTHENTICATION_REQUIRED: N_("Failed."),
    EDSState.COMMUNICATION_ERROR: N_("Failed."),
    EDSState.REFRESH_UNCONFIRMED: N_("Failed."),
}


def idle(callback):
    def run():
        callback()
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


def settings_file(path=None):
    """設定の保存先ファイル。取得した連絡先・文字列は保持せず、選択だけを書く。"""
    directory = (path if path is not None else
                 os.environ.get("PAPERPHONE_CONFIG_DIR") or
                 os.path.join(os.environ.get("XDG_CONFIG_HOME",
                                             os.path.join(os.path.expanduser("~"), ".config")), "paperphone"))
    return Path(directory) / "settings.json"


def load_settings_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def save_settings_file(path, data):
    """原子置換で0600のファイルへ書き込む。失敗は呼び出し元に通知する。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False,
                                         encoding="utf-8") as sink:
            sink.write(json.dumps(data, ensure_ascii=False, sort_keys=True))
            sink.flush()
            os.fsync(sink.fileno())
            temporary = sink.name
        os.chmod(temporary, 0o600)
        os.replace(temporary, file_path)
    except Exception:
        return False
    return True


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _children(child)
        child = child.get_next_sibling()


class ApplicationView(Gtk.Box):
    """サービスと子ビューfactoryを注入できる再利用可能なアプリ内容。

    コンストラクターはEDS列挙・位置要求・HTTP・ダイアログを起動しない。
    非同期完了はGTKへ配送し、ソース選択の世代で古い読取結果を排除する。
    """
    def __init__(self, services, *, language=None, submit=None, dispatch=idle,
                 asset_factory=AssetSelectionView, region_factory=OsmRegionView,
                 place_factory=PlaceSelectionView, weather_factory=WeatherLocationView,
                 output_factory=None, schedule=True, config_path=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar = Adw.ToolbarView(vexpand=True)
        self.append(self.toolbar)
        self.services = services
        self.language = language_code(language)
        self._dispatch = dispatch
        self._worker = None if submit is not None else ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="booklet-generation")
        self._submit = submit or self._worker.submit
        self._closed = False
        self._translating = False
        self._revision = 0
        self._generation = 0
        self._refresh_job = None
        self._refresh_token = 0
        self._refreshing = False
        self._listing = False
        self._generating = False
        self._config_path = settings_file(config_path)
        self._restoring = False
        self._saved_sources = ()
        self._saved_selection = ()
        self._selection_restored = False
        self._auto_refreshed = False
        self._save_pending = None
        self._generation_progresspulse = None
        self._weather_factory = weather_factory
        self.weather_view = None
        self._map_failed = False
        self._map_failure_reason = None
        self._loaded_extract = None
        self._last_fix = None
        self.selection = SelectionModel()
        self.sources, self.source_checks, self.source_states = {}, {}, {}
        self._kind_headings = {}
        self.source_status, self._reports = {}, {}
        self._registry_states = {}
        self._bindings = []
        self._static_bindings = []
        self._last_clock = None
        self._next_boundary = None
        self._timer = None

        header = Adw.HeaderBar()
        self.sources_button = self._button("Sources", lambda _b: self._show_sources_dialog())
        header.pack_start(self.sources_button)
        self.settings_button = self._button("Settings", lambda _b: self._show_settings_dialog())
        header.pack_end(self.settings_button)
        self.toolbar.add_top_bar(header)
        self.status = self._label("")
        self.status.set_visible(False)
        self.progress = Gtk.ProgressBar()
        self.progress.set_visible(False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(self.status)
        body.append(self.progress)
        self.main = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        flow = self._box()
        self.main.set_child(flow)
        self._flow = flow
        body.append(self.main)
        self.toolbar.set_content(body)
        self._section_headers = []

        settings = self._box()
        self.language_dropdown = Gtk.DropDown.new_from_strings(["English", "日本語"])
        self.language_dropdown.set_selected(1 if self.language == "ja" else 0)
        self.language_dropdown.connect("notify::selected", self._language_changed)
        settings.append(self._label("Language"))
        # 言語ラベルをドロップダウンの読み取り可能な名前として関係付ける。
        self.language_dropdown.update_property([Gtk.AccessibleProperty.LABEL],
                                               [self.tr("Language")])
        settings.append(self.language_dropdown)
        self.name_entry = Gtk.Entry()
        self._entry_label(settings, "Name on cover", self.name_entry)
        self.name_entry.connect("changed", self._edited)
        self.contact_entry = Gtk.Entry()
        self._entry_label(settings, "MET Norway client contact", self.contact_entry)
        settings.append(self._label(MESSAGES[4]))
        self.apply_contact_button = self._button(MESSAGES[5], self._apply_contact)
        settings.append(self.apply_contact_button)
        # 削除は保存系の操作から距離を置いた別セクションに置き、誤押しを避ける。
        settings.append(Gtk.Separator())
        settings.append(self._heading("Danger zone"))
        self.delete_data_button = self._button("Delete stored map data", self._delete_map_data)
        settings.append(self.delete_data_button)
        self.settings_dialog = self._dialog("Settings", settings)

        sources = self._box()
        sources.append(self._label(MESSAGES[11]))
        controls = Gtk.Box(spacing=8)
        self.refresh_button = self._button("Refresh selected sources", lambda _b: self.refresh_sources())
        self.cancel_button = self._button("Cancel", lambda _b: self.cancel_refresh())
        self.cancel_button.set_sensitive(False)
        controls.append(self.refresh_button)
        controls.append(self.cancel_button)
        sources.append(controls)
        self.source_box = self._box()
        # LABELLED_BY配列はPyGObject側でGValue変換に失敗する環境があるため、
        # グループ名を引数のプロパティとして付ける。
        self.source_box.update_property([Gtk.AccessibleProperty.LABEL],
                                        [self.tr("Sources")])
        # 件数が増えても対話が伸びず、下段の操作は常に押せる。超過はスクロールで見る。
        source_scroller = Gtk.ScrolledWindow()
        source_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        source_scroller.set_child(self.source_box)
        source_scroller.set_max_content_height(320)
        source_scroller.set_propagate_natural_height(True)
        sources.append(source_scroller)
        self.sources_dialog = self._dialog("Sources", sources)

        self._section("Contacts", self._contact_section())
        self._section("Calendar", self._calendar_section())
        self._section("Tasks", self._task_section())
        self._section("Weather", self._weather_section())
        self._section("Walking map", self._map_section(region_factory, place_factory))
        self._section("Photos", self._photo_section(asset_factory))
        self._section("Paper apps", self._paper_app_section())
        self._section("PDF", self._output_section(output_factory))
        self._capture_static_labels()
        self.set_language(self.language)
        self._load_persisted_settings()
        # ソースの列挙は起動時に一度もうけて、利用者に手動操作を求めない。
        self.list_sources()
        if schedule:
            self._timer = GLib.timeout_add_seconds(1, self._tick)

    def tr(self, message):
        return gettext_for(self.language)(message)

    @staticmethod
    def _box():
        return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

    def _label(self, message):
        widget = Gtk.Label(label=self.tr(message), wrap=True, xalign=0)
        self._bindings.append((widget, message))
        return widget

    def _button(self, message, callback):
        widget = Gtk.Button(label=self.tr(message))
        self._bindings.append((widget, message))
        widget.connect("clicked", callback)
        return widget

    def _check(self, message, callback=None):
        widget = Gtk.CheckButton(label=self.tr(message))
        self._bindings.append((widget, message))
        if callback:
            widget.connect("toggled", callback)
        return widget

    def _entry_label(self, box, message, entry):
        label = self._label(message)
        label.set_mnemonic_widget(entry)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [self.tr(message)])
        box.append(label)
        box.append(entry)

    def _contact_section(self):
        box = self._box()
        box.append(self._label("Contacts"))
        self.contacts = ContactSelectionView(self.selection)
        self.contacts.set_size_request(-1, 200)
        box.append(self.contacts)
        return box

    def _calendar_section(self):
        box = self._box()
        self.calendar_box = self._box()
        self.calendar_box.update_property([Gtk.AccessibleProperty.LABEL],
                                          [self.tr("Calendar")])
        box.append(self.calendar_box)
        return box

    def _task_section(self):
        box = self._box()
        self.todos = TodoSelectionView(self.selection)
        self.todos.set_size_request(-1, 260)
        box.append(self.todos)
        return box

    def _weather_section(self):
        box = self._box()
        self.weather_box = self._box()
        box.append(self.weather_box)
        box.append(Gtk.Label(label=ATTRIBUTION_SHORT, wrap=True, xalign=0))
        return box

    def _map_section(self, region_factory, place_factory):
        box = self._box()
        self.region = region_factory(self.services.store)
        self.places = place_factory()
        self.region.extract_listener = self._region_changed
        self.region.prefecture.connect("notify::selected", self._prefecture_changed)
        box.append(self.region)
        box.append(self.places)
        self.map_failure = Gtk.Label(wrap=True, xalign=0)
        box.append(self.map_failure)
        for label in self.places.chosen_labels.values():
            label.connect("notify::label", self._endpoints_changed)
        return box

    def _photo_section(self, asset_factory):
        self.assets = asset_factory()
        self.assets.status.connect("notify::label", self._photo_changed)
        self.assets.notes_checkbox.connect("toggled", self._edited)
        self.assets.contactless_checkbox.connect("toggled", self._edited)
        self.assets.paper_app_dropdown.connect("notify::selected", self._edited)
        box = self._box()
        box.append(self.assets)
        return box

    def _paper_app_section(self):
        box = self._box()
        box.append(self.assets.paper_box)
        return box

    def _output_section(self, output_factory):
        if output_factory is None:
            from .pdf_output import PdfOutputView
            output_factory = PdfOutputView
        self.output = output_factory()
        box = self._box()
        self.generate_button = self._button("Generate booklet", lambda _b: self.generate())
        self.generate_button.add_css_class("suggested-action")
        box.append(self.generate_button)
        box.append(self.output)
        return box

    def _dialog(self, title, child):
        child.set_margin_top(12)
        child.set_margin_bottom(12)
        child.set_margin_start(18)
        child.set_margin_end(18)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(child)
        close = Gtk.Button(label=self.tr("Close"))
        self._bindings.append((close, "Close"))
        close.set_halign(Gtk.Align.END)
        close.connect("clicked", lambda _b: dialog.close())
        content.append(close)
        dialog = Adw.Dialog()
        dialog.set_title(self.tr(title))
        dialog.set_child(content)
        return dialog

    def _show_settings_dialog(self):
        self.settings_dialog.present(self.get_root())

    def _show_sources_dialog(self):
        self.sources_dialog.present(self.get_root())

    def _heading(self, message):
        label = self._label(message)
        label.add_css_class("heading")
        return label

    def _reveal_section(self, title):
        for widget, message in self._section_headers:
            if message == title:
                widget.set_active(True)

    def _section(self, title, child):
        child.set_margin_top(12)
        child.set_margin_bottom(12)
        child.set_margin_start(18)
        child.set_margin_end(18)
        revealer = Gtk.Revealer()
        revealer.set_child(child)
        revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
        revealer.set_reveal_child(True)
        # 折りたたみは押せる要素として残すが、タブやボタンと同じ外観にはしない。
        toggle = Gtk.ToggleButton()
        toggle.set_label(self.tr(title))
        toggle.add_css_class("flat")
        toggle.set_halign(Gtk.Align.START)
        toggle.set_active(True)
        toggle.connect("toggled", lambda b, r=revealer: r.set_reveal_child(b.get_active()))
        self._section_headers.append((toggle, title))
        block = self._box()
        block.append(toggle)
        block.append(revealer)
        self._flow.append(block)

    def _capture_static_labels(self, root=None):
        # 既存子ビューの静的ラベルだけを記録。個人データ行は後で生成される。
        ja = gettext_for("ja")
        catalog = getattr(getattr(ja, "__self__", None), "_catalog", {})
        self._reverse_labels = {value: key for key, value in catalog.items()
                                if isinstance(key, str) and isinstance(value, str) and key}
        widgets = [root if root is not None else self,
                   *([] if root is not None else (self.settings_dialog, self.sources_dialog))]
        for widget in widgets:
            for descendant in _children(widget):
                if (isinstance(descendant, Gtk.Label)
                        and descendant is not getattr(self.output, "warnings", None)):
                    self._static_bindings.append(descendant)

    def set_language(self, language):
        self._translating = True
        try:
            self._set_language_labels(language)
        finally:
            self._translating = False
        self._edited()

    def _set_language_labels(self, language):
        self.language = language_code(language)
        set_language(self.language)
        self.language_dropdown.set_selected(1 if self.language == "ja" else 0)
        for widget in self._static_bindings:
            value = widget.get_label()
            widget.set_label(self.tr(self._reverse_labels.get(value, value)))
        for widget, message in self._bindings:
            if widget is not self.status:
                widget.set_label(self.tr(message))
        for view in (self.assets, self.region, self.todos, self.output):
            if hasattr(view, "retranslate"):
                view.retranslate()
        for widget, message in self._section_headers:
            widget.set_label(self.tr(message))
        self.settings_dialog.set_title(self.tr("Settings"))
        self.sources_dialog.set_title(self.tr("Sources"))
        self.name_entry.update_property([Gtk.AccessibleProperty.LABEL], [self.tr("Name on cover")])
        self.contact_entry.update_property([Gtk.AccessibleProperty.LABEL], [self.tr("MET Norway client contact")])
        for item in getattr(self, "_calendar_items", ()):
            checkbox = self.calendar_checks.get(item.key)
            if checkbox is not None:
                checkbox.set_label(self._calendar_label(item))
        for key, source in self.sources.items():
            self.source_checks[key].set_label(self.tr(source.kind.value) + ": " + source.display_name)
            self._show_source_state(key)
        if self._map_failure_reason is not None:
            self.map_failure.set_text(self.tr("Map failure: {reason}").format(
                reason=self.tr(ROUTE_REASONS[self._map_failure_reason])))
        if self.weather_view is not None and hasattr(self.weather_view, "forecast_details"):
            sheet = self._weather_sheet()
            self.weather_view.forecast_details.set_text("\n".join(weather_lines(sheet)) if sheet else "")

    def _language_changed(self, *_args):
        if self._translating:
            return
        self.set_language("ja" if self.language_dropdown.get_selected() == 1 else "en")

    def _edited(self, *_args):
        if self._translating or self._closed:
            return
        self._revision += 1
        self.status.set_text("")
        # 入力・選択が変わるたびに設定を保存する。個人の内容そのものは書かない。
        if self._config_path is not None and self._save_pending is None:
            self._save_pending = GLib.timeout_add(1000, self._flush_settings)

    def _flush_settings(self):
        self._save_pending = None
        self._persist_settings()
        return GLib.SOURCE_REMOVE

    def list_sources(self):
        if self._closed or self._listing:
            return
        self._listing = True
        def connected(ok):
            if self._closed:
                return
            if not ok:
                self._listing = False
                # 技術詳細を出さず、案内の一文だけを示す。
                self.status.set_text(self.tr(
                    "Personal sources are unavailable. Set up GNOME Online Accounts first."))
                for kind in SourceKind:
                    self._registry_failure(kind, EDSState.COMMUNICATION_ERROR)
                return
            remaining = set(SourceKind)
            def listed(kind, result):
                if self._closed:
                    return
                if result.state is EDSState.READY:
                    self._refresh_token += 1
                    self._clear_registry_failure(kind)
                    for source in result.items:
                        self.add_source(source)
                    available = {(source.kind, source.uid) for source in result.items}
                    for key in tuple(self.sources):
                        if key[0] is kind and key not in available:
                            for widgets in (self.source_checks, self.source_status):
                                self.source_box.remove(widgets.pop(key))
                            self.sources.pop(key)
                            self.source_states.pop(key, None)
                            self._reports.pop(key, None)
                    self._rebuild_personal()
                    for key in self.sources:
                        self._show_source_state(key)
                else:
                    self._registry_failure(kind, result.state)
                remaining.discard(kind)
                self._listing = bool(remaining)
                if not remaining:
                    self._auto_refresh()
            for kind in SourceKind:
                try:
                    self.services.list_sources_async(kind, lambda result, _data, k=kind: listed(k, result))
                except Exception:
                    listed(kind, EDSResult(EDSState.COMMUNICATION_ERROR))
        try:
            self.services.connect(connected)
        except Exception:
            connected(False)

    def _clear_registry_failure(self, kind):
        self._registry_states.pop(kind, None)

    def _registry_failure(self, kind, state):
        self._registry_states[kind] = state
        for key in self.sources:
            if key[0] is kind:
                self.source_states[key] = state
                self._reports.pop(key, None)
                self._show_source_state(key)
        self._refresh_token += 1
        self._rebuild_personal()

    def add_source(self, source):
        key = (source.kind, source.uid)
        if key in self.sources:
            self.sources[key] = source
            # 見出しの下には名称だけを表示し、種別の冠詞は繰り返さない。
            self.source_checks[key].set_label(source.display_name)
            self.source_checks[key].set_sensitive(source.enabled)
            if not source.enabled:
                self.source_checks[key].set_active(False)
            return
        self.sources[key] = source
        self._append_kind_heading(source.kind)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        checkbox = Gtk.CheckButton(label=source.display_name)
        checkbox.set_sensitive(source.enabled)
        checkbox.connect("toggled", lambda _c: self._source_selection_changed())
        self.source_checks[key] = checkbox
        # 読取状態はチェックボックスのすぐ右に出し、遠くの右端に置かない。
        label = Gtk.Label(label="", xalign=0, margin_start=8)
        self.source_status[key] = label
        row.append(checkbox)
        row.append(label)
        self.source_box.append(row)
        # 保存済み設定の丸 Gand選択は、取得が来てから一気に反映する。
        if (source.kind.value, source.uid) in self._saved_sources:
            checkbox.set_active(True)


    def _append_kind_heading(self, kind):
        """種類の見出しは一つだけ表示し、行には何も付けない。"""
        if kind in self._kind_headings:
            return
        label = Gtk.Label(label=self.tr(kind.value), xalign=0, halign=Gtk.Align.START)
        self.source_box.append(label)
        self._kind_headings[kind] = label

    def _source_selection_changed(self):
        self._refresh_token += 1
        for key in self.sources:
            self._show_source_state(key)
        self._rebuild_personal()
        self._edited()

    def selected_sources(self):
        return tuple(source for key, source in self.sources.items() if self.source_checks[key].get_active())

    def _refresh_status(self, text, *, priority=None):
        self.status.set_text(text)
        # 生成を左右する状態のみ読み上げを送る（GTK 4.14以上）。
        if priority is not None:
            self.status.announce(text, priority)

    def _show_source_state(self, key):
        state = self.source_states.get(key)
        source = self.sources.get(key)
        if self._awaiting_credentials(source) and state not in (EDSState.UNREGISTERED,):
            # 親コレクションの認証待ちなら失敗の原因は認証の未登録にある。
            text = self.tr("Credentials needed.")
        elif state in (EDSState.READY, EDSState.EMPTY):
            text = self.tr("OK")
        elif state is None:
            text = ""
        else:
            text = self.tr("Failed.")
        self.source_status[key].set_text(text)
        self.source_status[key].set_visible(bool(text))

    def _awaiting_credentials(self, source):
        """登録データの接続状態でCalDAV系の認証待ちを示す。"""
        if source is None:
            return False
        connection_status = getattr(source, "connection_status", "")
        return connection_status == "awaiting-credentials"
        failed = (self.source_checks[key].get_active() and key[0] not in self._registry_states
                  and state not in (EDSState.READY, EDSState.EMPTY))

    def refresh_sources(self):
        if self._refreshing:
            return
        self.refresh_clock()
        sources = self.selected_sources()
        if not sources:
            return
        self._refresh_token += 1
        token = self._refresh_token
        zone = self.services.timezone()
        now = self.services.clock().astimezone(zone)
        self._refreshing = True
        self.refresh_button.set_sensitive(False)
        self.cancel_button.set_sensitive(True)
        self.status.set_text("")
        for source in sources:
            key = (source.kind, source.uid)
            self.source_status[key].set_text(self.tr("Reading selected sources…"))
        # 読取中は既存選択を保持する。完了後だけ削除/完了項目を照合する。
        def finished(report, _data):
            self._refreshing = False
            self._refresh_job = None
            self.refresh_button.set_sensitive(True)
            self.cancel_button.set_sensitive(False)
            if self._closed:
                return
            if token != self._refresh_token:
                for key in self.sources:
                    self._show_source_state(key)
                self._rebuild_personal()
                self.status.set_text(self.tr("Source selection changed during refresh; old results were ignored."))
                return
            if report.state is RefreshState.CANCELLED:
                for key in self.sources:
                    self._show_source_state(key)
                self._rebuild_personal()
                self.status.set_text(self.tr("Refresh cancelled."))
                return
            for result in report.sources:
                key = (result.source.kind, result.source.uid)
                self.source_states[key] = result.state
                self._reports[key] = result
                self._show_source_state(key)
            self._rebuild_personal()
            self._edited()
        try:
            job = self.services.refresh_async(sources, now.date(), zone, finished)
            if self._refreshing:
                self._refresh_job = job
        except Exception:
            from .refresh import RefreshReport, SourceRefreshResult
            finished(RefreshReport(RefreshState.PARTIAL, tuple(
                SourceRefreshResult(source, EDSState.COMMUNICATION_ERROR, ()) for source in sources)), None)

    def cancel_refresh(self):
        if self._refresh_job is not None:
            self._refresh_job.cancel()

    def _auto_refresh(self):
        """列挙完了後、保存済み選択の再取得を一度だけ手動操作の代わりに行う。"""
        if self._auto_refreshed or self._closed or not self._saved_sources:
            return
        self._auto_refreshed = True
        if self.selected_sources():
            self.refresh_sources()

    def _rebuild_personal(self):
        now, zone = self.services.clock(), self.services.timezone()
        contacts, todos, calendar = [], [], []
        for source in self.selected_sources():
            key = (source.kind, source.uid)
            report = self._reports.get(key)
            # 未確認キャッシュも確認なしに生成対象にしない。
            if (source.kind in self._registry_states or report is None
                    or self.source_states.get(key) not in (EDSState.READY, EDSState.EMPTY)
                    or report.state not in (EDSState.READY, EDSState.EMPTY)):
                continue
            if source.kind is SourceKind.ADDRESS_BOOK:
                contacts.extend(report.items)
            elif source.kind is SourceKind.TASK_LIST:
                todos.extend(report.items)
            else:
                calendar.extend(calendar_item(event, source.uid, zone) for event in report.items)
        self._todo_records = tuple(todos)
        self._calendar_items = tuple(calendar)
        self.contacts.set_contacts(contacts)
        self.todos.set_tasks(todos, now=now, system_tz=zone)
        all_items = tuple(c.as_selectable_item() for c in contacts) + self.todos.selected_items + tuple(calendar)
        # 未選択TODOをavailableに含め、選択は分類と独立に保持する。
        available_keys = {item.key for item in all_items} | {t.key for t in todos if not t.completed and t.completed_at is None}
        removed = [key for key in self.selection.selected_keys if key not in available_keys]
        for key in removed:
            self.selection.set_selected(key, False)
        # 削除された選択はサイレントに退ける。画面の最上部に説明を出さない。
        for contact in contacts:
            checkbox = self.contacts.checkbox_for(contact.key)
            if checkbox is not None:
                checkbox.connect("toggled", self._edited)
        for todo in todos:
            checkbox = self.todos.checkbox_for(todo.key)
            if checkbox is not None:
                checkbox.connect("toggled", self._edited)
        while child := self.calendar_box.get_first_child():
            self.calendar_box.remove(child)
        self.calendar_checks = {}
        for item in calendar:
            checkbox = Gtk.CheckButton(label=self._calendar_label(item))
            checkbox.set_active(self.selection.is_selected(item.key))
            checkbox.connect("toggled", lambda c, key=item.key: self.selection.set_selected(key, c.get_active()))
            checkbox.connect("toggled", self._edited)
            self.calendar_box.append(checkbox)
            self.calendar_checks[item.key] = checkbox
        if not calendar:
            self.calendar_box.append(Gtk.Label(label=self.tr("No events for today."), xalign=0))
        self._next_boundary = next_todo_reassessment(tuple(t.as_todo_input() for t in todos), now=now, system_tz=zone)
        # 保存済みの選択は、項目が一旦そろう時点で復元する。
        if self._saved_selection and not self._selection_restored:
            saved = set(self._saved_selection)
            for item in all_items:
                if item.key.opaque_value() in saved:
                    self.selection.set_selected(item.key, True)
            self._selection_restored = True

    def _calendar_label(self, item):
        fields = item.fields
        detail = (self.tr("All day") if fields["all_day"] == "true"
                  else fields["start"] + " – " + fields["end"])
        return fields["title"] + "\n" + detail

    def _apply_contact(self, _button=None):
        contact = self.contact_entry.get_text().strip()
        try:
            client = self.services.weather_factory(contact)
            if not contact:
                raise ValueError()
        except Exception:
            self.status.set_text(self.tr("Invalid contact."))
            return
        if self.weather_view is not None:
            self.weather_view.close()
        while child := self.weather_box.get_first_child():
            self.weather_box.remove(child)
        self.weather_view = self._weather_factory(client, clock=self.services.clock)
        self.weather_box.append(self.weather_view)
        self._capture_static_labels(self.weather_view)
        self.weather_view.status.connect("notify::label", self._weather_changed)
        self._persist_settings()
        self._edited()

    def _persist_settings(self, *_args):
        """現在の設定と選択を設定ファイルに書く。個人情報は保存しない。"""
        if self._restoring or self._closed:
            return
        selected_sources = [
            [source.kind.value, source.uid] for source in self.selected_sources()]
        selected_items = [key.opaque_value() for key in self.selection.selected_keys]
        paper_app = self.assets.paper_app_dropdown.get_selected()
        data = {
            "version": 1,
            "language": self.language,
            "name": self.name_entry.get_text(),
            "contact": self.contact_entry.get_text(),
            "sources": selected_sources,
            "selection": selected_items,
            "paper_app": paper_app,
            "notes": self.assets.notes_checkbox.get_active(),
            "contactless": self.assets.contactless_checkbox.get_active(),
            "prefecture": self.region.prefecture_code,
        }
        if not save_settings_file(self._config_path, data):
            self.status.set_text(self.tr("Settings could not be saved."))

    def _load_persisted_settings(self):
        """起動時に保存済み設定を読み、入力と選択を元に戻す。"""
        if self._config_path is None:
            return
        data = load_settings_file(self._config_path)
        if not isinstance(data, dict):
            return
        self._restoring = True
        try:
            language = data.get("language")
            if language in ("ja", "en"):
                self.language = language_code(language)
                set_language(self.language)
                self.language_dropdown.set_selected(1 if self.language == "ja" else 0)
            name = data.get("name")
            if isinstance(name, str):
                self.name_entry.set_text(name)
            contact = data.get("contact")
            if isinstance(contact, str):
                self.contact_entry.set_text(contact)
            # 保存済みの連絡先で天気を自動的に用意する。利用者に「設定を保存」の再押下を求めない。
            if isinstance(contact, str) and contact:
                self._apply_contact()
            sources = data.get("sources")
            if isinstance(sources, list):
                self._saved_sources = tuple(
                    tuple(pair) for pair in sources
                    if isinstance(pair, list) and len(pair) == 2
                    and isinstance(pair[0], str) and isinstance(pair[1], str))
            selection = data.get("selection")
            if isinstance(selection, list):
                self._saved_selection = tuple(
                    value for value in selection if isinstance(value, str))
            selection_saved = self._saved_sources or ()
            if selection_saved:
                for source in self.selected_sources():
                    pass  # テスト実行でまだEDSが来ていないケースのガード。
            for key, source in self.sources.items():
                if (source.kind.value, source.uid) in self._saved_sources:
                    self.source_checks[key].set_active(True)
            paper_app = data.get("paper_app")
            if isinstance(paper_app, int) and 0 <= paper_app < self.assets.paper_app_dropdown.get_model().get_n_items():
                self.assets.paper_app_dropdown.set_selected(paper_app)
            for name, checkbox in (("notes", self.assets.notes_checkbox),
                                   ("contactless", self.assets.contactless_checkbox)):
                value = data.get(name)
                if isinstance(value, bool):
                    checkbox.set_active(value)
            prefecture = data.get("prefecture")
            if isinstance(prefecture, int) and 1 <= prefecture <= 47:
                self.region.prefecture.set_selected(prefecture)
        finally:
            self._restoring = False

    def _delete_map_data(self, _button=None):
        """保存済みの地図データをまとめて削除。県ごとの削除は地図ページでも行える。"""
        if self._closed:
            return
        status = self.status

        def work():
            try:
                self.services.store.delete_all()
            except Exception:
                self._dispatch(lambda: status.set_text(self.tr("Map data could not be deleted.")))
            else:
                self._dispatch(lambda: status.set_text(self.tr("Map data deleted.")))
        try:
            self._submit(work)
        except Exception:
            status.set_text(self.tr("Map data could not be deleted."))

    def _photo_changed(self, *_args):
        if self._translating:
            return
        self._edited()

    def _endpoints_changed(self, *_args):
        if self._translating:
            return
        self._edited()

    def _weather_changed(self, *_args):
        if self._translating:
            return
        fix = getattr(self.weather_view.controller, "fix", None)
        if fix is not None and fix != self._last_fix:
            self._last_fix = fix
            if self.region.prefecture_code is None:
                self.region.set_current_location(fix)
            self.places.set_current_location(fix)
        self._edited()

    def _prefecture_changed(self, *_args):
        if self._translating:
            return
        # 抽出色と地名索引はregionの読込通知に任せる。ここで二重に消さない。
        self._loaded_extract = None
        self._map_failed = False
        self._map_failure_reason = None
        self.map_failure.set_text("")
        self._edited()

    def _region_changed(self, extract):
        """抽出の読込や削除で英断をする。呼び出し元はワーカーの完了先ウインドウで受け取る。"""
        if self._translating:
            return
        if extract != self._loaded_extract:
            self._loaded_extract = extract
            self._map_failed = False
            self._map_failure_reason = None
            self.map_failure.set_text("")
            if extract is not None:
                self.places.load_extract(extract)
            else:
                self.places.set_index(None)
            self._edited()

    def _weather_sheet(self):
        if self.weather_view is None:
            return None
        result = self.weather_view.controller.result
        if result.state is WeatherLocationState.READY and result.forecast is not None:
            return build_weather_sheet(result.forecast, now=self.services.clock().astimezone(self.services.timezone()))
        return None

    def _map_ready(self):
        return (not self._map_failed and self.region.selected_extract is not None
                and self.places.index is not None and self.places.selected_start is not None
                and self.places.selected_destination is not None)

    def pending_exclusions(self):
        pending = []
        if getattr(self.assets, "photo_state", "none") in ("choosing", "reading"):
            pending.append("photo-busy")
        # ソースの読取失敗・未登録は確認を求めず、生成時にその内容だけを除外する。
        return tuple(pending)


    def capture_request(self):
        """全GTK状態をメインスレッドで捕捉。重いI/Oは一切行わない。"""
        self.refresh_clock()
        if self._refreshing or self.pending_exclusions():
            return None
        zone, now = self.services.timezone(), self.services.clock()
        items = self.contacts.selected_items + self.todos.selected_items + tuple(
            item for item in self._calendar_items if self.selection.is_selected(item.key))
        weather = self._weather_sheet()
        if weather is not None:
            items += (weather.as_selectable_item(),)
        exclusions = []
        for source in self.selected_sources():
            if (source.kind in self._registry_states
                    or self.source_states.get((source.kind, source.uid)) not in (EDSState.READY, EDSState.EMPTY)):
                exclusions.append(self.tr("{source}: excluded").format(source=self.tr(source.kind.value)))
        if getattr(self.assets, "photo_state", "none") == "failed":
            exclusions.append(self.tr("photo: excluded"))
        if weather is None and self.weather_view is not None:
            exclusions.append(self.tr("weather: excluded"))
        has_map = self._map_ready()
        map_intent = (self.places.selected_start is not None
                      or self.places.selected_destination is not None)
        if not has_map and map_intent:
            exclusions.append(self.tr("map: excluded"))

        snapshot = GenerationSnapshot.capture(items, created_at=now,
                                               timezone_id=getattr(zone, "key", str(zone)))
        choices = self.assets.capture_choices()
        if getattr(self.assets, "photo_state", "none") == "failed":
            choices = replace(choices, photo=None)
        return GenerationRequest(snapshot, self.name_entry.get_text(), choices,
                                 weather, self.region.selected_extract if has_map else None,
                                 self.places.index if has_map else None,
                                 self.places.selected_start if has_map else None,
                                 self.places.selected_destination if has_map else None,
                                 tuple(exclusions), self.language,
                                 tuple((source.kind.value, source.uid,
                                        self.source_states[(source.kind, source.uid)].value
                                        if (source.kind, source.uid) in self.source_states else "not-read")
                                       for source in self.selected_sources()))

    def generate(self):
        if self._closed or self._generating:
            return False
        request = self.capture_request()
        if request is None:
            message = ("Photo selection is still in progress."
                       if "photo-busy" in self.pending_exclusions()
                       else "Confirm unavailable source exclusions before generating.")
            self.status.set_text(self.tr(message))
            return False
        self._generation += 1
        token = self._generation
        self._generating = True
        self.generate_button.set_sensitive(False)
        self.progress.set_visible(True)
        self._generation_progresspulse = GLib.timeout_add(150, self._pulse_generation_progress)
        map_key = (request.extract, request.departure, request.destination)

        def work():
            try:
                result = self.services.generate(request)
            except Exception as error:
                source = error.source if isinstance(error, GenerationFailure) else "booklet"
                reason = error.reason if isinstance(error, GenerationFailure) else "render-failed"
                self._dispatch(lambda: self._generated(token, None, source, reason, map_key))
            else:
                self._dispatch(lambda: self._generated(token, result, None))
        try:
            self._submit(work)
        except Exception:
            self._generated(token, None, "booklet")
        return True

    def _pulse_generation_progress(self):
        if not self._generating:
            self._generation_progresspulse = None
            self.progress.set_visible(False)
            return GLib.SOURCE_REMOVE
        self.progress.set_visible(True)
        self.progress.pulse()
        return GLib.SOURCE_CONTINUE

    def _generated(self, token, result, failure, reason=None, map_key=None):
        if self._closed or token != self._generation:
            return
        self._generating = False
        self.generate_button.set_sensitive(True)
        if failure:
            if failure == "map":
                if map_key == (self.region.selected_extract, self.places.selected_start, self.places.selected_destination):
                    self._map_failed = True
                    self._map_failure_reason = reason if reason in ROUTE_REASONS else "route-failed"
                    self.map_failure.set_text(self.tr("Map failure: {reason}").format(
                        reason=self.tr(ROUTE_REASONS.get(reason, ROUTE_REASONS["route-failed"]))))
                self.status.set_text(self.tr("Walking map failed. The map is not included."))
                self._reveal_section("Walking map")
            else:
                self.status.set_text(self.tr("Generation failed. Your previous PDF has been kept."))
            return
        try:
            self.output.set_pdf(result.pdf, warnings=result.warnings)
        except Exception:
            self.status.set_text(self.tr("Generation failed. Your previous PDF has been kept."))
            return
        self.status.set_text(self.tr("Booklet ready. Later edits do not change this PDF."))
        self._reveal_section("PDF")

    def refresh_clock(self):
        zone = self.services.timezone()
        now = self.services.clock().astimezone(zone)
        current = (now.date(), getattr(zone, "key", str(zone)))
        changed = self._last_clock is not None and current != self._last_clock
        self._last_clock = current
        if changed:
            # 今日の繰り返し予定はEDSから再取得が必要。古い日を印刷しない。
            for key, source in self.sources.items():
                if source.kind is SourceKind.CALENDAR:
                    self.source_states.pop(key, None)
                    self._reports.pop(key, None)
                    self._show_source_state(key)
            self._refresh_token += 1
        if changed or self._next_boundary is None or now.timestamp() >= self._next_boundary.timestamp():
            self._rebuild_personal()
        if changed:
            if self.weather_view is not None and hasattr(self.weather_view, "forecast_details"):
                sheet = self._weather_sheet()
                self.weather_view.sheet = sheet
                self.weather_view.forecast_details.set_text("\n".join(weather_lines(sheet)) if sheet else "")
            self._edited()
        return changed

    def _tick(self):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.refresh_clock()
        return GLib.SOURCE_CONTINUE

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._refresh_token += 1
        self.cancel_refresh()
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        for view in (self.assets, self.region, self.places, self.weather_view, self.output):
            if view is not None:
                view.close()
        if self._worker is not None:
            self._worker.shutdown(wait=False, cancel_futures=True)
        self.contact_entry.set_text("")
        self.name_entry.set_text("")
        self._reports.clear()
