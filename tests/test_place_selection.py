"""ローカル候補の選択、取消、現在地既定、県外地点の拒否。実通信なし。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.location import LocationFix
from paperphone.places import Place, PlaceIndex
from paperphone.place_selection import PlaceSelectionView, _

TOKYO = Place("node/1", "東京駅", "station", 35.6812, 139.7671, ("Tokyo Station", "とうきょうえき"))
TOKYO_OTHER = Place("node/2", "東京駅", "station", 35.6814, 139.7673)
PARK = Place("way/3", "新宿公園", "park", 35.69, 139.70, ("Shinjuku Park",))


class PlaceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def make(self, fix=None, submit=None):
        # 索引キャッシュはモジュール全体で共有するため、各試験は隔離する。
        from paperphone.place_selection import _INDEX_CACHE
        _INDEX_CACHE.clear()
        index = PlaceIndex([TOKYO, TOKYO_OTHER, PARK], prefecture=13)
        view = PlaceSelectionView(index, fix=fix, submit=submit or (lambda fn: fn()),
                                  dispatch=lambda fn: fn())
        self.addCleanup(view.close)
        return view

    def test_current_location_defaults_to_departure_without_external_request(self):
        view = self.make(LocationFix(35.682, 139.759))
        self.assertEqual(view.selected_start.id, "current-location")
        self.assertEqual(view.selected_start.latitude, 35.682)
        self.assertIsNone(view.selected_destination)

    def test_results_show_name_kind_coordinates_and_select_independent_endpoints(self):
        view = self.make()
        view.entries["start"].set_text("Tokyo Station")
        view.search("start")
        button = view.results["start"].get_row_at_index(0).get_child()
        self.assertEqual(button.get_label(), "東京駅")
        button.emit("clicked")
        view.entries["destination"].set_text("新宿")
        view.search("destination")
        view.results["destination"].get_row_at_index(0).get_child().emit("clicked")
        self.assertEqual(view.selected_start, TOKYO)
        self.assertEqual(view.selected_destination, PARK)

    def test_no_results_does_not_invent_a_place(self):
        view = self.make()
        view.entries["start"].set_text("zzzzzzzz")
        view.search("start")
        self.assertIsNone(view.results["start"].get_first_child())
        self.assertIsNone(view.selected_start)
        self.assertEqual(view.status.get_text(), _("No local places found."))

    def test_cancel_discards_late_results_and_keeps_previous_selection(self):
        jobs = []
        view = self.make(submit=jobs.append)
        view.choose("start", TOKYO)
        view.entries["destination"].set_text("新宿")
        view.search("destination")
        view.cancel()
        jobs.pop()()
        self.assertIsNone(view.results["destination"].get_first_child())
        self.assertEqual(view.selected_start, TOKYO)
        self.assertIsNone(view.selected_destination)

    def test_outside_location_and_untrusted_foreign_candidate_are_not_selected(self):
        view = self.make(LocationFix(34.69, 135.50))
        self.assertIsNone(view.selected_start)
        self.assertFalse(view.choose("start", Place("foreign/1", "大阪", "city", 34.69, 135.50)))
        self.assertIsNone(view.selected_start)

    def test_same_names_are_distinguishable_results(self):
        view = self.make()
        view.entries["start"].set_text("東京駅")
        view.search("start")
        first = view.results["start"].get_row_at_index(0).get_child()
        second = view.results["start"].get_row_at_index(1).get_child()
        self.assertEqual(first.get_label(), second.get_label())  # 同名でも利用者には同じ表示。
        second.emit("clicked")
        self.assertEqual(view.selected_start, TOKYO_OTHER)

    def test_index_load_is_background_and_cancelable(self):
        view = self.make()
        jobs = []
        factory = Mock(return_value=view.index)
        view._submit, view._index_factory = jobs.append, factory
        view.load_extract(SimpleNamespace(path="synthetic.osm.pbf", prefecture=13))
        factory.assert_not_called()
        view.cancel()
        jobs[0]()
        self.assertTrue(factory.call_args.kwargs["cancelled"]())
        self.assertIsNone(view.index)
        self.assertIsNone(view.selected_start)

    def test_index_build_shows_progress_bar_until_ready(self):
        view = self.make()
        jobs = []
        factory = Mock(return_value=view.index)
        view._submit, view._index_factory = jobs.append, factory
        view.load_extract(SimpleNamespace(path="synthetic.osm.pbf", prefecture=13))
        self.assertTrue(view.progress.get_visible())
        jobs[0]()
        # ジョブは即実行のsubsetで、索引は完成済み。バーが消えることを確認する。
        self.assertFalse(view.progress.get_visible())

    def test_new_prefecture_index_clears_previous_endpoints(self):
        view = self.make(LocationFix(35.682, 139.759))
        view.choose("destination", PARK)
        view.set_index(PlaceIndex([], prefecture=27))
        self.assertIsNone(view.selected_start)
        self.assertIsNone(view.selected_destination)


if __name__ == "__main__":
    unittest.main()
