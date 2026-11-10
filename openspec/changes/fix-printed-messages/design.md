# Design

## 骨子

原因のほとんどは**翻訳回りの二つの破れ目**（空文字列のgettext、ja.poの手改行期の誤植）と、** Titanicの確認漏れ**（TODO幾何・帰属の重複）。実現物は最小の差分で、回帰テストを担保する。

## 空文言の保護（#5前半）

- `i18n.gettext_for()`は`gettext.translation(...).gettext`をそのまま返す。ここを包んで、空文字列とNoneは翻訳しないでそのまま返す一元ラッパーにする:
  ```python
  raw = translation.gettext
  return lambda message: raw(message) if message else ""
  ```
- `weather_location_view.py:46`の三項演算子（`_("") if MESSAGES[State.IDLE] else ""`）は一元ラッパーにより不要となり、`_(MESSAGES[State.IDLE])`に戻せる。**ナルーは単一関数に**置いて回帰を回避（他の`_("")`経路検索も手動確認）。
- 既にtangled #5のスクショはREADY状態でヘッダーが出ており、`weather_location_view.py:74`が原因であることを突き止めている。テストは`tests/test_i18n.py`等に「空文字列を渡すと空文字列が返る」を追加。

## 帰属の二重（#5後半）

- `weather_lines()`（`weather_sheet.py:200`）は末尾に`sheet.attribution`を入れていて、`application_view._weather_section()`（:305）が更に`ATTRIBUTION_SHORT`のlabelを積む。
- 方策: `_weather_section()`の付加labelを削除する。天気領域の帰属は`weather_lines()`（つまり`forecast_details`）の1か所に統一。翻訳経路・msgidは変更しない。

## 誤訳の点検（#12＋ja.po全監査）

- 手に対物: xgettext 0.21がf-string内の`_()`を抽出できないたびの手動補完で、ja.poの一部がmsgidと対のズレを起きたと判明している。実判明済みの最低一例「Extract unavailable.→天気を取得しました。」のほか、**全msgstr/msgid対を走査**して判読する。
- 方策:
  1. 現行msgid群と対のmsgstrを機械的に列挙し、翻訳者の目でペアごとに見另一位（私が実施）
  2. 明らかな別機能（天気・地図・カメラ等）の文言の横滑りをリストし、正す
  3. 修正後は`msgfmt`と全テスト（`runtests.sh`）で再ビルド
- ポーリングの明健身はドキュメントに置かない（作業の保守はpoの差分チャkraftで十分）。

## TODO面の幾何（#14）

- issue #14のスクショ（チェック丸から斜めの支線が顔見出しTODOに触る、見出しTODOに線が重なる）から、支線は checkbox円の連結線が連続反転やセル毎に印作曲されて出来ている可能性が高い。`todo_sheet`/`booklet_content`側の幾何を確認し、**Android正本の`TodoModule`の座標に揃え直す**。
- 比較の型: 既存のPDF fixture（tests）に支線の座標境界の検定（円の半径から矩形の外へ延びない）を追加し、Android正本との紙面比較は8.4の実紙受入で最終確認する。

## 人間向け日時（#17）

- `todo_reader.format_temporal`（`todo_reader.py:169-172`）の改修。方策:
  - ja: `%Y年%m月%d日 %H:%M`、en: `%b %d, %Y, %I:%M %p`相当。実装は素の`strftime`の2分岐（複数言語の拡張はしない）
  - 内部データ（`booklet_content`、JSON保存の`isoformat`）、PDFの紙面（当日冊子は既に時間のみの表示）は対象外
- テストは`tests/test_todo_reader.py`等にja/enのQWidget表記の試験を加える。
