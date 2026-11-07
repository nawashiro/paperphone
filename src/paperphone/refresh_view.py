"""EDS更新中・完了・取消状態を示すGTK 4コントロール。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, tzinfo

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .eds import EDSState, SourceDescriptor
from .i18n import gettext_for
from .refresh import EDSRefreshController, RefreshJob, RefreshReport, RefreshState

_ = gettext_for()


class EDSRefreshView(Gtk.Box):
    """利用者が選択したEDSソースの読取を開始・取消する。"""

    def __init__(
        self,
        controller: EDSRefreshController,
        get_sources: Callable[[], Sequence[SourceDescriptor]],
        get_day: Callable[[], date],
        get_system_timezone: Callable[[], tzinfo],
        on_report: Callable[[RefreshReport], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._controller = controller
        self._get_sources = get_sources
        self._get_day = get_day
        self._get_system_timezone = get_system_timezone
        self._on_report = on_report
        self._job: RefreshJob | None = None
        self._is_loading = False
        self._last_report: RefreshReport | None = None

        self._refresh_button = Gtk.Button(label=_("Refresh selected sources"))
        self._refresh_button.connect("clicked", self._on_refresh_clicked)
        self.append(self._refresh_button)

        self._cancel_button = Gtk.Button(label=_("Cancel"))
        self._cancel_button.set_sensitive(False)
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        self.append(self._cancel_button)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        self.append(self._spinner)

        self._status = Gtk.Label(label=_("Not refreshed yet."))
        self._status.set_xalign(0.0)
        self._status.set_wrap(True)
        self._status.set_hexpand(True)
        self.append(self._status)

    @property
    def is_loading(self) -> bool:
        return self._is_loading

    @property
    def last_report(self) -> RefreshReport | None:
        return self._last_report

    @property
    def refresh_button(self) -> Gtk.Button:
        return self._refresh_button

    @property
    def cancel_button(self) -> Gtk.Button:
        return self._cancel_button

    @property
    def status_label(self) -> Gtk.Label:
        return self._status

    def refresh(self) -> None:
        if self._is_loading:
            return
        self._last_report = None
        self._set_loading(True)
        try:
            job = self._controller.refresh_async(
                self._get_sources(),
                self._get_day(),
                self._get_system_timezone(),
                self._on_refresh_finished,
            )
        except Exception:
            self._set_loading(False)
            self._status.set_text(_("Refresh could not be started."))
            return
        # Empty source sets may finish synchronously; don't retain a completed job.
        if self._is_loading:
            self._job = job

    def cancel(self) -> None:
        if self._job is not None and self._is_loading:
            self._job.cancel()

    def _set_loading(self, loading: bool) -> None:
        self._is_loading = loading
        self._refresh_button.set_sensitive(not loading)
        self._cancel_button.set_sensitive(loading)
        self._spinner.set_visible(loading)
        if loading:
            self._spinner.start()
            self._status.set_text(_("Reading selected sources…"))
        else:
            self._spinner.stop()

    def _on_refresh_clicked(self, _button: Gtk.Button) -> None:
        self.refresh()

    def _on_cancel_clicked(self, _button: Gtk.Button) -> None:
        self.cancel()

    def _on_refresh_finished(self, report: RefreshReport, _user_data: object | None) -> None:
        self._job = None
        self._last_report = report
        self._set_loading(False)
        if report.state is RefreshState.CANCELLED:
            self._status.set_text(_("Refresh cancelled."))
        elif any(source.state is EDSState.REFRESH_UNCONFIRMED for source in report.sources):
            self._status.set_text(_("CalDAV changes were not confirmed. Cached tasks may be shown."))
        elif report.state is RefreshState.PARTIAL:
            self._status.set_text(_("Some sources could not be read."))
        else:
            self._status.set_text(_("Refresh complete."))
        if self._on_report is not None:
            self._on_report(report)
