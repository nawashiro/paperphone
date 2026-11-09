# Proposal

## Why

現行UIはStack型の9〜10ページで構成され、ソース管理・設定・各データ一覧が前景を占め、フォーク元（紙の冊子を作る携帯アプリ）の「冊子の中身を選んで一気に紙にする」という一点の体験が消えている。移行の目的（PCで外出前の冊子を準備する）に対し、UIの再構築は実用性の主障害になっている。OSM軽量化（lighten-osm-pipeline）の後に行うことで、速度の土台を壊さずに面を作り替える。

## What Changes

- **BREAKING** 起動時の主画面を「冊子に載せる内容を選ぶ」画面に統合する。StackSidebarの9ページ構成を廃止し、選択・プレビュー・出力の単一フロー画面に置き換える。
- ソース一覧・設定・テーマ切替は画面の主役から外し、ヘッダーバーのボタンと専用の二次ダイアログ（設定・ソース・出力履歴）へ移す。
- 内容選択はカテゴリ（予定・TODO・連絡先・天気・徒歩地図・メモ・写真・紙アプリ）ごとの選択セクションにまとめ、カテゴリ内での連絡先・予定・TODOの分離と利用者ごとの選択は維持する。
- 出力（PDF保存・印刷）は主画面の終端で完結する行動とし、生成進捗・失敗の表示を同一の流れに置く。
- 冊子プレビュー（折り畳み配置の確認）を選択画面から直接開けるようにし、ページ分けはプレビューの役割とする。
- 部分的な取得失敗・状態語・再起動時の設定復元などの既存の振る舞いは引き継ぐ。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `desktop-application`: 「GNOMEに適した操作」を補足し、画面構成を「主画面は内容選択、設定とソースは二次」という要件として改める。「部分的な取得失敗」「設定の保存」「連絡先と予定とタスクの分離」の行き先を新しい画面構成に合わせて言い換える。

## Impact

- `src/paperphone/application_view.py` が主対象。StackSidebar・Stack・ページ追加の構造を置き換える。各カテゴリの選択ビュー（`contact_selection.py`・`todo_selection.py`・`weather_location_view.py`・`osm_region_view.py`・`place_selection.py`・`booklet_asset_view.py`）と予定・TODOの列挙（`calendar_reader.py`・`todo_reader.py`）は流用し、配置を替える。
- 英日文字列の表示は `values` 系の文言群と `i18n.py` の翻訳経路を引き継ぐ。キーボード操作・アクセシビリティは再確認が必要。
- 前提: `lighten-osm-pipeline` を先に適用済みとする（地図セクションの応答は派生データ照会のままUIに接し直す）。
