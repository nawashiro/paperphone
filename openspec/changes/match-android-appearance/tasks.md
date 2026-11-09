# Tasks

## 1. 記号コード辞書と行の組み立て

- [x] 1.1 `weather_sheet.py`に`SYMBOL_DESCRIPTIONS`（基コード→英語descriptionのmsgid）を追加し、`day/night/polartwilight`変異体をbaseへ正規化して引く-helperを`known_symbol`と同じ規則で作る。検証: `tests/test_weather_sheet.py`に変異体が基と同一説明になる検定を追加して通す
- [x] 1.2 `weather_lines()`を時間帯見出し＋気温＋天気説明＋対象期間の人語行に組み替え、`compact_weather_lines()`を気温＋天気説明に絞る。検証: 行に`lightrain`等の記号コードが出ないことをテストで確認
- [x] 1.3 未知のコードが「不明な天気」へ落ちる既存挙動を辞書経路でも維持する。検証: 未知コードの場合のpytest追加

## 2. 翻訳カタログ

- [x] 2.1 MET Norway公式シンボル名に基づき、辞書の英語msgidを確認して`ja.po`相当の日本語訳（`po/paperphone.pot`と`po/ja.po`）に追加する。訳語の根拠をpoのコメントに添える。検証: `msgfmt --check po/ja.po`と、日本語環境で`weather_lines()`の説明が日本語になるpytest
- [x] 2.2 `ja.po`の`{name}'S`と`PHONE`の2項目を削除する。検証: 日本語環境で`cover()`の出力が「(氏名)'S / PHONE」になるpytest（`booklet_content`の検定に追加）
- [x] 2.3 `po/POTFILES.in`・`pot`の再生成と`msgfmt`検証をまとめて通す

## 3. 冊子の天気ブロック

- [x] 3.1 `weather_rendering.py`の`_draw_compact_body`をAndroid正本の5層構成（時間帯見出し・罫線・19ptアイコン・気温・天気説明）へ組み替え、overflowの検出と`text_ellipsized`警告の流れは維持する。検証: 予報3枠での描画がoverflowせず、警告経路が発火しないことをpytestで確認
- [x] 3.2 生成PDFを実際に作り、表紙見出し・天気ブロックを目視で確認する。検証: 保存したPDFページの点検記録

## 4. チェックアウト

- [x] 4.1 全pytestと`openspec validate --changes`を通す。検証: CI相当のコマンド一式が緑
