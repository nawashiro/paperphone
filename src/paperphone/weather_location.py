"""現在地の許可・妥当性判定と天気取得。座標や例外詳細は状態表示へ渡さない。"""

from dataclasses import dataclass, field
from enum import Enum
import math

from .location import LocationDenied, LocationError, LocationPortal, LocationTimeout
from .weather import ForecastResult


class WeatherLocationState(Enum):
    IDLE = "idle"
    LOCATING = "locating"
    FETCHING = "fetching"
    READY = "ready"
    DENIED = "denied"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True)
class WeatherLocationResult:
    state: WeatherLocationState
    accuracy_m: float | None = None
    forecast: ForecastResult | None = field(default=None, repr=False)


def usable_fix(fix):
    """非有限・範囲外の座標と怪しい精度値を送信前に除外する。

    精度の上限は設定しない。提供元が粗い値でも実際の入力か無い場合は
    送らないが、位置精度そのものが低くても送信自体は行う（利用者の指定）。
    """
    values = (fix.latitude, fix.longitude, fix.accuracy_m)
    return (all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) for v in values)
            and fix.accuracy_m >= 0
            and -90 <= fix.latitude <= 90 and -180 <= fix.longitude <= 180)


class WeatherLocationController:
    """位置取得はUIスレッド、HTTPは注入したワーカーで実行する。

    submitは関数をワーカーへ渡し、dispatchはUIスレッドに関数を戻す。
    起動時の自動要求・拒否後の再要求・継続追跡は行わない。
    """

    def __init__(self, client, *, submit, dispatch, portal_factory=LocationPortal,
                 on_change=lambda result: None):
        self.client = client
        self.submit = submit
        self.dispatch = dispatch
        self.portal_factory = portal_factory
        self.on_change = on_change
        self.result = WeatherLocationResult(WeatherLocationState.IDLE)
        self._attempted = False
        self._closed = False
        self.fix = None  # 許可・精度確認済みの位置だけをメモリ内で再利用する。

    def close(self):
        self._closed = True
        self.fix = None
        self.result = WeatherLocationResult(WeatherLocationState.IDLE)
        self.on_change = lambda result: None

    def _set(self, state, *, accuracy=None, forecast=None):
        if self._closed:
            return
        self.result = WeatherLocationResult(state, accuracy, forecast)
        self.on_change(self.result)

    def start(self, parent_window):
        if self._attempted or self._closed:
            return
        self._attempted = True
        self._set(WeatherLocationState.LOCATING)
        try:
            fix = self.portal_factory(parent_window=parent_window).request_once()
        except LocationDenied:
            self._set(WeatherLocationState.DENIED)
            return
        except LocationTimeout:
            self._set(WeatherLocationState.TIMEOUT)
            return
        except LocationError:
            self._set(WeatherLocationState.UNAVAILABLE)
            return
        if self._closed:
            return
        if not usable_fix(fix):
            self._set(WeatherLocationState.INVALID)
            return
        self.fix = fix
        self._set(WeatherLocationState.FETCHING, accuracy=fix.accuracy_m)

        def fetch():
            if self._closed:
                return
            try:
                forecast = self.client.fetch(fix.latitude, fix.longitude)
            except Exception:
                self.dispatch(lambda: self._set(WeatherLocationState.FAILED))
            else:
                self.dispatch(lambda: self._set(WeatherLocationState.READY,
                                               accuracy=fix.accuracy_m, forecast=forecast))

        try:
            self.submit(fetch)
        except Exception:
            self._set(WeatherLocationState.FAILED)
