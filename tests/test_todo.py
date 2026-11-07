"""TODO分類・期限切れの表形式テスト。"""

from datetime import date, datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from paperphone.temporal import DateOnly, FloatingDateTime, ZonedDateTime
from paperphone.todo import (
    TodoBucket,
    TodoInput,
    classify_todo,
    classify_todos,
    next_todo_reassessment,
)


UTC = timezone.utc
NOW = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)


class TodoClassificationTest(unittest.TestCase):
    def test_category_and_overdue_decision_table(self):
        cases = (
            ("startなし_dueなし", None, None, TodoBucket.UNDATED, False),
            ("将来dueのみ", None, ZonedDateTime(datetime(2024, 5, 12, 12, tzinfo=UTC)), TodoBucket.TODAY, False),
            ("過去dueのみ", None, ZonedDateTime(datetime(2024, 5, 9, 12, tzinfo=UTC)), TodoBucket.TODAY, True),
            ("将来start", ZonedDateTime(datetime(2024, 5, 11, 9, tzinfo=UTC)), None, TodoBucket.UPCOMING, False),
            ("startがnow", ZonedDateTime(NOW), None, TodoBucket.TODAY, False),
            ("startが今日の日付", DateOnly(date(2024, 5, 10)), None, TodoBucket.TODAY, False),
            (
                "start済みで期限切れ",
                ZonedDateTime(datetime(2024, 5, 9, 9, tzinfo=UTC)),
                ZonedDateTime(datetime(2024, 5, 9, 10, tzinfo=UTC)),
                TodoBucket.TODAY,
                True,
            ),
            (
                "日付期限は当日中有効",
                None,
                DateOnly(date(2024, 5, 10)),
                TodoBucket.TODAY,
                False,
            ),
            (
                "日付期限は翌日から期限切れ",
                None,
                DateOnly(date(2024, 5, 9)),
                TodoBucket.TODAY,
                True,
            ),
        )
        for label, start, due, expected_bucket, expected_overdue in cases:
            with self.subTest(label=label):
                result = classify_todo(start=start, due=due, now=NOW, system_tz=UTC)
                self.assertEqual(result.bucket, expected_bucket)
                self.assertEqual(result.overdue, expected_overdue)

    def test_date_only_due_remains_valid_through_its_local_day(self):
        zone = ZoneInfo("Asia/Kathmandu")
        due = DateOnly(date(2024, 5, 10))
        just_before_midnight = datetime(2024, 5, 10, 23, 59, 59, tzinfo=zone)
        next_midnight = datetime(2024, 5, 11, 0, 0, tzinfo=zone)

        self.assertFalse(classify_todo(start=None, due=due, now=just_before_midnight, system_tz=zone).overdue)
        self.assertTrue(classify_todo(start=None, due=due, now=next_midnight, system_tz=zone).overdue)

    def test_floating_start_is_interpreted_in_system_timezone(self):
        zone = ZoneInfo("Asia/Tokyo")
        start = FloatingDateTime(datetime(2024, 5, 10, 21, 0))
        before = datetime(2024, 5, 10, 11, 59, tzinfo=UTC)
        at_start = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)

        self.assertEqual(classify_todo(start=start, due=None, now=before, system_tz=zone).bucket, TodoBucket.UPCOMING)
        self.assertEqual(classify_todo(start=start, due=None, now=at_start, system_tz=zone).bucket, TodoBucket.TODAY)

    def test_future_start_takes_priority_even_when_due_is_overdue(self):
        start = ZonedDateTime(datetime(2024, 5, 11, 9, tzinfo=UTC))
        due = ZonedDateTime(datetime(2024, 5, 9, 9, tzinfo=UTC))

        result = classify_todo(start=start, due=due, now=NOW, system_tz=UTC)

        self.assertEqual(result.bucket, TodoBucket.UPCOMING)
        self.assertTrue(result.overdue)
        self.assertTrue(result.due_before_start)

    def test_equal_timed_due_is_not_overdue_until_time_has_passed(self):
        due = ZonedDateTime(NOW)

        at_due = classify_todo(start=None, due=due, now=NOW, system_tz=UTC)
        after_due = classify_todo(start=None, due=due, now=NOW + timedelta(microseconds=1), system_tz=UTC)

        self.assertFalse(at_due.overdue)
        self.assertTrue(after_due.overdue)

    def test_completed_state_or_completion_time_excludes_todo(self):
        due = ZonedDateTime(datetime(2024, 5, 1, 12, tzinfo=UTC))
        completion_time = ZonedDateTime(datetime(2024, 5, 9, 10, tzinfo=UTC))

        completed_by_state = classify_todo(start=None, due=due, completed=True, now=NOW, system_tz=UTC)
        completed_by_time = classify_todo(start=None, due=due, completed_at=completion_time, now=NOW, system_tz=UTC)

        self.assertIsNone(completed_by_state.bucket)
        self.assertIsNone(completed_by_time.bucket)
        self.assertFalse(completed_by_state.overdue)
        self.assertFalse(completed_by_time.overdue)

    def test_due_before_start_is_reported_without_changing_start_priority(self):
        start = ZonedDateTime(datetime(2024, 5, 11, 9, tzinfo=UTC))
        due = DateOnly(date(2024, 5, 9))

        result = classify_todo(start=start, due=due, now=NOW, system_tz=UTC)

        self.assertEqual(result.bucket, TodoBucket.UPCOMING)
        self.assertTrue(result.overdue)
        self.assertTrue(result.due_before_start)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_todo(start=None, due=None, now=datetime(2024, 5, 10, 12))

    def test_start_boundary_schedules_and_reclassifies_todo(self):
        start = ZonedDateTime(datetime(2024, 5, 10, 9, 0, tzinfo=UTC))
        item = TodoInput(start=start)
        before = datetime(2024, 5, 10, 8, 59, 59, tzinfo=UTC)

        self.assertEqual(classify_todos((item,), now=before, system_tz=UTC)[0].bucket, TodoBucket.UPCOMING)
        self.assertEqual(next_todo_reassessment((item,), now=before, system_tz=UTC), start.value)
        self.assertEqual(classify_todos((item,), now=start.value, system_tz=UTC)[0].bucket, TodoBucket.TODAY)

    def test_local_midnight_schedules_date_only_due_reassessment(self):
        zone = ZoneInfo("Asia/Tokyo")
        due = DateOnly(date(2024, 5, 10))
        item = TodoInput(due=due)
        before_midnight = datetime(2024, 5, 10, 23, 59, 59, tzinfo=zone)
        midnight = datetime(2024, 5, 11, 0, 0, tzinfo=zone)

        self.assertFalse(classify_todos((item,), now=before_midnight, system_tz=zone)[0].overdue)
        self.assertEqual(next_todo_reassessment((item,), now=before_midnight, system_tz=zone), midnight)
        self.assertTrue(classify_todos((item,), now=midnight, system_tz=zone)[0].overdue)

    def test_reclassification_uses_new_system_timezone_without_stale_cache(self):
        item = TodoInput(start=FloatingDateTime(datetime(2024, 5, 10, 9, 0)))
        now = datetime(2024, 5, 10, 0, 30, tzinfo=UTC)

        utc_result = classify_todos((item,), now=now, system_tz=ZoneInfo("UTC"))[0]
        tokyo_result = classify_todos((item,), now=now, system_tz=ZoneInfo("Asia/Tokyo"))[0]

        self.assertEqual(utc_result.bucket, TodoBucket.UPCOMING)
        self.assertEqual(tokyo_result.bucket, TodoBucket.TODAY)

    def test_completed_items_do_not_schedule_start_or_due_boundaries(self):
        item = TodoInput(
            start=ZonedDateTime(datetime(2024, 5, 10, 9, tzinfo=UTC)),
            completed=True,
        )
        now = datetime(2024, 5, 10, 8, tzinfo=UTC)

        self.assertEqual(
            next_todo_reassessment((item,), now=now, system_tz=UTC),
            datetime(2024, 5, 11, 0, tzinfo=UTC),
        )


if __name__ == "__main__":
    unittest.main()
