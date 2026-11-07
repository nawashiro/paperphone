# FlatpakからホストEDSへの最小権限実証

## 試験構成

- Flatpak runtime: `org.gnome.Platform` 51
- 実行コンテキスト: 既存アプリの最小権限サンドボックスを使い、テストコマンドだけを起動。アプリの通常UI・ファイル機能は起動しない。
- 追加したセッションバス権限:
  - `--talk-name=org.gnome.evolution.dataserver.Sources5`
  - `--talk-name=org.gnome.evolution.dataserver.Calendar8`
  - `--talk-name=org.gnome.evolution.dataserver.AddressBook10`
- `--nofilesystem=home`を指定。セッションバス全開放、ホーム全体へのアクセス、データソース変更APIは使用しない。

## 結果

- SourceManagerの読取、リモートタスクリストの列挙・VTODO読取、ローカルアドレス帳の読取がGNOME Platform 51のサンドボックス内で成功した。
- 許可していない`org.freedesktop.Notifications`へのD-Bus呼出しは拒否された。
- ホストのホームディレクトリはサンドボックス内にマウントされていないことをinode比較で確認した。
- 実データのタイトル、氏名、日時、URI、ソース識別子は表示・保存していない。変更用APIも呼び出していない。
- GNOME Platform 51にはこの環境でEDataServer/ECal/EBookのGI名前空間がなく、GioからEDSのバージョン付きD-Busサービスを呼び出して試験した。試験したサービス名と最小権限は上記のとおり。

## 判断

ホストEDS読取はバス全開放やホームアクセスなしで成立したため、Flatpakを配布候補として維持する。アプリのmanifestには上記3つの`talk-name`だけを追加し、`filesystem=home`、全セッションバス許可、EDSへの書込APIを追加しない。最終manifestと配布ランタイムでの再確認はタスク8.3で行う。
