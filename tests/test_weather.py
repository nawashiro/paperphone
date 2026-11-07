"""実通信・実座標・連絡先を使わない直接取得fixture。"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

from paperphone.weather import ForecastResult, Response, WeatherClient, WeatherError, parse_forecasts

UTC = timezone.utc
NOW = datetime(2026, 1, 2, tzinfo=UTC)


def item(time="2026-01-02T00:00:00Z", temperature=5, symbol="cloudy"):
    return {"time": time, "data": {"instant": {"details": {"air_temperature": temperature}},
            "next_1_hours": {"summary": {"symbol_code": symbol}}}}


def body(*items):
    return json.dumps({"properties": {"meta": {"units": {"air_temperature": "celsius"}},
                                     "timeseries": list(items)}}).encode()


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.requests = []
        self.responses = [Response(200, {"Expires": format_datetime(NOW + timedelta(hours=1)),
                                        "Last-Modified": format_datetime(NOW), "ETag": '"fixture"'}, body(item()))]
        self.client = WeatherClient("org.example.Test", "0.1", "https://example.invalid", transport=self.transport,
                                    clock=lambda: self.now)

    def transport(self, url, headers):
        self.requests.append((url, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def test_explicit_period_temperature_utc_and_deduplication(self):
        result = parse_forecasts(body(item("2026-01-02T09:00:00+09:00"), item()))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].time, NOW)
        self.assertEqual(result[0].temperature_c, 5)
        self.assertEqual(result[0].period_end, NOW + timedelta(hours=1))

    def test_excludes_medium_range_missing_temperature_and_naive_time(self):
        medium = item()
        medium["data"]["next_6_hours"] = medium["data"].pop("next_1_hours")
        self.assertEqual(parse_forecasts(body(medium, item(temperature=None), item(temperature=True),
                                                   item(temperature=float("nan")), item("2026-01-02T01:00:00"))), ())

    def test_unknown_symbol_is_not_invented(self):
        self.assertEqual(parse_forecasts(body(item(symbol="future_unknown")))[0].symbol, "future_unknown")

    def test_bad_payload_and_units(self):
        for payload in (b"broken", b"{}", b"[]", body(item()).replace(b"celsius", b"fahrenheit")):
            with self.subTest(payload=payload), self.assertRaises(WeatherError):
                parse_forecasts(payload)

    def test_identification_truncation_and_fresh_cache(self):
        first = self.client.fetch(12.345678, -45.678987)
        self.assertIs(self.client.fetch(12.345678, -45.678987), first)
        self.assertEqual(len(self.requests), 1)
        url, headers = self.requests[0]
        self.assertTrue(url.endswith("lat=12.3456&lon=-45.6789"))
        self.assertEqual(headers["User-Agent"], "org.example.Test/0.1 (https://example.invalid)")
        self.assertEqual(headers["Accept-Encoding"], "gzip, deflate")

    def test_conditional_304_preserves_content_and_fetch_time(self):
        first = self.client.fetch(0, 0)
        self.now += timedelta(hours=1)
        self.responses.append(Response(304, {"Expires": format_datetime(self.now + timedelta(hours=2))}, b""))
        second = self.client.fetch(0, 0)
        self.assertEqual(first.forecasts, second.forecasts)
        self.assertEqual(first.fetched_at, second.fetched_at)
        self.assertEqual(second.checked_at, self.now)
        self.assertEqual(self.requests[-1][1]["If-Modified-Since"], format_datetime(NOW))
        self.assertEqual(self.requests[-1][1]["If-None-Match"], '"fixture"')
        self.assertIs(self.client.fetch(0, 0), second)

    def test_missing_or_invalid_expiry_does_not_loop(self):
        for headers in ({}, {"Expires": "broken"}):
            with self.subTest(headers=headers):
                self.setUp()
                self.responses = [Response(200, headers, body(item()))]
                result = self.client.fetch(0, 0)
                self.assertIsNone(result.expires_at)
                with self.assertRaises(WeatherError):
                    self.client.fetch(0, 0)
                self.assertEqual(len(self.requests), 1)

    def test_retry_after_seconds_date_and_invalid(self):
        for retry, wait in (("3600", 3600), (format_datetime(NOW + timedelta(hours=1)), 3600), ("bad", 600)):
            with self.subTest(retry=retry):
                self.setUp()
                self.responses = [Response(429, {"Retry-After": retry}, b"")]
                with self.assertRaises(WeatherError):
                    self.client.fetch(0, 0)
                self.now += timedelta(seconds=wait - 1)
                with self.assertRaises(WeatherError):
                    self.client.fetch(0, 0)
                self.assertEqual(len(self.requests), 1)

    def test_network_failure_sanitized_and_no_immediate_retry(self):
        self.responses = [RuntimeError("sensitive URL and response")]
        with self.assertRaises(WeatherError) as caught:
            self.client.fetch(0, 0)
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        with self.assertRaises(WeatherError):
            self.client.fetch(0, 0)
        self.assertEqual(len(self.requests), 1)

    def test_other_location_does_not_receive_old_forecast_or_validators(self):
        self.client.fetch(0, 0)
        self.now += timedelta(hours=1)
        self.responses.append(Response(200, {}, body(item(temperature=10))))
        result = self.client.fetch(1, 1)
        self.assertEqual(result.forecasts[0].temperature_c, 10)
        self.assertNotIn("If-Modified-Since", self.requests[-1][1])
        self.assertNotIn("If-None-Match", self.requests[-1][1])

    def test_compression_and_deprecation(self):
        for encoding, compress in (("gzip", gzip.compress), ("deflate", zlib.compress)):
            with self.subTest(encoding=encoding):
                self.setUp()
                self.responses = [Response(203, {"Content-Encoding": encoding}, compress(body(item())))]
                result = self.client.fetch(0, 0)
                self.assertTrue(result.deprecated)
                self.assertEqual(len(result.forecasts), 1)

    def test_invalid_coordinates_and_identification(self):
        for latitude, longitude in ((91, 0), (0, 181), (float("nan"), 0), (0, float("inf")), (True, 0)):
            with self.assertRaises(ValueError):
                self.client.fetch(latitude, longitude)
        self.assertFalse(self.requests)
        for contact in ("", "header\r\ninjection", "\x7f"):
            with self.assertRaises(ValueError):
                WeatherClient("test", "1", contact)

    def test_client_writes_no_files_or_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("builtins.open", side_effect=AssertionError("disk write")), self.assertNoLogs():
                result = self.client.fetch(0, 0)
            self.assertIsInstance(result, ForecastResult)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
