# Proposal

## Why

tangledのissue #2と#3が同じ原因——Android正本への準拠漏れ——に束ねられます。天気ブロックはMET Norwayのsymbolコード（`lightrain`）と「11時38分の 対象時刻」のような寄衆2列をそのまま載せるために already 使えない構造になり、表紙見出しはja.poがAndroidが翻訳しない「(NAME)'S PHONE」を「(NAME)の Paper Phone」へねじれていますわ。

## What's Changed

- 天気ブロックの表示をAndroid正本（`app/src/main/java/.../weather/WeatherModule.kt`）の組成へ揃える：
  - 時間帯見出し `MORNING/AFTERNOON/EVENING`（09/12/18時、Androidの`getTimeOfDay`同値）
  - `draw_weather_glyph` の19pt自作線画アイコンは維持
  - 冊子の39ptの組み立ては「見出し＋罫線＋アイコン＋気温＋人語天気description」の5層
- symbol codeを破棄し、人語descriptionへ置換する：
  - 語文書は「symbol code → 英語description」の索引で取得し、jaはgettextカタログで翻訳される
  - `day/night/polartwilight` 変異体は既存 `known_symbol` と同様にbaseへ正規化して引く
  - 未知代号は既存の「不明な天気」を表示する（天気コードを表示には出さない）
- 表紙見出しは翻訳をやめ、Android正本と同じ「(NAME)'S / PHONE」へ戻す
- 表紙・天気・時間帯に関わらない既存掃及、#1（Android資産除去：closed）、#4（絵文字：経路に実装なし）は本changeの対象外

## Capabilities

### New Capabilities

（なし）

### Modified Capabilities

- `weather-forecast`：予報表示のrequirementを更新する。symbolコード非表示と「人語description＋時間帯見出し」の組成を画面・冊子共通に明文化する（現行実装の`lightrain`直表示は現行規格違反の是正）。
- `printable-booklet`：表紙見出しのrequirementを追加する。見出しは翻訳を適用せず、既存の立体組（名前大文字＋`'S`、`PHONE`と日付）を維持する。

## Impact

- `src/paperphone/weather_sheet.py`（symbol code→descriptionの索引と `weather_lines` の組立）
- `src/paperphone/weather_rendering.py`（冊子天気ブロックの5層組み立てと罫線）
- `po/ja.po`・`po/paperphone.pot`（descriptionの追加訳、表紙見出し2項目の削除）
- `tests/test_weather_sheet.py`（索引・正規化・見出し切替の検定）
- 画面（GTK）と冊子（Cairo/Pango PDF）のいずれの表示にもfinished影響：生成済みPDFの作り直しが必要になる既定期望
