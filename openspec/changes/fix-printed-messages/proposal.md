# Proposal

## Why

tangledのopen issueのうち、翻訳・紙面・表示文言の正しさに関わる4件（#5, #12, #14, #17）は、いずれも** gettext の翻訳カタログ（ja.po）と表示関数の守りが要因**ですわ。検証結果:

- **#5（技術情報）**: `weather_location_view.py:74`が`_(MESSAGES[State.READY])`＝`_("")`を呼び、gettextが**カタログヘッダー（Project-Id-Version…）を丸ごとUIへ返す**（issue #5のスクショに実出）。さらに天気領域で`ATTRIBUTION_SHORT`が`weather_lines()`（`weather_sheet.py:200`）と`application_view.py:305`の両方から出て**MET Norway帰属が二重**。
- **#12（無関係なメッセージ）**: `po/ja.po`でmsgid `"Extract unavailable."`（`osm_region_view.py:24`兜、地図領域の状態語）に誤って**「天気を取得しました。」**が入っている（ja.po内で確認済み）。地図側が失敗時に天気成功を読み上げる。
- **#14（TODO紙面崩れ）**: 冊子のTODO面で、checkboxの円からৃ斜めの支線が延び、見出しTODOにも線が重なる（issue #14のスクショ）。Android正本との比較待ち。
- **#17（ISO表記）**: `todo_reader.py:171`の`format_temporal`が`%Y-%m-%d %H:%M`を返し、タスク一覧に機械向reetけ表記が並ぶ。

## What's Changed

- gettextラッパーが**空文字列をそのまま返す**ようにする（`_("")`でカタログヘッダーがUIに出ない）。msgidが空の実データ経路も守りを一つに統一する
- 天気領域の帰属を**単一表示**にする（`application_view._weather_section`の付加ラベルを撤去し、`weather_lines()`の一か所に統合）
- **ja.poの全餐饮msgstr/msgid対の整合点検**を行い、「Extract unavailable.」等の誤訳を含む全てのねじれを修正する（#12入りはこの点検で一括STITUTE）
- タスク一覧などUI上の日時表示を、システムロケールに沿た人間向け表記（ja「2026年11月10日 17:30」、en「Nov 10, 2026, 5:30 PM」相当）に改める。内部データ・fixture・診断出力はISOのまま，にする
- TODO面の冊子組成はAndroid正本に追随：見出しTODO・checkbox・項目行の位置を正し、**不要な支線を消す**

対象外：#9（中央揃え、見送り）、#6,7,8,10,13,15,16,18（`rework-ui-dialogs`）。

## Capabilities

### New Capabilities

（なし）

### Modified Capabilities

- `desktop-application`：空文言と翻訳整合の保護、UI日時の人間向け表記、同一画面内での帰属の重複禁止を規格に追加する。
- `printable-booklet`：TODO面の組成（不要な支線の禁止、Android正本相当の配置）を規格に追加する。
