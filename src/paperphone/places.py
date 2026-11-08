"""一県の版付き派生データだけを使う、メモリ内の場所検索。検索語は保存しない。"""

from dataclasses import dataclass
import math
import unicodedata
from collections.abc import Callable, Iterable


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
        try:
            self.prefecture = prefecture
            self.source_path = None
            self._places = tuple(place for place in places if _valid(place))
            self._names = tuple(tuple(dict.fromkeys(_normalize(name) for name in
                                      (place.name, *place.aliases) if name.strip()))
                                for place in self._places)
        except Exception:
            raise PlaceIndexError() from None

    @classmethod
    def from_derived(cls, path, sha256: str, prefecture: int, *,
                     cancelled: Callable[[], bool] | None = None):
        """版付き派生データから索引を読む。版・県が一致しないものは索引を作らない。"""
        if (type(prefecture) is not int or not 1 <= prefecture <= 47
                or not isinstance(sha256, str) or not sha256):
            raise PlaceIndexError()
        if cancelled is not None and cancelled():
            raise PlaceIndexError()
        from .osm_derived import DerivedStore

        store = DerivedStore.open(path, sha256, prefecture)
        if store is None:
            raise PlaceIndexError()
        try:
            index = store.place_index()
        finally:
            store.close()
        if cancelled is not None and cancelled():
            raise PlaceIndexError()
        return index

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
