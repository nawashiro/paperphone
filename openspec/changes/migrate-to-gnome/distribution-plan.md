# 配布整備の草案（タスク8.3）

## 現時点の状態

配布形式はAppImageに決めた。Flatpakは配布候補から外し、ポータルの権限探求は配布要件から外した。既存の実績は[Flatpak EDS検証](<flatpak-eds-validation.md>)（EDS接続の参考記録）と[保存・印刷検証](<save-print-validation.md>)を参照。配布の成立そのものは書いていない。

## 暫定アプリID

現在の[main.py](<../../src/paperphone/main.py>)の`com.withgoogle.experiments.unplugged`は暫定値であり、上流のAndroid IDとは意図的に一致しない。製品IDの決定は利用者の判断であり、このセッションでは確定しない。ID未確定のため、desktopファイルやAppStreamの識別子も暫定のまま残す。

## 雛形

- desktopファイル: [paperphone.desktop.draft](<../../docs/paperphone.desktop.draft>)。アイコン指定は暫定であり、ICON名とアイコンの提供が確定するまで実装しない。
- アイコン: 現時点でアイコン素材が無い。GNOMEアイコンテーマへの依存、再配布時のアイコンライセンス・出典確認が必要。
- AppStreamビジネスメタデータ: 名前・説明・ライセンス・開発者・配布URLは、利用者の決定後に書き込む。
- AppImageビルド: [tools/build_appimage.py](<../../tools/build_appimage.py>)。Podman/DockerでUbuntu 22.04（glibc 2.35基準）を1コンテナ起動し、GTK 4.14以上・libadwaitaをソースから同梱プレフィックスへ組み、requirements.txt通りのwheelをPython 3.10のvenvに置き、`meson install --destdir` の出力をAppDirへ組み替えてappimagetoolで固める。必要なPython依存とGI機能の一覧は[docs/gnome-dependencies.md](<../../../docs/gnome-dependencies.md>)を参照する。
- 自動更新は作らない（AppImageUpdate等は後続判断）。

## クリーン環境での試験項目

クリーン環境はglibc 2.35相当（Ubuntu 22.04）を想定する:

- [tools/build_appimage.py](<../../tools/build_appimage.py>)の再実行で同じ構成のAppImageができること（サイズの実測を記録）
- AppImageのPythonから全GI読込・日本語フォールバックフォント・証明書・GSettingsの書込先が[docs/gnome-dependencies.md](<../../../docs/gnome-dependencies.md>)の機能一覧と一致すること
- 実ホストEDSアカウント（GNOMEオンラインアカウントまたはEvolution構成済み）からの読取。EDSサービス不在時は短い案内だけが表示されること
- 位置ポータル（XDG Location）の許可・拒否・タイムアウト処理
- AppImage実行からのPDF保存・印刷・実紙出力
- 実プリンターでのA4横100%出力と折り畳み確認

自動ではこれらは実施できない。記録の無い項目は完了へ移さず、制限として明記する。
