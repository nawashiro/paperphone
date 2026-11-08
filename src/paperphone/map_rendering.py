"""ローカルOSMから描く、冊子とは独立したCairo/Pangoベクタ地図。

座標・寸法はCairoのユーザー単位（印刷時はpt）。原本のPOIとグラフ上の
経路を別々に描く。MapLibre互換の公開OSM Libertyを参照した印刷向け
淡色パレットであり、エンジンの画素再現ではない。通信・タイル取得・
永続キャッシュなし。スタイルJSON・sprite・glyph・タイルは組み込まない。

参照（2026-11-08確認）:
https://github.com/maputnik/osm-liberty/blob/gh-pages/style.json
https://github.com/maputnik/osm-liberty/blob/gh-pages/LICENSE.md
https://github.com/mapbox/mapbox-gl-styles/blob/master/LICENSE.md
park=#d8e8c8・opacity=.7、water=rgb(158,189,255)、building=hsl(35,8%,85%)
を参照し、紙の背景#f5f3eeに対してparkは70%、waterは50%で合成した。
buildingはHSLをRGBへ変換した。road_minorのz14幅2.5pxは印刷の
0.75pt/px換算で約1.9ptを参照値とし、他の道路は独自の等級階層を維持する。
原スタイルのズーム依存・3D・opacity・外部資源を再現するものではない。

デザイン参照: OSM Liberty contributors、OpenMapTiles（CC BY 4.0）、
OSM Bright / Mapbox Open Styles（CC BY 3.0）。上記の印刷向け変更を含む。
原スタイルから参照した数値についても、配布時のBSD条件を以下に保持する。
Mapbox Open Styles copyright (c) 2014, Mapbox, all rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:
* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
* Neither the name of Mapbox nor the names of its contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

from dataclasses import dataclass
import math
import os

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .i18n import gettext_for
from .walking_sheet import OSM_ATTRIBUTION, OSM_ATTRIBUTION_SHORT, WalkingSheet


@dataclass(frozen=True)
class MapRenderResult:
    """overflowならホストは警告し、より大きな区画で再生成すること。

    寸法不足時は何も描かない（帰属だけを黙って欠落させない）。errorは
    個人情報・ローカルパスを含まない。projected_boundsはEPSG:3857のm。
    omitted_labelsは衝突・境界で省略した背景地名の数。
    """

    content_overflow: bool = False
    projected_bounds: tuple[float, float, float, float] | None = None
    error: str | None = None
    omitted_labels: int = 0


def project(latitude, longitude):
    """緯度経度からWeb Mercatorへ。極域は投影の有効範囲に制限する。"""
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("invalid-coordinate")
    latitude = max(-85.05112878, min(85.05112878, latitude))
    return (6378137 * math.radians(longitude),
            6378137 * math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2)))


# 元データの道路種別を保持。幅は印刷ptで、ズームで細線が消えない。
ROAD_STYLES = {
    "motorway": (3.5, "#e8b6a6"), "trunk": (3.2, "#edc5a0"),
    "primary": (2.8, "#efd6ac"), "secondary": (2.4, "#f1e2bc"),
    "tertiary": (2.0, "#f8f0d6"), "residential": (1.9, "#ffffff"),
    "living_street": (1.9, "#ffffff"), "unclassified": (1.9, "#ffffff"),
    "service": (1.0, "#ffffff"), "pedestrian": (1.4, "#e8e0d8"),
    "footway": (0.8, "#aa998a"), "path": (0.7, "#aa998a"),
    "steps": (0.8, "#947f70"), "track": (0.8, "#b4a58d"),
    "cycleway": (0.8, "#9ab6bd"), "bridleway": (0.7, "#b4a58d"),
}
AREA_COLORS = {"water": "#cad8f7", "building": "#dcd9d6",
               "park": "#e1ebd3", "forest": "#cbdcbe"}
STYLE_CREDIT = (
    "Print palette adapted from OSM Liberty\n"
    "© OpenMapTiles · CC BY 4.0\n"
    "OSM Bright / Mapbox © 2014 · CC BY 3.0"
)


def _area_kind(tags):
    if tags.get("natural") == "water" or tags.get("waterway") == "riverbank" or tags.get("landuse") == "reservoir":
        return "water"
    if tags.get("building") and tags.get("building") != "no":
        return "building"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "forest"
    if tags.get("leisure") in ("park", "garden", "recreation_ground") or tags.get("landuse") in ("grass", "meadow", "recreation_ground"):
        return "park"
    return None


_FEATURE_CACHE = {}
_FEATURE_CACHE_LIMIT = 8


def _feature_cache_key(path, bounds):
    try:
        return (str(path), tuple(float(v) for v in bounds))
    except (TypeError, ValueError):
        return (str(path),)


def _read_features(path, bounds):
    """同じ版・同じ範囲の地図表示データをメモリで再利用する。

    PBF/XMLは範囲で狭めて走査する。把up everything from 範囲内の構成が
    変わった時だけ走査し直す。保存はメモリ内だけ。
    """
    key = _feature_cache_key(path, bounds)
    if key in _FEATURE_CACHE:
        return _FEATURE_CACHE[key]
    result = _read_features_uncached(path, bounds)
    while len(_FEATURE_CACHE) >= _FEATURE_CACHE_LIMIT:
        _FEATURE_CACHE.pop(next(iter(_FEATURE_CACHE)))
    _FEATURE_CACHE[key] = result
    return result


def _read_features_uncached(path, bounds):
    """同じ不変抽出を走査し、表示範囲と交差する幾何だけ保持する。"""
    import osmium
    from shapely import wkb
    from shapely.geometry import box
    from shapely.ops import transform

    viewport = box(*bounds)
    # 県全体の幾何を毎回投影せず、まず経緯度bboxで候補を絞る。
    geographic_bounds = (
        math.degrees(bounds[0] / 6378137),
        math.degrees(math.atan(math.sinh(bounds[1] / 6378137))),
        math.degrees(bounds[2] / 6378137),
        math.degrees(math.atan(math.sinh(bounds[3] / 6378137))),
    )
    factory = osmium.geom.WKBFactory()
    areas, roads, labels = [], [], []

    def projected(geometry):
        return transform(lambda lon, lat, z=None: project(lat, lon), geometry)

    def retain(raw):
        geometry = wkb.loads(raw, hex=True)
        if geometry.is_empty:
            return None
        west, south, east, north = geometry.bounds
        if (east < geographic_bounds[0] or west > geographic_bounds[2]
                or north < geographic_bounds[1] or south > geographic_bounds[3]):
            return None
        geometry = projected(geometry)
        if not geometry.is_valid or not geometry.intersects(viewport):
            return None
        return geometry

    def label(tags, geometry):
        name = tags.get("name") or tags.get("name:ja") or tags.get("name:en")
        if name:
            point = geometry.intersection(viewport).representative_point()
            labels.append((name, point.x, point.y))

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            if not node.location.valid():
                return
            name = node.tags.get("name") or node.tags.get("name:ja") or node.tags.get("name:en")
            if name and any(node.tags.get(key) for key in ("place", "amenity", "tourism", "railway")):
                px, py = project(node.location.lat, node.location.lon)
                if bounds[0] <= px <= bounds[2] and bounds[1] <= py <= bounds[3]:
                    labels.append((name, px, py))

        def way(self, way):
            highway = way.tags.get("highway", "")
            road_class = highway.removesuffix("_link")
            if road_class not in ROAD_STYLES:
                return
            try:
                geometry = retain(factory.create_linestring(way))
            except (RuntimeError, ValueError):
                return  # 抽出境界の不完全wayを架空の線で補完しない。
            if geometry is not None:
                roads.append((road_class, geometry))
                label(way.tags, geometry)

        def area(self, area):
            kind = _area_kind(area.tags)
            if kind is None:
                return
            try:
                geometry = retain(factory.create_multipolygon(area))
            except (RuntimeError, ValueError):
                return
            if geometry is not None:
                areas.append((kind, geometry))
                label(area.tags, geometry)

    Handler().apply_file(os.fspath(path), locations=True, idx="flex_mem")
    return areas, roads, labels


def _to_gray(color):
    """印刷で読める白黒版にまとめる。ヘックスの輝度で置き換える。"""
    r = int(color[1:3], 16) / 255
    g = int(color[3:5], 16) / 255
    b = int(color[5:7], 16) / 255
    # Rec.601の輝度での重み。紙への印刷では薄すぎない範囲に丸める。
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return (luma, luma, luma)


def _color(context, color):
    context.set_source_rgb(*_to_gray(color))


def _layout(context, text, size, width=None):
    layout = PangoCairo.create_layout(context)
    font = Pango.FontDescription("Sans")
    font.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_text(text, -1)
    if width is not None:
        layout.set_width(max(1, int(width * Pango.SCALE)))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    return layout


def _size(layout):
    _, logical = layout.get_extents()
    return logical.width / Pango.SCALE, logical.height / Pango.SCALE


def _path(context, geometry, screen):
    if geometry.geom_type == "Polygon":
        for ring in (geometry.exterior, *geometry.interiors):
            for i, coordinate in enumerate(ring.coords):
                (context.move_to if i == 0 else context.line_to)(*screen(*coordinate[:2]))
            context.close_path()
    elif geometry.geom_type in ("LineString", "LinearRing"):
        for i, coordinate in enumerate(geometry.coords):
            (context.move_to if i == 0 else context.line_to)(*screen(*coordinate[:2]))
    else:
        for part in geometry.geoms:
            _path(context, part, screen)


def _route_path(context, coordinates, screen):
    """POIは受け取らず、グラフのポリラインだけをパスにする。"""
    context.new_path()
    for i, point in enumerate(coordinates):
        (context.move_to if i == 0 else context.line_to)(*screen(*point))


def draw_walking_map(context, sheet: WalkingSheet, *, x, y, width, height) -> MapRenderResult:
    """地図ブロックを描き、収容・データエラーを明示して返す。

    footerは最低8pt、背景地名は7pt。footer全体と最低40ptの地図を確保
    できなければ描画せずcontent_overflow=True。Cairo状態は変更しない。
    原POIはA/B、道路上の経路は青線。POIへの直線接続は生成しない。
    同一点は左A・右Bの分割マーカーで両方を保持する。
    """
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
        return MapRenderResult(content_overflow=True)
    try:
        endpoints = (sheet.departure, sheet.destination)
        coordinates = tuple(project(lat, lon) for lat, lon in sheet.route.points)
        original = tuple(project(p.latitude, p.longitude) for p in endpoints)
        if not coordinates:
            return MapRenderResult(error="invalid-route")
        metrics = (sheet.route.distance_m, sheet.route.duration_seconds,
                   sheet.route.start_snap_m, sheet.route.end_snap_m)
        if not all(math.isfinite(v) and v >= 0 for v in metrics):
            return MapRenderResult(error="invalid-route")
    except (ValueError, TypeError, AttributeError):
        return MapRenderResult(error="invalid-route")

    context.save()
    try:
        _ = gettext_for()
        # 紙面は白黒で読める。個別の地名・基準日・距離の注記は置かない。
        footer_text = f"{OSM_ATTRIBUTION_SHORT}\n{STYLE_CREDIT}"
        # 独自帰属が付加されていても法定帰属を置換させない。
        if sheet.attribution and sheet.attribution != OSM_ATTRIBUTION:
            footer_text += "\n" + sheet.attribution
        footer = _layout(context, footer_text, 8, width - 8)
        fw, fh = _size(footer)
        map_height = height - fh - 12
        if width < 64 or map_height < 40 or fw > width - 8:
            return MapRenderResult(content_overflow=True)

        all_points = coordinates + original
        west, east = min(p[0] for p in all_points), max(p[0] for p in all_points)
        south, north = min(p[1] for p in all_points), max(p[1] for p in all_points)
        # 最低100m四方、マーカーと周辺道路のため余白を持つ。
        span_x, span_y = max(100, east - west), max(100, north - south)
        scale = min((width - 36) / (span_x * 1.2), (map_height - 36) / (span_y * 1.2))
        cx, cy = (west + east) / 2, (south + north) / 2
        bounds = (cx - width / scale / 2, cy - map_height / scale / 2,
                  cx + width / scale / 2, cy + map_height / scale / 2)
        screen = lambda px, py: (x + (px - bounds[0]) * scale, y + (bounds[3] - py) * scale)
        try:
            areas, roads, labels = _read_features(sheet.source_path, bounds)
        except Exception:
            return MapRenderResult(projected_bounds=bounds, error="source-unavailable")

        context.save()
        context.rectangle(x, y, width, map_height)
        context.clip()
        _color(context, "#f5f3ee")
        context.paint()
        context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        for kind, geometry in sorted(areas, key=lambda item: ("forest", "park", "water", "building").index(item[0])):
            context.new_path()
            _path(context, geometry, screen)
            _color(context, AREA_COLORS[kind])
            context.fill()
        context.set_line_join(cairo.LINE_JOIN_ROUND)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        for road_class, geometry in sorted(roads, key=lambda item: ROAD_STYLES[item[0]][0]):
            line_width, color = ROAD_STYLES[road_class]
            for stroke_width, stroke_color in ((line_width + 0.65, "#ccc7bd"), (line_width, color)):
                context.new_path()
                _path(context, geometry, screen)
                context.set_line_width(stroke_width)
                _color(context, stroke_color)
                context.stroke()

        # 実際のグラフ上の点だけを結ぶ。単一点も可視の円とする。
        for stroke_width, color in ((5, "#ffffff"), (2.8, "#276aa0")):
            _route_path(context, coordinates, screen)
            context.set_line_width(stroke_width)
            _color(context, color)
            if len(coordinates) == 1:
                context.arc(*screen(*coordinates[0]), stroke_width / 2, 0, math.tau)
                context.fill()
            else:
                context.stroke()

        marker_positions = [screen(*point) for point in original]
        if math.dist(*marker_positions) < 20:
            midpoint = tuple((a + b) / 2 for a, b in zip(*marker_positions))
            marker_positions = [(midpoint[0] - 9, midpoint[1]), (midpoint[0] + 9, midpoint[1])]
        occupied = [(px - 10, py - 10, 20, 20) for px, py in marker_positions]
        omitted = 0
        for name, px, py in labels:
            layout = _layout(context, name, 7)
            lw, lh = _size(layout)
            sx, sy = screen(px, py)
            sx, sy = sx - lw / 2, sy - lh / 2
            if (sx < x + 2 or sy < y + 2 or sx + lw > x + width - 2 or sy + lh > y + map_height - 2
                    or any(sx < ox + ow + 2 and sx + lw + 2 > ox and sy < oy + oh + 2 and sy + lh + 2 > oy
                           for ox, oy, ow, oh in occupied)):
                omitted += 1
                continue
            occupied.append((sx, sy, lw, lh))
            context.move_to(sx, sy)
            _color(context, "#ffffff")
            PangoCairo.layout_path(context, layout)
            context.set_line_width(2)
            context.stroke()
            context.move_to(sx, sy)
            _color(context, "#454942")
            PangoCairo.show_layout(context, layout)
        for letter, (px, py), color in zip("AB", marker_positions, ("#25694e", "#a34a40")):
            context.new_path()
            context.arc(px, py, 8, 0, math.tau)
            _color(context, color)
            context.fill()
            layout = _layout(context, letter, 9)
            lw, lh = _size(layout)
            context.move_to(px - lw / 2, py - lh / 2)
            _color(context, "#ffffff")
            PangoCairo.show_layout(context, layout)
        context.restore()  # 地図幾何のclipからfooterを分離。
        _color(context, "#ffffff")
        context.rectangle(x, y + map_height, width, height - map_height)
        context.fill()
        context.move_to(x + 4, y + map_height + 6)
        _color(context, "#292d30")
        PangoCairo.show_layout(context, footer)
        return MapRenderResult(projected_bounds=bounds, omitted_labels=omitted)
    finally:
        context.restore()
