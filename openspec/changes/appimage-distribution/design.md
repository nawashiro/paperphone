# Design

## Context

配布候補はこれまでFlatpak第一候補だった（proposal・distribution-plan・docs/gnome-dependencies.md・tasks 8.3）。実装は`eds.py` がホストのEDSへD-Bus直接続し、Flatpak検証（flatpak-eds-validation・location-portal-validation）はポータルの権限・フォーカス制約で手間が続いてきた。アプリはPython＋GTK 4.14以上＋libadwaita＋Poppler/Cairo/GdkPixbuf GI＋osmium/shapely/rapidfuzz を要求し、`meson install --destdir` まで構成済み（docs/gnome-dependencies.md の構成検査）。

## Goals / Non-Goals

**Goals:**
- Ubuntu 22.04（glibc 2.35）基準のAppImageで、インストールなし起動を目指す。
- ビルドは再現可能な1手順（コンテナ内スクリプト）にし、CIで今回作らず手元から実行できる段階までとする。
- ホストEDS関連サービスが不在な環境での振る舞いを明示する。

**Non-Goals:**
- アプリID・連絡先の確定（利用者の判断事項、暫定IDのままアイコン・AppStreamの完成は後続）、自動更新（AppImageUpdate等、後続判断）、Gatekeeper的レビュー、CI構成。

## Decisions

**基盤環境: Ubuntu 22.04（利用者が選択した基準）**
glibc 2.35を最低基準にする。GTK 4.14以上・libadwaita 1は22.04のリポジトリに無いため、コンテナ内でソースからビルドしてAppDirに同梱する（AppImageのGNOMEスタック構築で一般的な手順）。より新しい基盤（24.04等）は対応環境を狭めるので今回は使わない。

**ビルド手順: 手作りAppDir＋`appimagetool`**
1コンテナ内スクリプト: Ubuntu 22.04をベースにGTK4・libadwaita・その他ネイティブ依存をビルド（またはPPA等が信頼できる場合は活用）し、`meson install --destdir build/stage` の出力をAppDirへ組み替え、`appimagetool` で固める。linuxdeploy等の既成ツールはGIドライバ同梱を通例で足りないため、AppDirの組立てを明示のスクリプトにして検証可能にする（docs/gnome-dependencies.md の機能一覧をチェック対象にする）。

**Python同梱: 基盤のpython3（3.10）＋venv方式**
システムのpython3.10を使い、`requirements.txt` 通りcp310のmanylinux wheelをvenvに組み込みAppDirへ配置する。osmium・shapely・rapidfuzzはwheelで、Giとの混用が必要なpycairo・PyGObjectはネイティブ（同梱ビルド）またはwheelでpython ABI（cp310）を統一する。

**接続: EDS等はD-Bus直、ポータルは現行のまま**
実装は位置情報を引き続きXDG Locationポータル、保存・印刷をネイティブ扱いとする（コード変更なし）。ホストEDSのサービス不在時は`eds.py` 列挙失敗を分類し、現行の取得失敗表示（短い状態語）に接続する。

## Risks / Trade-offs

- [GTK 4.14・libadwaitaの22.04ベースビルドは重い] → ビルドは1手順・再現可能にする。メサ等のパッケージが足りない場合はビルド時に追加する。
- [単一実行ファイルのサイズが大きくなる（数百MB想定）] → 実測値を検証で記録し、必要ならアイコンやデバッグシンボルの含める範囲を限定的に判断する。
- [EDS不在環境への案内は>E D S自体がホスト必須である説明になる] → 案内ではGNOMEのオンラインアカウントの接続方法を示し、トラブルシューティングの深入はREADMEに任せる。

## Migration Plan

Flatpak関連の検証・手順は本文書 scopes 外の参照情報とし、配布計画・依存文書のFlatpak節をAppImage基準に書き替える。旧配布物は存在しないため切り替え手順は不要。

## Open Questions

なし。AppImageサイズの実測値とGTKビルドの所要時間は検証時に記録する。
