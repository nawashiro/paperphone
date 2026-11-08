"""一県のOSM抽出だけを使う、メモリ内の場所検索。検索語は保存しない。"""

from dataclasses import dataclass
import json
import math
import os
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path


CACHE_VERSION = 1


class PlaceIndexError(Exception):
    """パス・座標・検索語を含まない、公開可能なエラー。"""

    def __init__(self):
        super().__init__("Local place index is unavailable.")


@dataclass(frozen=True, slots=True)
class Place:
    id: str
    name: str
    kind: str
    latitude: float
    longitude: float
    aliases: tuple[str, ...] = ()

    def __post_init__(self):
        # 呼出し元の可変リストを保持しない。
        object.__setattr__(self, "aliases", tuple(self.aliases))


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # カタカナをひらがなへ。半角・濁点は先のNFKCで揃える。
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    return " ".join(text.split())


def _valid(place: Place) -> bool:
    return (isinstance(place, Place) and bool(place.id.strip())
            and bool(place.name.strip()) and bool(place.kind.strip())
            and math.isfinite(place.latitude) and -90 <= place.latitude <= 90
            and math.isfinite(place.longitude) and -180 <= place.longitude <= 180)


_NAME_KEYS = ("name", "name:ja", "name:en", "int_name", "alt_name",
              "name:ja-Hira", "name:ja-Hira:reading", "name:ja-Kana",
              "name:ja_kana", "name:ja_kana:reading", "name:ja_rm",
              "name:kana", "name:hiragana", "name:katakana", "name:reading",
              "alt_name:ja", "alt_name:en", "alt_name:ja-Hira", "alt_name:ja_kana")
_KIND_KEYS = ("place", "amenity", "shop", "tourism", "leisure", "railway",
              "public_transport", "historic", "natural", "waterway", "highway",
              "landuse", "building", "boundary", "office", "craft", "man_made")


def _names(tags):
    names = tuple(dict.fromkeys(value.strip() for key in _NAME_KEYS
                               for value in tags.get(key, "").split(";") if value.strip()))
    return names


def _kind(tags):
    return next((tags[key] for key in _KIND_KEYS if tags.get(key)), "named")


def _is_area(tags):
    return tags.get("area") != "no" and (
        tags.get("area") == "yes" or any(tags.get(key) for key in
        ("building", "landuse", "leisure", "amenity", "natural", "shop", "tourism")))


class PlaceIndex:
    """同名でもOSM識別子が異なる候補を保持する。範囲判定は別の境界辞書が担う。"""

    def __init__(self, places: Iterable[Place], *, prefecture: int | None = None):
        if prefecture is not None and (type(prefecture) is not int or not 1 <= prefecture <= 47):
            raise PlaceIndexError()
        self.prefecture = prefecture
        self.source_path = None
        try:
            self._places = tuple(place for place in places if _valid(place))
            self._names = tuple(tuple(dict.fromkeys(_normalize(name) for name in
                                      (place.name, *place.aliases) if name.strip()))
                                for place in self._places)
        except Exception:
            raise PlaceIndexError() from None

    @classmethod
    def from_pbf(cls, path, prefecture: int, *, cancelled: Callable[[], bool] | None = None):
        """PBF/XMLをメモリ内で読む。取消時は部分索引を返さずエラーにする。"""
        if type(prefecture) is not int or not 1 <= prefecture <= 47:
            raise PlaceIndexError()
        try:
            class Cancelled(Exception):
                pass

            def check_cancelled():
                if cancelled is not None and cancelled():
                    raise Cancelled()

            check_cancelled()
            import osmium
            from shapely import from_wkb
            from shapely.errors import GEOSException

            geometry_errors = (RuntimeError, ValueError, osmium.InvalidLocationError, GEOSException)

            places = {}
            factory = osmium.geom.WKBFactory()

            class Handler(osmium.SimpleHandler):
                def add(self, obj, osm_type, geometry):
                    names = _names(obj.tags)
                    if not names or geometry.is_empty or not geometry.is_valid:
                        return
                    if geometry.geom_type in ("LineString", "MultiLineString"):
                        point = geometry.interpolate(0.5, normalized=True)
                    else:
                        point = geometry.representative_point()
                    place = Place(f"{osm_type}/{obj.id}",
                                  names[0], _kind(obj.tags), point.y, point.x, names[1:])
                    if _valid(place):
                        places[place.id] = place

                def node(self, node):
                    check_cancelled()
                    if not _names(node.tags):
                        return
                    try:
                        self.add(node, "node", from_wkb(factory.create_point(node)))
                    except geometry_errors:
                        pass

                def way(self, way):
                    check_cancelled()
                    if not _names(way.tags):
                        return
                    # 面はareaコールバックの検証済み幾何からのみ登録する。
                    if way.is_closed() and _is_area(way.tags):
                        return
                    try:
                        self.add(way, "way", from_wkb(factory.create_linestring(way)))
                    except geometry_errors:
                        pass

                def area(self, area):
                    check_cancelled()
                    if (not _names(area.tags) or area.tags.get("area") == "no"
                            or (area.from_way() and not _is_area(area.tags))):
                        return
                    try:
                        geometry = from_wkb(factory.create_multipolygon(area))
                        # area.idは派生IDなので、元のway/relation IDへ戻す。
                        osm_type = "way" if area.from_way() else "relation"
                        names = _names(area.tags)
                        if geometry.is_empty or not geometry.is_valid:
                            return
                        point = geometry.representative_point()
                        place = Place(f"{osm_type}/{area.orig_id()}", names[0], _kind(area.tags),
                                      point.y, point.x, names[1:])
                        if _valid(place):
                            places[place.id] = place
                    except geometry_errors:
                        pass

            # flex_memは座標キャッシュをディスクへ書かない。
            check_cancelled()
            Handler().apply_file(os.fspath(path), locations=True, idx="flex_mem")
            check_cancelled()
            index = cls(places.values(), prefecture=prefecture)
            index.source_path = os.path.abspath(os.fspath(path))
            check_cancelled()
            return index
        except Exception:
            # libosmiumの生エラーにはパスや幾何が含まれる可能性がある。
            raise PlaceIndexError() from None

    @classmethod
    def cache_path(cls, prefecture: int, sha256: str, *, directory=None):
        """版（県・SHA-256）に対応した保存先。索引そのものは含めない。"""
        if type(prefecture) is not int or not 1 <= prefecture <= 47 or not sha256:
            raise PlaceIndexError()
        root = (Path(directory) if directory is not None else
                Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "paperphone")
        return root / "place-index" / f"JP-{prefecture:02d}-{sha256}.json"

    def save_cache(self, path, sha256: str):
        """索引をキャッシュとして原子的に書き出す。検索語は含めない。"""
        try:
            payload = {"version": CACHE_VERSION, "prefecture": self.prefecture,
                       "sha256": sha256,
                       "places": [[p.id, p.name, p.kind, p.latitude, p.longitude, list(p.aliases)]
                                  for p in self._places]}
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".cache-")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            prefix = f"JP-{self.prefecture:02d}-"
            for stale in path.parent.glob(prefix + "*.json"):
                if stale != path:
                    try:
                        stale.unlink()
                    except OSError:
                        pass
        except (OSError, ValueError):
            # キャッシュの書出しに失敗しても索引自体は既にできている。静かに続ける。
            try:
                os.unlink(temporary)
            except (OSError, UnboundLocalError):
                pass

    @classmethod
    def load_cache(cls, path, sha256: str):
        """版付きのキャッシュを読む。不一致・破損・旧版式はキャッシュなし扱い。"""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if (payload.get("version") != CACHE_VERSION
                    or payload.get("sha256") != sha256
                    or not isinstance(payload.get("places"), list)):
                raise ValueError
            prefecture = payload.get("prefecture")
            places = (Place(str(p[0]), str(p[1]), str(p[2]), float(p[3]), float(p[4]),
                            [str(a) for a in p[5]]) for p in payload["places"])
            index = cls(places, prefecture=prefecture)
            try:
                index.source_path = str(path)
            except AttributeError:
                pass
            return index
        except (OSError, ValueError, TypeError, IndexError, json.JSONDecodeError):
            return None

    def get(self, place_id: str) -> Place | None:
        """選択候補がこの索引のOSMエンティティか確認する。"""
        return next((place for place in self._places if place.id == place_id), None)

    def search(self, query: str, limit=10) -> tuple[Place, ...]:
        """完全一致、部分一致、編集距離の順で候補を返す。検索語は保持しない。"""
        if not isinstance(query, str) or type(limit) is not int:
            raise PlaceIndexError()
        query = _normalize(query)
        if not query or limit <= 0:
            return ()
        try:
            from rapidfuzz.fuzz import ratio
            ranked = []
            for place, names in zip(self._places, self._names):
                best = (0, 0.0)
                for name in names:
                    if query == name:
                        score = (3, 100.0)
                    elif query in name:
                        score = (2, 100 * len(query) / len(name))
                    else:
                        similarity = ratio(query, name)
                        score = (1, similarity) if similarity >= 70 and len(query) >= 3 else (0, 0.0)
                    best = max(best, score)
                if best[0]:
                    ranked.append((best, place))
            ranked.sort(key=lambda item: (-item[0][0], -item[0][1],
                                          _normalize(item[1].name), item[1].id))
            return tuple(place for _, place in ranked[:limit])
        except Exception:
            raise PlaceIndexError() from None
