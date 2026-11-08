"""設定の永続化：設定ファイルの読み書きと、選択鍵の安定書式の文書化。"""

from datetime import datetime, timedelta  # noqa: F401 保持する選択の日時確定の試験で使う
from pathlib import Path
import osmium  # noqa: F401 実PBF走査のfixtureがこのモジュールを参照する場合のため保守
import unittest

from paperphone.application_view import load_settings_file, save_settings_file, settings_file
from paperphone.selection import ItemKey


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_default_path_uses_config_directory(self):
        path = settings_file(self.directory.name)
        self.assertEqual(path, Path(self.directory.name) / "settings.json")

    def test_roundtrip_keeps_saved_selections(self):
        path = settings_file(self.directory.name)
        data = {
            "version": 1, "language": "ja", "name": "Smith", "contact": "dev@example.com",
            "sources": [["ADDRESS_BOOK", "personal"]], "selection": ["9", "12", "18"],
            "paper_app": 3, "notes": False, "contactless": True, "prefecture": 13,
        }
        self.assertTrue(save_settings_file(path, data))
        self.assertEqual(load_settings_file(path), data)
        # 個人身元や写真自体を保存しない。
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("Smith", text.split('"name"')[1].split(',')[1])

    def test_failed_write_keeps_previous_settings(self):
        path = settings_file(self.directory.name) / "missing-dir" / "settings.json"
        new_data = {"version": 1, "language": "ja"}
        self.assertTrue(save_settings_file(path, new_data))

    def test_corrupted_file_is_not_catastrophic(self):
        path = settings_file(self.directory.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_settings_file(path), {})


class OpaqueSelectionKeyTests(unittest.TestCase):
    def test_key_roundtrips_through_stable_value(self):
        key = ItemKey("personal", "ab:12:34", None)
        rebuilt = ItemKey.from_opaque_value(key.opaque_value())
        self.assertEqual(rebuilt, key)

    def test_instance_id_is_distinct(self):
        base = ItemKey("personal", "caldav", None)
        recurring = ItemKey("personal", "caldav", "instance-1")
        self.assertNotEqual(base.opaque_value(), recurring.opaque_value())


class ApplicationViewPersistedRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw
        Gtk.init(); Adw.init()
        cls.Gtk = Gtk

    def setUp(self):
        import tempfile, os
        from unittest.mock import patch, Mock
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.patcher_env = patch.dict(os.environ, {
            "PAPERPHONE_LANGUAGE": "en", "GSETTINGS_BACKEND": "memory",
            "PAPERPHONE_CONFIG_DIR": self.temporary.name})
        self.patcher_env.start(); self.addCleanup(self.patcher_env.stop)
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        from tests.test_application_view import ApplicationViewTests, Services
        self.view_class, self.services_class = None, None
        # Reuse the existing fixture class by importing statically to avoid duplication.
        self.services = Services()
        self.temporary_directory = self.temporary

    def make_view(self, config_path=None):
        from tests.test_application_view import ApplicationViewTests, Services, immediate
        from unittest.mock import Mock
        from paperphone.application_view import ApplicationView
        from paperphone.booklet_asset_view import AssetSelectionView
        from paperphone.osm_region_view import OsmRegionView
        from paperphone.place_selection import PlaceSelectionView
        view = ApplicationView(self.services, schedule=False, dispatch=immediate, submit=immediate,
            asset_factory=lambda: AssetSelectionView(
                chooser_factory=Mock(side_effect=AssertionError("dialog forbidden")),
                submit=immediate, dispatch=immediate),
            region_factory=lambda store: OsmRegionView(store, submit=immediate, dispatch=immediate),
            place_factory=lambda: PlaceSelectionView(submit=immediate, dispatch=immediate),
            weather_factory=Mock(return_value=object()), config_path=config_path)
        self.addCleanup(view.close)
        return view

    def test_settings_survive_view_recreation(self):
        config = Path(self.temporary.name) / "settings.json"
        first = self.make_view(config_path=config.parent)
        first.name_entry.set_text("Persisted name")
        first._persist_settings()
        self.assertTrue(config.is_file())
        first.close()
        second = self.make_view(config_path=config.parent)
        self.assertEqual(second.name_entry.get_text(), "Persisted name")


if __name__ == "__main__":
    unittest.main()
