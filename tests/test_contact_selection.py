"""GTK連絡先選択ビューの操作fixture。"""

import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.contact_selection import MAX_CONTACTS, ContactSelectionView
from paperphone.contacts import ContactRecord
from paperphone.selection import ItemKey, SelectionModel


class ContactSelectionViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    @staticmethod
    def contact(source_uid, contact_uid, name, *phones):
        return ContactRecord(source_uid, contact_uid, name, tuple(phones))

    def test_japanese_contact_and_same_uid_from_two_books_are_selectable_independently(self):
        first = self.contact("book-a", "shared-uid", "山田 花子", "+81-90-0000-0001")
        second = self.contact("book-b", "shared-uid", "Fixture Contact", "+1-555-0100")
        no_phone = self.contact("book-a", "no-phone", "Fixture No Phone")
        selection = SelectionModel()
        view = ContactSelectionView(selection)

        view.set_contacts((first, second, no_phone))

        self.assertEqual(view.row_count, 2)
        self.assertIn("山田 花子", view.checkbox_for(first.key).get_label())
        self.assertIn("+81-90-0000-0001", view.checkbox_for(first.key).get_label())
        view.checkbox_for(first.key).set_active(True)
        self.assertEqual(view.selected_contacts, (first,))
        self.assertEqual(view.selected_items[0].key, first.key)
        self.assertEqual(view.selected_items[0].fields["phone"], "+81-90-0000-0001")
        self.assertEqual(selection.selected_keys, (first.key,))
        self.assertFalse(selection.is_selected(second.key))

    def test_eight_contacts_are_browsable_and_selectable(self):
        contacts = tuple(
            self.contact("book", f"contact-{index}", f"Fixture Contact {index}", f"+1-555-{index:04d}")
            for index in range(8)
        )
        selection = SelectionModel()
        view = ContactSelectionView(selection)
        view.set_contacts(contacts)

        scroller = view._list.get_ancestor(Gtk.ScrolledWindow)
        self.assertIsInstance(scroller, Gtk.ScrolledWindow)
        self.assertGreaterEqual(scroller.get_max_content_height(), 300)
        for contact in contacts:
            self.assertTrue(view.checkbox_for(contact.key).get_sensitive())
        view.checkbox_for(contacts[7].key).set_active(True)
        self.assertEqual(len(view.selected_contacts), 1)

    def test_selection_limit_matches_booklet_capacity(self):
        contacts = tuple(
            self.contact("book", f"contact-{index}", f"Fixture Contact {index}", f"+1-555-{index:04d}")
            for index in range(MAX_CONTACTS + 1)
        )
        selection = SelectionModel()
        view = ContactSelectionView(selection)
        view.set_contacts(contacts)

        for contact in contacts[:MAX_CONTACTS]:
            view.checkbox_for(contact.key).set_active(True)
        last_checkbox = view.checkbox_for(contacts[-1].key)
        last_checkbox.set_active(True)

        self.assertEqual(len(view.selected_contacts), MAX_CONTACTS)
        self.assertEqual(len(selection.selected_keys), MAX_CONTACTS)
        self.assertFalse(last_checkbox.get_active())
        self.assertTrue(view._limit_message.get_visible())

    def test_refresh_restores_selection_without_displaying_phone_less_rows(self):
        selected = self.contact("book", "selected", "Fixture Selected", "+1-555-0101")
        absent = self.contact("book", "absent", "Fixture No Phone")
        selection = SelectionModel()
        selection.set_selected(selected.key, True)
        view = ContactSelectionView(selection)

        view.set_contacts((selected, absent))

        self.assertTrue(view.checkbox_for(selected.key).get_active())
        self.assertIsNone(view.checkbox_for(absent.key))
        self.assertEqual(view.selected_contacts, (selected,))


if __name__ == "__main__":
    unittest.main()
