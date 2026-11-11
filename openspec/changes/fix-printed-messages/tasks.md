# Tasks

## 1. 空文言と翻訳の保護

- [x] 1.1 `i18n.gettext_for()`に空文字列をそのまま返す一元バリヤを設けて、空msgid経路がカタログヘッダーをUIへ返さないようにする。テスト: 空文字列の翻訳試験を`tests/test_i18n.py`等に追加し、天気READY状態でヘッダーが現れないことをモックで確認。
- [x] 1.2 `weather_location_view.py:46`の三項演算子を整理し、`MESSAGES[State.IDLE]`の空文言経路が単一バリヤで守られることを確認する。テスト: 既存の天気試験と全回帰。

## 2. 帰属の二重表示の統一

- [x] 2.1 `application_view._weather_section`の`ATTRIBUTION_SHORT`付加labelを撤去する。テスト: 天気領域にMET Norway帰属が1か所のみとなるa11y／モック確認。

## 3. ja.poの全監査と誤訳の是正

- [x] 3.1 `po/ja.po`の全msgstr/msgid対を走査して、横滑り・ねじれをリストし是正する（初期HR例：`Extract unavailable.→天気を取得しました。`）。テスト: `msgfmt`の通過、翻訳回帰試験（UI文言の英日同士）、`runtests.sh`全回帰。
- [ ] 3.2 実機確認: 天気成功・地図抽出不在の各状態語を画面で見て記録する（ Technician誤訳の丸つぶし）。

## 4. TODO面の幾何

- [ ] 4.1 TODO面の組成を旧Android版（`app/src/main`のTodoModule類）と比較し、見出し・丸・項目行の位置と、斜めの支線の原因を特定する。成果物: 比較記録（support:記録は個人情報を残さない）。
- [ ] 4.2 支線の除去と位置の揃え直しを実装する。テスト: PDF fixtureに支線の境界検定（円の外へ延びない、見出しに触れない）を追加し、既存TODO回帰を確認。
- [ ] 4.3 実機確認: 冊子PDFと実紙でTODO面の目視確認を行う（8.4の受入と同時に可）。

## 5. UI日時の人間向け表記

- [x] 5.1 `todo_reader.format_temporal`をロケールの表記へ改める（ja「2026年11月10日 17:30」相当、en相当）。テスト: ja/enの表記試験を追加、内部データ・PDF・fixtureがISOままのことを回帰で確認。
- [ ] 5.2 実機確認: Tasks一覧・期限つき表示を確認し記録する。

## 6. 全体

- [x] 6.1 `meson test`（VMのruntests.sh環境）での全回帰を確認する。
- [ ] 6.2 実機の検証記録をまとめ、tangled上で #5, #12, #14, #17 をクローズする（または利用者がクローズする）。
