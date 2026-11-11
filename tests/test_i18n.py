"""空文字列の翻訳がカタログのヘッダーを返さないことの試験。"""

import unittest

from paperphone.i18n import gettext_for


class I18nTests(unittest.TestCase):
    def test_empty_string_never_returns_the_catalog_header(self):
        for language in ("ja", "en"):
            with self.subTest(language=language):
                translate = gettext_for(language)
                self.assertEqual(translate(""), "")
                self.assertEqual(translate(None), "")

    def test_nonempty_messages_still_translate(self):
        ja = gettext_for("ja")
        self.assertEqual(ja("Settings"), "設定")

    def test_status_words_do_not_drift_across_features(self):
        ja = gettext_for("ja")
        expectations = {
            "Weather fetch failed.": "天気を取得できませんでした。",
            "Extract unavailable.": "抽出版を利用できません。",
            "Search failed.": "検索に失敗しました。",
            "Deleted.": "削除しました。",
            "Save failed.": "保存に失敗しました。",
            "Downloaded data is invalid.": "ダウンロードしたデータが不正です。",
            "Settings could not be saved.": "設定を保存できませんでした。",
            "Map data could not be deleted.": "地図データを削除できませんでした。",
            "Not confirmed.": "確認が取れていません。",
            "{t} °C": "{t} °C",
        }
        for message, expected in expectations.items():
            self.assertEqual(ja(message), expected)


if __name__ == "__main__":
    unittest.main()
