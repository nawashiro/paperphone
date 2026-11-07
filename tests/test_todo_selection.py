"""VTODO分類タブとカテゴリ横断選択のGTK操作fixture。"""

from datetime import datetime, timezone
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.selection import SelectionModel
from paperphone.temporal import ZonedDateTime
from paperphone.todo import TodoBucket
from paperphone.todo_reader import TodoRecord
from paperphone.todo_selection import TodoSelectionView


UTC = timezone.utc
NOW = datetime(2025, 3, 10, 12, tzinfo=UTC)


class TodoSelectionViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        self.today = TodoRecord(
            "task-list-a",
            "today-task",
            "Fixture overdue today",
            ZonedDateTime(datetime(2025, 3, 10, 9, tzinfo=UTC)),
            ZonedDateTime(datetime(2025, 3, 10, 11, tzinfo=UTC)),
        )
        self.upcoming = TodoRecord(
            "task-list-a",
            "upcoming-task",
            "Fixture future task",
            ZonedDateTime(datetime(2025, 3, 11, 9, tzinfo=UTC)),
            ZonedDateTime(datetime(2025, 3, 9, 11, tzinfo=UTC)),
        )
        self.undated = TodoRecord(
            "task-list-b",
            "undated-task",
            "Fixture task without dates",
            None,
            None,
        )
        self.completed = TodoRecord(
            "task-list-b",
            "completed-task",
            "Fixture completed task",
            None,
            None,
            completed=True,
        )
        self.selection = SelectionModel()
        self.view = TodoSelectionView(self.selection)
        self.view.set_tasks(
            (self.today, self.upcoming, self.undated, self.completed),
            now=NOW,
            system_tz=UTC,
        )

    def test_only_three_buckets_and_only_incomplete_tasks_are_displayed(self):
        self.assertEqual(self.view._stack.get_pages().get_n_items(), 3)
        self.assertEqual(self.view.row_count(TodoBucket.TODAY), 1)
        self.assertEqual(self.view.row_count(TodoBucket.UPCOMING), 1)
        self.assertEqual(self.view.row_count(TodoBucket.UNDATED), 1)
        self.assertIsNone(self.view.checkbox_for(self.completed.key))

    def test_overdue_is_a_small_badge_not_an_independent_category(self):
        badge = self.view.overdue_badge_for(self.today.key)

        self.assertTrue(self.view.is_overdue(self.today.key))
        self.assertIsNotNone(badge)
        self.assertIn("caption", badge.get_css_classes())
        self.assertTrue(self.view.is_overdue(self.upcoming.key))
        self.assertIsNone(self.view.overdue_badge_for(self.undated.key))
        self.assertEqual(self.view.row_count(TodoBucket.TODAY), 1)

    def test_switching_categories_preserves_selection_and_snapshot_items(self):
        self.view.checkbox_for(self.today.key).set_active(True)
        self.view.set_visible_bucket(TodoBucket.UPCOMING)
        self.view.checkbox_for(self.upcoming.key).set_active(True)
        self.view.set_visible_bucket(TodoBucket.UNDATED)
        self.view.checkbox_for(self.undated.key).set_active(True)

        self.assertEqual(self.view.visible_bucket, TodoBucket.UNDATED)
        self.assertEqual(
            set(item.key for item in self.view.selected_items),
            {self.today.key, self.upcoming.key, self.undated.key},
        )
        self.assertEqual(
            {item.category for item in self.view.selected_items},
            {TodoBucket.TODAY.value, TodoBucket.UPCOMING.value, TodoBucket.UNDATED.value},
        )
        self.view.set_visible_bucket(TodoBucket.TODAY)
        self.assertTrue(self.selection.is_selected(self.today.key))
        self.assertTrue(self.selection.is_selected(self.upcoming.key))

    def test_completed_after_refresh_is_not_displayed_or_returned_for_print(self):
        self.view.checkbox_for(self.today.key).set_active(True)
        completed_today = TodoRecord(
            self.today.source_uid,
            self.today.todo_uid,
            self.today.summary,
            self.today.start,
            self.today.due,
            completed=True,
        )

        self.view.set_tasks((completed_today,), now=NOW, system_tz=UTC)

        self.assertEqual(self.view.row_count(TodoBucket.TODAY), 0)
        self.assertEqual(self.view.selected_items, ())
        self.assertTrue(self.selection.is_selected(self.today.key))


if __name__ == "__main__":
    unittest.main()
