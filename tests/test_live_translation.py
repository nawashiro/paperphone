"""表示言語の変更で個人データ・選択・実行中の版を変えない。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
import os
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.booklet_asset_view import AssetSelectionView
from paperphone.i18n import gettext_for, language_code, set_language, using_language
from paperphone.location import LocationFix
from paperphone.osm_region_view import OsmRegionView
from paperphone.place_selection import PlaceSelectionView
from paperphone.places import Place, PlaceIndex
from paperphone.selection import SelectionModel
from paperphone.todo import TodoBucket
from paperphone.todo_reader import TodoRecord
from paperphone.todo_selection import TodoSelectionView
from paperphone.weather_location import WeatherLocationController


class LiveTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        scope = patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "en"})
        scope.start()
        self.addCleanup(scope.stop)

    def test_weather_description_translates_in_the_japanese_catalog(self):
        from paperphone.weather_sheet import weather_lines, build_weather_sheet
        import tests.test_weather_sheet as sample

        with using_language("ja"):
            sheet = build_weather_sheet(sample.result_at(symbol="lightrain"), now=sample.NOW)
            line = weather_lines(sheet)[0]
            self.assertIn("小雨", line)
            self.assertNotIn("lightrain", line)

    def test_dynamic_display_and_context_local_generation_languages_are_independent(self):
        live = gettext_for()
        fixed = gettext_for("en")
        set_language("ja")
        self.assertEqual(live("Departure"), "出発地")
        self.assertEqual(fixed("Departure"), "Departure")
        entered, release = Event(), Event()
        def generate():
            with using_language("en"):
                entered.set()
                self.assertTrue(release.wait(5))
                return live("Departure"), language_code()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(generate)
            self.assertTrue(entered.wait(5))
            self.assertEqual(language_code(), "ja")
            release.set()
            self.assertEqual(future.result(timeout=5), ("Departure", "en"))
        self.assertEqual(live("Departure"), "出発地")

    def test_region_retranslation_keeps_selected_extract_and_busy_generation_token(self):
        jobs = []
        view = OsmRegionView(Mock(), submit=jobs.append, dispatch=lambda fn: fn())
        self.addCleanup(view.close)
        view.prefecture.set_selected(13)
        saved = object()
        view.selected_extract = saved
        token = view._generation
        set_language("ja")
        view.retranslate()
        self.assertIs(view.selected_extract, saved)
        self.assertEqual(view._generation, token)
        self.assertEqual(view.prefecture_code, 13)
        self.assertIn("東京", view.prefecture.get_model().get_string(13))
        view._run(False)
        token = view._generation
        set_language("en")
        view.retranslate()
        self.assertEqual(view._generation, token)
        self.assertEqual(view.prefecture_code, 13)
        self.assertFalse(view.prefecture.get_sensitive())
        self.assertEqual(len(jobs), 1)

    def test_asset_retranslation_retains_photo_bytes_and_app_id(self):
        view = AssetSelectionView()
        self.addCleanup(view.close)
        view._photo = b"synthetic captured bytes"
        view.paper_app_dropdown.set_selected(4)
        before = view.capture_choices()
        set_language("ja")
        view.retranslate()
        self.assertEqual(view.capture_choices(), before)
        self.assertEqual(view.paper_app_dropdown.get_model().get_string(0), "なし")
        view.clear_photo()
        self.assertEqual(view.photo_state, "none")

    def test_todo_titles_translate_but_personal_summary_matching_msgid_does_not(self):
        view = TodoSelectionView(SelectionModel())
        record = TodoRecord("synthetic", "id", "Today", None, None)
        view.set_tasks([record], now=datetime(2026, 1, 2, tzinfo=timezone.utc), system_tz=timezone.utc)
        view.checkbox_for(record.key).set_active(True)
        view.set_visible_bucket(TodoBucket.UNDATED)
        set_language("ja")
        view.retranslate()
        self.assertEqual(view.checkbox_for(record.key).get_label(), "Today")
        self.assertEqual(len(view.selected_items), 1)
        self.assertEqual(view.visible_bucket, TodoBucket.UNDATED)
        page = view._stack.get_page(view._stack.get_child_by_name("today"))
        self.assertEqual(page.get_title(), "今日")

    def test_new_current_location_does_not_overwrite_explicit_departure(self):
        manual = Place("node/1", "Today", "place", 35.6812, 139.7671)
        view = PlaceSelectionView(PlaceIndex([manual], prefecture=13))
        self.addCleanup(view.close)
        view.set_current_location(LocationFix(35.682, 139.759))
        self.assertEqual(view.selected_start.id, "current-location")
        view.choose("start", manual)
        view.set_current_location(LocationFix(34.69, 135.50))
        self.assertEqual(view.selected_start, manual)

    def test_due_exactly_now_is_reassessed_just_after_not_next_day(self):
        from paperphone.temporal import ZonedDateTime
        from paperphone.todo import TodoInput, classify_todo, next_todo_reassessment
        now = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
        due = ZonedDateTime(now)
        item = TodoInput(due=due)
        self.assertFalse(classify_todo(start=None, due=due, now=now, system_tz=timezone.utc).overdue)
        deadline = next_todo_reassessment([item], now=now, system_tz=timezone.utc)
        self.assertGreater(deadline, now)
        self.assertLess((deadline - now).total_seconds(), 1)
        self.assertTrue(classify_todo(start=None, due=due, now=deadline, system_tz=timezone.utc).overdue)

    def test_weather_fix_is_memory_only_and_close_prevents_queued_request(self):
        jobs = []
        client = Mock()
        portal = Mock()
        fix = LocationFix(35.682, 139.759, 10)
        portal.request_once.return_value = fix
        controller = WeatherLocationController(client, submit=jobs.append,
            dispatch=lambda fn: fn(), portal_factory=lambda **kw: portal)
        controller.start("synthetic-parent")
        self.assertIs(controller.fix, fix)
        controller.close()
        jobs[0]()
        self.assertIsNone(controller.fix)
        client.fetch.assert_not_called()
        controller.start("synthetic-parent")
        portal.request_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
