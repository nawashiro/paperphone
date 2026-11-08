"""選択した抽出版・両地点・徒歩経路を、不変の印刷入力へまとめる。"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path

from .osm_derived import DerivedError, DerivedStore, derived_path
from .places import Place, PlaceIndex
from .prefectures import prefecture_at
from .selection import ItemKey, SelectableItem
from .walking import WalkingError, WalkingRoute, WalkingRouter

OSM_ATTRIBUTION = "© OpenStreetMap contributors · ODbL 1.0"
OSM_ATTRIBUTION_SHORT = "© OpenStreetMap contributors · ODbL 1.0"


class RoutePreparationError(Exception):
    def __init__(self, reason):
        self.reason = reason if reason in ("missing-endpoints", "source-mismatch", "outside-prefecture") else "source-mismatch"
        super().__init__(self.reason)


@dataclass(frozen=True, repr=False)
class WalkingSheet:
    prefecture: int
    basis: datetime
    source_sha256: str
    source_path: Path = field(repr=False)
    departure: Place
    destination: Place
    route: WalkingRoute
    bounds: tuple[float, float, float, float]
    attribution: str = OSM_ATTRIBUTION

    def as_selectable_item(self):
        # 意図して印刷する情報だけを共通スナップショットへ渡す。ローカルパスは除く。
        fields = {
            "prefecture": str(self.prefecture), "basis": self.basis.isoformat(),
            "source_sha256": self.source_sha256,
            "departure_name": self.departure.name, "destination_name": self.destination.name,
            "departure": json.dumps([self.departure.latitude, self.departure.longitude]),
            "destination": json.dumps([self.destination.latitude, self.destination.longitude]),
            "polyline": json.dumps(self.route.points, separators=(",", ":")),
            "distance_m": str(self.route.distance_m),
            "duration_seconds": str(self.route.duration_seconds),
            "start_snap_m": str(self.route.start_snap_m), "end_snap_m": str(self.route.end_snap_m),
            "bounds": json.dumps(self.bounds), "attribution": self.attribution,
        }
        return SelectableItem(ItemKey("walking-map", self.source_sha256), "walking-map", fields)


def routing_bounds(departure, destination, *, margin_m=2000):
    """両地点に2kmの余白を足す。探索外の到達可能性までは断言しない。"""
    values = (departure.latitude, departure.longitude, destination.latitude, destination.longitude, margin_m)
    if not all(type(v) in (int, float) and math.isfinite(v) for v in values) or margin_m <= 0:
        raise WalkingError("invalid-input")
    if (not all(-90 <= p.latitude <= 90 and -180 <= p.longitude <= 180 for p in (departure, destination))):
        raise WalkingError("invalid-input")
    latitude = (departure.latitude + destination.latitude) / 2
    dlat = margin_m / 111_000
    dlon = margin_m / (111_000 * max(0.01, math.cos(math.radians(latitude))))
    return (max(-180, min(departure.longitude, destination.longitude) - dlon),
            max(-90, min(departure.latitude, destination.latitude) - dlat),
            min(180, max(departure.longitude, destination.longitude) + dlon),
            min(90, max(departure.latitude, destination.latitude) + dlat))


def _belongs(index, point, prefecture):
    if point.id != "current-location":
        return index.get(point.id) == point
    try:
        return prefecture_at(point.latitude, point.longitude) == prefecture
    except (TypeError, ValueError):
        return False


def _source(extract):
    """抽出版に対応する派生データ。版が正しく構成されていないなら失敗。"""
    try:
        return derived_path(extract.prefecture, extract.sha256,
                            directory=Path(extract.path).parent)
    except (DerivedError, TypeError):
        raise WalkingError("unavailable") from None


def _router_from_derived(extract, bounds):
    """版付き派生データの範囲照会から経路を作る。版が一致しない物は失敗。"""
    store = DerivedStore.open(_source(extract), extract.sha256, extract.prefecture)
    if store is None:
        raise WalkingError("unavailable")
    try:
        return store.router(bounds, max_snap_m=100.0)
    finally:
        store.close()


def build_walking_sheet(extract, index: PlaceIndex, departure: Place, destination: Place, *,
                        router_factory=None) -> WalkingSheet:
    """HTTPや位置取得を行わず、選択済みの版から経路を組み立てる。

    重いI/Oなのでホスト画面はワーカーから呼ぶ。異なる県・版のインデックスを
    混ぜず、道路上の経路と元のPOIの位置ずれを別に保持する。
    """
    if departure is None or destination is None:
        raise RoutePreparationError("missing-endpoints")
    source_path = _source(extract)
    if (index.prefecture != extract.prefecture or index.source_path is None
            or Path(index.source_path).resolve() != source_path.resolve()):
        raise RoutePreparationError("source-mismatch")
    if not all(_belongs(index, point, extract.prefecture) for point in (departure, destination)):
        raise RoutePreparationError("outside-prefecture")
    bounds = routing_bounds(departure, destination)
    if router_factory is not None:
        # テストと合成試験では、幾何は差替え先から届く。
        router = router_factory(bounds)
    else:
        router = _router_from_derived(extract, bounds)
    route = router.route(departure.latitude, departure.longitude,
                         destination.latitude, destination.longitude)
    return WalkingSheet(extract.prefecture, extract.basis, extract.sha256, source_path,
                        departure, destination, route, bounds)
