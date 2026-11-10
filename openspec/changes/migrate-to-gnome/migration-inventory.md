# 移行対象・保存対象の対応表

調査日: 2026-11-07  
基準ブランチ: `dev`

## 既存差分と保存方針

- `git status` で約150件の追跡済みファイルが変更扱いになっている。`git diff --ignore-space-at-eol --exit-code` は差分なしで終了したため、確認できた変更は内容ではなく改行コード（LF→CRLF）のみ。ステージ済み差分はない。
- 追跡外の `.agents/`、`openspec/`、`app/release/app-release.apk` と同ディレクトリの出力メタデータは既存物として保持する。`app/build/` は生成物であり、移植元や基準PDFとは扱わない。
- `keys.properties` と `local.properties` はgitignore対象。値を読んだり、記録・移動したりしない。リリースAPKに鍵等が含まれるかは別途確認が必要。
- リポジトリ内に比較用PDFは見つからなかった。移行前にAndroidアプリから匿名化PDFを作れるか確認し、できなければ座標・素材を基にした代替基準を作って制約を明記する（タスク1.2）。
- Androidソース、素材、履歴はGNOME版の機能移植・比較資料・ライセンス確認が終わるまで削除しない。

## 機能・実装元の対応

| 既存機能 | 参照元 | GNOME版で引き継ぐ内容／注意 |
|---|---|---|
| 表紙の名前・日付 | `ui/pdf/FrontModule.kt`、`ui/NameSettingActivity.kt`、`ui/HomeActivity.kt` | 選択した名前、日付、表紙見出し。GNOME版ではロケール対応の描画に置換。 |
| 予定 | `data/integrations/calendar/`、`ui/calendar/`、`ui/pdf` 内 `CalendarModule.kt` | 今日に重なる予定と時刻を個別選択し、終日・TZID・繰返し例外を含む意味を保持。現状の紙面は最大6件。 |
| 連絡先 | `data/integrations/contacts/`、`ui/contacts/`、`ui/contacts/ContactsModule.kt` | 氏名・電話番号の個別選択。現状は氏名と番号を印刷。 |
| TODO | `data/integrations/tasks/`、`ui/tasks/`、`ui/tasks/TasksModule.kt` | TODOを選択して印刷。現状の紙面はチェック丸、件名、相対期限と空欄。AndroidのGoogle Tasks連携はGNOME版のEDS読取に置換。 |
| 天気 | `data/integrations/weather/`、`ui/weather/` | 場所・日付・予報枠・気温・状態・記号。現行OpenWeatherと時刻選択は移植せず、仕様どおりlibgweather/MET Norwayに置換。 |
| 地図・徒歩経路 | `ui/maps/MapsActivity.kt`、`ui/maps/MapsModule.kt`、`data/integrations/maps/` | 出発地・目的地、経路、地図、地点表示を追加ページに掲載。現在地取得／場所選択UIとGoogle API方式はGNOME版の仕様に合わせて置換。 |
| 写真 | `ui/photos/PhotosActivity.kt`、`ui/photos/PhotoModule.kt` | 利用者が選んだローカル写真を冊子区画に配置。現行描画は白黒化し、縦横比に応じて縮小・回転。 |
| ドット方眼メモ | `ui/notes/NotesActivity.kt`、`ui/notes/NotesModules.kt` | 入力文ではなく、冊子上に手書きするドット方眼。 |
| 紙アプリ | `ui/paperapps/PaperAppList.kt`、`ui/paperapps/PaperAppModule.kt`、`ui/pdf/PlaceHolderModule.kt` | 下表の13素材から1つ選択し、1区画に配置。未選択時はドット方眼の空欄を維持。 |
| カード用切り込みガイド | `ui/contactless/ContactlessModule.kt`、`ui/contactless/ContactlessActivity.kt` | 主ページ上へ破線の斜線4本を重ねる。紙アプリが未選択の場合は既存実装でその区画のドットを抑止。重なり位置と競合動作を比較資料で確認する。 |
| 折り方の案内 | `ui/print/FoldingInfoActivity.kt`、`res/layout/activity_folding.xml`、`res/raw/smiling.json`、`res/values*/strings.xml` | 印刷後に見られる折り方説明・アニメーション・完了操作をGNOME向けに再構成。主ページにも折り目用の目印がある。 |
| PDF生成・プレビュー・保存・印刷 | `ui/pdf/PdfGenerator.kt`、`ui/print/PaperPhoneDocumentAdapter.kt`、`ui/print/PdfActionSelectionActivity.kt` | A4横の主ページと地図追加ページ、保存・プレビュー・印刷。GNOME版では同一の不変PDFスナップショットを各出力で再利用する。 |
| 名前・言語・ヘルプ等 | `ui/`、`res/layout/`、`res/values/strings.xml`、`res/values-ja/strings.xml` | GNOMEネイティブUIへ置換。英日文字列と機能説明を対応付け、Android固有画面はそのまま移植しない。 |

## 主ページ8区画の位置対応

根拠: `ui/HomeActivity.kt` のPDFモジュール配列順と `ui/pdf/PdfGenerator.kt` の `moveToNext`。座標はページ余白（28pt）を平行移動する前の局所原点、区画サイズは154×242pt。上段のy=0、下段のy=300pt。インデックス3と7のxは632pt。

| 配列 index | 局所座標 (x, y) pt | 現行内容 | 180度回転 |
|---:|---:|---|---|
| 0 | (0, 0) | 連絡先 | あり |
| 1 | (212, 0) | 予定 | あり |
| 2 | (420, 0) | TODO | なし |
| 3 | (632, 0) | 写真 | なし |
| 4 | (0, 300) | ドット方眼メモ | なし |
| 5 | (212, 300) | 表紙（名前・日付） | なし |
| 6 | (420, 300) | 天気 | なし |
| 7 | (632, 300) | 紙アプリまたは空欄 | なし |

- 未選択区画は既定でドット方眼のプレースホルダー。カード用ガイドが選ばれて紙アプリがない場合は既存動作により区画7のドットを描かない。
- 紙面には区画の境界線を通常描かない。主ページの折り目目印は `PdfGenerator.drawFoldingHints` が局所座標 (601, 0) と (601, 537) に5ptの黒い印を描く。
- カード用ガイドはページ原点を基準に、余白を相殺して次の破線を描く: (650,358)→(688,320)、(785,320)→(823,358)、(650,541)→(688,580)、(785,580)→(823,541)。冊子素材・区画との相対位置を基準PDFで確認する。

## 追加地図ページ

`MapsModule` が選択された場合のみページを追加する。局所ページ内で経路入りGoogle地図、出発地・目的地の住所、A/Bの丸印、ドット欄、ページ両端の折り目印を持つ。紙面の地図帰属表示・印刷時の可読性は現状ソースだけでは保証できず、GNOME版で明示的に満たす。

## 素材台帳（移植候補）

| 種別 | 移植元 | 対象／取り扱い |
|---|---|---|
| 紙アプリ画像（13点） | `app/src/main/res/drawable-nodpi/` | 同名PNGを`src/paperphone/resources/paper-apps/`へ無変更で複製。上流リポジトリのルートライセンスはApache-2.0だが、画像ごとの権利表示・由来は不明のため継続確認。 |
| PDF用フォント（2点） | `app/src/main/res/font/` | `src/paperphone/resources/fonts/`へ複製。VarelaはJoe Prince / Admix Designs (2011)、Work SansはWork Sans Project Authors (2019)のSIL OFL 1.1。各OFL全文を`resources/licenses/`に同梱。 |
| 天気記号（7点） | `app/src/main/res/drawable/` | `ic_sunny.xml`、`ic_sun_cloud.xml`、`ic_cloudy.xml`、`ic_rain.xml`、`ic_thunder.xml`、`ic_snow.xml`、`ic_wind.xml`。MET Norwayの未知状態への対応は流用せず、新しい状態マッピングを行う。 |
| 折り方／導入アニメーション | `app/src/main/res/raw/`、`app/src/main/res/drawable/` | `smiling.json`、`selecting.json`、`loading.json`、`ic_folding_animation.xml`。出典・再配布表示を確定できないためGNOME版へコピーせず、案内内容をGTKネイティブに再構成する。 |
| アプリ画像・UIベクター | `app/src/main/res/drawable/`、`app/src/main/res/mipmap-*` | Android VectorDrawableはそのまま移さない。天気記号はGNOMEアイコンテーマ等に置換し、ランチャーアイコンは`mipmap-xxxhdpi/ic_launcher.png`を`data/dev.nawashiro.paperphone.png`へ引き継ぐ。出典は紙アプリPNGと同じ未確定扱いで、配布時に確認する。 |
| プロジェクト紹介写真 | `190807PaperPhoneStills295-Edit.jpg` | READMEの紹介画像。JPEGメタデータに Colin Ross / Special Projects Studio Ltd. の撮影著作権表記あり。紙面機能の写真素材ではなく、GNOMEアプリ同梱は別途許諾確認。 |
| 英語・日本語文字列 | `app/src/main/res/values/strings.xml`、`app/src/main/res/values-ja/strings.xml` | 英語原文・日本語45キーをgettextの`po/ja.po`へ移行済み。紙アプリ名13件も同カタログに追加。`po/LINGUAS`、`po/POTFILES.in`、Meson gettextで管理する。 |
| ライセンス | `LICENSE`、`src/paperphone/resources/licenses/` | リポジトリのApache-2.0とフォントごとのOFL 1.1を台帳化し、OFL全文を同梱。紙アプリPNGの個別由来・写真・アニメーションの扱いは`src/paperphone/resources/README.md`に未確定事項として明記。 |

## 調査範囲と未確定事項

機能表は `HomeActivity` の出力モジュール配列、全PDFモジュール、地図ページ、カードガイド、折り方画面を確認して作成した。比較PDF未発見のため、区画位置・素材の見た目・ガイドの折り畳み後の一致はまだ実物比較していない。素材の著作権・ライセンスと実際のPDF出力条件はタスク1.2および1.4で検証する。
