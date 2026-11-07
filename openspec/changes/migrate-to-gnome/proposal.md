# Proposal

## Why

外出前に必要な情報を紙にまとめる用途では、スマートフォンよりPC上での準備と印刷が自然である。Paper PhoneをGNOME向けに完全移行し、既存の折り畳み冊子を維持しながら、デスクトップの予定・TODO・連絡先とシステム設定を利用できるようにする。

## What Changes

- **BREAKING** Androidアプリの並行保守を終了し、GTK 4 / libadwaitaによるGNOMEネイティブアプリへ置き換える。Androidの画面構成を再現せず、GNOMEの操作・アクセシビリティ・設定・保存／印刷の作法に従う。
- 予定・TODO・連絡先はEvolution Data Server（EDS）から読み取る。ThunderbirdからCalDAVに登録したTODOも、EDSに登録されたタスクリストを介して利用する。独自同期や書き戻しは追加しない。
- TODOは未完了のみを「今日」「今後」「期限なし」に分類する。着手日時を優先し、「今日」に期限切れを含めて控えめに表示する。印刷対象は利用者が個別に選択する。
- 場所検索・地図・徒歩経路はGoogle Mapsを維持し、利用者自身のAPIキーを設定する。
- 天気はPCの現在地とMET Norway Locationforecast JSONの直接取得による短期予報を利用する。天気記号の対象期間を保持し、座標と応答のキャッシュはメモリ内に限定する。今日の9時・12時・18時を目安に選び、実際の予報対象時刻を印刷する。
- 日時処理はシステムのタイムゾーンに統一する。固定時差や日時文字列の切り貼りを廃止し、終日予定・日付だけの期限を保持する。
- A4横・8区画の折り畳み配置、必要時の追加地図ページ、表紙、写真、ドット方眼メモ、紙アプリ素材、カード用切り込みガイド、折り方の案内を引き継ぐ。PDF保存・プレビュー・印刷に対応する。
- 外部データの取得失敗を明示し、利用可能な内容だけでも出力できるようにする。英語と日本語を維持する。

## Capabilities

### New Capabilities

- `desktop-application`: GNOMEの操作、設定、現在地、配布と部分的な取得失敗への対応。
- `personal-information`: EDSの予定・連絡先・TODO読取、TODO分類、印刷対象選択、日時の意味の保持。
- `weather-forecast`: 現在地の短期予報取得、時刻選択と実時刻表示、出典表示。
- `walking-map`: 利用者のGoogle Mapsキーによる場所検索・徒歩経路・地図取得。
- `printable-booklet`: 既存冊子の内容と配置、プレビュー、PDF保存、印刷。

### Modified Capabilities

なし。既存の登録済み仕様はない。

## Impact

- `app/` のKotlin・XML・Android API、Gradle構成、AndroidのContentProvider・Room・印刷・位置情報連携を置換する。
- `ui/pdf/PdfGenerator.kt`、各 `*Module.kt`、`res/drawable-nodpi/`、フォント、英日文字列を移植の参照元とする。既存の未コミット変更を基準資料作成前に確認し、無断で破棄しない。
- GTK 4、libadwaita、EDS、HTTP/JSON取得、Cairo/Pango、PDFプレビュー、位置情報・印刷連携が新しい依存となる。libgweatherは先行実証の参照にとどめ、天気取得の実行時依存には含めない。Flatpakを配布の第一候補とし、ホストEDS・位置情報との連携を早期に実証する。
- Google Mapsの利用条件・課金・APIキー管理と、MET Norwayの出典・キャッシュ・アクセス条件を満たす必要がある。
- 対象外：Android版の継続開発／端末データの自動移行、OSM化、独自CalDAV同期、予定・TODO・連絡先の編集、長期・過去の天気、冊子の自由レイアウト編集。
