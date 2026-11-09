"""選択県の自動読込・更新・削除。実通信や現在地取得を使わないfixture試験。"""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.contact_selection import ContactSelectionView
from paperphone.contacts import ContactRecord
from paperphone.location import LocationFix
from paperphone.osm_extracts import ExtractState
from paperphone.i18n import gettext_for
from paperphone.osm_region_view import OsmRegionView, ERRORS
_ = gettext_for()
from paperphone.osm_derived import derived_path
from paperphone.osm_store import ExtractFailure, ExtractUpdate
from paperphone.selection import GenerationSnapshot, SelectionModel


class OsmRegionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    SYNTHETIC_XML = ('<osm version="0.6"><node id="1" lat="35.68" lon="139.76" version="1">'
                     '<tag k="name" v="東京駅"/><tag k="railway" v="station"/></node>'
                     '<node id="2" lat="35.681" lon="139.76" version="1"/>'
                     '<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="footway"/></way></osm>')

    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def make(self, fix=None, load=None, update=None, delete=None):
        store = Mock()
        # 版付きの抽出版には、派生生成が受け取れる実体を仮置きする。
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fixture = Path(directory.name) / "fixture.osm"
        fixture.write_text(self.SYNTHETIC_XML, encoding="utf-8")

        def cached_extract(code):
            return SimpleNamespace(prefecture=code, path=fixture, sha256="e" * 64,
                                   basis=datetime(2026, 1, 2, tzinfo=timezone.utc),
                                   state=ExtractState(bytes_count=132000000))
        store.load.side_effect = load if load is not None else cached_extract
        store.update.side_effect = (lambda code, confirmed=True:
                                    ExtractUpdate(cached_extract(code), True)) if update is None else update
        store.delete.side_effect = delete if delete is not None else (lambda code: None)
        view = OsmRegionView(store, fix=fix, submit=lambda fn: fn(), dispatch=lambda fn: fn())
        self.addCleanup(view.close)
        return view, store

    def test_prefecture_selection_automatically_loads_saved_data(self):
        view, store = self.make(LocationFix(35.682, 139.759, 500))
        # 都道府県の既定選択だけで、保存済み抽出の読込が終わっている。
        store.load.assert_called_once_with(13)
        store.update.assert_not_called()
        self.assertEqual(view.status.get_text(), "")
        self.assertIsNotNone(view.selected_extract)
        # 成功時は説明を加えず、更新ボタンでのみ通信が起こる。
        view.update_button.emit("clicked")
        store.update.assert_called_once_with(13, confirmed=True)
        self.assertIsNotNone(view.selected_extract)

    def test_no_saved_data_and_denial_still_keep_manual_selection(self):
        view, store = self.make()
        self.assertIsNone(view.selected_extract)
        self.assertEqual(view.status.get_text(), "")
        store.load.assert_not_called()
        view.prefecture.set_selected(27)
        self.assertEqual(store.load.call_args_list[-1][0], (27,))
        view.update_button.emit("clicked")
        store.update.assert_called_once_with(27, confirmed=True)
        self.assertIsNotNone(view.selected_extract)

    def test_missing_saved_data_reports_short_reason_and_stays_usable(self):
        view, store = self.make(load=ExtractFailure("missing"))
        view.prefecture.set_selected(27)
        self.assertIsNotNone(store.load.call_args_list)
        self.assertEqual(view.status.get_text(), _(ERRORS["missing"]))

    def test_outside_japan_does_not_pick_arbitrary_prefecture(self):
        view, store = self.make(LocationFix(0, 0))
        self.assertIsNone(view.prefecture_code)
        store.load.assert_not_called()
        store.update.assert_not_called()

    def test_changing_prefecture_clears_previous_selection(self):
        view, store = self.make(LocationFix(35.682, 139.759))
        self.assertIsNotNone(view.selected_extract)
        view.prefecture.set_selected(0)
        self.assertIsNone(view.selected_extract)

    def test_old_async_result_cannot_overwrite_new_selection(self):
        view, store = self.make(LocationFix(35.682, 139.759))
        jobs = []
        view._submit = jobs.append
        view.update_button.emit("clicked")
        self.assertFalse(view.prefecture.get_sensitive())
        view.prefecture.set_selected(27)  # ホスト側から県が変わったケース。
        jobs[0]()
        self.assertEqual(view.prefecture_code, 27)
        self.assertIsNone(view.selected_extract)

    def test_delete_removes_saved_data_and_is_explicit(self):
        view, store = self.make(LocationFix(35.682, 139.759))
        view.delete_button.emit("clicked")
        store.delete.assert_called_once_with(13)
        self.assertIsNone(view.selected_extract)
        self.assertEqual(view.status.get_text(), _("Deleted."))
        self.assertFalse(view.progress.get_visible())

    def test_delete_failure_keeps_existing_data_and_shows_reason(self):
        view, store = self.make(LocationFix(35.682, 139.759), delete=OSError("disk full"))
        view.delete_button.emit("clicked")
        self.assertEqual(view.status.get_text(), _(ERRORS["storage"]))
        # 失敗しても読込抽出は保持しない。次に選択し直せば再読込される。
        store.delete.assert_called_once()

    def test_failure_shows_reason_and_does_not_disable_other_booklet_choices(self):
        for reason in ("denied", "network", "missing", "wait"):
            view, store = self.make(LocationFix(35.682, 139.759), update=ExtractFailure(reason))
            view.update_button.emit("clicked")
            self.assertEqual(view.status.get_text(), _(ERRORS[reason]))
            self.assertIsNone(view.selected_extract)
            self.assertTrue(view.prefecture.get_sensitive())
            contacts = ContactSelectionView(SelectionModel())
            record = ContactRecord("fixture", "id", "Fixture", ("123",))
            contacts.set_contacts([record])
            record_selected = contacts.checkbox_for(record.key)
            record_selected.set_active(True)
            self.assertEqual(contacts.selected_items[0].key, record.key)
            snapshot = GenerationSnapshot.capture(contacts.selected_items,
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), timezone_id="UTC")
            self.assertEqual(len(snapshot.items), 1)
            self.assertNotEqual(snapshot.items[0].category, "map")

    def test_store_delete_removes_only_declared_pbf_file(self):
        from paperphone.osm_store import ExtractStore
        with __import__("tempfile").TemporaryDirectory() as directory:
            store = ExtractStore(directory)
            self.assertRaises(ExtractFailure, store.delete, 48)
            digest = "a" * 64
            data = Path(directory) / f"JP-13-{digest}.osm.pbf"
            data.write_bytes(b"x")
            outside = Path(directory) / "JP-13-evil.pbf"
            outside.write_bytes(b"y")
            manifest = Path(directory) / "JP-13.json"
            manifest.write_text('{"sha256": "%s", "prefecture": 13}' % digest, encoding="utf-8")
            store.delete(13)
            self.assertFalse(data.is_file())
            self.assertFalse(manifest.is_file())
            self.assertTrue(outside.is_file())  # manifestが参照しないファイルは消さない。



class OsmRegionDerivedTests(unittest.TestCase):
    """抽出の読込・更新直後に派生データが作られ、失敗では冊子を妨げない。"""

    SYNTHETIC_XML = ('<osm version="0.6"><node id="1" lat="35.68" lon="139.76" version="1">'
                     '<tag k="name" v="東京駅"/><tag k="railway" v="station"/></node>'
                     '<node id="2" lat="35.681" lon="139.76" version="1"/>'
                     '<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="footway"/></way></osm>')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Mock()

    def extract(self, valid=True, sha="e" * 64):
        path = self.root / f"JP-13-{sha}.osm"
        path.write_bytes(self.SYNTHETIC_XML.encode("utf-8") if valid else b"this is not osm data")
        return SimpleNamespace(prefecture=13, path=path, sha256=sha, basis=SimpleNamespace())

    def run_view(self, store):
        jobs = []
        view = OsmRegionView(store, submit=jobs.append, dispatch=lambda fn: fn())
        self.addCleanup(view.close)
        # ワーカーへの差替えは即時実行していないため、積みを順に流してから
        # 以降の明示操作は直行させる。
        view.prefecture.set_selected(13)
        while jobs:
            jobs.pop(0)()
        view._submit = lambda fn: fn()
        return view

    def test_loaded_extract_builds_its_derived_data_before_selection(self):
        extract = self.extract()
        self.store.load.return_value = extract
        view = self.run_view(self.store)
        self.assertIs(view.selected_extract, extract)
        self.assertTrue(derived_path(13, "e" * 64, directory=self.root).is_file())

    def test_updated_extract_regenerates_a_changed_version(self):
        first = self.extract()
        self.store.load.return_value = first
        self.store.update.return_value = ExtractUpdate(first, True)
        view = self.run_view(self.store)
        self.assertTrue(derived_path(13, "e" * 64, directory=self.root).is_file())
        second = self.extract(sha="f" * 64)
        self.store.update.return_value = ExtractUpdate(second, True)
        view._run(True)
        self.assertTrue(derived_path(13, "f" * 64, directory=self.root).is_file())
        self.assertFalse(derived_path(13, "e" * 64, directory=self.root).is_file())
        self.assertIs(view.selected_extract, second)

    def test_generation_failure_shows_reason_and_keeps_the_page_usable(self):
        extract = self.extract(valid=False)
        self.store.load.return_value = extract
        view = self.run_view(self.store)
        self.assertIsNone(view.selected_extract)
        self.assertEqual(view.status.get_text(), _(ERRORS["derived"]))
        self.assertFalse(derived_path(13, "e" * 64, directory=self.root).exists())
        view.prefecture.set_selected(1)  # 失敗後に他県へ移動できる。

    def test_download_failure_is_reported_as_before(self):
        self.store.load.side_effect = ExtractFailure("cache")
        view = self.run_view(self.store)
        self.assertIsNone(view.selected_extract)
        self.assertEqual(view.status.get_text(), _(ERRORS["cache"]))


if __name__ == "__main__":
    unittest.main()
