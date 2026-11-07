"""EDSアドレス帳の読取専用アダプターとvCardの最小投影。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from quopri import decodestring

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from .eds import (
    EDSClientHandle,
    EDSRegistryAdapter,
    EDSResult,
    EDSState,
    SourceDescriptor,
    SourceKind,
)
from .selection import ItemKey, SelectableItem


# Evolution AddressBook Query Language: match entries containing any field.
ALL_CONTACTS_QUERY = '(contains "x-evolution-any-field" "")'
_READ_CONCURRENCY = 4


@dataclass(frozen=True, slots=True, repr=False)
class ContactRecord:
    """冊子に必要な連絡先項目だけを保持する。値のreprは伏せる。"""

    source_uid: str = field(repr=False)
    contact_uid: str = field(repr=False)
    full_name: str = field(repr=False)
    phone_numbers: tuple[str, ...] = field(repr=False)

    @property
    def key(self) -> ItemKey:
        return ItemKey(self.source_uid, self.contact_uid)

    def as_selectable_item(self) -> SelectableItem:
        return SelectableItem(
            self.key,
            "contacts",
            {"name": self.full_name, "phone": "\n".join(self.phone_numbers)},
        )

    def __repr__(self) -> str:
        return f"ContactRecord(name=<redacted>, phone_count={len(self.phone_numbers)})"


def _unescape_text(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            index += 1
            escaped = value[index]
            result.append("\n" if escaped in "nN" else escaped)
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _split_escaped(value: str, separator: str) -> tuple[str, ...]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if char == separator and not escaped:
            parts.append("".join(current))
            current.clear()
            continue
        current.append(char)
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    parts.append("".join(current))
    return tuple(parts)


def _unfold_lines(card: str) -> tuple[str, ...]:
    logical: list[str] = []
    for line in card.splitlines():
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        elif (
            logical
            and logical[-1].endswith("=")
            and "ENCODING=QUOTED-PRINTABLE" in logical[-1].upper()
        ):
            logical[-1] = logical[-1][:-1] + line
        elif line:
            logical.append(line)
    return tuple(logical)


def _vcard_blocks(value: str) -> tuple[str, ...]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in value.splitlines():
        normalized = line.strip().upper()
        if normalized == "BEGIN:VCARD":
            current = [line]
        elif current is not None:
            current.append(line)
            if normalized == "END:VCARD":
                blocks.append("\r\n".join(current))
                current = None
    return tuple(blocks)


def _clean_name(value: str) -> str:
    normalized = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return " ".join(part for part in normalized.split(" ") if part)


def _property_value(parameters: Mapping[str, str], value: str) -> str:
    encoding = parameters.get("ENCODING", "").upper()
    if encoding not in {"QUOTED-PRINTABLE", "QP"}:
        return value
    charset = parameters.get("CHARSET", "UTF-8")
    try:
        encoded = value.encode("ascii")
        return decodestring(encoded).decode(charset)
    except (LookupError, UnicodeError):
        return ""


def _parse_card(card: str, source_uid: str) -> ContactRecord | None:
    contact_uid = ""
    formatted_name = ""
    structured_name = ""
    phones: list[str] = []

    for line in _unfold_lines(card):
        left, separator, raw_value = line.partition(":")
        if not separator:
            continue
        segments = left.split(";")
        property_name = segments[0].rsplit(".", 1)[-1].upper()
        parameters: dict[str, str] = {}
        for parameter in segments[1:]:
            name, has_value, value = parameter.partition("=")
            if has_value:
                parameters[name.upper()] = value.strip().strip('"')
            elif parameter:
                parameters["TYPE"] = ",".join(
                    filter(None, (parameters.get("TYPE", ""), parameter))
                )
        value = _property_value(parameters, raw_value)
        if property_name == "UID" and not contact_uid:
            contact_uid = _unescape_text(value).strip()
        elif property_name == "FN" and not formatted_name:
            formatted_name = _unescape_text(value)
        elif property_name == "N" and not structured_name:
            structured_name = value
        elif property_name == "TEL":
            phone = _unescape_text(value).strip()
            if phone.casefold().startswith("tel:"):
                phone = phone[4:]
            if phone and phone not in phones:
                phones.append(phone)

    full_name = _clean_name(formatted_name)
    if not full_name and structured_name:
        components = tuple(
            _clean_name(_unescape_text(part))
            for part in _split_escaped(structured_name, ";")
        )
        # vCard N is family;given;additional;prefix;suffix. Family-first is
        # stable across locales and preserves Japanese names when FN is absent.
        family, given, additional, prefix, suffix = (components + ("",) * 5)[:5]
        full_name = " ".join(
            part for part in (prefix, family, given, additional, suffix) if part
        )

    if not contact_uid or not full_name or not phones:
        return None
    return ContactRecord(source_uid, contact_uid, full_name, tuple(phones))


def parse_vcards(vcards: Iterable[str], *, source_uid: str) -> tuple[ContactRecord, ...]:
    """vCardからUID・氏名・電話だけを抽出し、電話なし等は候補から除外する。"""
    if not source_uid:
        raise ValueError("source_uid must not be empty")
    contacts: dict[tuple[str, str], ContactRecord] = {}
    for value in vcards:
        if not isinstance(value, str):
            continue
        for card in _vcard_blocks(value):
            contact = _parse_card(card, source_uid)
            if contact is not None:
                contacts[(contact.source_uid, contact.contact_uid)] = contact
    return tuple(
        sorted(
            contacts.values(),
            key=lambda contact: (contact.full_name.casefold(), contact.contact_uid),
        )
    )


class AddressBookReadAdapter:
    """選択されたEDSアドレス帳から連絡先を非同期・読取専用で取得する。"""

    def __init__(self, registry: EDSRegistryAdapter) -> None:
        self._registry = registry

    def read_contacts_async(
        self,
        source: SourceDescriptor,
        handle: EDSClientHandle,
        callback: Callable[[EDSResult[ContactRecord], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        if (
            source.kind is not SourceKind.ADDRESS_BOOK
            or handle.kind is not SourceKind.ADDRESS_BOOK
        ):
            raise ValueError("An address-book source and handle are required")
        if not source.uid:
            raise ValueError("An address-book UID is required")

        def on_contacts(
            result: EDSResult[GLib.Variant],
            _ignored: object | None,
        ) -> None:
            if result.state is not EDSState.READY:
                callback(EDSResult(result.state), user_data)
                return
            if not result.items:
                callback(EDSResult(EDSState.EMPTY), user_data)
                return
            try:
                values = result.items[0].unpack()[0]
                contacts = parse_vcards(values, source_uid=source.uid)
            except (AttributeError, IndexError, TypeError, ValueError):
                callback(EDSResult(EDSState.COMMUNICATION_ERROR), user_data)
                return
            callback(
                EDSResult(EDSState.READY, contacts) if contacts else EDSResult(EDSState.EMPTY),
                user_data,
            )

        def on_opened(
            result: EDSResult[GLib.Variant],
            _ignored: object | None,
        ) -> None:
            if result.state is not EDSState.READY:
                callback(EDSResult(result.state), user_data)
                return
            self._registry.call_read_async(
                handle,
                "GetContactList",
                GLib.Variant("(s)", (ALL_CONTACTS_QUERY,)),
                GLib.VariantType.new("(as)"),
                on_contacts,
                cancellable=cancellable,
            )

        self._registry.call_read_async(
            handle,
            "Open",
            None,
            GLib.VariantType.new("(as)"),
            on_opened,
            cancellable=cancellable,
        )

    def read_sources_async(
        self,
        sources: Sequence[tuple[SourceDescriptor, EDSClientHandle]],
        callback: Callable[[EDSResult[ContactRecord], object | None], None],
        *,
        cancellable: Gio.Cancellable | None = None,
        user_data: object | None = None,
    ) -> None:
        """複数の利用者選択済みアドレス帳を集約する。"""
        for source, handle in sources:
            if (
                source.kind is not SourceKind.ADDRESS_BOOK
                or handle.kind is not SourceKind.ADDRESS_BOOK
            ):
                raise ValueError("Only address-book sources may be read")
            if not source.uid:
                raise ValueError("An address-book UID is required")
        if not sources:
            callback(EDSResult(EDSState.UNREGISTERED), user_data)
            return

        pending = deque(sources)
        collected: list[ContactRecord] = []
        active = 0
        failure: EDSState | None = None
        completed = False

        def launch_next() -> None:
            nonlocal active, completed
            while pending and active < _READ_CONCURRENCY and failure is None and not completed:
                source, handle = pending.popleft()
                active += 1
                self.read_contacts_async(
                    source,
                    handle,
                    on_source_read,
                    cancellable=cancellable,
                )
            if active == 0 and not pending and not completed:
                completed = True
                if failure is not None:
                    result = EDSResult(failure)
                else:
                    unique = {
                        (contact.source_uid, contact.contact_uid): contact
                        for contact in collected
                    }
                    contacts = tuple(
                        sorted(
                            unique.values(),
                            key=lambda contact: (
                                contact.full_name.casefold(),
                                contact.source_uid,
                                contact.contact_uid,
                            ),
                        )
                    )
                    result = (
                        EDSResult(EDSState.READY, contacts)
                        if contacts
                        else EDSResult(EDSState.EMPTY)
                    )
                callback(result, user_data)

        def on_source_read(
            result: EDSResult[ContactRecord],
            _ignored: object | None,
        ) -> None:
            nonlocal active, failure
            active -= 1
            if result.state is EDSState.READY:
                collected.extend(result.items)
            elif result.state is not EDSState.EMPTY:
                failure = failure or result.state
                pending.clear()
            launch_next()

        launch_next()
