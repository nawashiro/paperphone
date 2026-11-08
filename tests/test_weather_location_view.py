"""天気の部分失敗後も別ビューで印刷対象を選択できることを検証。"""

import unittest
from unittest.mock import Mock
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.contact_selection import ContactSelectionView
from paperphone.contacts import ContactRecord
from paperphone.location import LocationDenied, LocationFix, LocationTimeout, LocationUnavailable
from paperphone.selection import SelectionModel
from paperphone.weather_location import WeatherLocationController, WeatherLocationState as S
from paperphone.weather_location_view import MESSAGES, WeatherLocationView, _


class WeatherLocationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_failure_status_does_not_disable_contact_selection(self):
        for failure, expected in [(LocationDenied("hidden"), S.DENIED),
                                  (LocationTimeout("hidden"), S.TIMEOUT),
                                  (LocationUnavailable("hidden"), S.UNAVAILABLE),
                                  (None, S.INVALID)]:
            with self.subTest(state=expected):
                portal = Mock()
                portal.request_once.side_effect = failure
                portal.request_once.return_value = LocationFix(35, 139, None)
                client = Mock()

                def controller(client, **kwargs):
                    return WeatherLocationController(client, **kwargs,
                        portal_factory=lambda **_kw: portal)

                view = WeatherLocationView(client, controller_factory=controller)
                host = Gtk.Box()
                host.append(view)
                contacts = ContactSelectionView(SelectionModel())
                host.append(contacts)
                record = ContactRecord("book", "id", "Fixture Contact", ("123",))
                contacts.set_contacts([record])
                try:
                    view.controller.start("synthetic-parent")
                    self.assertEqual(view.status.get_text(), _(MESSAGES[expected]))
                    self.assertFalse(view.request_button.get_sensitive())
                    client.fetch.assert_not_called()
                    contacts.checkbox_for(record.key).set_active(True)
                    self.assertEqual(contacts.selected_items[0].key, record.key)
                finally:
                    view.close()


    def test_no_fetch_after_last_window_and_plain_message(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from paperphone.weather_location_view import NO_FORECAST_MESSAGE
        late = datetime(2026, 11, 8, 21, 53, tzinfo=ZoneInfo("Asia/Tokyo"))
        client = Mock()
        view = WeatherLocationView(client, clock=lambda: late)
        host = Gtk.Box(); host.append(view)
        try:
            # 取得ボタン自体が出ない。開いた時点で「取得不要」の文言だけを示す。
            self.assertFalse(view.request_button.get_visible())
            self.assertEqual(view.status.get_text(), "")
            view._request(None)
            self.assertEqual(view.status.get_text(), _(NO_FORECAST_MESSAGE))
            client.fetch.assert_not_called()
        finally:
            view.close()

if __name__ == "__main__":
    unittest.main()
