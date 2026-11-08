"""一県抽出（PBF/XML）から一度だけ作るSQLite派生データ。

版は県コードとsha256に紐付き、版が替わった時だけ再構築する。検索・徒歩経路・
印刷地物の実行時照会はこのファイルの表だけを読み、抽出そのものを再走査しない。
地名・座標・歩行対象の経路情報・印刷地物の幾何以外を書き込まず、検索語など
利用者由来の情報を含まない。生成は一時ファイルで進め、完成後に原子的に
置換する。取り消しでは部分状態を残さない。
"""

import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile

from .places import Place, PlaceIndex
from .walking import _bounds, _scan_pedestrian, WalkingError, WalkingRouter

DERIVED_VERSION = 1

_REASONS = frozenset({"invalid-input", "invalid-data", "unavailable", "cancelled"})


class DerivedError(Exception):
    """パス・抽出の内容を含まない、公開可能な理由コード。"""

    def __init__(self, reason: str):
        self.reason = reason if isinstance(reason, str) and reason in _REASONS else "unavailable"
        super().__init__(self.reason)


def derived_path(prefecture: int, sha256: str, *, directory) -> Path:
    """版（県・SHA-256）に対応した保存先。"""
    if (type(prefecture) is not int or not 1 <= prefecture <= 47
            or not isinstance(sha256, str) or not sha256):
        raise DerivedError("invalid-input")
    return Path(directory) / f"JP-{prefecture:02d}-{sha256}.sqlite"


_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE places(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    latitude REAL NOT NULL, longitude REAL NOT NULL, aliases TEXT NOT NULL);
CREATE TABLE geo_nodes(
    id INTEGER PRIMARY KEY, latitude REAL NOT NULL, longitude REAL NOT NULL);
CREATE INDEX geo_nodes_position ON geo_nodes(latitude, longitude);
CREATE TABLE ped_ways(
    id INTEGER PRIMARY KEY, forward INTEGER NOT NULL, backward INTEGER NOT NULL);
CREATE TABLE ped_way_nodes(
    way_id INTEGER NOT NULL, seq INTEGER NOT NULL, node_id INTEGER NOT NULL,
    PRIMARY KEY(way_id, seq)) WITHOUT ROWID;
CREATE INDEX ped_way_nodes_node ON ped_way_nodes(node_id, way_id);
CREATE VIRTUAL TABLE map_area_bounds USING rtree(id, west, east, south, north);
CREATE TABLE map_areas(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, rings BLOB NOT NULL);
CREATE VIRTUAL TABLE map_road_bounds USING rtree(id, west, east, south, north);
CREATE TABLE map_roads(id INTEGER PRIMARY KEY, road_class TEXT NOT NULL, points BLOB NOT NULL);
CREATE TABLE map_labels(name TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL);
CREATE INDEX map_labels_position ON map_labels(x, y);
"""


class _Cancelled(Exception):
    """走査の打ち切り。"""


def _pack_paths(paths):
    """<i パス数>、各パスは <i 点数> と点数×2個の倍精度（x, y）。"""
    payload = bytearray(struct.pack("<I", len(paths)))
    for path in paths:
        payload += struct.pack("<I", len(path))
        flat = [value for point in path for value in point]
        payload += struct.pack(f"<{len(flat)}d", *flat)
    return bytes(payload)


def _unpack_paths(blob):
    offset = 0
    count, = struct.unpack_from("<I", blob, offset)
    offset += 4
    paths = []
    for _ in range(count):
        size, = struct.unpack_from("<I", blob, offset)
        offset += 4
        values = struct.unpack_from(f"<{2 * size}d", blob, offset)
        offset += 16 * size
        paths.append(tuple(zip(values[::2], values[1::2])))
    return tuple(paths)


def _scan_places_and_features(extract_path, *, cancelled=None):
    """保存済み抽出を1回走査して地名と印刷地物を拾う。

    戻り値は（{地名id: Place}、area特集、road特集、地名ラベル特集）。
    areaとroadの幾何は投影座標（Web Mercator）のリング／折れ線、
    地名ラベルは（name, x, y）。幾何が不完全な対象は補完せず飛ばす。
    """
    import osmium
    from shapely import from_wkb
    from shapely.errors import GEOSException
    from shapely.ops import transform
    from .map_rendering import ROAD_STYLES, _area_kind, project
    from .places import _is_area, _kind, _names, _valid

    geometry_errors = (RuntimeError, ValueError, osmium.InvalidLocationError, GEOSException)
    factory = osmium.geom.WKBFactory()
    places = {}
    areas, roads, labels = [], [], []

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise _Cancelled()

    def to_projected(geometry):
        return transform(lambda lon, lat, z=None: project(lat, lon), geometry)

    def polygon_rings(projected):
        polygons = projected.geoms if projected.geom_type == "MultiPolygon" else (projected,)
        rings = []
        for polygon in polygons:
            rings.append(list(polygon.exterior.coords))
            rings.extend(list(inner.coords) for inner in polygon.interiors)
        return rings

    def label(tags, projected):
        name = tags.get("name") or tags.get("name:ja") or tags.get("name:en")
        if name:
            point = projected.representative_point()
            labels.append((name, point.x, point.y))

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            check_cancelled()
            if node.deleted or not node.location.valid():
                return
            point = (node.location.lat, node.location.lon)
            names = _names(node.tags)
            if names:
                place = Place(f"node/{node.id}", names[0], _kind(node.tags),
                              point[0], point[1], names[1:])
                if _valid(place):
                    places[place.id] = place
            name = node.tags.get("name") or node.tags.get("name:ja") or node.tags.get("name:en")
            if name and any(node.tags.get(key) for key in ("place", "amenity", "tourism", "railway")):
                px, py = project(*point)
                labels.append((name, px, py))

        def way(self, way):
            check_cancelled()
            names = _names(way.tags)
            # 閉じた面wayの地名は、検証済み幾何しか出さないareaの側で作る。
            if names and not (way.is_closed() and _is_area(way.tags)):
                try:
                    geometry = from_wkb(factory.create_linestring(way))
                except geometry_errors:
                    geometry = None
                if geometry is not None and geometry.is_valid:
                    point = geometry.interpolate(0.5, normalized=True)
                    place = Place(f"way/{way.id}", names[0], _kind(way.tags),
                                  point.y, point.x, names[1:])
                    if _valid(place):
                        places[place.id] = place
            highway = way.tags.get("highway", "")
            road_class = highway.removesuffix("_link")
            if road_class in ROAD_STYLES:
                try:
                    geometry = from_wkb(factory.create_linestring(way))
                except geometry_errors:
                    return
                if geometry.is_empty or not geometry.is_valid:
                    return
                projected = to_projected(geometry)
                if projected.is_empty or not projected.is_valid:
                    return
                west, south, east, north = projected.bounds
                roads.append((road_class, [list(projected.coords)],
                              west, south, east, north))
                label(way.tags, projected)

        def area(self, area):
            check_cancelled()
            names = _names(area.tags)
            kind = _area_kind(area.tags)
            place_ready = bool(names) and area.tags.get("area") != "no" and (
                not area.from_way() or _is_area(area.tags))
            if not place_ready and kind is None:
                return
            try:
                geometry = from_wkb(factory.create_multipolygon(area))
            except geometry_errors:
                return
            if geometry.is_empty or not geometry.is_valid:
                return
            if place_ready:
                point = geometry.representative_point()
                osm_type = "way" if area.from_way() else "relation"
                place = Place(f"{osm_type}/{area.orig_id()}", names[0], _kind(area.tags),
                              point.y, point.x, names[1:])
                if _valid(place):
                    places[place.id] = place
            if kind is not None:
                projected = to_projected(geometry)
                if projected.is_empty or not projected.is_valid:
                    return
                west, south, east, north = projected.bounds
                areas.append((kind, polygon_rings(projected), west, south, east, north))
                label(area.tags, projected)

    Handler().apply_file(os.fspath(extract_path), locations=True, idx="flex_mem")
    return places, areas, roads, labels


def _write_derived(db, prefecture, sha256, places, areas, roads, labels, nodes, ways):
    db.executemany("INSERT INTO meta VALUES (?, ?)",
                   (("version", str(DERIVED_VERSION)), ("prefecture", str(prefecture)),
                    ("sha256", sha256)))
    db.executemany("INSERT INTO places VALUES (?, ?, ?, ?, ?, ?)",
                   ((place.id, place.name, place.kind, place.latitude, place.longitude,
                     json.dumps(list(place.aliases), ensure_ascii=False))
                    for place in places.values()))
    db.executemany("INSERT INTO geo_nodes VALUES (?, ?, ?)",
                   ((node_id, point[0], point[1]) for node_id, point in nodes.items()))
    db.executemany("INSERT INTO ped_ways VALUES (?, ?, ?)",
                   ((way_id, int(forward), int(backward))
                    for way_id, forward, backward, _refs in ways))

    way_node_rows = ((way_id, seq, node_id)
                     for way_id, _forward, _backward, refs in ways
                     for seq, node_id in enumerate(refs))
    db.executemany("INSERT INTO ped_way_nodes VALUES (?, ?, ?)", way_node_rows)

    for index, (kind, rings, west, south, east, north) in enumerate(areas, 1):
        db.execute("INSERT INTO map_areas VALUES (?, ?, ?)", (index, kind, _pack_paths(rings)))
        db.execute("INSERT INTO map_area_bounds VALUES (?, ?, ?, ?, ?)",
                   (index, west, east, south, north))
    for index, (road_class, coordinates, west, south, east, north) in enumerate(roads, 1):
        db.execute("INSERT INTO map_roads VALUES (?, ?, ?)",
                   (index, road_class, _pack_paths(coordinates)))
        db.execute("INSERT INTO map_road_bounds VALUES (?, ?, ?, ?, ?)",
                   (index, west, east, south, north))
    db.executemany("INSERT INTO map_labels VALUES (?, ?, ?)", labels)


def build_derived(extract_path, prefecture: int, sha256: str, *, directory,
                  cancelled=None, progress=None):
    """版付き派生データを作る。完成前にフォルダに部分状態は残さない。"""
    final = derived_path(prefecture, sha256, directory=directory)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=str(directory), prefix=".derived-")
    os.close(descriptor)
    temporary = Path(temporary_name)

    def report(fraction):
        if progress is not None:
            progress(float(fraction))

    try:
        report(0.0)
        db = sqlite3.connect(str(temporary))
        try:
            db.execute("PRAGMA journal_mode = OFF")
            db.execute("PRAGMA synchronous = OFF")
            db.executescript(_SCHEMA)
            places, areas, roads, labels = _scan_places_and_features(
                extract_path, cancelled=cancelled)
            report(0.33)
            nodes, ways = _scan_pedestrian(extract_path, cancelled=cancelled)
            report(0.66)
            _write_derived(db, prefecture, sha256, places, areas, roads, labels, nodes, ways)
            db.commit()
            report(1.0)
        finally:
            db.close()
        os.chmod(temporary, 0o600)
        handle = os.open(str(temporary), os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
        os.replace(temporary, final)
        for stale in directory.glob(f"JP-{prefecture:02d}-*.sqlite"):
            if stale != final:
                stale.unlink(missing_ok=True)
        return final
    except _Cancelled:
        raise DerivedError("cancelled") from None
    except ImportError:
        raise DerivedError("unavailable") from None
    except Exception:
        raise DerivedError("invalid-data") from None
    finally:
        temporary.unlink(missing_ok=True)


class DerivedStore:
    """版付き派生データの読み取り。検索・経路・地物照会はこれを通す。"""

    def __init__(self, db, path: Path, prefecture: int):
        self._db = db
        self._path = path
        self._prefecture = prefecture

    @classmethod
    def open(cls, path, sha256: str, prefecture: int):
        """版と県が一致し読める場合は書き込み不可の接続を返し、それ以外はNone。"""
        if (type(prefecture) is not int or not 1 <= prefecture <= 47
                or not isinstance(sha256, str) or not sha256):
            return None
        try:
            db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            if (int(meta["version"]) != DERIVED_VERSION or int(meta["prefecture"]) != prefecture
                    or meta["sha256"] != sha256):
                raise ValueError
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            db.close()
            return None
        return cls(db, Path(path), prefecture)

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def place_index(self) -> PlaceIndex:
        try:
            rows = self._db.execute(
                "SELECT id, name, kind, latitude, longitude, aliases FROM places").fetchall()
        except sqlite3.Error:
            raise PlaceIndexError() from None
        try:
            index = PlaceIndex(
                (Place(row[0], row[1], row[2], row[3], row[4], tuple(json.loads(row[5])))
                 for row in rows),
                prefecture=self._prefecture)
        except PlaceIndexError:
            raise
        index.source_path = str(self._path)
        return index

    def router(self, bounds, max_snap_m=100.0) -> WalkingRouter:
        """範囲内のノードだけで組み立てた経路。全文走査は二度と行わない。"""
        bounds = _bounds(bounds)
        if bounds is None:
            west, south, east, north = -180.0, -90.0, 180.0, 90.0
        else:
            west, south, east, north = bounds
        try:
            nodes = {int(node_id): (latitude, longitude) for node_id, latitude, longitude
                     in self._db.execute(
                         "SELECT id, latitude, longitude FROM geo_nodes "
                         "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                         (south, north, west, east))}
            self._db.execute("DROP TABLE IF EXISTS temp.route_nodes")
            self._db.execute("CREATE TEMP TABLE route_nodes(node_id INTEGER PRIMARY KEY)")
            self._db.executemany("INSERT INTO route_nodes VALUES (?)", ((n,) for n in nodes))
            way_rows = self._db.execute(
                "SELECT pw.id, pw.forward, pw.backward FROM ped_ways pw"
                " JOIN ped_way_nodes wn ON wn.way_id = pw.id"
                " JOIN route_nodes r ON r.node_id = wn.node_id"
                " GROUP BY pw.id").fetchall()
            edges = set()
            for way_id, forward, backward in way_rows:
                refs = [row[0] for row in self._db.execute(
                    "SELECT node_id FROM ped_way_nodes WHERE way_id = ? ORDER BY seq",
                    (way_id,)).fetchall()]
                for source, target in zip(refs, refs[1:]):
                    if source in nodes and target in nodes:
                        if forward:
                            edges.add((source, target))
                        if backward:
                            edges.add((target, source))
        except sqlite3.Error:
            raise WalkingError("invalid-data") from None
        return WalkingRouter(nodes, edges, bounds=bounds, max_snap_m=max_snap_m)

    def map_features(self, bounds):
        """投影座標（Web Mercator）の表示範囲に対する（area特集、road特集、label特集）。"""
        try:
            west, south, east, north = bounds
            if (not all(type(value) in (int, float) for value in (west, south, east, north))
                    or west >= east or south >= north):
                raise ValueError
        except (TypeError, ValueError):
            raise WalkingError("invalid-data") from None
        try:
            areas = self._db.execute(
                "SELECT a.kind, a.rings FROM map_area_bounds b"
                " JOIN map_areas a ON a.id = b.id"
                " WHERE b.west <= ? AND b.east >= ? AND b.south <= ? AND b.north >= ?",
                (east, west, north, south)).fetchall()
            roads = self._db.execute(
                "SELECT r.road_class, r.points FROM map_road_bounds b"
                " JOIN map_roads r ON r.id = b.id"
                " WHERE b.west <= ? AND b.east >= ? AND b.south <= ? AND b.north >= ?",
                (east, west, north, south)).fetchall()
            labels = self._db.execute(
                "SELECT name, x, y FROM map_labels WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
                (west, east, south, north)).fetchall()
        except sqlite3.Error:
            raise WalkingError("invalid-data") from None
        return ([[kind, list(_unpack_paths(blob))] for kind, blob in areas],
                [[road_class, list(_unpack_paths(blob))] for road_class, blob in roads],
                labels)


def ensure_derived(extract, *, cancelled=None, progress=None):
    """保存済み抽出版に対応する派生データを用意する。既にあれば作り直さない。"""
    path = derived_path(extract.prefecture, extract.sha256,
                        directory=Path(extract.path).parent)
    store = DerivedStore.open(path, extract.sha256, extract.prefecture)
    if store is not None:
        store.close()
        return path
    return build_derived(extract.path, extract.prefecture, extract.sha256,
                         directory=path.parent, cancelled=cancelled,
                         progress=progress)
