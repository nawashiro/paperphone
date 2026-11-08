"""合成PBFと偽HTTPで原子的更新・条件付き確認・障害時の保持を検証。"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paperphone.osm_store import ExtractStore, ExtractFailure, _MirrorRedirect

UTC = timezone.utc
NOW = datetime(2026, 1, 2, tzinfo=UTC)


def pbf(tag=b"a"):
    # 実OSM幾何ではなく、feature宣言だけの有効な空OSMHeaderブロック。
    feature = b"OsmSchema-V0.6"
    program = b"fixture-" + tag
    block = b"\x22" + bytes([len(feature)]) + feature + b"\x82\x01" + bytes([len(program)]) + program
    blob = b"\x0a" + bytes([len(block)]) + block
    header = b"\x0a\x09OSMHeader\x18" + bytes([len(blob)])
    return len(header).to_bytes(4, "big") + header + blob


class Stream(io.BytesIO):
    def __init__(self, data=None, *, status=200, headers=None):
        data = pbf() if data is None else data
        super().__init__(data)
        self.status = status
        self.headers = headers if headers is not None else {
            "Content-Type": "application/x-pbf", "ETag": '"fixture"',
            "Content-Length": str(len(data)),
            "Content-Disposition": 'attachment; filename="JP-13-202601020100.osm.pbf"',
        }


class ExtractStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = NOW
        self.requests = []
        self.responses = []
        self.store = ExtractStore(self.temp.name, transport=self.get, clock=lambda: self.now)

    def get(self, url, headers):
        self.requests.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def update(self, response):
        self.responses.append(response)
        self.now += timedelta(minutes=10)
        return self.store.update(13, confirmed=True)

    def test_no_request_or_files_before_confirmation(self):
        with self.assertRaises(ExtractFailure) as caught:
            self.store.update(13)
        self.assertEqual(caught.exception.reason, "confirmation")
        self.assertFalse(self.requests)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_download_persist_reload_and_304_keep_same_version(self):
        first = self.update(Stream())
        self.assertTrue(first.downloaded)
        self.assertEqual(first.extract.path.read_bytes(), pbf())
        self.assertEqual(first.extract.basis, datetime(2026, 1, 2, 1, tzinfo=UTC))
        self.assertEqual(self.store.load(13), first.extract)
        second = self.update(Stream(b"", status=304, headers={}))
        self.assertFalse(second.downloaded)
        self.assertEqual(second.extract, first.extract)
        self.assertEqual(self.requests[-1][1]["If-None-Match"], '"fixture"')
        self.assertNotIn("lat=", self.requests[-1][0])
        self.assertNotIn("lon=", self.requests[-1][0])

    def test_last_modified_fallback_is_conditional(self):
        stream = Stream()
        stream.headers.pop("ETag")
        stream.headers["Last-Modified"] = format_datetime(NOW)
        self.update(stream)
        self.update(Stream(b"", status=304, headers={}))
        self.assertEqual(self.requests[-1][1]["If-Modified-Since"], format_datetime(NOW))

    def test_successful_update_does_not_change_previous_snapshot_file(self):
        first = self.update(Stream()).extract
        new = Stream(pbf(b"b"))
        new.headers["ETag"] = '"new"'
        new.headers["Content-Disposition"] = 'attachment; filename="JP-13-202601030100.osm.pbf"'
        second = self.update(new).extract
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), pbf())
        self.assertEqual(second.path.read_bytes(), pbf(b"b"))
        self.assertEqual(self.store.load(13), second)

    def test_failure_retains_existing_file_and_manifest(self):
        first = self.update(Stream()).extract
        for response, reason in [(Stream(b"", status=403, headers={}), "denied"),
                                 (Stream(b"", status=404, headers={}), "missing"),
                                 (Stream(b"", status=503, headers={}), "http"),
                                 (OSError("sensitive details"), "network"),
                                 (Stream(b"not pbf"), "invalid")]:
            with self.subTest(reason=reason), self.assertRaises(ExtractFailure) as caught:
                self.update(response)
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn("sensitive", str(caught.exception))
            self.assertEqual(self.store.load(13), first)
        self.assertFalse(list(Path(self.temp.name).glob(".pbf-*")))

    def test_mismatch_and_wrong_region_do_not_replace_previous_data(self):
        first = self.update(Stream()).extract
        for header, value in [("Content-Length", "999"),
                              ("Content-Disposition", 'attachment; filename="JP-27-202601020100.osm.pbf"')]:
            bad = Stream()
            bad.headers[header] = value
            with self.assertRaises(ExtractFailure):
                self.update(bad)
            self.assertEqual(self.store.load(13), first)

    def test_rate_limit_and_retry_after_prevent_repeated_requests(self):
        for date_format in (False, True):
            self.now += timedelta(hours=3)
            request_time = self.now + timedelta(minutes=10)
            retry = format_datetime(request_time + timedelta(hours=1)) if date_format else "3600"
            with self.assertRaises(ExtractFailure) as caught:
                self.update(Stream(b"", status=429, headers={"Retry-After": retry}))
            self.assertEqual(caught.exception.reason, "limited")
            calls = len(self.requests)
            with self.assertRaises(ExtractFailure) as caught:
                self.store.update(13, confirmed=True)
            self.assertEqual(caught.exception.reason, "wait")
            self.assertEqual(len(self.requests), calls)
            self.now += timedelta(minutes=59)
            with self.assertRaises(ExtractFailure) as caught:
                self.store.update(13, confirmed=True)
            self.assertEqual(caught.exception.reason, "wait")
            self.assertEqual(len(self.requests), calls)

    def test_no_cache_for_initial_304(self):
        with self.assertRaises(ExtractFailure) as caught:
            self.update(Stream(b"", status=304, headers={}))
        self.assertEqual(caught.exception.reason, "invalid")

    def test_cache_hash_prevents_silent_corruption_and_can_be_repaired(self):
        first = self.update(Stream()).extract
        first.path.write_bytes(b"broken")
        with self.assertRaises(ExtractFailure) as caught:
            self.store.load(13)
        self.assertEqual(caught.exception.reason, "cache")
        repaired = self.update(Stream())
        self.assertEqual(repaired.extract.path.read_bytes(), pbf())
        self.assertNotIn("If-None-Match", self.requests[-1][1])

    def test_permission_failure_does_not_replace_previous_data(self):
        first = self.update(Stream()).extract
        with patch("paperphone.osm_store.tempfile.NamedTemporaryFile", side_effect=PermissionError):
            with self.assertRaises(ExtractFailure) as caught:
                self.update(Stream(pbf(b"b")))
        self.assertEqual(caught.exception.reason, "storage")
        self.assertEqual(self.store.load(13), first)

    def test_invalid_prefecture_cannot_escape_store(self):
        for code in (True, 1.5, "../x", -1, 48):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.store.update(code, confirmed=True)
        self.assertFalse(self.requests)

    def test_external_redirect_is_rejected(self):
        for url in ("http://osm.download.movisda.io/admin/JP-13-latest.osm.pbf", "https://example.invalid/map"):
            with self.assertRaises(ExtractFailure):
                _MirrorRedirect().redirect_request(None, None, 302, "", {}, url)


if __name__ == "__main__":
    unittest.main()
