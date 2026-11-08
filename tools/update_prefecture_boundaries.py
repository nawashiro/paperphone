#!/usr/bin/env python3
"""movisdaの公開indexから47都道府県だけを再現可能な形式で抜粋する。

開発時専用。利用者の座標を受け取らず、固定の公開URLへ一回だけGETする。
実行: python tools/update_prefecture_boundaries.py
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

SOURCE = "https://osm.download.movisda.io/index.geojson"
OUTPUT = Path(__file__).resolve().parents[1] / "src/paperphone/resources/maps/japan-prefectures.geojson"


def main():
    request = Request(SOURCE, headers={"User-Agent": "PaperPhone-boundary-update/1.0"})
    with urlopen(request, timeout=120) as response:
        raw = response.read(50_000_001)
        headers = response.headers
    if len(raw) > 50_000_000:
        raise ValueError("index.geojsonがサイズ制限を超えました")
    document = json.loads(raw)
    features = []
    for feature in document["features"]:
        properties = feature["properties"]
        identifier = properties.get("id", "")
        if not re.fullmatch(r"JP-\d{2}", identifier):
            continue
        code = int(identifier[3:])
        names = properties["names"]
        features.append({
            "type": "Feature",
            "properties": {
                "code": code, "name_ja": names.get("ja") or names[""],
                "name_en": properties["name"], "source_id": identifier,
                "source_timestamp": properties["timestamp"],
            },
            # 座標の丸め・単純化・bboxへの置換をしない。
            "geometry": feature["geometry"],
        })
    features.sort(key=lambda feature: feature["properties"]["code"])
    if [f["properties"]["code"] for f in features] != list(range(1, 48)):
        raise ValueError("47都道府県が揃っていません。既存資源は変更しません")
    result = {
        "type": "FeatureCollection",
        "provenance": {
            "provider": "movisda extracts", "source_url": SOURCE,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_last_modified": headers.get("Last-Modified"),
            "source_etag": headers.get("ETag"),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "geometry": "Provider-generalized index geometry, unchanged",
            "attribution": "© OpenStreetMap contributors",
            "copyright_url": "https://www.openstreetmap.org/copyright",
            "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        },
        "features": features,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(features)} prefectures; {OUTPUT.stat().st_size} bytes")
    print(json.dumps(result["provenance"], ensure_ascii=False, indent=2))
    print(json.dumps(features[0]["properties"], ensure_ascii=False))


if __name__ == "__main__":
    main()
