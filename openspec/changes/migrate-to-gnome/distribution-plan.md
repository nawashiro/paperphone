# 配布整備の草案（タスク8.3）

## 現時点の状態

この文書は、利用者が選んだ「Flatpak（推奨）・現時点でIDと連絡先は確定しない」に沿った草案であり、実際の配布成功は書いていない。既存の検証資料は[Flatpak EDS検証](<flatpak-eds-validation.md>)と[保存・印刷検証](<save-print-validation.md>)を参照。

## 暫定アプリID

現在の[main.py](<../../src/paperphone/main.py>)の`com.withgoogle.experiments.unplugged`は暫定値であり、上流のAndroid IDとは意図的に一致しない。製品IDの決定は利用者の判断であり、このセッションでは確定しない。ID未確定のため、desktopファイルやAppStreamの識別子も暫定のまま残す。

## 雛形

- desktopファイル: [paperphone.desktop.draft](<../../docs/paperphone.desktop.draft>)。アイコン指定は暫定であり、ICON名とアイコンの提供が確定するまで実装しない。
- アイコン: 現時点でアイコン素材が無い。GNOMEアイコンテーマへの依存、再配布時のアイコンライセンス・出典確認が必要。
- AppStreamビジネスメタデータ: 名前・説明・ライセンス・開発者・配布URLは、利用者の決定後に書き込む。AppStreamの`<releases>`タグでの時期を自動化しない。
- Flatpak manifest: 未作成。必要なPython依存とGI連携は[docs/gnome-dependencies.md](<../../docs/gnome-dependencies.md>)を参照。

## クリーン環境での試験項目

- Flatpak内部での依存読込・GI検査
- 実ホストEDSアカウント（GNOMEオンラインアカウントまたはEvolution構成済み）からの読取
- 位置ポータルの許可・拒否・タイムアウト処理
- Flatpak内部からのPDF保存・印刷・実紙出力
- 実プリンターでのA4横100%出力と折り畳み摺り

自動ではこれらは実施できない。記録の無い項目は完了へ移さず、制限として明記する。

## 検証再試験（2.6から引き継ぐ）

タスク2.6で利用者が承認した検証はFlatpak全体への統合ではクリーン環境に置き換えない。下記を再試験する：

- 本文付きPDF保存
- 印刷操作・取消・権限不足
- ポータル・ファイルアクセス権限の必要最低限
