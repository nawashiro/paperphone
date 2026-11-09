# 冊子天気ブロックの組版点検記録

## 生成環境

- テスト環境（docs/gnome-dependencies.mdのGTK 4.14自前プレフィックス手順）で`render_core_booklet`によりA4横向き冊子を生成。
- 合成データ: 氏名`Sample`、1件の予定、`cloudy`の予報3枠（09:30・12:30・18:30、各1時間）。個人データは含まない。
- 生成PDFの1ページをPopplerでPNG化して目視点検した（2026-11-09）。

## 点検結果

- 表紙見出しは「SAMPLE'S」「PHONE」の2段で、翻訳の適用を受けていない。下端に「02 January 2026」。
- 天気ブロックはAndroid正本どおりの5層で、予報3枠ごとに:
  - 時間帯見出し（MORNING／AFTERNOON／EVENING）と同等の罫線
  - 19ptの自作線画アイコン（雲）
  - 気温行「5 °C（09:30–10:30）」等の対象期間つきの短い時刻
  - 人語の天気説明「Cloudy」(for `lightrain`では「Light rain」と人が読める語になる)
  - 記号コード（`cloudy`等）は紙面のどこにも出ない
- 帰属は末尾に「MET Norway · CC-BY 4.0」の1行のみ。
- `render_core_booklet`の警告は空で、overflowや省略は発生しない。
- 折り畳み配置のためカレンダー区画は上下逆に印刷される（既存の折り畳み仕様どおり）。
