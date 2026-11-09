# Design

## Context

天気表示は `weather_sheet.py` の `weather_lines()`（画面・冊子共通）と `weather_rendering.py` の `compact_weather_lines()`（冊子）が、MET Norwayの記号コードをそのまま行として出している。表紙は `booklet_content.py` の `cover()` が正しく「(氏名'S)/PHONE」を組み立てているが、`ja.po` が `{name}'S` と `PHONE` に訳を付けているため日本語環境で「(氏名)の/Paper Phone」になる。正本はAndroid `WeatherModule.kt`／`FrontModule.kt`。

## Goals / Non-Goals

**Goals:**
- 冊子の天気ブロックをAndroid正本の5層構成（時間帯見出し・罫線・19ptアイコン・気温・天気説明）へ組み立て直す
- 記号コードを人語の天気説明へ置き換え、説明はgettextカタログで日英両方に追従させる
- 表紙見出しから翻訳を外す

**Non-Goals:**
- Android資産の除去（issue #1、別タスク）
- 絵文字フォントの同梱（issue #4、天気経路に絵文字の描画は存在しないので対象外）
- OpenWeatherMap互換のdescription表への揃え（MET Norwayの記号体系のまま）
- 徒歩地図・予定など他紙面ブロックの見直し

## Decisions

- **辞書はgettextのmsgidとして英語descriptionを保持する**: `SYMBOL_DESCRIPTIONS` を基の記号コード → 英語説明の対応表として `weather_sheet.py` に置く。表示時は `_()` を通すので、ja.poに訳を足すだけで両言語が1機構で完結する。ハードコードした日英2面の表を置く案よりも、翻訳経路が既存の `weather_lines()` と同一である点、`ja.po` 読み込み済みのプロセスで追加ファイルが不要な点を採用根拠とする。
- **変異体の正規化は `known_symbol()` と同じ規則で行う**: コードの末尾が `_day/_night/_polartwilight` の場合はbaseコードに落として辞書を引く。昼夜で説明を変えないことを仕様どおり1箇所で担保する。
- **時間帯見出しはAndroid同値の切替**: 実際の対象時刻から09:00未満・09:00-12:00・12:00-18:00・18:00以降に分け、`MORNING/AFTERNOON/EVENING`（空欄を含むAndroidの`getTimeOfDay`を踏襲しつつ、冊子では空欄を挟まず詰める）。msgidとするのでjaで「朝/昼/夕方」に翻訳できる。
- **画面の行は「瞬間気温＋天気説明＋対象期間」を保つ**: `気温の瞬間値と期間の天気を区別する`（SHALL）仕様を維持し、期間表示は1行の簡潔な形に整える。冊子は紙面に合うよう気温＋天気説明に絞る。
- **表紙の修正はja.poの2項目削除で行う**: `cover()` のソースは正しいので、`{name}'S` と `PHONE` の訳をカタログから外し、msgidが表示される状態に戻す。`POTFILES` とpotの再生成は既存のビルド手順に従う。

## Risks / Trade-offs

- [ met.noの記号体系（40コード弱）の日本語訳の語彙寄り味] → 訳語は「小雨」「にわか雨」等の気象庁で一般的な語を選び、訳の根拠をja.poのコメント行に添える。不明が残るコードは「不明な天気」で安全側へ落とす。
- [ 冊子のレイアウト変更でoverflow警告の閾値が変わる ] → `_draw_compact_body` のoverflow検出と警告（`text_ellipsized`）の流用は変えず、ブロック組み立てだけを置き換える。既存の`test_weather_sheet.py`の追加点で枠外省略の挙動を固定する。
- [ potの再生成漏れ ] → タスクで`po/paperphone.pot`の更新と`msgfmt`の検証を1手順として束ねる。

## Migration Plan

コード内の表示経路だけの変更で、データ移行や外部契約の変更はない。既存の生成済みPDFは該当紙面を作り直す。ロールバックはdevブランチへのrevertで足りる。

## Open Questions

なし。辞書の訳語は実装時にMET Norwayの公式シンボル名を確認して確定する。
