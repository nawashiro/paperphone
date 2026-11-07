"""EDSアドレス帳・vCard読取の匿名fixtureテスト。"""

import unittest

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from paperphone.contacts import ALL_CONTACTS_QUERY, AddressBookReadAdapter, ContactRecord, parse_vcards
from paperphone.eds import EDSClientHandle, EDSResult, EDSState, SourceDescriptor, SourceKind


def vcard(uid, name, phone_lines=(), *, version="3.0"):
    lines = ["BEGIN:VCARD", f"VERSION:{version}", f"UID:{uid}"]
    if name is not None:
        lines.append(f"FN:{name}")
    lines.extend(phone_lines)
    lines.append("END:VCARD")
    return "\r\n".join(lines)


class FakeAddressBookRegistry:
    def __init__(self, cards_by_path):
        self.cards_by_path = cards_by_path
        self.calls = []

    def call_read_async(self, handle, method, parameters, reply_type, callback, *, cancellable=None, user_data=None):
        self.calls.append((handle, method, parameters, reply_type))
        if method == "Open":
            reply = GLib.Variant("(as)", ([],))
        elif method == "GetContactList":
            reply = GLib.Variant("(as)", (self.cards_by_path[handle.object_path],))
        else:
            raise AssertionError("Unexpected read method")
        callback(EDSResult(EDSState.READY, (reply,)), user_data)


class ContactVCardParserTest(unittest.TestCase):
    def test_reads_japanese_name_and_multiple_phone_numbers(self):
        card = vcard(
            "contact-fixture-1",
            "山田\\, 花子",
            (
                "TEL;TYPE=CELL:+81-90-0000-0001",
                "item1.TEL;TYPE=WORK:tel:+81-3-0000-0002",
                "TEL;TYPE=OTHER:+81-90-0000-0001",
            ),
        )

        contacts = parse_vcards((card,), source_uid="book-fixture-a")

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].full_name, "山田, 花子")
        self.assertEqual(
            contacts[0].phone_numbers,
            ("+81-90-0000-0001", "+81-3-0000-0002"),
        )
        self.assertEqual(contacts[0].key.object_uid, "contact-fixture-1")
        self.assertEqual(contacts[0].as_selectable_item().fields["name"], "山田, 花子")

    def test_phone_less_or_incomplete_contacts_are_not_print_candidates(self):
        no_phone = vcard("contact-no-phone", "Fixture No Phone")
        no_name = vcard("contact-no-name", None, ("TEL:+1-555-0100",))
        no_uid = "BEGIN:VCARD\r\nFN:Fixture No UID\r\nTEL:+1-555-0101\r\nEND:VCARD"

        self.assertEqual(parse_vcards((no_phone, no_name, no_uid), source_uid="book-fixture"), ())

    def test_name_falls_back_to_structured_n_property(self):
        card = (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:contact-n-fallback\r\n"
            "N:山田;花子;;;\r\nTEL:+81-90-0000-0003\r\nEND:VCARD"
        )

        contacts = parse_vcards((card,), source_uid="book-fixture")

        self.assertEqual(contacts[0].full_name, "山田 花子")

    def test_quoted_printable_japanese_and_folded_lines(self):
        card = (
            "BEGIN:VCARD\r\nVERSION:2.1\r\nUID:contact-qp\r\n"
            "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E4=BD=90=E8=97=A4=E3=80=80\r\n"
            " =E8=8A=B1=E5=AD=90\r\nTEL;CELL:+81-90-0000-0004\r\nEND:VCARD"
        )

        contacts = parse_vcards((card,), source_uid="book-fixture")

        self.assertEqual(contacts[0].full_name, "佐藤　花子")

    def test_duplicate_cards_are_deduplicated_and_repr_hides_contact_data(self):
        card = vcard("private-fixture-uid", "Private Fixture Name", ("TEL:+1-555-0102",))

        contacts = parse_vcards((card, card), source_uid="private-fixture-source")

        self.assertEqual(len(contacts), 1)
        for private_value in ("Private Fixture Name", "+1-555-0102", "private-fixture-uid", "private-fixture-source"):
            self.assertNotIn(private_value, repr(contacts[0]))
        self.assertNotIn("Private Fixture Name", repr(contacts[0].as_selectable_item()))

    def test_source_uid_is_required(self):
        with self.assertRaises(ValueError):
            parse_vcards((), source_uid="")


class AddressBookReadAdapterTest(unittest.TestCase):
    def test_reads_multiple_selected_books_and_never_calls_a_write_method(self):
        japanese = vcard("same-contact-uid", "日本語の連絡先", ("TEL:+81-90-0000-0005",))
        english = vcard("same-contact-uid", "Fixture Contact", ("TEL:+1-555-0103",))
        no_phone = vcard("no-phone-uid", "Fixture No Phone")
        registry = FakeAddressBookRegistry(
            {
                "/book/a": [japanese, no_phone],
                "/book/b": [english],
            }
        )
        adapter = AddressBookReadAdapter(registry)
        sources = (
            (
                SourceDescriptor("book-source-a", "Fixture A", SourceKind.ADDRESS_BOOK, True),
                EDSClientHandle(SourceKind.ADDRESS_BOOK, ":1.fixture", "/book/a"),
            ),
            (
                SourceDescriptor("book-source-b", "Fixture B", SourceKind.ADDRESS_BOOK, True),
                EDSClientHandle(SourceKind.ADDRESS_BOOK, ":1.fixture", "/book/b"),
            ),
        )
        results = []

        adapter.read_sources_async(sources, lambda result, _data: results.append(result))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, EDSState.READY)
        contacts = results[0].items
        self.assertEqual(len(contacts), 2)
        self.assertEqual({contact.source_uid for contact in contacts}, {"book-source-a", "book-source-b"})
        self.assertEqual(len({contact.key for contact in contacts}), 2)
        self.assertEqual(
            [method for _handle, method, _params, _reply in registry.calls],
            ["Open", "GetContactList", "Open", "GetContactList"],
        )
        queries = [params.unpack()[0] for _handle, method, params, _reply in registry.calls if method == "GetContactList"]
        self.assertEqual(queries, [ALL_CONTACTS_QUERY, ALL_CONTACTS_QUERY])
        self.assertTrue(all(method in {"Open", "GetContactList"} for _handle, method, _p, _r in registry.calls))
        self.assertNotIn("日本語の連絡先", repr(results[0]))
        self.assertNotIn("book-source-a", repr(results[0]))

    def test_registered_book_without_phone_candidates_is_empty(self):
        registry = FakeAddressBookRegistry({"/book/empty": [vcard("no-phone", "No Phone")]})
        adapter = AddressBookReadAdapter(registry)
        source = SourceDescriptor("book-empty", "Fixture Empty", SourceKind.ADDRESS_BOOK, True)
        handle = EDSClientHandle(SourceKind.ADDRESS_BOOK, ":1.fixture", "/book/empty")
        results = []

        adapter.read_contacts_async(source, handle, lambda result, _data: results.append(result))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], EDSResult(EDSState.EMPTY))

    def test_no_selected_books_is_distinct_from_an_empty_book(self):
        results = []
        AddressBookReadAdapter(FakeAddressBookRegistry({})).read_sources_async(
            (), lambda result, _data: results.append(result)
        )
        self.assertEqual(results, [EDSResult(EDSState.UNREGISTERED)])

    def test_calendar_client_cannot_be_used_as_an_address_book(self):
        source = SourceDescriptor("calendar", "Fixture", SourceKind.CALENDAR, True)
        handle = EDSClientHandle(SourceKind.CALENDAR, ":1.fixture", "/calendar")
        with self.assertRaises(ValueError):
            AddressBookReadAdapter(FakeAddressBookRegistry({})).read_contacts_async(
                source, handle, lambda _result, _data: None
            )


if __name__ == "__main__":
    unittest.main()
