"""実ノードだけを結ぶ徒歩経路。通信・ログ・永続化は行わない。

ノードと有向区間は引数として渡され、距離は実座標から計算する。
幾何学的交差や切断されたwayの欠落部分を接続しない。スナップ先は
実在する通行可能なOSMノード（既定100m以内）であり、道路上への投影や
出発地点への架空の接続線は生成しない。長い区間の中間では、近いノードが
なければ失敗する。県境の正確な包含判定は呼出し元の境界辞書が担う。

距離最短のDijkstra法を使う。目安時間は水平球面距離 / 1.4m/s（5.04km/h）。
階段は通れるが、標高・勾配・信号待ちを推測しない。条件付き通行権は
日時を評価できないため除外し、未知の通行権や障害物も許可とみなさない。
歩行者専用のターン制限relationは、from/to/viaの全wayを除外する。
ターン状態を扱わないため合法な直進や逆方向も失われ得る保守的な方式である。
そのrelationが不正・参照先欠落なら読込全体をinvalid-dataとして拒否する。
一般の自動車ターン制限は徒歩には適用しない。
"""

from collections.abc import Mapping, Iterable
from dataclasses import dataclass
import heapq
import math
import os


_REASONS = frozenset({"invalid-input", "invalid-bounds", "invalid-data", "unavailable",
                      "outside-bounds", "no-graph", "no-nearby-road", "disconnected"})


class WalkingError(Exception):
    """座標・ファイル名・OSM値を含まない公開可能な理由コード。"""

    def __init__(self, reason: str):
        self.reason = reason if isinstance(reason, str) and reason in _REASONS else "unavailable"
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class WalkingRoute:
    points: tuple[tuple[float, float], ...]
    distance_m: float
    duration_seconds: float
    start_snap_m: float
    end_snap_m: float

    def __post_init__(self):
        object.__setattr__(self, "points", tuple(tuple(point) for point in self.points))


_ALLOWED = frozenset({"yes", "designated", "official", "permissive"})
_HIGHWAYS = frozenset({"footway", "pedestrian", "path", "steps", "living_street",
                       "residential", "service", "unclassified", "track",
                       "tertiary", "tertiary_link", "secondary", "secondary_link",
                       "primary", "primary_link"})
_EXPLICIT_HIGHWAYS = frozenset({"cycleway", "bridleway", "trunk", "trunk_link",
                                "motorway", "motorway_link"})
_PASSABLE_BARRIERS = frozenset({"no", "entrance", "bollard", "cycle_barrier",
                                "kissing_gate", "stile"})
_OPENABLE_BARRIERS = frozenset({"gate", "lift_gate", "swing_gate", "sally_port"})
_SPEED_M_S = 1.4


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _coordinate(latitude, longitude):
    if (not _finite_number(latitude) or not _finite_number(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        raise WalkingError("invalid-input")
    return float(latitude), float(longitude)


def _bounds(value):
    if value is None:
        return None
    try:
        west, south, east, north = value
        _coordinate(south, west)
        _coordinate(north, east)
        if west >= east or south >= north:
            raise ValueError()
        return west, south, east, north
    except (TypeError, ValueError, WalkingError):
        raise WalkingError("invalid-bounds") from None


def _inside(point, bounds):
    return bounds is None or (bounds[0] <= point[1] <= bounds[2]
                              and bounds[1] <= point[0] <= bounds[3])


def _distance(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def _access(tags):
    # 自動車専用の条件やonewayは歩行者に適用しない。
    if any(tags.get(key) for key in ("access:conditional", "foot:conditional",
                                   "foot:forward:conditional", "foot:backward:conditional",
                                   "oneway:foot:conditional", "foot:oneway:conditional")):
        return False
    if tags.get("foot", tags.get("access", "yes")) not in _ALLOWED:
        return False
    return not (tags.get("smoothness") == "impassable"
                or tags.get("impassable") == "yes" or tags.get("status") == "impassable")


def _node_passable(tags):
    if not _access(tags) or tags.get("locked") == "yes" or tags.get("crossing") == "no":
        return False
    barrier = tags.get("barrier")
    if not barrier or barrier in _PASSABLE_BARRIERS:
        return True
    return barrier in _OPENABLE_BARRIERS and tags.get("foot", tags.get("access")) in _ALLOWED


def _way_directions(tags):
    highway = tags.get("highway")
    explicit = tags.get("foot") in _ALLOWED
    if (not _access(tags) or tags.get("area") == "yes"
            or (highway not in _HIGHWAYS and not (highway in _EXPLICIT_HIGHWAYS and explicit))
            or (tags.get("motorroad") == "yes" and not explicit)):
        return False, False
    values = [tags[key] for key in ("oneway:foot", "foot:oneway") if key in tags]
    if len(set(values)) > 1:
        return False, False
    value = values[0] if values else "no"
    if value not in {"yes", "1", "true", "-1", "no", "0", "false"}:
        return False, False
    forward = value != "-1"
    backward = value not in {"yes", "1", "true"}
    forward = forward and tags.get("foot:forward", "yes") in _ALLOWED
    backward = backward and tags.get("foot:backward", "yes") in _ALLOWED
    return forward, backward


class WalkingRouter:
    """メモリ内グラフ。版から切り出したノードと有向区間を受け取る。

    nodes: {OSM node ID: (latitude, longitude)}
    edges: (from ID, to ID)の有向区間。距離は実座標から計算する。
    同じ入力地点も道路へスナップし、1点・距離0の結果を返す。
    """

    def __init__(self, nodes: Mapping[int, tuple[float, float]],
                 edges: Iterable[tuple[int, int]], *, bounds=None, max_snap_m=100.0):
        self.bounds = _bounds(bounds)
        if not _finite_number(max_snap_m) or max_snap_m <= 0:
            raise WalkingError("invalid-input")
        self.max_snap_m = float(max_snap_m)
        try:
            coordinates = {node: _coordinate(*point) for node, point in nodes.items()}
            if any(type(node) is not int for node in coordinates):
                raise ValueError()
            graph = {}
            for source, target in edges:
                if source not in coordinates or target not in coordinates:
                    raise ValueError()
                if source == target or not (_inside(coordinates[source], self.bounds)
                                            and _inside(coordinates[target], self.bounds)):
                    continue
                distance = _distance(coordinates[source], coordinates[target])
                graph.setdefault(source, {})[target] = distance
                graph.setdefault(target, {})
            self._nodes = {node: coordinates[node] for node in graph}
            self._graph = graph
        except (TypeError, ValueError, WalkingError, AttributeError):
            raise WalkingError("invalid-data") from None

    def _snap(self, point):
        distance, node = min((_distance(point, coordinate), node)
                             for node, coordinate in self._nodes.items())
        if distance > self.max_snap_m:
            raise WalkingError("no-nearby-road")
        return node, distance

    def route(self, start_latitude, start_longitude, end_latitude, end_longitude):
        start = _coordinate(start_latitude, start_longitude)
        end = _coordinate(end_latitude, end_longitude)
        if not _inside(start, self.bounds) or not _inside(end, self.bounds):
            raise WalkingError("outside-bounds")
        if not self._nodes:
            raise WalkingError("no-graph")
        source, start_snap = self._snap(start)
        target, end_snap = self._snap(end)
        distances = {source: 0.0}
        previous = {}
        queue = [(0.0, source)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == target:
                path = [node]
                while node != source:
                    node = previous[node]
                    path.append(node)
                return WalkingRoute(tuple(self._nodes[node] for node in reversed(path)),
                                    distance, distance / _SPEED_M_S, start_snap, end_snap)
            for neighbor, length in self._graph[node].items():
                candidate = distance + length
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(queue, (candidate, neighbor))
        raise WalkingError("disconnected")


class _Cancelled(Exception):
    """走査の打ち切り。"""


def _scan_pedestrian(path, *, cancelled=None):
    """PBF/XMLを2回走査し、徒歩グラフの構成部品を全部取り出す共有手順。

    位置索引や一時ファイルは作らない。戻り値は
    （{ノードID: (lat, lon)}、(way_id, forward, backward, (node_id, ...))のリスト）。
    参照先の欠落・通行不能ノードを飛び越えて接続しない。
    """
    try:
        import osmium
        nodes = {}
        way_rows = []
        restricted_ways = set()
        restriction_nodes = set()

        def check_cancelled():
            if cancelled is not None and cancelled():
                raise _Cancelled()

        class Nodes(osmium.SimpleHandler):
            def node(self, node):
                check_cancelled()
                if node.deleted or not node.location.valid():
                    return
                point = (node.location.lat, node.location.lon)
                if _node_passable(node.tags):
                    nodes[node.id] = point

            def relation(self, relation):
                if relation.deleted:
                    return
                tags = relation.tags
                # restriction:conditionalだけなら車両向け。footを明示したものだけ扱う。
                foot_keys = [tag.k for tag in tags if tag.k == "restriction:foot"
                             or tag.k.startswith("restriction:foot:")]
                if not foot_keys and tags.get("type") != "restriction:foot":
                    return
                if tags.get("type") not in {"restriction", "restriction:foot"}:
                    raise ValueError()
                if not (any(tags.get(key) for key in foot_keys)
                        or tags.get("restriction") or tags.get("restriction:conditional")):
                    raise ValueError()
                members = list(relation.members)
                from_members = [member for member in members if member.role == "from"]
                to_members = [member for member in members if member.role == "to"]
                via_members = [member for member in members if member.role == "via"]
                if (not from_members or not to_members or not via_members
                        or any(member.role not in {"from", "to", "via"} for member in members)
                        or any(member.type != "w" for member in from_members + to_members)
                        or any(member.type not in {"w", "n"} for member in via_members)
                        or (any(member.type == "n" for member in via_members)
                            and len(via_members) != 1)
                        or any(member.ref <= 0 for member in members)):
                    raise ValueError()
                restricted_ways.update(member.ref for member in members if member.type == "w")
                restriction_nodes.update(member.ref for member in via_members if member.type == "n")

        found_restricted_ways = set()
        found_restriction_nodes = set()

        class Ways(osmium.SimpleHandler):
            def node(self, node):
                if node.id in restriction_nodes and not node.deleted and node.location.valid():
                    found_restriction_nodes.add(node.id)

            def way(self, way):
                check_cancelled()
                if way.deleted:
                    return
                if way.id in restricted_ways:
                    # 欠落したway・viaノード参照も許可と推測しない。
                    if len(way.nodes) < 2:
                        raise ValueError()
                    found_restricted_ways.add(way.id)
                    return
                forward, backward = _way_directions(way.tags)
                if not (forward or backward):
                    return
                refs = tuple(node.ref for node in way.nodes)
                way_rows.append((way.id, forward, backward, refs))

        Nodes().apply_file(os.fspath(path))
        Ways().apply_file(os.fspath(path))
        if (found_restricted_ways != restricted_ways
                or found_restriction_nodes != restriction_nodes):
            raise ValueError()
        return nodes, way_rows
    except _Cancelled:
        raise WalkingError("unavailable") from None
    except ImportError:
        raise WalkingError("unavailable") from None
