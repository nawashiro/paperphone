# VM検証記録（2026-11-11）

Cua VM（runtests.sh環境・jaカタログ再生成済み）で確認した。個人情報は含まない。

## 1.1・1.2 空文言の保護

- `gettext_for()`は空文字列とNoneを翻訳せずそのまま返す一元バリヤにした。天気READY状態の状態ラベルが空のまま（カタログヘッダーを出さない）ことをモックで確認。
- 試験: `test_i18n`（空・非空・状態語の横滑り）、`test_ready_state_does_not_show_the_catalog_header`。

## 2.1 帰属の二重表示の統一

- 天気領域の`ATTRIBUTION_SHORT`付加labelを撤去。MET Norwayの帰属は`forecast_details`（`weather_lines`末尾）の1か所だけになる構成を試験で固定。
- 試験: `test_weather_section_shows_attribution_once`。

## 3.1 ja.po全監査

- 全235のmsgid/msgstr対を走査し、12対を是正した（横滑り・欠落）:
  - `Map data could not be deleted.`: ローカルの道路データを読み込めません。→ 地図データを削除できませんでした。
  - `Settings could not be saved.`: 更新を開始できませんでした。→ 設定を保存できませんでした。
  - `Print operation accepted…`: 誤字（はらわかりません）を正字へ。
  - `Extract unavailable.`: 天気を取得しました。→ 抽出版を利用できません。
  - `Rate limited.`: 要求が絞られています。→ 要求が制限されています。
  - `Downloaded data is invalid.`: 経路の探索範囲が不正です。→ ダウンロードしたデータが不正です。
  - `Save failed.` / `Search failed.`: ソースにアクセスできません。→ 保存／検索に失敗しました。
  - `Not confirmed.` / `{t} °C`: 空msgstrを補填（空はgettextでmsgidなし空文字を返す経路になる）。
  - `Deleted.`: 写真を選びました。→ 削除しました。
  - `Weather fetch failed.`: 天気を取得しました。→ 天気を取得できませんでした。
- 是正は`msgfmt --check`通過、回帰試験（`test_status_words_do_not_drift_across_features`）で固定。全446試験が通過。

## 5.1 UI日時の人間向け表記

- `format_temporal`をロケール表記へ（ja `2026年11月10日 17:30`／en `Nov 10, 2026, 05:30 PM`）。スナップショットの`fields`はISOのまま（紙面の`fromisoformat`解析を守る）ため、ISO保存を`_snapshot_temporal`に分離。
- 試験: `test_temporal_display_is_locale_readable_and_snapshot_stays_iso`。

## 6.1 全回帰

- `runtests.sh`で446試験がすべて通過（2026-11-11時点）。
