# Proposal

## Why

場所検索・徒歩経路は一県のPBF全体を都度解釈する実装で、 地名検索の初回にPBF全文スキャン、目的地ごとに徒歩グラフの全文再スキャンが発生する。東京など数百MB規模の抽出では待ち時間が実用域を外れ、これが移行版の最大の軽量不満である。

## What Changes

- PBF入手後に一度だけ前処理を行い、SQLiteの派生データ（地名索引と徒歩グラフ）を作る。前処理はPBFの版（sha256）に紐付き、版が替わった時だけ再構築する。
- 場所検索は派生データを読み、PBFのメモリ内全展開と実行時スキャンを廃止する。
- 徒歩経路は派生データに格納した歩行対象のノード・ ways に対する範囲照会で組み立てる。経路要求のたびのPBF全文スキャンを廃止する。
- 印刷地図の地物取得も派生データへ統一し、(PBFパス, bounds)単位のJSON地物キャッシュを廃止する。
- **BREAKING** 派生データが無い版のキャッシュ・中間JSONは読み替えず破棄する。旧実装のメモリ内索引・実行時スキャン経路は撤去する。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `walking-map`: 場所検索・徒歩経路・印刷地図のデータ源を「一県PBF＋派生データ」に改め、応答が派生データを基準に即時に返ること、派生データの再構築条件をrequirementsとして変更する。取得と保持（都道府県PBF、削除、失敗時の冊子続行）のrequirementは据え置く。

## Impact

- `src/paperphone/places.py`（メモリ内索引・JSONキャッシュ）と `walking.py`（`from_pbf`／`_scan_pedestrian`）の置換、`walking_sheet.py`・`place_selection.py`・`map_rendering.py` のデータ源接続の変更。
- SQLite（標準ライブラリの `sqlite3`）を新たな実行時依存にする。osmium・shapely は前処理でのみ使用し、依存は増やさない。
- `openspec/changes/migrate-to-gnome` の tasks 8.4（全体試験）は派生データの初回生成を含む試験手順に読み替える。
