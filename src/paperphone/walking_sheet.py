"""選択した抽出版・両地点・徒歩経路を、不変の印刷入力へまとめる。"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path

from .places import Place, PlaceIndex
from .prefectures import prefecture_at
from .selection import ItemKey, SelectableItem
from .walking import _bounds, WalkingError, WalkingRoute, WalkingRouter
from .walking_store import build_full_graph, graph_path, load_graph, save_graph, _router_from_arrays

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


_ROUTER_CACHE: "MutableMapping[tuple, WalkingRouter]" = None


def _router_cache():
    """同じPBF版・同じ探索範囲で一度だけ歩行者グラフを作る。

    生成のたびにPBFを走査し直さないため、探索範囲か抽出版が変わった時だけ
    作り直す。エントリーは既定で8つまで。保存はUIのデータと併せてメモリ内だけ。
    """
    global _ROUTER_CACHE
    if _ROUTER_CACHE is None:
        from collections import OrderedDict

        class RouterCache(OrderedDict):
            def __init__(self):
                super().__init__()
                self._cap = 8

            def __missing__(self, key):
                router = WalkingRouter.from_pbf(key[0], bounds=key[1], max_snap_m=key[2])
                while len(self) >= self._cap:
                    self.popitem(last=False)
                self[key] = router
                return router
        _ROUTER_CACHE = RouterCache()
    return _ROUTER_CACHE


def _router_from_graph_cache(extract, bounds):
    """保存済み県グラフからの経路用スライス。版が一致しなければNone。"""
    try:
        stored = load_graph(graph_path(extract.prefecture, extract.sha256,
                                       directory=Path(extract.path).parent),
                            extract.sha256, extract.prefecture)
        if stored is None:
            return None
        node_ids, coordinates, edges = stored
        return _router_from_arrays(node_ids, coordinates, edges, bounds)
    except (OSError, ValueError, TypeError, WalkingError):
        return None


def _router_from_full_graph(extract, bounds):
    """版グラフが未保存なら一度だけ県全域で作って保存し、その上でスライスする。"""
    try:
        import numpy
        nodes, edges = build_full_graph(extract.path)
        node_ids = numpy.array(sorted(nodes), dtype=numpy.int64)
        coordinates = numpy.array([nodes[node] for node in sorted(nodes)], dtype=numpy.float64)
        edge_array = numpy.array(sorted(set(edges)), dtype=numpy.int64)
    except (ImportError, Exception):
        return None
    path = graph_path(extract.prefecture, extract.sha256,
                      directory=Path(extract.path).parent)
    if not save_graph(path, extract.sha256, extract.prefecture,
                      node_ids, coordinates, edge_array):
        return None
    try:
        return _router_from_arrays(node_ids, coordinates, edge_array, bounds)
    except (OSError, ValueError, TypeError, WalkingError):
        return None


def _get_router(path, bounds):
    key = (str(path), tuple(_bounds(bounds)), 100.0)
    return _router_cache()[key]


def build_walking_sheet(extract, index: PlaceIndex, departure: Place, destination: Place, *,
                        router_factory=None) -> WalkingSheet:
    """HTTPや位置取得を行わず、選択済みの版別PBFから経路を組み立てる。

    重いI/Oなのでホスト画面はワーカーから呼ぶ。異なる県・版のインデックスを
    混ぜず、道路上の経路と元のPOIの位置ずれを別に保持する。
    """
    if departure is None or destination is None:
        raise RoutePreparationError("missing-endpoints")
    if (index.prefecture != extract.prefecture or index.source_path is None
            or Path(index.source_path).resolve() != Path(extract.path).resolve()):
        raise RoutePreparationError("source-mismatch")
    if not all(_belongs(index, point, extract.prefecture) for point in (departure, destination)):
        raise RoutePreparationError("outside-prefecture")
    bounds = routing_bounds(departure, destination)
    if router_factory is not None:
        # テストや合成試験では差替中のBboxからの取り込みを行わない。
        router = router_factory(extract.path, bounds=bounds, max_snap_m=100)
    else:
        # 保存済みの県グラフ（版＋Bzip）があれば、経路ごとのPBF走査をスキップする。
        router = _router_from_graph_cache(extract, bounds)
        if router is None:
            router = _router_from_full_graph(extract, bounds)
        if router is None:
            router = _get_router(extract.path, bounds)
    route = router.route(departure.latitude, departure.longitude,
                         destination.latitude, destination.longitude)
    return WalkingSheet(extract.prefecture, extract.basis, extract.sha256, Path(extract.path),
                        departure, destination, route, bounds)
