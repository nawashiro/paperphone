# GNOME版の構成・配布依存

対象はGNOME版のMeson構成とネイティブ配布で、Android/Gradleは変更しない。

## 必須依存

| 用途 | Python配布名・機能 | ネイティブ側の要件 |
|---|---|---|
| 既存UI | PyGObject、Gtk 4.0、Adw 1 | GTK 4、libadwaitaと各GI typelib |
| 既存予定解析 | icalendar、python-dateutil | [requirements.txt](<../requirements.txt>)の範囲 |
| キー保管 | PyGObjectのSecret 1 | libsecretとSecret-1.typelib、実行時のSecret Service |

Pythonパッケージは[requirements.txt](<../requirements.txt>)をネイティブ配布の依存入力にする。GIと証明書はpipだけでは揃うと仮定しない。ディストリビューションのパッケージ名は異なるため、上記の機能とGI名前空間で確認する。

## 開発環境と構成検査

```sh
python3 -m pip install -r requirements.txt
meson setup build-gnome
meson test -C build-gnome --print-errorlogs
```

[meson.build](<../meson.build>)は選択されたPythonでGTK 4・libadwaitaのGI読み込みと予定解析ライブラリを検査し、使えなければ日本語エラーで構成を停止する。

libsecretのSecret GIは依存解決が通っても、利用者セッションでSecret Serviceが起動・解錠できる保証ではない。実行時の保管先利用不能はキー管理側で明示エラーとし、平文保存に切り替えない。

## Flatpak候補

現時点で製品用Flatpak manifestは存在しない。[既存の最小権限検証](<../openspec/changes/migrate-to-gnome/flatpak-eds-validation.md>)を維持し、最終manifest作成時には上記Python依存とlibsecret/Secret typelibをランタイム内へ含め、同じMeson検査を通す。ホストのインストールだけで充足したとみなさない。Secret Serviceへの必要な接続権限と実セッションでの保管も別途検証する。ホーム全体やセッションバス全開放を依存解決策にしない。

この文書は配布依存の入力であり、未作成manifestのビルド成功・最小権限でのキー保管成功を報告するものではない。
