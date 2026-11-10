# 配布整備の記録（タスク8.3）

## 現時点の状態

配布形式はAppImageに決めた。Flatpakは配布候補から外し、ポータルの権限探求は配布要件から外した。既存の実績は[Flatpak EDS検証](<flatpak-eds-validation.md>)（EDS接続の参考記録）と[保存・印刷検証](<save-print-validation.md>)を参照。配布の成立そのものは書いていない。

## 確定事項（2026-11-10、利用者の決定）

- アプリID: `dev.nawashiro.paperphone`。`src/paperphone/main.py`の`APPLICATION_ID`と、desktop・metainfo・アイコンの識別子。
- 連絡先: `nawashiro@nawashiro.dev`。AppStreamの`update_contact`に格納した。MET Norway用の連絡先はアプリ設定で入れる項目のまま。
- 配布は当面しない。自動更新は作らない（AppImageUpdate等は後続判断）。

## 整備済みのファイル

desktop・アイコン・AppStreamメタデータは`data/`に置き、`meson install`が`share/applications`、`share/metainfo`、`share/icons/hicolor/192x192/apps`へ入れる。アイコンは本リポジトリの`mipmap-xxxhdpi/ic_launcher.png`を無変更で複製したもので、出典は紙アプリPNGと同じ未確定扱い（[migration-inventory.md](<migration-inventory.md>)の素材台帳）。

- desktop: `data/dev.nawashiro.paperphone.desktop`
- AppStream: `data/dev.nawashiro.paperphone.metainfo.xml`
- アイコン: `data/dev.nawashiro.paperphone.png`

- AppImageビルド: [tools/build_appimage.py](<../../tools/build_appimage.py>)。Podman/DockerでUbuntu 22.04（glibc 2.35基準）を1コンテナ起動し、GTK 4.14以上・libadwaitaをソースから同梱プレフィックスへ組み、requirements.txt通りのwheelをPython 3.10のvenvに置き、`meson install --destdir` の出力をAppDirへ組み替えてappimagetoolで固める。必要なPython依存とGI機能の一覧は[docs/gnome-dependencies.md](<../../../docs/gnome-dependencies.md>)を参照する。
## クリーン環境での試験項目

クリーン環境はglibc 2.35相当（Ubuntu 22.04）を想定する:

- [tools/build_appimage.py](<../../tools/build_appimage.py>)の再実行で同じ構成のAppImageができること（サイズの実測を記録）
- AppImageのPythonから全GI読込・日本語フォールバックフォント・証明書・GSettingsの書込先が[docs/gnome-dependencies.md](<../../../docs/gnome-dependencies.md>)の機能一覧と一致すること
- 実ホストEDSアカウント（GNOMEオンラインアカウントまたはEvolution構成済み）からの読取。EDSサービス不在時は短い案内だけが表示されること
- 位置ポータル（XDG Location）の許可・拒否・タイムアウト処理
- AppImage実行からのPDF保存・印刷・実紙出力
- 実プリンターでのA4横100%出力と折り畳み確認

自動ではこれらは実施できない。記録の無い項目は完了へ移さず、制限として明記する。
