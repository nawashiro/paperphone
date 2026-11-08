"""都道府県の内蔵境界によるオフライン判定（標準ライブラリのみ）。

公開APIの引数は緯度、経度の順。GeoJSON内部は経度、緯度の順。
境界の一般化・沿岸/県境付近の限界は resources/maps/README.md を参照。
通信・座標の保存/記録・位置情報の取得は行わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import math
from numbers import Real
from typing import Mapping


@dataclass(frozen=True)
class Prefecture:
    code: int
    name_ja: str
    name_en: str


_NAMES = (
    ("北海道", "Hokkaido"), ("青森県", "Aomori"), ("岩手県", "Iwate"),
    ("宮城県", "Miyagi"), ("秋田県", "Akita"), ("山形県", "Yamagata"),
    ("福島県", "Fukushima"), ("茨城県", "Ibaraki"), ("栃木県", "Tochigi"),
    ("群馬県", "Gunma"), ("埼玉県", "Saitama"), ("千葉県", "Chiba"),
    ("東京都", "Tokyo"), ("神奈川県", "Kanagawa"), ("新潟県", "Niigata"),
    ("富山県", "Toyama"), ("石川県", "Ishikawa"), ("福井県", "Fukui"),
    ("山梨県", "Yamanashi"), ("長野県", "Nagano"), ("岐阜県", "Gifu"),
    ("静岡県", "Shizuoka"), ("愛知県", "Aichi"), ("三重県", "Mie"),
    ("滋賀県", "Shiga"), ("京都府", "Kyoto"), ("大阪府", "Osaka"),
    ("兵庫県", "Hyogo"), ("奈良県", "Nara"), ("和歌山県", "Wakayama"),
    ("鳥取県", "Tottori"), ("島根県", "Shimane"), ("岡山県", "Okayama"),
    ("広島県", "Hiroshima"), ("山口県", "Yamaguchi"), ("徳島県", "Tokushima"),
    ("香川県", "Kagawa"), ("愛媛県", "Ehime"), ("高知県", "Kochi"),
    ("福岡県", "Fukuoka"), ("佐賀県", "Saga"), ("長崎県", "Nagasaki"),
    ("熊本県", "Kumamoto"), ("大分県", "Oita"), ("宮崎県", "Miyazaki"),
    ("鹿児島県", "Kagoshima"), ("沖縄県", "Okinawa"),
)
PREFECTURES = tuple(Prefecture(code, *names) for code, names in enumerate(_NAMES, 1))


def prefecture_by_code(code: int) -> Prefecture | None:
    """1..47の選択肢を返す。不明なコードやbool/文字列はNone。"""
    if type(code) is not int or not 1 <= code <= 47:
        return None
    return PREFECTURES[code - 1]


def _coordinate(value: Real, bound: int) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("座標は有限な実数で指定してください")
    if not -bound <= value <= bound:
        raise ValueError("座標は有効範囲内で指定してください")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("座標は有限な実数で指定してください")
    return value


def _ring_location(x: float, y: float, ring: tuple) -> int:
    """0=外、1=内、2=辺上。頂点の向きに依存しない半開区間ray casting。"""
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if cross == 0 and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by):
            return 2
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return int(inside)


@dataclass(frozen=True)
class _Polygon:
    rings: tuple
    bounds: tuple[float, float, float, float]

    def contains(self, longitude: float, latitude: float) -> bool:
        west, south, east, north = self.bounds
        if not west <= longitude <= east or not south <= latitude <= north:
            return False
        return bool(_ring_location(longitude, latitude, self.rings[0])) and not any(
            _ring_location(longitude, latitude, hole) for hole in self.rings[1:]
        )


class PrefectureBoundaries:
    """Polygon/MultiPolygon辞書。注入可能なGeoJSONでネットワークなしに試験できる。"""

    def __init__(self, polygons: tuple[tuple[int, tuple[_Polygon, ...]], ...]):
        self._polygons = polygons

    @classmethod
    def from_geojson(cls, document: Mapping) -> PrefectureBoundaries:
        """properties.code (1..47)を持つFeatureCollectionを検証して保持する。"""
        if document.get("type") != "FeatureCollection":
            raise ValueError("FeatureCollectionが必要です")
        entries = []
        seen = set()
        for feature in document["features"]:
            code = feature["properties"]["code"]
            if prefecture_by_code(code) is None or code in seen:
                raise ValueError("都道府県コードが不正または重複しています")
            seen.add(code)
            geometry = feature["geometry"]
            kind = geometry["type"]
            if kind not in ("Polygon", "MultiPolygon"):
                raise ValueError("PolygonかMultiPolygonが必要です")
            coordinates = geometry["coordinates"]
            polygons = coordinates if kind == "MultiPolygon" else [coordinates]
            parsed = []
            if not polygons:
                raise ValueError("空の境界は使用できません")
            for polygon in polygons:
                if not polygon:
                    raise ValueError("外周が必要です")
                rings = []
                for ring in polygon:
                    points = tuple((_coordinate(p[0], 180), _coordinate(p[1], 90)) for p in ring)
                    if len(points) < 4 or points[0] != points[-1] or len(set(points)) < 3:
                        raise ValueError("閉じた非退化の線形リングが必要です")
                    rings.append(points)
                xs, ys = zip(*rings[0])
                parsed.append(_Polygon(tuple(rings), (min(xs), min(ys), max(xs), max(ys))))
            entries.append((code, tuple(parsed)))
        return cls(tuple(entries))

    def lookup(self, latitude: Real, longitude: Real) -> int | None:
        """一致するコード。県境共有・一般化による重複・範囲外ならNone。

        外周の辺上は含め、穴の辺上は除く。複数県への一致時は推測しない。
        型（bool/文字列等）、非有限値、緯度±90/経度±180外はValueError。
        """
        latitude = _coordinate(latitude, 90)
        longitude = _coordinate(longitude, 180)
        matches = [code for code, polygons in self._polygons
                   if any(polygon.contains(longitude, latitude) for polygon in polygons)]
        return matches[0] if len(matches) == 1 else None


@lru_cache(maxsize=1)
def _builtin() -> PrefectureBoundaries:
    resource = files("paperphone").joinpath("resources/maps/japan-prefectures.geojson")
    document = json.loads(resource.read_text(encoding="utf-8"))
    if sorted(f["properties"]["code"] for f in document["features"]) != list(range(1, 48)):
        raise ValueError("内蔵の47都道府県境界が揃っていません")
    return PrefectureBoundaries.from_geojson(document)


def prefecture_at(latitude: Real, longitude: Real) -> int | None:
    """内蔵の境界だけを使う。判定不能なら手動選択に委ねる。"""
    return _builtin().lookup(latitude, longitude)
