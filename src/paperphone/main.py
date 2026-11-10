"""GTK 4 / libadwaitaのアプリ起動と再利用可能なメイン画面。"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

APPLICATION_ID = "dev.nawashiro.paperphone"


class PaperPhoneApplication(Adw.Application):
    """GNOME版Paper Phoneのアプリケーション基底。"""

    def __init__(self, *, services=None, view_factory=None, application_id=APPLICATION_ID) -> None:
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._services = services
        self._view_factory = view_factory
        self._application_id = application_id

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = Adw.ApplicationWindow(application=self)
            window.set_title("Paper Phone")
            window.set_default_size(900, 640)
            view = self._build_content()
            window.set_content(view)
            window.connect("close-request", lambda _window: self._close_view(view))
            window.connect("notify::is-active", lambda w, _p: view.refresh_clock() if w.is_active() else None)
        window.present()

    @staticmethod
    def _close_view(view):
        view.close()
        return False

    def _build_content(self):
        from .application_services import ApplicationServices
        from .application_view import ApplicationView
        services = self._services or ApplicationServices(application_id=self._application_id)
        factory = self._view_factory or ApplicationView
        return factory(services)


def main() -> int:
    """アプリを起動する。"""
    return PaperPhoneApplication().run(sys.argv)
