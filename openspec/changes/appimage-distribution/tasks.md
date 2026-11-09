# Tasks

## 1. AppImageビルド手順

- [ ] 1.1 Ubuntu 22.04ベースのビルドコンテナに、GTK 4.14以上・libadwaita・その他ネイティブ依存の同梱ビルドと `meson install --destdir build/stage` 出力のAppDir変換を一続きのスクリプトとして整える。テスト: スクリプトの再実行で同じ構成のAppImageが作られる。
- [ ] 1.2 `requirements.txt` 通りのPython同梱とGI接続（`gi.require_versions` と Cairo/Poppler類）をAppDir内で検査し、docs/gnome-dependencies.md の機能一覧と照合する。テスト: AppImageのPythonから全GI読み込みが成功する。
- [ ] 1.3 同梱する日本語フォールバックフォントと証明書、GLibスキーマのコンパイル済み配置を検査する。テスト: AppImage起動時にGSettingsへの書込がアプリ設定の専用ファイルに矛盾しない。

## 2. 実行時の接続と案内

- [x] 2.1 ホストEDSのD-Busサービス不在を短い案内に分類し、技術詳細を主画面に出さない。テスト: サービス不在を模擬した環境での表示を確認する。
- [ ] 2.2 既存の位置ポータル・保存・印刷経路がAppImage実行で機能することを、ホストセッションで確認する（実確認の記録を残す）。

## 3. 配布文書の整備

- [x] 3.1 `distribution-plan.md` をAppImage基準に書き替え、ビルド手順への参照とクリーン環境試験項目を更新する。確認: 手順どおりにスクリプト・試験が実行できる。
- [x] 3.2 `docs/gnome-dependencies.md` のFlatpak節をAppImage構成に置換する。確認: 记載の機能一覧と1.2の検査対象が一致する。
- [x] 3.3 `openspec/changes/migrate-to-gnome/tasks.md` 8.3 のFlatpak再試験項目を削除し、AppImageのクリーン環境試験への置換を記す。確認: 残存タスクにFlatpak前提が無い。

## 4. 検証

- [ ] 4.1 クリーン環境（glibc 2.35相当）でAppImageの起動・EDS読取・位置情報・PDF保存と印刷を確認し、実測サイズと所要情報を検証記録に残す。
- [ ] 4.2 READMEに入手方法と既知制限（EDSがホスト必要、自動更新なし）を記載する。確認: 記載手順をクリーン環境で実行できる。
