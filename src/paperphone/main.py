"""GTK 4 / libadwaitaの最小起動ウィンドウ。"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from .i18n import gettext_for

_ = gettext_for()

# 配布用IDはタスク8.3で確定する。ここでは既存Android IDを暫定利用する。
APPLICATION_ID = "com.withgoogle.experiments.unplugged"


class PaperPhoneApplication(Adw.Application):
    """GNOME版Paper Phoneのアプリケーション基底。"""

    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = Adw.ApplicationWindow(application=self)
            window.set_title("Paper Phone")
            window.set_default_size(900, 640)
            window.set_content(self._build_content())
        window.present()

    @staticmethod
    def _build_content() -> Adw.ToolbarView:
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        content.set_margin_top(32)
        content.set_margin_bottom(32)
        content.set_margin_start(32)
        content.set_margin_end(32)

        title = Gtk.Label(label="Paper Phone")
        title.add_css_class("title-1")
        description = Gtk.Label(
            label=_("Create, preview, save, and print your paper booklet.")
        )
        description.set_wrap(True)
        description.set_justify(Gtk.Justification.CENTER)

        status = Gtk.Label(label=_("GNOME startup foundation"))
        start_button = Gtk.Button(label=_("Start preparing"))
        start_button.add_css_class("suggested-action")
        start_button.connect(
            "clicked",
            lambda _button: status.set_text(_("Startup check complete.")),
        )

        content.append(title)
        content.append(description)
        content.append(start_button)
        content.append(status)
        toolbar.set_content(content)
        return toolbar


def main() -> int:
    """アプリを起動する。"""
    return PaperPhoneApplication().run(sys.argv)
