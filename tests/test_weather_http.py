"""実通信なしでHTTP層・機密非出力・期限切れ表示を検証。"""

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import timedelta
import io
import unittest
from unittest.mock import patch, Mock

from paperphone.weather import Response, WeatherClient, WeatherError, _http_get
from paperphone.weather_sheet import build_weather_sheet
from tests.test_weather import NOW, item, body
from tests.test_weather_sheet import result_at


class FakeStream(io.BytesIO):
    code = 200
    headers = {"Content-Type": "application/json"}


class WeatherHttpTests(unittest.TestCase):
    def test_http_transport_has_no_disk_cache_or_logging(self):
        stream = FakeStream(body(item()))
        opener = Mock()
        opener.open.return_value = stream
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("paperphone.weather.build_opener", return_value=opener), \
             patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
             redirect_stdout(stdout), redirect_stderr(stderr), self.assertNoLogs():
            response = _http_get("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=0&lon=0",
                                 {"User-Agent": "fixture/1 (https://example.invalid)"})
        self.assertEqual(response.status, 200)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "fixture/1 (https://example.invalid)")
        self.assertEqual(response.body, body(item()))

    def test_failed_transport_does_not_expose_url_in_message(self):
        opener = Mock()
        opener.open.side_effect = RuntimeError("private-url-and-contact")
        with patch("paperphone.weather.build_opener", return_value=opener):
            with self.assertRaises(WeatherError) as caught:
                _http_get("https://example.invalid/private", {})
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_no_prior_result_for_304_is_failure(self):
        client = WeatherClient("fixture", "1", "https://example.invalid",
            transport=lambda *_: Response(304, {}, b""), clock=lambda: NOW)
        with self.assertRaises(WeatherError):
            client.fetch(0, 0)

    def test_missing_expiry_is_not_described_as_fresh(self):
        sheet = build_weather_sheet(replace(result_at(), expires_at=None), now=NOW)
        self.assertIsNone(sheet.expires_at)
        self.assertEqual(sheet.as_selectable_item().fields["expires_at"], "")

    def test_expired_data_retains_source_time_and_explicit_expired_flag(self):
        result = replace(result_at(), expires_at=NOW - timedelta(seconds=1))
        sheet = build_weather_sheet(result, now=NOW)
        self.assertTrue(sheet.expired)
        self.assertEqual(sheet.as_selectable_item().fields["expired"], "true")
        self.assertEqual(sheet.fetched_at, result.fetched_at)


if __name__ == "__main__":
    unittest.main()
