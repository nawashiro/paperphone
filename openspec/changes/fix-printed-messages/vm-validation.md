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

## 4.1・4.2 TODO面の幾何

- 旧Android正本（`app/src/main/.../ui/tasks/TasksModule.kt`）と比較:
  - タイトル 11.33pt、項目は円(2.9, 8.5, r2.9)＋題名9pt(x16)＋期限6.47pt(x16)、行送り≈22pt、予備円は20pt間隔で円のみ。`drawCircle`は独立図形で連結線はない。
  - GNOME版は行送り22pt・予備20pt・円位置で一致（ラスター実測: 項目円は22pt間隔、予備円は20pt間隔で並ぶことを確認）。比較記録の成果物は本節。
- **支線の原因**: cairoの`arc()`は現在点から弧の始点へ結線を張る（Cairoの描画仕様）。`stroke()`で描くTODO円は前の円の右端(cx+r)から次の円の右端へ縦線が結ばれ、最初の円には見出し領域から斜めの線が伸びていた（issue #14の丸つなぎ・見出し接触の線）。
- 是正: 項目円・予備円それぞれに`new_sub_path()`を追加（`booklet_assets.py`の点線方眼と同じ既存イディオム）。同一経路で天気記号の太陽円（`weather_rendering.py`）も修正。
- 検定: `test_todo_circles_have_no_connector_lines`（PDFラスターで円の外へ延びない・見出しに触れないことを境界検定。旧実装でFAIL、修正後PASSを確認済み）、`test_consecutive_clearsky_glyphs_are_not_connected_by_stray_lines`。
- 見出しから最初の円までの絶対オフセットは、旧版フォントメトリクス由来の推定と約2pt差。視認への影響は小さく、4.3の実紙受入（8.4と同時）で最終確認する。
