"""連絡先の印刷選択を行うGTK 4ビュー。"""

from __future__ import annotations

from collections.abc import Iterable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .contacts import ContactRecord
from .i18n import gettext_for
from .selection import ItemKey, SelectableItem, SelectionModel

_ = gettext_for()
MAX_CONTACTS = 7


class ContactSelectionView(Gtk.Box):
    """電話番号のある連絡先を表示し、最大7件を選択可能にする。"""

    def __init__(self, selection: SelectionModel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._selection = selection
        self._contacts: tuple[ContactRecord, ...] = ()
        self._rows: dict[ItemKey, Gtk.ListBoxRow] = {}
        self._checkboxes: dict[ItemKey, Gtk.CheckButton] = {}
        self._updating = False

        title = Gtk.Label(label=_("Contacts"))
        title.add_css_class("title-2")
        title.set_xalign(0.0)
        self.append(title)

        self._hint = Gtk.Label(label=_("Select up to 7 contacts."))
        self._hint.set_xalign(0.0)
        self._hint.add_css_class("dim-label")
        self.append(self._hint)

        self._limit_message = Gtk.Label(label=_("You can select up to 7 contacts."))
        self._limit_message.set_xalign(0.0)
        self._limit_message.set_visible(False)
        self.append(self._limit_message)

        self._empty = Gtk.Label(label=_("No contacts with phone numbers are available."))
        self._empty.set_xalign(0.0)
        self._empty.set_wrap(True)
        self._empty.add_css_class("dim-label")
        self.append(self._empty)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.update_property([Gtk.AccessibleProperty.LABEL], [_("Contacts")])
        title.set_mnemonic_widget(self._list)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self._list)
        scroller.set_vexpand(True)
        self.append(scroller)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def selected_contacts(self) -> tuple[ContactRecord, ...]:
        return tuple(
            contact
            for contact in self._contacts
            if self._selection.is_selected(contact.key)
        )

    @property
    def selected_items(self) -> tuple[SelectableItem, ...]:
        """生成スナップショットへ渡す選択済み連絡先を返す。"""
        return tuple(contact.as_selectable_item() for contact in self.selected_contacts)

    def checkbox_for(self, key: ItemKey) -> Gtk.CheckButton | None:
        """テスト・操作連携用に表示中の選択コントロールを返す。"""
        return self._checkboxes.get(key)

    def set_contacts(self, contacts: Iterable[ContactRecord]) -> None:
        for row in self._rows.values():
            self._list.remove(row)
        self._rows.clear()
        self._checkboxes.clear()

        unique = {
            contact.key: contact
            for contact in contacts
            if contact.full_name and contact.phone_numbers
        }
        self._contacts = tuple(
            sorted(
                unique.values(),
                key=lambda contact: (
                    contact.full_name.casefold(),
                    contact.source_uid,
                    contact.contact_uid,
                ),
            )
        )
        for contact in self._contacts:
            key = contact.key
            phones = "\n".join(contact.phone_numbers)
            checkbox = Gtk.CheckButton(label=f"{contact.full_name}\n{phones}")
            checkbox.set_use_underline(False)
            checkbox.set_hexpand(True)
            label = checkbox.get_child()
            if isinstance(label, Gtk.Label):
                label.set_wrap(True)
                label.set_xalign(0.0)
            checkbox.set_active(self._selection.is_selected(key))
            checkbox.connect("toggled", self._on_toggled, key)

            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            row.set_child(checkbox)
            self._list.append(row)
            self._rows[key] = row
            self._checkboxes[key] = checkbox

        self._empty.set_visible(not self._contacts)
        self._limit_message.set_visible(False)

    def _on_toggled(self, checkbox: Gtk.CheckButton, key: ItemKey) -> None:
        if self._updating:
            return
        if checkbox.get_active() and not self._selection.is_selected(key):
            selected_count = sum(
                1 for contact in self._contacts if self._selection.is_selected(contact.key)
            )
            if selected_count >= MAX_CONTACTS:
                self._updating = True
                checkbox.set_active(False)
                self._updating = False
                self._limit_message.set_visible(True)
                # 必須状態を読み取り可能にする。中止が必要な既定優先度にする。
                if Gtk.check_version(4, 14, 0) is None:
                    self._limit_message.announce(self._limit_message.get_text(),
                                                 Gtk.AccessibleAnnouncementPriority.HIGH)
                return
        self._selection.set_selected(key, checkbox.get_active())
        self._limit_message.set_visible(False)
