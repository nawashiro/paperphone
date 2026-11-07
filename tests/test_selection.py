"""選択保持・更新時除外・不変スナップショットのテスト。"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest

from paperphone.selection import (
    GenerationSnapshot,
    ItemKey,
    SelectableItem,
    SelectionModel,
)


class SelectionModelTest(unittest.TestCase):
    def test_category_switch_preserves_selected_items(self):
        today_key = ItemKey("source-a", "item-1")
        future_key = ItemKey("source-b", "item-2")
        items = (
            SelectableItem(today_key, "today", {"title": "fixture today"}),
            SelectableItem(future_key, "upcoming", {"title": "fixture upcoming"}),
        )
        selection = SelectionModel()
        selection.set_selected(today_key, True)
        selection.set_selected(future_key, True)

        self.assertEqual(selection.selected_in_category(items, "today"), (items[0],))
        self.assertEqual(selection.selected_in_category(items, "upcoming"), (items[1],))
        self.assertEqual(selection.selected_keys, (today_key, future_key))

    def test_reconcile_excludes_completed_and_deleted_items_and_returns_notice_keys(self):
        retained = ItemKey("source-a", "active")
        completed = ItemKey("source-a", "completed")
        deleted = ItemKey("source-b", "deleted")
        selection = SelectionModel()
        for key in (retained, completed, deleted):
            selection.set_selected(key, True)

        removed = selection.reconcile(
            (
                SelectableItem(retained, "today", {"title": "active"}),
                SelectableItem(completed, "today", {"title": "completed"}, completed=True),
            )
        )

        self.assertEqual(removed, (completed, deleted))
        self.assertEqual(selection.selected_keys, (retained,))

    def test_occurrences_with_different_instance_ids_have_distinct_keys(self):
        first = ItemKey("calendar", "recurring-event", "2024-05-10T09:00")
        second = ItemKey("calendar", "recurring-event", "2024-05-11T09:00")
        selection = SelectionModel()

        selection.set_selected(first, True)
        selection.set_selected(second, True)

        self.assertEqual(selection.selected_keys, (first, second))
        self.assertNotEqual(first, second)

    def test_generation_snapshot_copies_fields_and_is_immutable(self):
        key = ItemKey("source", "item")
        fields = {"title": "before refresh", "time": "09:30"}
        item = SelectableItem(key, "today", fields)
        created_at = datetime(2024, 5, 10, 8, tzinfo=timezone.utc)

        snapshot = GenerationSnapshot.capture(
            (item,),
            created_at=created_at,
            timezone_id="Asia/Tokyo",
        )
        fields["title"] = "after refresh"

        self.assertEqual(snapshot.items[0].fields, (("time", "09:30"), ("title", "before refresh")))
        self.assertEqual(snapshot.items[0].fields[1][1], "before refresh")
        with self.assertRaises(FrozenInstanceError):
            snapshot.timezone_id = "UTC"
        with self.assertRaises(TypeError):
            snapshot.items[0].fields[0] = ("time", "changed")

    def test_completed_items_are_not_captured_in_generation_snapshot(self):
        active = SelectableItem(ItemKey("s", "active"), "today", {"title": "active"})
        completed = SelectableItem(
            ItemKey("s", "done"), "today", {"title": "done"}, completed=True
        )

        snapshot = GenerationSnapshot.capture(
            (active, completed),
            created_at=datetime(2024, 5, 10, tzinfo=timezone.utc),
            timezone_id="UTC",
        )

        self.assertEqual(tuple(item.key for item in snapshot.items), (active.key,))

    def test_selection_representations_do_not_expose_personal_fields_or_source_uids(self):
        key = ItemKey("private-source-uid", "private-contact-uid")
        item = SelectableItem(
            key,
            "contacts",
            {"name": "Private Fixture Name", "phone": "+1-555-0104"},
        )

        snapshot = GenerationSnapshot.capture(
            (item,),
            created_at=datetime(2025, 3, 10, tzinfo=timezone.utc),
            timezone_id="UTC",
        )
        for private_value in (
            "private-source-uid",
            "private-contact-uid",
            "Private Fixture Name",
            "+1-555-0104",
        ):
            self.assertNotIn(private_value, repr(key))
            self.assertNotIn(private_value, repr(item))
            self.assertNotIn(private_value, repr(snapshot))

    def test_snapshot_requires_aware_time_and_timezone_id(self):
        item = SelectableItem(ItemKey("s", "i"), "today", {})

        with self.assertRaises(ValueError):
            GenerationSnapshot.capture(
                (item,),
                created_at=datetime(2024, 5, 10),
                timezone_id="UTC",
            )
        with self.assertRaises(ValueError):
            GenerationSnapshot.capture(
                (item,),
                created_at=datetime(2024, 5, 10, tzinfo=timezone.utc),
                timezone_id="",
            )


if __name__ == "__main__":
    unittest.main()
