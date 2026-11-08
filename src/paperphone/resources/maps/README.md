# 内蔵の日本47都道府県境界

## 出典とライセンス

- 提供者: **movisda extracts**（https://osm.download.movisda.io/）。OSMの行政境界を使う公開抽出サービス。
- 取得元: https://osm.download.movisda.io/index.geojson
- 提供者の仕様説明: https://osm.download.movisda.io/#index-of-all-areas
- 取得日時: **2026-11-08T07:34:47.145539+00:00**（UTC）。これは境界データの基準時刻ではない。
- 元indexのLast-Modified: **Sat, 07 Nov 2026 05:04:54 GMT**。
- 元indexのETag: `"282185902"`。
- 元indexのSHA-256: `e857c62af57ec173c4e25f76e3b9bee95e4e5fade5dec154a989fa01e3949609`。
- 各抽出のリリース基準時刻は各Featureの`properties.source_timestamp`（`YYYYMMDDhhmm`形式）に保存。この同梱版のJP-01は`202611070100`。index全体の更新時刻や取得日時と混同しない。
- **© OpenStreetMap contributors**。
- OSMの著作権・帰属案内: https://www.openstreetmap.org/copyright
- Open Database License（ODbL）1.0: https://opendatacommons.org/licenses/odbl/1-0/

この資源はmovisdaのindex中の`JP-01`〜`JP-47`だけを抜粋したOSM由来の境界データである。形状の合成や別の地理データソースとの結合はしていない。提供者の名称や帰属を載せることと、複数ソースについて別途ライセンス確認したと主張することは異なる。本説明の出典範囲は上記の境界データのみで、PBF・描画スタイル・他のアプリ資源をカバーしない。

## 解像度と判定の限界

提供者によると`index.geojson`はサイズ削減のため**一般化済み**であり、個別の`.geojson`/`.poly`はより高解像度である。一般化の数値的な許容誤差・距離精度は公開説明から確認できないため、メートル単位の精度は保証しない。この同梱版では提供者の座標を追加で丸めたり、単純化したり、矩形に置換したりしていない。47件合計194,069バイト。

- あくまで県選択の既定候補を得るための境界であり、測量・行政上の確定判定や陸上判定には使わない。抽出境界には海域が含まれる場合がある。
- 県境・海岸・小島・飛地の近傍では一般化、OSMの更新、位置精度によって誤判定や未判定が起こりうる。一般化された隣接県の線が完全に一致することも保証しない。
- 穴の内部・辺上は除外し、外周の辺上は含める。複数県に一致する場合（共有境界・重複）はコードを恣意的に選ばず`None`を返す。県間の隙間や辞書範囲外も`None`となる。
- 経緯度平面でray castingを行う。日本のこの資源を対象とし、日付変更線を跨ぐ任意の世界境界への一般化はしない。
- 自動判定不能なら利用者が47都道府県から手動選択する。最近傍県・bboxへのフォールバックはしない。

## 再作成

リポジトリルートで次を実行する（標準ライブラリのみ）。

```sh
python tools/update_prefecture_boundaries.py
PYTHONPATH=src python -m unittest discover -s tests -p test_prefectures.py -v
```

開発用スクリプトは固定の公開URLからindexを取得し、IDで47都道府県を選別し、形状をそのまま保存する。名称・コード・元のID・リリース時刻と取得メタデータを保持し、47件が揃わなければ書き込まない。更新後は本READMEのサイズ・取得メタデータも照合する。提供者の最新indexは変わるため、将来の再取得がこのスナップショットとバイト一致することは保証しない。

ランタイムの`paperphone.prefectures`は同梱ファイルだけを遅延読込する。スクリプトはアプリから呼ばれず、位置情報・利用者座標を引数に取らない。判定時のHTTP、位置情報サービスへの問い合わせ、座標のログ出力・ディスク保存はない。

## APIとスコープ

- `PREFECTURES`: 不変の47件（`code`, `name_ja`, `name_en`）。コードは1..47、英語名は簡潔な表記。資源の英語名は提供者の表記を保存しているため接尾辞等が異なる場合がある。
- `prefecture_by_code(code)`: 手動選択用の項目。無効/未知コードは`None`。
- `prefecture_at(latitude, longitude)`: WGS84度数の緯度・経度順。コードまたは`None`。`bool`、文字列、非実数、非有限値、緯度±90/経度±180外は`ValueError`（エラー文に座標値を含めない）。
- `PrefectureBoundaries.from_geojson(document).lookup(latitude, longitude)`: Polygon/MultiPolygon、穴を含む注入可能な辞書。GeoJSONの頂点は標準通り経度・緯度順。

これはタスク6.1の独立した境界判定部分のみである。PBF取得・更新、位置情報許可、UI、Mesonへの資源登録、インストール検証はこの変更に含めない。
