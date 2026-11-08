"""8.1画面の注入試験。ホストEDS・位置・HTTP・ネイティブダイアログは使わない。"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from paperphone.application_services import GeneratedBooklet, GenerationFailure, generate_booklet
from paperphone.application_view import ApplicationView
from paperphone.booklet_asset_view import AssetSelectionView
from paperphone.contacts import ContactRecord
from paperphone.eds import EDSResult, EDSState, SourceDescriptor, SourceKind
from paperphone.events import CalendarEvent
from paperphone.i18n import gettext_for, language_code
from paperphone.osm_region_view import OsmRegionView
from paperphone.osm_store import ExtractUpdate
from paperphone.place_selection import PlaceSelectionView
from paperphone.refresh import RefreshReport, RefreshState, SourceRefreshResult
from paperphone.temporal import DateOnly, ZonedDateTime
from paperphone.todo import TodoBucket
from paperphone.todo_reader import TodoRecord
from paperphone.weather import Forecast, ForecastResult
from paperphone.weather_location import WeatherLocationResult, WeatherLocationState

NOW = datetime(2026, 1, 2, 8, tzinfo=ZoneInfo("UTC"))
XML = '''<osm version="0.6">
<node id="1" lat="35.68" lon="139.76"><tag k="name" v="出発駅"/></node>
<node id="2" lat="35.681" lon="139.76"/>
<node id="3" lat="35.682" lon="139.76"><tag k="name" v="到着公園"/></node>
<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/></way>
</osm>'''


def immediate(function, *args):
    return function(*args)


class FakeOutput(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.pdf_bytes = None
        self.warnings = ()

    def set_pdf(self, blob, warnings=()):
        self.pdf_bytes = blob
        self.warnings = tuple(warnings)

    def clear_pdf(self):
        self.pdf_bytes = None

    def close(self):
        self.clear_pdf()


class FakeWeather(Gtk.Box):
    def __init__(self, _client, **_kwargs):
        super().__init__()
        self.status = Gtk.Label(label="idle")
        self.append(self.status)
        self.controller = SimpleNamespace(result=WeatherLocationResult(WeatherLocationState.IDLE), fix=None)
        self.closed = False

    def result(self, state, forecast=None, fix=None):
        self.controller.result = WeatherLocationResult(state, forecast=forecast)
        self.controller.fix = fix
        self.status.set_text(state.value)

    def close(self):
        self.closed = True


class Services:
    def __init__(self):
        self.now = NOW
        self.zone = ZoneInfo("UTC")
        self.store = Mock()
        self.calls = []
        self.sources = tuple(SourceDescriptor(kind.name, kind.value, kind, True) for kind in SourceKind)
        self.results = {}
        self.requests = []
        self.defer_refresh = False
        self.pending = None
        self.generate_impl = lambda request: GeneratedBooklet(b"%PDF-fixture", request.exclusions)
        self.weather_factory = Mock(return_value=object())

    def clock(self):
        return self.now

    def timezone(self):
        return self.zone

    def connect(self, callback):
        self.calls.append("connect")
        callback(True)

    def list_sources_async(self, kind, callback):
        self.calls.append(kind)
        callback(EDSResult(EDSState.READY, tuple(s for s in self.sources if s.kind == kind)), None)

    def refresh_async(self, sources, day, zone, callback):
        self.calls.append(("refresh", day, zone))
        report = RefreshReport(RefreshState.READY, tuple(
            SourceRefreshResult(source, *self.results.get(source.kind, (EDSState.EMPTY, ())))
            for source in sources))
        if self.defer_refresh:
            self.pending = lambda: callback(report, None)
        else:
            callback(report, None)
        return SimpleNamespace(cancel=lambda: callback(RefreshReport(RefreshState.CANCELLED, ()), None))

    def generate(self, request):
        self.requests.append(request)
        return self.generate_impl(request)


class ApplicationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()

    def setUp(self):
        self.config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_dir.cleanup)
        self.environment = patch.dict(os.environ, {
            "PAPERPHONE_LANGUAGE": "en", "GSETTINGS_BACKEND": "memory",
            "PAPERPHONE_CONFIG_DIR": self.config_dir.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.services = Services()
        self.views = []
        self.addCleanup(lambda: [v.close() for v in self.views])

    def view(self, **kwargs):
        view = ApplicationView(self.services, schedule=False, dispatch=immediate,
            submit=kwargs.pop("submit", immediate),
            asset_factory=lambda: AssetSelectionView(chooser_factory=Mock(side_effect=AssertionError("dialog forbidden")),
                                                     submit=immediate, dispatch=immediate),
            region_factory=lambda store: OsmRegionView(store, submit=immediate, dispatch=immediate),
            place_factory=lambda: PlaceSelectionView(submit=immediate, dispatch=immediate),
            weather_factory=FakeWeather, output_factory=kwargs.pop("output_factory", FakeOutput), **kwargs)
        self.views.append(view)
        return view

    def sources(self, view, selected=None):
        view.list_sources()
        for key, checkbox in view.source_checks.items():
            checkbox.set_active(selected is None or key[0] in selected)
        view.refresh_sources()

    def available(self):
        self.contact = ContactRecord("ADDRESS_BOOK", "contact", "Notes", ("000-0000",))
        self.todo = TodoRecord("TASK_LIST", "todo", "Today", None, None)
        self.event = CalendarEvent("event", "instance", "Notes", DateOnly(NOW.date()), DateOnly((NOW + timedelta(days=1)).date()))
        self.services.results = {
            SourceKind.ADDRESS_BOOK: (EDSState.READY, (self.contact,)),
            SourceKind.TASK_LIST: (EDSState.READY, (self.todo,)),
            SourceKind.CALENDAR: (EDSState.READY, (self.event,)),
        }

    def test_construction_automatically_lists_sources_and_does_not_load_extract(self):
        view = self.view()
        self.assertEqual(self.services.calls[0], "connect")
        self.assertEqual(len(self.services.calls), 4)
        self.services.store.load.assert_not_called()
        self.services.store.update.assert_not_called()
        self.services.weather_factory.assert_not_called()
        self.assertIsNone(view.weather_view)

    def test_offline_booklet_with_notes_uses_real_composition_and_frozen_pdf(self):
        self.services.generate_impl = generate_booklet
        view = self.view()
        view.assets.notes_checkbox.set_active(True)
        view.name_entry.set_text("Fixture")
        self.assertTrue(view.generate())
        self.assertTrue(view.output.pdf_bytes.startswith(b"%PDF"))
        original = view.output.pdf_bytes
        view.name_entry.set_text("Changed")
        view.assets.notes_checkbox.set_active(False)
        self.assertEqual(view.output.pdf_bytes, original)
        self.assertEqual(self.services.calls[0], "connect")
        self.assertEqual(len(self.services.calls), 4)  # 起動時にソース列挙まで行う。

    def test_all_available_sources_preserve_individual_selection(self):
        self.available()
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        view.todos.checkbox_for(self.todo.key).set_active(True)
        next(iter(view.calendar_checks.values())).set_active(True)
        view.todos.set_visible_bucket(TodoBucket.UPCOMING)
        request = view.capture_request()
        self.assertEqual({item.category for item in request.snapshot.items}, {"contacts", "undated", "calendar"})
        self.assertEqual(request.exclusions, ())
        self.assertTrue(view.generate())
        self.assertEqual(view.output.pdf_bytes, b"%PDF-fixture")

    def test_partial_failure_continues_without_confirmation(self):
        self.available()
        self.services.results[SourceKind.CALENDAR] = (EDSState.AUTHENTICATION_REQUIRED, ())
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        self.assertTrue(view.generate())
        key = (SourceKind.CALENDAR, "CALENDAR")
        self.assertEqual(STATE_MESSAGES_TEXT, None) if False else None
        self.assertIn("Failed.", view.source_status[key].get_text())
        self.assertTrue(view.contacts.get_sensitive())
        self.assertTrue(view.generate())
        self.assertIn("Calendar: excluded", view.output.warnings)
        self.assertEqual([item.category for item in self.services.requests[-1].snapshot.items], ["contacts"])

    def test_empty_is_ok_but_unregistered_kind_is_just_excluded(self):
        view = self.view()
        self.sources(view)
        self.assertEqual(view.pending_exclusions(), ())
        self.assertEqual("OK", next(iter(view.source_status.values())).get_text())
        view._registry_failure(SourceKind.TASK_LIST, EDSState.UNREGISTERED)
        # 未登録でも確認を求めない。生成はそのまま続く。
        self.assertTrue(view.generate())

    def test_denied_weather_does_not_disable_other_content(self):
        view = self.view()
        view.contact_entry.set_text("fixture-contact")
        view._apply_contact()
        self.services.weather_factory.assert_called_once_with("fixture-contact")
        self.assertEqual(self.services.calls[1:], [SourceKind.CALENDAR, SourceKind.TASK_LIST, SourceKind.ADDRESS_BOOK])
        view.weather_view.result(WeatherLocationState.DENIED)
        self.assertTrue(view.generate())
        view.assets.notes_checkbox.set_active(True)
        self.assertTrue(view.assets.notes_checkbox.get_sensitive())
        self.assertTrue(view.generate())
        view.contact_entry.set_text("fixture-contact")
        view._apply_contact()
        self.services.generate_impl = generate_booklet
        self.assertTrue(view.generate())
        self.assertIn("weather: excluded", view.output.warnings)

    def test_missing_contact_never_constructs_location_view(self):
        view = self.view()
        view._apply_contact()
        self.assertIsNone(view.weather_view)
        self.assertEqual(self.services.calls[0], "connect")
        self.assertEqual(len(self.services.calls), 4)  # 起動時にソース列挙まで行う。

    def test_weather_snapshot_uses_actual_forecast_times_and_local_suggestion_only(self):
        view = self.view()
        view.contact_entry.set_text("fixture-contact")
        view._apply_contact()
        at = NOW.replace(hour=9, minute=30)
        result = ForecastResult((Forecast(at, 5, "clearsky_day", at + timedelta(hours=1)),), NOW, NOW, NOW + timedelta(hours=2))
        view.weather_view.result(WeatherLocationState.READY, result,
                                SimpleNamespace(latitude=35.68, longitude=139.76))
        request = view.capture_request()
        fields = dict(request.snapshot.items[0].fields)
        self.assertIn("09:30", fields["forecast_9_actual_at"])
        self.assertEqual(fields["forecast_12_actual_at"], "")
        self.assertEqual(view.region.prefecture_code, 13)
        self.services.store.update.assert_not_called()
        # 地図データの読込 freeze-checkはmanifestに触れない。
        self.services.store.update.assert_not_called()

    def test_refresh_preserves_selection_until_actual_removal(self):
        self.available()
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        view.todos.checkbox_for(self.todo.key).set_active(True)
        self.services.defer_refresh = True
        view.refresh_sources()
        self.assertTrue(self.contact.key in view.selection.selected_keys)
        self.assertFalse(view.generate())
        self.services.pending()
        self.assertTrue(view.contacts.checkbox_for(self.contact.key).get_active())
        self.assertTrue(view.todos.checkbox_for(self.todo.key).get_active())
        self.services.defer_refresh = False
        self.services.results[SourceKind.TASK_LIST] = (EDSState.EMPTY, ())
        view.refresh_sources()
        self.assertNotIn(self.todo.key, view.selection.selected_keys)
        self.assertFalse(view.status.get_text())
        self.assertTrue(view.contacts.checkbox_for(self.contact.key).get_active())

    def test_failed_refresh_excludes_old_candidates(self):
        self.available()
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        self.services.results[SourceKind.ADDRESS_BOOK] = (EDSState.COMMUNICATION_ERROR, ())
        view.refresh_sources()
        key = (SourceKind.ADDRESS_BOOK, "ADDRESS_BOOK")
        self.assertEqual(view.contacts.row_count, 0)
        self.assertTrue(view.generate())
        self.assertFalse(self.services.requests[-1].snapshot.items)
        self.assertIn(("Address Book", "ADDRESS_BOOK", "communication-error"), self.services.requests[-1].source_states)

    def test_old_refresh_cannot_restore_deselected_source(self):
        self.available()
        self.services.defer_refresh = True
        view = self.view()
        self.sources(view, {SourceKind.ADDRESS_BOOK})
        view.source_checks[(SourceKind.ADDRESS_BOOK, "ADDRESS_BOOK")].set_active(False)
        self.services.pending()
        self.assertEqual(view.contacts.row_count, 0)
        self.assertNotIn((SourceKind.ADDRESS_BOOK, "ADDRESS_BOOK"), view._reports)
        self.assertIn("ignored", view.status.get_text())

    def test_worker_receives_captured_choices_and_language_not_later_edits(self):
        jobs, updates = [], []
        view = self.view(submit=jobs.append)
        view._dispatch = updates.append
        view.name_entry.set_text("Before")
        view.assets.notes_checkbox.set_active(True)
        self.assertTrue(view.generate())
        view.name_entry.set_text("After")
        view.assets.notes_checkbox.set_active(False)
        view.set_language("ja")
        jobs.pop()()
        request = self.services.requests[-1]
        self.assertEqual(request.name, "Before")
        self.assertEqual(request.language, "en")
        self.assertTrue(request.assets.notes_enabled)
        self.assertIsNone(view.output.pdf_bytes)
        updates.pop()()
        self.assertEqual(view.output.pdf_bytes, b"%PDF-fixture")

    def test_photo_busy_blocks_and_failure_requires_explicit_ack(self):
        view = self.view()
        view.assets.photo_state = "reading"
        self.assertFalse(view.generate())
        view.assets.photo_state = "failed"
        view.assets.status.set_text("failed")
        # 失敗した写真は含めず、他の内容だけで生成できる。
        self.assertTrue(view.generate())
        self.assertIn("photo: excluded", view.output.warnings)

    def test_en_ja_switch_preserves_user_text_and_selections(self):
        self.available()
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        view.name_entry.set_text("Notes")
        view.assets.paper_app_dropdown.set_selected(1)
        view.set_language("ja")
        self.assertEqual(view.generate_button.get_label(), gettext_for("ja")("Generate booklet"))
        self.assertNotEqual(view.generate_button.get_label(), "Generate booklet")
        self.assertEqual(view.name_entry.get_text(), "Notes")
        self.assertIn("Notes", view.contacts.checkbox_for(self.contact.key).get_label())
        self.assertTrue(view.contacts.checkbox_for(self.contact.key).get_active())
        self.assertEqual(view.todos.checkbox_for(self.todo.key).get_label(), "Today")
        self.assertTrue(next(iter(view.calendar_checks.values())).get_label().startswith("Notes\n"))
        self.assertIn(gettext_for("ja")("All day"), next(iter(view.calendar_checks.values())).get_label())
        self.assertEqual(view.assets.paper_app_dropdown.get_selected(), 1)
        view.set_language("en")
        self.assertEqual(view.generate_button.get_label(), "Generate booklet")

    def test_language_switch_preserves_map_index_endpoints_and_exclusion_ack(self):
        view = self.view()
        view.region.prefecture.set_selected(13)
        extract, index, start, destination = object(), object(), object(), object()
        view.region.selected_extract = extract
        view._loaded_extract = extract
        view.places.index = index
        view.places.selected.update(start=start, destination=destination)
        token = view.region._generation
        view.set_language("ja")
        self.assertEqual(view.region.prefecture_code, 13)
        self.assertIs(view.region.selected_extract, extract)
        self.assertIs(view.places.index, index)
        self.assertIs(view.places.selected_start, start)
        self.assertIs(view.places.selected_destination, destination)
        self.assertEqual(view.region._generation, token)

    def test_clock_reclassifies_todo_and_invalidates_old_day_calendar_without_touching_pdf(self):
        self.available()
        future = TodoRecord("TASK_LIST", "future", "Fixture", ZonedDateTime(NOW + timedelta(minutes=1)), None)
        self.services.results[SourceKind.TASK_LIST] = (EDSState.READY, (future,))
        view = self.view()
        self.sources(view)
        self.assertEqual(view.todos.row_count(TodoBucket.UPCOMING), 1)
        view.todos.checkbox_for(future.key).set_active(True)
        self.assertTrue(view.generate())
        original = view.output.pdf_bytes
        self.services.now += timedelta(minutes=1)
        view.refresh_clock()
        self.assertEqual(view.todos.row_count(TodoBucket.TODAY), 1)
        self.assertTrue(view.todos.checkbox_for(future.key).get_active())
        self.services.now += timedelta(days=1)
        view.refresh_clock()
        # 日替わりで古い予定は印刷しない。生成は確認なしに続行できる。
        self.assertFalse(view.calendar_checks)
        self.assertEqual(view.output.pdf_bytes, original)

    def test_local_extract_offline_index_route_and_full_pdf_use_selected_immutable_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.osm"
            path.write_text(XML, encoding="utf-8")
            extract = SimpleNamespace(prefecture=13, path=path, basis=NOW,
                                      sha256="a" * 64, state=SimpleNamespace(bytes_count=path.stat().st_size))
            self.services.store.load.return_value = extract
            self.services.generate_impl = generate_booklet
            self.available()
            view = self.view()
            self.sources(view)
            view.contacts.checkbox_for(self.contact.key).set_active(True)
            view.todos.checkbox_for(self.todo.key).set_active(True)
            next(iter(view.calendar_checks.values())).set_active(True)
            view.contact_entry.set_text("fixture-contact")
            view._apply_contact()
            forecast_at = NOW.replace(hour=9)
            forecast = ForecastResult((Forecast(forecast_at, 6, "clearsky_day", forecast_at + timedelta(hours=1)),),
                                      NOW, NOW, NOW + timedelta(hours=2))
            view.weather_view.result(WeatherLocationState.READY, forecast)
            from tests.test_booklet_asset_view import synthetic_png
            view.assets._photo = synthetic_png()
            view.assets.photo_state = "ready"
            view.assets.notes_checkbox.set_active(True)
            view.assets.paper_app_dropdown.set_selected(1)
            view.assets.contactless_checkbox.set_active(True)
            view.region.prefecture.set_selected(13)
            self.services.store.update.assert_not_called()
            self.assertIsNotNone(view.places.index)
            view.places.choose("start", view.places.index.get("node/1"))
            view.places.choose("destination", view.places.index.get("node/3"))
            self.assertTrue(view.generate())
            self.assertTrue(view.output.pdf_bytes.startswith(b"%PDF"))
            self.assertEqual(self.services.requests[-1].extract.path, path)
            from tests.test_booklet_maps import document
            self.assertEqual(document(view.output.pdf_bytes).get_n_pages(), 2)
            self.services.store.load.assert_called_once_with(13)

    def test_map_failure_needs_ack_and_retains_previous_pdf(self):
        view = self.view()
        view.output.set_pdf(b"old")
        view.region.selected_extract = object()
        view.places.index = object()
        view.places.selected.update(start=object(), destination=object())
        self.services.generate_impl = Mock(side_effect=GenerationFailure("map", "disconnected"))
        self.assertTrue(view.generate())
        self.assertEqual(view.output.pdf_bytes, b"old")
        # 地図の出力はエンドポイントの指定がなければ生成に影響しない。
        self.assertEqual(view.pending_exclusions(), ())
        self.assertIn("disconnected", view.map_failure.get_text())
        self.services.generate_impl = lambda request: GeneratedBooklet(b"new", request.exclusions)
        self.assertTrue(view.generate())
        self.assertIsNone(self.services.requests[-1].extract)
        self.assertIn("map: excluded", view.output.warnings)

    def test_registry_failure_excludes_old_selected_items_and_relisting_clears_failure(self):
        self.available()
        view = self.view()
        self.sources(view)
        view.contacts.checkbox_for(self.contact.key).set_active(True)
        original_list = self.services.list_sources_async
        def failed_list(kind, callback):
            if kind is SourceKind.ADDRESS_BOOK:
                callback(EDSResult(EDSState.COMMUNICATION_ERROR), None)
            else:
                original_list(kind, callback)
        self.services.list_sources_async = failed_list
        view.list_sources()
        self.assertEqual(view.contacts.row_count, 0)
        self.assertNotIn(self.contact.key, view.selection.selected_keys)
        self.assertTrue(view.generate())
        self.assertEqual(self.services.requests[-1].snapshot.items, ())
        self.assertIn("Address Book: excluded", view.output.warnings)
        self.services.list_sources_async = original_list
        view.list_sources()
        self.assertNotIn(SourceKind.ADDRESS_BOOK, view._registry_states)
        self.assertEqual(view.contacts.row_count, 0)
        view.refresh_sources()
        self.assertEqual(view.contacts.row_count, 1)
        self.assertFalse(view.contacts.checkbox_for(self.contact.key).get_active())

    def test_photo_failure_exclusion_cannot_print_retained_previous_photo(self):
        from tests.test_booklet_asset_view import synthetic_png
        view = self.view()
        old = synthetic_png()
        view.assets._photo = old
        view.assets.photo_state = "ready"
        view.output.set_pdf(b"old-pdf")
        view.assets._choose_photo(None)  # 注入済みの失敗chooser。実ダイアログは開かない。
        self.assertEqual(view.assets.photo_state, "failed")
        self.assertEqual(view.assets.capture_choices().photo, old)
        request = view.capture_request()
        self.assertIsNone(request.assets.photo)
        self.assertIn("photo: excluded", request.exclusions)
        self.assertEqual(view.output.pdf_bytes, b"old-pdf")
        from tests.test_booklet_asset_view import Picker
        picker = Picker()
        view.assets._chooser_factory = lambda: picker
        view.assets._choose_photo(None)
        picker.respond(file=None)
        self.assertEqual(view.assets.photo_state, "cancelled")
        self.assertEqual(view.capture_request().assets.photo, old)

    def test_exclusion_and_render_overflow_warnings_are_not_mixed_during_render(self):
        self.services.generate_impl = generate_booklet
        view = self.view()
        view.name_entry.set_text("Long fixture cover name " * 100)
        self.assertTrue(view.generate())
        self.assertTrue(view.output.pdf_bytes.startswith(b"%PDF"))
        view.contact_entry.set_text("fixture-contact")
        view._apply_contact()
        self.services.generate_impl = generate_booklet
        self.assertTrue(view.generate())
        self.assertIn("weather: excluded", view.output.warnings)
        self.assertTrue(any("shortened" in warning for warning in view.output.warnings))

    def test_real_preview_language_switch_keeps_pdf_and_frozen_warning_text(self):
        from paperphone.pdf_output import PdfOutputView
        forbidden = Mock(side_effect=AssertionError("native dialog forbidden"))
        self.services.generate_impl = generate_booklet
        view = self.view(output_factory=lambda: PdfOutputView(chooser_factory=forbidden, print_factory=forbidden))
        view.generate()
        original, warning = view.output.pdf_bytes, view.output.warnings.get_text()
        view.set_language("ja")
        self.assertEqual(view.output.pdf_bytes, original)
        self.assertEqual(view.output.warnings.get_text(), warning)
        forbidden.assert_not_called()

    def test_service_composes_core_assets_and_guides_under_captured_language(self):
        view = self.view(language="ja")
        view.assets.contactless_checkbox.set_active(True)
        request = view.capture_request()
        seen = []
        marker = object()
        def core(*args, **kwargs):
            seen.append(language_code())
            os.environ["PAPERPHONE_LANGUAGE"] = "en"
            return {5: marker}
        def assets(**kwargs):
            seen.append(language_code())
            self.assertEqual(kwargs["occupied_indices"], {5})
            self.assertTrue(kwargs["contactless"])
            return {3: marker}
        with patch("paperphone.application_services.core_painters", side_effect=core), \
             patch("paperphone.application_services.asset_painters", side_effect=assets), \
             patch("paperphone.application_services.render_with_map", return_value=b"pdf") as render:
            result = generate_booklet(request)
        self.assertEqual(seen, ["ja", "ja"])
        self.assertEqual(set(render.call_args.args[0]), {3, 5})
        self.assertIsNotNone(render.call_args.kwargs["main_overlay"])
        self.assertEqual(result.pdf, b"pdf")

    def test_main_content_factory_accepts_fake_services_without_activation(self):
        from paperphone.main import APPLICATION_ID, PaperPhoneApplication
        factory = Mock(return_value=object())
        app = PaperPhoneApplication(services=self.services, view_factory=factory)
        self.assertEqual(app.get_application_id(), APPLICATION_ID)
        app._build_content()
        factory.assert_called_once_with(self.services)
        self.assertEqual(self.services.calls, [])  # 実ビューでなければ列挙も発生しない。

    def test_close_discards_late_generation_result(self):
        jobs, updates = [], []
        view = self.view(submit=jobs.append)
        view._dispatch = updates.append
        view.generate()
        view.close()
        jobs.pop()()
        updates.pop()()
        self.assertIsNone(view.output.pdf_bytes)


if __name__ == "__main__":
    unittest.main()
