"""許可された現在地から天気を取得する独立したGTKビュー。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .i18n import N_, gettext_for
from .weather_location import WeatherLocationController, WeatherLocationState as State
from .temporal import system_timezone
from .weather_selection import remaining_targets
from .weather_sheet import build_weather_sheet, weather_lines

_ = gettext_for()

MESSAGES = {
    State.IDLE: "",
    State.LOCATING: N_("Requesting location permission…"),
    State.FETCHING: N_("Fetching weather…"),
    State.READY: "",
    State.DENIED: N_("Location denied."),
    State.TIMEOUT: N_("Location timed out."),
    State.UNAVAILABLE: N_("Location unavailable."),
    State.INVALID: N_("Location service returned unusable coordinates. Nothing was sent."),
    State.FAILED: N_("Weather fetch failed."),
}

NO_FORECAST_MESSAGE = N_("No forecast left for today.")


class WeatherLocationView(Gtk.Box):
    def __init__(self, client, *, controller_factory=WeatherLocationController,
                 clock=lambda: datetime.now(system_timezone())):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._clock = clock
        self.sheet = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weather")
        self._started = False
        self.controller = controller_factory(client, submit=self._worker.submit,
                                             dispatch=self._dispatch, on_change=self._changed)
        self.description = Gtk.Label(label=_("Location and client contact are sent to MET Norway."))
        self.description.set_wrap(True)
        # 空文字列を _() に渡すとカタログのヘッダーが返るので、素の空にしておく。
        self.status = Gtk.Label(label=_(MESSAGES[State.IDLE]))
        self.status.set_wrap(True)
        self.request_button = Gtk.Button(label=_("Fetch weather"))
        self.request_button.connect("clicked", self._request)
        # 今日の9・12・18時のどれも選べない時点では取得そのものをしない。
        if not remaining_targets(self._clock()):
            self.request_button.set_visible(False)
            self.description.set_visible(False)
        self.forecast_details = Gtk.Label()
        self.forecast_details.set_wrap(True)
        self.forecast_details.set_xalign(0)
        for widget in (self.description, self.request_button, self.status, self.forecast_details):
            self.append(widget)

    @staticmethod
    def _dispatch(callback):
        def run():
            callback()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    def _changed_result_only(self, text):
        self.status.set_text(text)
        self.request_button.set_sensitive(False)
        self.request_button.set_visible(False)
        self.forecast_details.set_text("")

    def _changed(self, result):
        self.status.set_text(_(MESSAGES[result.state]))
        self.request_button.set_sensitive(False)
        self.sheet = (build_weather_sheet(result.forecast, now=self._clock())
                      if result.state == State.READY and result.forecast is not None else None)
        self.forecast_details.set_text("\n".join(weather_lines(self.sheet)) if self.sheet else "")

    @property
    def printable_item(self):
        return self.sheet.as_selectable_item() if self.sheet else None

    def _request(self, _button):
        if self._started:
            return
        if not remaining_targets(self._clock()):
            # 18時枠の選択窓が閉じた後の取得はしない。短い文言だけを出す。
            self._changed_result_only(_(NO_FORECAST_MESSAGE))
            return
        window = self.get_root()
        if not isinstance(window, Gtk.Window) or not window.is_active():
            self.status.set_text(_(MESSAGES[State.UNAVAILABLE]))
            return
        self._started = True
        self.request_button.set_sensitive(False)
        # ポータルの親ウィンドウハンドル取得はGTKのGIバインディングが
        # Gtk.Window.export_handle を公開してくれる場合だけ行う。
        # 公開されていない環境では parent_window を空にしてポータルだけを要求する。
        exported_handle = None
        exporter = getattr(Gtk.Window, "export_handle", None)
        if exporter is not None:
            try:
                def exported(window_, handle, _data):
                    nonlocal exported_handle
                    exported_handle = handle
                    return
                exporter(window, exported, None)
            except Exception:
                exported_handle = None
        self._dispatch(lambda: self.controller.start(
            ("wayland:" + exported_handle) if exported_handle else ""))
        return

    def close(self):
        """ホスト画面の破棄時に呼ぶ。実行中のHTTPは終了後に破棄する。"""
        self.controller.close()
        self._worker.shutdown(wait=False, cancel_futures=True)
