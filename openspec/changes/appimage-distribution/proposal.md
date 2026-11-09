# Proposal

## Why

配布をFlatpak第一候補とする現行計画は、ポータルの権限・フォーカス制約・保存と印刷の再試験（8.3継承分）に大きな検証コストを強い。アプリはEDSへのD-Bus直接続で実装されており、サンドボックスは恩恵より検証負担が大きい。利用者がCalDAV等のアカウントを自身で追加する運用（アプリ側のアカウント設定を持たない方針）にも、無サンドボックスの単一実行ファイルが合致する。

## What Changes

- 配布形式をAppImageへ変更する。基盤環境はUbuntu 22.04（glibc 2.35）とし、GTK 4・libadwaita・PyGObject・連携ネイティブライブラリ・日本語フォールバックフォントを同梱して単一実行ファイルで起動できるようにする。
- **BREAKING** Flatpakを配布候補から外す。Flatpak内の保存・印刷・EDS読取の再試験をタスク対象から削除し、ポータル権限の探求を配布要件から外す。
- ホストのEDS関連D-Busサービスが不在の場合は、技術詳細を出さず短い案内を表示する機能を追加する。設定と個人有データの保存先（`$XDG_CONFIG_HOME` 基準）の振る舞いは変更しない。
- 位置情報は現行どおりXDG Locationポータルを使う（ネイティブ実行でも動作し、コード変更なし）。
- 配布計画・依存文書のFlatpak記述をAppImage基準に書き替える。アプリIDは暫定値のまま確定しない（利用者の判断事項）。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `desktop-application`: 配布形態に関する新要件（単一実行ファイルでの起動、ホストEDS不在時の短い案内）を追加する。既存要件（ホストEDS・位置情報・保存と印刷の必要最小限接続、EDSアカウント未登録時の案内）は据え置く。

## Impact

- 新規: AppImageビルド手順（Ubuntu 22.04ベースのコンテナ、GTK 4.14以上とlibadwaitaの同梱ビルド、`meson install --destdir` 出力のAppDir化、`requirements.txt` を依存入力にしたpip同梱）。
- 更新: `openspec/changes/migrate-to-gnome/tasks.md` 8.3 のFlatpak再試験項目、`distribution-plan.md`、`docs/gnome-dependencies.md` のFlatpak節、README（後続変更との兼ね合いは最終化時調整）。
- 廃棄: Flatpak manifest前提の検証項目。`flatpak-eds-validation.md` はEDS接続の実績記録として参照には残し、配布要件からは外す。
- 前提変更なし。lighten-osm-pipeline・simplify-ui-flow と独立に実施できるが、配布検証はUIの並べ替え後（simplify-ui-flow適用後）が望ましい。
