"""画面から分離した接続口と、生成ワーカー専用の冊子合成。"""

from dataclasses import dataclass, field
from datetime import datetime

from gi.repository import Gio

from .booklet_assets import asset_painters, draw_contactless_guides
from .booklet_content import core_painters
from .booklet_maps import render_with_map
from .eds import EDSRegistryAdapter
from .i18n import N_, gettext_for, using_language
from .osm_store import ExtractStore
from .refresh import EDSRefreshController
from .temporal import system_timezone
from .walking_sheet import build_walking_sheet
from .weather import WeatherClient


@dataclass(frozen=True, repr=False)
class GenerationRequest:
    """GTKスレッドで捕捉済み。ワーカーはウィジェットを参照しない。"""
    snapshot: object
    name: str
    assets: object
    weather: object = None
    extract: object = None
    index: object = None
    departure: object = None
    destination: object = None
    exclusions: tuple[str, ...] = ()
    language: str = "en"
    source_states: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, repr=False)
class GeneratedBooklet:
    pdf: bytes = field(repr=False)
    warnings: tuple = ()


ROUTE_REASONS = {
    "missing-endpoints": N_("Choose both departure and destination."),
    "source-mismatch": N_("The map index and selected extract do not match."),
    "outside-prefecture": N_("An endpoint is outside the selected prefecture."),
    "disconnected": N_("The walking road graph is disconnected between these endpoints."),
    "no-nearby-road": N_("No walking road is close enough to an endpoint."),
    "no-graph": N_("No walking roads are available in this area."),
    "outside-bounds": N_("An endpoint is outside the routing area."),
    "overflow": N_("The map content does not fit the printable page."),
    "invalid-route": N_("The walking route is invalid."),
    "invalid-input": N_("The walking endpoints are invalid."),
    "invalid-bounds": N_("The routing area is invalid."),
    "invalid-data": N_("The local road data could not be read."),
    "source-unavailable": N_("The local map source is unavailable."),
    "unavailable": N_("The local walking route is unavailable."),
    "route-failed": N_("The local walking route is unavailable."),
    "render-failed": N_("The booklet could not be rendered."),
}


class GenerationFailure(Exception):
    """個人情報を含まない機能別の安全な失敗。"""
    def __init__(self, source, reason):
        self.source = source if source in ("map", "booklet") else "booklet"
        self.reason = reason if isinstance(reason, str) and reason in ROUTE_REASONS else "render-failed"
        super().__init__(self.source)


def generate_booklet(request):
    """ローカルI/Oと経路/PDF生成。HTTP・位置取得・GTK操作は行わない。"""
    with using_language(request.language):
        return _generate_booklet(request)


def _generate_booklet(request):
    sheet = None
    if request.extract is not None:
        try:
            sheet = build_walking_sheet(request.extract, request.index,
                                        request.departure, request.destination)
        except Exception as error:
            raise GenerationFailure("map", getattr(error, "reason", "route-failed")) from None
    warnings = []
    choices = request.assets
    try:
        core = core_painters(request.snapshot, name=request.name,
                             weather=request.weather, warnings=warnings)
        assets = asset_painters(photo=choices.photo, paper_app=choices.paper_app,
                                notes_enabled=choices.notes_enabled,
                                contactless=choices.contactless, occupied_indices=set(core))
        pdf = render_with_map({**assets, **core}, sheet,
                              main_overlay=draw_contactless_guides if choices.contactless else None,
                              warnings=warnings)
    except Exception as error:
        # 地図描画の失敗も、確認なしに地図を除外した成功へ変えない。
        if sheet is not None and hasattr(error, "reason"):
            raise GenerationFailure("map", error.reason) from None
        raise GenerationFailure("booklet", "render-failed") from None
    return GeneratedBooklet(pdf, request.exclusions + tuple(_warning_text(warning) for warning in warnings))


def _warning_text(warning):
    if isinstance(warning, str):
        return warning
    _ = gettext_for()
    messages = {
        "text_ellipsized": N_("Panel {panel}: some text was shortened ({count})."),
        "items_omitted": N_("Panel {panel}: items did not fit ({count})."),
        "invalid_date": N_("Panel {panel}: an invalid date could not be printed ({count})."),
        "invalid_weather": N_("Panel {panel}: weather data could not be printed ({count})."),
        "location_name_ellipsized": N_("Map: a location name was shortened ({count})."),
    }
    message = messages.get(getattr(warning, "code", ""), N_("Some booklet content could not be printed."))
    return _(message).format(panel=getattr(warning, "panel", 0) + 1,
                             count=getattr(warning, "count", 1))


class ApplicationServices:
    """実装は遅延接続。テストはこのオブジェクト全体またはfactoryを差し替える。"""
    def __init__(self, *, application_id, registry=None, store=None,
                 weather_factory=None, generate=generate_booklet,
                 clock=None, timezone_provider=system_timezone):
        self.application_id = application_id
        self.registry = registry
        self.store = store if store is not None else ExtractStore()
        self.weather_factory = weather_factory or (
            lambda contact: WeatherClient(application_id, "0.1.0", contact))
        self.generate = generate
        self.timezone = timezone_provider
        self.clock = clock or (lambda: datetime.now(self.timezone()))
        self._refresh = EDSRefreshController(registry) if registry is not None else None

    def connect(self, callback):
        """利用者のソース列挙クリック後だけセッションバスへ接続する。"""
        if self.registry is not None:
            callback(True)
            return
        def connected(_object, result):
            try:
                self.registry = EDSRegistryAdapter(Gio.bus_get_finish(result))
                self._refresh = EDSRefreshController(self.registry)
            except Exception:
                callback(False)
            else:
                callback(True)
        Gio.bus_get(Gio.BusType.SESSION, None, connected)

    def list_sources_async(self, kind, callback):
        self.registry.list_sources_async(kind, callback)

    def refresh_async(self, *args, **kwargs):
        return self._refresh.refresh_async(*args, **kwargs)
