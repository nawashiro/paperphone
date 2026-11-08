# GNOME版の構成・配布依存

対象はGNOME版のMeson構成とネイティブ配布で、Android/Gradleは変更しない。

## 必須依存

| 用途 | Python配布名・機能 | ネイティブ側の要件 |
|---|---|---|
| UI | PyGObject、Gtk 4.0、Adw 1 | GTK 4.14以上（FileDialog、Gtk.Accessible.announce）、libadwaitaと各GI typelib |
| 既存予定解析 | icalendar、python-dateutil | [requirements.txt](<../requirements.txt>)の範囲 |
| PDF描画・画像・検証 | pycairo、PyGObjectのPango/PangoCairo/PangoFc/Poppler/GdkPixbuf | Cairo、Pango/Fontconfig、Poppler GLib、GdkPixbufと各GI typelib（ディストリビューション提供のGI/Cairo連携を含む）。日本語用のNoto Sans CJK JP等のフォールバックフォントも必要 |
| ローカルOSM検索・幾何 | osmium、shapely、RapidFuzz | 同じPython ABIに対応するwheelまたは対応ネイティブライブラリ。別Python版のバイナリパッケージを混用しない |

Pythonパッケージは[requirements.txt](<../requirements.txt>)をネイティブ配布の依存入力にする。GIと証明書はpipだけでは揃うと仮定しない。ディストリビューションのパッケージ名は異なるため、上記の機能とGI名前空間で確認する。

## 開発環境と構成検査

```sh
python3 -m venv --system-site-packages .venv-gnome
.venv-gnome/bin/python -m pip install -r requirements.txt 'meson>=1.6,<2'
export PATH="$PWD/.venv-gnome/bin:$PATH"
meson setup build/gnome
meson compile -C build/gnome
meson test -C build/gnome --print-errorlogs
meson install -C build/gnome --destdir build/stage
```

[meson.build](<../meson.build>)は選択されたPythonでGTK 4・libadwaitaのGI読み込み、予定解析ライブラリ、OSM検索ライブラリ、PDF・画像関連GIを検査し、使えなければ日本語エラーで構成を停止する。テスト環境ではGTKのdconf書込を避けるため`GSETTINGS_BACKEND=memory`を使う。

## 動作確認（一般環境）

自動テストは偽の依存・偽のダイアログだけを使う。実のGNOME環境での起動・ホストEDS読取・位置情報・地図保存・印刷・Flatpakは未検証であり、追加の利用者・環境確認が必要である：

- セッションで`meson compile -C build/gnome`後の実起動と、設定の名前・言語・MET Norway連絡先の入力確認。
- 既に構成済みのEDSアカウントやCalDAVタスクリストからの読取。
- 位置ポータル・許可後の天気取得（精度の上限は設けない）。座標・例外詳細の非保存を含む。
- movisdaからのPBF取得・更新（数百MB）、オフラインの地名検索・徒歩経路。
- PDF保存と宅内プリンターでのA4横向き100%印刷、実際の用紙・倍率・折り畳み確認。両面綴じ方向は機種依存のため手順通りに確認が必要。

実環境での記録との一致・不具合内容は利用者の環境・情報提供が前提であり、この文書では成功を宣言しない。

Google MapsからローカルOSM方式へ変更したため、地図機能のAPIキー管理とlibsecret依存は不要となった。既存の地理処理用Python 3.13環境とGTK用Python 3.15環境のパッケージを混用せず、上記の共通環境を使用する。

## 設定と個人が持つデータの保存先

- **設定**： アプリは表紙の名前・MET Norway用の連絡先・選択中の情報ソースと項目・紙アプリ等の選択を、`$XDG_CONFIG_HOME/paperphone/settings.json`に0600で原子的に書込む（[application_view.py](<../src/paperphone/application_view.py>)の`settings_file`と`save_settings_file`）。取得した連絡先・写真・地名そのものは保存しない。テストや開発環境では`PAPERPHONE_CONFIG_DIR`で保存先を切替えられる。設定の書き出しと復元は[tests/test_persistence.py](<../tests/test_persistence.py>)が担保する。
- **整合**： `ItemKey.opaque_value()`／`from_opaque_value()`が選択の安定書式を持つ。保存や復元で名前・電話番号を文字列として含めない。

## Flatpak候補

現時点で製品用Flatpak manifestは存在しない。[既存の最小権限検証](<../openspec/changes/migrate-to-gnome/flatpak-eds-validation.md>)を維持し、最終manifest作成時には上記Python依存と必要なGI/Cairo連携をランタイム内へ含め、同じMeson検査を通す。ホストのインストールだけで充足したとみなさない。ホーム全体やセッションバス全開放を依存解決策にしない。

この文書は配布依存の入力であり、未作成manifestのビルド成功を報告するものではない。保存・印刷を含む最終配布環境での確認はタスク8.3で行う。上記の一般環境での追加試験は、実環境・実プリンター・利用者同意のあるデータ取得のために必要である。
