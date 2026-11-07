"""gettext切替と同梱素材の基本スモークテスト。"""

import os
import struct
import unittest
from unittest.mock import patch

from paperphone.i18n import gettext_for, language_code
from paperphone.resources import (
    FONT_ASSETS,
    PAPER_APP_ASSETS,
    paper_app_names,
    read_font,
    read_paper_app,
)


class LocalizationSmokeTest(unittest.TestCase):
    def test_language_code_accepts_english_and_japanese_locales(self) -> None:
        self.assertEqual(language_code("ja_JP.UTF-8"), "ja")
        self.assertEqual(language_code("en_US.UTF-8"), "en")

    def test_gettext_switches_between_english_and_japanese(self) -> None:
        source = "Create, preview, save, and print your paper booklet."
        self.assertEqual(gettext_for("en")(source), source)
        self.assertEqual(
            gettext_for("ja")(source),
            "紙の冊子を作成して、プレビュー・保存・印刷します。",
        )
        with patch.dict(os.environ, {"PAPERPHONE_LANGUAGE": "ja"}):
            self.assertEqual(
                gettext_for()(source),
                "紙の冊子を作成して、プレビュー・保存・印刷します。",
            )

    def test_paper_app_names_are_translated_and_complete(self) -> None:
        english = paper_app_names(gettext_for("en"))
        japanese = paper_app_names(gettext_for("ja"))
        self.assertEqual(len(english), 13)
        self.assertEqual(len(japanese), 13)
        self.assertEqual(english[0], "Conversion Charts")
        self.assertEqual(japanese[0], "換算表")
        self.assertEqual(english[-1], "Typography")
        self.assertEqual(japanese[-1], "タイポグラフィ")


class ResourceSmokeTest(unittest.TestCase):
    def test_all_paper_app_images_are_readable_png_resources(self) -> None:
        self.assertEqual(len(PAPER_APP_ASSETS), 13)
        for name in PAPER_APP_ASSETS:
            with self.subTest(name=name):
                data = read_paper_app(name)
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack(">II", data[16:24])
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)

    def test_font_files_are_available_as_package_resources(self) -> None:
        self.assertEqual(len(FONT_ASSETS), 2)
        for name in FONT_ASSETS:
            with self.subTest(name=name):
                self.assertGreater(len(read_font(name)), 1000)

    def test_resource_reader_rejects_unlisted_paths(self) -> None:
        with self.assertRaises(ValueError):
            read_paper_app("../../keys.properties")
        with self.assertRaises(ValueError):
            read_font("../../keys.properties")


if __name__ == "__main__":
    unittest.main()
