# 素材・ライセンス・文字列の移行台帳

このディレクトリは、GNOME版がパッケージに同梱する再利用素材の置き場です。読み込みは `paperphone.resources` の `importlib.resources` 経由とし、Android固有のリソースIDには依存しません。

## 画像・フォント

| 素材 | Android版の移植元 | GNOME版の格納先 | ライセンス／対応 |
|---|---|---|---|
| 紙アプリ画像13点（`anagram`、`conversion_charts`、`italian_phrasebook`、`knots`、`maze`、`multiplication_table`、`pigeon_origami`、`recipe`、`riddle`、`sign_language`、`sudoku`、`typography`、`word_of_the_day`） | `app/src/main/res/drawable-nodpi/*.png` | `paper-apps/*.png` | 上流Paper Phoneリポジトリのルート`LICENSE`はApache-2.0。個別画像の出典・例外記載はファイル内に見つからなかった。配布時はルートライセンスを維持し、権利者・特別条件の追加確認を続ける。移植画像は元ファイルから無変更で複製。 |
| Varela Regular | `app/src/main/res/font/varela_regular.otf` | `fonts/varela_regular.otf` | SIL Open Font License 1.1。著作権表示: Joe Prince / Admix Designs, 2011。Reserved Font Name: “Varela”。`licenses/OFL-Varela.txt`を同梱。フォントを変更した派生物は予約名条件に従う。 |
| Work Sans Regular | `app/src/main/res/font/worksans_regular.ttf` | `fonts/worksans_regular.ttf` | SIL Open Font License 1.1。ライセンス表示: Work Sans Project Authors, 2019。元ファイルのメタデータにはWei Huang, 2015の著作権情報もある。`licenses/OFL-Work-Sans.txt`を同梱。 |

フォントライセンスの上流参照先:
- https://github.com/google/fonts/blob/main/ofl/varela/OFL.txt
- https://github.com/google/fonts/blob/main/ofl/worksans/OFL.txt

## Python実行時依存

- 予定のiCalendar解析・繰り返し展開には`requirements.txt`の`icalendar`（BSD-2-Clause）と`python-dateutil`（BSD／Apache-2.0デュアルライセンス）を使う。`six`（MIT）は`python-dateutil`の依存として導入される。
- Flatpak等の配布物には依存パッケージのライセンス文書も含める。最終的な取得方法と同梱内容はタスク8.3で再確認する。

Paper Phoneのリポジトリ自体:
- https://github.com/specialprojects-experiments/paperphone
- 同リポジトリの`LICENSE`はApache License 2.0。第三者フォントのライセンスは各OFLファイルを優先する。

## 移植しないAndroid素材

- `app/src/main/res/drawable/ic_*.xml`のAndroid VectorDrawable天気記号・UI図形はGTK用素材ではない。天気状態はGNOMEアイコンテーマ／新しい描画資源へ対応付ける。
- `app/src/main/res/raw/{loading,selecting,smiling}.json`と折り方用AndroidアニメーションはGNOME版へコピーしない。出典・再配布表示を個別確認できないため、必要な操作案内はGTKネイティブな表示に置き換える。
- `190807PaperPhoneStills295-Edit.jpg`はREADME掲載用の写真。メタデータに Colin Ross / Special Projects Studio Ltd. の著作権表示がある。GNOMEアプリの同梱素材にはせず、README掲載継続も別途許諾を確認する。
- 利用者が選ぶ写真はアプリ同梱素材ではない。利用者のローカルファイルを一時的に読む設計とする。

## 英語・日本語文字列

- 英語の原文はGTK/Python側の`_()`呼び出し。gettextドメインは`paperphone`。
- 日本語訳と旧Android `values/strings.xml` / `values-ja/strings.xml` の45キーは`po/ja.po`へ移行済み。各旧キーはPOコメントに残す。紙アプリ名13件も同カタログに含む。
- `po/LINGUAS`と`po/POTFILES.in`をMeson gettextに接続。英語は原文へフォールバック、日本語は`PAPERPHONE_LANGUAGE=ja`またはシステムロケールで選択する。
- カタログの更新は`meson compile -C <build-dir> paperphone-pot`と`meson compile -C <build-dir> paperphone-update-po`を使う。既存の日本語訳を確認してから更新する。

## 配布時の確認

Apache-2.0の`LICENSE`と上記OFLライセンスファイルを配布物に含める。紙アプリ画像に個別の第三者帰属表示が必要か、GNOMEアプリID・配布条件確定前に再確認する。マーケティング写真、出典未確認のLottie JSON、Android UI図形はアプリへ移さない。
